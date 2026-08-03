"""Value types shared across the package.

Everything downstream of the feed boundary works in integer ticks and lots.
The conversion happens here, once, and never again.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Side(Enum):
    BID = "bid"
    ASK = "ask"

    @property
    def opposite(self) -> Side:
        return Side.ASK if self is Side.BID else Side.BID


class OffGridPrice(ValueError):
    """A price that is not a whole multiple of the instrument's tick size."""


@dataclass(frozen=True, slots=True)
class Instrument:
    """Maps a venue's decimal prices onto an integer grid.

    Prices are integer counts of ``tick_size`` and sizes are integer counts of
    ``lot_size``. Off-grid values raise rather than rounding: a price that does
    not sit on the grid means either the instrument is configured wrongly or the
    venue changed its tick size, and both are worth failing loudly for.
    """

    symbol: str
    tick_size: Decimal
    lot_size: Decimal

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {self.tick_size}")
        if self.lot_size <= 0:
            raise ValueError(f"lot_size must be positive, got {self.lot_size}")

    def to_ticks(self, price: Decimal | str | int) -> int:
        return self._to_grid(price, self.tick_size, "price")

    def to_lots(self, size: Decimal | str | int) -> int:
        return self._to_grid(size, self.lot_size, "size")

    def from_ticks(self, ticks: int) -> Decimal:
        return Decimal(ticks) * self.tick_size

    def from_lots(self, lots: int) -> Decimal:
        return Decimal(lots) * self.lot_size

    @staticmethod
    def _to_grid(value: Decimal | str | int, grid: Decimal, label: str) -> int:
        quotient = Decimal(value) / grid
        rounded = int(quotient.to_integral_value())
        if quotient != rounded:
            raise OffGridPrice(
                f"{label} {value} is not a multiple of {grid} "
                f"(quotient {quotient})"
            )
        return rounded


@dataclass(frozen=True, slots=True)
class PriceSize:
    """A price level. Price in ticks, size in lots."""

    price: int
    size: int


@dataclass(frozen=True, slots=True)
class DepthDelta:
    """An incremental depth update, already converted to the integer grid.

    ``first_id`` and ``final_id`` bound the venue update IDs this message
    covers. ``prev_final_id`` is the explicit predecessor some venues supply;
    it is ``None`` where the venue expects continuity to be inferred instead.

    Sizes are absolute, not deltas: a level reported at size 0 is removed.
    """

    first_id: int
    final_id: int
    event_time_ns: int
    bids: tuple[PriceSize, ...] = ()
    asks: tuple[PriceSize, ...] = ()
    prev_final_id: int | None = None

    def __post_init__(self) -> None:
        if self.final_id < self.first_id:
            raise ValueError(
                f"final_id {self.final_id} precedes first_id {self.first_id}"
            )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A full picture of the book at one update ID."""

    last_id: int
    event_time_ns: int
    bids: tuple[PriceSize, ...] = ()
    asks: tuple[PriceSize, ...] = ()


@dataclass(frozen=True, slots=True)
class Trade:
    """An execution. ``aggressor`` is the side that crossed the spread."""

    price: int
    size: int
    aggressor: Side
    event_time_ns: int
