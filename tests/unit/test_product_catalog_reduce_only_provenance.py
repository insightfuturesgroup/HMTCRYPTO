"""Risk-increasing venue permission requires explicit reduce-only metadata."""

from __future__ import annotations

import pytest
from product_catalog_test_support import (
    FetchQueue,
    catalog_api,
    complete_load,
    load_products,
    new_catalog,
)

from hmt_crypto.domain import Product

pytestmark = pytest.mark.unit


def product_without_reduce_only_provenance() -> Product:
    values = load_products()[0].model_dump(mode="python")
    values.pop("only_reduce_only_orders_allowed")
    product = Product.model_validate(values, strict=True)
    assert "only_reduce_only_orders_allowed" not in product.model_fields_set
    return product


@pytest.mark.asyncio
@pytest.mark.parametrize("copied", [False, True])
async def test_omitted_reduce_only_provenance_never_publishes(copied: bool) -> None:
    product = product_without_reduce_only_provenance()
    if copied:
        product = product.model_copy(update={"symbol": "COPIED-WITHOUT-PROVENANCE"})
    catalog = new_catalog(FetchQueue(complete_load((product,))))

    result = await catalog.refresh()

    assert result.status is catalog_api().CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is None


def test_venue_helper_fails_closed_without_reduce_only_provenance() -> None:
    api = catalog_api()
    product = product_without_reduce_only_provenance()
    assert api.venue_allows_new_orders(product) is False
    copied = product.model_copy(update={"symbol": "COPIED-WITHOUT-PROVENANCE"})
    assert api.venue_allows_new_orders(copied) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("product_index", "expected_venue_permission"), [(0, True), (1, False)])
async def test_explicit_false_and_true_reduce_only_metadata_are_preserved(
    product_index: int,
    expected_venue_permission: bool,
) -> None:
    api = catalog_api()
    product = load_products()[product_index]
    assert "only_reduce_only_orders_allowed" in product.model_fields_set
    catalog = new_catalog(FetchQueue(complete_load((product,))))

    result = await catalog.refresh()
    snapshot = catalog.peek()

    assert result.status is api.CatalogRefreshStatus.PUBLISHED_CHANGED
    assert snapshot is not None
    assert snapshot.products == (product,)
    assert api.venue_allows_new_orders(product) is expected_venue_permission


@pytest.mark.asyncio
async def test_owned_load_mutation_cannot_forge_reduce_only_type() -> None:
    load = complete_load((load_products()[0],))
    load.pagination.items[0].__dict__["only_reduce_only_orders_allowed"] = "false"
    catalog = new_catalog(FetchQueue(load))

    result = await catalog.refresh()

    assert result.status is catalog_api().CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is None
