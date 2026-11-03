"""Exact linear-contract arithmetic, rounding evidence, and boundaries."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, Rounded, localcontext

import pytest
from contract_sizing_test_support import resolve, sizing_api

pytestmark = pytest.mark.unit


def test_sizing_types_are_available_from_the_delta_public_package() -> None:
    api = sizing_api()
    from hmt_crypto.exchange import delta

    assert delta.ContractSizerRegistry is api.ContractSizerRegistry
    assert delta.LinearContractSizer is api.LinearContractSizer
    assert delta.ContractConversion is api.ContractConversion
    assert delta.PriceQuantization is api.PriceQuantization
    assert delta.SourceBoundTicker is api.SourceBoundTicker


def test_known_linear_vectors_and_limits_are_not_misused() -> None:
    api = sizing_api()
    sizer = resolve()

    assert sizer.contracts_to_base_quantity(7) == Decimal("0.007")
    assert sizer.contracts_to_notional(7, Decimal("50000")) == Decimal("350")

    quantity = sizer.quantity_to_contracts(Decimal("0.0075"), rounding=api.ContractRounding.FLOOR)
    assert quantity.contracts == 7
    assert quantity.requested == Decimal("0.0075")
    assert quantity.represented == Decimal("0.007")
    assert quantity.remainder == Decimal("0.0005")
    assert quantity.contract_unit == Decimal("0.001")
    assert quantity.unit is api.SizingUnit.BASE_QUANTITY
    assert quantity.price_basis is None

    notional = sizer.notional_to_contracts(
        Decimal("375"),
        price=Decimal("50000"),
        price_basis=api.PriceBasis.MARK,
        rounding=api.ContractRounding.FLOOR,
    )
    assert notional.contracts == 7
    assert notional.represented == Decimal("350")
    assert notional.remainder == Decimal("25")
    assert notional.contract_unit == Decimal("50")
    assert notional.unit is api.SizingUnit.QUOTE_NOTIONAL
    assert notional.price_basis is api.PriceBasis.MARK

    # Catalogue position and leverage fields are retained metadata, not sizing formulae.
    assert sizer.contracts_to_base_quantity(6000) == Decimal("6")
    assert sizer.contracts_to_notional(6000, Decimal("50000")) == Decimal("300000")


def test_exact_mode_accepts_only_integral_contract_representation() -> None:
    api = sizing_api()
    sizer = resolve()

    exact_quantity = sizer.quantity_to_contracts("0.007", rounding=api.ContractRounding.EXACT)
    exact_notional = sizer.notional_to_contracts(
        350,
        price="50000",
        price_basis=api.PriceBasis.MARK,
        rounding=api.ContractRounding.EXACT,
    )
    assert exact_quantity.contracts == exact_notional.contracts == 7
    assert exact_quantity.remainder == exact_notional.remainder == Decimal(0)

    for operation in (
        lambda: sizer.quantity_to_contracts("0.0075", rounding=api.ContractRounding.EXACT),
        lambda: sizer.notional_to_contracts(
            "375",
            price="50000",
            price_basis=api.PriceBasis.MARK,
            rounding=api.ContractRounding.EXACT,
        ),
    ):
        with pytest.raises(api.ContractSizingError) as error:
            operation()
        assert error.value.code == "contract_sizing"


def test_floor_never_rounds_up_or_returns_zero_and_exposes_every_remainder() -> None:
    api = sizing_api()
    sizer = resolve()

    for requested in ("0.0001", "0.000999999"):
        with pytest.raises(api.ContractSizingError):
            sizer.quantity_to_contracts(requested, rounding=api.ContractRounding.FLOOR)

    conversion = sizer.quantity_to_contracts("0.002999", rounding=api.ContractRounding.FLOOR)
    assert conversion.contracts == 2
    assert conversion.represented < conversion.requested
    assert Decimal(0) <= conversion.remainder < conversion.contract_unit
    assert conversion.represented + conversion.remainder == conversion.requested


def test_tick_quantization_is_explicit_exact_or_floor_evidence() -> None:
    api = sizing_api()
    sizer = resolve()

    floor = sizer.quantize_price("50123.7", rounding=api.ContractRounding.FLOOR)
    assert floor.requested == Decimal("50123.7")
    assert floor.quantized == Decimal("50123.5")
    assert floor.remainder == Decimal("0.2")
    assert floor.tick_size == Decimal("0.5")
    assert floor.rounding is api.ContractRounding.FLOOR

    exact = sizer.quantize_price(50123, rounding=api.ContractRounding.EXACT)
    assert exact.quantized == Decimal("50123")
    assert exact.remainder == Decimal(0)

    with pytest.raises(api.ContractSizingError):
        sizer.quantize_price("50123.7", rounding=api.ContractRounding.EXACT)
    with pytest.raises(api.ContractSizingError):
        sizer.quantize_price("0.2", rounding=api.ContractRounding.FLOOR)


@pytest.mark.parametrize("bad", [True, False, 1.0, 7.0, 0, -1, "7", Decimal("7")])
def test_contract_counts_are_strict_positive_integers(bad: object) -> None:
    api = sizing_api()
    sizer = resolve()
    with pytest.raises(api.ContractSizingError):
        sizer.contracts_to_base_quantity(bad)
    with pytest.raises(api.ContractSizingError):
        sizer.contracts_to_notional(bad, 50000)


@pytest.mark.parametrize(
    "bad",
    [
        True,
        False,
        1.25,
        float("inf"),
        Decimal("NaN"),
        Decimal("Infinity"),
        "",
        "not-a-number",
        "1e1000",
        "1" * 300,
        " " * 300 + "1",
        object(),
    ],
)
def test_numeric_boundaries_reject_coercion_nonfinite_and_resource_abuse(bad: object) -> None:
    api = sizing_api()
    sizer = resolve()
    operations = (
        lambda: sizer.quantity_to_contracts(bad, rounding=api.ContractRounding.FLOOR),
        lambda: sizer.notional_to_contracts(
            100,
            price=bad,
            price_basis=api.PriceBasis.MARK,
            rounding=api.ContractRounding.FLOOR,
        ),
        lambda: sizer.quantize_price(bad, rounding=api.ContractRounding.FLOOR),
    )
    for operation in operations:
        with pytest.raises(api.ContractSizingError):
            operation()


def test_rounding_and_price_basis_are_exact_enums_not_strings() -> None:
    api = sizing_api()
    sizer = resolve()
    assert tuple(api.ContractRounding) == (
        api.ContractRounding.EXACT,
        api.ContractRounding.FLOOR,
    )
    assert tuple(api.PriceBasis) == (
        api.PriceBasis.MARK,
        api.PriceBasis.BEST_BID,
        api.PriceBasis.BEST_ASK,
        api.PriceBasis.CLOSE,
    )

    with pytest.raises(api.ContractSizingError):
        sizer.quantity_to_contracts("0.007", rounding="exact")
    with pytest.raises(api.ContractSizingError):
        sizer.notional_to_contracts(
            350,
            price=50000,
            price_basis="mark",
            rounding=api.ContractRounding.EXACT,
        )


def test_arithmetic_is_independent_of_hostile_global_decimal_context() -> None:
    api = sizing_api()
    sizer = resolve()

    with localcontext() as context:
        context.prec = 1
        context.rounding = "ROUND_UP"
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        quantity = sizer.contracts_to_base_quantity(7)
        notional = sizer.contracts_to_notional(7, "50000")
        conversion = sizer.notional_to_contracts(
            "375",
            price="50000",
            price_basis=api.PriceBasis.MARK,
            rounding=api.ContractRounding.FLOOR,
        )
        tick = sizer.quantize_price("50123.7", rounding=api.ContractRounding.FLOOR)

    assert quantity == Decimal("0.007")
    assert notional == Decimal("350")
    assert conversion.contracts == 7
    assert conversion.remainder == Decimal("25")
    assert tick.quantized == Decimal("50123.5")


def test_evidence_models_are_immutable_and_reject_direct_inconsistency_safely() -> None:
    api = sizing_api()
    conversion = resolve().quantity_to_contracts("0.0075", rounding=api.ContractRounding.FLOOR)
    quantization = resolve().quantize_price("50123.7", rounding=api.ContractRounding.FLOOR)

    with pytest.raises((FrozenInstanceError, AttributeError)):
        conversion.contracts = 99
    with pytest.raises((FrozenInstanceError, AttributeError)):
        quantization.quantized = Decimal("999")

    secret = "DO-NOT-LEAK-SIZING-INPUT"
    with pytest.raises(api.ContractSizingError) as forged_conversion:
        api.ContractConversion(
            requested=secret,
            represented="0.007",
            contracts=7,
            remainder="0",
            contract_unit="0.001",
            unit=api.SizingUnit.BASE_QUANTITY,
            rounding=api.ContractRounding.EXACT,
            price_basis=None,
        )
    with pytest.raises(api.ContractSizingError) as forged_tick:
        api.PriceQuantization(
            requested="10.2",
            quantized="10",
            remainder="0.3",
            tick_size="0.5",
            rounding=api.ContractRounding.FLOOR,
        )
    with pytest.raises(api.ContractSizingError):
        api.ContractConversion(
            requested="0.007",
            represented="0.006",
            contracts=7,
            remainder="0.001",
            contract_unit="0.001",
            unit=api.SizingUnit.BASE_QUANTITY,
            rounding=api.ContractRounding.FLOOR,
            price_basis=None,
        )
    for error in (forged_conversion.value, forged_tick.value):
        assert secret not in str(error)
        assert secret not in repr(error)
        assert secret not in str(error.to_dict())


def test_evidence_resource_bound_treats_exact_integer_and_decimal_forms_equally() -> None:
    api = sizing_api()
    large_exact_integer = 10**199
    result = api.ContractConversion(
        requested=large_exact_integer,
        represented=Decimal(large_exact_integer),
        contracts=1,
        remainder=0,
        contract_unit="1e199",
        unit=api.SizingUnit.BASE_QUANTITY,
        rounding=api.ContractRounding.EXACT,
        price_basis=None,
    )
    assert result.requested == Decimal("1e199")
