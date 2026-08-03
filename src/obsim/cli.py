"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
import time
from decimal import Decimal

from .feeds import SyntheticVenue
from .metrics import imbalance, slippage_ticks, spread_bps
from .queue_model import ALL_MODELS
from .replay import Replay
from .types import Instrument, Side

INSTRUMENT = Instrument("SYNTH", Decimal("0.01"), Decimal("1"))


def _rule(width: int = 62) -> str:
    return "-" * width


def _run(model, seed, events, order_latency_ns, quote_size, hold, offset):
    """Quote a bid, hold it for a while, pull it if it has not filled.

    The holding period is the point. A strategy that quotes once and waits
    forever fills almost everything eventually, so its fill rate measures
    patience rather than queue position and every model agrees. Pulling an
    unfilled quote after a fixed window is both what a real maker does and
    the regime where where-you-sit in the queue decides the outcome.
    """
    venue = SyntheticVenue(seed=seed)
    replay = Replay(
        INSTRUMENT, queue_model=model, order_latency_ns=order_latency_ns
    )
    replay.on_snapshot(venue.snapshot())

    working = None
    submitted_at = 0

    for index in range(events):
        step = venue.step()
        now = step.delta.event_time_ns

        if working is not None and not working.is_live:
            working = None

        if working is None:
            if replay.book.best_bid is not None:
                working = replay.sim.submit(
                    Side.BID, replay.book.best_bid - offset, quote_size, now
                )
                submitted_at = index
        elif index - submitted_at >= hold:
            replay.sim.cancel(working.id, now)

        replay.on_event(step.delta, step.trades)

    return replay


def cmd_demo(args: argparse.Namespace) -> int:
    venue = SyntheticVenue(seed=args.seed)
    replay = Replay(INSTRUMENT)
    replay.on_snapshot(venue.snapshot())

    for _ in range(args.events):
        step = venue.step()
        replay.on_event(step.delta, step.trades)

    book = replay.book
    print(f"Reconstructed {args.events} events from seed {args.seed}\n")

    # Every one of these is None when a side of the book is empty, which is a
    # legitimate state for a thin market. Formatting None with a precision
    # spec raises, so it goes through here rather than into an f-string.
    def line(label: str, value, spec: str = "", suffix: str = "") -> None:
        if value is None:
            print(f"  {label:<14}-")
        else:
            print(f"  {label:<14}{value:{spec}}{suffix}")

    line("best bid", book.best_bid)
    line("best ask", book.best_ask)
    line("spread", book.spread, suffix=" ticks")
    line("spread (bps)", spread_bps(book), ".2f")
    line("mid", book.mid)
    line("microprice", book.microprice, ".3f")
    line("imbalance(5)", imbalance(book, depth=5), "+.3f")
    line("slip to buy", slippage_ticks(book, Side.ASK, 100), ".3f", " ticks")

    print(f"\n  bid levels    {len(book.bids)}")
    print(f"  ask levels    {len(book.asks)}")

    truth_ok = (
        {lvl.price: lvl.size for lvl in book.bids.top(10_000)} == venue.true_bids()
        and {lvl.price: lvl.size for lvl in book.asks.top(10_000)}
        == venue.true_asks()
    )
    print(f"\n  matches the venue's own book: {'yes' if truth_ok else 'NO'}")
    return 0 if truth_ok else 1


def cmd_sweep(args: argparse.Namespace) -> int:
    """Run the same stream under every queue assumption."""
    print(
        f"Same {args.events} events, same strategy, seed {args.seed}.\n"
        f"Only the assumption about where cancellations sit changes.\n"
    )
    header = (
        f"{'queue model':<15}{'quotes':>8}{'filled':>9}{'pulled':>8}"
        f"{'fill rate':>11}"
    )
    print(header)
    print(_rule(len(header)))

    rates = []
    for model in ALL_MODELS:
        replay = _run(
            model,
            args.seed,
            args.events,
            args.order_latency,
            args.size,
            args.hold,
            args.offset,
        )
        stats = replay.sim.stats
        rates.append(stats.fill_ratio)
        print(
            f"{model.name:<15}{stats.orders_submitted:>8}"
            f"{stats.lots_filled:>9}{stats.orders_cancelled:>8}"
            f"{stats.fill_ratio:>10.1%}"
        )

    print(_rule(len(header)))
    low, high = min(rates), max(rates)
    spread = high - low
    print(
        f"\nSame data, same strategy. Fill rate lands between {low:.1%} and "
        f"{high:.1%},\na spread of {spread:.1%} that comes entirely from an "
        f"assumption the feed\ncannot settle. Quoting a single number here "
        f"would be picking one and\nhoping."
    )
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    venue = SyntheticVenue(seed=args.seed)
    steps = venue.stream(args.events)

    replay = Replay(INSTRUMENT)
    replay.on_snapshot(SyntheticVenue(seed=args.seed).snapshot())

    start = time.perf_counter()
    for step in steps:
        replay.on_event(step.delta, step.trades)
    elapsed = time.perf_counter() - start

    rate = args.events / elapsed if elapsed else float("inf")
    print(f"Reconstruction throughput, seed {args.seed}\n")
    print(f"  events        {args.events:,}")
    print(f"  elapsed       {elapsed:.3f} s")
    print(f"  rate          {rate:,.0f} events/sec")
    print(f"  per event     {elapsed / args.events * 1e6:.2f} us")
    print(f"\n  applied       {replay.stats.events_applied:,}")
    print(f"  book levels   {len(replay.book.bids)} bid / {len(replay.book.asks)} ask")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="obsim",
        description="Reconstruct an order book and simulate orders against it.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="reconstruct a stream and describe the book")
    demo.add_argument("--events", type=int, default=10_000)
    demo.add_argument("--seed", type=int, default=1)
    demo.set_defaults(func=cmd_demo)

    sweep = sub.add_parser(
        "sweep", help="run one strategy under every queue assumption"
    )
    sweep.add_argument("--events", type=int, default=20_000)
    sweep.add_argument("--seed", type=int, default=1)
    sweep.add_argument("--size", type=int, default=10, help="quote size in lots")
    sweep.add_argument(
        "--hold",
        type=int,
        default=40,
        help="events to hold an unfilled quote before pulling it",
    )
    sweep.add_argument(
        "--offset", type=int, default=0, help="ticks behind the touch to quote"
    )
    sweep.add_argument(
        "--order-latency", type=int, default=0, help="nanoseconds"
    )
    sweep.set_defaults(func=cmd_sweep)

    bench = sub.add_parser("bench", help="measure reconstruction throughput")
    bench.add_argument("--events", type=int, default=200_000)
    bench.add_argument("--seed", type=int, default=1)
    bench.set_defaults(func=cmd_bench)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
