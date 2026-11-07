"""Canonical safe wallet/funding evidence; no accounting policy or aggregation."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.engine import Dialect

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.schema_support import NulFreeText
from hmt_crypto.persistence.types import _decimal_text, _utc

WALLET_KIND = "wallet_transaction"
FUNDING_KIND = "funding_snapshot"
WALLET_FIELDS = frozenset(
    {
        "amount",
        "asset_id",
        "asset_symbol",
        "effective_at",
        "exchange_transaction_id",
        "post_balance",
        "product_id",
        "transaction_type",
    }
)
FUNDING_FIELDS = frozenset(
    {
        "accounting_role",
        "amount",
        "asset_id",
        "asset_symbol",
        "effective_at",
        "position_snapshot_id",
        "product_id",
        "rate",
        "source_kind",
        "wallet_transaction_id",
    }
)
SOURCE_KINDS = ("WALLET_TRANSACTION", "POSITION_AGGREGATE", "MARKET_RATE")
ACCOUNTING_ROLE = "NON_ADDITIVE_OBSERVATION"


def _text(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128 or not value.strip() or "\x00" in value:
        raise PersistenceValueError()
    return value


def _positive_id(value: object, *, nullable: bool) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or not 1 <= value < 2**63:
        raise PersistenceValueError()
    return value


def _decimal(value: object, *, nullable: bool) -> str | None:
    if value is None and nullable:
        return None
    return _decimal_text(value, 38, 18)


def _time(value: object, *, nullable: bool) -> str | None:
    if value is None and nullable:
        return None
    return _utc(value).isoformat(timespec="microseconds")


def _uuid(value: object, *, nullable: bool) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not UUID:
        raise PersistenceValueError()
    return str(value)


def _wallet_payload(values: dict[str, object]) -> dict[str, object]:
    if set(values) != WALLET_FIELDS:
        raise PersistenceValueError()
    return {
        "amount": _decimal(values["amount"], nullable=False),
        "asset_id": _positive_id(values["asset_id"], nullable=False),
        "asset_symbol": _text(values["asset_symbol"]),
        "effective_at": _time(values["effective_at"], nullable=True),
        "exchange_transaction_id": _text(values["exchange_transaction_id"]),
        "post_balance": _decimal(values["post_balance"], nullable=True),
        "product_id": _positive_id(values["product_id"], nullable=True),
        "transaction_type": _text(values["transaction_type"]),
    }


def _funding_payload(values: dict[str, object]) -> dict[str, object]:
    if set(values) != FUNDING_FIELDS:
        raise PersistenceValueError()
    source = values["source_kind"]
    if type(source) is not str or source not in SOURCE_KINDS:
        raise PersistenceValueError()
    if type(values["accounting_role"]) is not str or values["accounting_role"] != ACCOUNTING_ROLE:
        raise PersistenceValueError()
    wallet_parent = values["wallet_transaction_id"]
    position_parent = values["position_snapshot_id"]
    asset_id = values["asset_id"]
    asset_symbol = values["asset_symbol"]
    if (asset_id is None) != (asset_symbol is None):
        raise PersistenceValueError()
    product_id = values["product_id"]
    amount = values["amount"]
    rate = values["rate"]
    effective_at = values["effective_at"]
    if source == "WALLET_TRANSACTION":
        if (
            type(wallet_parent) is not UUID
            or position_parent is not None
            or amount is None
            or rate is not None
            or asset_id is None
        ):
            raise PersistenceValueError()
    elif source == "POSITION_AGGREGATE":
        if (
            wallet_parent is not None
            or type(position_parent) is not UUID
            or product_id is None
            or amount is None
            or rate is not None
        ):
            raise PersistenceValueError()
    elif (
        wallet_parent is not None
        or position_parent is not None
        or product_id is None
        or amount is not None
        or rate is None
        or effective_at is None
    ):
        raise PersistenceValueError()
    return {
        "accounting_role": ACCOUNTING_ROLE,
        "amount": _decimal(amount, nullable=True),
        "asset_id": _positive_id(asset_id, nullable=True),
        "asset_symbol": None if asset_symbol is None else _text(asset_symbol),
        "effective_at": _time(effective_at, nullable=True),
        "position_snapshot_id": _uuid(position_parent, nullable=True),
        "product_id": _positive_id(product_id, nullable=True),
        "rate": _decimal(rate, nullable=True),
        "source_kind": source,
        "wallet_transaction_id": _uuid(wallet_parent, nullable=True),
    }


def _canonical_payload(kind: object, values: object) -> str:
    if type(kind) is not str or type(values) is not dict:
        raise PersistenceValueError()
    owned = dict(values)
    if kind == WALLET_KIND:
        payload = _wallet_payload(owned)
    elif kind == FUNDING_KIND:
        payload = _funding_payload(owned)
    else:
        raise PersistenceValueError()
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(encoded.encode("ascii")) > 2048:
        raise PersistenceValueError()
    return encoded


def encode_accounting_evidence(
    kind: object, schema_version: object, values: object
) -> tuple[str, str]:
    """Own exact typed semantics and return canonical payload plus SHA-256."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            raise PersistenceValueError()
        payload = _canonical_payload(kind, values)
        envelope = (
            '{"kind":'
            + json.dumps(kind, ensure_ascii=True)
            + ',"payload":'
            + payload
            + ',"schema_version":1}'
        )
        return payload, hashlib.sha256(envelope.encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def decode_accounting_evidence(kind: object, payload: object) -> dict[str, object]:
    """Decode only exact standalone canonical payloads, restoring typed values."""
    try:
        if type(payload) is not str or len(payload.encode("utf-8")) > 2048:
            raise PersistenceValueError()
        raw = json.loads(payload)
        if type(raw) is not dict:
            raise PersistenceValueError()
        for field in ("amount", "post_balance", "rate"):
            if field in raw and raw[field] is not None:
                raw[field] = Decimal(raw[field])
        if "effective_at" in raw and raw["effective_at"] is not None:
            raw["effective_at"] = datetime.fromisoformat(raw["effective_at"])
        for field in ("wallet_transaction_id", "position_snapshot_id"):
            if field in raw and raw[field] is not None:
                raw[field] = UUID(raw[field])
        canonical, _ = encode_accounting_evidence(kind, 1, raw)
        if canonical != payload:
            raise PersistenceValueError()
        return raw
    except Exception:
        raise PersistenceValueError() from None


def validate_accounting_evidence(
    kind: object, schema_version: object, payload: object, digest: object
) -> None:
    """Validate standalone payload and its complete sibling semantic identity."""
    try:
        values = decode_accounting_evidence(kind, payload)
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or encode_accounting_evidence(kind, schema_version, values) != (payload, digest)
        ):
            raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None


class AccountingPayload(NulFreeText):
    """Canonical standalone payload admission on ORM/Core bind and result paths."""

    cache_ok = True

    def __init__(self, kind: str) -> None:
        if kind not in {WALLET_KIND, FUNDING_KIND}:
            raise PersistenceValueError()
        self.kind = kind
        super().__init__()

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        decode_accounting_evidence(self.kind, value)
        assert isinstance(value, str)
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)
