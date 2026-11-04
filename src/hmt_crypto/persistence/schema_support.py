"""Shared portable schema constraints; these are local storage admission limits."""

from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKeyConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from hmt_crypto.persistence.errors import PersistenceValueError

SCOPE = ("account_id", "region", "environment", "mode")


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
        f"length({column}) BETWEEN 1 AND {maximum} AND length(trim({column})) > 0",
        name=f"{column}_bounded",
    )


def sha_check(column: str) -> CheckConstraint:
    """Require exactly 64 lowercase hexadecimal characters using portable SQL."""
    remainder = column
    for digit in "0123456789abcdef":
        remainder = f"replace({remainder}, '{digit}', '')"
    return CheckConstraint(
        f"length({column}) = 64 AND length({remainder}) = 0", name=f"{column}_sha256"
    )


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
