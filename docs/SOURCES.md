# Sources

What this project was derived from. Kept whether or not a license requires it.

- **Kind:** reimplementation
- **Primary sources studied:** hftbacktest (queue modelling), CppTrader (book
  structure), Binance diff-depth stream documentation
- **Started:** 2026-07-31

## What came from where

Nothing was copied. These are the things I read to understand the problem
before writing any code, and what I took from each as an idea.

**[nkaz001/hftbacktest](https://github.com/nkaz001/hftbacktest)** — the idea
that queue position deserves named, swappable models rather than one hardcoded
assumption. The models here and their implementation are my own; what I took was
the framing that the assumption should be a parameter you sweep, not a constant
buried in the fill logic.

**[chronoxor/CppTrader](https://github.com/chronoxor/CppTrader)** — read for how
a performance-oriented book organises price levels. It pushed me toward
separating the size map from the ordering structure, which led to the
dict-plus-sorted-array design in `src/obsim/levels.py`, and from there to
benchmarking that choice against the alternatives.

**Binance spot and USD-M futures depth stream documentation** — the source of
the two continuity rules. Spot expects continuity to be inferred from update ID
adjacency; futures names the previous message's final ID outright. That the same
venue does it two different ways is why `SequenceRule` is an interface rather
than a branch.

## What I looked for and did not find

I went looking for an existing order book reconstruction project to reimplement
and did not find one worth the effort. The repositories in this space are mostly
matching engines, which solve the opposite problem — they *create* the book
rather than infer it from a lossy feed — or full backtesting frameworks where
reconstruction is an incidental detail rather than the point. That gap is why
this exists.

## Data

No market data is redistributed here. The synthetic venue in
`src/obsim/feeds/synthetic.py` generates everything the tests and demos use, so
there are no data licensing obligations and the test suite runs with no network
access.

## License obligations

None. Original work — see `Tracker/docs/ATTRIBUTION.md`.
