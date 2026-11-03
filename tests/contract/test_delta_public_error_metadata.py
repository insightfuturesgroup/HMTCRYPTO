"""Every terminal exchange error carries only validated correlation metadata."""

from __future__ import annotations

import json
from uuid import UUID

import httpx
import pytest
from delta_public_test_support import (
    fixture_json,
    json_response,
    new_client,
    public_api,
)

from hmt_crypto.errors import (
    AuthError,
    ExchangeDownError,
    ExchangeProtocolError,
    ExchangeTimeoutError,
    RateLimitError,
)

pytestmark = pytest.mark.contract


async def _ticker_call(client: object) -> object:
    return await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))


def _assert_correlation(
    error: Exception,
    *,
    request_id: str | None,
    status: int | None,
) -> None:
    context = error.context
    assert str(UUID(context["correlation_id"])) == context["correlation_id"]
    assert context["operation"] == "ticker"
    if request_id is None:
        assert "request_id" not in context
    else:
        assert context["request_id"] == request_id
    if status is None:
        assert "http_status" not in context
    else:
        assert context["http_status"] == status


@pytest.mark.asyncio
async def test_auth_error_retains_validated_local_and_exchange_correlation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            request,
            b'{"success":false,"error":{"code":"invalid_api_key"}}',
            status_code=401,
            headers={"X-Request-ID": "req-auth-1"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(AuthError) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    _assert_correlation(raised.value, request_id="req-auth-1", status=401)


@pytest.mark.asyncio
async def test_terminal_rate_limit_error_retains_correlation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            request,
            b'{"error":"rate_limited"}',
            status_code=429,
            headers={"X-Rate-Limit-Reset": "0", "X-Request-ID": "req-rate-1"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(RateLimitError) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    _assert_correlation(raised.value, request_id="req-rate-1", status=429)


@pytest.mark.asyncio
async def test_terminal_exchange_down_error_retains_correlation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            request,
            b'{"error":"unavailable"}',
            status_code=503,
            headers={"X-Request-ID": "req-down-1"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeDownError) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    _assert_correlation(raised.value, request_id="req-down-1", status=503)


@pytest.mark.asyncio
async def test_timeout_error_retains_local_correlation_without_response_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture-secret-timeout", request=request)

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeTimeoutError) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    _assert_correlation(raised.value, request_id=None, status=None)


@pytest.mark.asyncio
async def test_protocol_error_retains_correlation_without_leaking_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            request,
            b"fixture-secret-malformed-json",
            headers={"X-Request-ID": "req-protocol-1"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    _assert_correlation(raised.value, request_id="req-protocol-1", status=200)
    assert "fixture-secret-malformed-json" not in json.dumps(raised.value.to_dict())


@pytest.mark.asyncio
async def test_schema_error_retains_transport_correlation() -> None:
    payload = fixture_json("public_ticker.json")
    payload["result"]["product_id"] = "fixture-secret-invalid-id"

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            request,
            json.dumps(payload).encode(),
            headers={"X-Request-ID": "req-schema-1"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    _assert_correlation(raised.value, request_id="req-schema-1", status=200)
    assert "fixture-secret-invalid-id" not in json.dumps(raised.value.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [(b"x-request-id", b"fixture-secret request")],
        [(b"x-request-id", b"a" * 129)],
        [(b"x-request-id", b"req-one"), (b"x-request-id", b"req-two")],
        [(b"x-request-id", b"fixture-secret-\xff")],
    ],
)
async def test_invalid_or_ambiguous_exchange_correlation_is_omitted(
    headers: list[tuple[bytes, bytes]],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"not-json",
            headers=[(b"content-type", b"application/json"), *headers],
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    _assert_correlation(raised.value, request_id=None, status=200)
    rendered = str(raised.value) + repr(raised.value) + json.dumps(raised.value.to_dict())
    assert "fixture-secret" not in rendered
