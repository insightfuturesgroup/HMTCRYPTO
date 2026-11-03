"""Strict, bounded JSON decoding and Delta envelope normalization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, DecimalException

from hmt_crypto.errors import (
    AuthError,
    ExchangeProtocolError,
    SignatureExpiredError,
    UnauthorizedApiAccessError,
)


class _InvalidJson(ValueError):
    pass


_MAX_CONTAINER_DEPTH = 64
# Local resource policy, independent of interpreter-wide integer conversion limits.
_MAX_NUMBER_CHARACTERS = 1024


def _bounded_integer(value: str) -> int:
    if len(value) > _MAX_NUMBER_CHARACTERS:
        raise _InvalidJson("number length")
    return int(value)


def _bounded_decimal(value: str) -> Decimal:
    if len(value) > _MAX_NUMBER_CHARACTERS:
        raise _InvalidJson("number length")
    result = Decimal(value)
    if not result.is_finite():
        raise _InvalidJson("non-finite number")
    return result


def _context(endpoint_id: str, inherited: Mapping[str, object] | None) -> dict[str, object]:
    result = dict(inherited or ())
    result["operation"] = endpoint_id
    return result


def _validate_container_depth(body: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False
    for byte in body:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in {0x5B, 0x7B}:  # opening bracket or brace
            depth += 1
            if depth > _MAX_CONTAINER_DEPTH:
                raise _InvalidJson("container depth")
        elif byte in {0x5D, 0x7D}:  # closing bracket or brace
            depth -= 1


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJson("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise _InvalidJson("non-finite number")


def decode_json_object(
    body: bytes,
    *,
    endpoint_id: str,
    context: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Decode one UTF-8 object, retaining decimal literals as exact Decimal values."""
    try:
        _validate_container_depth(body)
        text = body.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            parse_float=_bounded_decimal,
            parse_int=_bounded_integer,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _InvalidJson,
        ValueError,
        RecursionError,
        MemoryError,
        DecimalException,
        OverflowError,
    ):
        raise ExchangeProtocolError(context=_context(endpoint_id, context)) from None
    if not isinstance(value, dict):
        raise ExchangeProtocolError(context=_context(endpoint_id, context))
    return value


def success_result(
    envelope: dict[str, object],
    *,
    endpoint_id: str,
    context: Mapping[str, object] | None = None,
) -> tuple[object, object]:
    """Return a successful result and optional meta from the documented envelope."""
    if envelope.get("success") is not True or "result" not in envelope:
        raise ExchangeProtocolError(context=_context(endpoint_id, context))
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
    context: Mapping[str, object] | None = None,
) -> Exception:
    """Map documented auth codes without retaining messages, contexts, or raw values."""
    code: object = None
    error = envelope.get("error")
    if envelope.get("success") is False and isinstance(error, dict):
        code = error.get("code")
    elif type(error) is str:
        code = error
    normalized = _normalized_code(code)
    error_context = _context(endpoint_id, context)
    error_context["http_status"] = status_code
    if normalized == "signatureexpired":
        return SignatureExpiredError(context=error_context)
    if normalized == "unauthorizedapiaccess":
        return UnauthorizedApiAccessError(context=error_context)
    if normalized in {
        "invalidapikey",
        "invalidkey",
        "invalidsignature",
        "signaturemismatch",
    }:
        return AuthError(context=error_context)
    return ExchangeProtocolError(context=error_context)


__all__ = ["decode_json_object", "error_from_envelope", "success_result"]
