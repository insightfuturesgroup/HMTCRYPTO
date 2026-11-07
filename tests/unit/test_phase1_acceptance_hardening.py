"""Final Phase 1 safety boundaries retained as focused regressions."""

from __future__ import annotations

import asyncio
import copy
import pickle
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from product_catalog_test_support import FetchQueue, new_catalog
from pydantic import SecretStr, ValidationError

from hmt_crypto import errors
from hmt_crypto.config import Settings
from hmt_crypto.domain import (
    Asset,
    ContractType,
    NotionalType,
    Product,
    ProductState,
    TradingStatus,
)
from hmt_crypto.errors import ConfigurationError, ExchangeDownError, HmtCryptoError
from hmt_crypto.exchange.base import CandleResult, ProductRef
from hmt_crypto.exchange.delta.client import DeltaPublicClient
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints
from hmt_crypto.exchange.delta.rate_limiter import RateLimitScope, ScopeKind, WeightedRateLimiter
from hmt_crypto.exchange.delta.signer import DeltaSigner
from hmt_crypto.exchange.delta.sizing_math import NumericContractError, positive_decimal
from hmt_crypto.exchange.delta.wire_schemas import parse_product

pytestmark = pytest.mark.unit


def _scope() -> RateLimitScope:
    return RateLimitScope(
        region="india",
        environment="testnet",
        kind=ScopeKind.IP,
        external_id="phase1-hardening-egress",
    )


def _client(user_agent: object, transport: httpx.AsyncBaseTransport) -> DeltaPublicClient:
    return DeltaPublicClient(
        endpoints=get_delta_endpoints("india", "testnet"),
        limiter=WeightedRateLimiter(_scope()),
        user_agent=user_agent,  # type: ignore[arg-type]
        transport=transport,
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        " leading",
        "trailing ",
        "agent\x1f",
        "agent\x7f",
        "agent-₹",
        "a" * 257,
        7,
        True,
    ],
)
def test_signer_and_public_client_share_bounded_visible_ascii_user_agent(value: object) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    transport = httpx.MockTransport(handler)
    with pytest.raises(ConfigurationError) as signer_error:
        DeltaSigner(
            api_key=SecretStr("fixture-key"),
            api_secret=SecretStr("fixture-secret"),
            user_agent=value,  # type: ignore[arg-type]
            clock=lambda: 1_700_000_000,
        )
    with pytest.raises(ConfigurationError) as client_error:
        _client(value, transport)

    rendered = repr(signer_error.value.to_dict()) + repr(client_error.value.to_dict())
    assert "agent-₹" not in rendered
    assert calls == 0


@pytest.mark.asyncio
async def test_user_agent_accepts_the_documented_256_visible_ascii_boundary() -> None:
    value = "a" * 256
    signer = DeltaSigner(
        api_key=SecretStr("fixture-key"),
        api_secret=SecretStr("fixture-secret"),
        user_agent=value,
        clock=lambda: 1_700_000_000,
    )
    client = _client(
        value, httpx.MockTransport(lambda request: httpx.Response(500, request=request))
    )

    assert signer.sign_at("GET", 1_700_000_000, "/v2/profile").as_headers()["User-Agent"] == value
    await client.aclose()


def _product_payload(**changes: object) -> dict[str, object]:
    asset = Asset(id=1, symbol="BTC", precision=8, minimum_precision=2)
    usd = Asset(id=2, symbol="USD", precision=8, minimum_precision=2)
    values: dict[str, object] = {
        "id": 27,
        "symbol": "BTCUSD",
        "contract_type": ContractType.PERPETUAL_FUTURES,
        "notional_type": NotionalType.VANILLA,
        "contract_value": "0.001",
        "contract_unit_currency": "BTC",
        "tick_size": "0.5",
        "state": ProductState.LIVE,
        "trading_status": TradingStatus.OPERATIONAL,
        "underlying_asset": asset,
        "quoting_asset": usd,
        "settling_asset": usd,
        "is_quanto": False,
        "only_reduce_only_orders_allowed": False,
        "position_size_limit": "0",
        "position_notional_limit": "0",
        "initial_margin": "0",
        "maintenance_margin": "0",
        "default_leverage": "1",
        "max_leverage_notional": "0",
    }
    values.update(changes)
    return values


@pytest.mark.asyncio
async def test_optional_zero_product_metadata_is_preserved_through_catalogue() -> None:
    from product_catalog_test_support import catalog_source

    from hmt_crypto.exchange.delta.catalog import CatalogProductLoad
    from hmt_crypto.exchange.delta.paginator import PaginationResult

    product = Product(**_product_payload())
    optional = (
        product.position_size_limit,
        product.position_notional_limit,
        product.initial_margin,
        product.maintenance_margin,
        product.max_leverage_notional,
    )
    assert optional == (Decimal("0"),) * 5
    load = CatalogProductLoad(
        source=catalog_source(),
        pagination=PaginationResult(
            items=(product,),
            page_count=1,
            complete=True,
            truncated=False,
            terminal_after=None,
        ),
    )
    catalog = new_catalog(FetchQueue(load))
    await catalog.refresh()
    published = catalog.require_snapshot(at=datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    assert published.products[0].model_dump(mode="json")["position_size_limit"] == "0"
    assert len(published.content_sha256) == 64


@pytest.mark.asyncio
async def test_spot_without_settling_asset_publishes_without_inventing_one() -> None:
    from product_catalog_test_support import complete_load

    spot = Product(
        **_product_payload(
            contract_type=ContractType.SPOT,
            settling_asset=None,
        )
    )
    catalog = new_catalog(FetchQueue(complete_load((spot,))))

    await catalog.refresh()
    published = catalog.require_snapshot(at=datetime(2026, 11, 3, 10, 0, tzinfo=UTC))

    assert published.products[0].settling_asset is None
    assert published.assets_by_id == {1: spot.underlying_asset, 2: spot.quoting_asset}


@pytest.mark.parametrize(
    "name",
    [
        "position_size_limit",
        "position_notional_limit",
        "initial_margin",
        "maintenance_margin",
        "max_leverage_notional",
    ],
)
def test_optional_product_metadata_rejects_negative_values(name: str) -> None:
    with pytest.raises(ValidationError):
        Product(**_product_payload(**{name: "-0.0001"}))


def test_default_leverage_remains_strictly_positive() -> None:
    with pytest.raises(ValidationError):
        Product(**_product_payload(default_leverage="0"))


def test_wire_product_parser_preserves_zero_optional_metadata() -> None:
    raw: dict[str, object] = {
        "id": 27,
        "symbol": "BTCUSD",
        "contract_type": "perpetual_futures",
        "notional_type": "vanilla",
        "contract_value": "0.001",
        "contract_unit_currency": "BTC",
        "tick_size": "0.5",
        "state": "live",
        "trading_status": "operational",
        "underlying_asset": {"id": 1, "symbol": "BTC", "precision": 8, "minimum_precision": 2},
        "quoting_asset": {"id": 2, "symbol": "USD", "precision": 8, "minimum_precision": 2},
        "settling_asset": {"id": 2, "symbol": "USD", "precision": 8, "minimum_precision": 2},
        "is_quanto": False,
        "product_specs": {"only_reduce_only_orders_allowed": False},
        "position_size_limit": "0",
        "position_notional_limit": "0",
        "initial_margin": "0",
        "maintenance_margin": "0",
        "default_leverage": "1",
        "max_leverage_notional": "0",
    }
    parsed = parse_product(raw, endpoint_id="zero-metadata")
    assert parsed.position_size_limit == Decimal("0")
    assert parsed.max_leverage_notional == Decimal("0")


class _FailOnceCloseTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    async def aclose(self) -> None:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await self.release.wait()
            raise RuntimeError("RAW-CLOSE-MARKER")


@pytest.mark.asyncio
async def test_close_failure_is_typed_shared_and_retryable_without_reopening_requests() -> None:
    transport = _FailOnceCloseTransport()
    client = _client("hmt-crypto-hardening/1.0", transport)
    first = asyncio.create_task(client.aclose())
    await transport.entered.wait()
    second = asyncio.create_task(client.aclose())
    transport.release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)

    assert transport.calls == 1
    assert all(isinstance(outcome, ExchangeDownError) for outcome in outcomes)
    assert "RAW-CLOSE-MARKER" not in repr([str(item) for item in outcomes])
    with pytest.raises(ConfigurationError):
        await client.get_ticker(ProductRef(symbol="BTCUSD"))

    await client.aclose()
    await client.aclose()
    assert transport.calls == 2


_ERROR_TYPES = tuple(
    value
    for name in errors.__all__
    if isinstance((value := getattr(errors, name)), type) and issubclass(value, HmtCryptoError)
)


@pytest.mark.parametrize("error_type", _ERROR_TYPES, ids=lambda value: value.__name__)
def test_safe_errors_support_copy_deepcopy_and_pickle(error_type: type[HmtCryptoError]) -> None:
    original = error_type(
        context={"operation": "copy_test", "symbol": "BTCUSD", "api_key": "never-retain"},
        retry_after_ms=123,
    )
    reconstructed = (
        copy.copy(original),
        copy.deepcopy(original),
        pickle.loads(pickle.dumps(original)),
    )

    for item in reconstructed:
        assert type(item) is error_type
        assert item.to_dict() == original.to_dict()
        assert item.context is not original.context
        assert "never-retain" not in repr(item.to_dict())


@pytest.mark.parametrize(
    "field",
    [
        "DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE",
        "DELTA_LIVE_TRADING_ENABLED",
        "PRODUCTION_WRITES_FORBIDDEN",
    ],
)
@pytest.mark.parametrize("value", [1, "yes", "1"])
def test_safety_boolean_constructor_values_are_strict(field: str, value: object) -> None:
    supplied: dict[str, object] = {field: value}
    if field == "DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE":
        supplied["DELTA_DEVELOPMENT_REST_BASE_URL"] = "http://127.0.0.1:9010"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **supplied)


@pytest.mark.parametrize("value", [1, "yes", "true"])
def test_candle_result_truncation_flag_is_strict_and_evidence_backed(value: object) -> None:
    with pytest.raises(ValidationError):
        CandleResult(items=(), possibly_truncated=value)


class _HostileCodeError(HmtCryptoError):
    def __init__(self) -> None:
        self._armed = False
        super().__init__()
        self._armed = True

    @property
    def code(self) -> str:
        if self._armed:
            raise RuntimeError("RAW-CODE-MARKER")
        return "hostile_code"


@pytest.mark.asyncio
async def test_catalogue_normalizes_unknown_error_codes_without_calling_hostile_property() -> None:
    catalog = new_catalog(FetchQueue(_HostileCodeError()))
    result = await catalog.refresh()

    assert result.safe_error_code == "catalog_refresh_failed"
    assert "RAW-CODE-MARKER" not in repr(result.to_dict())


def test_equivalent_decimal_encodings_share_the_same_exponent_resource_bound() -> None:
    with pytest.raises(NumericContractError):
        positive_decimal(Decimal("1E+129"))
    with pytest.raises(NumericContractError):
        positive_decimal(Decimal("10E+128"))
