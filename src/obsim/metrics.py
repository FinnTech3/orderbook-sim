"""Microstructure measurements taken from a reconstructed book.

Everything here is a pure function of book state. Nothing mutates, nothing is
cached, so a metric can be taken at any point in a replay without disturbing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .book import OrderBook
from .types import Side


def spread_ticks(book: OrderBook) -> int | None:
    return book.spread


def spread_bps(book: OrderBook) -> Decimal | None:
    """Spread as basis points of the midpoint.

    The comparable measure across instruments — a one-tick spread means
    something different on a $2 stock than on a $60,000 future.
    """
    spread, mid = book.spread, book.mid
    if spread is None or mid is None or mid == 0:
        return None
    return Decimal(spread) / mid * 10_000


def imbalance(book: OrderBook, depth: int = 1) -> Decimal | None:
    """Order book imbalance over the top ``depth`` levels.

    Returns a value in [-1, 1]: +1 is all bid, -1 is all ask. Widely used as a
    short-horizon directional signal, on the reasoning that the heavier side
    is more likely to be the one that gets consumed.
    """
    if depth < 1:
        raise ValueError("depth must be at least 1")
    bid_size = sum(level.size for level in book.levels(Side.BID, depth))
    ask_size = sum(level.size for level in book.levels(Side.ASK, depth))
    total = bid_size + ask_size
    if total == 0:
        return None
    return (Decimal(bid_size) - Decimal(ask_size)) / Decimal(total)


def depth_within(book: OrderBook, side: Side, ticks: int) -> int:
    """Total size resting within ``ticks`` of the touch on one side."""
    if ticks < 0:
        raise ValueError("ticks cannot be negative")
    best = book.best_bid if side is Side.BID else book.best_ask
    if best is None:
        return 0
    limit = best - ticks if side is Side.BID else best + ticks
    levels = book.levels(side, 10_000)
    if side is Side.BID:
        return sum(level.size for level in levels if level.price >= limit)
    return sum(level.size for level in levels if level.price <= limit)


@dataclass(frozen=True, slots=True)
class SweepResult:
    """What it would cost to take ``requested`` size off one side."""

    requested: int
    filled: int
    #: Size-weighted average price in ticks. None if nothing filled.
    average_price: Decimal | None
    levels_consumed: int
    #: True if the book ran out before the full size was filled.
    exhausted: bool

    @property
    def complete(self) -> bool:
        return self.filled == self.requested


def sweep(book: OrderBook, side: Side, quantity: int) -> SweepResult:
    """Walk one side of the book to fill ``quantity``.

    ``side`` is the side being *consumed*: pass ASK to price a buy, BID to
    price a sell. Assumes no market impact beyond the displayed depth — the
    book does not refresh as it is eaten, which understates the true cost of
    any size large enough to be noticed.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    remaining = quantity
    cost = Decimal(0)
    consumed = 0

    for level in book.levels(side, 10_000):
        if remaining == 0:
            break
        taken = min(remaining, level.size)
        cost += Decimal(level.price) * taken
        remaining -= taken
        consumed += 1

    filled = quantity - remaining
    average = cost / filled if filled else None
    return SweepResult(
        requested=quantity,
        filled=filled,
        average_price=average,
        levels_consumed=consumed,
        exhausted=remaining > 0,
    )


def slippage_ticks(book: OrderBook, side: Side, quantity: int) -> Decimal | None:
    """How far the average sweep price lands from the midpoint.

    The honest cost of demanding liquidity now. Positive means worse than mid
    on either side, since buying pays above and selling receives below.
    """
    result = sweep(book, side, quantity)
    mid = book.mid
    if result.average_price is None or mid is None:
        return None
    if side is Side.ASK:  # buying
        return result.average_price - mid
    return mid - result.average_price
