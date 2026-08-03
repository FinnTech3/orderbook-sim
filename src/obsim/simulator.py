"""Resting orders, queue position, latency, and fills.

The order being simulated was never actually at the venue, which is the single
most important thing to hold onto when reading this file. Every size the feed
reports is other participants' size only. Our order is invisible to the data,
so on arrival it joins the back of whatever queue the feed says is there.

Queue position is tracked as two numbers — how much sits ahead of us and how
much behind — and every market event reconciles them against what the venue
reports. Trades consume from the front. Whatever change is left over is
cancellations, and where those sat is the one thing the data cannot tell us;
:mod:`obsim.queue_model` holds the assumptions about that.

Two delays are modelled separately because they are physically different paths:

- **Feed latency** — venue publishes an event, we see it some time later. The
  strategy is therefore always acting on a book that has already moved.
- **Order latency** — we decide, the venue acts on it some time later. The book
  our order lands in is not the book we decided against.

Ignoring either is the most common reason a backtest overstates itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from itertools import count

from .book import OrderBook
from .queue_model import PessimisticQueue, QueueModel
from .types import DepthDelta, Side, Trade


class OrderState(Enum):
    #: Submitted, not yet live at the venue.
    PENDING = "pending"
    #: Live in the book, waiting.
    RESTING = "resting"
    #: Cancel sent, not yet effective.
    CANCEL_PENDING = "cancel_pending"
    FILLED = "filled"
    CANCELLED = "cancelled"


_WORKING = (OrderState.RESTING, OrderState.CANCEL_PENDING)


@dataclass
class Order:
    id: int
    side: Side
    price: int
    size: int
    submitted_ns: int
    effective_ns: int
    state: OrderState = OrderState.PENDING
    filled: int = 0
    #: Other participants' size in front of us at our price.
    queue_ahead: int = 0
    #: Other participants' size behind us at our price.
    queue_behind: int = 0
    cancel_effective_ns: int | None = None

    @property
    def remaining(self) -> int:
        return self.size - self.filled

    @property
    def is_live(self) -> bool:
        return self.state in (OrderState.PENDING, *_WORKING)


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: int
    price: int
    size: int
    event_time_ns: int
    #: True when we crossed the spread rather than being passively hit.
    aggressive: bool


@dataclass
class SimStats:
    orders_submitted: int = 0
    orders_filled: int = 0
    orders_cancelled: int = 0
    partial_fill_events: int = 0
    lots_filled: int = 0
    lots_submitted: int = 0
    passive_fills: int = 0
    aggressive_fills: int = 0
    #: Fills granted because the market printed through our price.
    trade_through_fills: int = 0

    @property
    def fill_ratio(self) -> float:
        """Fraction of submitted size that filled."""
        if self.lots_submitted == 0:
            return 0.0
        return self.lots_filled / self.lots_submitted

    def as_dict(self) -> dict[str, float]:
        out: dict[str, float] = {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }
        out["fill_ratio"] = self.fill_ratio
        return out


class Simulator:
    """Runs hypothetical orders against a reconstructed book.

    Assumes no market impact: our order does not change what anybody else
    does, and taking liquidity does not move the book. That holds while the
    simulated size is small relative to displayed depth and breaks when it is
    not — see the README.
    """

    def __init__(
        self,
        book: OrderBook,
        *,
        queue_model: QueueModel | None = None,
        feed_latency_ns: int = 0,
        order_latency_ns: int = 0,
    ) -> None:
        if feed_latency_ns < 0 or order_latency_ns < 0:
            raise ValueError("latencies cannot be negative")
        self.book = book
        self.queue_model = queue_model or PessimisticQueue()
        self.feed_latency_ns = feed_latency_ns
        self.order_latency_ns = order_latency_ns
        self.stats = SimStats()
        self.orders: dict[int, Order] = {}
        self.fills: list[Fill] = []
        self._ids = count(1)

    # ---- strategy interface ------------------------------------------

    def submit(self, side: Side, price: int, size: int, now_ns: int) -> Order:
        """Send a limit order. It becomes live after the order latency."""
        if size <= 0:
            raise ValueError(f"size must be positive, got {size}")
        order = Order(
            id=next(self._ids),
            side=side,
            price=price,
            size=size,
            submitted_ns=now_ns,
            effective_ns=now_ns + self.order_latency_ns,
        )
        self.orders[order.id] = order
        self.stats.orders_submitted += 1
        self.stats.lots_submitted += size
        return order

    def cancel(self, order_id: int, now_ns: int) -> None:
        """Request a cancel. It takes effect after the order latency.

        The window between request and effect is real, and orders do get
        filled inside it.
        """
        order = self.orders.get(order_id)
        if order is None or not order.is_live:
            return
        order.state = OrderState.CANCEL_PENDING
        order.cancel_effective_ns = now_ns + self.order_latency_ns

    @property
    def live_orders(self) -> list[Order]:
        return [order for order in self.orders.values() if order.is_live]

    def observed_time(self, event_time_ns: int) -> int:
        """When the strategy actually learns about an event."""
        return event_time_ns + self.feed_latency_ns

    # ---- market events -----------------------------------------------

    def on_market_event(
        self, delta: DepthDelta, trades: tuple[Trade, ...] = ()
    ) -> list[Fill]:
        """Process one market event and return any fills it produced.

        Sequence matters: cancels retire, due orders go live, trades work
        through the queue, the book moves, and finally each order's tracked
        queue is reconciled against the level the venue now reports.
        """
        now = delta.event_time_ns
        produced: list[Fill] = []

        self._retire_cancels(now)
        produced += self._activate_pending(now)
        produced += self._apply_trades(trades, now)

        self.book.apply_delta(delta)

        self._reconcile_queues()
        self._reap()
        self.fills += produced
        return produced

    # ---- internals ---------------------------------------------------

    def _activate_pending(self, now_ns: int) -> list[Fill]:
        """Bring due orders live, filling any that arrive marketable."""
        produced: list[Fill] = []
        for order in self.orders.values():
            if order.state is not OrderState.PENDING:
                continue
            if now_ns < order.effective_ns:
                continue

            crossed = self._marketable_fill(order, now_ns)
            if crossed is not None:
                produced.append(crossed)
            if order.state is OrderState.PENDING:
                self._rest(order)
        return produced

    def _rest(self, order: Order) -> None:
        """Join the back of the queue at our price.

        Everything the feed shows at this price belongs to somebody else, so
        it is all in front of us and nothing is behind us yet.
        """
        order.queue_ahead = self.book.size_at(order.side, order.price)
        order.queue_behind = 0
        order.state = OrderState.RESTING

    def _marketable_fill(self, order: Order, now_ns: int) -> Fill | None:
        """Fill immediately if the order arrives crossing the spread."""
        if order.side is Side.BID:
            opposing = self.book.best_ask
            crosses = opposing is not None and order.price >= opposing
        else:
            opposing = self.book.best_bid
            crosses = opposing is not None and order.price <= opposing
        if not crosses:
            return None

        assert opposing is not None
        available = self.book.size_at(order.side.opposite, opposing)
        size = min(order.remaining, available)
        if size <= 0:
            return None

        order.filled += size
        if order.remaining == 0:
            order.state = OrderState.FILLED
        else:
            self._rest(order)
        self._record(order, aggressive=True, size=size)
        return Fill(order.id, opposing, size, now_ns, aggressive=True)

    def _apply_trades(
        self, trades: tuple[Trade, ...], now_ns: int
    ) -> list[Fill]:
        produced: list[Fill] = []
        for order in self.orders.values():
            if order.state not in _WORKING:
                continue
            for trade in trades:
                if trade.aggressor is not order.side.opposite:
                    continue
                fill = self._trade_against_order(order, trade, now_ns)
                if fill is not None:
                    produced.append(fill)
                if order.remaining == 0:
                    break
        return produced

    def _trade_against_order(
        self, order: Order, trade: Trade, now_ns: int
    ) -> Fill | None:
        through = (
            trade.price < order.price
            if order.side is Side.BID
            else trade.price > order.price
        )

        if through:
            # The market printed at a price we were bettering. Had our order
            # really been resting, the aggressor would have reached us first.
            size = min(order.remaining, trade.size)
            if size <= 0:
                return None
            order.queue_ahead = 0
            order.filled += size
            self.stats.trade_through_fills += 1
            self._record(order, aggressive=False, size=size)
            return Fill(order.id, order.price, size, now_ns, aggressive=False)

        if trade.price != order.price:
            return None

        # Trades consume the queue from the front.
        from_ahead = min(trade.size, order.queue_ahead)
        order.queue_ahead -= from_ahead
        leftover = trade.size - from_ahead
        if leftover <= 0:
            return None

        size = min(leftover, order.remaining)
        # Anything still left reached the orders behind us.
        order.queue_behind = max(0, order.queue_behind - (leftover - size))
        if size <= 0:
            return None

        order.filled += size
        self._record(order, aggressive=False, size=size)
        return Fill(order.id, order.price, size, now_ns, aggressive=False)

    def _reconcile_queues(self) -> None:
        """Match tracked queue sizes to what the venue now reports.

        Trades have already been taken out of the queue, so any remaining
        discrepancy is other participants joining or cancelling. Growth goes
        behind us — new orders queue up at the back. Shrinkage is cancellation,
        and the queue model decides how much of it came from in front.
        """
        for order in self.orders.values():
            if order.state not in _WORKING:
                continue

            actual = self.book.size_at(order.side, order.price)
            tracked = order.queue_ahead + order.queue_behind

            if actual > tracked:
                order.queue_behind += actual - tracked
                continue
            if actual == tracked:
                continue

            cancelled = tracked - actual
            from_ahead = self.queue_model.cancellations_ahead(
                order.queue_ahead, order.queue_behind, cancelled
            )
            order.queue_ahead -= from_ahead
            order.queue_behind -= cancelled - from_ahead

    def _retire_cancels(self, now_ns: int) -> None:
        for order in self.orders.values():
            if order.state is not OrderState.CANCEL_PENDING:
                continue
            if order.cancel_effective_ns is not None and (
                now_ns >= order.cancel_effective_ns
            ):
                order.state = OrderState.CANCELLED
                self.stats.orders_cancelled += 1

    def _reap(self) -> None:
        for order in self.orders.values():
            if order.remaining == 0 and order.state in _WORKING:
                order.state = OrderState.FILLED

    def _record(self, order: Order, *, aggressive: bool, size: int) -> None:
        self.stats.lots_filled += size
        if aggressive:
            self.stats.aggressive_fills += 1
        else:
            self.stats.passive_fills += 1
        if order.remaining == 0:
            self.stats.orders_filled += 1
        else:
            self.stats.partial_fill_events += 1
