"""Strict, bounded JSON decoding and Delta envelope normalization."""

from __future__ import annotations

import json
from decimal import Decimal

from hmt_crypto.errors import (
    AuthError,
    ExchangeProtocolError,
    SignatureExpiredError,
    UnauthorizedApiAccessError,
)


class _InvalidJson(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJson("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise _InvalidJson("non-finite number")


def decode_json_object(body: bytes, *, endpoint_id: str) -> dict[str, object]:
    """Decode one UTF-8 object, retaining decimal literals as exact Decimal values."""
    try:
        text = body.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _InvalidJson, ValueError):
        raise ExchangeProtocolError(context={"operation": endpoint_id}) from None
    if not isinstance(value, dict):
        raise ExchangeProtocolError(context={"operation": endpoint_id})
    return value


def success_result(envelope: dict[str, object], *, endpoint_id: str) -> tuple[object, object]:
    """Return a successful result and optional meta from the documented envelope."""
    if envelope.get("success") is not True or "result" not in envelope:
        raise ExchangeProtocolError(context={"operation": endpoint_id})
    return envelope["result"], envelope.get("meta")


def _normalized_code(value: object) -> str:
    if type(value) is not str:
        return ""
    return "".join(character for character in value.casefold() if character.isalnum())


def error_from_envelope(
    envelope: dict[str, object],
    *,
    endpoint_id: str,
    status_code: int,
) -> Exception:
    """Map documented auth codes without retaining messages, contexts, or raw values."""
    code: object = None
    error = envelope.get("error")
    if envelope.get("success") is False and isinstance(error, dict):
        code = error.get("code")
    elif type(error) is str:
        code = error
    normalized = _normalized_code(code)
    context = {"operation": endpoint_id, "http_status": status_code}
    if normalized == "signatureexpired":
        return SignatureExpiredError(context=context)
    if normalized == "unauthorizedapiaccess":
        return UnauthorizedApiAccessError(context=context)
    if normalized in {
        "invalidapikey",
        "invalidkey",
        "invalidsignature",
        "signaturemismatch",
    }:
        return AuthError(context=context)
    return ExchangeProtocolError(context=context)


__all__ = ["decode_json_object", "error_from_envelope", "success_result"]
