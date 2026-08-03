"""Smoke tests for the command line entry point.

Small event counts — these check the commands run end to end and report
success, not that the numbers are right. Correctness lives in the other files.
"""

from __future__ import annotations

import pytest

from obsim.cli import main


def test_demo_runs_and_reports_a_matching_book(capsys):
    assert main(["demo", "--events", "500", "--seed", "3"]) == 0
    out = capsys.readouterr().out
    assert "matches the venue's own book: yes" in out
    assert "best bid" in out


def test_sweep_reports_every_model(capsys):
    assert main(["sweep", "--events", "2000", "--hold", "20"]) == 0
    out = capsys.readouterr().out
    for name in ("pessimistic", "proportional", "optimistic"):
        assert name in out
    assert "fill rate" in out


def test_sweep_honours_the_quote_offset(capsys):
    assert main(["sweep", "--events", "1000", "--offset", "2"]) == 0


def test_sweep_honours_order_latency(capsys):
    assert main(["sweep", "--events", "1000", "--order-latency", "5000"]) == 0


def test_bench_reports_a_rate(capsys):
    assert main(["bench", "--events", "2000"]) == 0
    out = capsys.readouterr().out
    assert "events/sec" in out


def test_queue_models_still_disagree():
    """The core demonstration, guarded.

    Every other test checks a component in isolation, and all of them would
    still pass if a refactor made the three queue models produce identical
    fills — at which point the project's entire claim would be silently dead.
    This asserts the range is real: optimistic must fill more than pessimistic
    on the same stream.
    """
    from obsim.cli import _run
    from obsim.queue_model import OptimisticQueue, PessimisticQueue

    common = dict(
        seed=1, events=12_000, order_latency_ns=0,
        quote_size=10, hold=40, offset=0,
    )
    low = _run(PessimisticQueue(), **common).sim.stats.fill_ratio
    high = _run(OptimisticQueue(), **common).sim.stats.fill_ratio

    assert high > low, (
        f"queue models agree (both {low:.3%}) — the assumption is no longer "
        f"changing the outcome, so the demonstration is broken"
    )


def test_no_subcommand_is_an_error():
    with pytest.raises(SystemExit):
        main([])


def test_unknown_subcommand_is_an_error():
    with pytest.raises(SystemExit):
        main(["nonsense"])


def test_demo_output_has_no_blank_metric_lines(capsys):
    """Regression: optional metrics used to print an empty line when absent.

    They also raised TypeError when formatted with a precision spec, which any
    book with an empty side would have triggered.
    """
    main(["demo", "--events", "300", "--seed", "5"])
    lines = capsys.readouterr().out.splitlines()
    metric_block = [line for line in lines if line.startswith("  ")]
    assert metric_block, "expected indented metric lines"
    assert all(line.strip() for line in metric_block)
