"""Supervisor-owned WebSocket writer and queued liveness observation authority."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from typing import NoReturn, Protocol, SupportsIndex
from uuid import UUID

from hmt_crypto.errors import ExchangeDownError, ExchangeProtocolError
from hmt_crypto.exchange.delta.websocket_codec import encode_enable_heartbeat, encode_ping
from hmt_crypto.exchange.delta.websocket_contracts import WsPlane
from hmt_crypto.exchange.delta.websocket_ownership import _abort_owned_ws_connection
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    GenerationControlInvalidation,
    GenerationSubscriptionReceipt,
)
from hmt_crypto.exchange.delta.websocket_transport import InboundKind, WsConnection
from hmt_crypto.market_data.stream_control_evidence import (
    canonical_invalidation,
    canonical_subscription_receipt,
)
from hmt_crypto.market_data.stream_control_io import (
    SendOutcome,
    send_without_retaining_failure,
)
from hmt_crypto.market_data.stream_liveness import (
    StreamLivenessAction,
    StreamLivenessDeadlineExpired,
    StreamLivenessDecision,
    StreamLivenessState,
    initial_liveness,
    next_liveness_action,
    observe_inbound,
    reserve_due_ping,
)


class StreamEvidenceSink(Protocol):
    """Durable generation-fence and subscription-receipt boundary."""

    async def invalidate(self, receipt: GenerationControlInvalidation) -> None: ...

    async def subscriptions_accepted(self, receipt: GenerationSubscriptionReceipt) -> None: ...


class StreamControlStateError(ExchangeProtocolError):
    """The physical generation can no longer accept control work."""

    _code = "stream_control_state"
    _message = "WebSocket control state is unavailable"

    def __init__(self, state: str) -> None:
        super().__init__(context={"operation": "websocket_control", "state": state})


async def _complete_without_retaining_failure(
    operation: Callable[[], Awaitable[None]],
) -> bool:
    """Collapse an untrusted dependency failure before a public error is raised."""
    try:
        await operation()
    except asyncio.CancelledError:
        raise
    except BaseException:
        return False
    return True


def _sample_clock_without_retaining_failure(clock: Callable[[], int]) -> int | None:
    try:
        return clock()
    except BaseException:
        return None


def _valid_constructor_dependencies(
    connection: object,
    sink: object,
    monotonic_ns: object,
) -> bool:
    try:
        return (
            callable(getattr(connection, "send", None))
            and callable(getattr(sink, "invalidate", None))
            and callable(getattr(sink, "subscriptions_accepted", None))
            and callable(monotonic_ns)
        )
    except BaseException:
        return False


_OWNER_TOKEN = object()


class StreamControlOwner:
    """Serialize sends while applying inbound evidence at observation time.

    The actor's synchronous observation callback only queues bounded, sanitized
    classifications. ``tick`` is the sole state-mutation owner and reserves a
    Ping before its physical send. A failed or cancelled send permanently breaks
    this generation so a supervisor can close and reconnect it.
    """

    __slots__ = (
        "__weakref__",
        "_accepted_receipt",
        "_broken",
        "_connection",
        "_connection_generation",
        "_evidence_lock",
        "_last_control_revision",
        "_last_desired_revision",
        "_last_invalidation_sequence",
        "_last_observed_ns",
        "_last_receipt_at_ns",
        "_last_waited_control_revision",
        "_lock",
        "_max_pending_observations",
        "_monotonic_ns",
        "_observation_fault",
        "_observation_ready",
        "_observations",
        "_ownership_marker",
        "_plane",
        "_ping_send_required",
        "_send_lock",
        "_sink",
        "_state",
        "_subscription_ready",
        "_successful_actor_sends",
        "_transport_aborted",
    )

    def __init__(
        self,
        *,
        _token: object,
        _ownership_marker: object | None = None,
        connection: WsConnection,
        sink: StreamEvidenceSink,
        monotonic_ns: Callable[[], int],
        plane: WsPlane,
        connection_generation: UUID,
        max_pending_observations: int = 4096,
    ) -> None:
        if _token is not _OWNER_TOKEN:
            del (
                _token,
                _ownership_marker,
                connection,
                sink,
                monotonic_ns,
                plane,
                connection_generation,
            )
            raise TypeError("stream control owners require module issuance") from None
        dependencies_valid = _valid_constructor_dependencies(connection, sink, monotonic_ns)
        identity_valid = (
            type(plane) is WsPlane
            and type(connection_generation) is UUID
            and connection_generation.int != 0
        )
        bound_valid = (
            type(max_pending_observations) is int and 1 <= max_pending_observations <= 4096
        )
        if not dependencies_valid or not identity_valid or not bound_valid:
            del connection, sink, monotonic_ns, plane, connection_generation
            del max_pending_observations
            if not dependencies_valid:
                raise TypeError("stream control dependencies are invalid") from None
            if not identity_valid:
                raise TypeError("stream generation identity is invalid") from None
            raise ValueError("stream observation bound is invalid") from None
        self._connection = connection
        self._ownership_marker = _ownership_marker
        self._sink = sink
        self._monotonic_ns = monotonic_ns
        self._plane = plane
        self._connection_generation = connection_generation
        self._max_pending_observations = max_pending_observations
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._evidence_lock = asyncio.Lock()
        self._observations: deque[tuple[InboundKind, int]] = deque()
        self._observation_ready = asyncio.Event()
        self._subscription_ready = asyncio.Event()
        self._state: StreamLivenessState | None = None
        self._last_control_revision = 0
        self._last_desired_revision = 0
        self._last_invalidation_sequence = 0
        self._last_waited_control_revision = 0
        self._last_observed_ns: int | None = None
        self._last_receipt_at_ns: int | None = None
        self._observation_fault: str | None = None
        self._broken = False
        self._ping_send_required = False
        self._accepted_receipt: GenerationSubscriptionReceipt | None = None
        self._successful_actor_sends = 0
        self._transport_aborted = False

    def __copy__(self) -> NoReturn:
        raise TypeError("stream control owners cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("stream control owners cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("stream control owners cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("stream control owners cannot be serialized")

    def _read_now(self) -> int:
        value = _sample_clock_without_retaining_failure(self._monotonic_ns)
        if type(value) is not int or value < 0:
            raise StreamControlStateError("invalid_monotonic_clock")
        if self._last_observed_ns is not None and value < self._last_observed_ns:
            raise StreamControlStateError("monotonic_clock_regressed")
        self._last_observed_ns = value
        return value

    def _require_live(self) -> StreamLivenessState:
        if self._broken:
            raise StreamControlStateError("broken")
        if self._state is None:
            raise StreamControlStateError("not_started")
        return self._state

    def _mark_broken(self) -> None:
        self._broken = True
        self._accepted_receipt = None
        self._observation_ready.set()
        self._subscription_ready.set()

    def _abort_transport(self) -> bool:
        """Abort exact production transport before bounded child cleanup."""
        self._mark_broken()
        if self._transport_aborted:
            return True
        self._transport_aborted = _abort_owned_ws_connection(self._connection)
        return self._transport_aborted

    def _break(self, reason: str) -> StreamControlStateError:
        self._mark_broken()
        return StreamControlStateError(reason)

    async def _physical_send(self, message: str) -> None:
        outcome = await send_without_retaining_failure(self._connection, message)
        del message
        if outcome is SendOutcome.CANCELLED:
            self._mark_broken()
            raise asyncio.CancelledError from None
        if outcome is not SendOutcome.COMPLETED:
            self._mark_broken()
            raise ExchangeDownError(context={"operation": "websocket_send"}) from None

    async def start(self) -> None:
        """Enable venue heartbeats before the actor can receive or subscribe."""
        async with self._lock:
            if self._state is not None or self._broken:
                raise StreamControlStateError("already_started")
            await self._physical_send(encode_enable_heartbeat())
            try:
                now_ns = self._read_now()
            except StreamControlStateError:
                self._mark_broken()
                raise
            self._state = initial_liveness(now_ns=now_ns)

    async def send(self, message: str) -> None:
        """Serialize one actor control message with supervisor Ping sends."""
        if type(message) is not str:
            del message
            raise StreamControlStateError("invalid_control_message")
        try:
            async with self._send_lock:
                async with self._lock:
                    self._require_live()
                await self._physical_send(message)
                async with self._lock:
                    self._require_live()
                    self._successful_actor_sends += 1
        except BaseException:
            del message
            raise
        del message

    def observe_inbound(self, kind: InboundKind) -> None:
        """Queue one bounded classification at its exact observation instant."""
        if self._broken:
            return
        if self._state is None:
            self._observation_fault = "not_started"
            self._mark_broken()
            return
        if type(kind) is not InboundKind:
            self._observation_fault = "invalid_observation"
            self._observation_ready.set()
            return
        try:
            observed_at_ns = self._read_now()
        except StreamControlStateError:
            self._observation_fault = "invalid_monotonic_clock"
            self._observation_ready.set()
            return
        if len(self._observations) >= self._max_pending_observations:
            self._observation_fault = "observation_overflow"
            self._observation_ready.set()
            return
        self._observations.append((kind, observed_at_ns))
        self._observation_ready.set()

    def _apply_observations(self, state: StreamLivenessState) -> StreamLivenessState:
        if self._observation_fault is not None:
            raise self._break(self._observation_fault)
        retained = state
        while self._observations:
            kind, observed_at_ns = self._observations.popleft()
            try:
                retained = observe_inbound(
                    retained,
                    kind=kind,
                    now_ns=observed_at_ns,
                )
            except StreamLivenessDeadlineExpired as error:
                raise self._break(error.decision.action.value) from None
            except (TypeError, ValueError):
                raise self._break("invalid_observation_evidence") from None
        return retained

    async def tick(self) -> StreamLivenessDecision:
        """Apply queued evidence and perform at most one due Ping send."""
        async with self._lock:
            self._observation_ready.clear()
            state = self._apply_observations(self._require_live())
            now_ns = self._read_now()
            decision = next_liveness_action(state, now_ns=now_ns)
            if decision.action in {
                StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT,
                StreamLivenessAction.CLOSE_PONG_TIMEOUT,
            }:
                raise self._break(decision.action.value)
            if decision.action is StreamLivenessAction.SEND_PING:
                state = reserve_due_ping(state, now_ns=now_ns)
                self._state = state
                self._ping_send_required = True
            else:
                self._state = state
            return decision

    async def send_reserved_ping(self) -> None:
        """Physically send the liveness-owner-reserved Ping from the actor task."""
        async with self._send_lock:
            async with self._lock:
                self._require_live()
                if not self._ping_send_required:
                    raise StreamControlStateError("ping_not_reserved")
            await self._physical_send(encode_ping())
            async with self._lock:
                self._require_live()
                self._ping_send_required = False

    async def wait_for_observation(self) -> None:
        """Wait until sanitized inbound evidence or a terminal fault is queued."""
        while True:
            await self._observation_ready.wait()
            async with self._lock:
                self._require_live()
                if self._observation_fault is not None or self._observations:
                    return
                self._observation_ready.clear()

    async def snapshot(self) -> StreamLivenessState:
        """Return the current immutable evidence after applying queued frames."""
        async with self._lock:
            state = self._apply_observations(self._require_live())
            self._state = state
            return state

    async def current_monotonic_ns(self) -> int:
        """Sample the exact owner clock under the same generation lock."""
        async with self._lock:
            self._require_live()
            return self._read_now()

    async def invalidate(self, receipt: GenerationControlInvalidation) -> None:
        """Forward one awaited generation fence and clear readiness evidence."""
        canonical = canonical_invalidation(
            receipt,
            plane=self._plane,
            generation=self._connection_generation,
        )
        del receipt
        if canonical is None:
            raise StreamControlStateError("invalid_invalidation")
        async with self._evidence_lock:
            async with self._lock:
                self._require_live()
                if canonical.local_sequence != self._last_invalidation_sequence + 1:
                    raise StreamControlStateError("invalidation_not_current")
            try:
                completed = await _complete_without_retaining_failure(
                    lambda: self._sink.invalidate(canonical)
                )
            except asyncio.CancelledError:
                self._mark_broken()
                raise
            if not completed:
                self._mark_broken()
                raise ExchangeDownError(context={"operation": "websocket_evidence_sink"}) from None
            async with self._lock:
                self._require_live()
                self._last_invalidation_sequence = canonical.local_sequence
                self._accepted_receipt = None
                self._subscription_ready.clear()

    async def subscriptions_accepted(self, receipt: GenerationSubscriptionReceipt) -> None:
        """Forward and retain one exact generation subscription receipt."""
        canonical = canonical_subscription_receipt(
            receipt,
            plane=self._plane,
            generation=self._connection_generation,
        )
        del receipt
        if canonical is None:
            raise StreamControlStateError("invalid_subscription_receipt")
        async with self._evidence_lock:
            async with self._lock:
                self._require_live()
                now_ns = self._read_now()
                if canonical.control_revision != self._successful_actor_sends:
                    raise StreamControlStateError("subscription_receipt_not_current")
                if canonical.control_revision <= self._last_control_revision:
                    raise StreamControlStateError("subscription_receipt_replayed")
                if canonical.desired_revision < self._last_desired_revision:
                    raise StreamControlStateError("desired_revision_regressed")
                bound_receipt = GenerationSubscriptionReceipt(
                    plane=self._plane,
                    connection_generation=self._connection_generation,
                    control_revision=canonical.control_revision,
                    desired_revision=canonical.desired_revision,
                    evidence_sha256=canonical.evidence_sha256,
                    accepted_at_monotonic_ns=now_ns,
                )
            try:
                completed = await _complete_without_retaining_failure(
                    lambda: self._sink.subscriptions_accepted(bound_receipt)
                )
            except asyncio.CancelledError:
                self._mark_broken()
                raise
            if not completed:
                self._mark_broken()
                raise ExchangeDownError(context={"operation": "websocket_evidence_sink"}) from None
            async with self._lock:
                self._require_live()
                self._last_control_revision = bound_receipt.control_revision
                self._last_desired_revision = bound_receipt.desired_revision
                self._last_receipt_at_ns = bound_receipt.accepted_at_monotonic_ns
                self._accepted_receipt = bound_receipt
                self._subscription_ready.set()

    async def wait_for_subscription_receipt(self) -> GenerationSubscriptionReceipt:
        """Wait for the current generation's next complete desired-set receipt."""
        while True:
            await self._subscription_ready.wait()
            async with self._lock:
                self._require_live()
                receipt = self._accepted_receipt
                if (
                    receipt is not None
                    and receipt.control_revision > self._last_waited_control_revision
                ):
                    self._last_waited_control_revision = receipt.control_revision
                    self._subscription_ready.clear()
                    return receipt
                self._subscription_ready.clear()

    @property
    def accepted_receipt(self) -> GenerationSubscriptionReceipt | None:
        """Return the latest accepted desired-set receipt for this generation."""
        return self._accepted_receipt

    @property
    def pending_observation_count(self) -> int:
        """Return the bounded number of queued sanitized classifications."""
        return len(self._observations)


__all__ = [
    "StreamControlOwner",
    "StreamControlStateError",
    "StreamEvidenceSink",
]
