"""Bounded transport, retry, lifecycle, error, and redaction contracts."""

from __future__ import annotations

import json

import httpx
import pytest
from delta_public_test_support import (
    RecordingSleeper,
    fixture_bytes,
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
    SignatureExpiredError,
    UnauthorizedApiAccessError,
)

pytestmark = pytest.mark.contract


async def _ticker_call(client: object) -> object:
    return await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload_name", "error_type"),
    [
        ("public_error_nested.json", UnauthorizedApiAccessError),
        ("public_error_flat.json", SignatureExpiredError),
    ],
)
async def test_both_error_envelopes_map_without_raw_message_leak(
    payload_name: str, error_type: type[Exception]
) -> None:
    raw = fixture_bytes(payload_name)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, raw, status_code=400)

    client = new_client(handler)
    try:
        with pytest.raises(error_type) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    rendered = json.dumps(raised.value.to_dict()) + str(raised.value) + repr(raised.value)
    assert "fixture-secret-error-body" not in rendered
    assert "fixture-secret-exchange-message" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        ("invalid_api_key", AuthError),
        ("InvalidApiKey", AuthError),
        ("Signature Mismatch", AuthError),
        ("SignatureExpired", SignatureExpiredError),
        ("UnauthorizedApiAccess", UnauthorizedApiAccessError),
    ],
)
async def test_error_code_variants_are_normalized(code: str, error_type: type[Exception]) -> None:
    body = json.dumps({"success": False, "error": {"code": code, "context": {}}}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, body, status_code=401)

    client = new_client(handler)
    try:
        with pytest.raises(error_type):
            await _ticker_call(client)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_timeout_retries_safe_read_then_raises_typed_timeout() -> None:
    calls = 0
    sleeper = RecordingSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("fixture-secret-timeout", request=request)

    client = new_client(handler, sleeper=sleeper)
    try:
        with pytest.raises(ExchangeTimeoutError) as raised:
            await _ticker_call(client)
        limiter_snapshot = await client._transport._limiter.snapshot()
    finally:
        await client.aclose()
    assert calls == 3
    assert sleeper.delays == [0.001, 0.002]
    assert limiter_snapshot.rest_used == 9
    assert "fixture-secret-timeout" not in str(raised.value)


@pytest.mark.asyncio
async def test_5xx_retries_safe_read_then_raises_exchange_down() -> None:
    calls = 0
    sleeper = RecordingSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return json_response(request, b'{"error":"temporary"}', status_code=503)

    client = new_client(handler, sleeper=sleeper)
    try:
        with pytest.raises(ExchangeDownError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert calls == 3
    assert sleeper.delays == [0.001, 0.002]


@pytest.mark.asyncio
async def test_malformed_5xx_json_is_protocol_error_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return json_response(request, b"not-json", status_code=503)

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert calls == 1


@pytest.mark.asyncio
async def test_429_observes_strict_millisecond_reset_and_retries() -> None:
    calls = 0
    sleeper = RecordingSleeper()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return json_response(
                request,
                b'{"error":"rate_limited"}',
                status_code=429,
                headers={"X-RATE-LIMIT-RESET": "1"},
            )
        return json_response(request, fixture_bytes("public_ticker.json"))

    client = new_client(handler, sleeper=sleeper)
    try:
        ticker = await _ticker_call(client)
    finally:
        await client.aclose()
    assert ticker.product_id == 101
    assert calls == 2
    assert sleeper.delays == [0.001]


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["-1", "1.5", " 1", "300001", "١"])
async def test_malformed_reset_header_fails_without_retry(header: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if not header.isascii():
            return httpx.Response(
                429,
                content=b'{"error":"rate_limited"}',
                headers=[
                    (b"content-type", b"application/json"),
                    (b"x-rate-limit-reset", header.encode("utf-8")),
                ],
                request=request,
            )
        return json_response(
            request,
            b'{"error":"rate_limited"}',
            status_code=429,
            headers={"X-RATE-LIMIT-RESET": header},
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert calls == 1


@pytest.mark.asyncio
async def test_terminal_429_maps_rate_limit_with_retry_metadata() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return json_response(
            request,
            b'{"error":"rate_limited"}',
            status_code=429,
            headers={"X-RATE-LIMIT-RESET": "1"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(RateLimitError) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert calls == 3
    assert raised.value.retry_after_ms == 1


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_is_protocol_error() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://evil.invalid/fixture-secret"},
            request=request,
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert len(seen) == 1
    assert "evil.invalid" not in seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Content-Type": "text/html"},
        {"Content-Type": "application/json, text/plain"},
    ],
)
async def test_missing_wrong_or_ambiguous_content_type_is_rejected(headers: dict[str, str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=fixture_bytes("public_ticker.json"), headers=headers)

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not-json fixture-secret-body",
        b'{"success":true,"success":false,"result":{}}',
        b'{"success":true,"result":{"mark_price":NaN}}',
        b"[]",
        b'{"success":true}',
    ],
)
async def test_malformed_duplicate_nonfinite_or_invalid_envelope_is_safe(body: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, body)

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError) as raised:
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert body.decode(errors="ignore") not in str(raised.value)
    assert "fixture-secret-body" not in json.dumps(raised.value.to_dict())
