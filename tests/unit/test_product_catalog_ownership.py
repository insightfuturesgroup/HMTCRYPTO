"""Catalogue boundaries own source, product, and nested asset evidence."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from product_catalog_test_support import (
    BASE_TIME,
    FetchQueue,
    MutableClock,
    catalog_api,
    complete_result,
    load_products,
)

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import Product

pytestmark = pytest.mark.unit


def source(
    region: DeltaRegion = DeltaRegion.INDIA,
    environment: DeltaEnvironment = DeltaEnvironment.TESTNET,
) -> object:
    return catalog_api().CatalogSource(region=region, environment=environment)


def mutate_source(value: object) -> None:
    object.__setattr__(value, "region", DeltaRegion.GLOBAL)
    object.__setattr__(value, "environment", DeltaEnvironment.PROD)


def mutate_product(product: Product, *, identifier: int, suffix: str) -> None:
    product.__dict__["id"] = identifier
    product.__dict__["symbol"] = f"MUTATED-{suffix}"
    product.__dict__["contract_value"] = Decimal("99")
    product.__dict__["only_reduce_only_orders_allowed"] = True
    product.underlying_asset.__dict__["id"] = identifier + 1000
    product.underlying_asset.__dict__["symbol"] = f"ASSET-{suffix}"


def catalog_with_source(source_value: object, load: object) -> object:
    api = catalog_api()
    return api.ProductCatalog(
        source=source_value,
        fetch_products=FetchQueue(load),
        ttl=timedelta(seconds=60),
        clock=MutableClock(),
    )


@pytest.mark.asyncio
async def test_shared_source_mutation_cannot_relabel_a_load_before_refresh() -> None:
    api = catalog_api()
    external_source = source()
    load = api.CatalogProductLoad(
        source=external_source,
        pagination=complete_result(load_products()),
    )
    catalog = catalog_with_source(external_source, load)

    mutate_source(external_source)
    result = await catalog.refresh()
    snapshot = catalog.peek()

    assert result.status is api.CatalogRefreshStatus.PUBLISHED_CHANGED
    assert snapshot is not None
    assert catalog.source.region is DeltaRegion.INDIA
    assert catalog.source.environment is DeltaEnvironment.TESTNET
    assert load.source.region is DeltaRegion.INDIA
    assert load.source.environment is DeltaEnvironment.TESTNET
    assert snapshot.source.region is DeltaRegion.INDIA
    assert snapshot.source.environment is DeltaEnvironment.TESTNET
    assert catalog.source is not external_source
    assert load.source is not external_source
    assert snapshot.source is not external_source
    assert snapshot.content_sha256 == (
        "ca905c092add7231063044c6a224f1d932c8a2aa9296a9ed30bac55fc549397b"
    )


@pytest.mark.asyncio
async def test_original_source_mutation_cannot_hide_a_real_mismatch() -> None:
    api = catalog_api()
    catalog_source = source()
    load_source = source(DeltaRegion.GLOBAL, DeltaEnvironment.PROD)
    load = api.CatalogProductLoad(
        source=load_source,
        pagination=complete_result(load_products()),
    )
    catalog = catalog_with_source(catalog_source, load)

    mutate_source(catalog_source)
    result = await catalog.refresh()

    assert result.status is api.CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_source_mismatch"
    assert catalog.peek() is None
    assert catalog.source.region is DeltaRegion.INDIA
    assert catalog.source.environment is DeltaEnvironment.TESTNET
    assert load.source.region is DeltaRegion.GLOBAL
    assert load.source.environment is DeltaEnvironment.PROD


def test_load_deep_snapshots_pagination_products_and_assets() -> None:
    api = catalog_api()
    external_source = source()
    products = load_products()
    page = complete_result(products)
    load = api.CatalogProductLoad(source=external_source, pagination=page)
    owned = load.pagination.items[0]

    assert load.pagination is not page
    assert load.source is not external_source
    assert owned is not products[0]
    assert owned.underlying_asset is not products[0].underlying_asset
    assert owned.quoting_asset is not products[0].quoting_asset
    assert owned.settling_asset is not products[0].settling_asset
    assert "only_reduce_only_orders_allowed" in owned.model_fields_set

    mutate_product(products[0], identifier=9001, suffix="ORIGINAL")
    object.__setattr__(page, "items", ())

    assert len(load.pagination.items) == 2
    assert owned.id == 1101
    assert owned.symbol == "XBTUSD_CAT"
    assert owned.contract_value == Decimal("0.001")
    assert owned.only_reduce_only_orders_allowed is False
    assert owned.underlying_asset.id == 11
    assert owned.underlying_asset.symbol == "XBT"


@pytest.mark.asyncio
async def test_snapshot_owns_load_products_and_survives_all_original_mutation() -> None:
    api = catalog_api()
    external_source = source()
    products = load_products()
    load = api.CatalogProductLoad(
        source=external_source,
        pagination=complete_result(products),
    )
    load_product = load.pagination.items[0]
    catalog = catalog_with_source(external_source, load)
    await catalog.refresh()
    snapshot = catalog.peek()
    assert snapshot is not None
    snapshot_product = snapshot.require_product_id(1101)
    original_hash = snapshot.content_sha256
    original_fresh_until = snapshot.fresh_until

    assert snapshot.source is not external_source
    assert snapshot.source is not catalog.source
    assert snapshot_product is not products[0]
    assert snapshot_product is not load_product
    assert snapshot_product.underlying_asset is not products[0].underlying_asset
    assert snapshot_product.underlying_asset is not load_product.underlying_asset

    mutate_source(external_source)
    mutate_product(products[0], identifier=9001, suffix="ORIGINAL")
    mutate_product(load_product, identifier=9002, suffix="LOAD")

    assert snapshot.source.region is DeltaRegion.INDIA
    assert snapshot.source.environment is DeltaEnvironment.TESTNET
    assert snapshot.content_sha256 == original_hash
    assert snapshot.fresh_until == original_fresh_until
    assert set(snapshot.by_id) == {1101, 1102}
    assert set(snapshot.by_symbol) == {"XBTUSD_CAT", "ETHUSD_CAT"}
    assert snapshot.require_product_id(1101) is snapshot_product
    assert snapshot.require_symbol("XBTUSD_CAT") is snapshot_product
    assert snapshot_product.id == 1101
    assert snapshot_product.symbol == "XBTUSD_CAT"
    assert snapshot_product.contract_value == Decimal("0.001")
    assert snapshot_product.only_reduce_only_orders_allowed is False
    assert snapshot_product.underlying_asset.id == 11
    assert snapshot_product.underlying_asset.symbol == "XBT"
    external_assets = {
        id(asset)
        for product in products
        for asset in (
            product.underlying_asset,
            product.quoting_asset,
            product.settling_asset,
        )
    }
    assert all(id(asset) not in external_assets for asset in snapshot.assets_by_id.values())


def test_direct_snapshot_owns_source_products_and_assets() -> None:
    api = catalog_api()
    external_source = source()
    products = load_products()
    snapshot = api.CatalogSnapshot(
        source=external_source,
        revision=1,
        fetched_at=BASE_TIME,
        validated_at=BASE_TIME,
        ttl=timedelta(seconds=60),
        products=products,
    )

    assert snapshot.source is not external_source
    assert snapshot.products[0] is not products[0]
    assert snapshot.products[0].underlying_asset is not products[0].underlying_asset

    mutate_source(external_source)
    mutate_product(products[0], identifier=9001, suffix="DIRECT")

    assert snapshot.source.region is DeltaRegion.INDIA
    assert snapshot.source.environment is DeltaEnvironment.TESTNET
    assert snapshot.require_product_id(1101).symbol == "XBTUSD_CAT"
    assert snapshot.require_product_id(1101).underlying_asset.symbol == "XBT"


@pytest.mark.asyncio
async def test_product_clone_preserves_reduce_only_provenance() -> None:
    api = catalog_api()
    explicit = load_products()[0]
    explicit_load = api.CatalogProductLoad(
        source=source(),
        pagination=complete_result((explicit,)),
    )
    explicit_owned = explicit_load.pagination.items[0]
    assert explicit_owned is not explicit
    assert "only_reduce_only_orders_allowed" in explicit_owned.model_fields_set
    assert explicit_owned.only_reduce_only_orders_allowed is False

    values = explicit.model_dump(mode="python")
    values.pop("only_reduce_only_orders_allowed")
    omitted = Product.model_validate(values, strict=True)
    assert "only_reduce_only_orders_allowed" not in omitted.model_fields_set
    omitted_load = api.CatalogProductLoad(
        source=source(),
        pagination=complete_result((omitted,)),
    )
    omitted_owned = omitted_load.pagination.items[0]
    assert omitted_owned is not omitted
    assert "only_reduce_only_orders_allowed" not in omitted_owned.model_fields_set
    catalog = catalog_with_source(source(), omitted_load)

    result = await catalog.refresh()

    assert result.status is api.CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is None
