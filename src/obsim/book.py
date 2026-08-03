"""The order book itself.

Applies snapshots and deltas to two :class:`~obsim.levels.PriceLevels` and
exposes the quantities strategies actually read. It knows nothing about any
particular venue, and nothing about how the events reached it in order — that
is :mod:`obsim.sequencing`'s job.
"""

from __future__ import annotations

from decimal import Decimal

from .levels import PriceLevels
from .types import DepthDelta, Instrument, PriceSize, Side, Snapshot


class CrossedBook(RuntimeError):
    """Best bid landed at or above best ask.

    In a correctly sequenced L2 feed this cannot happen. When it does, the
    cause is nearly always a dropped or reordered message, so it is raised
    rather than tolerated.
    """


class OrderBook:
    """Level-2 book for one instrument."""

    __slots__ = ("instrument", "bids", "asks", "last_update_id", "event_time_ns")

    def __init__(self, instrument: Instrument) -> None:
        self.instrument = instrument
        self.bids = PriceLevels(Side.BID)
        self.asks = PriceLevels(Side.ASK)
        self.last_update_id: int | None = None
        self.event_time_ns: int | None = None

    # ---- mutation ----------------------------------------------------

    def load_snapshot(self, snapshot: Snapshot) -> None:
        """Replace the whole book with ``snapshot``."""
        self.bids.clear()
        self.asks.clear()
        for level in snapshot.bids:
            self.bids.set(level.price, level.size)
        for level in snapshot.asks:
            self.asks.set(level.price, level.size)
        self.last_update_id = snapshot.last_id
        self.event_time_ns = snapshot.event_time_ns
        self._assert_uncrossed()

    def apply_delta(self, delta: DepthDelta) -> None:
        """Apply one incremental update.

        Both sides are written before the crossing check runs. A single message
        can legitimately move the bid above the old ask and the ask out of the
        way at the same time, so checking level by level would raise on a book
        that is fine once the message is fully applied.
        """
        for level in delta.bids:
            self.bids.set(level.price, level.size)
        for level in delta.asks:
            self.asks.set(level.price, level.size)
        self.last_update_id = delta.final_id
        self.event_time_ns = delta.event_time_ns
        self._assert_uncrossed()

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.last_update_id = None
        self.event_time_ns = None

    # ---- reads -------------------------------------------------------

    @property
    def best_bid(self) -> int | None:
        return self.bids.best

    @property
    def best_ask(self) -> int | None:
        return self.asks.best

    @property
    def spread(self) -> int | None:
        """Best ask minus best bid, in ticks. None if either side is empty."""
        bid, ask = self.bids.best, self.asks.best
        if bid is None or ask is None:
            return None
        return ask - bid

    @property
    def mid(self) -> Decimal | None:
        """Midpoint in ticks. May be a half tick."""
        bid, ask = self.bids.best, self.asks.best
        if bid is None or ask is None:
            return None
        return (Decimal(bid) + Decimal(ask)) / 2

    @property
    def microprice(self) -> Decimal | None:
        """Size-weighted midpoint, in ticks.

        Weighted by the *opposite* side's size, so heavy bid depth pulls the
        estimate toward the ask. A better short-horizon predictor of where the
        next trade prints than the plain midpoint.
        """
        bid, ask = self.bids.best, self.asks.best
        if bid is None or ask is None:
            return None
        bid_size = self.bids.size_at(bid)
        ask_size = self.asks.size_at(ask)
        total = bid_size + ask_size
        if total == 0:
            return None
        return (Decimal(ask) * bid_size + Decimal(bid) * ask_size) / total

    def levels(self, side: Side, depth: int) -> list[PriceSize]:
        return (self.bids if side is Side.BID else self.asks).top(depth)

    def size_at(self, side: Side, price: int) -> int:
        return (self.bids if side is Side.BID else self.asks).size_at(price)

    @property
    def is_crossed(self) -> bool:
        bid, ask = self.bids.best, self.asks.best
        return bid is not None and ask is not None and bid >= ask

    # ---- internals ---------------------------------------------------

    def _assert_uncrossed(self) -> None:
        if self.is_crossed:
            raise CrossedBook(
                f"best bid {self.bids.best} >= best ask {self.asks.best} "
                f"after update {self.last_update_id}"
            )

    def check_invariants(self) -> None:
        self.bids.check_invariants()
        self.asks.check_invariants()
        if self.is_crossed:
            raise AssertionError("book is crossed")

    def __repr__(self) -> str:
        return (
            f"OrderBook({self.instrument.symbol}, "
            f"{self.bids.best}/{self.asks.best}, id={self.last_update_id})"
        )
