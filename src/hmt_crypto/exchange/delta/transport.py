"""Owned, bounded, retry-aware public HTTP transport for Delta."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TypeAlias
from uuid import uuid4

import httpx

from hmt_crypto.errors import (
    ConfigurationError,
    ExchangeDownError,
    ExchangeTimeoutError,
    RateLimitError,
)
from hmt_crypto.exchange.delta.endpoints import DeltaEndpointSet
from hmt_crypto.exchange.delta.lifecycle import _ClientLifecycle
from hmt_crypto.exchange.delta.rate_limiter import (
    OperationClass,
    RateRequest,
    RetryAction,
    RetryPolicy,
    WeightedRateLimiter,
    classify_retry,
    parse_rate_limit_reset_ms,
)
from hmt_crypto.exchange.delta.response import (
    drain_status_body,
    protocol,
    read_json_body,
    response_context,
)
from hmt_crypto.exchange.delta.routes import PublicRouteSpec, resolve_public_request
from hmt_crypto.exchange.delta.wire_json import (
    decode_json_object,
    error_from_envelope,
    success_result,
)
from hmt_crypto.logging import get_logger
from hmt_crypto.persistence.redaction import safe_request_summary, safe_response_summary

QueryParams: TypeAlias = tuple[tuple[str, str | int], ...]
Sleeper: TypeAlias = Callable[[float], Awaitable[None]]

_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10)


@dataclass(frozen=True, slots=True)
class _EnvelopeResult:
    result: object
    meta: object
    context: dict[str, object]


def _safe_request_log(request: httpx.Request) -> dict[str, object]:
    summary: dict[str, object] = {
        name: value for name, value in safe_request_summary(request).items()
    }
    summary["url"] = request.url.path
    return summary


class _DeltaPublicTransport:
    """Private transport reachable only through typed public client methods."""

    def __init__(
        self,
        *,
        endpoints: DeltaEndpointSet,
        limiter: WeightedRateLimiter,
        user_agent: str,
        retry_policy: RetryPolicy,
        transport: httpx.AsyncBaseTransport | None,
        sleeper: Sleeper,
        jitter_source: Callable[[], Decimal],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._endpoints = endpoints
        self._limiter = limiter
        self._retry_policy = retry_policy
        self._sleeper = sleeper
        self._jitter_source = jitter_source
        self._monotonic = monotonic
        self._logger = get_logger("exchange.delta.public")
        self._base_url = httpx.URL(endpoints.rest_base_url)
        self._owned_transport = transport or httpx.AsyncHTTPTransport(limits=_LIMITS)
        self._client = httpx.AsyncClient(
            base_url=endpoints.rest_base_url,
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
            timeout=_TIMEOUT,
            limits=_LIMITS,
            follow_redirects=False,
            transport=self._owned_transport,
        )
        self._client_close_attempted = False
        self._lifecycle = _ClientLifecycle(self._close_owned_transport)

    async def _close_owned_transport(self) -> None:
        """Retry raw transport cleanup if HTTPX made its close state terminal on failure."""
        if not self._client_close_attempted:
            self._client_close_attempted = True
            await self._client.aclose()
            return
        await self._owned_transport.aclose()

    def _check_request(self, request: httpx.Request, spec: PublicRouteSpec) -> None:
        url = request.url
        if (
            request.method != "GET"
            or url.scheme != self._base_url.scheme
            or url.host != self._base_url.host
            or url.port != self._base_url.port
            or resolve_public_request(request.method, url.path).route_id is not spec.route_id
        ):
            raise ConfigurationError(context={"operation": "public_transport_origin"})

    def _retry_decision(
        self,
        *,
        attempt: int,
        status_code: int | None = None,
        reset_header: str | None = None,
        transport_error: BaseException | None = None,
    ) -> tuple[bool, float | None]:
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
            raise ConfigurationError(context={"operation": "public_retry"}) from None
        if decision.action is RetryAction.RETRY:
            assert decision.delay_seconds is not None
            return True, float(decision.delay_seconds)
        return False, None

    def _reset_header(
        self,
        response: httpx.Response,
        context: dict[str, object],
    ) -> str | None:
        values = response.headers.get_list("x-rate-limit-reset")
        if not values:
            return None
        if len(values) != 1 or "," in values[0]:
            raise protocol(context)
        try:
            parse_rate_limit_reset_ms(values[0])
        except ConfigurationError:
            raise protocol(context) from None
        return values[0]

    def _log_attempt(
        self,
        *,
        request: httpx.Request,
        response: httpx.Response | None,
        spec: PublicRouteSpec,
        attempt: int,
        correlation_id: str,
        attempt_context: dict[str, object],
        started_at: float,
    ) -> None:
        event: dict[str, object] = {
            "endpoint_id": spec.route_id.value,
            "attempt": attempt,
            "correlation_id": correlation_id,
            "duration_ms": max(0, round((self._monotonic() - started_at) * 1000, 3)),
            "request": _safe_request_log(request),
        }
        if response is not None:
            event["http_status"] = response.status_code
            summary = safe_response_summary(response)
            # Failed framing/reset validation must not echo untrusted numeric headers.
            # Correlation below comes only from the validated response context.
            summary.pop("headers", None)
            event["response"] = summary
            request_id = attempt_context.get("request_id")
            if request_id is not None:
                event["exchange_request_id"] = request_id
        self._logger.info("exchange_request_attempt", **event)

    async def _get_json(
        self,
        spec: PublicRouteSpec,
        *,
        path: str,
        params: QueryParams = (),
    ) -> _EnvelopeResult:
        self._lifecycle.admit()
        try:
            return await self._get_json_admitted(spec, path=path, params=params)
        finally:
            self._lifecycle.release()

    async def _get_json_admitted(
        self,
        spec: PublicRouteSpec,
        *,
        path: str,
        params: QueryParams,
    ) -> _EnvelopeResult:
        resolved = resolve_public_request("GET", path)
        if resolved.route_id is not spec.route_id:
            raise ConfigurationError(context={"operation": "public_route_mismatch"})
        correlation_id = str(uuid4())
        base_context: dict[str, object] = {
            "operation": spec.route_id.value,
            "correlation_id": correlation_id,
        }

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            request = self._client.build_request("GET", path, params=params)
            self._check_request(request, spec)
            await self._limiter.acquire(RateRequest(route=spec.rate_route))
            response: httpx.Response | None = None
            started_at = self._monotonic()
            retry_delay: float | None = None
            terminal_error: Exception | None = None
            result: _EnvelopeResult | None = None
            attempt_context = base_context
            try:
                response = await self._client.send(request, stream=True)
                status = response.status_code
                attempt_context = response_context(
                    response,
                    spec=spec,
                    correlation_id=correlation_id,
                )
                if status < 200 or status > 599 or 300 <= status < 400:
                    terminal_error = protocol(attempt_context)
                elif status == 429:
                    reset = self._reset_header(response, attempt_context)
                    if reset is not None:
                        await self._limiter.observe_429(reset)
                    await drain_status_body(response, maximum=spec.max_response_bytes)
                    retry, retry_delay = self._retry_decision(
                        attempt=attempt,
                        status_code=status,
                        reset_header=reset,
                    )
                    if not retry:
                        terminal_error = RateLimitError(
                            context=attempt_context,
                            retry_after_ms=(
                                parse_rate_limit_reset_ms(reset) if reset is not None else None
                            ),
                        )
                elif 500 <= status <= 599:
                    await drain_status_body(response, maximum=spec.max_response_bytes)
                    retry, retry_delay = self._retry_decision(
                        attempt=attempt,
                        status_code=status,
                    )
                    if not retry:
                        terminal_error = ExchangeDownError(context=attempt_context)
                else:
                    body = await read_json_body(
                        response,
                        spec=spec,
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
                        result = _EnvelopeResult(
                            result=payload,
                            meta=meta,
                            context=attempt_context,
                        )
                    else:
                        terminal_error = error_from_envelope(
                            envelope,
                            endpoint_id=spec.route_id.value,
                            status_code=status,
                            context=attempt_context,
                        )
            except httpx.DecodingError:
                terminal_error = protocol(attempt_context)
            except httpx.TransportError as error:
                retry, retry_delay = self._retry_decision(attempt=attempt, transport_error=error)
                if not retry:
                    terminal_error = (
                        ExchangeTimeoutError(context=attempt_context)
                        if isinstance(error, httpx.TimeoutException)
                        else ExchangeDownError(context=attempt_context)
                    )
            finally:
                if response is not None:
                    await response.aclose()
                self._log_attempt(
                    request=request,
                    response=response,
                    spec=spec,
                    attempt=attempt,
                    correlation_id=correlation_id,
                    attempt_context=attempt_context,
                    started_at=started_at,
                )

            if result is not None:
                return result
            if terminal_error is not None:
                raise terminal_error
            if retry_delay is None:
                raise ExchangeDownError(context=base_context)
            await self._sleeper(retry_delay)

        raise ExchangeDownError(context=base_context)

    async def aclose(self) -> None:
        """Close the owned HTTP client idempotently."""
        await self._lifecycle.close()


__all__: list[str] = []
