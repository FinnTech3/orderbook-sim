"""A venue that makes up its own market data.

The point of this is not realism. It is that the venue keeps the true book
alongside the stream it emits, so a test can reconstruct from the stream and
compare against what the venue knows to be correct. Nothing else in the test
suite can make that comparison.

Everything is driven by a seeded generator, so a given seed always produces
the same stream, and a failing case can be reproduced from its seed alone.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..types import DepthDelta, PriceSize, Side, Snapshot, Trade


@dataclass(frozen=True, slots=True)
class Step:
    """What happened in one tick of the venue's life."""

    delta: DepthDelta
    trades: tuple[Trade, ...] = ()


@dataclass
class SyntheticVenue:
    """Generates a self-consistent depth stream and knows the truth.

    The book is kept uncrossed by construction: a new bid is only ever placed
    below the best ask, and a new ask only ever above the best bid, so no
    sequence of steps can produce a crossed state. If reconstruction ever
    crosses, the bug is in reconstruction.
    """

    start_price: int = 10_000
    depth: int = 8
    seed: int = 0
    max_size: int = 500

    _rng: random.Random = field(init=False, repr=False)
    _bids: dict[int, int] = field(init=False, default_factory=dict, repr=False)
    _asks: dict[int, int] = field(init=False, default_factory=dict, repr=False)
    _update_id: int = field(init=False, default=0, repr=False)
    _clock_ns: int = field(init=False, default=0, repr=False)

    def __post_init__(self) -> None:
        if self.depth < 1:
            raise ValueError("depth must be at least 1")
        self._rng = random.Random(self.seed)
        self._seed_book()

    # ---- truth -------------------------------------------------------

    @property
    def best_bid(self) -> int:
        return max(self._bids)

    @property
    def best_ask(self) -> int:
        return min(self._asks)

    def true_bids(self) -> dict[int, int]:
        return dict(self._bids)

    def true_asks(self) -> dict[int, int]:
        return dict(self._asks)

    @property
    def update_id(self) -> int:
        return self._update_id

    def snapshot(self) -> Snapshot:
        """The current true state, as the venue's REST endpoint would give it."""
        return Snapshot(
            last_id=self._update_id,
            event_time_ns=self._clock_ns,
            bids=tuple(
                PriceSize(p, s) for p, s in sorted(self._bids.items(), reverse=True)
            ),
            asks=tuple(PriceSize(p, s) for p, s in sorted(self._asks.items())),
        )

    # ---- generation --------------------------------------------------

    def step(self) -> Step:
        """Advance one message and return the delta describing the change."""
        self._update_id += 1
        self._clock_ns += self._rng.randint(1_000_000, 50_000_000)

        action = self._rng.random()
        if action < 0.15:
            return self._trade()
        if action < 0.35:
            return self._add_level()
        if action < 0.55:
            return self._remove_level()
        return self._resize_level()

    def stream(self, count: int) -> list[Step]:
        return [self.step() for _ in range(count)]

    # ---- actions -----------------------------------------------------

    def _trade(self) -> Step:
        """An aggressor eats into the touch."""
        side = self._rng.choice((Side.BID, Side.ASK))
        levels = self._bids if side is Side.BID else self._asks
        price = self.best_bid if side is Side.BID else self.best_ask

        resting = levels[price]

        # A single lot on the only level cannot be partially filled without
        # emptying the side, so do something else this tick instead.
        if len(levels) == 1 and resting == 1:
            return self._add_level()

        filled = self._rng.randint(1, resting)
        remaining = resting - filled
        if remaining == 0 and len(levels) == 1:
            # Leave the touch standing. resting >= 2 here, so this is safe.
            filled = resting - 1
            remaining = 1

        self._write(levels, price, remaining)
        trade = Trade(
            price=price,
            size=filled,
            # The side that crossed is the opposite of the resting side.
            aggressor=side.opposite,
            event_time_ns=self._clock_ns,
        )
        return Step(self._delta_for(side, price, remaining), (trade,))

    def _add_level(self) -> Step:
        """Place a new level, always on the safe side of the opposing touch."""
        side = self._rng.choice((Side.BID, Side.ASK))
        if side is Side.BID:
            highest = self.best_ask - 1
            price = self._rng.randint(max(1, highest - self.depth), highest)
            levels = self._bids
        else:
            lowest = self.best_bid + 1
            price = self._rng.randint(lowest, lowest + self.depth)
            levels = self._asks

        size = self._rng.randint(1, self.max_size)
        self._write(levels, price, size)
        return Step(self._delta_for(side, price, size))

    def _remove_level(self) -> Step:
        side = self._rng.choice((Side.BID, Side.ASK))
        levels = self._bids if side is Side.BID else self._asks
        if len(levels) <= 1:
            return self._resize_level()

        price = self._rng.choice(sorted(levels))
        self._write(levels, price, 0)
        return Step(self._delta_for(side, price, 0))

    def _resize_level(self) -> Step:
        side = self._rng.choice((Side.BID, Side.ASK))
        levels = self._bids if side is Side.BID else self._asks
        price = self._rng.choice(sorted(levels))
        size = self._rng.randint(1, self.max_size)
        self._write(levels, price, size)
        return Step(self._delta_for(side, price, size))

    # ---- internals ---------------------------------------------------

    def _seed_book(self) -> None:
        mid = self.start_price
        for offset in range(1, self.depth + 1):
            self._bids[mid - offset] = self._rng.randint(1, self.max_size)
            self._asks[mid + offset] = self._rng.randint(1, self.max_size)

    @staticmethod
    def _write(levels: dict[int, int], price: int, size: int) -> None:
        if size == 0:
            levels.pop(price, None)
        else:
            levels[price] = size

    def _delta_for(self, side: Side, price: int, size: int) -> DepthDelta:
        level = (PriceSize(price, size),)
        return DepthDelta(
            first_id=self._update_id,
            final_id=self._update_id,
            event_time_ns=self._clock_ns,
            bids=level if side is Side.BID else (),
            asks=level if side is Side.ASK else (),
            prev_final_id=self._update_id - 1,
        )
