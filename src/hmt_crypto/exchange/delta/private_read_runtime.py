"""Owned, signed, retry-aware runtime for reviewed Delta India private reads."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import httpx

from hmt_crypto.config import DeltaEnvironment
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import (
    AuthError,
    ConfigurationError,
    ExchangeDownError,
    ExchangeProtocolError,
    ExchangeTimeoutError,
    HmtCryptoError,
    RateLimitError,
    SignatureExpiredError,
)
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.delta.lifecycle import _ClientLifecycle
from hmt_crypto.exchange.delta.private_admission import (
    PrivateReadAdmission,
    _admit_private_read_nonblocking,
)
from hmt_crypto.exchange.delta.private_limiter_registry import (
    PrivateLimiterLease,
    _revalidate_private_limiter_lease_nonblocking,
)
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin
from hmt_crypto.exchange.delta.private_read_audit import (
    PrivateReadAuditEvent,
    PrivateReadAuditOutcome,
    PrivateReadAuditPhase,
    PrivateReadAuditSink,
)
from hmt_crypto.exchange.delta.private_read_clock import PrivateReadTrustedInstantIssuer
from hmt_crypto.exchange.delta.private_read_limiter import (
    _acquire_private_read_permit,
    _observe_private_read_429,
)
from hmt_crypto.exchange.delta.private_read_wire import (
    _attest_frozen_private_read,
    _FrozenPrivateReadRequestV1,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteSpec, get_private_route_spec
from hmt_crypto.exchange.delta.rate_limit_policy import (
    RateLimitScope,
    ScopeKind,
    parse_rate_limit_reset_ms,
)
from hmt_crypto.exchange.delta.response import drain_status_body, read_json_body
from hmt_crypto.exchange.delta.retry_policy import (
    DEFAULT_RETRY_POLICY,
    OperationClass,
    RetryAction,
    RetryPolicy,
    classify_retry,
)
from hmt_crypto.exchange.delta.routes import PublicRouteSpec
from hmt_crypto.exchange.delta.wire_json import (
    decode_json_object,
    error_from_envelope,
    success_result,
)
from hmt_crypto.exchange.private_dispatch import TrustedDispatchInstant
from hmt_crypto.worker import clock as trusted_clock
from hmt_crypto.worker import leadership

Sleeper = Callable[[float], Awaitable[None]]

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)


@dataclass(frozen=True, slots=True)
class _PrivateEnvelopeResult:
    result: object
    meta: object
    correlation_id: UUID
    requested_at: datetime
    received_at: datetime


class _DeltaPrivateReadRuntime:
    """Internal transport with no write-route or raw-credential surface."""

    def __init__(
        self,
        *,
        account: AccountRef,
        signer: credentials.OwnedDeltaSignerSnapshot,
        account_binding: credentials.AccountBindingProofSnapshot,
        credential_currentness: credentials.Task27PrivateCredentialCurrentnessPort,
        worker_proof: leadership.Task210SinglePrivateWorkerProof,
        worker_currentness: leadership.Task210PrivateWorkerCurrentnessPort,
        clock: trusted_clock.Task210TrustedDispatchClock,
        clock_currentness: trusted_clock.Task210TrustedClockCurrentnessPort,
        trusted_instant_issuer: PrivateReadTrustedInstantIssuer,
        limiter_lease: PrivateLimiterLease,
        audit_sink: PrivateReadAuditSink,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        sleeper: Sleeper | None = None,
        jitter_source: Callable[[], Decimal] | None = None,
    ) -> None:
        expected = (
            (account, AccountRef),
            (signer, credentials.OwnedDeltaSignerSnapshot),
            (account_binding, credentials.AccountBindingProofSnapshot),
            (credential_currentness, credentials.Task27PrivateCredentialCurrentnessPort),
            (worker_proof, leadership.Task210SinglePrivateWorkerProof),
            (worker_currentness, leadership.Task210PrivateWorkerCurrentnessPort),
            (clock, trusted_clock.Task210TrustedDispatchClock),
            (clock_currentness, trusted_clock.Task210TrustedClockCurrentnessPort),
            (trusted_instant_issuer, PrivateReadTrustedInstantIssuer),
            (limiter_lease, PrivateLimiterLease),
            (audit_sink, PrivateReadAuditSink),
            (retry_policy, RetryPolicy),
        )
        if any(type(value) is not expected_type for value, expected_type in expected):
            raise ConfigurationError(context={"operation": "private_read_runtime"})
        if sleeper is not None and not callable(sleeper):
            raise ConfigurationError(context={"operation": "private_read_runtime"})
        if jitter_source is not None and not callable(jitter_source):
            raise ConfigurationError(context={"operation": "private_read_runtime"})
        self._account = AccountRef.model_validate(account).model_copy()
        origin = _canonical_private_origin(self._account.region, self._account.environment)
        slot = (
            credentials.IndiaCredentialSlot.INDIA_TESTNET
            if self._account.environment is DeltaEnvironment.TESTNET
            else credentials.IndiaCredentialSlot.INDIA_PROD
        )
        try:
            returned_binding = credential_currentness.revalidate_private_read_nonblocking(
                signer=signer,
                proof=account_binding,
                expected_account=self._account,
                expected_origin=origin,
                expected_slot=slot,
                expected_credential_version=self._account.credential_version,
            )
        except Exception:
            raise ConfigurationError(context={"operation": "private_read_runtime"}) from None
        if returned_binding is not account_binding:
            raise ConfigurationError(context={"operation": "private_read_runtime"})
        try:
            returned_worker = worker_currentness.revalidate_single_private_worker_nonblocking(
                worker_proof,
                expected_external_user_id=self._account.external_user_id,
                expected_region=self._account.region,
                expected_environment=self._account.environment,
                expected_process_id=worker_proof.process_id,
                expected_process_incarnation_id=worker_proof.process_incarnation_id,
            )
            returned_clock = clock_currentness.revalidate_dispatch_clock_nonblocking(
                clock,
                expected_process_id=worker_proof.process_id,
                expected_process_incarnation_id=worker_proof.process_incarnation_id,
            )
            trusted_instant_issuer.attest_association_nonblocking(
                clock=clock,
                clock_currentness=clock_currentness,
                expected_process_id=worker_proof.process_id,
                expected_process_incarnation_id=worker_proof.process_incarnation_id,
            )
            returned_lease = _revalidate_private_limiter_lease_nonblocking(
                limiter_lease,
                expected_scope=RateLimitScope(
                    self._account.region.value,
                    self._account.environment.value,
                    ScopeKind.EXTERNAL_USER,
                    self._account.external_user_id,
                ),
                expected_process_id=worker_proof.process_id,
                expected_process_incarnation_id=worker_proof.process_incarnation_id,
            )
            audit_sink.attest_account_nonblocking(self._account)
        except Exception:
            raise ConfigurationError(context={"operation": "private_read_runtime"}) from None
        if (
            returned_worker is not worker_proof
            or returned_clock is not clock
            or returned_lease is not limiter_lease
        ):
            raise ConfigurationError(context={"operation": "private_read_runtime"})
        self._signer = signer
        self._account_binding = account_binding
        self._credential_currentness = credential_currentness
        self._worker_proof = worker_proof
        self._worker_currentness = worker_currentness
        self._clock = clock
        self._clock_currentness = clock_currentness
        self._trusted_instant_issuer = trusted_instant_issuer
        self._limiter_lease = limiter_lease
        self._audit_sink = audit_sink
        self._retry_policy = retry_policy
        self._sleeper = sleeper or asyncio.sleep
        self._jitter_source = jitter_source or (lambda: Decimal("0.5"))
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=_LIMITS,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._lifecycle = _ClientLifecycle(self._client.aclose)

    def _admit(self, request: _FrozenPrivateReadRequestV1) -> PrivateReadAdmission:
        return _admit_private_read_nonblocking(
            request=request,
            account=self._account,
            signer=self._signer,
            account_binding=self._account_binding,
            credential_currentness=self._credential_currentness,
            worker_proof=self._worker_proof,
            worker_currentness=self._worker_currentness,
            clock=self._clock,
            clock_currentness=self._clock_currentness,
            limiter_lease=self._limiter_lease,
        )

    @property
    def account(self) -> AccountRef:
        """Return the exact runtime account as a defensive copy."""
        return self._account.model_copy()

    def _fresh_instant(self) -> TrustedDispatchInstant:
        return self._trusted_instant_issuer.issue_fresh_nonblocking(
            clock=self._clock,
            clock_currentness=self._clock_currentness,
            expected_process_id=self._worker_proof.process_id,
            expected_process_incarnation_id=self._worker_proof.process_incarnation_id,
        )

    async def _audit(
        self,
        *,
        correlation_id: UUID,
        request: _FrozenPrivateReadRequestV1,
        attempt: int,
        phase: PrivateReadAuditPhase,
        outcome: PrivateReadAuditOutcome,
        instant: TrustedDispatchInstant,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        event = PrivateReadAuditEvent(
            event_id=uuid4(),
            correlation_id=correlation_id,
            account_id=self._account.account_id,
            external_user_id=self._account.external_user_id,
            route_id=request.route_id,
            request_evidence_sha256=request.request_evidence_sha256,
            attempt=attempt,
            phase=phase,
            outcome=outcome,
            occurred_at=datetime.fromtimestamp(instant.utc_epoch_seconds, UTC),
            http_status=status_code,
            error_code=error_code,
        )
        receipt = await self._audit_sink.append_durable(event)
        if receipt.event_id != event.event_id:
            raise ConfigurationError(context={"operation": "private_read_audit"})

    @staticmethod
    def _response_context(
        response: httpx.Response,
        *,
        spec: PrivateRouteSpec,
        correlation_id: UUID,
    ) -> dict[str, object]:
        context: dict[str, object] = {
            "operation": spec.route_id.value,
            "http_status": response.status_code,
            "correlation_id": str(correlation_id),
        }
        request_ids = response.headers.get_list("x-request-id")
        if (
            len(request_ids) == 1
            and 1 <= len(request_ids[0]) <= 128
            and request_ids[0].isascii()
            and all(character.isalnum() or character in "._:-" for character in request_ids[0])
        ):
            context["request_id"] = request_ids[0]
        return context

    @staticmethod
    def _reset_header(response: httpx.Response, context: dict[str, object]) -> str | None:
        values = response.headers.get_list("x-rate-limit-reset")
        if not values:
            return None
        if len(values) != 1 or "," in values[0]:
            raise ExchangeProtocolError(context=context)
        try:
            parse_rate_limit_reset_ms(values[0])
        except ConfigurationError:
            raise ExchangeProtocolError(context=context) from None
        return values[0]

    def _retry_delay(
        self,
        *,
        attempt: int,
        status_code: int | None = None,
        reset_header: str | None = None,
        transport_error: BaseException | None = None,
    ) -> float | None:
        try:
            decision = classify_retry(
                operation=OperationClass.SAFE_READ,
                attempt=attempt,
                status_code=status_code,
                reset_header=reset_header,
                transport_error=transport_error,
                bytes_may_have_been_sent=False,
                jitter_unit=self._jitter_source(),
                policy=self._retry_policy,
            )
        except (TypeError, ValueError):
            raise ConfigurationError(context={"operation": "private_read_retry"}) from None
        if decision.action is RetryAction.RETRY:
            if decision.delay_seconds is None:
                raise ConfigurationError(context={"operation": "private_read_retry"})
            return float(decision.delay_seconds)
        return None

    @staticmethod
    def _check_request(
        request: httpx.Request,
        frozen: _FrozenPrivateReadRequestV1,
    ) -> None:
        expected_raw_path = frozen.path.encode("ascii") + frozen.query_string
        url = request.url
        origin = httpx.URL(frozen.origin)
        if (
            request.method != "GET"
            or request.content != b""
            or url.scheme != "https"
            or url.scheme != origin.scheme
            or url.host != origin.host
            or url.port != origin.port
            or url.raw_path != expected_raw_path
        ):
            raise ConfigurationError(context={"operation": "private_read_origin"})

    def _invalidate_auth_failure(
        self,
        correlation_id: UUID,
        *,
        invalidate_clock: bool,
    ) -> None:
        self._credential_currentness.invalidate_signature_expired_nonblocking(
            signer=self._signer,
            proof=self._account_binding,
            correlation_id=correlation_id,
        )
        if invalidate_clock:
            self._clock_currentness.invalidate_signature_expired_nonblocking(
                self._clock,
                correlation_id=correlation_id,
            )

    async def _get_json(
        self,
        request: _FrozenPrivateReadRequestV1,
    ) -> _PrivateEnvelopeResult:
        """Execute one admitted reviewed read; no write request can cross this boundary."""
        self._lifecycle.admit()
        try:
            return await self._get_json_admitted(request)
        finally:
            self._lifecycle.release()

    async def _get_json_admitted(
        self,
        request: _FrozenPrivateReadRequestV1,
    ) -> _PrivateEnvelopeResult:
        frozen = _attest_frozen_private_read(request)
        spec = get_private_route_spec(frozen.route_id)
        correlation_id = uuid4()
        base_context: dict[str, object] = {
            "operation": spec.route_id.value,
            "correlation_id": str(correlation_id),
        }

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            admission = self._admit(frozen)
            await _acquire_private_read_permit(
                lease=self._limiter_lease,
                admission=admission,
                request=frozen,
            )
            self._admit(frozen)
            audit_instant = self._fresh_instant()
            await self._audit(
                correlation_id=correlation_id,
                request=frozen,
                attempt=attempt,
                phase=PrivateReadAuditPhase.PRE_DISPATCH,
                outcome=PrivateReadAuditOutcome.DISPATCH_PENDING,
                instant=audit_instant,
            )
            self._admit(frozen)
            signing_instant = self._fresh_instant()
            timestamp = signing_instant.utc_epoch_seconds
            signed = credentials._sign_owned_private_read_at(
                self._signer,
                frozen,
                timestamp=timestamp,
            )
            headers = signed.as_headers()
            headers["Accept"] = "application/json"
            headers["Accept-Encoding"] = "gzip"
            url = f"{frozen.origin}{frozen.path}{frozen.query_string.decode('ascii')}"
            http_request = self._client.build_request("GET", url, headers=headers)
            del headers, signed
            self._check_request(http_request, frozen)
            requested_at = datetime.fromtimestamp(timestamp, UTC)
            response: httpx.Response | None = None
            retry_delay: float | None = None
            terminal_error: Exception | None = None
            result: _PrivateEnvelopeResult | None = None
            attempt_context = base_context
            status_code: int | None = None
            received_instant: TrustedDispatchInstant | None = None
            retry_code: str | None = None
            try:
                response = await self._client.send(http_request, stream=True)
                status = response.status_code
                status_code = status
                attempt_context = self._response_context(
                    response,
                    spec=spec,
                    correlation_id=correlation_id,
                )
                if status < 200 or status > 599 or 300 <= status < 400:
                    terminal_error = ExchangeProtocolError(context=attempt_context)
                elif status == 429:
                    reset = self._reset_header(response, attempt_context)
                    if reset is not None:
                        await _observe_private_read_429(
                            self._limiter_lease,
                            reset_header=reset,
                        )
                    await drain_status_body(response, maximum=spec.max_response_bytes)
                    retry_delay = self._retry_delay(
                        attempt=attempt,
                        status_code=status,
                        reset_header=reset,
                    )
                    retry_code = "rate_limit_retry"
                    if retry_delay is None:
                        terminal_error = RateLimitError(
                            context=attempt_context,
                            retry_after_ms=(
                                parse_rate_limit_reset_ms(reset) if reset is not None else None
                            ),
                        )
                elif 500 <= status <= 599:
                    await drain_status_body(response, maximum=spec.max_response_bytes)
                    retry_delay = self._retry_delay(attempt=attempt, status_code=status)
                    retry_code = "exchange_retry"
                    if retry_delay is None:
                        terminal_error = ExchangeDownError(context=attempt_context)
                else:
                    body = await read_json_body(
                        response,
                        spec=cast(PublicRouteSpec, spec),
                        context=attempt_context,
                    )
                    envelope = decode_json_object(
                        body,
                        endpoint_id=spec.route_id.value,
                        context=attempt_context,
                    )
                    if 200 <= status < 300:
                        payload, meta = success_result(
                            envelope,
                            endpoint_id=spec.route_id.value,
                            context=attempt_context,
                        )
                        received_instant = self._fresh_instant()
                        result = _PrivateEnvelopeResult(
                            result=payload,
                            meta=meta,
                            correlation_id=correlation_id,
                            requested_at=requested_at,
                            received_at=datetime.fromtimestamp(
                                received_instant.utc_epoch_seconds,
                                UTC,
                            ),
                        )
                    else:
                        terminal_error = error_from_envelope(
                            envelope,
                            endpoint_id=spec.route_id.value,
                            status_code=status,
                            context=attempt_context,
                        )
                if received_instant is None:
                    received_instant = self._fresh_instant()
            except httpx.DecodingError:
                received_instant = self._fresh_instant()
                terminal_error = ExchangeProtocolError(context=attempt_context)
            except httpx.TransportError as error:
                if received_instant is None:
                    received_instant = self._fresh_instant()
                retry_delay = self._retry_delay(attempt=attempt, transport_error=error)
                retry_code = "transport_retry"
                if retry_delay is None:
                    terminal_error = (
                        ExchangeTimeoutError(context=attempt_context)
                        if isinstance(error, httpx.TimeoutException)
                        else ExchangeDownError(context=attempt_context)
                    )
            except HmtCryptoError as error:
                if received_instant is None:
                    received_instant = self._fresh_instant()
                terminal_error = error
            finally:
                if response is not None:
                    await response.aclose()

            if result is not None:
                if received_instant is None:
                    raise ConfigurationError(context={"operation": "private_read_clock"})
                await self._audit(
                    correlation_id=correlation_id,
                    request=frozen,
                    attempt=attempt,
                    phase=PrivateReadAuditPhase.TERMINAL,
                    outcome=PrivateReadAuditOutcome.SUCCESS,
                    instant=received_instant,
                    status_code=status_code,
                )
                return result
            if terminal_error is not None:
                if isinstance(terminal_error, AuthError):
                    self._invalidate_auth_failure(
                        correlation_id,
                        invalidate_clock=isinstance(terminal_error, SignatureExpiredError),
                    )
                if received_instant is None:
                    raise ConfigurationError(context={"operation": "private_read_clock"})
                error_code = (
                    terminal_error.code
                    if isinstance(terminal_error, HmtCryptoError)
                    else "private_read_failure"
                )
                await self._audit(
                    correlation_id=correlation_id,
                    request=frozen,
                    attempt=attempt,
                    phase=PrivateReadAuditPhase.TERMINAL,
                    outcome=PrivateReadAuditOutcome.FAILURE,
                    instant=received_instant,
                    status_code=status_code,
                    error_code=error_code,
                )
                raise terminal_error
            if retry_delay is None:
                raise ExchangeDownError(context=base_context)
            if received_instant is None or retry_code is None:
                raise ConfigurationError(context={"operation": "private_read_retry"})
            await self._audit(
                correlation_id=correlation_id,
                request=frozen,
                attempt=attempt,
                phase=PrivateReadAuditPhase.TERMINAL,
                outcome=PrivateReadAuditOutcome.RETRY,
                instant=received_instant,
                status_code=status_code,
                error_code=retry_code,
            )
            await self._sleeper(retry_delay)

        raise ExchangeDownError(context=base_context)

    async def aclose(self) -> None:
        """Close the owned HTTP client idempotently."""
        await self._lifecycle.close()


__all__: list[str] = []
