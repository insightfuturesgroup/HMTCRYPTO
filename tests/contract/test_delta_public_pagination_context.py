"""Delta product pagination failures retain only the last safe page context."""

from __future__ import annotations

import json
from copy import deepcopy
from uuid import UUID

import httpx
import pytest
from delta_public_test_support import fixture_json, json_response, new_client

from hmt_crypto.errors import ExchangeProtocolError, PaginationLimitError

pytestmark = pytest.mark.contract


def _page(*, after: str | None, count: int = 1) -> dict[str, object]:
    payload = fixture_json("public_products_page1.json")
    original = payload["result"][0]
    products = []
    for index in range(count):
        product = deepcopy(original)
        product["id"] = 101 + index
        product["symbol"] = f"XBTUSD_TEST_{index}"
        products.append(product)
    payload["result"] = products
    payload["meta"] = {"after": after, "before": None}
    return payload


def _assert_last_page_context(error: Exception, *, request_id: str) -> None:
    context = error.context
    assert str(UUID(context["correlation_id"])) == context["correlation_id"]
    assert context == {
        "operation": "products",
        "correlation_id": context["correlation_id"],
        "request_id": request_id,
        "http_status": 200,
        "region": "india",
    }
    rendered = str(error) + repr(error) + json.dumps(error.to_dict())
    assert "fixture-secret" not in rendered
    assert "after" not in context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursors",
    [
        ["fixture-secret-repeat", "fixture-secret-repeat"],
        ["fixture-secret-a", "fixture-secret-b", "fixture-secret-a"],
    ],
)
async def test_product_cursor_cycles_use_last_page_correlation(
    cursors: list[str],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        cursor = cursors[calls]
        calls += 1
        return json_response(
            request,
            json.dumps(_page(after=cursor)).encode(),
            headers={"X-Request-ID": f"req-page-{calls}"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError) as raised:
            await client.get_products(page_size=1, max_pages=4, max_items=4)
    finally:
        await client.aclose()
    assert calls == len(cursors)
    _assert_last_page_context(raised.value, request_id=f"req-page-{calls}")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing_cursor", "oversized_page"])
async def test_product_page_shape_failures_use_current_page_correlation(failure: str) -> None:
    payload = _page(after=None, count=2 if failure == "oversized_page" else 1)
    if failure == "missing_cursor":
        payload.pop("meta")

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            request,
            json.dumps(payload).encode(),
            headers={"X-Request-ID": "req-shape-1"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError) as raised:
            await client.get_products(page_size=1, max_pages=2, max_items=3)
    finally:
        await client.aclose()
    _assert_last_page_context(raised.value, request_id="req-shape-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["max_pages", "max_items"])
async def test_product_pagination_limits_use_last_page_correlation(bound: str) -> None:
    payload = _page(
        after="fixture-secret-more" if bound == "max_pages" else None,
        count=1 if bound == "max_pages" else 2,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            request,
            json.dumps(payload).encode(),
            headers={"X-Request-ID": "req-limit-1"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(PaginationLimitError) as raised:
            await client.get_products(
                page_size=1 if bound == "max_pages" else 2,
                max_pages=1 if bound == "max_pages" else 2,
                max_items=10 if bound == "max_pages" else 1,
            )
    finally:
        await client.aclose()
    _assert_last_page_context(raised.value, request_id="req-limit-1")


@pytest.mark.asyncio
async def test_product_callback_schema_failure_keeps_its_response_correlation() -> None:
    payload = _page(after=None)
    payload["result"][0]["id"] = "fixture-secret-invalid-id"

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            request,
            json.dumps(payload).encode(),
            headers={"X-Request-ID": "req-schema-page"},
        )

    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError) as raised:
            await client.get_products(page_size=1, max_pages=2, max_items=2)
    finally:
        await client.aclose()
    _assert_last_page_context(raised.value, request_id="req-schema-page")
