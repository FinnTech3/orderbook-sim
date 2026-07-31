# orderbook-sim

<!--
ONE SENTENCE. What this does, in words a non-programmer would understand.
No jargon. If your housemate could not repeat it back, rewrite it.

Good:  "Reconstructs what an exchange's order book looked like at any moment
        in the past, so you can test a trading idea against what was really
        there rather than a daily average."
Weak:  "A high-performance framework leveraging event-driven architecture for
        market microstructure analysis."
-->

## What this is

<!--
Two or three short paragraphs, still plain English. Answer, in order:

1. What problem was in front of me?
2. Why did the existing options not solve it?
3. What does this do about it?

Write it the way you would explain it out loud to a friend who is smart but
does not work in this field. Concrete nouns. Short sentences. No adjectives
doing work that facts should do.
-->

## Why I built it

<!--
The honest reason. "I wanted to understand X." "I kept hitting Y." "I read
about Z and did not believe the claim until I tested it."

This paragraph is what makes the README read like a person wrote it, because
it contains a reason only you would have. Do not skip it.
-->

## Using it

```sh
# Install and run. Keep it to the shortest path that produces output.
```

<!-- Show what the output actually looks like. A code block, a table, a chart. -->

## How it works

<!--
Here is where the register changes. From this point on, assume the reader is
technical and go deep.

Cover:
- The shape of the system: components and how data moves between them.
- The data structures that matter, and why those ones.
- Anything performance-sensitive, with numbers.

A diagram helps. Mermaid renders natively on GitHub.
-->

## Decisions and trade-offs

<!--
The most valuable section in this file, and the one almost nobody writes.

For each significant decision:
- What I chose.
- What I chose it over.
- What it costs me.

Example of the register to aim for:

  I store the book as two sorted arrays rather than a heap. A heap gives
  cheaper inserts, but almost every operation here reads the top of the book
  and only occasionally inserts deep into it, so the arrays win on the access
  pattern that actually dominates. The cost is that a deep insert is O(n), and
  on the widest instrument I tested that showed up as a visible spike.

Note what it costs. An engineer reading a page of decisions with no downsides
concludes you have not looked hard enough.
-->

## What I would do differently

<!--
Known limitations, things you would change, things you ran out of time for.

This section signals judgement more reliably than anything else in the file.
Be specific: "the parser assumes UTC and would break on a venue that reports
local time" beats "could be more robust."
-->

## License

MIT. Original work — see [docs/SOURCES.md](docs/SOURCES.md).

<!--
=============================================================================
Delete every comment in this file before publishing.

Voice checklist:
  - First person. "I built", "I chose", "I got this wrong at first."
  - Past tense for what you did, present tense for what the code does.
  - Say the specific thing. Numbers, names, versions, measurements.
  - Cut these words: leverage, robust, seamless, powerful, comprehensive,
    cutting-edge, showcase, delve, realm, intricate, pivotal, spearheaded.
    They carry no information and are the exact vocabulary that gets flagged
    as machine-written.
  - Cut any sentence that would still be true if the project did something
    else entirely.
  - Read it aloud. Anywhere you would not say it that way, rewrite it.
=============================================================================
-->
