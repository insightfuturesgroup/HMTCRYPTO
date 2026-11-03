"""Bounded exact-decimal arithmetic for verified linear contract sizing.

Inputs are limited to 128 coefficient digits, an absolute exponent of 128, and
38-digit integer quotients. These bounds cover exchange metadata while preventing
pathological powers or coefficients. Arithmetic uses integer coefficients and
exponents, so it is independent of the process-global :mod:`decimal` context.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

MAX_INPUT_DIGITS = 128
MAX_INPUT_ABS_EXPONENT = 128
MAX_DECIMAL_TEXT_LENGTH = 256
MAX_RESULT_DIGITS = 384
MAX_RESULT_ABS_EXPONENT = 256
MAX_INTEGER_QUOTIENT = 10**38 - 1


class NumericContractError(ValueError):
    """Internal marker for safe normalization into ContractSizingError."""


def _bounded_decimal(
    value: object,
    *,
    positive: bool,
    result: bool,
) -> Decimal:
    max_digits = MAX_RESULT_DIGITS if result else MAX_INPUT_DIGITS
    max_exponent = MAX_RESULT_ABS_EXPONENT if result else MAX_INPUT_ABS_EXPONENT
    max_text = MAX_RESULT_DIGITS + MAX_RESULT_ABS_EXPONENT + 16
    if isinstance(value, bool) or isinstance(value, float):
        raise NumericContractError
    if type(value) is Decimal:
        parsed = value
    elif type(value) is int:
        if value.bit_length() > max_digits * 4:
            raise NumericContractError
        parsed = Decimal(value)
    elif type(value) is str:
        if len(value) > (max_text if result else MAX_DECIMAL_TEXT_LENGTH):
            raise NumericContractError
        stripped = value.strip()
        if not stripped:
            raise NumericContractError
        try:
            parsed = Decimal(stripped)
        except InvalidOperation:
            raise NumericContractError from None
    else:
        raise NumericContractError
    if not parsed.is_finite():
        raise NumericContractError
    sign, digits, exponent = parsed.as_tuple()
    if not isinstance(exponent, int):
        raise NumericContractError
    if len(digits) > max_digits or abs(exponent) > max_exponent:
        raise NumericContractError
    coefficient = _coefficient(digits)
    if sign and coefficient:
        raise NumericContractError
    if positive and coefficient == 0:
        raise NumericContractError
    return _from_parts(coefficient, exponent)


def positive_decimal(value: object) -> Decimal:
    """Parse one bounded positive exact operand without implicit coercion."""
    return _bounded_decimal(value, positive=True, result=False)


def nonnegative_result_decimal(value: object) -> Decimal:
    """Parse one bounded nonnegative exact result/evidence value."""
    return _bounded_decimal(value, positive=False, result=True)


def positive_result_decimal(value: object) -> Decimal:
    """Parse one bounded positive exact result/evidence value."""
    return _bounded_decimal(value, positive=True, result=True)


def positive_integer(value: object) -> int:
    """Return a strict positive bounded integer, rejecting bool and subclasses."""
    if type(value) is not int or value <= 0 or value > MAX_INTEGER_QUOTIENT:
        raise NumericContractError
    return value


def _coefficient(digits: tuple[int, ...]) -> int:
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    return coefficient


def _parts(value: Decimal) -> tuple[int, int]:
    sign, digits, exponent = value.as_tuple()
    if sign or not isinstance(exponent, int):
        raise NumericContractError
    coefficient = _coefficient(digits)
    if coefficient == 0:
        return 0, 0
    while coefficient % 10 == 0:
        coefficient //= 10
        exponent += 1
    return coefficient, exponent


def _from_parts(coefficient: int, exponent: int) -> Decimal:
    if coefficient == 0:
        return Decimal(0)
    if coefficient < 0:
        raise NumericContractError
    digits = tuple(int(character) for character in str(coefficient))
    if len(digits) > MAX_RESULT_DIGITS or abs(exponent) > MAX_RESULT_ABS_EXPONENT:
        raise NumericContractError
    return Decimal((0, digits, exponent))


def multiply(left: Decimal, right: Decimal) -> Decimal:
    """Multiply two validated nonnegative decimals exactly."""
    left_coefficient, left_exponent = _parts(left)
    right_coefficient, right_exponent = _parts(right)
    return _from_parts(
        left_coefficient * right_coefficient,
        left_exponent + right_exponent,
    )


def multiply_integer(value: Decimal, multiplier: int) -> Decimal:
    """Multiply a validated decimal by a validated positive integer exactly."""
    checked = positive_integer(multiplier)
    coefficient, exponent = _parts(value)
    return _from_parts(coefficient * checked, exponent)


def add(left: Decimal, right: Decimal) -> Decimal:
    """Add two validated nonnegative decimals exactly."""
    left_coefficient, left_exponent = _parts(left)
    right_coefficient, right_exponent = _parts(right)
    common_exponent = min(left_exponent, right_exponent)
    total = left_coefficient * 10 ** (left_exponent - common_exponent)
    total += right_coefficient * 10 ** (right_exponent - common_exponent)
    return _from_parts(total, common_exponent)


def floor_division(requested: Decimal, unit: Decimal) -> tuple[int, Decimal, Decimal]:
    """Return integer quotient, represented amount, and exact remainder."""
    requested_coefficient, requested_exponent = _parts(requested)
    unit_coefficient, unit_exponent = _parts(unit)
    if unit_coefficient == 0:
        raise NumericContractError
    common_exponent = min(requested_exponent, unit_exponent)
    numerator = requested_coefficient * 10 ** (requested_exponent - common_exponent)
    denominator = unit_coefficient * 10 ** (unit_exponent - common_exponent)
    quotient, remainder = divmod(numerator, denominator)
    if quotient > MAX_INTEGER_QUOTIENT:
        raise NumericContractError
    return (
        quotient,
        _from_parts(quotient * denominator, common_exponent),
        _from_parts(remainder, common_exponent),
    )


def equal(left: Decimal, right: Decimal) -> bool:
    """Compare two finite exact decimals without context arithmetic."""
    return _parts(left) == _parts(right)


def is_zero(value: Decimal) -> bool:
    """Return whether an exact finite decimal is zero."""
    coefficient, _ = _parts(value)
    return coefficient == 0


__all__ = [
    "NumericContractError",
    "add",
    "equal",
    "floor_division",
    "is_zero",
    "multiply",
    "multiply_integer",
    "nonnegative_result_decimal",
    "positive_decimal",
    "positive_integer",
    "positive_result_decimal",
]
