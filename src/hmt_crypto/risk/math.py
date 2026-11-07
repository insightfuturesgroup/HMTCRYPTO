"""Context-independent exact arithmetic for risk limits."""

from __future__ import annotations

from decimal import Decimal

from hmt_crypto.risk.models import RiskInputError

_SCALE = 10**18
_MAX_SCALED = 10**38 - 1
_MAX_TUPLE_DIGITS = 38
_MIN_RAW_EXPONENT = -(_MAX_TUPLE_DIGITS - 1 + 18)
_MAX_RAW_EXPONENT = _MAX_TUPLE_DIGITS - 1


def scaled_amount(value: object) -> int:
    """Convert one exact NUMERIC(38,18) Decimal to a scaled integer."""
    if type(value) is not Decimal or not value.is_finite():
        raise RiskInputError() from None
    sign, raw_digits, exponent = value.as_tuple()
    if (
        type(sign) is not int
        or sign not in (0, 1)
        or type(raw_digits) is not tuple
        or not 1 <= len(raw_digits) <= _MAX_TUPLE_DIGITS
        or type(exponent) is not int
        or not _MIN_RAW_EXPONENT <= exponent <= _MAX_RAW_EXPONENT
    ):
        raise RiskInputError() from None
    integer_exponent: int = exponent
    digits: list[int] = []
    for raw_digit in raw_digits:
        if type(raw_digit) is not int or not 0 <= raw_digit <= 9:
            raise RiskInputError() from None
        digits.append(raw_digit)
    while integer_exponent < 0 and digits and digits[-1] == 0:
        digits.pop()
        integer_exponent += 1
    if integer_exponent < -18:
        raise RiskInputError() from None
    coefficient: int = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    scaled = coefficient * 10 ** (integer_exponent + 18)
    if sign:
        scaled = -scaled
    if not -_MAX_SCALED <= scaled <= _MAX_SCALED:
        raise RiskInputError() from None
    return int(scaled)


def amount_from_scaled(value: object) -> Decimal:
    """Restore an exact NUMERIC(38,18) Decimal from a scaled integer."""
    if type(value) is not int or not -_MAX_SCALED <= value <= _MAX_SCALED:
        raise RiskInputError() from None
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    whole, fraction = divmod(magnitude, _SCALE)
    if fraction == 0:
        return Decimal(f"{sign}{whole}")
    return Decimal(f"{sign}{whole}.{fraction:018d}")


def sum_amounts(values: object) -> Decimal:
    """Sum one exact tuple without consulting the ambient Decimal context."""
    if type(values) is not tuple:
        raise RiskInputError() from None
    total = 0
    for value in values:
        total += scaled_amount(value)
        if not -_MAX_SCALED <= total <= _MAX_SCALED:
            raise RiskInputError() from None
    return amount_from_scaled(total)


def multiply_amount(value: object, multiplier: object) -> Decimal:
    """Multiply one amount by an exact nonnegative contract count."""
    if type(multiplier) is not int or not 0 <= multiplier < 2**63:
        raise RiskInputError() from None
    result = scaled_amount(value) * multiplier
    if not -_MAX_SCALED <= result <= _MAX_SCALED:
        raise RiskInputError() from None
    return amount_from_scaled(result)


def conservative_product(left: object, right: object) -> Decimal:
    """Multiply two nonnegative amounts, rounding any sub-scale remainder upward."""
    lhs = scaled_amount(left)
    rhs = scaled_amount(right)
    if lhs < 0 or rhs < 0:
        raise RiskInputError() from None
    numerator = lhs * rhs
    result = (numerator + _SCALE - 1) // _SCALE
    return amount_from_scaled(result)


def conservative_division(numerator: object, denominator: object) -> Decimal:
    """Divide nonnegative amounts, rounding any sub-scale remainder upward."""
    top = scaled_amount(numerator)
    bottom = scaled_amount(denominator)
    if top < 0 or bottom <= 0:
        raise RiskInputError() from None
    result = (top * _SCALE + bottom - 1) // bottom
    return amount_from_scaled(result)


def with_bps_buffer(value: object, basis_points: object) -> Decimal:
    """Add a nonnegative basis-point buffer, rounding conservatively upward."""
    amount = scaled_amount(value)
    bps = scaled_amount(basis_points)
    if amount < 0 or bps < 0:
        raise RiskInputError() from None
    numerator = amount * (10_000 * _SCALE + bps)
    result = (numerator + 10_000 * _SCALE - 1) // (10_000 * _SCALE)
    return amount_from_scaled(result)


def conservative_bps(value: object, reference: object) -> Decimal:
    """Return absolute deviation in basis points, rounded upward at 18 decimals."""
    observed = scaled_amount(value)
    baseline = scaled_amount(reference)
    if observed <= 0 or baseline <= 0:
        raise RiskInputError() from None
    numerator = abs(observed - baseline) * 10_000 * _SCALE
    scaled = (numerator + baseline - 1) // baseline
    if scaled > _MAX_SCALED:
        raise RiskInputError() from None
    return amount_from_scaled(scaled)


__all__ = [
    "amount_from_scaled",
    "conservative_bps",
    "conservative_division",
    "conservative_product",
    "multiply_amount",
    "scaled_amount",
    "sum_amounts",
    "with_bps_buffer",
]
