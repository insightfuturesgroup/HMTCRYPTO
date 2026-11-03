"""Immutable product-catalogue snapshots, mappings, and validation."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from types import MappingProxyType

import pytest
from product_catalog_test_support import (
    BASE_TIME,
    FetchQueue,
    MutableClock,
    catalog_api,
    complete_result,
    load_products,
    new_catalog,
)

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import ContractType, NotionalType, ProductState, TradingStatus
from hmt_crypto.errors import ExchangeProtocolError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_complete_load_publishes_exact_immutable_snapshot() -> None:
    api = catalog_api()
    products = load_products()
    clock = MutableClock()
    catalog = new_catalog(FetchQueue(complete_result(products, page_count=2)), clock=clock)

    result = await catalog.refresh()
    snapshot = catalog.peek()

    assert result.status is api.CatalogRefreshStatus.PUBLISHED_CHANGED
    assert snapshot is not None
    assert snapshot.source.region is DeltaRegion.INDIA
    assert snapshot.source.environment is DeltaEnvironment.TESTNET
    assert snapshot.revision == 1
    assert len(snapshot.content_sha256) == 64
    assert snapshot.fetched_at == BASE_TIME
    assert snapshot.validated_at == BASE_TIME
    assert snapshot.fresh_until == BASE_TIME + timedelta(seconds=300)
    assert snapshot.products == products
    assert snapshot.by_id == {1101: products[0], 1102: products[1]}
    assert snapshot.by_symbol == {
        "XBTUSD_CAT": products[0],
        "ETHUSD_CAT": products[1],
    }
    assert snapshot.assets_by_id[12].symbol == "USD"
    assert products[0].contract_value == Decimal("0.001")
    assert products[0].tick_size == Decimal("0.5")
    assert products[0].contract_type is ContractType.PERPETUAL_FUTURES
    assert products[0].state is ProductState.LIVE
    assert products[0].trading_status is TradingStatus.OPERATIONAL
    assert products[0].settling_asset.id == 12
    assert products[0].position_size_limit == Decimal("5000")
    assert products[0].position_notional_limit == Decimal("250000")
    assert products[0].initial_margin == Decimal("0.02")
    assert products[0].maintenance_margin == Decimal("0.01")
    assert products[0].default_leverage == Decimal("50")
    assert products[0].max_leverage_notional == Decimal("100000")
    assert result.active_revision == 1
    assert result.active_content_sha256 == snapshot.content_sha256
    assert result.product_count == 2
    assert result.safe_error_code is None


@pytest.mark.asyncio
async def test_id_symbol_and_asset_mappings_round_trip() -> None:
    api = catalog_api()
    products = load_products()
    catalog = new_catalog(FetchQueue(complete_result(products)))
    await catalog.refresh()
    snapshot = catalog.require_snapshot(at=BASE_TIME)

    for product in products:
        assert snapshot.require_product_id(product.id) is product
        assert snapshot.require_symbol(product.symbol) is product
        for asset in (product.underlying_asset, product.quoting_asset, product.settling_asset):
            assert snapshot.assets_by_id[asset.id] == asset

    with pytest.raises(api.ProductNotFoundError) as missing_id:
        snapshot.require_product_id(999999)
    with pytest.raises(api.ProductNotFoundError) as missing_symbol:
        snapshot.require_symbol("SECRET-MISSING-SYMBOL")
    assert "SECRET-MISSING-SYMBOL" not in str(missing_symbol.value)
    assert "SECRET-MISSING-SYMBOL" not in repr(missing_symbol.value)
    assert missing_id.value.code == "product_not_found"


@pytest.mark.asyncio
async def test_snapshot_collections_and_identity_are_frozen() -> None:
    products = load_products()
    catalog = new_catalog(FetchQueue(complete_result(products)))
    await catalog.refresh()
    snapshot = catalog.peek()
    assert snapshot is not None
    assert isinstance(snapshot.by_id, MappingProxyType)
    assert isinstance(snapshot.by_symbol, MappingProxyType)
    assert isinstance(snapshot.assets_by_id, MappingProxyType)

    with pytest.raises(TypeError):
        snapshot.by_id[999] = products[0]  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.by_symbol["MUTATED"] = products[0]  # type: ignore[index]
    with pytest.raises((FrozenInstanceError, AttributeError)):
        snapshot.revision = 99
    with pytest.raises(Exception):  # noqa: B017 - Pydantic frozen error is version-specific
        snapshot.products[0].symbol = "MUTATED"
    assert "XBTUSD_CAT" not in repr(snapshot)
    assert snapshot.content_sha256 not in repr(snapshot)
    assert "product_count=2" in repr(snapshot)


@pytest.mark.asyncio
async def test_freshness_boundaries_and_require_snapshot_fail_closed() -> None:
    api = catalog_api()
    catalog = new_catalog(FetchQueue(complete_result(load_products())), ttl_seconds=60)

    assert catalog.peek() is None
    with pytest.raises(api.CatalogUnavailableError):
        catalog.require_snapshot(at=BASE_TIME)

    await catalog.refresh()
    snapshot = catalog.peek()
    assert snapshot is not None
    assert snapshot.is_fresh(BASE_TIME + timedelta(seconds=59, microseconds=999999))
    assert snapshot.is_fresh(BASE_TIME + timedelta(seconds=60))
    assert not snapshot.is_fresh(BASE_TIME + timedelta(seconds=60, microseconds=1))
    assert catalog.require_snapshot(at=snapshot.fresh_until) is snapshot
    with pytest.raises(api.StaleCatalogError) as stale:
        catalog.require_snapshot(at=snapshot.fresh_until + timedelta(microseconds=1))
    assert stale.value.code == "catalog_stale"

    for invalid in (datetime(2026, 11, 3, 10, 0), "2026-11-03T10:00:00Z", True):
        with pytest.raises((TypeError, ValueError)):
            snapshot.is_fresh(invalid)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_mixed_product_families_remain_visible_but_venue_helper_is_narrow() -> None:
    api = catalog_api()
    base = load_products()
    mixed = load_products("products_catalog_mixed.json")
    catalog = new_catalog(FetchQueue(complete_result(base + mixed)))
    await catalog.refresh()
    snapshot = catalog.require_snapshot(at=BASE_TIME)

    assert len(snapshot.products) == 5
    assert snapshot.require_product_id(1201).contract_type is ContractType.CALL_OPTIONS
    assert snapshot.require_product_id(1202).notional_type is NotionalType.INVERSE
    assert snapshot.require_product_id(1203).contract_type is ContractType.UNKNOWN
    assert snapshot.require_product_id(1203).state is ProductState.SETTLED
    assert snapshot.require_product_id(1203).trading_status is TradingStatus.UNKNOWN

    assert api.venue_allows_new_orders(base[0]) is True
    assert api.venue_allows_new_orders(base[1]) is False
    assert api.venue_allows_new_orders(mixed[0]) is False  # reduce-only option
    assert api.venue_allows_new_orders(mixed[1]) is False  # expired/disrupted
    assert api.venue_allows_new_orders(mixed[2]) is False  # settled/unknown
    live_option = mixed[0].model_copy(update={"only_reduce_only_orders_allowed": False})
    assert api.venue_allows_new_orders(live_option) is True


@pytest.mark.asyncio
async def test_hash_is_order_independent_source_aware_and_field_complete() -> None:
    products = load_products()
    first = new_catalog(FetchQueue(complete_result(products)), region="india")
    reversed_catalog = new_catalog(
        FetchQueue(complete_result(tuple(reversed(products)))), region="india"
    )
    global_catalog = new_catalog(FetchQueue(complete_result(products)), region="global")
    prod_catalog = new_catalog(
        FetchQueue(complete_result(products)), region="india", environment="prod"
    )
    changed_product = products[0].model_copy(update={"only_reduce_only_orders_allowed": True})
    changed = new_catalog(
        FetchQueue(complete_result((changed_product, products[1]))), region="india"
    )

    with localcontext() as context:
        context.prec = 2
        context.rounding = "ROUND_UP"
        await first.refresh()
        await reversed_catalog.refresh()
        await global_catalog.refresh()
        await prod_catalog.refresh()
        await changed.refresh()

    first_hash = first.peek().content_sha256  # type: ignore[union-attr]
    assert reversed_catalog.peek().content_sha256 == first_hash  # type: ignore[union-attr]
    assert global_catalog.peek().content_sha256 != first_hash  # type: ignore[union-attr]
    assert prod_catalog.peek().content_sha256 != first_hash  # type: ignore[union-attr]
    assert changed.peek().content_sha256 != first_hash  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_hash_canonicalizes_numerically_equal_decimal_encodings() -> None:
    products = load_products()
    equivalent = products[0].model_copy(
        update={
            "contract_value": Decimal("0.0010"),
            "tick_size": Decimal("0.500"),
            "initial_margin": Decimal("0.0200"),
            "default_leverage": Decimal("5E+1"),
        }
    )
    canonical = new_catalog(FetchQueue(complete_result(products)))
    reencoded = new_catalog(FetchQueue(complete_result((equivalent, products[1]))))

    await canonical.refresh()
    await reencoded.refresh()

    assert reencoded.peek().content_sha256 == canonical.peek().content_sha256  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_id",
        "duplicate_symbol",
        "casefold_symbol",
        "conflicting_asset",
        "asset_symbol_collision",
        "maintenance_above_initial",
    ],
)
async def test_invalid_candidates_are_rejected_atomically(mutation: str) -> None:
    products = list(load_products())
    first, second = products
    if mutation == "duplicate_id":
        second = second.model_copy(update={"id": first.id})
    elif mutation == "duplicate_symbol":
        second = second.model_copy(update={"symbol": first.symbol})
    elif mutation == "casefold_symbol":
        second = second.model_copy(update={"symbol": first.symbol.lower()})
    elif mutation == "conflicting_asset":
        altered = second.quoting_asset.model_copy(update={"precision": 7})
        second = second.model_copy(update={"quoting_asset": altered})
    elif mutation == "asset_symbol_collision":
        altered = second.underlying_asset.model_copy(update={"symbol": "USD"})
        second = second.model_copy(update={"underlying_asset": altered})
    else:
        second = second.model_copy(
            update={"initial_margin": Decimal("0.01"), "maintenance_margin": Decimal("0.02")}
        )

    catalog = new_catalog(FetchQueue(complete_result((first, second))))
    result = await catalog.refresh()

    assert result.status is catalog_api().CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is None
    assert mutation not in json.dumps(result.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["zero_tick", "negative_contract_value", "untyped_state", "invalid_asset_id"],
)
async def test_model_copy_cannot_bypass_candidate_validation(mutation: str) -> None:
    products = list(load_products())
    product = products[0]
    if mutation == "zero_tick":
        product = product.model_copy(update={"tick_size": Decimal("0")})
    elif mutation == "negative_contract_value":
        product = product.model_copy(update={"contract_value": Decimal("-0.001")})
    elif mutation == "untyped_state":
        product = product.model_copy(update={"state": "live"})
    else:
        asset = product.underlying_asset.model_copy(update={"id": 0})
        product = product.model_copy(update={"underlying_asset": asset})
    products[0] = product
    catalog = new_catalog(FetchQueue(complete_result(products)))

    result = await catalog.refresh()

    assert result.status is catalog_api().CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is None


def test_source_and_constructor_inputs_are_strict() -> None:
    api = catalog_api()
    for region, environment in (
        ("india", DeltaEnvironment.TESTNET),
        (DeltaRegion.INDIA, "testnet"),
        (True, DeltaEnvironment.TESTNET),
    ):
        with pytest.raises((TypeError, ValueError)):
            api.CatalogSource(region=region, environment=environment)
    source = api.CatalogSource(region=DeltaRegion.INDIA, environment=DeltaEnvironment.TESTNET)
    assert "india" in repr(source)
    assert "testnet" in repr(source)


@pytest.mark.asyncio
async def test_snapshot_and_errors_do_not_expose_raw_product_payloads() -> None:
    api = catalog_api()
    secret = "FIXTURE-SECRET-PRODUCT-PAYLOAD"
    catalog = new_catalog(FetchQueue(ExchangeProtocolError(context={"raw_body": secret})))
    result = await catalog.refresh()
    rendered = json.dumps(
        {
            "repr": repr(result),
            "error": result.safe_error_code,
            "context": getattr(result, "to_dict", lambda: {})(),
        },
        default=str,
    )
    assert secret not in rendered
    assert result.status is api.CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "exchange_protocol_error"
