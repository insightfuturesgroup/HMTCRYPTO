"""Fail-closed public schema, pagination, and constructor edge contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from delta_public_test_support import fixture_json, json_response, new_client, public_api

from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError, PaginationLimitError
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints
from hmt_crypto.exchange.delta.rate_limiter import RateLimitScope, ScopeKind, WeightedRateLimiter

pytestmark = pytest.mark.contract


@pytest.mark.asyncio
async def test_nonempty_products_without_cursor_evidence_fail_closed() -> None:
    payload = fixture_json("public_products_page1.json")
    payload.pop("meta")

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await client.get_products(page_size=1, max_pages=2, max_items=2)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_repeated_product_cursor_is_rejected_without_cursor_leak() -> None:
    payload = fixture_json("public_products_page1.json")
    payload["meta"]["after"] = "fixture-secret-repeated-cursor"

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError) as raised:
            await client.get_products(page_size=1, max_pages=3, max_items=3)
    finally:
        await client.aclose()
    assert "fixture-secret-repeated-cursor" not in str(raised.value)
    assert "fixture-secret-repeated-cursor" not in json.dumps(raised.value.to_dict())


@pytest.mark.asyncio
async def test_product_local_bounds_never_return_incomplete_as_complete() -> None:
    payload = fixture_json("public_products_page1.json")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return json_response(request, json.dumps(payload).encode())

    client = new_client(handler)
    try:
        with pytest.raises(PaginationLimitError):
            await client.get_products(page_size=1, max_pages=1, max_items=10)
    finally:
        await client.aclose()
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("result", 0), None),
        (("result", 0, "contract_value"), "fixture-secret-invalid-decimal"),
        (("result", 0, "id"), True),
        (("result", 0, "underlying_asset", "id"), 0),
        (("result", 0, "product_specs"), "not-an-object"),
    ],
)
async def test_product_required_field_drift_is_typed_and_safe(
    path: tuple[object, ...], value: object
) -> None:
    payload = fixture_json("public_products_page1.json")
    if value is None:
        payload["result"][0].pop("symbol")
    else:
        target: object = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError) as raised:
            await client.get_products(page_size=1, max_pages=2, max_items=2)
    finally:
        await client.aclose()
    rendered = str(raised.value) + repr(raised.value) + json.dumps(raised.value.to_dict())
    assert "fixture-secret-invalid-decimal" not in rendered


@pytest.mark.asyncio
async def test_ticker_requested_symbol_and_returned_symbol_must_match() -> None:
    payload = fixture_json("public_ticker.json")
    payload["result"]["symbol"] = "OTHER_TEST"

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [1700000000, 1700000000123, -1, True, "1700000000"])
async def test_l2_inferred_microseconds_rejects_non_microsecond_shape(timestamp: object) -> None:
    payload = fixture_json("public_orderbook.json")
    payload["result"]["last_updated_at"] = timestamp

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    api = public_api().base
    client = new_client(handler, l2_microseconds=True)
    try:
        with pytest.raises(ExchangeProtocolError):
            await client.get_order_book(
                api.OrderBookRequest(product=api.ProductRef(symbol="XBTUSD_TEST"))
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_l2_response_cannot_exceed_requested_depth() -> None:
    payload = fixture_json("public_orderbook.json")
    payload["result"]["buy"].append({"depth": "12", "price": "49999", "size": 2})

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    api = public_api().base
    client = new_client(handler, l2_microseconds=True)
    try:
        with pytest.raises(ExchangeProtocolError):
            await client.get_order_book(
                api.OrderBookRequest(product=api.ProductRef(symbol="XBTUSD_TEST"), depth=2)
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_candle_requested_symbol_is_not_taken_from_response_payload() -> None:
    api = public_api().base
    request = api.CandleRequest(
        product=api.ProductRef(symbol="XBTUSD_TEST"),
        resolution=api.CandleResolution.ONE_MINUTE,
        start=datetime.fromtimestamp(1_700_000_000, UTC),
        end=datetime.fromtimestamp(1_700_000_120, UTC),
    )
    payload = {
        "success": True,
        "result": [
            {
                "time": 1_700_000_000,
                "open": "10",
                "high": "9",
                "low": "8",
                "close": "8.5",
                "volume": "1",
            }
        ],
    }

    def handler(http_request: httpx.Request) -> httpx.Response:
        return json_response(http_request, json.dumps(payload).encode())

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await client.get_candles(request)
    finally:
        await client.aclose()


def test_client_rejects_limiter_scope_mismatch_and_invalid_user_agent() -> None:
    api = public_api().client
    endpoints = get_delta_endpoints("india", "testnet")
    wrong_scope = RateLimitScope(
        region="global",
        environment="testnet",
        kind=ScopeKind.IP,
        external_id="mismatched-egress",
    )
    limiter = WeightedRateLimiter(wrong_scope)
    transport = httpx.MockTransport(lambda request: pytest.fail("transport called"))
    with pytest.raises(ConfigurationError):
        api.DeltaPublicClient(
            endpoints=endpoints,
            limiter=limiter,
            user_agent="valid-agent/1",
            transport=transport,
        )
    right_scope = RateLimitScope(
        region="india",
        environment="testnet",
        kind=ScopeKind.IP,
        external_id="right-egress",
    )
    for user_agent in ["", " ", "line\nbreak", 1]:
        with pytest.raises(ConfigurationError):
            api.DeltaPublicClient(
                endpoints=endpoints,
                limiter=WeightedRateLimiter(right_scope),
                user_agent=user_agent,
                transport=transport,
            )
