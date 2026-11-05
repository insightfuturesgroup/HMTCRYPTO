"""Exact arithmetic for the authority-free risk evaluator."""

from decimal import Decimal, localcontext
from time import perf_counter

import pytest

from hmt_crypto.risk.math import (
    amount_from_scaled,
    conservative_bps,
    conservative_division,
    conservative_product,
    multiply_amount,
    scaled_amount,
    sum_amounts,
    with_bps_buffer,
)
from hmt_crypto.risk.models import RiskInputError


def test_scaled_amount_preserves_numeric_38_18_boundaries_exactly() -> None:
    maximum = Decimal("99999999999999999999.999999999999999999")
    assert amount_from_scaled(scaled_amount(maximum)) == maximum
    assert amount_from_scaled(scaled_amount(Decimal("-0.000000000000000001"))) == Decimal(
        "-0.000000000000000001"
    )


@pytest.mark.parametrize(
    "value",
    [
        True,
        1,
        "1",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e-19"),
        Decimal("100000000000000000000"),
    ],
)
def test_scaled_amount_rejects_coercion_nonfinite_precision_and_overflow(value: object) -> None:
    with pytest.raises(RiskInputError):
        scaled_amount(value)


def test_scaled_amount_rejects_overencoded_decimal_tuples_before_coefficient_work() -> None:
    overencoded_one = Decimal("1." + "0" * 38)

    with pytest.raises(RiskInputError):
        scaled_amount(overencoded_one)


def test_scaled_amount_rejects_extreme_exponents_in_constant_bounded_time() -> None:
    extreme = Decimal("1e1000000")

    started = perf_counter()
    with pytest.raises(RiskInputError):
        scaled_amount(extreme)

    assert perf_counter() - started < 0.02


def test_sums_and_products_ignore_ambient_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 3
        assert sum_amounts(
            (Decimal("1.000000000000000001"), Decimal("2.000000000000000002"))
        ) == Decimal("3.000000000000000003")
        assert multiply_amount(Decimal("1.000000000000000001"), 3) == Decimal(
            "3.000000000000000003"
        )


def test_conservative_bps_rounds_up_without_understating_the_collar() -> None:
    assert conservative_bps(Decimal("101"), Decimal("100")) == Decimal("100")
    assert conservative_bps(Decimal("99"), Decimal("100")) == Decimal("100")
    assert conservative_bps(Decimal("1.000000000000000001"), Decimal("1")) == Decimal(
        "0.000000000000010000"
    )


@pytest.mark.parametrize("reference", [Decimal("0"), Decimal("-1")])
def test_conservative_bps_requires_a_positive_reference(reference: Decimal) -> None:
    with pytest.raises(RiskInputError):
        conservative_bps(Decimal("1"), reference)


def test_products_divisions_and_buffers_round_only_toward_more_risk() -> None:
    assert conservative_product(Decimal("0.000000000000000001"), Decimal("0.5")) == Decimal(
        "0.000000000000000001"
    )
    assert conservative_division(Decimal("1"), Decimal("3")) == Decimal("0.333333333333333334")
    assert with_bps_buffer(Decimal("1"), Decimal("1")) == Decimal("1.0001")


@pytest.mark.parametrize(
    "function,args",
    [
        (conservative_product, (Decimal("-1"), Decimal("1"))),
        (conservative_division, (Decimal("1"), Decimal("0"))),
        (with_bps_buffer, (Decimal("1"), Decimal("-1"))),
    ],
)
def test_conservative_amount_helpers_reject_unsafe_signs(
    function: object, args: tuple[Decimal, Decimal]
) -> None:
    assert callable(function)
    with pytest.raises(RiskInputError):
        function(*args)
