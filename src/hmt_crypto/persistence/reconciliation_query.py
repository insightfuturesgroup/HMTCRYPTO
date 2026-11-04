"""Owned canonical query v1 evidence; no requests or pagination engine.

Selector maxima, page size and 2048 bytes are local admission limits. A typed
Core payload validates standalone shape, never agreement with sibling columns.
"""

import hashlib
import json
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy.engine import Dialect

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.schema_support import NulFreeText
from hmt_crypto.persistence.types import _utc

RESOURCE_KINDS = (
    "BALANCES",
    "WALLET_TRANSACTIONS",
    "LATEST_POSITION",
    "MARGINED_POSITIONS",
    "OPEN_ORDERS",
    "ORDER_HISTORY",
    "FILLS",
)
SINGLE_KINDS = ("BALANCES", "LATEST_POSITION", "MARGINED_POSITIONS")


def _hash(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class ReconciliationQuery(BaseModel):
    """Frozen owned selectors; omission is distinct from an explicitly null field."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    product_ids: tuple[int, ...] | None = None
    asset_ids: tuple[int, ...] | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    page_size: int | None = None
    initial_cursor_direction: Literal["NONE", "AFTER", "BEFORE"]
    initial_cursor_sha256: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if type(value) is not dict or any(
            value.get(field, False) is None
            for field in ("product_ids", "asset_ids", "start_at", "end_at", "page_size")
        ):
            raise PersistenceValueError()
        return value

    @field_validator("product_ids", "asset_ids", mode="before")
    @classmethod
    def selectors(cls, value: object) -> tuple[int, ...]:
        if type(value) is not list and type(value) is not tuple:
            raise PersistenceValueError()
        if (
            not 1 <= len(value) <= 10
            or any(type(item) is not int or not 1 <= item < 2**63 for item in value)
            or len(set(value)) != len(value)
        ):
            raise PersistenceValueError()
        return tuple(sorted(value))

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> datetime:
        return _utc(value)

    @field_validator("page_size", mode="before")
    @classmethod
    def local_page_size(cls, value: object) -> int:
        if type(value) is not int or not 1 <= value <= 50:
            raise PersistenceValueError()
        return value

    @model_validator(mode="after")
    def relations(self) -> Self:
        if self.start_at is not None and self.end_at is not None and self.start_at > self.end_at:
            raise PersistenceValueError()
        if self.initial_cursor_direction == "NONE":
            if self.initial_cursor_sha256 is not None:
                raise PersistenceValueError()
        elif not _hash(self.initial_cursor_sha256):
            raise PersistenceValueError()
        return self


def _json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _owned(value: object) -> ReconciliationQuery:
    if type(value) is ReconciliationQuery:
        value = value.model_dump(exclude_unset=True)
    if type(value) is not dict:
        raise PersistenceValueError()
    return ReconciliationQuery.model_validate(value)


def _payload(query: ReconciliationQuery) -> str:
    values = query.model_dump(exclude_unset=True)
    # The cursor pair is always explicit, even when the nullable hash was omitted.
    values["initial_cursor_sha256"] = query.initial_cursor_sha256
    for field in ("start_at", "end_at"):
        if field in values:
            values[field] = values[field].isoformat(timespec="microseconds")
    payload = _json(values)
    if len(payload.encode("ascii")) > 2048:
        raise PersistenceValueError()
    return payload


def encode_query(kind: object, version: object, value: object) -> tuple[str, str]:
    """Validate resource-specific selectors and hash exact canonical envelope bytes."""
    try:
        if (
            type(kind) is not str
            or kind not in RESOURCE_KINDS
            or type(version) is not int
            or version != 1
        ):
            raise PersistenceValueError()
        query = _owned(value)
        fields = query.model_fields_set - {"initial_cursor_direction", "initial_cursor_sha256"}
        allowed = (
            set()
            if kind == "BALANCES"
            else {"product_ids"}
            if kind in {"LATEST_POSITION", "MARGINED_POSITIONS"}
            else {
                "asset_ids" if kind == "WALLET_TRANSACTIONS" else "product_ids",
                "start_at",
                "end_at",
                "page_size",
            }
        )
        if (
            fields - allowed
            or (
                kind == "LATEST_POSITION"
                and (query.product_ids is None or len(query.product_ids) != 1)
            )
            or (kind in SINGLE_KINDS and query.initial_cursor_direction != "NONE")
        ):
            raise PersistenceValueError()
        payload = _payload(query)
        envelope = (
            '{"payload":' + payload + ',"resource_kind":' + _json(kind) + ',"schema_version":1}'
        )
        return payload, hashlib.sha256(envelope.encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def decode_query(payload: object) -> ReconciliationQuery:
    """Require canonical standalone structure on stored-result and bind paths."""
    try:
        if type(payload) is not str or len(payload.encode("utf-8")) > 2048:
            raise PersistenceValueError()
        values = json.loads(payload)
        if type(values) is not dict:
            raise PersistenceValueError()
        for field in ("start_at", "end_at"):
            if field in values and type(values[field]) is str:
                values[field] = datetime.fromisoformat(values[field])
        query = _owned(values)
        if _payload(query) != payload:
            raise PersistenceValueError()
        return query
    except Exception:
        raise PersistenceValueError() from None


def validate_query(
    kind: object, version: object, payload: object, digest: object
) -> ReconciliationQuery:
    """ORM tuple validation; a single Core column cannot check sibling identity."""
    query = decode_query(payload)
    if encode_query(kind, version, query) != (payload, digest):
        raise PersistenceValueError()
    return query


class QueryPayload(NulFreeText):
    """Canonical standalone admission on ORM/Core bind and result paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        decode_query(value)
        assert isinstance(value, str)
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)
