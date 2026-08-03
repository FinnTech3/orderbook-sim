"""Where your order sits in the queue, and what happens when the level shrinks.

A level-2 feed reports that size at a price fell from 500 to 300. It does not
say why. Either 200 lots traded, or 200 lots were cancelled, and for an order
resting at that price the difference decides everything:

- A **trade** consumes the queue from the front. If you were within 200 lots of
  the head, part of you filled.
- A **cancellation** can come from anywhere in the queue. Someone behind you
  pulling their order does not move you forward at all; someone in front of you
  pulling theirs moves you forward by exactly their size.

Trades are published on a separate stream, so the traded portion is knowable
and the rest is cancellation. What stays unknowable is *where in the queue*
those cancellations sat, and that is what these models are for.

The split is not purely a modelling choice, though. It is bounded by what is
physically there. An order that just joined the back of a 500-lot level has 500
ahead of it and nothing behind; if 200 then cancel, all 200 came from in front,
whatever assumption you would have preferred. So each model states a
*preference* for where cancellations come from, and the simulator applies it
subject to the sizes that actually exist on each side of the order.

The honest way to report a backtest is as a range across all three models. The
gap between optimistic and pessimistic is the real uncertainty in any fill
assumption, and quoting a single number hides it.
"""

from __future__ import annotations

from typing import Protocol


class QueueModel(Protocol):
    """How cancellations at a level are assumed to be distributed."""

    name: str

    def cancellations_ahead(self, ahead: int, behind: int, cancelled: int) -> int:
        """How many of ``cancelled`` lots were in front of our order.

        ``ahead`` and ``behind`` are other participants' sizes on either side
        of us. The return value must lie within ``[max(0, cancelled - behind),
        min(cancelled, ahead)]`` — outside that range the arithmetic does not
        add up — and :func:`clamp` enforces exactly that.
        """


def clamp(ahead: int, behind: int, cancelled: int, preferred: int) -> int:
    """Force a preference into the range the sizes actually allow."""
    lowest = max(0, cancelled - behind)
    highest = min(cancelled, ahead)
    if highest < lowest:
        # Only reachable if cancelled exceeds the whole level, which means the
        # caller's accounting is off.
        raise ValueError(
            f"cannot cancel {cancelled} from a queue of {ahead} ahead "
            f"and {behind} behind"
        )
    return max(lowest, min(preferred, highest))


class PessimisticQueue:
    """Cancellations come from behind us wherever that is possible.

    We only move up when arithmetic forces it. The worst case, and the
    default: a simulator wrong in this direction understates its fills, which
    is the survivable way to be wrong.
    """

    name = "pessimistic"

    def cancellations_ahead(self, ahead: int, behind: int, cancelled: int) -> int:
        return clamp(ahead, behind, cancelled, 0)


class OptimisticQueue:
    """Cancellations come from in front of us wherever possible.

    The best case. Useful as the other end of the range, not as an answer.
    """

    name = "optimistic"

    def cancellations_ahead(self, ahead: int, behind: int, cancelled: int) -> int:
        return clamp(ahead, behind, cancelled, cancelled)


class ProportionalQueue:
    """Cancellations are spread uniformly across the queue.

    If a third of the level sits in front of us, a third of the cancellations
    are assumed to have come from in front. Closest to a queue where every
    participant is equally likely to pull, though real queues are not uniform:
    orders near the front are older and tend to be stickier, which makes this
    mildly optimistic in practice.
    """

    name = "proportional"

    def cancellations_ahead(self, ahead: int, behind: int, cancelled: int) -> int:
        total = ahead + behind
        preferred = 0 if total <= 0 else round(cancelled * ahead / total)
        return clamp(ahead, behind, cancelled, preferred)


#: Every model, for sweeping a backtest across the full range of assumptions.
ALL_MODELS: tuple[QueueModel, ...] = (
    PessimisticQueue(),
    ProportionalQueue(),
    OptimisticQueue(),
)


def by_name(name: str) -> QueueModel:
    for model in ALL_MODELS:
        if model.name == name:
            return model
    available = ", ".join(model.name for model in ALL_MODELS)
    raise KeyError(f"unknown queue model {name!r}; available: {available}")
