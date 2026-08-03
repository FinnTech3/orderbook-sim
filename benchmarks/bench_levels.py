"""Measures the level container against the alternatives it was chosen over.

The claim in docs/ARCHITECTURE.md is that a dictionary plus a bisect-maintained
sorted array beats the obvious alternatives at realistic book sizes, despite
insertion being O(n). This checks that rather than asserting it.

Run: python3 benchmarks/bench_levels.py
"""

from __future__ import annotations

import heapq
import random
import sys
import time
from bisect import insort
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from obsim.levels import PriceLevels  # noqa: E402
from obsim.types import Side  # noqa: E402


class SortOnRead:
    """Dictionary only. Sorts whenever the best price is asked for."""

    def __init__(self, side: Side) -> None:
        self.side = side
        self.sizes: dict[int, int] = {}

    def set(self, price: int, size: int) -> None:
        if size == 0:
            self.sizes.pop(price, None)
        else:
            self.sizes[price] = size

    @property
    def best(self) -> int | None:
        if not self.sizes:
            return None
        return max(self.sizes) if self.side is Side.BID else min(self.sizes)


class LazyHeap:
    """Heap of prices with lazy deletion, plus a dictionary of sizes.

    Better insertion complexity, but stale entries accumulate and every read
    has to skip past them.
    """

    def __init__(self, side: Side) -> None:
        self.side = side
        self.sizes: dict[int, int] = {}
        self.heap: list[int] = []

    def set(self, price: int, size: int) -> None:
        if size == 0:
            self.sizes.pop(price, None)
            return
        if price not in self.sizes:
            heapq.heappush(self.heap, -price if self.side is Side.BID else price)
        self.sizes[price] = size

    @property
    def best(self) -> int | None:
        while self.heap:
            candidate = self.heap[0]
            price = -candidate if self.side is Side.BID else candidate
            if price in self.sizes:
                return price
            heapq.heappop(self.heap)
        return None


class NaiveSortedList:
    """Sorted list rebuilt with sort() on every insert, for contrast."""

    def __init__(self, side: Side) -> None:
        self.side = side
        self.sizes: dict[int, int] = {}
        self.prices: list[int] = []

    def set(self, price: int, size: int) -> None:
        if size == 0:
            if self.sizes.pop(price, None) is not None:
                self.prices.remove(price)
            return
        if price not in self.sizes:
            self.prices.append(price)
            self.prices.sort()
        self.sizes[price] = size

    @property
    def best(self) -> int | None:
        if not self.prices:
            return None
        return self.prices[-1] if self.side is Side.BID else self.prices[0]


def workload(levels: int, operations: int, seed: int = 42):
    """Updates clustered near the touch, as real depth updates are."""
    rng = random.Random(seed)
    base = 100_000
    ops = []
    for _ in range(operations):
        # Most activity sits within a few ticks of the top of the book.
        offset = min(int(rng.expovariate(1 / 3.0)), levels - 1)
        price = base - offset
        size = 0 if rng.random() < 0.25 else rng.randint(1, 1_000)
        ops.append((price, size))
    return ops


def measure(container_factory, ops, reads_per_write: int = 2) -> float:
    container = container_factory(Side.BID)
    # Prime the book so removals have something to remove.
    for price, size in ops[:200]:
        container.set(price, size or 1)

    start = time.perf_counter()
    for price, size in ops:
        container.set(price, size)
        for _ in range(reads_per_write):
            _ = container.best
    return time.perf_counter() - start


def main() -> int:
    operations = 200_000
    print(f"{operations:,} updates, 2 best-price reads each\n")

    for levels in (10, 50, 500):
        ops = workload(levels, operations)
        print(f"  book depth {levels} levels")
        results = {}
        for name, factory in (
            ("dict + bisect (chosen)", PriceLevels),
            ("dict + sort on read", SortOnRead),
            ("dict + lazy heap", LazyHeap),
            ("dict + list.sort()", NaiveSortedList),
        ):
            elapsed = measure(factory, ops)
            results[name] = elapsed
            print(f"    {name:<24}{elapsed:7.3f} s")

        best = min(results.values())
        chosen = results["dict + bisect (chosen)"]
        verdict = "fastest" if chosen == best else f"{chosen / best:.2f}x off best"
        print(f"    -> chosen container is {verdict}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
