"""Tests for the cancellation-placement models."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from obsim.queue_model import (
    ALL_MODELS,
    OptimisticQueue,
    PessimisticQueue,
    ProportionalQueue,
    by_name,
    clamp,
)


def test_pessimistic_takes_from_behind_when_it_can():
    model = PessimisticQueue()
    assert model.cancellations_ahead(ahead=100, behind=50, cancelled=50) == 0


def test_pessimistic_is_forced_forward_when_nothing_is_behind():
    """The case that motivates tracking both sides of the queue.

    An order that just joined the back of a 500-lot level has everything ahead
    of it. If 200 cancel, all 200 came from in front — no assumption can put
    them anywhere else.
    """
    model = PessimisticQueue()
    assert model.cancellations_ahead(ahead=500, behind=0, cancelled=200) == 200


def test_pessimistic_takes_only_the_excess_from_ahead():
    model = PessimisticQueue()
    # 30 can hide behind; the other 20 have to come from in front.
    assert model.cancellations_ahead(ahead=100, behind=30, cancelled=50) == 20


def test_optimistic_takes_from_ahead_when_it_can():
    model = OptimisticQueue()
    assert model.cancellations_ahead(ahead=100, behind=50, cancelled=50) == 50


def test_optimistic_cannot_take_more_than_is_ahead():
    model = OptimisticQueue()
    assert model.cancellations_ahead(ahead=20, behind=100, cancelled=50) == 20


def test_proportional_splits_by_position():
    model = ProportionalQueue()
    # A quarter of the queue is ahead, so a quarter of the cancels are.
    assert model.cancellations_ahead(ahead=25, behind=75, cancelled=40) == 10


def test_proportional_handles_an_empty_queue():
    assert ProportionalQueue().cancellations_ahead(0, 0, 0) == 0


def test_models_are_ordered_pessimistic_to_optimistic():
    ahead, behind, cancelled = 100, 100, 40
    results = [
        model.cancellations_ahead(ahead, behind, cancelled) for model in ALL_MODELS
    ]
    assert results == sorted(results), "ALL_MODELS should run worst to best"
    assert results[0] < results[-1], "the range should not be degenerate"


def test_clamp_rejects_impossible_accounting():
    with pytest.raises(ValueError, match="cannot cancel"):
        clamp(ahead=10, behind=10, cancelled=50, preferred=0)


def test_by_name_round_trips():
    for model in ALL_MODELS:
        assert by_name(model.name) is not None
        assert by_name(model.name).name == model.name


def test_by_name_rejects_unknown():
    with pytest.raises(KeyError, match="unknown queue model"):
        by_name("wishful")


@given(
    ahead=st.integers(min_value=0, max_value=10_000),
    behind=st.integers(min_value=0, max_value=10_000),
    fraction=st.floats(min_value=0.0, max_value=1.0),
)
def test_property_every_model_stays_within_the_possible_range(
    ahead, behind, fraction
):
    """No model may return a split the sizes cannot support."""
    cancelled = int((ahead + behind) * fraction)
    lowest = max(0, cancelled - behind)
    highest = min(cancelled, ahead)

    for model in ALL_MODELS:
        result = model.cancellations_ahead(ahead, behind, cancelled)
        assert lowest <= result <= highest
        # Whatever does not come from ahead must fit behind.
        assert 0 <= cancelled - result <= behind
