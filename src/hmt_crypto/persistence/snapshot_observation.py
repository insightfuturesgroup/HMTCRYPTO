"""Canonical identities for normalized position and balance observations.

The typed snapshot columns remain authoritative.  These helpers hash only the
economic observation tuple; request/receive/record provenance is deliberately
outside the content identity.
"""

import hashlib
import json

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.types import _decimal_text, _utc

POSITION_FIELDS = frozenset(
    {
        "product_id",
        "signed_contracts",
        "entry_price",
        "mark_price",
        "liquidation_price",
        "margin",
        "unrealized_pnl",
        "margin_mode",
        "exchange_updated_at",
    }
)
BALANCE_FIELDS = frozenset(
    {"asset_id", "asset_symbol", "available_balance", "balance", "blocked_margin"}
)
POSITION_KINDS = ("LATEST_POSITION", "MARGINED_POSITIONS")
MARGIN_MODES = ("isolated", "portfolio", "UNKNOWN")


def _positive_id(value: object) -> int:
    if type(value) is not int or not 1 <= value < 2**63:
        raise PersistenceValueError()
    return value


def _signed_bigint(value: object) -> int:
    if type(value) is not int or not -(2**63) <= value < 2**63:
        raise PersistenceValueError()
    return value


def _decimal(value: object, *, nullable: bool) -> str | None:
    if value is None and nullable:
        return None
    return _decimal_text(value, 38, 18)


def _position_payload(kind: str, values: dict[str, object]) -> dict[str, object]:
    if set(values) != POSITION_FIELDS:
        raise PersistenceValueError()
    margin_mode = values["margin_mode"]
    if margin_mode is not None and (
        type(margin_mode) is not str or margin_mode not in MARGIN_MODES
    ):
        raise PersistenceValueError()
    exchange_updated_at = values["exchange_updated_at"]
    if exchange_updated_at is not None:
        exchange_updated_at = _utc(exchange_updated_at).isoformat(timespec="microseconds")
    rich = (
        "mark_price",
        "liquidation_price",
        "margin",
        "unrealized_pnl",
        "margin_mode",
        "exchange_updated_at",
    )
    if kind == "LATEST_POSITION" and any(values[field] is not None for field in rich):
        raise PersistenceValueError()
    return {
        "entry_price": _decimal(values["entry_price"], nullable=True),
        "exchange_updated_at": exchange_updated_at,
        "liquidation_price": _decimal(values["liquidation_price"], nullable=True),
        "margin": _decimal(values["margin"], nullable=True),
        "margin_mode": margin_mode,
        "mark_price": _decimal(values["mark_price"], nullable=True),
        "product_id": _positive_id(values["product_id"]),
        "signed_contracts": _signed_bigint(values["signed_contracts"]),
        "unrealized_pnl": _decimal(values["unrealized_pnl"], nullable=True),
    }


def _balance_payload(values: dict[str, object]) -> dict[str, object]:
    if set(values) != BALANCE_FIELDS:
        raise PersistenceValueError()
    symbol = values["asset_symbol"]
    if (
        type(symbol) is not str
        or not 1 <= len(symbol) <= 128
        or not symbol.strip()
        or "\x00" in symbol
    ):
        raise PersistenceValueError()
    return {
        "asset_id": _positive_id(values["asset_id"]),
        "asset_symbol": symbol,
        "available_balance": _decimal(values["available_balance"], nullable=False),
        "balance": _decimal(values["balance"], nullable=False),
        "blocked_margin": _decimal(values["blocked_margin"], nullable=False),
    }


def encode_snapshot(kind: object, version: object, values: object) -> str:
    """Return the v1 SHA-256 identity for an owned normalized observation."""
    try:
        if type(kind) is not str or type(version) is not int or version != 1:
            raise PersistenceValueError()
        if type(values) is not dict:
            raise PersistenceValueError()
        owned = dict(values)
        if kind in POSITION_KINDS:
            payload = _position_payload(kind, owned)
        elif kind == "BALANCES":
            payload = _balance_payload(owned)
        else:
            raise PersistenceValueError()
        envelope = json.dumps(
            {"observation_kind": kind, "payload": payload, "schema_version": 1},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        if len(envelope.encode("ascii")) > 2048:
            raise PersistenceValueError()
        return hashlib.sha256(envelope.encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def validate_snapshot(kind: object, version: object, values: object, digest: object) -> None:
    """Fail closed unless the complete semantic tuple matches its stored digest."""
    try:
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or encode_snapshot(kind, version, values) != digest
        ):
            raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None
