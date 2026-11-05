"""Deterministic OMS client-order identifiers with no retry or dispatch authority."""

from __future__ import annotations

from base64 import b32encode
from hashlib import sha256
from uuid import UUID

from hmt_crypto.oms.models import OmsAccountScope, OmsTransitionError, _copy_exact_uuid

_DOMAIN = b"hmt-crypto-client-order-id-v1"
_PREFIX = "HMC1_"


def _fail() -> OmsTransitionError:
    return OmsTransitionError()


def _framed(parts: tuple[bytes, ...]) -> bytes:
    return b"".join(len(part).to_bytes(2, "big") + part for part in parts)


def make_client_order_id(account: OmsAccountScope, command_id: UUID) -> str:
    """Derive a stable ID after the coordinator binds account UUID to Delta user.

    ``external_user_id`` is deliberately omitted: the later durable coordinator must
    enforce an immutable account UUID to Delta user binding before calling this pure helper.
    """
    if type(account) is not OmsAccountScope or type(command_id) is not UUID:
        raise _fail() from None
    try:
        owned = OmsAccountScope(
            account_id=object.__getattribute__(account, "account_id"),
            external_user_id=object.__getattribute__(account, "external_user_id"),
            region=object.__getattribute__(account, "region"),
            environment=object.__getattribute__(account, "environment"),
            mode=object.__getattribute__(account, "mode"),
            credential_version=object.__getattribute__(account, "credential_version"),
        )
        owned_command_id = _copy_exact_uuid(command_id)
        parts = (
            _DOMAIN,
            owned.account_id.bytes,
            str.__str__(owned.region).encode("ascii"),
            str.__str__(owned.environment).encode("ascii"),
            str.__str__(owned.mode).encode("ascii"),
            owned_command_id.bytes,
        )
        digest = sha256(_framed(parts)).digest()
        result = _PREFIX + b32encode(digest).decode("ascii")[:27]
    except Exception:
        raise _fail() from None
    if len(result) != 32 or not result.isascii():
        raise _fail() from None
    return result


__all__ = ["make_client_order_id"]
