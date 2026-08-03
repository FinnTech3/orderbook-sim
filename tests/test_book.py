"""Tests for the order book and the instrument grid conversion."""

from __future__ import annotations

from decimal import Decimal

import pytest

from obsim.book import CrossedBook, OrderBook
from obsim.types import (
    DepthDelta,
    Instrument,
    OffGridPrice,
    PriceSize,
    Side,
    Snapshot,
)

INSTRUMENT = Instrument("BTCUSDT", Decimal("0.01"), Decimal("0.00001"))


def book() -> OrderBook:
    return OrderBook(INSTRUMENT)


def snapshot(bids, asks, last_id=100, when=1_000):
    return Snapshot(
        last_id=last_id,
        event_time_ns=when,
        bids=tuple(PriceSize(p, s) for p, s in bids),
        asks=tuple(PriceSize(p, s) for p, s in asks),
    )


# ---- instrument grid ----------------------------------------------------


def test_prices_convert_to_ticks():
    assert INSTRUMENT.to_ticks(Decimal("100.00")) == 10_000
    assert INSTRUMENT.to_ticks("100.01") == 10_001
    assert INSTRUMENT.from_ticks(10_001) == Decimal("100.01")


def test_off_grid_price_raises_rather_than_rounding():
    with pytest.raises(OffGridPrice, match="not a multiple"):
        INSTRUMENT.to_ticks("100.005")


def test_round_trip_is_exact_for_values_that_break_floats():
    # 0.1 + 0.2 != 0.3 in binary floating point. On the tick grid it is exact.
    grid = Instrument("T", Decimal("0.1"), Decimal("1"))
    assert grid.to_ticks("0.1") + grid.to_ticks("0.2") == grid.to_ticks("0.3")


def test_non_positive_grid_rejected():
    with pytest.raises(ValueError, match="tick_size must be positive"):
        Instrument("T", Decimal("0"), Decimal("1"))
    with pytest.raises(ValueError, match="lot_size must be positive"):
        Instrument("T", Decimal("1"), Decimal("-1"))


# ---- snapshots and deltas ----------------------------------------------


def test_snapshot_populates_both_sides():
    b = book()
    b.load_snapshot(snapshot([(99, 5), (98, 3)], [(101, 4), (102, 6)]))
    assert b.best_bid == 99
    assert b.best_ask == 101
    assert b.spread == 2
    assert b.last_update_id == 100


def test_snapshot_replaces_rather_than_merges():
    b = book()
    b.load_snapshot(snapshot([(99, 5)], [(101, 4)]))
    b.load_snapshot(snapshot([(90, 1)], [(110, 1)], last_id=200))
    assert b.best_bid == 90
    assert b.best_ask == 110
    assert len(b.bids) == 1
    assert b.last_update_id == 200


def test_delta_updates_and_removes_levels():
    b = book()
    b.load_snapshot(snapshot([(99, 5), (98, 3)], [(101, 4)]))
    b.apply_delta(
        DepthDelta(
            first_id=101,
            final_id=105,
            event_time_ns=2_000,
            bids=(PriceSize(99, 0), PriceSize(97, 9)),
            asks=(PriceSize(101, 7),),
        )
    )
    assert b.best_bid == 98
    assert b.size_at(Side.BID, 97) == 9
    assert b.size_at(Side.ASK, 101) == 7
    assert b.last_update_id == 105
    assert b.event_time_ns == 2_000


def test_empty_side_yields_none_metrics():
    b = book()
    b.load_snapshot(snapshot([(99, 5)], []))
    assert b.best_ask is None
    assert b.spread is None
    assert b.mid is None
    assert b.microprice is None


# ---- crossing -----------------------------------------------------------


def test_crossed_book_raises():
    b = book()
    b.load_snapshot(snapshot([(99, 5)], [(101, 4)]))
    with pytest.raises(CrossedBook):
        b.apply_delta(
            DepthDelta(
                first_id=101,
                final_id=101,
                event_time_ns=2_000,
                bids=(PriceSize(102, 1),),
            )
        )


def test_touching_prices_count_as_crossed():
    b = book()
    with pytest.raises(CrossedBook):
        b.load_snapshot(snapshot([(100, 1)], [(100, 1)]))


def test_transient_cross_within_one_message_is_allowed():
    """A message that lifts the bid and clears the old ask is legitimate.

    Checking level by level would reject this; checking once the whole message
    is applied accepts it. This is the case that motivates the design.
    """
    b = book()
    b.load_snapshot(snapshot([(99, 5)], [(101, 4)]))
    b.apply_delta(
        DepthDelta(
            first_id=101,
            final_id=101,
            event_time_ns=2_000,
            bids=(PriceSize(102, 3),),  # alone, this would cross
            asks=(PriceSize(101, 0), PriceSize(103, 2)),  # but the ask moves too
        )
    )
    assert b.best_bid == 102
    assert b.best_ask == 103


# ---- derived quantities -------------------------------------------------


def test_mid_can_be_a_half_tick():
    b = book()
    b.load_snapshot(snapshot([(100, 1)], [(101, 1)]))
    assert b.mid == Decimal("100.5")


def test_microprice_leans_away_from_the_heavy_side():
    b = book()
    b.load_snapshot(snapshot([(100, 10)], [(101, 1)]))
    # Heavy bid depth means the next print is more likely at the ask.
    assert b.microprice > b.mid
    assert b.microprice == (Decimal(101) * 10 + Decimal(100) * 1) / 11


def test_microprice_equals_mid_when_balanced():
    b = book()
    b.load_snapshot(snapshot([(100, 7)], [(101, 7)]))
    assert b.microprice == b.mid


def test_levels_returns_requested_depth_best_first():
    b = book()
    b.load_snapshot(
        snapshot([(99, 1), (98, 2), (97, 3)], [(101, 4), (102, 5)]),
    )
    assert b.levels(Side.BID, 2) == [PriceSize(99, 1), PriceSize(98, 2)]
    assert b.levels(Side.ASK, 5) == [PriceSize(101, 4), PriceSize(102, 5)]


def test_clear_resets_metadata():
    b = book()
    b.load_snapshot(snapshot([(99, 5)], [(101, 4)]))
    b.clear()
    assert b.best_bid is None
    assert b.last_update_id is None
    assert b.event_time_ns is None


def test_delta_with_inverted_ids_rejected():
    with pytest.raises(ValueError, match="precedes"):
        DepthDelta(first_id=10, final_id=5, event_time_ns=0)
