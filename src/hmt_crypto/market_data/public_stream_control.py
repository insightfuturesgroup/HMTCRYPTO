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
    GenerationSubscriptionReceipt,
    StreamInvalidationKind,
)
from hmt_crypto.exchange.delta.websocket_transport import (
    InboundKind,
    StreamControlPort,
    WsReceivePort,
)
from hmt_crypto.market_data.public_stream_models import (
    CatalogueRefreshMailbox,
    CatalogueRefreshPort,
    CatalogueRefreshRequest,
    MonotonicNs,
    PublicConnectionGeneration,
    SleepNs,
)
from hmt_crypto.market_data.public_stream_operations import (
    _OPERATION_TOKEN,
    _BoundedOperation,
    _close_unowned_coroutine,
    _issue_operation,
    _OperationKind,
    _OperationRecord,
    _take_operation,
)
from hmt_crypto.market_data.public_stream_registry_poll import (
    RegistryPollError,
    RegistryPollOutcome,
    RegistryPollSchedule,
)
from hmt_crypto.market_data.public_stream_support import (
    ControlTimeout,
    ReceiverEnded,
    abandon_task,
    observe_task,
)

_T = TypeVar("_T")
_NANOSECONDS_PER_SECOND = 1_000_000_000


async def _event_loop_sleep_ns(delay_ns: int) -> None:
    await asyncio.sleep(delay_ns / _NANOSECONDS_PER_SECOND)


class LivenessWake(Exception):
    """The exact generation liveness owner needs actor-task service."""


class _PublicControlSession:
    """Own one persistent receive task and all bounded serialized control calls."""

    def __init__(
        self,
        *,
        generation: PublicConnectionGeneration,
        receiver: WsReceivePort,
        control: StreamControlPort,
        catalogue_refresh: CatalogueRefreshPort,
        monotonic_ns: MonotonicNs,
        sleep_ns: SleepNs,
        production_owned: bool,
    ) -> None:
        if type(production_owned) is not bool:
            raise TypeError("public control ownership is invalid")
        if production_owned:
            from hmt_crypto.exchange.delta.websocket_ownership import (
                _claim_owned_receive_capability,
            )
            from hmt_crypto.market_data.stream_control_composition import (
                is_owned_stream_control_owner,
            )

            if not (
                is_owned_stream_control_owner(control)
                and type(catalogue_refresh) is CatalogueRefreshMailbox
                and _claim_owned_receive_capability(receiver)
            ):
                del receiver, control, catalogue_refresh, monotonic_ns, sleep_ns
                raise TypeError("public control capabilities are invalid") from None
        self.generation = generation
        self.receiver = receiver
        self.control = control
        self.catalogue_refresh = catalogue_refresh
        self.monotonic_ns = monotonic_ns
        self.sleep_ns = _event_loop_sleep_ns if production_owned else sleep_ns
        self.registry_poll = RegistryPollSchedule(
            interval_ns=generation.registry_poll_ns,
            monotonic_ns=self.now,
            sleep_ns=self.sleep_ns,
        )
        self.production_owned = production_owned
        self.receive_task: asyncio.Task[str | bytes] | None = None
        self.liveness_task: asyncio.Task[None] | None = None
        self.control_revision = 0
        self.invalidation_sequence = 0

    def now(self) -> int:
        """Read one strict nonnegative injected monotonic instant."""
        try:
            value = self.monotonic_ns()
        except BaseException:
            raise ControlTimeout from None
        if type(value) is not int or value < 0:
            raise ControlTimeout
        return value

    def deadline(self, *, after_ns: int | None = None) -> int:
        """Create a local absolute deadline without accepting remote progress."""
        delay = self.generation.control_timeout_ns if after_ns is None else after_ns
        if type(delay) is not int or delay <= 0:
            raise ControlTimeout
        return self.now() + delay

    def bind_liveness_task(self, task: asyncio.Task[None] | None) -> None:
        """Bind the exact owner task that may wake blocked receive operations."""
        if task is not None and type(task) is not asyncio.Task:
            raise TypeError("public liveness task is invalid")
        self.liveness_task = task

    async def bounded(self, operation: object, *, deadline: int) -> Any:
        """Run only an exact module-issued operation under the local deadline."""
        record = _take_operation(operation, self)
        if record is None:
            _close_unowned_coroutine(operation)
            del operation
            raise TypeError("bounded operation is not module-owned") from None
        del operation
        awaitable = self._operation_awaitable(record)
        del record
        if awaitable is None:
            raise TypeError("bounded operation is not module-owned") from None
        if self.production_owned:
            return await self._run_owned_bounded(awaitable, deadline=deadline)
        return await self._run_bounded(awaitable, deadline=deadline)

    def _operation_awaitable(self, record: _OperationRecord) -> Awaitable[Any] | None:
        payload = record.payload
        if record.kind is _OperationKind.SEND and type(payload) is str:
            return self.control.send(payload)
        if record.kind is _OperationKind.RECEIPT and type(payload) is GenerationSubscriptionReceipt:
            return self.control.subscriptions_accepted(payload)
        if record.kind is _OperationKind.REFRESH and type(payload) is CatalogueRefreshRequest:
            return self.catalogue_refresh.request_catalogue_refresh(payload)
        if (
            record.kind is _OperationKind.INVALIDATE
            and type(payload) is GenerationControlInvalidation
        ):
            return self.control.invalidate(payload)
        if record.kind is _OperationKind.SLEEP and type(payload) is int and payload > 0:
            return self.sleep_ns(payload)
        return None

    async def _run_bounded(self, awaitable: Awaitable[_T], *, deadline: int) -> _T:
        remaining = deadline - self.now()
        if remaining <= 0:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise ControlTimeout
        operation = asyncio.ensure_future(awaitable)
        timer = asyncio.ensure_future(self.sleep_ns(remaining))
        try:
            waiters: set[asyncio.Future[Any]] = {operation, timer}
            terminal = self.liveness_task
            if terminal is not None:
                waiters.add(terminal)
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if terminal is not None and terminal in done:
                abandon_task(operation)
                abandon_task(timer)
                raise ControlTimeout
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

    async def _drain_owned_task(self, task: asyncio.Future[Any]) -> None:
        if not task.done():
            task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if task.done():
            observe_task(task)

    def _abort_owned_control(self) -> None:
        from hmt_crypto.market_data.stream_control_owner import StreamControlOwner

        control = self.control
        if type(control) is StreamControlOwner:
            control._abort_transport()

    async def _run_owned_bounded(self, awaitable: Awaitable[_T], *, deadline: int) -> _T:
        """Bound exact cancellation-cooperative operations without detaching children."""
        remaining = deadline - self.now()
        if remaining <= 0:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise ControlTimeout
        operation = asyncio.ensure_future(awaitable)
        timer = asyncio.create_task(_event_loop_sleep_ns(remaining))
        try:
            waiters: set[asyncio.Future[Any]] = {operation, timer}
            terminal = self.liveness_task
            if terminal is not None:
                waiters.add(terminal)
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if terminal is not None and terminal in done:
                self._abort_owned_control()
                await self._drain_owned_task(operation)
                await self._drain_owned_task(timer)
                raise ControlTimeout
            if operation in done:
                await self._drain_owned_task(timer)
                return operation.result()
            self._abort_owned_control()
            await self._drain_owned_task(operation)
            await self._drain_owned_task(timer)
            raise ControlTimeout
        except asyncio.CancelledError:
            self._abort_owned_control()
            await self._drain_owned_task(operation)
            await self._drain_owned_task(timer)
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
            waiters: set[asyncio.Future[Any]] = {task, timer}
            terminal = self.liveness_task
            if terminal is not None:
                waiters.add(terminal)
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if terminal is not None and terminal in done:
                raise LivenessWake
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
        """Receive while preserving one absolute, non-resetting registry timer."""
        task = self.ensure_receive()
        try:
            outcome = await self.registry_poll.wait(
                receive=task,
                terminal=self.liveness_task,
            )
        except RegistryPollError:
            raise ControlTimeout from None
        if outcome is RegistryPollOutcome.TERMINAL:
            raise LivenessWake
        if outcome is RegistryPollOutcome.DUE:
            return None
        self.receive_task = None
        try:
            return task.result()
        except (EOFError, ExchangeDownError):
            raise ReceiverEnded from None

    def complete_registry_poll(self) -> None:
        """Advance the schedule only after registry reconciliation completes."""
        try:
            self.registry_poll.advance()
        except RegistryPollError:
            raise ControlTimeout from None

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
        try:
            await self.bounded(
                _issue_operation(self, _OperationKind.SEND, message),
                deadline=self.deadline(),
            )
        except BaseException:
            del message
            raise
        del message
        self.control_revision += 1

    async def subscriptions_accepted(
        self,
        receipt: GenerationSubscriptionReceipt,
        *,
        deadline: int,
    ) -> None:
        """Publish one receipt through an exact module-issued operation."""
        await self.bounded(
            _issue_operation(self, _OperationKind.RECEIPT, receipt),
            deadline=deadline,
        )

    async def request_catalogue_refresh(
        self,
        request: CatalogueRefreshRequest,
        *,
        deadline: int,
    ) -> None:
        """Request one refresh through an exact module-issued operation."""
        await self.bounded(
            _issue_operation(self, _OperationKind.REFRESH, request),
            deadline=deadline,
        )

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
            _issue_operation(self, _OperationKind.INVALIDATE, receipt),
            deadline=self.deadline() if deadline is None else deadline,
        )

    async def wait_repair_minimum(self) -> None:
        """Measure the repair minimum and enforce an independent safety deadline."""
        target = self.now() + self.generation.repair_delay_ns
        safety = target + self.generation.control_timeout_ns
        while self.now() < target:
            before = self.now()
            await self.bounded(
                _issue_operation(self, _OperationKind.SLEEP, target - before),
                deadline=safety,
            )
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
        """Cancel the registry timer and sole recv within one finalization budget."""
        await self._cancel_before(self.registry_poll.take_timer(), deadline=deadline)
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


__all__ = [
    "LivenessWake",
    "_BoundedOperation",
    "_OPERATION_TOKEN",
    "_OperationKind",
    "_issue_operation",
]
