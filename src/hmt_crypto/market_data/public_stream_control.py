"""Bounded control and single-receiver mechanics for one public generation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, TypeVar

from hmt_crypto.errors import ExchangeDownError
from hmt_crypto.exchange.delta.websocket_codec import decode_ws_object
from hmt_crypto.exchange.delta.websocket_contracts import WsPlane
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    DesiredSubscriptions,
    GenerationControlInvalidation,
    StreamInvalidationKind,
)
from hmt_crypto.exchange.delta.websocket_transport import (
    InboundKind,
    StreamControlPort,
    WsReceivePort,
)
from hmt_crypto.market_data.public_stream_models import (
    MonotonicNs,
    PublicConnectionGeneration,
    SleepNs,
)
from hmt_crypto.market_data.public_stream_support import (
    ControlTimeout,
    ReceiverEnded,
    abandon_task,
    observe_task,
)

_T = TypeVar("_T")


class PublicControlSession:
    """Own one persistent receive task and all bounded serialized control calls."""

    def __init__(
        self,
        *,
        generation: PublicConnectionGeneration,
        receiver: WsReceivePort,
        control: StreamControlPort,
        monotonic_ns: MonotonicNs,
        sleep_ns: SleepNs,
    ) -> None:
        self.generation = generation
        self.receiver = receiver
        self.control = control
        self.monotonic_ns = monotonic_ns
        self.sleep_ns = sleep_ns
        self.receive_task: asyncio.Task[str | bytes] | None = None
        self.control_revision = 0
        self.invalidation_sequence = 0

    def now(self) -> int:
        """Read one strict nonnegative injected monotonic instant."""
        value = self.monotonic_ns()
        if type(value) is not int or value < 0:
            raise ControlTimeout
        return value

    def deadline(self, *, after_ns: int | None = None) -> int:
        """Create a local absolute deadline without accepting remote progress."""
        delay = self.generation.control_timeout_ns if after_ns is None else after_ns
        if type(delay) is not int or delay <= 0:
            raise ControlTimeout
        return self.now() + delay

    async def bounded(self, awaitable: Awaitable[_T], *, deadline: int) -> _T:
        """Bound one operation with an independent injected monotonic timer."""
        remaining = deadline - self.now()
        if remaining <= 0:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise ControlTimeout
        operation = asyncio.ensure_future(awaitable)
        timer = asyncio.ensure_future(self.sleep_ns(remaining))
        try:
            done, _ = await asyncio.wait({operation, timer}, return_when=asyncio.FIRST_COMPLETED)
            if operation in done:
                abandon_task(timer)
                return operation.result()
            abandon_task(operation)
            observe_task(timer)
            raise ControlTimeout
        except asyncio.CancelledError:
            abandon_task(operation)
            abandon_task(timer)
            raise

    def ensure_receive(self) -> asyncio.Task[str | bytes]:
        """Return the sole persistent recv owner for this generation."""
        if self.receive_task is None:
            self.receive_task = asyncio.create_task(self.receiver.recv())
        return self.receive_task

    async def receive_before(self, deadline: int) -> str | bytes:
        """Receive before one fixed deadline without replacing a pending owner."""
        task = self.ensure_receive()
        remaining = deadline - self.now()
        if remaining <= 0:
            raise ControlTimeout
        timer = asyncio.ensure_future(self.sleep_ns(remaining))
        try:
            done, _ = await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                self.receive_task = None
                abandon_task(timer)
                try:
                    return task.result()
                except (EOFError, ExchangeDownError):
                    raise ReceiverEnded from None
            raise ControlTimeout
        finally:
            abandon_task(timer)

    async def receive_or_poll(self) -> str | bytes | None:
        """Poll registry timing while preserving the same pending receive task."""
        task = self.ensure_receive()
        started = self.now()
        timer = asyncio.ensure_future(self.sleep_ns(self.generation.registry_poll_ns))
        try:
            done, _ = await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                self.receive_task = None
                abandon_task(timer)
                try:
                    return task.result()
                except (EOFError, ExchangeDownError):
                    raise ReceiverEnded from None
            if self.now() - started < self.generation.registry_poll_ns:
                raise ControlTimeout
            return None
        finally:
            abandon_task(timer)

    def decode(self, frame: str | bytes) -> dict[str, object]:
        """Decode and report one inbound frame without granting liveness authority."""
        self.control.observe_inbound(
            InboundKind.TEXT_FRAME if type(frame) is str else InboundKind.BINARY_FRAME
        )
        message = decode_ws_object(
            frame,
            max_bytes=self.generation.max_frame_bytes,
            stream_label="public",
        )
        message_type = message.get("type")
        if message_type == "heartbeat":
            self.control.observe_inbound(InboundKind.HEARTBEAT)
        elif message_type == "pong":
            self.control.observe_inbound(InboundKind.PONG)
        else:
            self.control.observe_inbound(InboundKind.DATA)
        return message

    async def send(self, message: str) -> None:
        """Send through the supervisor-owned serialized writer under a deadline."""
        await self.bounded(self.control.send(message), deadline=self.deadline())
        self.control_revision += 1

    async def invalidate(
        self,
        kind: StreamInvalidationKind,
        desired: DesiredSubscriptions,
        *,
        deadline: int | None = None,
    ) -> None:
        """Publish an awaited generation fence before later control actions."""
        self.invalidation_sequence += 1
        receipt = GenerationControlInvalidation(
            plane=WsPlane.PUBLIC,
            connection_generation=self.generation.connection_generation,
            local_sequence=self.invalidation_sequence,
            kind=kind,
            evidence_sha256=desired.evidence_sha256,
        )
        await self.bounded(
            self.control.invalidate(receipt),
            deadline=self.deadline() if deadline is None else deadline,
        )

    async def wait_repair_minimum(self) -> None:
        """Measure the repair minimum and enforce an independent safety deadline."""
        target = self.now() + self.generation.repair_delay_ns
        safety = target + self.generation.control_timeout_ns
        while self.now() < target:
            before = self.now()
            await self.bounded(self.sleep_ns(target - before), deadline=safety)
            if self.now() <= before:
                raise ControlTimeout

    async def _cancel_before(
        self,
        task: asyncio.Future[Any] | None,
        *,
        deadline: int,
    ) -> None:
        if task is None:
            return
        if task.done():
            observe_task(task)
            return
        task.cancel()
        remaining = deadline - self.now()
        if remaining <= 0:
            abandon_task(task)
            return
        timer = asyncio.ensure_future(self.sleep_ns(remaining))
        try:
            done, _ = await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                observe_task(task)
                abandon_task(timer)
                return
            observe_task(timer)
            abandon_task(task)
        except asyncio.CancelledError:
            abandon_task(task)
            abandon_task(timer)
            raise

    async def finish_receive(self, *, deadline: int) -> None:
        """Observe/cancel the sole recv within one absolute finalization budget."""
        task = self.receive_task
        self.receive_task = None
        if task is not None and task.done() and not task.cancelled():
            try:
                frame = task.result()
            except Exception:
                pass
            else:
                try:
                    self.decode(frame)
                except Exception:
                    pass
            observe_task(task)
            return
        await self._cancel_before(task, deadline=deadline)


__all__ = ["PublicControlSession"]
