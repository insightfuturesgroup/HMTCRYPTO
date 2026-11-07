"""Direct snapshot construction cannot forge derived catalogue evidence."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from product_catalog_test_support import BASE_TIME, catalog_api, load_products

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import Asset

pytestmark = pytest.mark.unit


def source() -> object:
    api = catalog_api()
    return api.CatalogSource(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )


def test_direct_snapshot_derives_indexes_hash_and_freshness() -> None:
    api = catalog_api()
    products = load_products()
    try:
        snapshot = api.CatalogSnapshot(
            source=source(),
            revision=1,
            fetched_at=BASE_TIME,
            validated_at=BASE_TIME,
            ttl=timedelta(seconds=60),
            products=products,
        )
    except TypeError as error:
        pytest.fail(f"snapshot constructor did not derive evidence: {type(error).__name__}")

    assert snapshot.products == products
    assert set(snapshot.by_id) == {1101, 1102}
    assert set(snapshot.by_symbol) == {"XBTUSD_CAT", "ETHUSD_CAT"}
    assert set(snapshot.assets_by_id) == {11, 12, 13}
    assert snapshot.content_sha256 == (
        "ca905c092add7231063044c6a224f1d932c8a2aa9296a9ed30bac55fc549397b"
    )
    assert snapshot.fresh_until == BASE_TIME + timedelta(seconds=60)
    assert "XBTUSD_CAT" not in repr(snapshot)
    assert snapshot.content_sha256 not in repr(snapshot)


@pytest.mark.parametrize(
    "case",
    ["forged_hash", "extra_asset", "duplicate_products", "forged_freshness"],
)
def test_legacy_constructor_cannot_supply_derived_evidence(case: str) -> None:
    api = catalog_api()
    products = load_products()
    kwargs = {
        "source": source(),
        "revision": 1,
        "content_sha256": "0" * 64,
        "fetched_at": BASE_TIME,
        "validated_at": BASE_TIME,
        "fresh_until": BASE_TIME + timedelta(seconds=60),
        "products": products,
        "by_id": {product.id: product for product in products},
        "by_symbol": {product.symbol: product for product in products},
        "assets_by_id": {
            asset.id: asset
            for product in products
            for asset in (
                product.underlying_asset,
                product.quoting_asset,
                product.settling_asset,
            )
        },
    }
    if case == "extra_asset":
        kwargs["content_sha256"] = (
            "ca905c092add7231063044c6a224f1d932c8a2aa9296a9ed30bac55fc549397b"
        )
        kwargs["assets_by_id"] = {
            **kwargs["assets_by_id"],
            999: Asset(id=999, symbol="EXTRA", precision=8, minimum_precision=1),
        }
    elif case == "duplicate_products":
        first = products[0]
        kwargs.update(
            products=(first, first),
            by_id={first.id: first},
            by_symbol={first.symbol: first},
            assets_by_id={},
        )
    elif case == "forged_freshness":
        kwargs["fresh_until"] = BASE_TIME + timedelta(days=3650)

    with pytest.raises((TypeError, ValueError)):
        api.CatalogSnapshot(**kwargs)


@pytest.mark.parametrize("case", ["duplicate", "invalid_product"])
def test_direct_snapshot_reuses_full_candidate_validation(case: str) -> None:
    api = catalog_api()
    products = load_products()
    if case == "duplicate":
        candidate = (products[0], products[0])
    else:
        candidate = (
            products[0].model_copy(update={"tick_size": Decimal("0")}),
            products[1],
        )

    with pytest.raises(ValueError):
        api.CatalogSnapshot(
            source=source(),
            revision=1,
            fetched_at=BASE_TIME,
            validated_at=BASE_TIME,
            ttl=timedelta(seconds=60),
            products=candidate,
        )
