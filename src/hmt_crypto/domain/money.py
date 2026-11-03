"""Exact numeric and UTC parsing at exchange/domain boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator

_MAX_EPOCH_SECONDS = 253_402_300_799


def parse_decimal(value: object) -> Decimal:
    """Return an exact finite Decimal without accepting binary floating point."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("value must be a finite decimal, integer, or decimal string")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must be a finite decimal")
        try:
            result = Decimal(stripped)
        except InvalidOperation as error:
            raise ValueError("value must be a finite decimal") from error
    else:
        raise ValueError("value must be a finite decimal, integer, or decimal string")
    if not result.is_finite():
        raise ValueError("value must be a finite decimal")
    return result


def _positive(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("value must be a positive finite decimal")
    return value


def _nonnegative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("value must be a nonnegative finite decimal")
    return value


def parse_utc_datetime(value: object) -> datetime:
    """Parse an aware datetime, aware ISO-8601 string, or Unix seconds into UTC."""
    parsed: datetime
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("timestamp must be a UTC-aware datetime, ISO string, or Unix seconds")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, int):
        if value < 0 or value > _MAX_EPOCH_SECONDS:
            raise ValueError("timestamp must be UTC-aware Unix seconds, not milliseconds")
        try:
            parsed = datetime.fromtimestamp(value, tz=UTC)
        except (OverflowError, OSError, ValueError) as error:
            raise ValueError("timestamp must be valid UTC-aware Unix seconds") from error
    elif isinstance(value, str):
        stripped = value.strip()
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("timestamp must be a UTC-aware ISO-8601 string") from error
    else:
        raise ValueError("timestamp must be a UTC-aware datetime, ISO string, or Unix seconds")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be UTC-aware")
    return parsed.astimezone(UTC)


ExactDecimal = Annotated[Decimal, BeforeValidator(parse_decimal)]
PositiveDecimal = Annotated[
    Decimal,
    BeforeValidator(parse_decimal),
    AfterValidator(_positive),
]
NonNegativeDecimal = Annotated[
    Decimal,
    BeforeValidator(parse_decimal),
    AfterValidator(_nonnegative),
]
UtcDateTime = Annotated[datetime, BeforeValidator(parse_utc_datetime)]
