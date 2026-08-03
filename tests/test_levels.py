"""Unit and differential tests for the price level container."""

from __future__ import annotations

import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from obsim.levels import PriceLevels
from obsim.types import PriceSize, Side


class ReferenceLevels:
    """Deliberately naive levels, obviously correct, used as an oracle.

    No incremental maintenance: it sorts on every read. If the real container
    ever disagrees with this one, the real container is wrong.
    """

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

    def top(self, depth: int) -> list[PriceSize]:
        ordered = sorted(self.sizes, reverse=self.side is Side.BID)
        return [PriceSize(p, self.sizes[p]) for p in ordered[:depth]]


def test_empty_has_no_best():
    assert PriceLevels(Side.BID).best is None
    assert PriceLevels(Side.ASK).best is None


def test_bid_best_is_highest():
    levels = PriceLevels(Side.BID)
    for price in (100, 103, 101):
        levels.set(price, 5)
    assert levels.best == 103


def test_ask_best_is_lowest():
    levels = PriceLevels(Side.ASK)
    for price in (100, 103, 101):
        levels.set(price, 5)
    assert levels.best == 100


def test_zero_size_removes_level():
    levels = PriceLevels(Side.BID)
    levels.set(100, 5)
    levels.set(101, 7)
    levels.set(101, 0)
    assert levels.best == 100
    assert 101 not in levels
    assert len(levels) == 1


def test_removing_absent_level_is_a_no_op():
    levels = PriceLevels(Side.ASK)
    levels.set(100, 3)
    levels.set(999, 0)
    assert len(levels) == 1
    levels.check_invariants()


def test_resize_does_not_duplicate_price():
    levels = PriceLevels(Side.BID)
    levels.set(100, 5)
    levels.set(100, 9)
    levels.set(100, 1)
    assert len(levels) == 1
    assert levels.size_at(100) == 1
    assert levels.prices_ascending() == [100]


def test_negative_size_rejected():
    with pytest.raises(ValueError, match="negative size"):
        PriceLevels(Side.BID).set(100, -1)


def test_top_is_best_first_and_respects_depth():
    bids = PriceLevels(Side.BID)
    for price, size in ((98, 1), (99, 2), (100, 3), (101, 4)):
        bids.set(price, size)
    assert bids.top(2) == [PriceSize(101, 4), PriceSize(100, 3)]

    asks = PriceLevels(Side.ASK)
    for price, size in ((102, 1), (103, 2), (104, 3)):
        asks.set(price, size)
    assert asks.top(2) == [PriceSize(102, 1), PriceSize(103, 2)]


def test_top_handles_depth_beyond_available():
    levels = PriceLevels(Side.BID)
    levels.set(100, 1)
    assert levels.top(50) == [PriceSize(100, 1)]
    assert levels.top(0) == []
    assert levels.top(-1) == []


def test_clear_empties_both_structures():
    levels = PriceLevels(Side.BID)
    for price in range(100, 110):
        levels.set(price, 1)
    levels.clear()
    assert len(levels) == 0
    assert levels.best is None
    levels.check_invariants()


@pytest.mark.parametrize("side", [Side.BID, Side.ASK])
def test_differential_against_reference(side):
    """Random operations must leave both implementations agreeing exactly."""
    rng = random.Random(20260803)
    real, oracle = PriceLevels(side), ReferenceLevels(side)

    for _ in range(20_000):
        price = rng.randint(9_950, 10_050)
        # Zero often, so removals are exercised as heavily as insertions.
        size = 0 if rng.random() < 0.3 else rng.randint(1, 500)
        real.set(price, size)
        oracle.set(price, size)

        assert real.best == oracle.best
        assert len(real) == len(oracle.sizes)

    real.check_invariants()
    assert real.top(10) == oracle.top(10)
    assert real.total_size() == sum(oracle.sizes.values())


@settings(max_examples=200, deadline=None)
@given(
    side=st.sampled_from([Side.BID, Side.ASK]),
    ops=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=200),
            st.integers(min_value=0, max_value=1_000),
        ),
        min_size=0,
        max_size=300,
    ),
)
def test_property_invariants_hold_over_arbitrary_operations(side, ops):
    real, oracle = PriceLevels(side), ReferenceLevels(side)
    for price, size in ops:
        real.set(price, size)
        oracle.set(price, size)

    real.check_invariants()
    assert real.best == oracle.best
    assert real.top(5) == oracle.top(5)
    assert len(real) == len(oracle.sizes)
