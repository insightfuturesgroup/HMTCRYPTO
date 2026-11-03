"""Fail-closed product resolution, provenance, and fresh-price selection."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from contract_sizing_test_support import (
    BASE_TIME,
    make_product,
    make_registry,
    make_snapshot,
    make_ticker,
    resolve,
    sizing_api,
)

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import ContractType, NotionalType, ProductState, TradingStatus

pytestmark = pytest.mark.unit


class ExplodingDateTime(datetime):
    """Hostile datetime subclass that must not escape the typed boundary."""

    def utcoffset(self) -> timedelta | None:
        raise RuntimeError("DO-NOT-LEAK-CLOCK")


class ExplodingProducts:
    """Forged snapshot collection that raises instead of yielding products."""

    def __iter__(self) -> object:
        raise RuntimeError("DO-NOT-LEAK-SNAPSHOT")


@pytest.mark.parametrize(
    "updates",
    [
        {"contract_type": ContractType.CALL_OPTIONS},
        {"contract_type": ContractType.PUT_OPTIONS},
        {"contract_type": ContractType.SPOT},
        {"contract_type": ContractType.UNKNOWN},
        {"notional_type": NotionalType.INVERSE},
        {"notional_type": NotionalType.UNKNOWN},
        {"is_quanto": True},
        {"contract_unit_currency": "USD"},
        {"state": ProductState.UPCOMING},
        {"state": ProductState.EXPIRED},
        {"state": ProductState.SETTLED},
        {"state": ProductState.UNKNOWN},
        {"trading_status": TradingStatus.DISRUPTED_CANCEL_ONLY},
        {"trading_status": TradingStatus.DISRUPTED_POST_ONLY},
        {"trading_status": TradingStatus.UNKNOWN},
    ],
)
def test_resolver_rejects_every_unverified_or_inactive_product(updates: dict[str, object]) -> None:
    api = sizing_api()
    with pytest.raises(api.UnsupportedProductError) as error:
        resolve(make_product(**updates))
    assert error.value.code == "unsupported_product"


def test_futures_and_perpetuals_are_the_only_supported_contract_families() -> None:
    perpetual = resolve(make_product(contract_type=ContractType.PERPETUAL_FUTURES))
    dated = resolve(make_product(contract_type=ContractType.FUTURES))
    deceptive_symbol = resolve(make_product(symbol="INVERSE-NAME-IS-NOT-METADATA"))

    assert perpetual.contracts_to_base_quantity(2) == Decimal("0.002")
    assert dated.contracts_to_base_quantity(2) == Decimal("0.002")
    assert deceptive_symbol.contracts_to_base_quantity(2) == Decimal("0.002")


def test_reduce_only_provenance_blocks_new_risk_but_permits_explicit_reduction() -> None:
    api = sizing_api()
    product = make_product(only_reduce_only_orders_allowed=True)

    with pytest.raises(api.UnsupportedProductError):
        resolve(product, risk_increasing=True)

    reducing = resolve(product, risk_increasing=False)
    assert reducing.contracts_to_base_quantity(7) == Decimal("0.007")
    assert reducing.audit_evidence()["risk_increasing"] is False
    assert reducing.audit_evidence()["only_reduce_only_orders_allowed"] is True

    with pytest.raises(api.ContractSizingError):
        resolve(product, risk_increasing="false")  # type: ignore[arg-type]


def test_missing_or_forged_product_metadata_fails_before_arithmetic() -> None:
    api = sizing_api()

    missing_provenance = make_snapshot()
    missing_provenance.products[0].__pydantic_fields_set__.discard(
        "only_reduce_only_orders_allowed"
    )
    with pytest.raises(api.UnsupportedProductError):
        resolve(snapshot=missing_provenance)

    invalid_contract = make_snapshot()
    object.__setattr__(invalid_contract.products[0], "contract_value", Decimal("0"))
    with pytest.raises(api.UnsupportedProductError):
        resolve(snapshot=invalid_contract)

    pathological_option = make_product(
        contract_type=ContractType.CALL_OPTIONS,
        contract_value=Decimal("1E+1000"),
    )
    with pytest.raises(api.UnsupportedProductError):
        resolve(pathological_option)


def test_registry_requires_a_real_fresh_source_bound_snapshot() -> None:
    api = sizing_api()
    registry = make_registry()
    snapshot = make_snapshot()

    with pytest.raises(api.ContractSizingError):
        registry.resolve(
            snapshot=object(),
            product_id=1001,
            checked_at=BASE_TIME,
            risk_increasing=True,
        )

    global_snapshot = make_snapshot(region=DeltaRegion.GLOBAL)
    with pytest.raises(api.UnsupportedProductError):
        registry.resolve(
            snapshot=global_snapshot,
            product_id=1001,
            checked_at=BASE_TIME,
            risk_increasing=True,
        )

    stale_at = snapshot.fresh_until + timedelta(microseconds=1)
    with pytest.raises(api.StaleCatalogError) as stale:
        registry.resolve(
            snapshot=snapshot,
            product_id=1001,
            checked_at=stale_at,
            risk_increasing=True,
        )
    assert stale.value.code == "catalog_stale"

    # The inclusive freshness edge remains valid.
    assert (
        registry.resolve(
            snapshot=snapshot,
            product_id=1001,
            checked_at=snapshot.fresh_until,
            risk_increasing=True,
        ).product_id
        == 1001
    )

    hostile_clock = ExplodingDateTime(2026, 11, 3, 10, tzinfo=BASE_TIME.tzinfo)
    with pytest.raises(api.ContractSizingError) as invalid_clock:
        registry.resolve(
            snapshot=snapshot,
            product_id=1001,
            checked_at=hostile_clock,
            risk_increasing=True,
        )
    assert "DO-NOT-LEAK-CLOCK" not in str(invalid_clock.value)


def test_snapshot_revision_hash_and_source_evidence_are_verified() -> None:
    api = sizing_api()

    for attribute, value in (
        ("revision", True),
        ("content_sha256", "a" * 64),
        ("content_sha256", "not-a-hash"),
    ):
        snapshot = make_snapshot()
        object.__setattr__(snapshot, attribute, value)
        with pytest.raises(api.UnsupportedProductError):
            resolve(snapshot=snapshot)

    forged_products = make_snapshot()
    object.__setattr__(forged_products, "products", ExplodingProducts())
    with pytest.raises(api.UnsupportedProductError) as forged:
        make_registry().resolve(
            snapshot=forged_products,
            product_id=1001,
            checked_at=BASE_TIME,
            risk_increasing=True,
        )
    assert "DO-NOT-LEAK-SNAPSHOT" not in str(forged.value)


def test_sizer_owns_a_product_clone_and_exposes_only_safe_audit_evidence() -> None:
    snapshot = make_snapshot()
    original_hash = snapshot.content_sha256
    sizer = resolve(snapshot=snapshot)
    object.__setattr__(snapshot.products[0], "contract_value", Decimal("999"))

    assert sizer.contracts_to_base_quantity(7) == Decimal("0.007")
    evidence = sizer.audit_evidence()
    assert evidence == {
        "region": "india",
        "environment": "testnet",
        "catalog_revision": 7,
        "catalog_content_sha256": original_hash,
        "product_id": 1001,
        "risk_increasing": True,
        "only_reduce_only_orders_allowed": False,
    }
    rendered = repr(sizer)
    assert "region='india'" in rendered
    assert "environment='testnet'" in rendered
    assert "catalog_revision=7" in rendered
    assert original_hash in rendered
    assert "product_id=1001" in rendered
    assert "Product(" not in rendered
    assert "CatalogSnapshot(" not in rendered

    evidence["product_id"] = 999  # returned mapping cannot mutate the sizer
    assert sizer.audit_evidence()["product_id"] == 1001


@pytest.mark.parametrize(
    ("basis_name", "expected"),
    [
        ("MARK", Decimal("50000")),
        ("BEST_BID", Decimal("49999.5")),
        ("BEST_ASK", Decimal("50000.5")),
        ("CLOSE", Decimal("49990")),
    ],
)
def test_fresh_price_selection_requires_an_explicit_basis(
    basis_name: str, expected: Decimal
) -> None:
    api = sizing_api()
    selected = resolve().select_fresh_price(
        make_ticker(),
        basis=getattr(api.PriceBasis, basis_name),
        checked_at=BASE_TIME,
        max_age=timedelta(seconds=1),
    )
    assert selected == expected

    with pytest.raises(api.ContractSizingError):
        resolve().select_fresh_price(
            make_ticker(),
            basis="mark",
            checked_at=BASE_TIME,
            max_age=timedelta(seconds=1),
        )


def test_price_selection_never_falls_back_and_close_is_reference_only() -> None:
    api = sizing_api()
    sizer = resolve()
    ticker = make_ticker(mark_price=None, close=Decimal("49990"))

    with pytest.raises(api.ContractSizingError):
        sizer.select_fresh_price(
            ticker,
            basis=api.PriceBasis.MARK,
            checked_at=BASE_TIME,
            max_age=timedelta(seconds=1),
        )
    assert sizer.select_fresh_price(
        ticker,
        basis=api.PriceBasis.CLOSE,
        checked_at=BASE_TIME,
        max_age=timedelta(seconds=1),
    ) == Decimal("49990")


def test_price_selection_validates_identity_status_and_time_boundaries() -> None:
    api = sizing_api()
    sizer = resolve()
    max_age = timedelta(seconds=5)

    boundary = make_ticker(timestamp=BASE_TIME - max_age)
    assert sizer.select_fresh_price(
        boundary,
        basis=api.PriceBasis.MARK,
        checked_at=BASE_TIME,
        max_age=max_age,
    ) == Decimal("50000")

    invalid_tickers = (
        make_ticker(product_id=999),
        make_ticker(symbol="OTHER"),
        make_ticker(timestamp=BASE_TIME + timedelta(microseconds=1)),
        make_ticker(timestamp=BASE_TIME - max_age - timedelta(microseconds=1)),
        make_ticker(product_trading_status=TradingStatus.DISRUPTED_CANCEL_ONLY),
        make_ticker(product_trading_status=TradingStatus.DISRUPTED_POST_ONLY),
    )
    for ticker in invalid_tickers:
        with pytest.raises(api.ContractSizingError):
            sizer.select_fresh_price(
                ticker,
                basis=api.PriceBasis.MARK,
                checked_at=BASE_TIME,
                max_age=max_age,
            )

    # UNKNOWN is documented for REST and catalogue status remains authoritative.
    assert sizer.select_fresh_price(
        make_ticker(product_trading_status=TradingStatus.UNKNOWN),
        basis=api.PriceBasis.MARK,
        checked_at=BASE_TIME,
        max_age=max_age,
    ) == Decimal("50000")
    assert sizer.select_fresh_price(
        make_ticker(product_trading_status=TradingStatus.OPERATIONAL),
        basis=api.PriceBasis.MARK,
        checked_at=BASE_TIME,
        max_age=max_age,
    ) == Decimal("50000")


def test_price_selection_rejects_invalid_clock_age_and_hostile_ticker_mutation() -> None:
    api = sizing_api()
    sizer = resolve()

    for max_age in (timedelta(0), timedelta(microseconds=-1), "5 seconds"):
        with pytest.raises(api.ContractSizingError):
            sizer.select_fresh_price(
                make_ticker(),
                basis=api.PriceBasis.MARK,
                checked_at=BASE_TIME,
                max_age=max_age,
            )

    with pytest.raises(api.ContractSizingError):
        sizer.select_fresh_price(
            make_ticker(),
            basis=api.PriceBasis.MARK,
            checked_at=BASE_TIME.replace(tzinfo=None),
            max_age=timedelta(seconds=1),
        )

    hostile = make_ticker()
    object.__setattr__(hostile, "mark_price", "DO-NOT-LEAK-TICKER")
    with pytest.raises(api.ContractSizingError) as error:
        sizer.select_fresh_price(
            hostile,
            basis=api.PriceBasis.MARK,
            checked_at=BASE_TIME,
            max_age=timedelta(seconds=1),
        )
    assert "DO-NOT-LEAK-TICKER" not in str(error.value)
    assert "DO-NOT-LEAK-TICKER" not in repr(error.value)


def test_registry_clones_its_source_identity() -> None:
    source_region = DeltaRegion.INDIA
    registry = make_registry(region=source_region, environment=DeltaEnvironment.TESTNET)
    assert registry.source_evidence() == {
        "region": "india",
        "environment": "testnet",
    }


def test_linear_sizer_cannot_be_constructed_from_an_unresolved_product() -> None:
    api = sizing_api()
    with pytest.raises(api.ContractSizingError):
        api.LinearContractSizer(make_product())
