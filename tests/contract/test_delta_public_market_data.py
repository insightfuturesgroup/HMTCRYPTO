"""Sanitized public product, ticker, orderbook, and candle contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from delta_public_test_support import (
    fixture_bytes,
    fixture_json,
    json_response,
    new_client,
    public_api,
)

from hmt_crypto.domain import ContractType, NotionalType, ProductState, TradingStatus
from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError

pytestmark = pytest.mark.contract


@pytest.mark.asyncio
async def test_products_follow_cursor_with_exact_filters_and_complete_evidence() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = (
            fixture_bytes("public_products_page2.json")
            if request.url.params.get("after")
            else fixture_bytes("public_products_page1.json")
        )
        return json_response(request, body)

    query_input = ["perpetual_futures"]
    query = public_api().base.ProductQuery(contract_types=query_input, states=["live"])
    client = new_client(handler)
    try:
        result = await client.get_products(query=query, page_size=1, max_pages=3, max_items=3)
    finally:
        await client.aclose()

    query_input.append("futures")
    assert result.complete is True
    assert result.truncated is False
    assert result.page_count == 2
    assert [product.id for product in result.items] == [101, 202]
    first, second = result.items
    assert first.contract_value == Decimal("0.001")
    assert first.tick_size == Decimal("0.5")
    assert first.contract_type is ContractType.PERPETUAL_FUTURES
    assert first.notional_type is NotionalType.VANILLA
    assert first.underlying_asset.id == 1
    assert first.quoting_asset.id == first.settling_asset.id == 2
    assert first.position_size_limit == Decimal("5000")
    assert first.position_notional_limit == Decimal("250000")
    assert first.default_leverage == Decimal("50")
    assert first.max_leverage_notional == Decimal("100000")
    assert second.contract_type is ContractType.UNKNOWN
    assert second.notional_type is NotionalType.UNKNOWN
    assert second.state is ProductState.UPCOMING
    assert second.trading_status is TradingStatus.DISRUPTED_CANCEL_ONLY
    assert second.only_reduce_only_orders_allowed is True
    assert "ignored_future_field" not in second.model_dump()
    assert len(requests) == 2
    assert requests[0].method == "GET"
    assert requests[0].url.host == "cdn-ind.testnet.deltaex.org"
    assert requests[0].url.path == "/v2/products"
    assert list(requests[0].url.params.multi_items()) == [
        ("contract_types", "perpetual_futures"),
        ("states", "live"),
        ("page_size", "1"),
    ]
    assert list(requests[1].url.params.multi_items()) == [
        ("contract_types", "perpetual_futures"),
        ("states", "live"),
        ("page_size", "1"),
        ("after", "synthetic-next"),
    ]
    assert requests[0].headers["User-Agent"] == "hmt-crypto-contract-tests/1.0"
    assert requests[0].headers["Accept"] == "application/json"
    assert query.to_filters() == {
        "contract_types": "perpetual_futures",
        "states": "live",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("region", "environment", "host"),
    [
        ("india", "prod", "api.india.delta.exchange"),
        ("india", "testnet", "cdn-ind.testnet.deltaex.org"),
        ("global", "prod", "api.delta.exchange"),
        ("global", "testnet", "testnet-api.delta.exchange"),
    ],
)
async def test_product_lookup_uses_exact_selected_host(
    region: str, environment: str, host: str
) -> None:
    payload = fixture_json("public_products_page1.json")
    payload.pop("meta")
    payload["result"] = payload["result"][0]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(request, json.dumps(payload).encode())

    client = new_client(handler, region=region, environment=environment)
    try:
        product = await client.get_product(public_api().base.ProductRef(symbol="XBTUSD_TEST"))
    finally:
        await client.aclose()
    assert product.id == 101
    assert seen[0].url.host == host
    assert seen[0].url.path == "/v2/products/XBTUSD_TEST"


@pytest.mark.asyncio
async def test_ticker_parses_json_number_directly_to_decimal_and_epoch_seconds() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(request, fixture_bytes("public_ticker.json"))

    client = new_client(handler)
    try:
        ticker = await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))
    finally:
        await client.aclose()
    assert ticker.product_id == 101
    assert ticker.timestamp == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
    assert ticker.close == Decimal("49999.875")
    assert ticker.mark_price == Decimal("50000.125")
    assert ticker.best_bid == Decimal("50000.0")
    assert ticker.best_ask == Decimal("50000.5")
    assert ticker.contract_type is ContractType.PERPETUAL_FUTURES
    # REST ticker does not carry trading status; catalogue Product is authoritative.
    assert ticker.product_trading_status is TradingStatus.UNKNOWN
    assert seen[0].url.path == "/v2/tickers/XBTUSD_TEST"


@pytest.mark.asyncio
async def test_undocumented_ticker_trading_status_is_ignored_not_inferred() -> None:
    payload = fixture_json("public_ticker.json")
    payload["result"]["product_trading_status"] = "operational"

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    client = new_client(handler)
    try:
        ticker = await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))
    finally:
        await client.aclose()
    assert ticker.product_trading_status is TradingStatus.UNKNOWN


@pytest.mark.asyncio
async def test_multi_ticker_path_accepts_at_most_ten_and_preserves_order() -> None:
    payload = fixture_json("public_ticker.json")
    second = dict(payload["result"])
    second.update({"product_id": 202, "symbol": "ETHUSD_TEST"})
    # Swagger declares a single result for this multi-symbol route, while the
    # prose permits ten. A verified list is normalized to caller order.
    payload["result"] = [second, payload["result"]]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(request, json.dumps(payload).encode())

    api = public_api().base
    client = new_client(handler)
    try:
        tickers = await client.get_tickers(
            (api.ProductRef(symbol="XBTUSD_TEST"), api.ProductRef(symbol="ETHUSD_TEST"))
        )
        with pytest.raises(ConfigurationError):
            await client.get_tickers(tuple(api.ProductRef(symbol=f"SYNTH{i}") for i in range(11)))
    finally:
        await client.aclose()
    assert [ticker.symbol for ticker in tickers] == ["XBTUSD_TEST", "ETHUSD_TEST"]
    assert len(seen) == 1
    assert seen[0].url.path == "/v2/tickers/XBTUSD_TEST,ETHUSD_TEST"


@pytest.mark.asyncio
async def test_all_tickers_uses_collection_route_and_requires_list_shape() -> None:
    payload = fixture_json("public_ticker.json")
    payload["result"] = [payload["result"]]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(request, json.dumps(payload).encode())

    client = new_client(handler)
    try:
        tickers = await client.get_tickers()
    finally:
        await client.aclose()
    assert [ticker.symbol for ticker in tickers] == ["XBTUSD_TEST"]
    assert seen[0].url.path == "/v2/tickers"


@pytest.mark.asyncio
async def test_l2_requires_explicit_inferred_microsecond_policy() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return json_response(request, fixture_bytes("public_orderbook.json"))

    request = public_api().base.OrderBookRequest(
        product=public_api().base.ProductRef(symbol="XBTUSD_TEST"), depth=2
    )
    client = new_client(handler)
    try:
        with pytest.raises(ConfigurationError):
            await client.get_order_book(request)
    finally:
        await client.aclose()
    assert called is False


@pytest.mark.asyncio
async def test_l2_explicit_microsecond_policy_normalizes_and_validates_book() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(request, fixture_bytes("public_orderbook.json"))

    api = public_api().base
    client = new_client(handler, l2_microseconds=True)
    try:
        book = await client.get_order_book(
            api.OrderBookRequest(product=api.ProductRef(symbol="XBTUSD_TEST"), depth=2)
        )
    finally:
        await client.aclose()
    assert book.timestamp == datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=UTC)
    assert [level.price for level in book.bids] == [Decimal("50000.0"), Decimal("49999.5")]
    assert [level.price for level in book.asks] == [Decimal("50000.5"), Decimal("50001.0")]
    assert [level.size for level in book.bids] == [3, 5]
    assert list(seen[0].url.params.multi_items()) == [("depth", "2")]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["duplicate", "negative_size"])
async def test_l2_rejects_structurally_unsafe_books(mutation: str) -> None:
    payload = fixture_json("public_orderbook.json")
    result = payload["result"]
    if mutation == "duplicate":
        result["sell"][1]["price"] = result["sell"][0]["price"]
    else:
        result["buy"][0]["size"] = -1

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
async def test_l2_normalizes_unsorted_levels_and_allows_auction_overlap() -> None:
    payload = fixture_json("public_orderbook.json")
    payload["result"]["buy"].reverse()
    payload["result"]["sell"].reverse()
    payload["result"]["sell"][1]["price"] = "49999"

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    api = public_api().base
    client = new_client(handler, l2_microseconds=True)
    try:
        book = await client.get_order_book(
            api.OrderBookRequest(product=api.ProductRef(symbol="XBTUSD_TEST"))
        )
    finally:
        await client.aclose()
    assert [level.price for level in book.bids] == sorted(
        (level.price for level in book.bids), reverse=True
    )
    assert [level.price for level in book.asks] == sorted(level.price for level in book.asks)
    assert book.best_bid is not None and book.best_ask is not None
    assert book.best_bid > book.best_ask
