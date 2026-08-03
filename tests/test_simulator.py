"""Tests for order simulation: queue position, latency, and fills."""

from __future__ import annotations

from decimal import Decimal

import pytest

from obsim.book import OrderBook
from obsim.queue_model import OptimisticQueue, PessimisticQueue, ProportionalQueue
from obsim.simulator import OrderState, Simulator
from obsim.types import DepthDelta, Instrument, PriceSize, Side, Snapshot, Trade

INSTRUMENT = Instrument("TEST", Decimal("1"), Decimal("1"))


def make_sim(bids=((100, 500),), asks=((101, 500),), **kwargs) -> Simulator:
    book = OrderBook(INSTRUMENT)
    book.load_snapshot(
        Snapshot(
            last_id=0,
            event_time_ns=0,
            bids=tuple(PriceSize(p, s) for p, s in bids),
            asks=tuple(PriceSize(p, s) for p, s in asks),
        )
    )
    return Simulator(book, **kwargs)


def evt(when, bids=(), asks=()):
    return DepthDelta(
        first_id=when,
        final_id=when,
        event_time_ns=when,
        bids=tuple(PriceSize(p, s) for p, s in bids),
        asks=tuple(PriceSize(p, s) for p, s in asks),
    )


def sell_into(price, size, when):
    """A trade that consumes resting bids."""
    return (Trade(price=price, size=size, aggressor=Side.ASK, event_time_ns=when),)


# ---- joining the queue --------------------------------------------------


def test_order_joins_the_back_of_the_queue():
    sim = make_sim()
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    assert order.state is OrderState.PENDING

    sim.on_market_event(evt(1, bids=[(100, 500)]))
    assert order.state is OrderState.RESTING
    assert order.queue_ahead == 500
    assert order.queue_behind == 0


def test_order_at_an_empty_price_is_at_the_front():
    sim = make_sim()
    order = sim.submit(Side.BID, 99, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))
    assert order.queue_ahead == 0


def test_new_size_at_our_price_queues_behind_us():
    sim = make_sim()
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))
    sim.on_market_event(evt(2, bids=[(100, 700)]))
    assert order.queue_ahead == 500
    assert order.queue_behind == 200


def test_zero_size_rejected():
    sim = make_sim()
    with pytest.raises(ValueError, match="size must be positive"):
        sim.submit(Side.BID, 100, 0, now_ns=0)


# ---- trades -------------------------------------------------------------


def test_trade_eats_the_queue_ahead_without_filling_us():
    sim = make_sim()
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))

    sim.on_market_event(evt(2, bids=[(100, 300)]), sell_into(100, 200, 2))
    assert order.queue_ahead == 300
    assert order.filled == 0


def test_trade_fills_us_once_the_queue_ahead_is_gone():
    sim = make_sim()
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))
    sim.on_market_event(evt(2, bids=[(100, 300)]), sell_into(100, 200, 2))

    # 300 ahead, then a 310-lot sell: 300 clears the queue, 10 reaches us.
    fills = sim.on_market_event(evt(3, bids=[(100, 0)]), sell_into(100, 310, 3))
    assert order.state is OrderState.FILLED
    assert order.filled == 10
    assert len(fills) == 1
    assert fills[0].price == 100
    assert fills[0].aggressive is False
    assert sim.stats.fill_ratio == 1.0


def test_partial_fill_leaves_the_rest_working():
    sim = make_sim()
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))
    sim.on_market_event(evt(2, bids=[(100, 0)]), sell_into(100, 504, 2))

    assert order.filled == 4
    assert order.remaining == 6
    assert order.state is OrderState.RESTING
    assert sim.stats.partial_fill_events == 1
    assert sim.stats.fill_ratio == pytest.approx(0.4)


def test_trade_on_the_other_side_is_ignored():
    sim = make_sim()
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))

    buy = (Trade(price=101, size=900, aggressor=Side.BID, event_time_ns=2),)
    sim.on_market_event(evt(2, asks=[(101, 100)]), buy)
    assert order.filled == 0
    assert order.queue_ahead == 500


def test_trade_through_our_price_fills_us():
    """A print below our bid means we would have been hit first."""
    sim = make_sim(bids=((100, 500), (99, 400)))
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))

    sim.on_market_event(evt(2, bids=[(99, 300)]), sell_into(99, 100, 2))
    assert order.filled == 10
    assert order.state is OrderState.FILLED
    assert sim.stats.trade_through_fills == 1


# ---- cancellations and the queue model ----------------------------------


def test_cancellations_move_us_up_when_nothing_is_behind():
    """Pessimism cannot save us when the whole level is in front."""
    sim = make_sim(queue_model=PessimisticQueue())
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))

    sim.on_market_event(evt(2, bids=[(100, 300)]))
    assert order.queue_ahead == 300
    assert order.queue_behind == 0


def test_pessimism_holds_us_back_when_there_is_size_behind():
    sim = make_sim(queue_model=PessimisticQueue())
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))
    sim.on_market_event(evt(2, bids=[(100, 1000)]))  # 500 joins behind us

    sim.on_market_event(evt(3, bids=[(100, 500)]))  # 500 cancel
    assert order.queue_ahead == 500
    assert order.queue_behind == 0


def test_optimism_moves_us_to_the_front_on_the_same_data():
    sim = make_sim(queue_model=OptimisticQueue())
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))
    sim.on_market_event(evt(2, bids=[(100, 1000)]))

    sim.on_market_event(evt(3, bids=[(100, 500)]))
    assert order.queue_ahead == 0
    assert order.queue_behind == 500


def test_the_queue_model_decides_whether_we_fill():
    """Identical market data, opposite outcomes. This is the whole point.

    The feed cannot say whether the 500 lots that vanished were in front of us
    or behind us, so the assumption — not the data — determines the fill.
    """
    stream = [
        (evt(1, bids=[(100, 500)]), ()),
        (evt(2, bids=[(100, 1000)]), ()),
        (evt(3, bids=[(100, 500)]), ()),
        (evt(4, bids=[(100, 490)]), sell_into(100, 10, 4)),
    ]

    outcomes = {}
    for model in (PessimisticQueue(), OptimisticQueue()):
        sim = make_sim(queue_model=model)
        order = sim.submit(Side.BID, 100, 10, now_ns=0)
        for delta, trades in stream:
            sim.on_market_event(delta, trades)
        outcomes[model.name] = order.filled

    assert outcomes["pessimistic"] == 0
    assert outcomes["optimistic"] == 10


def test_proportional_lands_between_the_two_extremes():
    results = {}
    for model in (PessimisticQueue(), ProportionalQueue(), OptimisticQueue()):
        sim = make_sim(queue_model=model)
        order = sim.submit(Side.BID, 100, 10, now_ns=0)
        sim.on_market_event(evt(1, bids=[(100, 500)]))
        sim.on_market_event(evt(2, bids=[(100, 1000)]))
        sim.on_market_event(evt(3, bids=[(100, 600)]))
        results[model.name] = order.queue_ahead

    assert results["optimistic"] <= results["proportional"] <= results["pessimistic"]
    assert results["optimistic"] < results["pessimistic"]


# ---- latency ------------------------------------------------------------


def test_order_latency_delays_going_live():
    sim = make_sim(order_latency_ns=1_000)
    order = sim.submit(Side.BID, 100, 10, now_ns=0)

    sim.on_market_event(evt(500, bids=[(100, 500)]))
    assert order.state is OrderState.PENDING

    sim.on_market_event(evt(1_000, bids=[(100, 500)]))
    assert order.state is OrderState.RESTING


def test_a_pending_order_misses_a_trade_it_would_have_caught():
    """Latency is not cosmetic — it changes which fills happen."""
    quick = make_sim(order_latency_ns=0)
    slow = make_sim(order_latency_ns=5_000)

    for sim in (quick, slow):
        sim.submit(Side.BID, 100, 10, now_ns=0)
        sim.on_market_event(evt(1, bids=[(100, 0)]), sell_into(100, 600, 1))

    assert quick.stats.lots_filled == 10
    assert slow.stats.lots_filled == 0


def test_feed_latency_shifts_observed_time():
    sim = make_sim(feed_latency_ns=250)
    assert sim.observed_time(1_000) == 1_250


def test_negative_latency_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        make_sim(order_latency_ns=-1)


# ---- marketable orders --------------------------------------------------


def test_order_arriving_across_the_spread_fills_immediately():
    sim = make_sim()
    order = sim.submit(Side.BID, 101, 10, now_ns=0)
    fills = sim.on_market_event(evt(1, bids=[(100, 500)]))

    assert order.state is OrderState.FILLED
    assert fills[0].aggressive is True
    assert fills[0].price == 101
    assert sim.stats.aggressive_fills == 1


def test_marketable_order_larger_than_the_touch_rests_the_remainder():
    sim = make_sim(asks=((101, 4),))
    order = sim.submit(Side.BID, 101, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 500)]))

    assert order.filled == 4
    assert order.state is OrderState.RESTING
    assert order.remaining == 6


# ---- cancels ------------------------------------------------------------


def test_cancel_takes_effect_after_the_latency():
    sim = make_sim(order_latency_ns=1_000)
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1_000, bids=[(100, 500)]))
    assert order.state is OrderState.RESTING

    sim.cancel(order.id, now_ns=2_000)
    assert order.state is OrderState.CANCEL_PENDING

    sim.on_market_event(evt(2_500, bids=[(100, 500)]))
    assert order.state is OrderState.CANCEL_PENDING

    sim.on_market_event(evt(3_000, bids=[(100, 500)]))
    assert order.state is OrderState.CANCELLED
    assert sim.stats.orders_cancelled == 1


def test_an_order_can_fill_inside_the_cancel_window():
    """The gap between asking to cancel and the venue acting is real."""
    sim = make_sim(order_latency_ns=1_000)
    order = sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1_000, bids=[(100, 500)]))

    sim.cancel(order.id, now_ns=1_100)
    sim.on_market_event(evt(1_500, bids=[(100, 0)]), sell_into(100, 600, 1_500))

    assert order.filled == 10
    assert order.state is OrderState.FILLED


def test_cancelling_an_unknown_order_is_harmless():
    sim = make_sim()
    sim.cancel(999, now_ns=0)


def test_live_orders_excludes_finished_ones():
    sim = make_sim()
    filled = sim.submit(Side.BID, 100, 10, now_ns=0)
    resting = sim.submit(Side.BID, 99, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 0)]), sell_into(100, 600, 1))

    assert filled.state is OrderState.FILLED
    assert sim.live_orders == [resting]


def test_stats_round_trip_to_a_dict():
    sim = make_sim()
    sim.submit(Side.BID, 100, 10, now_ns=0)
    sim.on_market_event(evt(1, bids=[(100, 0)]), sell_into(100, 600, 1))
    stats = sim.stats.as_dict()
    assert stats["lots_filled"] == 10
    assert stats["fill_ratio"] == 1.0
