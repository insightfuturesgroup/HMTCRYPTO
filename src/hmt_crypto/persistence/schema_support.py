"""Shared portable schema constraints; these are local storage admission limits."""

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    String,
    Text,
    and_,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import TypeDecorator

from hmt_crypto.persistence.errors import PersistenceValueError

SCOPE = ("account_id", "region", "environment", "mode")


class _NoNul(ColumnElement[bool]):
    type = Boolean()
    inherit_cache = True

    def __init__(self, column: str) -> None:
        self.column = column


@compiles(_NoNul)
def _no_nul_native(element: _NoNul, compiler: SQLCompiler, **kwargs: Any) -> str:
    # PostgreSQL TEXT cannot contain NUL. Avoid chr(0), which PostgreSQL rejects
    # even when a checked value itself contains no NUL.
    return "true"


@compiles(_NoNul, "sqlite")
def _no_nul_sqlite(element: _NoNul, compiler: SQLCompiler, **kwargs: Any) -> str:
    return f"instr({compiler.preparer.quote(element.column)}, char(0)) = 0"


class NulFreeString(TypeDecorator[str]):
    """Reject embedded NUL before driver binding and when reading stored text."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        """Keep SQLite truncating string functions out of the admission boundary."""
        if value is None:
            return None
        if not isinstance(value, str) or "\x00" in value:
            raise PersistenceValueError()
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> str | None:
        """Fail closed on legacy/corrupted values without silently truncating."""
        return self.process_bind_param(value, dialect)


class NulFreeText(NulFreeString):
    """The same exact text boundary for bounded canonical payload columns."""

    impl = Text
    cache_ok = True


def venue_checks() -> tuple[CheckConstraint, ...]:
    """Return fresh checked-string venue constraints for each owning table."""
    return (
        CheckConstraint("region IN ('india', 'global')", name="region"),
        CheckConstraint("environment IN ('testnet', 'prod')", name="environment"),
    )


def mode_check() -> CheckConstraint:
    """Keep simulated and LIVE identities distinct without native PG enums."""
    return CheckConstraint("mode IN ('BACKTEST', 'PAPER', 'LIVE')", name="mode")


def bounded(column: str, maximum: int = 128) -> CheckConstraint:
    """Bound required opaque text on both databases, including SQLite VARCHAR."""
    return CheckConstraint(
        and_(
            text(f"length({column}) BETWEEN 1 AND {maximum} AND length(trim({column})) > 0"),
            _NoNul(column),
        ),
        name=f"{column}_bounded",
    )


def hex_check(column: str, length: int) -> CheckConstraint:
    """Require exact lowercase hexadecimal text, including SQLite's hidden tail."""
    remainder = column
    for digit in "0123456789abcdef":
        remainder = f"replace({remainder}, '{digit}', '')"
    return CheckConstraint(
        and_(text(f"length({column}) = {length} AND length({remainder}) = 0"), _NoNul(column)),
        name=f"{column}_hex",
    )


def sha_check(column: str) -> CheckConstraint:
    """Require exactly 64 lowercase hexadecimal characters using portable SQL."""
    constraint = hex_check(column, 64)
    constraint.name = f"{column}_sha256"
    return constraint


def account_fk(table: str) -> ForeignKeyConstraint:
    """Bind every account-owned row to the complete immutable operational scope."""
    return ForeignKeyConstraint(
        SCOPE,
        [f"hmt_crypto.accounts.{name}" for name in ("id", *SCOPE[1:])],
        name=f"fk_{table}_account_scope",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class StrictBigInteger(TypeDecorator[int]):
    """Reject coercion of booleans/floats and integers outside signed BIGINT."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> int | None:
        """Preserve exact integer input before the driver's numeric conversion."""
        if value is None:
            return None
        if type(value) is not int or not -(2**63) <= value < 2**63:
            raise PersistenceValueError()
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> int | None:
        """Reject corrupt or coerced driver values on reads as well."""
        return self.process_bind_param(value, dialect)
