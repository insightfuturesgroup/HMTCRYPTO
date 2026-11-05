"""Exchange-neutral request models and strict Delta public-route policy."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from delta_public_test_support import new_client, public_api
from pydantic import ValidationError

from hmt_crypto.errors import ConfigurationError

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(
    ("method", "path", "route_id"),
    [
        ("GET", "/v2/products", "products"),
        ("GET", "/v2/products/XBTUSD_TEST", "product"),
        ("GET", "/v2/tickers", "tickers"),
        ("GET", "/v2/tickers/XBTUSD_TEST,ETHUSD_TEST", "ticker"),
        ("GET", "/v2/l2orderbook/XBTUSD_TEST", "orderbook"),
        ("GET", "/v2/history/candles", "candles"),
    ],
)
def test_exact_public_routes_are_allowlisted(method: str, path: str, route_id: str) -> None:
    spec = public_api().routes.resolve_public_request(method, path)
    assert spec.route_id.value == route_id
    assert spec.method == "GET"
    assert spec.is_public is True
    assert spec.retry_safe is True
    assert spec.max_response_bytes > 0


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_every_non_get_method_is_denied(method: str) -> None:
    with pytest.raises(ConfigurationError):
        public_api().routes.resolve_public_request(method, "/v2/products")


@pytest.mark.parametrize(
    "path",
    [
        "/v2/wallet/balances",
        "/v2/wallet/transactions",
        "/v2/withdrawals",
        "/v2/deposit-address",
        "/v2/subaccounts/transfer",
        "/v2/funds/move",
        "/v2/orders",
        "/v2/products/1/orders/leverage",
        "/v2/unknown",
    ],
)
def test_private_write_fund_and_unknown_paths_are_denied(path: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        public_api().routes.resolve_public_request("GET", path)
    assert path not in str(raised.value)
    assert path not in repr(raised.value)


def test_all_private_profiles_and_fund_admin_routes_stay_outside_public_authority() -> None:
    from hmt_crypto.exchange.delta.private_routes import PRIVATE_ROUTE_SPECS

    excluded = tuple((spec.method, spec.path_template) for spec in PRIVATE_ROUTE_SPECS) + (
        ("POST", "/v2/wallets/sub_account_balance_transfer"),
        ("GET", "/v2/wallets/sub_accounts_transfer_history"),
        ("GET", "/v2/wallet/transactions/download"),
        ("GET", "/v2/fills/history/download/csv"),
        ("POST", "/v2/orders/batch"),
        ("POST", "/v2/positions/close_all"),
        ("GET", "/v2/rate_limits/quota"),
    )
    assert len(PRIVATE_ROUTE_SPECS) == 19
    for method, path in excluded:
        with pytest.raises(ConfigurationError):
            public_api().routes.resolve_public_request(method, path)


@pytest.mark.parametrize(
    "path",
    [
        "/v2/products/../orders",
        "/v2/products/XBT/USD",
        "/v2/tickers/XBTUSD?secret=fixture-secret",
        "/v2/tickers/XBTUSD#fragment",
        "/v2/tickers/XBTUSD%2Forders",
        "/v2/tickers/XBTUSD\\orders",
        "/v2/tickers/ XBTUSD",
        "/v2/tickers/XBTUSD\n",
        "/v2/tickers/",
    ],
)
def test_route_path_smuggling_is_rejected(path: str) -> None:
    with pytest.raises(ConfigurationError):
        public_api().routes.resolve_public_request("GET", path)


def test_route_registry_has_no_raw_dispatch_and_specs_are_immutable() -> None:
    routes = public_api().routes
    assert not hasattr(routes, "request")
    spec = routes.resolve_public_request("GET", "/v2/products")
    with pytest.raises((AttributeError, TypeError)):
        spec.method = "POST"


def test_product_query_is_frozen_ordered_and_does_not_mutate_inputs() -> None:
    base = public_api().base
    contract_types = ["perpetual_futures", "futures"]
    states = ["live", "upcoming"]
    query = base.ProductQuery(
        contract_types=contract_types,
        states=states,
        expiry="2030-01-02",
    )
    contract_types.append("call_options")
    states.clear()
    assert query.to_filters() == {
        "contract_types": "perpetual_futures,futures",
        "states": "live,upcoming",
        "expiry": "2030-01-02",
    }
    with pytest.raises(ValidationError):
        query.expiry = None


@pytest.mark.parametrize(
    "symbol",
    ["", " ", "XBT/USD", "XBT?USD", "XBT#USD", "XBT\\USD", "XBT\nUSD", "üSD"],
)
def test_product_reference_rejects_unsafe_symbols(symbol: str) -> None:
    with pytest.raises(ValidationError) as raised:
        public_api().base.ProductRef(symbol=symbol)
    rendered = str(raised.value)
    assert "input_value" not in rendered
    if len(symbol) > 1 and not symbol.isspace():
        assert symbol not in rendered


def test_candle_request_requires_utc_ordered_seconds_and_valid_enum() -> None:
    base = public_api().base
    request = base.CandleRequest(
        product=base.ProductRef(symbol="XBTUSD_TEST"),
        resolution=base.CandleResolution.ONE_MINUTE,
        start=datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC),
        end=datetime(2023, 11, 14, 22, 15, 20, tzinfo=UTC),
    )
    assert request.start_epoch_seconds == 1_700_000_000
    assert request.end_epoch_seconds == 1_700_000_120
    with pytest.raises(ValidationError):
        base.CandleRequest(
            product=base.ProductRef(symbol="XBTUSD_TEST"),
            resolution="10m",
            start=request.end,
            end=request.start,
        )


@pytest.mark.parametrize("depth", [0, -1, True, "10", 1001])
def test_orderbook_depth_is_strict_and_bounded(depth: object) -> None:
    base = public_api().base
    with pytest.raises(ValidationError):
        base.OrderBookRequest(product=base.ProductRef(symbol="XBTUSD_TEST"), depth=depth)


def test_market_data_protocol_exposes_no_private_or_raw_request_methods() -> None:
    protocol = public_api().base.MarketDataClient
    names = set(protocol.__annotations__) | set(protocol.__dict__)
    for forbidden in {
        "request",
        "send",
        "place_order",
        "cancel_order",
        "modify_order",
        "get_balance",
        "get_positions",
        "subscribe_private",
    }:
        assert forbidden not in names


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v2/orders", "/v2/wallet/transactions", "/v2/withdrawals"])
async def test_private_transport_revalidates_denied_paths_before_dispatch(path: str) -> None:
    called = False

    def handler(_request: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("transport must not be reached")

    api = public_api()
    client = new_client(handler)
    spec = api.routes.resolve_public_request("GET", "/v2/products")
    try:
        with pytest.raises(ConfigurationError):
            await client._transport._get_json(spec, path=path)
    finally:
        await client.aclose()
    assert called is False


@pytest.mark.asyncio
async def test_public_transport_rejects_every_private_template_before_dispatch() -> None:
    from hmt_crypto.exchange.delta.private_routes import PRIVATE_ROUTE_SPECS

    called = False

    def handler(_request: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("transport must not be reached")

    api = public_api()
    client = new_client(handler)
    public_spec = api.routes.resolve_public_request("GET", "/v2/products")
    try:
        for private_spec in PRIVATE_ROUTE_SPECS:
            with pytest.raises(ConfigurationError):
                await client._transport._get_json(
                    public_spec,
                    path=private_spec.path_template,
                )
    finally:
        await client.aclose()
    assert called is False
