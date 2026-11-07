"""Owned, fail-closed primitives for money-moving private exchange data."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BeforeValidator

from hmt_crypto._private_enums import (
    ExactEnumField,
    ExactStrEnum,
    exact_enum_literal,
    exact_enum_member_for_literal,
)
from hmt_crypto.domain._private_model import PrivateBoundaryModel

_MAX_I63 = 2**63 - 1
_MAX_PID = 2**31 - 1
_MAX_UUID_INT = 2**128 - 1
_UUID_INT_SLOT = vars(UUID)["int"]
_MIN_PRIVATE_TIME = datetime(2000, 1, 1, tzinfo=UTC)
_MAX_PRIVATE_TIME = datetime(2100, 1, 1, tzinfo=UTC)
_MIN_EPOCH_MICROSECONDS = 946_684_800_000_000
_MAX_EPOCH_MICROSECONDS = 4_102_444_800_000_000
_EXTERNAL_USER = re.compile(r"[1-9][0-9]{0,18}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_OMS_CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{1,32}\Z", re.ASCII)
_OBSERVED_CLIENT_ID = re.compile(r"[A-Za-z0-9._:-]{1,32}\Z", re.ASCII)
_PRODUCT_SYMBOL = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z", re.ASCII)
_ASSET_SYMBOL = re.compile(r"[A-Za-z0-9._:-]{1,32}\Z", re.ASCII)
_UNKNOWN_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z", re.ASCII)
_DECIMAL_TEXT = re.compile(
    r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,18})?\Z",
    re.ASCII,
)
_EPOCH_MICROSECONDS = re.compile(r"[1-9][0-9]{0,18}\Z", re.ASCII)
_POSITION_ISO = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))\Z",
    re.ASCII,
)


def _bounded_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("value must be an exact bounded integer")
    return value


def _positive_i63(value: object) -> int:
    return _bounded_int(value, minimum=1, maximum=_MAX_I63)


def _nonnegative_i63(value: object) -> int:
    return _bounded_int(value, minimum=0, maximum=_MAX_I63)


def _signed_i63(value: object) -> int:
    return _bounded_int(value, minimum=-_MAX_I63, maximum=_MAX_I63)


def _positive_pid(value: object) -> int:
    return _bounded_int(value, minimum=1, maximum=_MAX_PID)


def _private_id(value: object) -> int:
    return _positive_i63(value)


def _contracts(value: object) -> int:
    return _positive_i63(value)


def _unfilled_contracts(value: object) -> int:
    return _nonnegative_i63(value)


def _signed_contracts(value: object) -> int:
    return _signed_i63(value)


def _credential_version(value: object) -> int:
    return _positive_i63(value)


def _strict_epoch(value: object) -> int:
    return _positive_i63(value)


def _strict_positive_pid(value: object) -> int:
    return _positive_pid(value)


def _strict_positive_int(value: object) -> int:
    return _positive_i63(value)


def _strict_nonnegative_int(value: object) -> int:
    return _nonnegative_i63(value)


PrivateId = Annotated[int, BeforeValidator(_private_id)]
Contracts = Annotated[int, BeforeValidator(_contracts)]
UnfilledContracts = Annotated[int, BeforeValidator(_unfilled_contracts)]
SignedContracts = Annotated[int, BeforeValidator(_signed_contracts)]
CredentialVersion = Annotated[int, BeforeValidator(_credential_version)]
StrictEpoch = Annotated[int, BeforeValidator(_strict_epoch)]
StrictPositivePid = Annotated[int, BeforeValidator(_strict_positive_pid)]
StrictPositiveInt = Annotated[int, BeforeValidator(_strict_positive_int)]
StrictNonNegativeInt = Annotated[int, BeforeValidator(_strict_nonnegative_int)]


def _exact_text(value: object, pattern: re.Pattern[str], *, label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError(f"value must be a valid {label}")
    return value


def _external_user(value: object) -> str:
    result = _exact_text(value, _EXTERNAL_USER, label="external user identifier")
    if int(result) > _MAX_I63:
        raise ValueError("value must be a valid external user identifier")
    return result


def _sha256(value: object) -> str:
    return _exact_text(value, _SHA256, label="SHA-256 digest")


def _oms_client_id(value: object) -> str:
    return _exact_text(value, _OMS_CLIENT_ID, label="OMS client order identifier")


def _observed_client_id(value: object) -> str:
    return _exact_text(value, _OBSERVED_CLIENT_ID, label="observed client order identifier")


def _product_symbol(value: object) -> str:
    return _exact_text(value, _PRODUCT_SYMBOL, label="product symbol")


def _asset_symbol(value: object) -> str:
    return _exact_text(value, _ASSET_SYMBOL, label="asset symbol")


def _unknown_token(value: object) -> str:
    return _exact_text(value, _UNKNOWN_TOKEN, label="observed enum token")


def _opaque_cursor(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 1024
        or not value.isascii()
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise ValueError("value must be an opaque visible-ASCII cursor")
    return value


def _bounded_diagnostic(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 256 or not value.isprintable():
        raise ValueError("value must be bounded printable diagnostic text")
    return value


ExternalUserText = Annotated[str, BeforeValidator(_external_user)]
Sha256Hex = Annotated[str, BeforeValidator(_sha256)]
OmsClientOrderId = Annotated[str, BeforeValidator(_oms_client_id)]
ObservedDeltaClientOrderId = Annotated[str, BeforeValidator(_observed_client_id)]
ProductSymbol = Annotated[str, BeforeValidator(_product_symbol)]
AssetSymbol = Annotated[str, BeforeValidator(_asset_symbol)]
UnknownEnumToken = Annotated[str, BeforeValidator(_unknown_token)]
OpaqueCursor = Annotated[str, BeforeValidator(_opaque_cursor)]
BoundedDiagnosticText = Annotated[str, BeforeValidator(_bounded_diagnostic)]


def _exact_uuid(value: object) -> UUID:
    if type(value) is not UUID:
        raise ValueError("value must be an exact UUID object")
    try:
        raw = _UUID_INT_SLOT.__get__(value, UUID)
    except Exception:
        raise ValueError("value must be an exact UUID object") from None
    if type(raw) is not int or not 0 <= raw <= _MAX_UUID_INT:
        raise ValueError("value must be an exact UUID object")
    return UUID(int=raw)


ExactUuid = Annotated[UUID, BeforeValidator(_exact_uuid)]


def _exact_utc(value: object) -> datetime:
    if type(value) is not datetime or type(value.tzinfo) not in (timezone, ZoneInfo):
        raise ValueError("value must be an exact UTC-aware datetime")
    try:
        if value.utcoffset() != timedelta(0):
            raise ValueError
        return value.astimezone(UTC)
    except Exception:
        raise ValueError("value must be an exact UTC-aware datetime") from None


ExactAwareUtcDatetime = Annotated[datetime, BeforeValidator(_exact_utc)]


def _canonical_decimal_text(value: object) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("value must be an exact private decimal")
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast(int, raw_exponent)
    if not any(digits):
        return "0"
    end = len(digits)
    while end > 1 and digits[end - 1] == 0:
        end -= 1
        exponent += 1
    if -exponent > 18 or end + exponent > 20:
        raise ValueError("value must fit the private decimal 38/18 boundary")
    return format(Decimal((sign, digits[:end], exponent)), "f")


def _private_decimal(value: object) -> Decimal:
    return Decimal(_canonical_decimal_text(value))


def _positive_private_decimal(value: object) -> Decimal:
    result = _private_decimal(value)
    if result <= 0:
        raise ValueError("value must be a positive private decimal")
    return result


def _nonnegative_private_decimal(value: object) -> Decimal:
    result = _private_decimal(value)
    if result < 0:
        raise ValueError("value must be a nonnegative private decimal")
    return result


def _nonzero_private_decimal(value: object) -> Decimal:
    result = _private_decimal(value)
    if result == 0:
        raise ValueError("value must be a nonzero private decimal")
    return result


PrivateDecimal = Annotated[Decimal, BeforeValidator(_private_decimal)]
PositivePrivateDecimal = Annotated[Decimal, BeforeValidator(_positive_private_decimal)]
NonNegativePrivateDecimal = Annotated[Decimal, BeforeValidator(_nonnegative_private_decimal)]
NonZeroPrivateDecimal = Annotated[Decimal, BeforeValidator(_nonzero_private_decimal)]


def parse_private_decimal_text(value: object) -> Decimal:
    """Parse one canonical non-exponent decimal string into owned 38/18 data."""
    if type(value) is not str or _DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError("invalid private decimal text")
    try:
        return _private_decimal(Decimal(value))
    except Exception:
        raise ValueError("invalid private decimal text") from None


def parse_epoch_microseconds(value: object) -> datetime:
    """Parse one strict epoch-microsecond string in the local 2000–2100 range."""
    if type(value) is not str or _EPOCH_MICROSECONDS.fullmatch(value) is None:
        raise ValueError("invalid epoch microseconds")
    raw = int(value)
    if not _MIN_EPOCH_MICROSECONDS <= raw < _MAX_EPOCH_MICROSECONDS:
        raise ValueError("invalid epoch microseconds")
    seconds, microseconds = divmod(raw, 1_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=microseconds)


def parse_position_iso_datetime(value: object) -> datetime:
    """Parse one bounded aware ISO position timestamp and normalize it to UTC."""
    if (
        type(value) is not str
        or not 20 <= len(value) <= 35
        or _POSITION_ISO.fullmatch(value) is None
    ):
        raise ValueError("invalid position timestamp")
    try:
        encoded = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        result = datetime.fromisoformat(encoded)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        result = result.astimezone(UTC)
    except Exception:
        raise ValueError("invalid position timestamp") from None
    if not _MIN_PRIVATE_TIME <= result < _MAX_PRIVATE_TIME:
        raise ValueError("invalid position timestamp")
    return result


__all__ = [
    "AssetSymbol",
    "BoundedDiagnosticText",
    "Contracts",
    "CredentialVersion",
    "ExactAwareUtcDatetime",
    "ExactEnumField",
    "ExactStrEnum",
    "ExactUuid",
    "ExternalUserText",
    "NonNegativePrivateDecimal",
    "NonZeroPrivateDecimal",
    "ObservedDeltaClientOrderId",
    "OmsClientOrderId",
    "OpaqueCursor",
    "PositivePrivateDecimal",
    "PrivateBoundaryModel",
    "PrivateDecimal",
    "PrivateId",
    "ProductSymbol",
    "Sha256Hex",
    "SignedContracts",
    "StrictEpoch",
    "StrictNonNegativeInt",
    "StrictPositiveInt",
    "StrictPositivePid",
    "UnfilledContracts",
    "UnknownEnumToken",
    "exact_enum_literal",
    "exact_enum_member_for_literal",
    "parse_epoch_microseconds",
    "parse_position_iso_datetime",
    "parse_private_decimal_text",
]
