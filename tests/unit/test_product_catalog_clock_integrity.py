"""Catalogue freshness and generations fail closed across wall-clock rollback."""

from __future__ import annotations

from datetime import timedelta

import pytest
from product_catalog_test_support import (
    BASE_TIME,
    FetchQueue,
    SequenceClock,
    catalog_api,
    complete_load,
    load_products,
    new_catalog,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_snapshot_is_fresh_only_from_validation_through_expiry() -> None:
    api = catalog_api()
    catalog = new_catalog(FetchQueue(complete_load(load_products())), ttl_seconds=60)
    await catalog.refresh()
    snapshot = catalog.peek()
    assert snapshot is not None

    assert snapshot.is_fresh(snapshot.validated_at - timedelta(microseconds=1)) is False
    assert snapshot.is_fresh(snapshot.validated_at) is True
    assert snapshot.is_fresh(snapshot.fresh_until) is True
    assert snapshot.is_fresh(snapshot.fresh_until + timedelta(microseconds=1)) is False
    with pytest.raises(api.StaleCatalogError):
        catalog.require_snapshot(at=snapshot.validated_at - timedelta(microseconds=1))
    assert catalog.require_snapshot(at=snapshot.validated_at) is snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("rollback_stage", ["attempted", "fetched", "validated"])
async def test_clock_rollback_retains_exact_generation_without_renewal(
    rollback_stage: str,
    changed: bool,
) -> None:
    api = catalog_api()
    products = load_products()
    replacement = products
    if changed:
        replacement = (
            products[0].model_copy(update={"tick_size": products[0].tick_size * 2}),
            products[1],
        )
    second_times = {
        "attempted": (
            BASE_TIME - timedelta(seconds=1),
            BASE_TIME + timedelta(seconds=1),
            BASE_TIME + timedelta(seconds=2),
        ),
        "fetched": (
            BASE_TIME + timedelta(seconds=1),
            BASE_TIME - timedelta(seconds=1),
            BASE_TIME + timedelta(seconds=2),
        ),
        "validated": (
            BASE_TIME + timedelta(seconds=1),
            BASE_TIME + timedelta(seconds=2),
            BASE_TIME - timedelta(seconds=1),
        ),
    }[rollback_stage]
    clock = SequenceClock(BASE_TIME, BASE_TIME, BASE_TIME, *second_times)
    catalog = new_catalog(
        FetchQueue(complete_load(products), complete_load(replacement)),
        clock=clock,
        ttl_seconds=60,
    )
    await catalog.refresh()
    first = catalog.peek()
    assert first is not None

    result = await catalog.refresh()

    assert result.status is api.CatalogRefreshStatus.RETAINED_LAST_GOOD
    assert result.safe_error_code == "catalog_clock_invalid"
    assert result.active_revision == first.revision == 1
    assert result.active_content_sha256 == first.content_sha256
    assert catalog.peek() is first
    assert catalog.peek().fresh_until == first.fresh_until
