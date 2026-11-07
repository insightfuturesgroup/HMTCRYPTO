"""Catalogue loads carry immutable, exact region/environment provenance."""

from __future__ import annotations

import importlib
import json
from dataclasses import FrozenInstanceError
from datetime import timedelta
from typing import Any

import pytest
from product_catalog_test_support import (
    FetchQueue,
    MutableClock,
    catalog_api,
    complete_result,
    load_products,
    new_catalog,
)

from hmt_crypto.config import DeltaEnvironment, DeltaRegion

pytestmark = pytest.mark.unit

SOURCES = tuple(
    (region.value, environment.value) for region in DeltaRegion for environment in DeltaEnvironment
)
MISMATCHES = tuple(
    (catalog_source, load_source)
    for catalog_source in SOURCES
    for load_source in SOURCES
    if catalog_source != load_source
)


def product_load_type() -> type[Any]:
    module = importlib.import_module("hmt_crypto.exchange.delta.catalog")
    load_type = getattr(module, "CatalogProductLoad", None)
    if not isinstance(load_type, type):
        pytest.fail("source-bearing CatalogProductLoad API is missing", pytrace=False)
    return load_type


def source(region: str, environment: str) -> Any:
    api = catalog_api()
    return api.CatalogSource(
        region=DeltaRegion(region),
        environment=DeltaEnvironment(environment),
    )


def load(region: str, environment: str) -> Any:
    return product_load_type()(
        source=source(region, environment),
        pagination=complete_result(load_products()),
    )


def test_source_bearing_load_is_immutable_and_repr_is_payload_safe() -> None:
    evidence = load("india", "testnet")
    with pytest.raises((FrozenInstanceError, AttributeError)):
        evidence.source = source("global", "prod")
    rendered = repr(evidence)
    assert "india" in rendered
    assert "testnet" in rendered
    assert "item_count=2" in rendered
    assert "XBTUSD_CAT" not in rendered
    assert "ETHUSD_CAT" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(("catalog_source", "load_source"), MISMATCHES)
async def test_every_region_environment_mismatch_fails_closed(
    catalog_source: tuple[str, str],
    load_source: tuple[str, str],
) -> None:
    api = catalog_api()
    region, environment = catalog_source
    catalog = new_catalog(
        FetchQueue(load(*load_source)),
        region=region,
        environment=environment,
    )

    result = await catalog.refresh()

    assert result.status is api.CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_source_mismatch"
    assert result.product_count == 0
    assert catalog.peek() is None
    rendered = json.dumps(result.to_dict()) + repr(result)
    assert "XBTUSD_CAT" not in rendered
    assert "ETHUSD_CAT" not in rendered


@pytest.mark.asyncio
async def test_mismatched_refresh_retains_exact_last_good_without_renewal() -> None:
    api = catalog_api()
    clock = MutableClock()
    catalog = new_catalog(
        FetchQueue(load("india", "testnet"), load("global", "testnet")),
        clock=clock,
        ttl_seconds=60,
    )
    await catalog.refresh()
    first = catalog.peek()
    assert first is not None
    clock.now += timedelta(seconds=30)

    result = await catalog.refresh()

    assert result.status is api.CatalogRefreshStatus.RETAINED_LAST_GOOD
    assert result.safe_error_code == "catalog_source_mismatch"
    assert result.active_revision == 1
    assert catalog.peek() is first
    assert catalog.peek().fresh_until == first.fresh_until


@pytest.mark.asyncio
async def test_equality_spoofed_source_fails_closed() -> None:
    class EqualitySpoof:
        region = DeltaRegion.INDIA
        environment = DeltaEnvironment.TESTNET

        def __eq__(self, other: object) -> bool:
            return True

    evidence = load("india", "testnet")
    object.__setattr__(evidence, "source", EqualitySpoof())
    catalog = new_catalog(FetchQueue(evidence))

    result = await catalog.refresh()

    assert result.status is catalog_api().CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_source_mismatch"
    assert catalog.peek() is None


@pytest.mark.asyncio
async def test_bare_pagination_result_is_no_longer_a_catalogue_load() -> None:
    catalog = new_catalog(FetchQueue(complete_result(load_products())))

    result = await catalog.refresh()

    assert result.status is catalog_api().CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is None
