"""Canonical configuration/data provenance hashing without ambient state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


def _canonical(value: object) -> Any:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is Decimal:
        return {"decimal": str(value)}
    if type(value) is datetime:
        return {"utc": value.isoformat()}
    if type(value) is UUID:
        return {"uuid": str(value)}
    if isinstance(value, Enum):
        return {"enum": f"{type(value).__name__}:{value.value}"}
    if type(value) is tuple:
        return [_canonical(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            "fields": {item.name: _canonical(getattr(value, item.name)) for item in fields(value)},
        }
    raise TypeError(f"unsupported canonical type: {type(value).__name__}")


def identity_sha256(namespace: str, value: object) -> str:
    """Hash one canonical immutable value under a protocol namespace."""
    payload = json.dumps(
        {"namespace": namespace, "value": _canonical(value)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


__all__ = ["identity_sha256"]
