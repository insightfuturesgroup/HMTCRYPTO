"""Owned canonical payloads with bounded exact values and fail-closed decoding.

The 16 KiB/128-character/38,18 Decimal envelope is a local admission policy,
not a claim about venue precision. Raw SQL bypasses these application boundaries.
"""

import json
from decimal import Decimal
from enum import Enum
from typing import Any

from hmt_crypto.domain import Product
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.redaction import redact
from hmt_crypto.persistence.types import _decimal_text

MAX_PAYLOAD = 16384


def _exact(value: object, depth: int = 0) -> Any:
    if depth > 16:
        raise PersistenceValueError()
    if isinstance(value, Enum):
        return _exact(value.value, depth)
    if type(value) is Decimal:
        return _decimal_text(value, 38, 18)
    if value is None or type(value) in (str, bool):
        if type(value) is str and len(value) > MAX_PAYLOAD:
            raise PersistenceValueError()
        return value
    if type(value) is int and -(2**63) <= value < 2**63:
        return value
    if type(value) is dict and len(value) <= 256:
        if any(type(key) is not str or not 1 <= len(key) <= 128 for key in value):
            raise PersistenceValueError()
        return {key: _exact(item, depth + 1) for key, item in value.items()}
    if type(value) is list and len(value) <= 256:
        return [_exact(item, depth + 1) for item in value]
    raise PersistenceValueError()


def _encode(value: object) -> str:
    encoded = json.dumps(
        _exact(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD:
        raise PersistenceValueError()
    return encoded


def canonical_intent(value: object) -> str:
    """Copy, validate and sanitize structured intent without accepting raw JSON."""
    if type(value) is not dict:
        raise PersistenceValueError()
    return _encode(redact(_exact(value)))


def encode_product(value: Product) -> str:
    """Revalidate even mutated Product inputs and retain field presence recursively."""
    try:
        if type(value) is not Product:
            raise PersistenceValueError()
        raw = value.model_dump(mode="python", exclude_unset=True, warnings="error")
        # Validate exact Python values before Decimal-to-string canonicalization.
        _exact(raw)
        owned = Product.model_validate(raw, strict=True)
        for asset in (owned.underlying_asset, owned.quoting_asset, owned.settling_asset):
            if asset is not None and (asset.id >= 2**63 or len(asset.symbol) > 128):
                raise PersistenceValueError()
        if owned.id >= 2**63 or len(owned.symbol) > 128 or len(owned.contract_unit_currency) > 128:
            raise PersistenceValueError()
        return _encode({"values": owned.model_dump(mode="python", exclude_unset=True)})
    except Exception:
        raise PersistenceValueError() from None


def decode_product(payload: str) -> Product:
    """Reconstruct through existing Product validation; never issue publication proof."""
    try:
        if type(payload) is not str or len(payload.encode()) > MAX_PAYLOAD:
            raise PersistenceValueError()
        decoded = json.loads(payload)
        if type(decoded) is not dict or set(decoded) != {"values"}:
            raise PersistenceValueError()
        result = Product.model_validate(decoded["values"])
        if encode_product(result) != payload:
            raise PersistenceValueError()
        return result
    except Exception:
        raise PersistenceValueError() from None
