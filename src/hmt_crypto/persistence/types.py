"""Exact SQLAlchemy bind/result boundaries for Decimal and UTC datetime values.

Raw SQL bypasses these adapters. PostgreSQL NUMERIC rounds excess scale before
CHECK/trigger inspection; adapter rejection is not raw-SQL precision enforcement.
SQLite canonical text has no numeric SQL ordering/arithmetic semantics. Use the
domain's Decimal arithmetic or PostgreSQL when numeric SQL operations are needed.
"""

from datetime import UTC, datetime, timezone
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, Numeric, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

from hmt_crypto.persistence.errors import PersistenceValueError


def _decimal_text(value: object, precision: int, scale: int) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise PersistenceValueError()
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast(int, raw_exponent)
    if not any(digits):
        return "0"
    end = len(digits)
    while end > 1 and digits[end - 1] == 0:
        end -= 1
        exponent += 1
    if -exponent > scale or end + exponent > precision - scale:
        raise PersistenceValueError()
    return format(Decimal((sign, digits[:end], exponent)), "f")


def _utc(value: object) -> datetime:
    if type(value) is not datetime or type(value.tzinfo) not in (timezone, ZoneInfo):
        raise PersistenceValueError()
    try:
        return value.astimezone(UTC)
    except Exception:
        raise PersistenceValueError() from None


class ExactDecimal(TypeDecorator[Decimal]):
    """Require an explicit NUMERIC precision/scale and reject loss before binding.

    Finite exact Decimal instances only; trailing zeros do not consume scale.
    Tuple validation and fixed formatting are independent of ambient context.
    The maximum explicit precision matches PostgreSQL's limit of 1,000.
    """

    impl = Text
    cache_ok = True

    def __init__(self, precision: int, scale: int) -> None:
        # SQLAlchemy 2.0 derives cache identity from named positional parameters;
        # keyword-only parameters are omitted and would conflate distinct scales.
        if (
            type(precision) is not int
            or type(scale) is not int
            or not 1 <= precision <= 1000
            or not 0 <= scale <= precision
        ):
            raise PersistenceValueError()
        self.precision = precision
        self.scale = scale
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        """Select native exact NUMERIC or canonical SQLite TEXT."""
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        raise PersistenceValueError()

    def process_bind_param(self, value: object, dialect: Dialect) -> Decimal | str | None:
        """Validate before driver coercion; reject float, bool and hostile subclasses."""
        if value is None:
            return None
        encoded = _decimal_text(value, self.precision, self.scale)
        if dialect.name == "sqlite":
            return encoded
        if dialect.name == "postgresql":
            return Decimal(encoded)
        raise PersistenceValueError()

    def process_result_value(self, value: object, dialect: Dialect) -> Decimal | None:
        """Validate native numerics and reject corrupt/noncanonical SQLite text."""
        if value is None:
            return None
        if dialect.name == "postgresql":
            return Decimal(_decimal_text(value, self.precision, self.scale))
        if dialect.name != "sqlite" or type(value) is not str:
            raise PersistenceValueError()
        if len(value) > self.precision + self.scale + 3:
            raise PersistenceValueError()
        try:
            result = Decimal(value)
        except Exception:
            raise PersistenceValueError() from None
        if _decimal_text(result, self.precision, self.scale) != value:
            raise PersistenceValueError()
        return result


class UTCDateTime(TypeDecorator[datetime]):
    """Store aware datetimes in UTC, preserving all microsecond digits.

    Accept only exact datetime with builtin timezone or ZoneInfo tzinfo; custom
    timezone callbacks and hostile datetime subclasses are never evaluated.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        """Select TIMESTAMPTZ on PostgreSQL or canonical UTC text on SQLite."""
        if dialect.name == "postgresql":
            return dialect.type_descriptor(DateTime(timezone=True))
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        raise PersistenceValueError()

    def process_bind_param(self, value: object, dialect: Dialect) -> datetime | str | None:
        """Reject naive/hostile inputs, then normalize valid aware values to UTC."""
        if value is None:
            return None
        result = _utc(value)
        if dialect.name == "sqlite":
            return result.isoformat(timespec="microseconds")
        if dialect.name == "postgresql":
            return result
        raise PersistenceValueError()

    def process_result_value(self, value: object, dialect: Dialect) -> datetime | None:
        """Normalize native timestamps and reject corrupt/noncanonical SQLite text."""
        if value is None:
            return None
        if dialect.name == "postgresql":
            return _utc(value)
        if dialect.name != "sqlite" or type(value) is not str or len(value) != 32:
            raise PersistenceValueError()
        try:
            result = _utc(datetime.fromisoformat(value))
        except Exception:
            raise PersistenceValueError() from None
        if result.isoformat(timespec="microseconds") != value:
            raise PersistenceValueError()
        return result
