"""Canonical SHA-256 evidence for already-validated pure risk values."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from hmt_crypto.risk.math import amount_from_scaled, scaled_amount
from hmt_crypto.risk.models import RiskInputError


def _canonical(value: object) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is Decimal:
        return format(amount_from_scaled(scaled_amount(value)), "f")
    if type(value) is datetime:
        if value.tzinfo is not UTC:
            raise RiskInputError() from None
        return value.isoformat(timespec="microseconds")
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is tuple:
        return [_canonical(item) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise RiskInputError() from None
        return {key: _canonical(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    raise RiskInputError() from None


def canonical_digest(value: object) -> str:
    """Hash one deterministic ASCII JSON encoding."""
    encoded = json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["canonical_digest"]
