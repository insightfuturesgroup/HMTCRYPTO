"""Event-loop-owned timer for one stream-control generation."""

from __future__ import annotations

import asyncio

from hmt_crypto.market_data.stream_control_owner import (
    StreamControlOwner,
    StreamControlStateError,
)
from hmt_crypto.market_data.stream_liveness import StreamLivenessAction

_NANOSECONDS_PER_SECOND = 1_000_000_000


async def run_stream_liveness(owner: StreamControlOwner) -> None:
    """Drive liveness without spawning an untrusted timer dependency.

    ``asyncio.timeout`` owns the timer in this task. Consequently cancellation
    cannot detach a dependency task, while the owner clock still proves that a
    loop-timer wake reached the requested monotonic duration.
    """
    if type(owner) is not StreamControlOwner:
        raise TypeError("stream liveness runner owner is invalid")
    while True:
        decision = await owner.tick()
        if decision.action is StreamLivenessAction.SEND_PING:
            return
        before_ns = await owner.current_monotonic_ns()
        delay_ns = decision.deadline_ns - before_ns
        if delay_ns <= 0:
            if decision.action is StreamLivenessAction.NONE:
                raise StreamControlStateError("invalid_timer_deadline")
            continue
        timed_out = False
        try:
            async with asyncio.timeout(delay_ns / _NANOSECONDS_PER_SECOND):
                await owner.wait_for_observation()
        except TimeoutError:
            timed_out = True
        if timed_out:
            after_ns = await owner.current_monotonic_ns()
            if after_ns < before_ns + delay_ns:
                raise StreamControlStateError("timer_deadline_not_reached")


__all__ = ["run_stream_liveness"]
