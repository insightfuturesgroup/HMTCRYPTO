"""Hostile numeric text cannot escape safe typed protocol boundaries."""

import json
from decimal import Decimal

import httpx
import pytest
from delta_public_test_support import RecordingSleeper, json_response, new_client
from structlog.testing import capture_logs

from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError
from hmt_crypto.exchange.delta.rate_limit_policy import parse_rate_limit_reset_ms
from hmt_crypto.exchange.delta.response import read_json_body
from hmt_crypto.exchange.delta.routes import resolve_public_request
from hmt_crypto.exchange.delta.wire_json import decode_json_object

pytestmark = pytest.mark.contract


@pytest.mark.parametrize("token", ["1e" + "9" * 100, "9" * 5000, "0." + "1" * 1024])
def test_json_numeric_tokens_are_bounded_with_safe_context(token: str) -> None:
    with pytest.raises(ExchangeProtocolError) as raised:
        decode_json_object(
            ('{"result":' + token + ',"secret":"numeric-marker"}').encode(),
            endpoint_id="tickers",
            context={"correlation_id": "numeric-correlation", "raw_body": "numeric-marker"},
        )
    assert raised.value.context == {"operation": "tickers", "correlation_id": "numeric-correlation"}
    assert token not in repr(raised.value.to_dict())
    assert "numeric-marker" not in str(raised.value)


@pytest.mark.parametrize("token", ["9" * 1024, "0." + "1" * 1022, "1e999999"])
def test_bounded_json_numbers_preserve_exact_values(token: str) -> None:
    parsed = decode_json_object(('{"result":' + token + "}").encode(), endpoint_id="tickers")
    assert parsed["result"] == (Decimal(token) if "." in token or "e" in token else int(token))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["decimal", "length", "reset"])
async def test_hostile_wire_numbers_fail_as_protocol_without_retry(
    failure: str,
) -> None:
    calls = 0
    sleeper = RecordingSleeper()
    token = "9" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        headers = {"X-Request-ID": "numeric-request"}
        body = b'{"success":true,"result":[],"secret":"numeric-marker"}'
        status = 200
        if failure == "decimal":
            body = ('{"success":true,"result":[],"ignored":1e' + token + "}").encode()
        elif failure == "length":
            headers["Content-Length"] = token
        else:
            headers["X-RATE-LIMIT-RESET"] = token
            status = 429
        return json_response(request, body, status_code=status, headers=headers)

    with capture_logs() as events:
        async with new_client(handler, sleeper=sleeper) as client:
            with pytest.raises(ExchangeProtocolError) as raised:
                await client.get_tickers()
    assert calls == 1
    assert sleeper.delays == []
    context = raised.value.context
    assert set(context) == {"operation", "http_status", "correlation_id", "request_id"}
    assert context["request_id"] == "numeric-request"
    assert context["http_status"] == (429 if failure == "reset" else 200)
    assert context["correlation_id"]
    assert len(events) == 1
    assert events[0]["exchange_request_id"] == "numeric-request"
    rendered = repr(raised.value.to_dict()) + json.dumps(events)
    assert token not in rendered
    assert "numeric-marker" not in rendered


@pytest.mark.parametrize("value", ["9" * 5000, "0" * 5000, "0000000"])
def test_reset_header_has_bounded_integer_conversion(value: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        parse_rate_limit_reset_ms(value)
    assert raised.value.context == {"operation": "rate_limit_reset"}
    assert value not in str(raised.value)


@pytest.mark.parametrize(("value", "expected"), [("0", 0), ("000000", 0), ("300000", 300000)])
def test_reset_header_boundary_values(value: str, expected: int) -> None:
    assert parse_rate_limit_reset_ms(value) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [0, 1])
async def test_content_length_exact_body_ceiling(extra: int) -> None:
    spec = resolve_public_request("GET", "/v2/tickers")
    body = b" " * (spec.max_response_bytes + extra)
    response = httpx.Response(200, content=body, headers={"Content-Type": "application/json"})
    if extra:
        with pytest.raises(ExchangeProtocolError):
            await read_json_body(response, spec=spec, context={"correlation_id": "length-bound"})
    else:
        assert await read_json_body(response, spec=spec, context={}) == body


@pytest.mark.asyncio
async def test_overlong_leading_zero_content_length_is_rejected() -> None:
    body = json.dumps({"success": True, "result": []}).encode()
    response = httpx.Response(
        200,
        content=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": "0" * 5000 + str(len(body)),
        },
    )
    with pytest.raises(ExchangeProtocolError):
        await read_json_body(
            response, spec=resolve_public_request("GET", "/v2/tickers"), context={}
        )
