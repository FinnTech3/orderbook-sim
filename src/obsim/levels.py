"""One side of the book: price to size, with ordered access.

The container holds a dictionary of sizes keyed by price, and a list of the
occupied prices kept in ascending order. Both sides store ascending; the bid
side reads its best from the end of the list and the ask side from the front.

The list is maintained with :mod:`bisect`, so an insert is O(n) in the worst
case because the tail has to move. That is deliberate — see the benchmark note
in the README. Depth updates cluster near the top of the book, so the moved
tail is short and the move itself is one vectorised block copy rather than n
interpreted steps.
"""

from __future__ import annotations

from bisect import bisect_left, insort

from .types import PriceSize, Side


class PriceLevels:
    """Mutable price-to-size map for a single side."""

    __slots__ = ("_side", "_sizes", "_prices")

    def __init__(self, side: Side) -> None:
        self._side = side
        self._sizes: dict[int, int] = {}
        self._prices: list[int] = []

    @property
    def side(self) -> Side:
        return self._side

    def set(self, price: int, size: int) -> None:
        """Set the absolute size at ``price``. A size of 0 removes the level."""
        if size < 0:
            raise ValueError(f"negative size {size} at price {price}")

        if size == 0:
            if self._sizes.pop(price, None) is not None:
                del self._prices[bisect_left(self._prices, price)]
            return

        if price not in self._sizes:
            insort(self._prices, price)
        self._sizes[price] = size

    def size_at(self, price: int) -> int:
        return self._sizes.get(price, 0)

    @property
    def best(self) -> int | None:
        """Best price on this side, or None if empty."""
        if not self._prices:
            return None
        return self._prices[-1] if self._side is Side.BID else self._prices[0]

    def top(self, depth: int) -> list[PriceSize]:
        """The ``depth`` best levels, best first."""
        if depth <= 0:
            return []
        if self._side is Side.BID:
            prices = self._prices[-depth:]
            prices.reverse()
        else:
            prices = self._prices[:depth]
        return [PriceSize(price, self._sizes[price]) for price in prices]

    def total_size(self) -> int:
        return sum(self._sizes.values())

    def clear(self) -> None:
        self._sizes.clear()
        self._prices.clear()

    def prices_ascending(self) -> list[int]:
        """Copy of the occupied prices, ascending. Mainly for tests."""
        return list(self._prices)

    def check_invariants(self) -> None:
        """Raise if the dictionary and the sorted list have diverged.

        Only used by tests and by the differential harness. The two structures
        are updated together and must always agree; this is what catches the
        kind of bug that a fast container acquires quietly.
        """
        if len(self._sizes) != len(self._prices):
            raise AssertionError(
                f"{len(self._sizes)} sizes but {len(self._prices)} prices"
            )
        if self._prices != sorted(self._prices):
            raise AssertionError("price list is not sorted")
        if len(set(self._prices)) != len(self._prices):
            raise AssertionError("price list contains duplicates")
        for price in self._prices:
            if price not in self._sizes:
                raise AssertionError(f"price {price} listed but has no size")
            if self._sizes[price] <= 0:
                raise AssertionError(f"price {price} has non-positive size")

    def __len__(self) -> int:
        return len(self._sizes)

    def __contains__(self, price: object) -> bool:
        return price in self._sizes

    def __repr__(self) -> str:
        return f"PriceLevels({self._side.value}, {len(self._sizes)} levels)"
