"""Fail-closed exact scalar copying for the pure paper simulator."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Protocol, cast
from uuid import UUID

_MAX_I63 = 2**63 - 1
_MAX_U128 = 2**128 - 1
_MAX_DECIMAL_DIGITS = 38
_MAX_DECIMAL_PLACES = 18


class _UuidIntDescriptor(Protocol):
    def __get__(self, instance: UUID, owner: type[UUID]) -> object: ...


_UUID_INT = cast(_UuidIntDescriptor, vars(UUID)["int"])


class PaperSimulationError(Exception):
    """A PAPER input or transition violated the deterministic model contract."""

    code = "paper_simulation"
    message = "PAPER simulation input is invalid"

    def __init__(self) -> None:
        super().__init__(f"{self.code}: {self.message}")

    def __repr__(self) -> str:
        return "PaperSimulationError(code='paper_simulation')"


def _fail() -> PaperSimulationError:
    return PaperSimulationError()


def _copy_uuid(value: object) -> UUID:
    if type(value) is not UUID:
        raise _fail() from None
    try:
        integer = _UUID_INT.__get__(value, UUID)
    except Exception:
        raise _fail() from None
    if type(integer) is not int or not 0 <= integer <= _MAX_U128:
        raise _fail() from None
    return UUID(int=integer)


def _copy_utc(value: object) -> datetime:
    if type(value) is not datetime:
        raise _fail() from None
    try:
        if value.tzinfo is not UTC or value.utcoffset() != UTC.utcoffset(value):
            raise _fail() from None
        copied = datetime(
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
    except PaperSimulationError:
        raise
    except Exception:
        raise _fail() from None
    return copied


def _copy_decimal(value: object, *, positive: bool) -> Decimal:
    if type(value) is not Decimal:
        raise _fail() from None
    try:
        parts = value.as_tuple()
    except Exception:
        raise _fail() from None
    exponent = parts.exponent
    if (
        type(parts.sign) is not int
        or parts.sign not in (0, 1)
        or type(parts.digits) is not tuple
        or not 1 <= len(parts.digits) <= _MAX_DECIMAL_DIGITS
        or any(type(digit) is not int or not 0 <= digit <= 9 for digit in parts.digits)
        or type(exponent) is not int
        or exponent < -_MAX_DECIMAL_PLACES
        or exponent > _MAX_DECIMAL_DIGITS
    ):
        raise _fail() from None
    copied = Decimal((parts.sign, parts.digits, exponent))
    if not copied.is_finite() or (positive and copied <= 0) or (not positive and copied < 0):
        raise _fail() from None
    if copied.adjusted() > 19:
        raise _fail() from None
    return copied


def _exact_int(value: object, *, minimum: int = 0, maximum: int = _MAX_I63) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _fail() from None
    return value


def _copy_stream_id(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 64
        or not value.isascii()
        or any(not (character.isalnum() or character in "-_.:") for character in value)
    ):
        raise _fail() from None
    return value


def _is_tick_aligned(value: Decimal, tick_size: Decimal) -> bool:
    with localcontext() as context:
        context.prec = 80
        return value % tick_size == 0


__all__ = ["PaperSimulationError"]
