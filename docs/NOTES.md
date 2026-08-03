# Design notes

Written before implementation, and updated where things turned out differently.
The architecture as built is in [ARCHITECTURE.md](ARCHITECTURE.md); this is the
reasoning behind it.

## What it does

Takes the two things an exchange publishes — one snapshot of the book, and a
stream of incremental changes to it — and maintains a correct local copy. Then
replays that copy so a hypothetical order can be tested against it.

## The actual problem

Reconstruction sounds like bookkeeping and mostly is, but three things make it
harder than applying updates in a loop.

**You cannot trust the stream.** Messages get dropped and reordered. A local
book that has missed one update is wrong forever after, with nothing in the data
to reveal it. So the sequencing layer has to prove continuity rather than assume
it, and admit defeat when it cannot.

**The snapshot and the stream race each other.** By the time a snapshot request
comes back, updates have already arrived that it may or may not include. Some of
the held updates are older than the snapshot and must be dropped; exactly one
straddles it; the rest follow. Getting that boundary wrong is the classic way to
end up with a book that is subtly wrong from the very start.

**Depth changes are ambiguous.** A level shrinking says nothing about whether it
traded or was cancelled, and for a resting order that difference decides
everything. Trades arrive on a separate stream, which resolves half of it. Where
in the queue the cancellations sat is not recoverable at all.

## Key decisions

**Integer price grid.** Converting at the feed boundary and never touching a
float afterwards removes a whole category of bug rather than making it unlikely.

**Sequencing separated from the book.** The book applies what it is given; the
synchroniser decides what it is allowed to be given. Keeping those apart is what
made the gap and resync logic testable in isolation.

**A synthetic venue that knows the truth.** This turned out to be the highest
leverage decision in the project. Because the generator keeps its own copy of
the book, tests can compare reconstruction against ground truth rather than
against itself. Nearly every real bug found during development was caught this
way, or by the property tests built on top of it.

**Queue models as first-class objects.** The assumption about cancellation
placement is the single biggest lever on simulated fill rates, so it is a
parameter to sweep rather than a constant to bury.

## What changed during implementation

**Queue position needed two numbers, not one.** I started by tracking only the
size ahead of the order, and found the pessimistic model could produce
impossible states — more size claimed ahead than existed on the level at all.
The split between ahead and behind is constrained by what is physically there,
so both have to be tracked and the model's preference clamped into the feasible
range.

**The simulator stopped applying deltas.** It originally applied the update
itself, which collided with the synchroniser wanting to do the same. Splitting
it into a pre-update and a post-update phase let the replay driver own the
ordering, and made the desync behaviour explicit rather than accidental.

**The synthetic venue needed two corrections.** It drained to one level per side
over a long run, because trades remove levels as well as the remove action does.
And its trade sizes were drawn uniformly over the level, so the average trade
consumed half the queue — in that regime queue position stops mattering and
every model agrees, which quietly destroyed the entire demonstration. Both are
fixed, and both have regression tests.

## Open questions

- How far do the L2 queue models land from the truth when checked against L3
  data, where per-order detail makes the answer knowable?
- Does modelling market impact change the ranking of the queue models, or shift
  all three down together?
- Is proportional placement actually better than pessimistic on real data, or
  does the fact that older orders near the front are stickier make it
  systematically optimistic?
