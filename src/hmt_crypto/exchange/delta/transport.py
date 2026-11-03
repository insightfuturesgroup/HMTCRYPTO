"""Owned, bounded, retry-aware public HTTP transport for Delta."""

from __future__ import annotations

import asyncio
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
    ExchangeProtocolError,
    ExchangeTimeoutError,
    RateLimitError,
)
from hmt_crypto.exchange.delta.endpoints import DeltaEndpointSet
from hmt_crypto.exchange.delta.rate_limiter import (
    OperationClass,
    RateRequest,
    RetryAction,
    RetryPolicy,
    WeightedRateLimiter,
    classify_retry,
    parse_rate_limit_reset_ms,
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


def _protocol(spec: PublicRouteSpec, status: int | None = None) -> ExchangeProtocolError:
    context: dict[str, object] = {"operation": spec.route_id.value}
    if status is not None:
        context["http_status"] = status
    return ExchangeProtocolError(context=context)


def _validate_media_type(response: httpx.Response, spec: PublicRouteSpec) -> None:
    values = response.headers.get_list("content-type")
    if len(values) != 1 or "," in values[0]:
        raise _protocol(spec, response.status_code)
    media_type = values[0].split(";", 1)[0].strip().casefold()
    parts = media_type.split("/")
    if (
        len(parts) != 2
        or not all(parts)
        or not media_type.isascii()
        or any(not character.isprintable() or character.isspace() for character in media_type)
        or (media_type != "application/json" and not parts[1].endswith("+json"))
    ):
        raise _protocol(spec, response.status_code)


def _declared_length(response: httpx.Response, spec: PublicRouteSpec) -> int | None:
    values = response.headers.get_list("content-length")
    if not values:
        return None
    if len(values) != 1 or "," in values[0]:
        raise _protocol(spec, response.status_code)
    value = values[0]
    if not value or not value.isascii() or not value.isdecimal():
        raise _protocol(spec, response.status_code)
    if int(value) > spec.max_response_bytes:
        raise _protocol(spec, response.status_code)
    return int(value)


async def _bounded_body(response: httpx.Response, spec: PublicRouteSpec) -> bytes:
    _validate_media_type(response, spec)
    declared_length = _declared_length(response, spec)
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > spec.max_response_bytes:
                raise _protocol(spec, response.status_code)
            chunks.append(chunk)
    except asyncio.CancelledError:
        raise
    except ExchangeProtocolError:
        raise
    except (httpx.HTTPError, UnicodeError, ValueError):
        raise _protocol(spec, response.status_code) from None
    if (
        declared_length is not None
        and not response.headers.get("content-encoding")
        and size != declared_length
    ):
        raise _protocol(spec, response.status_code)
    return b"".join(chunks)


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
        self._closed = False
        self._logger = get_logger("exchange.delta.public")
        self._base_url = httpx.URL(endpoints.rest_base_url)
        self._client = httpx.AsyncClient(
            base_url=endpoints.rest_base_url,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=_TIMEOUT,
            limits=_LIMITS,
            follow_redirects=False,
            transport=transport,
        )

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

    def _reset_header(self, response: httpx.Response, spec: PublicRouteSpec) -> str | None:
        values = response.headers.get_list("x-rate-limit-reset")
        if not values:
            return None
        if len(values) != 1 or "," in values[0]:
            raise _protocol(spec, response.status_code)
        try:
            parse_rate_limit_reset_ms(values[0])
        except ConfigurationError:
            raise _protocol(spec, response.status_code) from None
        return values[0]

    def _log_attempt(
        self,
        *,
        request: httpx.Request,
        response: httpx.Response | None,
        spec: PublicRouteSpec,
        attempt: int,
        correlation_id: str,
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
            event["response"] = safe_response_summary(response)
            request_id = response.headers.get("x-request-id")
            if (
                request_id
                and len(request_id) <= 128
                and all(0x21 <= ord(character) <= 0x7E for character in request_id)
            ):
                event["exchange_request_id"] = request_id
        self._logger.info("exchange_request_attempt", **event)

    async def _get_json(
        self,
        spec: PublicRouteSpec,
        *,
        path: str,
        params: QueryParams = (),
    ) -> _EnvelopeResult:
        if self._closed:
            raise ConfigurationError(context={"operation": "public_client", "state": "closed"})
        resolved = resolve_public_request("GET", path)
        if resolved.route_id is not spec.route_id:
            raise ConfigurationError(context={"operation": "public_route_mismatch"})
        correlation_id = str(uuid4())

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            request = self._client.build_request("GET", path, params=params)
            self._check_request(request, spec)
            await self._limiter.acquire(RateRequest(route=spec.rate_route))
            response: httpx.Response | None = None
            started_at = self._monotonic()
            retry_delay: float | None = None
            terminal_error: Exception | None = None
            result: _EnvelopeResult | None = None
            try:
                response = await self._client.send(request, stream=True)
                status = response.status_code
                if status < 200 or status > 599 or 300 <= status < 400:
                    terminal_error = _protocol(spec, status)
                else:
                    body = await _bounded_body(response, spec)
                    envelope = decode_json_object(body, endpoint_id=spec.route_id.value)
                    if status == 429:
                        reset = self._reset_header(response, spec)
                        if reset is not None:
                            await self._limiter.observe_429(reset)
                        retry, retry_delay = self._retry_decision(
                            attempt=attempt, status_code=status, reset_header=reset
                        )
                        if not retry:
                            terminal_error = RateLimitError(
                                context={"operation": spec.route_id.value, "http_status": status},
                                retry_after_ms=(
                                    parse_rate_limit_reset_ms(reset) if reset is not None else None
                                ),
                            )
                    elif 500 <= status <= 599:
                        retry, retry_delay = self._retry_decision(
                            attempt=attempt, status_code=status
                        )
                        if not retry:
                            terminal_error = ExchangeDownError(
                                context={"operation": spec.route_id.value, "http_status": status}
                            )
                    elif 200 <= status < 500:
                        if 200 <= status < 300:
                            payload, meta = success_result(
                                envelope, endpoint_id=spec.route_id.value
                            )
                            result = _EnvelopeResult(result=payload, meta=meta)
                        else:
                            terminal_error = error_from_envelope(
                                envelope,
                                endpoint_id=spec.route_id.value,
                                status_code=status,
                            )
            except asyncio.CancelledError:
                raise
            except httpx.DecodingError:
                terminal_error = _protocol(spec)
            except httpx.TransportError as error:
                retry, retry_delay = self._retry_decision(attempt=attempt, transport_error=error)
                if not retry:
                    terminal_error = (
                        ExchangeTimeoutError(context={"operation": spec.route_id.value})
                        if isinstance(error, httpx.TimeoutException)
                        else ExchangeDownError(context={"operation": spec.route_id.value})
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
                    started_at=started_at,
                )

            if result is not None:
                return result
            if terminal_error is not None:
                raise terminal_error
            if retry_delay is None:
                raise ExchangeDownError(context={"operation": spec.route_id.value})
            await self._sleeper(retry_delay)

        raise ExchangeDownError(context={"operation": spec.route_id.value})

    async def aclose(self) -> None:
        """Close the owned HTTP client idempotently."""
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()


__all__: list[str] = []
