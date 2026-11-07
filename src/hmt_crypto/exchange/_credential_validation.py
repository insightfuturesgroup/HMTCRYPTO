"""Pure fail-closed validation primitives for the Task 2.7 owner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import NoReturn
from uuid import UUID
from zoneinfo import ZoneInfo

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.private_origin import (
    CanonicalPrivateOrigin,
    _canonical_private_origin,
)

_MAX_I63 = 2**63 - 1
_UUID_INT_SLOT = vars(UUID)["int"]
_MIN_TIME = datetime(2000, 1, 1, tzinfo=UTC)
_MAX_TIME = datetime(2100, 1, 1, tzinfo=UTC)


def _fail() -> NoReturn:
    raise ConfigurationError(context={"operation": "task27_private_credentials"}) from None


def _exact_int(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_I63:
        _fail()
    return value


def _exact_uuid(value: object) -> UUID:
    if type(value) is not UUID:
        _fail()
    try:
        raw = _UUID_INT_SLOT.__get__(value, UUID)
    except Exception:
        _fail()
    if type(raw) is not int or not 1 <= raw <= 2**128 - 1:
        _fail()
    return UUID(int=raw)


def _exact_utc(value: object) -> datetime:
    if type(value) is not datetime or type(value.tzinfo) not in (timezone, ZoneInfo):
        _fail()
    try:
        if value.utcoffset() != timedelta(0):
            _fail()
        result = value.astimezone(UTC)
    except ConfigurationError:
        raise
    except Exception:
        _fail()
    if not _MIN_TIME <= result <= _MAX_TIME:
        _fail()
    return result


def _external_user(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 19
        or value[0] == "0"
        or not value.isascii()
        or not value.isdigit()
        or not 1 <= int(value) <= _MAX_I63
    ):
        _fail()
    return value


def _exact_two_member_environment(
    value: object,
    testnet_member: object,
    prod_member: object,
) -> DeltaEnvironment:
    member_type = type(testnet_member)
    if member_type is not type(prod_member) or type(value) is not member_type:
        _fail()
    if value is testnet_member:
        return DeltaEnvironment.TESTNET
    if value is prod_member:
        return DeltaEnvironment.PROD
    _fail()


def _exact_origin(value: object) -> tuple[CanonicalPrivateOrigin, DeltaEnvironment]:
    if type(value) is not CanonicalPrivateOrigin:
        _fail()
    for environment in (DeltaEnvironment.TESTNET, DeltaEnvironment.PROD):
        try:
            canonical = _canonical_private_origin(DeltaRegion.INDIA, environment)
        except Exception:
            _fail()
        if value is canonical:
            return canonical, environment
    _fail()


def _exact_mode(value: object, environment: DeltaEnvironment) -> ExecutionMode:
    if type(value) is not ExecutionMode:
        _fail()
    if value is ExecutionMode.PAPER and environment is DeltaEnvironment.TESTNET:
        return value
    if value is ExecutionMode.LIVE:
        return value
    _fail()


def _owned_account(value: object) -> AccountRef:
    if type(value) is not AccountRef:
        _fail()
    try:
        return AccountRef.model_validate(value).model_copy()
    except Exception:
        _fail()


def _same_account(left: AccountRef, right: AccountRef) -> bool:
    return (
        left.account_id == right.account_id
        and left.external_user_id == right.external_user_id
        and left.region is right.region
        and left.environment is right.environment
        and left.mode is right.mode
        and left.credential_version == right.credential_version
    )
