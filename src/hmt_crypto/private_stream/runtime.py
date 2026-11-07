"""Endpoint-bound authenticated Delta India testnet private-stream runtime."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from hmt_crypto.errors import (
    AuthError,
    ConfigurationError,
    ExchangeDownError,
    ExchangeProtocolError,
    SignatureExpiredError,
    UnauthorizedApiAccessError,
)
from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot
from hmt_crypto.exchange.delta.private_ws_auth import (
    PrivateWsAuthIssuer,
    parse_private_ws_auth_response,
    raise_for_private_ws_auth,
    require_india_testnet_private_endpoint,
)
from hmt_crypto.exchange.delta.private_ws_connection import (
    PrivateWsConnectionAuthority,
    _attest_private_ws_connection,
)
from hmt_crypto.exchange.delta.private_ws_models import (
    MarginUpdate,
    OrderSnapshot,
    OrderUpdate,
    PositionSnapshot,
    PositionUpdate,
    PrivateWsObservation,
    UserTradeUpdate,
)
from hmt_crypto.exchange.delta.private_ws_parsers import parse_private_ws_observation
from hmt_crypto.exchange.delta.private_ws_protocol import (
    PrivateWsAuthorityClass,
    PrivateWsGenerationLease,
    PrivateWsInvalidationReason,
    PrivateWsSubscriptionPlan,
    _generation_state,
    _invalidate_private_ws_generation,
    _issue_private_ws_delivery,
    _issue_private_ws_generation,
    private_ws_subscription_plan,
)
from hmt_crypto.exchange.delta.private_ws_sequence import (
    PrivateWsGenerationLedger,
    PrivateWsSequenceError,
)
from hmt_crypto.exchange.delta.websocket_codec import (
    decode_ws_object,
    encode_enable_heartbeat,
    encode_ping,
)
from hmt_crypto.exchange.delta.websocket_errors import WebSocketHandshakeError
from hmt_crypto.exchange.delta.websocket_ports import InboundKind, WsConnection, WsReceivePort
from hmt_crypto.exchange.delta.websocket_transport import (
    _PRODUCTION_CONNECTION_MARKER,
    _DeltaWsConnection,
)
from hmt_crypto.market_data.reconnect_policy import (
    ReconnectAction,
    ReconnectCause,
    ReconnectPolicy,
    cause_from_handshake,
    plan_reconnect,
    record_connection_attempt,
)
from hmt_crypto.market_data.stream_liveness import (
    DEFAULT_STREAM_LIVENESS_POLICY,
    StreamLivenessAction,
    StreamLivenessDeadlineExpired,
    StreamLivenessPolicy,
    StreamLivenessState,
    initial_liveness,
    next_liveness_action,
    observe_inbound,
    reserve_due_ping,
)
from hmt_crypto.private_stream.events import (
    PrivateStreamEvent,
    PrivateStreamReceivePort,
    _issue_private_stream_receive_port,
)
from hmt_crypto.private_stream.reconciliation import (
    PrivateWsReconciliationReceipt,
    PrivateWsReconciliationReset,
    PrivateWsRestReconciler,
    _attest_private_ws_reconciliation,
    _attest_private_ws_reconciliation_barrier,
    _issue_private_ws_reconciliation_reset,
)

_MAX_FRAME_BYTES = 64 * 1024
_MAX_OUTPUT_CAPACITY = 64
_MAX_PENDING = 64
_AUTH_DEADLINE_SECONDS = 4.0
_CONTROL_SEND_SECONDS = 4.0
_SUBSCRIPTION_ACK_SECONDS = 10.0
_SNAPSHOT_DEADLINE_SECONDS = 15.0
_RECONCILIATION_SECONDS = 30.0
_AUTHORITY_REVALIDATION_SECONDS = 1.0


def _protocol_error(operation: str) -> ExchangeProtocolError:
    return ExchangeProtocolError(context={"operation": operation, "scope": "private"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_reviewed_connection(value: object) -> bool:
    try:
        return (
            type(value) is _DeltaWsConnection
            and object.__getattribute__(value, "_ownership_marker") is _PRODUCTION_CONNECTION_MARKER
        )
    except BaseException:
        return False


def _authority(observation: PrivateWsObservation) -> PrivateWsAuthorityClass:
    if isinstance(observation, OrderUpdate):
        return PrivateWsAuthorityClass.SEQUENCED
    if isinstance(
        observation,
        (MarginUpdate, PositionUpdate, PositionSnapshot, OrderSnapshot, UserTradeUpdate),
    ):
        return PrivateWsAuthorityClass.PROVISIONAL
    raise _protocol_error("classify_delta_private_ws")


async def _finish_cancelled_tasks(*tasks: asyncio.Task[object] | None) -> None:
    """Cancel owned tasks and defer repeated outer cancellation until cleanup ends."""
    owned = tuple(task for task in tasks if task is not None)
    for task in owned:
        if not task.done():
            task.cancel()
    if not owned:
        return
    cleanup = asyncio.ensure_future(asyncio.gather(*owned, return_exceptions=True))
    cancellation_pending = False
    while True:
        try:
            await asyncio.shield(cleanup)
            break
        except asyncio.CancelledError:
            cancellation_pending = True
    if cancellation_pending:
        raise asyncio.CancelledError


class _PrivateStreamGeneration:
    """Internal actor for one reviewed physical connection generation."""

    def __init__(
        self,
        *,
        auth: PrivateWsAuthIssuer,
        plan: PrivateWsSubscriptionPlan,
        reconciliation: PrivateWsRestReconciler,
        output: asyncio.Queue[PrivateStreamEvent],
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        liveness_policy: StreamLivenessPolicy = DEFAULT_STREAM_LIVENESS_POLICY,
        attest_currentness: Callable[[], None] = lambda: None,
    ) -> None:
        if (
            type(auth) is not PrivateWsAuthIssuer
            or type(plan) is not PrivateWsSubscriptionPlan
            or type(reconciliation) is not PrivateWsRestReconciler
            or auth.account.model_dump() != reconciliation.account.model_dump()
            or type(output) is not asyncio.Queue
            or not 1 <= output.maxsize <= _MAX_OUTPUT_CAPACITY
            or not callable(monotonic_ns)
            or type(liveness_policy) is not StreamLivenessPolicy
            or not callable(attest_currentness)
        ):
            raise _protocol_error("configure_delta_private_stream")
        self._auth = auth
        self._plan = plan
        self._reconciliation = reconciliation
        self._output = output
        self._monotonic_ns = monotonic_ns
        self._liveness_policy = liveness_policy
        self._attest_currentness = attest_currentness

    def _now_ns(self) -> int:
        try:
            value = self._monotonic_ns()
        except Exception:
            raise _protocol_error("read_private_ws_monotonic_clock") from None
        if type(value) is not int or value < 0:
            raise _protocol_error("read_private_ws_monotonic_clock")
        return value

    async def _send_control(
        self,
        connection: WsConnection,
        message: str,
        *,
        state: str,
    ) -> None:
        try:
            async with asyncio.timeout(_CONTROL_SEND_SECONDS):
                self._attest_currentness()
                await connection.send(message)
        except TimeoutError:
            raise ExchangeDownError(
                context={
                    "operation": "send_delta_private_ws_control",
                    "scope": "private",
                    "state": state,
                }
            ) from None

    async def _receive(
        self,
        connection: WsConnection,
        receiver: WsReceivePort,
        liveness: StreamLivenessState,
    ) -> tuple[dict[str, object], StreamLivenessState, datetime]:
        while True:
            self._attest_currentness()
            now_ns = self._now_ns()
            try:
                decision = next_liveness_action(
                    liveness,
                    now_ns=now_ns,
                    policy=self._liveness_policy,
                )
            except (TypeError, ValueError):
                raise _protocol_error("track_delta_private_ws_liveness") from None
            if decision.action in {
                StreamLivenessAction.CLOSE_HEARTBEAT_TIMEOUT,
                StreamLivenessAction.CLOSE_PONG_TIMEOUT,
            }:
                raise ExchangeDownError(
                    context={
                        "operation": "delta_private_ws_liveness",
                        "scope": "private",
                        "state": decision.action.value,
                    }
                )
            if decision.action is StreamLivenessAction.SEND_PING:
                liveness = reserve_due_ping(
                    liveness,
                    now_ns=now_ns,
                    policy=self._liveness_policy,
                )
                await self._send_control(
                    connection,
                    encode_ping(),
                    state="ping_send_timeout",
                )
                continue
            timeout = min(
                _AUTHORITY_REVALIDATION_SECONDS,
                max(0.001, (decision.deadline_ns - now_ns) / 1_000_000_000),
            )
            try:
                async with asyncio.timeout(timeout):
                    frame = await receiver.recv()
            except TimeoutError:
                continue
            if type(frame) is not str:
                raise _protocol_error("receive_delta_private_ws")
            received_at = _utc_now()
            message = decode_ws_object(
                frame,
                max_bytes=_MAX_FRAME_BYTES,
                stream_label="private",
            )
            raw_type = message.get("type")
            kind = InboundKind.DATA
            if raw_type == "heartbeat":
                if set(message) != {"type"}:
                    raise _protocol_error("receive_delta_private_ws")
                kind = InboundKind.HEARTBEAT
            elif raw_type == "pong":
                if set(message) != {"type"}:
                    raise _protocol_error("receive_delta_private_ws")
                kind = InboundKind.PONG
            try:
                liveness = observe_inbound(
                    liveness,
                    kind=kind,
                    now_ns=self._now_ns(),
                    policy=self._liveness_policy,
                )
            except StreamLivenessDeadlineExpired:
                raise ExchangeDownError(
                    context={
                        "operation": "delta_private_ws_liveness",
                        "scope": "private",
                        "state": "late_frame",
                    }
                ) from None
            except (TypeError, ValueError):
                raise _protocol_error("track_delta_private_ws_liveness") from None
            return message, liveness, received_at

    async def _authenticate(
        self,
        connection: WsConnection,
        receiver: WsReceivePort,
        liveness: StreamLivenessState,
    ) -> StreamLivenessState:
        try:
            async with asyncio.timeout(_AUTH_DEADLINE_SECONDS):
                await connection.send(self._auth.encode_fresh_auth())
                while True:
                    message, liveness, _received_at = await self._receive(
                        connection, receiver, liveness
                    )
                    if message.get("type") in {"heartbeat", "pong"}:
                        continue
                    result = parse_private_ws_auth_response(message)
                    try:
                        raise_for_private_ws_auth(result)
                    except SignatureExpiredError:
                        self._auth.invalidate_signature_expired(correlation_id=uuid4())
                        raise
                    return liveness
        except TimeoutError:
            raise SignatureExpiredError(
                context={"operation": "authenticate_delta_private_ws", "scope": "private"}
            ) from None

    async def _receive_subscription_ack(
        self,
        connection: WsConnection,
        receiver: WsReceivePort,
        liveness: StreamLivenessState,
    ) -> StreamLivenessState:
        try:
            async with asyncio.timeout(_SUBSCRIPTION_ACK_SECONDS):
                while True:
                    message, liveness, _received_at = await self._receive(
                        connection, receiver, liveness
                    )
                    if message.get("type") in {"heartbeat", "pong"}:
                        continue
                    self._plan.parse_ack(message)
                    return liveness
        except TimeoutError:
            raise ExchangeDownError(
                context={"operation": "subscribe_delta_private_ws", "scope": "private"}
            ) from None

    async def _reconcile_while_receiving(
        self,
        *,
        connection: WsConnection,
        receiver: WsReceivePort,
        liveness: StreamLivenessState,
        ledger: PrivateWsGenerationLedger,
        generation: UUID,
        pending: list[tuple[PrivateWsObservation, datetime]],
    ) -> tuple[PrivateWsReconciliationReceipt, StreamLivenessState]:
        reconcile_task = asyncio.create_task(
            self._reconciliation.reconcile_after_baselines(
                generation=generation,
                product_id=self._plan.product_id,
                catalogue_snapshot_id=self._plan.catalogue_snapshot_id,
                catalogue_revision=self._plan.catalogue_revision,
                catalogue_content_sha256=self._plan.catalogue_content_sha256,
            )
        )
        receive_task: (
            asyncio.Task[tuple[dict[str, object], StreamLivenessState, datetime]] | None
        ) = None
        deadline_task = asyncio.create_task(asyncio.sleep(_RECONCILIATION_SECONDS))
        try:
            while True:
                if receive_task is None:
                    receive_task = asyncio.create_task(
                        self._receive(connection, receiver, liveness)
                    )
                done, _pending_tasks = await asyncio.wait(
                    {reconcile_task, receive_task, deadline_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if deadline_task in done:
                    raise ExchangeDownError(
                        context={
                            "operation": "reconcile_delta_private_ws",
                            "scope": "private",
                        }
                    )
                if receive_task in done:
                    message, liveness, received_at = receive_task.result()
                    receive_task = None
                    if message.get("type") not in {"heartbeat", "pong"}:
                        if message.get("type") in {"key-auth", "subscriptions"}:
                            raise _protocol_error("receive_delta_private_ws")
                        observation = parse_private_ws_observation(message)
                        ledger.observe(observation)
                        if len(pending) >= _MAX_PENDING:
                            raise _protocol_error("buffer_delta_private_ws")
                        pending.append((observation, received_at))
                if reconcile_task in done:
                    receipt = reconcile_task.result()
                    return receipt, liveness
        finally:
            await _finish_cancelled_tasks(deadline_task, receive_task, reconcile_task)

    def _flush(
        self,
        *,
        pending: list[tuple[PrivateWsObservation, datetime]],
        receipt: PrivateWsReconciliationReceipt,
        lease: PrivateWsGenerationLease,
    ) -> None:
        if self._output.qsize() + len(pending) > self._output.maxsize:
            raise _protocol_error("buffer_delta_private_ws")
        for observation, received_at in pending:
            self._output.put_nowait(
                _issue_private_ws_delivery(
                    lease=lease,
                    authority=_authority(observation),
                    observation=observation,
                    received_at=received_at,
                    reconciliation_evidence_sha256=receipt.evidence_sha256,
                )
            )
        pending.clear()

    def _publish_reset_and_pending(
        self,
        *,
        lease: PrivateWsGenerationLease,
        receipt: PrivateWsReconciliationReceipt,
        pending: list[tuple[PrivateWsObservation, datetime]],
    ) -> PrivateWsReconciliationReset:
        if self._output.qsize() + 1 + len(pending) > self._output.maxsize:
            raise _protocol_error("buffer_delta_private_ws")
        reset = _issue_private_ws_reconciliation_reset(
            lease=lease,
            receipt=receipt,
            received_at=receipt.observed_at,
        )
        self._output.put_nowait(reset)
        self._flush(pending=pending, receipt=receipt, lease=lease)
        return reset

    async def run(self, connection: WsConnection, *, lease: PrivateWsGenerationLease) -> None:
        """Run until disconnect; only post-reconciliation deliveries leave the actor."""
        if not _is_reviewed_connection(connection):
            raise _protocol_error("start_delta_private_ws_generation")
        generation_state = _generation_state(lease)
        if generation_state.plan is not self._plan:
            raise _protocol_error("start_delta_private_ws_generation")
        generation = generation_state.generation
        require_india_testnet_private_endpoint(self._auth.endpoint)
        receiver = connection.receiver()
        await self._send_control(
            connection,
            encode_enable_heartbeat(),
            state="heartbeat_enable_send_timeout",
        )
        liveness = initial_liveness(now_ns=self._now_ns())
        liveness = await self._authenticate(connection, receiver, liveness)
        ledger = PrivateWsGenerationLedger(
            generation=generation,
            symbol=self._plan.symbol,
            product_id=self._plan.product_id,
            external_user_id=self._auth.account.external_user_id,
        )
        ledger.authenticated()
        await self._send_control(
            connection,
            self._plan.encoded_subscribe,
            state="subscribe_send_timeout",
        )
        liveness = await self._receive_subscription_ack(connection, receiver, liveness)
        ledger.subscriptions_accepted()
        pending: list[tuple[PrivateWsObservation, datetime]] = []
        try:
            async with asyncio.timeout(_SNAPSHOT_DEADLINE_SECONDS):
                while not ledger.snapshots_ready:
                    message, liveness, received_at = await self._receive(
                        connection, receiver, liveness
                    )
                    frame_type = message.get("type")
                    if frame_type in {"heartbeat", "pong"}:
                        continue
                    if frame_type in {"key-auth", "subscriptions"}:
                        ledger.invalidate()
                        raise _protocol_error("receive_delta_private_ws")
                    observation = parse_private_ws_observation(message)
                    try:
                        ledger.observe(observation)
                    except PrivateWsSequenceError:
                        raise _protocol_error("track_delta_private_ws_sequence") from None
                    if len(pending) >= _MAX_PENDING:
                        ledger.invalidate()
                        raise _protocol_error("buffer_delta_private_ws")
                    pending.append((observation, received_at))
        except TimeoutError:
            ledger.invalidate()
            raise ExchangeDownError(
                context={
                    "operation": "await_delta_private_ws_snapshots",
                    "scope": "private",
                    "state": "snapshot_timeout",
                }
            ) from None

        ledger.begin_reconciliation()
        receipt, liveness = await self._reconcile_while_receiving(
            connection=connection,
            receiver=receiver,
            liveness=liveness,
            ledger=ledger,
            generation=generation,
            pending=pending,
        )
        receipt = _attest_private_ws_reconciliation(
            receipt,
            generation=generation,
            account=self._auth.account,
            product_id=self._plan.product_id,
            catalogue_snapshot_id=self._plan.catalogue_snapshot_id,
            catalogue_revision=self._plan.catalogue_revision,
            catalogue_content_sha256=self._plan.catalogue_content_sha256,
        )
        receipt = _attest_private_ws_reconciliation_barrier(
            receipt,
            pending=tuple(pending),
        )
        pending.clear()
        self._publish_reset_and_pending(
            lease=lease,
            receipt=receipt,
            pending=pending,
        )
        ledger.reconciliation_completed(generation=receipt.generation)

        while True:
            message, liveness, received_at = await self._receive(connection, receiver, liveness)
            frame_type = message.get("type")
            if frame_type in {"heartbeat", "pong"}:
                continue
            if frame_type in {"key-auth", "subscriptions"}:
                ledger.invalidate()
                raise _protocol_error("receive_delta_private_ws")
            observation = parse_private_ws_observation(message)
            try:
                ledger.observe(observation)
            except PrivateWsSequenceError:
                raise _protocol_error("track_delta_private_ws_sequence") from None
            if len(pending) >= _MAX_PENDING:
                ledger.invalidate()
                raise _protocol_error("buffer_delta_private_ws")
            pending.append((observation, received_at))
            self._flush(
                pending=pending,
                receipt=receipt,
                lease=lease,
            )


class PrivateStreamSupervisor:
    """Single-owner reconnect loop over one sealed connection authority."""

    def __init__(
        self,
        *,
        connection_authority: PrivateWsConnectionAuthority,
        symbol: str,
        catalogue: CatalogSnapshot,
        catalogue_snapshot_id: UUID,
        reconciliation: PrivateWsRestReconciler,
        output_capacity: int = 32,
        jitter: Callable[[], Decimal] = lambda: Decimal("0.5"),
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        try:
            connector, attempts, auth, endpoint, policy = _attest_private_ws_connection(
                connection_authority
            )
        except ConfigurationError:
            raise _protocol_error("configure_delta_private_stream_supervisor") from None
        if (
            type(connection_authority) is not PrivateWsConnectionAuthority
            or type(reconciliation) is not PrivateWsRestReconciler
            or auth.account.model_dump() != reconciliation.account.model_dump()
            or not 1 <= output_capacity <= _MAX_OUTPUT_CAPACITY
            or not callable(jitter)
            or not callable(monotonic_ns)
            or not callable(sleep)
            or type(policy) is not ReconnectPolicy
        ):
            raise _protocol_error("configure_delta_private_stream_supervisor")
        self._connection_authority = connection_authority
        self._connector = connector
        self._attempts = attempts
        self._auth = auth
        self._endpoint = endpoint
        self._plan = private_ws_subscription_plan(
            symbol=symbol,
            catalogue=catalogue,
            catalogue_snapshot_id=catalogue_snapshot_id,
        )
        self._reconciliation = reconciliation
        self._output: asyncio.Queue[PrivateStreamEvent] = asyncio.Queue(maxsize=output_capacity)
        self._receive_port = _issue_private_stream_receive_port(self._output)
        self._jitter = jitter
        self._monotonic_ns = monotonic_ns
        self._sleep = sleep
        self._policy = policy
        self._running = False

    @property
    def output(self) -> PrivateStreamReceivePort:
        """Return a receive-only bounded sealed-event port."""
        return self._receive_port

    def _attest_connection_currentness(self) -> None:
        connector, attempts, auth, endpoint, policy = _attest_private_ws_connection(
            self._connection_authority
        )
        if (
            connector is not self._connector
            or attempts is not self._attempts
            or auth is not self._auth
            or endpoint != self._endpoint
            or policy != self._policy
        ):
            raise _protocol_error("attest_delta_private_stream_supervisor")

    def _drain_events(self) -> None:
        while True:
            try:
                self._output.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _invalidate_generation(
        self,
        lease: PrivateWsGenerationLease,
        reason: PrivateWsInvalidationReason,
    ) -> None:
        self._drain_events()
        invalidation = _invalidate_private_ws_generation(
            lease=lease,
            reason=reason,
            invalidated_at=_utc_now(),
        )
        self._output.put_nowait(invalidation)

    async def run(self) -> None:
        """Run until cancellation or a terminal account authentication rejection."""
        if self._running:
            raise _protocol_error("claim_delta_private_stream_supervisor")
        self._running = True
        try:
            await self._run_owned()
        finally:
            self._running = False

    async def _run_owned(self) -> None:
        attempted_at: tuple[int, ...] = ()
        failures = 0
        while True:
            self._attest_connection_currentness()
            now_ns = self._monotonic_ns()
            if type(now_ns) is not int or now_ns < 0:
                raise _protocol_error("read_private_ws_monotonic_clock")
            attempted_at = record_connection_attempt(
                now_ns=now_ns,
                attempted_at_ns=attempted_at,
                policy=self._policy,
            )
            lease = _issue_private_ws_generation(
                account=self._auth.account,
                plan=self._plan,
                generation=uuid4(),
            )
            invalidation_reason = PrivateWsInvalidationReason.PROTOCOL
            try:
                async with self._connector.open(
                    self._endpoint, attempts=self._attempts
                ) as connection:
                    actor = _PrivateStreamGeneration(
                        auth=self._auth,
                        plan=self._plan,
                        reconciliation=self._reconciliation,
                        output=self._output,
                        monotonic_ns=self._monotonic_ns,
                        attest_currentness=self._attest_connection_currentness,
                    )
                    await actor.run(connection, lease=lease)
                cause = ReconnectCause.RECEIVER_ENDED
                invalidation_reason = PrivateWsInvalidationReason.DISCONNECTED
            except asyncio.CancelledError:
                invalidation_reason = PrivateWsInvalidationReason.CANCELLED
                raise
            except (AuthError, SignatureExpiredError, UnauthorizedApiAccessError):
                invalidation_reason = PrivateWsInvalidationReason.AUTH
                raise
            except WebSocketHandshakeError as error:
                cause = cause_from_handshake(error.category)
                invalidation_reason = PrivateWsInvalidationReason.DISCONNECTED
            except ExchangeDownError:
                cause = ReconnectCause.RECEIVER_ENDED
                invalidation_reason = PrivateWsInvalidationReason.DISCONNECTED
            except (ExchangeProtocolError, PrivateWsSequenceError):
                cause = ReconnectCause.PROTOCOL_DRIFT
                invalidation_reason = PrivateWsInvalidationReason.PROTOCOL
            finally:
                self._invalidate_generation(lease, invalidation_reason)
            failures += 1
            plan = plan_reconnect(
                cause=cause,
                consecutive_failures=failures,
                jitter_unit=self._jitter(),
                now_ns=self._monotonic_ns(),
                attempted_at_ns=attempted_at,
                policy=self._policy,
            )
            if plan.action is ReconnectAction.STOP or plan.delay_ns is None:
                raise ExchangeDownError(
                    context={"operation": "reconnect_delta_private_ws", "scope": "private"}
                )
            await self._sleep(plan.delay_ns / 1_000_000_000)


__all__ = ["PrivateStreamSupervisor"]
