"""Cancellation-safe lifecycle coordination for an owned async client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from hmt_crypto.errors import ConfigurationError


class _ClientLifecycle:
    """Coordinate admitted calls with one shared, shielded close operation."""

    def __init__(self, close_callback: Callable[[], Awaitable[None]]) -> None:
        self._close_callback = close_callback
        self._condition = asyncio.Condition()
        self._state = "open"
        self._active_calls = 0
        self._close_task: asyncio.Task[None] | None = None

    async def admit(self) -> None:
        """Admit a call only while the owned client is open."""
        async with self._condition:
            if self._state != "open":
                raise ConfigurationError(
                    context={"operation": "public_client", "state": self._state}
                )
            self._active_calls += 1

    async def release(self) -> None:
        """Release one previously admitted call."""
        async with self._condition:
            self._active_calls -= 1
            if self._active_calls == 0:
                self._condition.notify_all()

    async def _finish_close(self) -> None:
        try:
            async with self._condition:
                await self._condition.wait_for(lambda: self._active_calls == 0)
            await self._close_callback()
        finally:
            async with self._condition:
                self._state = "closed"
                self._condition.notify_all()

    async def close(self) -> None:
        """Start close once and shield it from any individual closer's cancellation."""
        async with self._condition:
            if self._close_task is None:
                self._state = "closing"
                self._close_task = asyncio.create_task(self._finish_close())
            close_task = self._close_task
        await asyncio.shield(close_task)


__all__: list[str] = []
