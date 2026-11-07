"""Exact bounded value helpers used only by the pure backtest package."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from hmt_crypto.backtest.errors import BacktestInputError

_MAX_I63 = 2**63 - 1
_HEX = frozenset("0123456789abcdef")


def exact_int(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = _MAX_I63,
) -> int:
    """Copy an exact bounded integer, rejecting booleans and subclasses."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise BacktestInputError("integer is outside the supported exact range") from None
    return value


def exact_decimal(
    value: object,
    *,
    positive: bool = False,
    nonnegative: bool = False,
    absolute_max: Decimal | None = None,
) -> Decimal:
    """Copy one finite Decimal within the shared 38-digit/18-scale envelope."""
    if type(value) is not Decimal or not value.is_finite():
        raise BacktestInputError("value must be a finite exact Decimal") from None
    parts = value.as_tuple()
    exponent = parts.exponent
    if (
        type(exponent) is not int
        or len(parts.digits) > 38
        or exponent < -18
        or value.adjusted() > 19
        or positive
        and value <= 0
        or nonnegative
        and value < 0
        or absolute_max is not None
        and value.copy_abs() > absolute_max
    ):
        raise BacktestInputError("Decimal is outside the supported exact range") from None
    return value.copy_abs().copy_negate() if value.is_signed() else value.copy_abs()


def exact_utc(value: object) -> datetime:
    """Copy an exact timezone-aware UTC datetime."""
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise BacktestInputError("timestamp must be an exact UTC datetime") from None
    if value.utcoffset() != UTC.utcoffset(value):
        raise BacktestInputError("timestamp must be UTC") from None
    return datetime(
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        tzinfo=UTC,
        fold=value.fold,
    )


def exact_uuid(value: object) -> UUID:
    """Copy an exact UUID without accepting subclasses."""
    if type(value) is not UUID:
        raise BacktestInputError("account identity must be an exact UUID") from None
    return UUID(int=value.int)


def exact_text(value: object, *, maximum: int = 128) -> str:
    """Copy a bounded printable ASCII provenance label."""
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or not value.isascii()
        or not value.isprintable()
    ):
        raise BacktestInputError("provenance label must be bounded printable ASCII") from None
    return str(value)


def exact_sha256(value: object) -> str:
    """Copy a canonical lowercase SHA-256 digest."""
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise BacktestInputError("source digest must be lowercase SHA-256") from None
    return str(value)


__all__: list[str] = []
