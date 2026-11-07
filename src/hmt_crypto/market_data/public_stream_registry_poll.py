"""Persistent absolute registry-poll scheduling for one stream generation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from hmt_crypto.market_data.public_stream_support import abandon_task, observe_task


class RegistryPollError(Exception):
    """The local registry-poll clock or timer violated its contract."""


class RegistryPollOutcome(StrEnum):
    """The first generation-owned event at one registry poll boundary."""

    FRAME = "frame"
    DUE = "due"
    TERMINAL = "terminal"


class RegistryPollSchedule:
    """Own one absolute deadline and timer that inbound frames cannot reset."""

    __slots__ = ("_deadline", "_interval", "_last_now", "_now", "_sleep", "_timer")

    def __init__(
        self,
        *,
        interval_ns: int,
        monotonic_ns: Callable[[], int],
        sleep_ns: Callable[[int], Awaitable[None]],
    ) -> None:
        if (
            type(interval_ns) is not int
            or interval_ns <= 0
            or not callable(monotonic_ns)
            or not callable(sleep_ns)
        ):
            raise TypeError("registry poll schedule is invalid")
        self._interval = interval_ns
        self._now = monotonic_ns
        self._sleep = sleep_ns
        self._deadline: int | None = None
        self._last_now: int | None = None
        self._timer: asyncio.Future[None] | None = None

    def _sample(self) -> int:
        try:
            now_ns = self._now()
        except BaseException:
            raise RegistryPollError from None
        if (
            type(now_ns) is not int
            or now_ns < 0
            or (self._last_now is not None and now_ns < self._last_now)
        ):
            raise RegistryPollError from None
        self._last_now = now_ns
        return now_ns

    def _arm(self) -> asyncio.Future[None] | None:
        """Return the persistent timer, or ``None`` when already due."""
        now_ns = self._sample()
        if self._deadline is None:
            self._deadline = now_ns + self._interval
        remaining = self._deadline - now_ns
        if remaining <= 0:
            return None
        if self._timer is not None:
            return self._timer
        awaitable: Awaitable[None] | None = None
        try:
            awaitable = self._sleep(remaining)
            self._timer = asyncio.ensure_future(awaitable)
        except BaseException:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise RegistryPollError from None
        return self._timer

    def _is_due(self) -> bool:
        """Return whether the fixed deadline has elapsed on a valid clock."""
        now_ns = self._sample()
        return self._deadline is not None and now_ns >= self._deadline

    def take_timer(self) -> asyncio.Future[Any] | None:
        """Detach and return the currently owned timer for observation."""
        timer = self._timer
        self._timer = None
        return timer

    async def wait(
        self,
        *,
        receive: asyncio.Future[Any],
        terminal: asyncio.Future[Any] | None,
    ) -> RegistryPollOutcome:
        """Wait without replacing the absolute timer when a frame arrives."""
        timer = self._arm()
        if timer is None:
            owned_timer = self.take_timer()
            if owned_timer is not None and owned_timer.done():
                try:
                    owned_timer.result()
                except BaseException:
                    raise RegistryPollError from None
                observe_task(owned_timer)
            else:
                abandon_task(owned_timer)
            return RegistryPollOutcome.DUE
        waiters = {receive, timer}
        if terminal is not None:
            waiters.add(terminal)
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if terminal is not None and terminal in done:
            return RegistryPollOutcome.TERMINAL
        due = self._is_due()
        if timer in done or due:
            owned_timer = self.take_timer()
            if timer in done:
                try:
                    timer.result()
                except BaseException:
                    raise RegistryPollError from None
                if not due:
                    raise RegistryPollError from None
                observe_task(owned_timer)
            else:
                abandon_task(owned_timer)
            return RegistryPollOutcome.DUE
        if receive in done:
            return RegistryPollOutcome.FRAME
        raise RegistryPollError from None

    def advance(self) -> None:
        """Advance by whole intervals to the first deadline after ``now_ns``."""
        now_ns = self._sample()
        deadline = self._deadline
        if deadline is None or now_ns < deadline or self._timer is not None:
            raise RegistryPollError from None
        intervals = ((now_ns - deadline) // self._interval) + 1
        self._deadline = deadline + intervals * self._interval


__all__: list[str] = []
