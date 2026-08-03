# Architecture

## The problem

An exchange publishes its order book as a snapshot plus a stream of incremental
updates. To know what the book looked like at some past moment you have to
replay that stream exactly — which means handling the parts that are easy to get
wrong: sequence gaps, updates that arrive before the snapshot they apply to, and
the fact that a "size went from 5 to 3" message does not tell you whether those
two units were traded or cancelled.

That last ambiguity is the whole reason realistic simulation is hard. If you
had an order resting at that price, whether you got filled depends on whether
the two units that left were in front of you or behind you, and the feed does
not say.

## Layers

```
feeds/          parse venue messages into venue-neutral events
   │
sequencing/     order the events, detect gaps, drive resynchronisation
   │
book/           apply ordered events to price levels
   │
replay/         drive the book through time, expose state at any timestamp
   │
simulator/      resting orders, queue position, latency, fills
   │
metrics/        spread, imbalance, depth, microprice
```

Each layer depends only on the one above it. The book knows nothing about
Binance; the simulator knows nothing about websockets.

## Decisions

### Prices are integers, never floats

Prices arrive as decimal strings. They are converted once, at the feed boundary,
into integer counts of the instrument's tick size. Sizes likewise become integer
counts of lot size.

Price levels are dictionary keys and are compared for equality constantly. With
floats, `0.1 + 0.2 != 0.3` eventually produces two "identical" levels that do
not compare equal, and the book silently grows a phantom level. Integers make
that class of bug impossible rather than unlikely.

Cost: every feed needs an instrument definition before it can parse, and a venue
that changes tick size mid-session needs handling.

### Levels are a dict plus a sorted price array

The book needs three things: O(1) size lookup at a price, fast best-bid/best-ask,
and ordered iteration for depth queries.

Chosen: `dict[int, int]` mapping price ticks to size, alongside a `list[int]` of
occupied prices kept sorted with `bisect`. Both sides store ascending; the bid
side reads its best from the end of the array and the ask side from the front.

The obvious objection is that `bisect.insort` is O(n) — it memmoves the tail of
the list. It is chosen anyway because real depth updates cluster near the top of
the book, so the moved tail is short, and the memmove is a single vectorised
block copy rather than n interpreted operations. A tree gives a better
complexity class and loses on constants at these sizes.

`benchmarks/bench_levels.py` measures this against a sort-on-demand
implementation and a heap with lazy deletion. The numbers are in the README.

### Sequencing is a state machine, and venue rules are pluggable

Synchronisation state is explicit: `DISCONNECTED → BUFFERING → SYNCED`, with any
detected gap sending it back to `BUFFERING`.

While buffering, deltas are queued rather than applied. On snapshot arrival,
queued deltas that are wholly older than the snapshot are discarded, the delta
straddling the snapshot is identified, and the rest are applied in order.

Venues disagree on how continuity is expressed. Binance spot says each event's
first ID must follow the previous event's final ID; Binance futures carries an
explicit previous-ID field. That difference lives in a `SequenceRule`
implementation, not in the state machine.

### Queue position is modelled explicitly, with the assumption named

When size at a price level falls, the cause is either a trade or a cancellation,
and for a resting order the difference decides whether it fills.

- A **trade** consumes from the front of the queue. Unambiguous.
- A **cancellation** can come from anywhere in the queue. Not observable.

Three models, each stating its assumption:

| Model | Assumes cancellations come from | Effect |
| --- | --- | --- |
| `PessimisticQueue` | behind you | queue ahead unchanged; latest fills |
| `OptimisticQueue` | in front of you | queue ahead shrinks; earliest fills |
| `ProportionalQueue` | uniformly across the queue | shrinks in proportion to position |

The default is pessimistic, because a backtest that flatters itself is worse
than useless. Results should be quoted as a range across models — the spread
between optimistic and pessimistic is the honest uncertainty in any fill
assumption, and reporting a single number hides it.

### Latency is two separate delays

Feed latency (venue event → your process sees it) and order latency (your
decision → venue acts on it) are modelled independently, because they are
physically different paths and are often asymmetric.

A strategy therefore acts on a book that is already stale, and its orders arrive
into a book that has moved again. Ignoring this is the single most common reason
a backtest overstates its results.

### Replay is deterministic

Identical input produces identical output, with no wall-clock reads and no
unseeded randomness in the core. The synthetic feed takes a seed.

This is what makes the property tests possible: replaying a stream twice must
give byte-identical book states, and replaying to time T then continuing must
match replaying straight through to T+1.

## Testing

- **Unit** — each component against hand-built cases with known answers.
- **Property (Hypothesis)** — invariants that must hold over arbitrary streams:
  the book never crosses, sizes are never negative, the sorted array and the
  dictionary always agree, replay is deterministic.
- **Differential** — the fast level structure is checked against a deliberately
  naive reference implementation over random operations. They must agree
  exactly.

The differential test is the one that matters. It is easy to write a fast data
structure that is subtly wrong; comparing it against an obviously-correct slow
one catches what unit tests miss.
