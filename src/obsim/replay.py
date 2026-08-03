"""Drives a stream through sequencing, the book, and the simulator in order.

This is the piece that decides what happens when the feed breaks mid-run.
A backtest that quietly carries on through a desynchronisation is reporting
fills it has no basis for, so this stops, drops the resting orders, and counts
the event.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .book import OrderBook
from .queue_model import QueueModel
from .sequencing import SequenceRule, Synchroniser, SyncState
from .simulator import Fill, Simulator
from .types import DepthDelta, Instrument, Snapshot, Trade


@dataclass
class ReplayStats:
    events_seen: int = 0
    events_applied: int = 0
    events_skipped_unsynced: int = 0
    desyncs: int = 0

    def as_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass
class Replay:
    """Sequencing, book, and simulation wired together."""

    instrument: Instrument
    queue_model: QueueModel | None = None
    rule: SequenceRule | None = None
    feed_latency_ns: int = 0
    order_latency_ns: int = 0

    book: OrderBook = field(init=False)
    sync: Synchroniser = field(init=False)
    sim: Simulator = field(init=False)
    stats: ReplayStats = field(init=False, default_factory=ReplayStats)

    def __post_init__(self) -> None:
        self.book = OrderBook(self.instrument)
        self.sync = Synchroniser(self.book, self.rule)
        self.sim = Simulator(
            self.book,
            queue_model=self.queue_model,
            feed_latency_ns=self.feed_latency_ns,
            order_latency_ns=self.order_latency_ns,
        )

    def on_snapshot(self, snapshot: Snapshot) -> bool:
        return self.sync.on_snapshot(snapshot)

    def on_event(
        self, delta: DepthDelta, trades: tuple[Trade, ...] = ()
    ) -> list[Fill]:
        """Advance one market event."""
        self.stats.events_seen += 1

        if self.sync.state is not SyncState.SYNCED:
            # Not synced: hand the delta over to be buffered and do nothing
            # else. Simulating against a book we cannot vouch for is worse
            # than simulating nothing.
            self.sync.on_delta(delta)
            self.stats.events_skipped_unsynced += 1
            return []

        fills = self.sim.pre_update(delta.event_time_ns, trades)

        if self.sync.on_delta(delta):
            self.sim.post_update()
            self.stats.events_applied += 1
        else:
            # The synchroniser rejected it, so the book has been torn down.
            self.sim.on_desync()
            self.stats.desyncs += 1

        return fills

    @property
    def synced(self) -> bool:
        return self.sync.state is SyncState.SYNCED
