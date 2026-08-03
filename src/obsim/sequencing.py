"""Getting the events into the right order, and noticing when they are not.

A depth stream is only useful if you can prove you have every message. This
module owns that proof. It buffers deltas until a snapshot arrives, discards
the ones the snapshot already accounts for, identifies the single delta that
straddles the snapshot, and from then on refuses to apply anything that does
not follow the last message applied.

Venues express continuity differently, so the rule is pluggable and the state
machine is not.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

from .book import OrderBook
from .types import DepthDelta, Snapshot

DEFAULT_MAX_BUFFER = 10_000


class SyncState(Enum):
    #: No book, no snapshot. Deltas are buffered.
    DISCONNECTED = "disconnected"
    #: Deltas are buffered while we wait for a snapshot to anchor them.
    BUFFERING = "buffering"
    #: Book is live and every delta since the snapshot has been accounted for.
    SYNCED = "synced"


class SequenceRule(Protocol):
    """How one venue expresses message continuity."""

    def is_stale(self, snapshot_last_id: int, delta: DepthDelta) -> bool:
        """True if the snapshot already includes everything in this delta."""

    def bridges(self, snapshot_last_id: int, delta: DepthDelta) -> bool:
        """True if this is the correct first delta to apply after the snapshot."""

    def follows(self, prev_final_id: int, delta: DepthDelta) -> bool:
        """True if this delta directly follows the last one applied."""


class InferredContinuityRule:
    """Continuity inferred from ID adjacency.

    Each message carries the first and last update ID it covers, and the next
    message must start exactly one past where the previous one ended. Binance
    spot works this way.
    """

    def is_stale(self, snapshot_last_id: int, delta: DepthDelta) -> bool:
        return delta.final_id <= snapshot_last_id

    def bridges(self, snapshot_last_id: int, delta: DepthDelta) -> bool:
        return delta.first_id <= snapshot_last_id + 1 <= delta.final_id

    def follows(self, prev_final_id: int, delta: DepthDelta) -> bool:
        return delta.first_id == prev_final_id + 1


class ExplicitPredecessorRule:
    """Continuity stated outright by the venue.

    Each message names the final ID of the message that should precede it, so
    a gap is detected by mismatch rather than by arithmetic. Binance USD-M
    futures works this way. Stricter than inferring adjacency, because it
    catches a dropped message even if IDs happen to line up.
    """

    def is_stale(self, snapshot_last_id: int, delta: DepthDelta) -> bool:
        return delta.final_id < snapshot_last_id

    def bridges(self, snapshot_last_id: int, delta: DepthDelta) -> bool:
        return delta.first_id <= snapshot_last_id <= delta.final_id

    def follows(self, prev_final_id: int, delta: DepthDelta) -> bool:
        if delta.prev_final_id is None:
            raise ValueError(
                "ExplicitPredecessorRule needs prev_final_id, but the delta "
                "did not carry one — is this feed using the right rule?"
            )
        return delta.prev_final_id == prev_final_id


@dataclass
class SyncStats:
    """Counters. Every one of these is a thing worth knowing went wrong."""

    deltas_seen: int = 0
    deltas_applied: int = 0
    deltas_buffered: int = 0
    #: Superseded by a snapshot. Expected, not a problem.
    deltas_discarded_stale: int = 0
    #: Retransmitted by the venue after we had already applied them.
    deltas_discarded_duplicate: int = 0
    #: Dropped because the buffer filled before a snapshot arrived.
    deltas_dropped_overflow: int = 0
    #: Continuity broke. Each one is a resynchronisation.
    gaps_detected: int = 0
    snapshots_applied: int = 0
    #: Snapshot arrived but could not be reconciled with the buffer.
    snapshots_rejected: int = 0
    #: Buffer was not already in ID order. Should be zero on a single stream.
    out_of_order_arrivals: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }


@dataclass
class Gap:
    """A detected break in continuity."""

    expected_after: int
    got_first_id: int
    got_final_id: int
    event_time_ns: int

    def __str__(self) -> str:
        return (
            f"gap: expected a delta following {self.expected_after}, "
            f"got one covering {self.got_first_id}..{self.got_final_id}"
        )


class Synchroniser:
    """Drives an :class:`~obsim.book.OrderBook` from an unreliable stream.

    Feed it deltas and snapshots in whatever order they arrive. It applies what
    it can prove is correct, and asks for a new snapshot when it cannot.
    """

    def __init__(
        self,
        book: OrderBook,
        rule: SequenceRule | None = None,
        *,
        max_buffer: int = DEFAULT_MAX_BUFFER,
        on_gap: Callable[[Gap], None] | None = None,
    ) -> None:
        if max_buffer < 1:
            raise ValueError("max_buffer must be at least 1")
        self.book = book
        self.rule = rule if rule is not None else InferredContinuityRule()
        self.max_buffer = max_buffer
        self.stats = SyncStats()
        self.state = SyncState.DISCONNECTED
        self._buffer: deque[DepthDelta] = deque()
        self._last_applied_id: int | None = None
        self._on_gap = on_gap

    # ---- properties --------------------------------------------------

    @property
    def needs_snapshot(self) -> bool:
        """True when the caller should fetch a snapshot and hand it over."""
        return self.state is not SyncState.SYNCED

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    @property
    def last_applied_id(self) -> int | None:
        return self._last_applied_id

    # ---- inputs ------------------------------------------------------

    def on_delta(self, delta: DepthDelta) -> bool:
        """Offer one delta. Returns True if it was applied to the book."""
        self.stats.deltas_seen += 1

        if self.state is SyncState.SYNCED:
            assert self._last_applied_id is not None

            if delta.final_id <= self._last_applied_id:
                # Already applied. Venues replay messages after a reconnect,
                # and tearing down a good book over a duplicate would turn a
                # harmless retransmission into a real outage.
                self.stats.deltas_discarded_duplicate += 1
                return False

            if self.rule.follows(self._last_applied_id, delta):
                self.book.apply_delta(delta)
                self._last_applied_id = delta.final_id
                self.stats.deltas_applied += 1
                return True

            self._handle_gap(delta)
            return False

        self._buffer_delta(delta)
        return False

    def on_snapshot(self, snapshot: Snapshot) -> bool:
        """Offer a snapshot. Returns True if the book is synced afterwards.

        A snapshot is only usable if the buffer can be joined to it without a
        hole. If the earliest buffered delta already starts after the snapshot
        ends, messages were lost in between and no amount of replaying fixes
        it — the snapshot is rejected and a newer one is needed.
        """
        if self.state is SyncState.SYNCED:
            return True

        ordered = self._ordered_buffer()
        fresh = [
            delta
            for delta in ordered
            if not self.rule.is_stale(snapshot.last_id, delta)
        ]
        self.stats.deltas_discarded_stale += len(ordered) - len(fresh)

        if fresh and not self.rule.bridges(snapshot.last_id, fresh[0]):
            # The snapshot lands in a hole. Keep buffering and ask again.
            self.stats.snapshots_rejected += 1
            self._buffer = deque(fresh)
            self.state = SyncState.BUFFERING
            return False

        self.book.load_snapshot(snapshot)
        self._last_applied_id = snapshot.last_id
        self.stats.snapshots_applied += 1

        for index, delta in enumerate(fresh):
            is_bridge = index == 0 and self.rule.bridges(snapshot.last_id, delta)
            if not is_bridge and not self.rule.follows(
                self._last_applied_id, delta
            ):
                # A hole inside the buffer itself.
                self._handle_gap(delta)
                return False
            self.book.apply_delta(delta)
            self._last_applied_id = delta.final_id
            self.stats.deltas_applied += 1

        self._buffer.clear()
        self.state = SyncState.SYNCED
        return True

    def reset(self) -> None:
        """Drop everything and start over. Use on reconnect."""
        self.book.clear()
        self._buffer.clear()
        self._last_applied_id = None
        self.state = SyncState.DISCONNECTED

    # ---- internals ---------------------------------------------------

    def _buffer_delta(self, delta: DepthDelta) -> None:
        if len(self._buffer) >= self.max_buffer:
            # Keep the newest. The oldest are the ones a future snapshot is
            # most likely to supersede anyway.
            self._buffer.popleft()
            self.stats.deltas_dropped_overflow += 1
        self._buffer.append(delta)
        self.stats.deltas_buffered += 1
        if self.state is SyncState.DISCONNECTED:
            self.state = SyncState.BUFFERING

    def _ordered_buffer(self) -> list[DepthDelta]:
        buffered = list(self._buffer)
        ordered = sorted(buffered, key=lambda d: (d.first_id, d.final_id))
        if ordered != buffered:
            # Ordering is guaranteed on a single connection. If this fires,
            # the transport is doing something unexpected and it is worth
            # surfacing rather than silently repairing.
            self.stats.out_of_order_arrivals += 1
        return ordered

    def _handle_gap(self, delta: DepthDelta) -> None:
        assert self._last_applied_id is not None
        gap = Gap(
            expected_after=self._last_applied_id,
            got_first_id=delta.first_id,
            got_final_id=delta.final_id,
            event_time_ns=delta.event_time_ns,
        )
        self.stats.gaps_detected += 1
        self.book.clear()
        self._buffer.clear()
        self._last_applied_id = None
        self.state = SyncState.BUFFERING
        # The delta that exposed the gap is still needed once we resync.
        self._buffer_delta(delta)
        if self._on_gap is not None:
            self._on_gap(gap)
