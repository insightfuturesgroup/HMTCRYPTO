"""Cancellation-safe lifecycle coordination for an owned async client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from hmt_crypto.errors import ConfigurationError, ExchangeDownError


class _ClientLifecycle:
    """Coordinate admitted calls with one shared, shielded close operation."""

    def __init__(self, close_callback: Callable[[], Awaitable[None]]) -> None:
        self._close_callback = close_callback
        self._state = "open"
        self._active_calls = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._close_task: asyncio.Task[None] | None = None

    def admit(self) -> None:
        """Admit a call only while the owned client is open."""
        if self._state != "open":
            raise ConfigurationError(context={"operation": "public_client", "state": self._state})
        if self._active_calls == 0:
            self._idle.clear()
        self._active_calls += 1

    def release(self) -> None:
        """Release one lease atomically without introducing a cancellation point."""
        self._active_calls -= 1
        if self._active_calls == 0:
            self._idle.set()

    async def _finish_close(self) -> None:
        try:
            await self._idle.wait()
            await self._close_callback()
        except asyncio.CancelledError:
            self._state = "close_failed"
            raise
        except Exception:
            self._state = "close_failed"
            raise ExchangeDownError(context={"operation": "public_client_close"}) from None
        else:
            self._state = "closed"

    async def close(self) -> None:
        """Start close once and shield it from any individual closer's cancellation."""
        if self._state == "closed":
            return
        if self._close_task is None or self._close_task.done():
            self._state = "closing"
            self._close_task = asyncio.create_task(self._finish_close())
        close_task = self._close_task
        await asyncio.shield(close_task)


__all__: list[str] = []
