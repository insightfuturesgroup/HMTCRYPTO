"""Bounded deterministic properties for exact linear sizing."""

from __future__ import annotations

from decimal import Decimal, Inexact, Rounded, localcontext

import pytest
from contract_sizing_test_support import make_product, resolve, sizing_api
from hypothesis import given, settings
from hypothesis import strategies as st

from hmt_crypto.domain import ContractType

pytestmark = pytest.mark.unit

CASES = settings(max_examples=60, deadline=None, derandomize=True)


def exact_decimal(coefficient: int, exponent: int) -> Decimal:
    """Construct a nonnegative Decimal without invoking context arithmetic."""
    digits = tuple(int(character) for character in str(coefficient))
    return Decimal((0, digits, exponent))


@CASES
@given(
    contracts=st.integers(min_value=1, max_value=1_000_000),
    coefficient=st.integers(min_value=1, max_value=9999),
    scale=st.integers(min_value=0, max_value=8),
    price_coefficient=st.integers(min_value=1, max_value=1_000_000),
)
def test_quantity_and_notional_round_trip_exactly(
    contracts: int, coefficient: int, scale: int, price_coefficient: int
) -> None:
    api = sizing_api()
    contract_value = exact_decimal(coefficient, -scale)
    price = exact_decimal(price_coefficient, -2)
    sizer = resolve(make_product(contract_value=contract_value))

    quantity = sizer.contracts_to_base_quantity(contracts)
    notional = sizer.contracts_to_notional(contracts, price)
    quantity_result = sizer.quantity_to_contracts(quantity, rounding=api.ContractRounding.EXACT)
    notional_result = sizer.notional_to_contracts(
        notional,
        price=price,
        price_basis=api.PriceBasis.MARK,
        rounding=api.ContractRounding.EXACT,
    )

    assert quantity_result.contracts == contracts
    assert notional_result.contracts == contracts
    assert quantity_result.represented == quantity
    assert notional_result.represented == notional


@CASES
@given(
    left=st.integers(min_value=1, max_value=1_000_000),
    right=st.integers(min_value=1, max_value=1_000_000),
)
def test_contract_conversion_is_additive(left: int, right: int) -> None:
    sizer = resolve()
    combined_quantity = sizer.contracts_to_base_quantity(left + right)
    separate_quantity = sizer.contracts_to_base_quantity(left) + sizer.contracts_to_base_quantity(
        right
    )
    combined_notional = sizer.contracts_to_notional(left + right, "50000")
    separate_notional = sizer.contracts_to_notional(left, "50000") + sizer.contracts_to_notional(
        right, "50000"
    )
    assert combined_quantity == separate_quantity
    assert combined_notional == separate_notional


@CASES
@given(milli_units=st.integers(min_value=1, max_value=1_000_000))
def test_exact_succeeds_if_and_only_if_request_is_divisible(milli_units: int) -> None:
    api = sizing_api()
    sizer = resolve(make_product(contract_value=Decimal("0.007")))
    request = exact_decimal(milli_units, -3)

    if milli_units % 7 == 0:
        result = sizer.quantity_to_contracts(request, rounding=api.ContractRounding.EXACT)
        assert result.contracts == milli_units // 7
        assert result.remainder == 0
    else:
        with pytest.raises(api.ContractSizingError):
            sizer.quantity_to_contracts(request, rounding=api.ContractRounding.EXACT)


@CASES
@given(quote_units=st.integers(min_value=1, max_value=1_000_000))
def test_notional_exact_succeeds_if_and_only_if_request_is_divisible(
    quote_units: int,
) -> None:
    api = sizing_api()
    sizer = resolve()

    if quote_units % 50 == 0:
        result = sizer.notional_to_contracts(
            quote_units,
            price=50000,
            price_basis=api.PriceBasis.MARK,
            rounding=api.ContractRounding.EXACT,
        )
        assert result.contracts == quote_units // 50
        assert result.remainder == 0
    else:
        with pytest.raises(api.ContractSizingError):
            sizer.notional_to_contracts(
                quote_units,
                price=50000,
                price_basis=api.PriceBasis.MARK,
                rounding=api.ContractRounding.EXACT,
            )


@CASES
@given(milli_units=st.integers(min_value=7, max_value=1_000_000))
def test_floor_bounds_remainder_and_monotonicity(milli_units: int) -> None:
    api = sizing_api()
    sizer = resolve(make_product(contract_value=Decimal("0.007")))
    request = exact_decimal(milli_units, -3)
    next_request = exact_decimal(milli_units + 1, -3)

    result = sizer.quantity_to_contracts(request, rounding=api.ContractRounding.FLOOR)
    next_result = sizer.quantity_to_contracts(next_request, rounding=api.ContractRounding.FLOOR)

    assert result.contracts <= next_result.contracts
    assert result.represented <= result.requested
    assert result.requested == result.represented + result.remainder
    assert Decimal(0) <= result.remainder < result.contract_unit


@CASES
@given(tenths=st.integers(min_value=5, max_value=10_000_000))
def test_tick_floor_bounds_and_remainder(tenths: int) -> None:
    api = sizing_api()
    request = exact_decimal(tenths, -1)
    result = resolve().quantize_price(request, rounding=api.ContractRounding.FLOOR)

    assert result.quantized <= result.requested
    assert result.requested == result.quantized + result.remainder
    assert Decimal(0) <= result.remainder < result.tick_size


@CASES
@given(
    contracts=st.integers(min_value=1, max_value=1_000_000),
    coefficient=st.integers(min_value=1, max_value=9999),
    scale=st.integers(min_value=0, max_value=8),
)
def test_global_context_never_changes_exact_outputs(
    contracts: int, coefficient: int, scale: int
) -> None:
    api = sizing_api()
    contract_value = exact_decimal(coefficient, -scale)
    product = make_product(contract_value=contract_value)
    baseline = resolve(product).contracts_to_base_quantity(contracts)

    with localcontext() as context:
        context.prec = 1
        context.rounding = "ROUND_UP"
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        hostile_sizer = resolve(product)
        hostile = hostile_sizer.contracts_to_base_quantity(contracts)
        round_trip = hostile_sizer.quantity_to_contracts(
            hostile, rounding=api.ContractRounding.EXACT
        )

    assert hostile == baseline
    assert round_trip.contracts == contracts


@CASES
@given(exponent=st.integers(min_value=200, max_value=1000))
def test_unsupported_family_rejects_before_pathological_numeric_metadata(
    exponent: int,
) -> None:
    api = sizing_api()
    product = make_product(
        contract_type=ContractType.CALL_OPTIONS,
        contract_value=Decimal(f"1E+{exponent}"),
    )
    with pytest.raises(api.UnsupportedProductError):
        resolve(product)
