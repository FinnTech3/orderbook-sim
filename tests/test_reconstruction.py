"""End-to-end reconstruction against a known-correct book.

The synthetic venue keeps the true book beside the stream it emits, so these
tests can assert something no unit test can: that replaying the messages
produces exactly the book the venue actually had, level for level.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from obsim.book import OrderBook
from obsim.feeds import SyntheticVenue
from obsim.sequencing import (
    ExplicitPredecessorRule,
    InferredContinuityRule,
    Synchroniser,
    SyncState,
)
from obsim.types import Instrument

INSTRUMENT = Instrument("SYNTH", Decimal("0.01"), Decimal("1"))


def assert_matches_truth(book: OrderBook, venue: SyntheticVenue) -> None:
    """Every level on both sides must agree exactly."""
    reconstructed_bids = {
        level.price: level.size for level in book.bids.top(10_000)
    }
    reconstructed_asks = {
        level.price: level.size for level in book.asks.top(10_000)
    }
    assert reconstructed_bids == venue.true_bids()
    assert reconstructed_asks == venue.true_asks()
    assert book.best_bid == venue.best_bid
    assert book.best_ask == venue.best_ask
    book.check_invariants()


def test_clean_stream_reconstructs_exactly():
    venue = SyntheticVenue(seed=1)
    sync = Synchroniser(OrderBook(INSTRUMENT))
    sync.on_snapshot(venue.snapshot())

    for _ in range(5_000):
        step = venue.step()
        assert sync.on_delta(step.delta) is True

    assert_matches_truth(sync.book, venue)
    assert sync.stats.gaps_detected == 0
    assert sync.stats.deltas_applied == 5_000


def test_venue_never_produces_a_crossed_book():
    venue = SyntheticVenue(seed=2)
    for _ in range(20_000):
        venue.step()
        assert venue.best_bid < venue.best_ask


def test_trade_never_empties_a_side():
    """Regression: a single lot on the only level used to clear the side.

    The fill size was clamped with max(1, resting - 1), which for resting == 1
    still took the whole level. Found by the property test below; pinned here
    by building the exact state rather than relying on a seed to rediscover it.
    The internals are reached into deliberately — no public API can set up a
    one-lot-one-level book, and that is the state that broke.
    """
    venue = SyntheticVenue(seed=0, depth=1)
    venue._bids.clear()
    venue._asks.clear()
    venue._bids[9_999] = 1
    venue._asks[10_001] = 1

    for _ in range(2_000):
        venue.step()
        assert venue.true_bids(), "bid side emptied"
        assert venue.true_asks(), "ask side emptied"
        assert venue.best_bid < venue.best_ask


def test_book_keeps_its_depth_over_a_long_run():
    """Regression: the book used to drain to one level per side.

    Trades remove levels as well as the remove action does, so a fixed
    add/remove split loses depth steadily. After a few thousand events the
    book was a single price on each side, which made every depth metric
    meaningless and the demo look like a dead market.
    """
    venue = SyntheticVenue(seed=12, depth=8)
    for _ in range(20_000):
        venue.step()

    assert len(venue.true_bids()) >= 4
    assert len(venue.true_asks()) >= 4
    # And it must not run away in the other direction either.
    assert len(venue.true_bids()) <= 40
    assert len(venue.true_asks()) <= 40


def test_same_seed_produces_an_identical_stream():
    left = [s.delta for s in SyntheticVenue(seed=7).stream(500)]
    right = [s.delta for s in SyntheticVenue(seed=7).stream(500)]
    assert left == right


def test_different_seeds_diverge():
    left = [s.delta for s in SyntheticVenue(seed=7).stream(500)]
    right = [s.delta for s in SyntheticVenue(seed=8).stream(500)]
    assert left != right


def test_snapshot_taken_mid_stream_still_reconstructs():
    """Join a stream already in progress, as a real client always does."""
    venue = SyntheticVenue(seed=3)
    for _ in range(1_000):
        venue.step()

    sync = Synchroniser(OrderBook(INSTRUMENT))
    sync.on_snapshot(venue.snapshot())
    for _ in range(1_000):
        sync.on_delta(venue.step().delta)

    assert_matches_truth(sync.book, venue)


def test_dropped_messages_are_detected_and_recovered_from():
    """Drop a message, confirm the gap is caught, then resync and verify."""
    venue = SyntheticVenue(seed=4)
    sync = Synchroniser(OrderBook(INSTRUMENT))
    sync.on_snapshot(venue.snapshot())

    for _ in range(200):
        sync.on_delta(venue.step().delta)
    assert sync.state is SyncState.SYNCED

    venue.step()  # generated but never delivered
    assert sync.on_delta(venue.step().delta) is False
    assert sync.stats.gaps_detected == 1
    assert sync.state is SyncState.BUFFERING

    # Recover the way a real client does: fetch a fresh snapshot.
    assert sync.on_snapshot(venue.snapshot()) is True
    for _ in range(200):
        sync.on_delta(venue.step().delta)

    assert_matches_truth(sync.book, venue)


def test_buffering_before_the_snapshot_arrives():
    """Deltas seen while the snapshot request is in flight must not be lost."""
    venue = SyntheticVenue(seed=5)
    sync = Synchroniser(OrderBook(INSTRUMENT))

    snapshot = venue.snapshot()
    in_flight = [venue.step().delta for _ in range(50)]
    for delta in in_flight:
        assert sync.on_delta(delta) is False
    assert sync.buffered == 50

    assert sync.on_snapshot(snapshot) is True
    assert_matches_truth(sync.book, venue)
    assert sync.stats.deltas_applied == 50


def test_explicit_predecessor_rule_reconstructs_the_same_book():
    venue = SyntheticVenue(seed=6)
    sync = Synchroniser(OrderBook(INSTRUMENT), ExplicitPredecessorRule())
    sync.on_snapshot(venue.snapshot())
    for _ in range(2_000):
        assert sync.on_delta(venue.step().delta) is True
    assert_matches_truth(sync.book, venue)


def test_duplicated_delivery_does_not_corrupt_the_book():
    venue = SyntheticVenue(seed=9)
    sync = Synchroniser(OrderBook(INSTRUMENT))
    sync.on_snapshot(venue.snapshot())

    for _ in range(500):
        delta = venue.step().delta
        sync.on_delta(delta)
        sync.on_delta(delta)  # delivered twice

    assert_matches_truth(sync.book, venue)
    assert sync.stats.gaps_detected == 0
    assert sync.stats.deltas_discarded_duplicate == 500


def test_trades_are_consistent_with_the_depth_change():
    """A trade must be matched by the level shrinking by the same amount."""
    venue = SyntheticVenue(seed=11)
    for _ in range(5_000):
        before_bids, before_asks = venue.true_bids(), venue.true_asks()
        step = venue.step()
        if not step.trades:
            continue
        trade = step.trades[0]
        resting_side = trade.aggressor.opposite
        before = (before_bids if resting_side.value == "bid" else before_asks)
        after = (
            venue.true_bids() if resting_side.value == "bid" else venue.true_asks()
        )
        shrank = before[trade.price] - after.get(trade.price, 0)
        assert shrank == trade.size


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(seed=st.integers(min_value=0, max_value=10_000), count=st.integers(20, 400))
def test_property_any_seeded_stream_reconstructs(seed, count):
    venue = SyntheticVenue(seed=seed)
    sync = Synchroniser(OrderBook(INSTRUMENT), InferredContinuityRule())
    sync.on_snapshot(venue.snapshot())
    for _ in range(count):
        sync.on_delta(venue.step().delta)
    assert_matches_truth(sync.book, venue)
