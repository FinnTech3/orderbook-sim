"""Tests for buffering, gap detection, and resynchronisation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from obsim.book import OrderBook
from obsim.sequencing import (
    ExplicitPredecessorRule,
    Gap,
    InferredContinuityRule,
    Synchroniser,
    SyncState,
)
from obsim.types import DepthDelta, Instrument, PriceSize, Snapshot

INSTRUMENT = Instrument("TEST", Decimal("1"), Decimal("1"))


def sync(rule=None, **kwargs) -> Synchroniser:
    return Synchroniser(OrderBook(INSTRUMENT), rule, **kwargs)


def delta(first, final, *, bids=(), asks=(), prev=None, when=0) -> DepthDelta:
    return DepthDelta(
        first_id=first,
        final_id=final,
        event_time_ns=when,
        bids=tuple(PriceSize(p, s) for p, s in bids),
        asks=tuple(PriceSize(p, s) for p, s in asks),
        prev_final_id=prev,
    )


def snap(last_id, *, bids=((99, 5),), asks=((101, 5),), when=0) -> Snapshot:
    return Snapshot(
        last_id=last_id,
        event_time_ns=when,
        bids=tuple(PriceSize(p, s) for p, s in bids),
        asks=tuple(PriceSize(p, s) for p, s in asks),
    )


# ---- the happy path -----------------------------------------------------


def test_starts_disconnected_and_wants_a_snapshot():
    s = sync()
    assert s.state is SyncState.DISCONNECTED
    assert s.needs_snapshot


def test_deltas_before_a_snapshot_are_buffered_not_applied():
    s = sync()
    assert s.on_delta(delta(101, 102)) is False
    assert s.state is SyncState.BUFFERING
    assert s.buffered == 1
    assert s.book.best_bid is None


def test_snapshot_drains_the_buffer_in_order():
    s = sync()
    s.on_delta(delta(95, 99, bids=((98, 1),)))  # stale, superseded
    s.on_delta(delta(99, 101, bids=((97, 7),)))  # bridges the snapshot
    s.on_delta(delta(102, 103, bids=((96, 3),)))  # follows

    assert s.on_snapshot(snap(100)) is True
    assert s.state is SyncState.SYNCED
    assert s.last_applied_id == 103
    assert s.book.size_at(s.book.bids.side, 97) == 7
    assert s.book.size_at(s.book.bids.side, 96) == 3
    # The stale delta must not have been applied.
    assert s.book.size_at(s.book.bids.side, 98) == 0
    assert s.stats.deltas_discarded_stale == 1
    assert s.stats.deltas_applied == 2


def test_deltas_apply_directly_once_synced():
    s = sync()
    s.on_snapshot(snap(100))
    assert s.on_delta(delta(101, 101, bids=((98, 4),))) is True
    assert s.last_applied_id == 101
    assert s.book.size_at(s.book.bids.side, 98) == 4


def test_snapshot_with_empty_buffer_syncs_immediately():
    s = sync()
    assert s.on_snapshot(snap(100)) is True
    assert s.last_applied_id == 100
    assert not s.needs_snapshot


# ---- gaps ---------------------------------------------------------------


def test_gap_triggers_resync_and_clears_the_book():
    seen: list[Gap] = []
    s = sync(on_gap=seen.append)
    s.on_snapshot(snap(100))
    s.on_delta(delta(101, 101))
    assert s.state is SyncState.SYNCED

    # 103 does not follow 101 — message 102 was lost.
    assert s.on_delta(delta(103, 103)) is False
    assert s.state is SyncState.BUFFERING
    assert s.book.best_bid is None
    assert s.stats.gaps_detected == 1
    assert len(seen) == 1
    assert seen[0].expected_after == 101
    assert seen[0].got_first_id == 103


def test_delta_that_exposed_the_gap_is_kept_for_after_the_resync():
    s = sync()
    s.on_snapshot(snap(100))
    s.on_delta(delta(103, 103, bids=((97, 2),)))  # gap
    assert s.buffered == 1

    # A fresh snapshot at 102 lets the held delta apply.
    assert s.on_snapshot(snap(102)) is True
    assert s.book.size_at(s.book.bids.side, 97) == 2
    assert s.last_applied_id == 103


def test_recovers_fully_after_a_gap():
    s = sync()
    s.on_snapshot(snap(100))
    s.on_delta(delta(105, 105))  # gap
    s.on_snapshot(snap(104))
    assert s.state is SyncState.SYNCED
    assert s.on_delta(delta(106, 106, bids=((98, 9),))) is True
    assert s.book.size_at(s.book.bids.side, 98) == 9


# ---- snapshots that cannot be used --------------------------------------


def test_snapshot_landing_in_a_hole_is_rejected():
    """Buffer starts after the snapshot ends, so messages between are gone."""
    s = sync()
    s.on_delta(delta(110, 111))
    assert s.on_snapshot(snap(100)) is False
    assert s.state is SyncState.BUFFERING
    assert s.needs_snapshot
    assert s.stats.snapshots_rejected == 1
    assert s.book.best_bid is None


def test_a_newer_snapshot_resolves_the_rejection():
    s = sync()
    s.on_delta(delta(110, 111, bids=((97, 4),)))
    s.on_snapshot(snap(100))
    assert s.on_snapshot(snap(109)) is True
    assert s.book.size_at(s.book.bids.side, 97) == 4


def test_snapshot_while_already_synced_is_a_no_op():
    s = sync()
    s.on_snapshot(snap(100))
    before = s.stats.snapshots_applied
    assert s.on_snapshot(snap(200)) is True
    assert s.stats.snapshots_applied == before
    assert s.last_applied_id == 100


# ---- buffer bounds ------------------------------------------------------

def test_buffer_is_bounded_and_drops_the_oldest():
    s = sync(max_buffer=3)
    for i in range(101, 111):
        s.on_delta(delta(i, i))
    assert s.buffered == 3
    assert s.stats.deltas_dropped_overflow == 7


def test_zero_buffer_rejected():
    with pytest.raises(ValueError, match="at least 1"):
        sync(max_buffer=0)


def test_out_of_order_arrival_is_counted_and_repaired():
    s = sync()
    s.on_delta(delta(102, 103, bids=((96, 2),)))
    s.on_delta(delta(99, 101, bids=((97, 1),)))  # arrived late
    assert s.on_snapshot(snap(100)) is True
    assert s.stats.out_of_order_arrivals == 1
    assert s.last_applied_id == 103


# ---- venue rules --------------------------------------------------------


def test_explicit_predecessor_rule_follows_the_named_id():
    s = sync(ExplicitPredecessorRule())
    s.on_snapshot(snap(100))
    assert s.on_delta(delta(101, 105, prev=100)) is True
    assert s.on_delta(delta(106, 110, prev=105)) is True
    assert s.last_applied_id == 110


def test_explicit_predecessor_rule_catches_a_gap_that_ids_would_hide():
    """IDs are adjacent but the named predecessor is wrong.

    Inferred continuity would accept this. The explicit rule does not, which
    is the entire reason a venue bothers to send the field.
    """
    s = sync(ExplicitPredecessorRule())
    s.on_snapshot(snap(100))
    s.on_delta(delta(101, 105, prev=100))
    assert s.on_delta(delta(106, 110, prev=99)) is False
    assert s.stats.gaps_detected == 1

    inferred = sync(InferredContinuityRule())
    inferred.on_snapshot(snap(100))
    inferred.on_delta(delta(101, 105))
    assert inferred.on_delta(delta(106, 110, prev=99)) is True


def test_explicit_rule_complains_when_the_field_is_missing():
    s = sync(ExplicitPredecessorRule())
    s.on_snapshot(snap(100))
    with pytest.raises(ValueError, match="right rule"):
        s.on_delta(delta(101, 105))


# ---- lifecycle ----------------------------------------------------------


def test_duplicate_delta_is_discarded_without_a_resync():
    """A replayed message must not be mistaken for a gap.

    Venues retransmit after a reconnect. Treating that as a break in
    continuity would throw away a correct book and force a pointless
    snapshot fetch.
    """
    s = sync()
    s.on_snapshot(snap(100))
    s.on_delta(delta(101, 102, bids=((98, 4),)))
    assert s.state is SyncState.SYNCED

    assert s.on_delta(delta(101, 102, bids=((98, 4),))) is False
    assert s.state is SyncState.SYNCED
    assert s.stats.gaps_detected == 0
    assert s.stats.deltas_discarded_duplicate == 1
    assert s.book.size_at(s.book.bids.side, 98) == 4
    assert s.last_applied_id == 102

    # And the stream carries on normally.
    assert s.on_delta(delta(103, 103, bids=((97, 1),))) is True


def test_wholly_older_delta_is_discarded_not_treated_as_a_gap():
    s = sync()
    s.on_snapshot(snap(100))
    s.on_delta(delta(101, 110))
    assert s.on_delta(delta(102, 104)) is False
    assert s.stats.gaps_detected == 0
    assert s.stats.deltas_discarded_duplicate == 1


def test_reset_returns_to_disconnected():
    s = sync()
    s.on_snapshot(snap(100))
    s.on_delta(delta(101, 101))
    s.reset()
    assert s.state is SyncState.DISCONNECTED
    assert s.book.best_bid is None
    assert s.last_applied_id is None
    assert s.buffered == 0


def test_stats_round_trip_to_a_dict():
    s = sync()
    s.on_snapshot(snap(100))
    s.on_delta(delta(101, 101))
    stats = s.stats.as_dict()
    assert stats["deltas_applied"] == 1
    assert stats["snapshots_applied"] == 1
    assert set(stats) >= {"gaps_detected", "deltas_seen"}
