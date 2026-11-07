"""Regressions for sizing issuance, publication, Decimal, and ticker provenance."""

from __future__ import annotations

import importlib
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from contract_sizing_test_support import (
    BASE_TIME,
    make_product,
    make_registry,
    make_snapshot,
    make_ticker,
    make_unpublished_snapshot,
    resolve,
    sizing_api,
)

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import ContractType
from hmt_crypto.exchange.delta.catalog import CatalogSource
from hmt_crypto.exchange.delta.sizing_validation import ResolvedSizingProduct

pytestmark = pytest.mark.unit

HOSTILE_DECIMAL_MARKER = "DO-NOT-LEAK-HOSTILE-DECIMAL"


class HostileDecimal(Decimal):
    """Decimal subclass whose normalization methods expose unsafe execution."""

    def is_finite(self) -> bool:
        raise RuntimeError(HOSTILE_DECIMAL_MARKER)

    def as_tuple(self) -> Any:
        raise RuntimeError(HOSTILE_DECIMAL_MARKER)


def source_bound_ticker(*, source: CatalogSource, ticker: object) -> object:
    """Construct the wished-for public source-bearing ticker evidence."""
    module = importlib.import_module("hmt_crypto.exchange.delta.sizing")
    evidence_type = getattr(module, "SourceBoundTicker", None)
    if evidence_type is None:
        pytest.fail("Source-bound ticker evidence API is missing", pytrace=False)
    return evidence_type(source=source, ticker=ticker)


def test_public_valid_shaped_proof_cannot_bypass_registry_issuance() -> None:
    api = sizing_api()
    forged = ResolvedSizingProduct(
        product=make_product(),
        source=CatalogSource(
            region=DeltaRegion.GLOBAL,
            environment=DeltaEnvironment.PROD,
        ),
        catalog_revision=999,
        catalog_content_sha256="0" * 64,
        risk_increasing=True,
    )

    with pytest.raises(api.ContractSizingError) as rejected:
        api.LinearContractSizer(forged)

    assert rejected.value.code == "contract_sizing"
    assert "999" not in str(rejected.value)
    assert "0" * 64 not in repr(rejected.value)


def test_direct_valid_shaped_snapshot_has_no_catalog_publication_issuance() -> None:
    api = sizing_api()
    snapshot = make_unpublished_snapshot()

    with pytest.raises(api.UnsupportedProductError):
        make_registry().resolve(
            snapshot=snapshot,
            product_id=1001,
            checked_at=BASE_TIME,
            risk_increasing=True,
        )


def test_mutating_published_snapshot_revision_invalidates_issuance() -> None:
    api = sizing_api()
    snapshot = make_snapshot()
    object.__setattr__(snapshot, "revision", snapshot.revision + 998)

    with pytest.raises(api.UnsupportedProductError):
        make_registry().resolve(
            snapshot=snapshot,
            product_id=1001,
            checked_at=BASE_TIME,
            risk_increasing=True,
        )


def test_mutating_fresh_until_cannot_revive_expired_published_snapshot() -> None:
    api = sizing_api()
    snapshot = make_snapshot(ttl=timedelta(seconds=1))
    checked_at = BASE_TIME + timedelta(seconds=2)
    object.__setattr__(snapshot, "fresh_until", checked_at + timedelta(minutes=5))

    with pytest.raises(api.UnsupportedProductError):
        make_registry().resolve(
            snapshot=snapshot,
            product_id=1001,
            checked_at=checked_at,
            risk_increasing=True,
        )


def test_real_catalog_refresh_issues_snapshot_resolvable_by_registry() -> None:
    snapshot = make_snapshot()
    sizer = make_registry().resolve(
        snapshot=snapshot,
        product_id=1001,
        checked_at=BASE_TIME,
        risk_increasing=True,
    )

    assert snapshot.revision == 1
    assert sizer.contracts_to_base_quantity(7) == Decimal("0.007")


@pytest.mark.parametrize(
    "boundary",
    [
        "contracts_to_notional_price",
        "quantity_request",
        "notional_request",
        "notional_price",
        "tick_price",
        "conversion_evidence",
        "quantization_evidence",
    ],
)
def test_hostile_decimal_subclass_never_executes_outside_safe_boundary(boundary: str) -> None:
    api = sizing_api()
    sizer = resolve()
    hostile = HostileDecimal("1")

    def invoke() -> object:
        if boundary == "contracts_to_notional_price":
            return sizer.contracts_to_notional(1, hostile)
        if boundary == "quantity_request":
            return sizer.quantity_to_contracts(
                hostile,
                rounding=api.ContractRounding.FLOOR,
            )
        if boundary == "notional_request":
            return sizer.notional_to_contracts(
                hostile,
                price=50000,
                price_basis=api.PriceBasis.MARK,
                rounding=api.ContractRounding.FLOOR,
            )
        if boundary == "notional_price":
            return sizer.notional_to_contracts(
                100,
                price=hostile,
                price_basis=api.PriceBasis.MARK,
                rounding=api.ContractRounding.FLOOR,
            )
        if boundary == "tick_price":
            return sizer.quantize_price(hostile, rounding=api.ContractRounding.FLOOR)
        if boundary == "conversion_evidence":
            return api.ContractConversion(
                requested=hostile,
                represented=1,
                contracts=1,
                remainder=0,
                contract_unit=1,
                unit=api.SizingUnit.BASE_QUANTITY,
                rounding=api.ContractRounding.EXACT,
                price_basis=None,
            )
        return api.PriceQuantization(
            requested=hostile,
            quantized=1,
            remainder=0,
            tick_size=1,
            rounding=api.ContractRounding.EXACT,
        )

    with pytest.raises(api.ContractSizingError) as rejected:
        invoke()
    for rendered in (
        str(rejected.value),
        repr(rejected.value),
        str(rejected.value.to_dict()),
    ):
        assert HOSTILE_DECIMAL_MARKER not in rendered


def test_bare_ticker_with_explicit_contradictory_family_fails_closed() -> None:
    api = sizing_api()
    with pytest.raises(api.ContractSizingError):
        resolve().select_fresh_price(
            make_ticker(contract_type=ContractType.SPOT),
            basis=api.PriceBasis.MARK,
            checked_at=BASE_TIME,
            max_age=timedelta(seconds=1),
        )


def test_bare_valid_ticker_is_rejected_without_source_evidence() -> None:
    api = sizing_api()
    with pytest.raises(api.ContractSizingError):
        resolve().select_fresh_price(
            make_ticker(contract_type=ContractType.UNKNOWN),
            basis=api.PriceBasis.MARK,
            checked_at=BASE_TIME,
            max_age=timedelta(seconds=1),
        )


def test_same_source_rest_ticker_evidence_selects_requested_basis() -> None:
    api = sizing_api()
    evidence = source_bound_ticker(
        source=CatalogSource(
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
        ),
        ticker=make_ticker(contract_type=ContractType.UNKNOWN),
    )

    assert resolve().select_fresh_price(
        evidence,
        basis=api.PriceBasis.MARK,
        checked_at=BASE_TIME,
        max_age=timedelta(seconds=1),
    ) == Decimal("50000")


@pytest.mark.parametrize(
    ("region", "environment"),
    [
        (DeltaRegion.GLOBAL, DeltaEnvironment.TESTNET),
        (DeltaRegion.INDIA, DeltaEnvironment.PROD),
    ],
)
def test_ticker_evidence_source_must_match_sizer_source(
    region: DeltaRegion,
    environment: DeltaEnvironment,
) -> None:
    api = sizing_api()
    evidence = source_bound_ticker(
        source=CatalogSource(region=region, environment=environment),
        ticker=make_ticker(contract_type=ContractType.UNKNOWN),
    )

    with pytest.raises(api.ContractSizingError):
        resolve().select_fresh_price(
            evidence,
            basis=api.PriceBasis.MARK,
            checked_at=BASE_TIME,
            max_age=timedelta(seconds=1),
        )


def test_source_bound_ticker_rejects_explicit_contract_family_contradiction() -> None:
    api = sizing_api()
    evidence = source_bound_ticker(
        source=CatalogSource(
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
        ),
        ticker=make_ticker(contract_type=ContractType.SPOT),
    )

    with pytest.raises(api.ContractSizingError):
        resolve().select_fresh_price(
            evidence,
            basis=api.PriceBasis.MARK,
            checked_at=BASE_TIME,
            max_age=timedelta(seconds=1),
        )
