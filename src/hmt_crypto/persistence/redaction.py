"""Fail-closed redaction and bounded HTTP audit summaries."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import TypeAlias
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import UUID

import httpx
from pydantic import SecretStr

REDACTED = "[REDACTED]"
_CYCLE = "[CYCLE]"
_MAX_DEPTH = "[MAX_DEPTH]"
_MAX_REDACTION_DEPTH = 32

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

_SENSITIVE_COMPACT_NAMES = frozenset(
    {
        "apikey",
        "apisecret",
        "authorization",
        "clientsecret",
        "cookie",
        "credentials",
        "password",
        "passphrase",
        "privatekey",
        "proxyauthorization",
        "refreshtoken",
        "setcookie",
        "signature",
        "signedprehash",
        "signingpayload",
        "token",
        "accesstoken",
    }
)
_SENSITIVE_SEGMENTS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passphrase",
        "prehash",
        "secret",
        "signature",
        "token",
    }
)
_SAFE_HEADER_NAMES = frozenset(
    {
        "content-length",
        "content-type",
        "date",
        "retry-after",
        "user-agent",
        "x-correlation-id",
        "x-request-id",
    }
)
_NORMALIZE_NAME = re.compile(r"[^a-z0-9]+")


def _normalized_name(name: str) -> tuple[str, str, tuple[str, ...]]:
    normalized = _NORMALIZE_NAME.sub("_", name.casefold()).strip("_")
    compact = normalized.replace("_", "")
    return normalized, compact, tuple(part for part in normalized.split("_") if part)


def _is_sensitive_name(name: str) -> bool:
    _normalized, compact, parts = _normalized_name(name)
    if compact in _SENSITIVE_COMPACT_NAMES:
        return True
    if any(part in _SENSITIVE_SEGMENTS for part in parts):
        return True
    return ("api" in parts and "key" in parts) or ("private" in parts and "key" in parts)


def _safe_key(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool, Decimal, UUID)):
        return str(value)
    return f"<{type(value).__name__}>"


def _redact_mapping_value(name: str, value: object, *, seen: set[int], depth: int) -> JsonValue:
    if _is_sensitive_name(name):
        return REDACTED
    normalized, _compact, parts = _normalized_name(name)
    if "url" in parts and isinstance(value, (str, httpx.URL)):
        return redact_url(value)
    if normalized in {"body", "request_body", "response_body", "payload"} and isinstance(
        value, (str, bytes, bytearray, memoryview)
    ):
        return safe_body_summary(value)
    if normalized in {"header", "headers", "request_headers", "response_headers"} and isinstance(
        value, Mapping
    ):
        string_headers = {
            _safe_key(header_name): header_value
            for header_name, header_value in value.items()
            if isinstance(header_value, str)
        }
        return _safe_headers(string_headers)
    return _redact(value, seen=seen, depth=depth)


def _redact(value: object, *, seen: set[int], depth: int) -> JsonValue:
    if depth > _MAX_REDACTION_DEPTH:
        return _MAX_DEPTH
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, (Decimal, UUID, datetime, date)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return {"byte_length": len(raw), "sha256": sha256(raw).hexdigest()}

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return _CYCLE
        seen.add(identity)
        try:
            return {
                (key := _safe_key(raw_key)): _redact_mapping_value(
                    key, raw_value, seen=seen, depth=depth + 1
                )
                for raw_key, raw_value in value.items()
            }
        finally:
            seen.remove(identity)

    if isinstance(value, AbstractSet):
        identity = id(value)
        if identity in seen:
            return _CYCLE
        seen.add(identity)
        try:
            redacted_items = [_redact(item, seen=seen, depth=depth + 1) for item in value]
            return sorted(redacted_items, key=lambda item: json.dumps(item, sort_keys=True))
        finally:
            seen.remove(identity)

    if isinstance(value, Sequence):
        identity = id(value)
        if identity in seen:
            return _CYCLE
        seen.add(identity)
        try:
            return [_redact(item, seen=seen, depth=depth + 1) for item in value]
        finally:
            seen.remove(identity)

    return f"<{type(value).__name__}>"


def redact(value: object) -> JsonValue:
    """Return a recursively redacted, JSON-safe copy without mutating ``value``."""
    return _redact(value, seen=set(), depth=0)


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Normalize header names and redact every known authentication field."""
    return {
        name.casefold(): REDACTED if _is_sensitive_name(name) else value
        for name, value in headers.items()
    }


def _safe_headers(headers: Mapping[str, str]) -> dict[str, JsonValue]:
    redacted = redact_headers(headers)
    return {
        name: value
        for name, value in redacted.items()
        if name in _SAFE_HEADER_NAMES
        or name.startswith("x-rate-limit-")
        or name.startswith("x-ratelimit-")
    }


def redact_url(url: str | httpx.URL) -> str:
    """Redact URL user information and sensitive query parameter values."""
    try:
        parsed = urlsplit(str(url))
        hostname = parsed.hostname
        if not hostname:
            return "<invalid-url>"
        safe_host = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None:
            safe_host = f"{safe_host}:{parsed.port}"
        userinfo = f"{quote(REDACTED, safe='')}@" if parsed.username is not None else ""
        safe_query = urlencode(
            [
                (name, REDACTED if _is_sensitive_name(name) else value)
                for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            ],
            doseq=True,
        )
        return urlunsplit((parsed.scheme, f"{userinfo}{safe_host}", parsed.path, safe_query, ""))
    except (TypeError, ValueError):
        return "<invalid-url>"


def _body_bytes(body: str | bytes | bytearray | memoryview | None) -> bytes:
    if body is None:
        return b""
    if isinstance(body, str):
        return body.encode("utf-8")
    return bytes(body)


def safe_body_summary(
    body: str | bytes | bytearray | memoryview | None,
    *,
    allowlisted_fields: AbstractSet[str] = frozenset(),
) -> dict[str, JsonValue]:
    """Summarize buffered body bytes without returning their raw contents."""
    raw = _body_bytes(body)
    summary: dict[str, JsonValue] = {
        "byte_length": len(raw),
        "sha256": sha256(raw).hexdigest(),
    }
    if not raw or not allowlisted_fields:
        return summary
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return summary
    if not isinstance(parsed, dict):
        return summary
    fields: dict[str, JsonValue] = {}
    for name in sorted(allowlisted_fields):
        if name not in parsed:
            continue
        fields[name] = REDACTED if _is_sensitive_name(name) else redact(parsed[name])
    if fields:
        summary["fields"] = fields
    return summary


def _buffered_request_body(request: httpx.Request) -> bytes | None:
    try:
        return request.content
    except httpx.RequestNotRead:
        return None


def _buffered_response_body(response: httpx.Response) -> bytes | None:
    try:
        return response.content
    except httpx.ResponseNotRead:
        return None


def safe_request_summary(
    request: httpx.Request,
    *,
    allowlisted_body_fields: AbstractSet[str] = frozenset(),
) -> dict[str, JsonValue]:
    """Build a safe request summary without reading a streaming request body."""
    body = _buffered_request_body(request)
    body_summary: dict[str, JsonValue] = (
        {"streaming": True}
        if body is None
        else safe_body_summary(body, allowlisted_fields=allowlisted_body_fields)
    )
    return {
        "method": request.method.upper(),
        "url": redact_url(request.url),
        "headers": _safe_headers(request.headers),
        "body": body_summary,
    }


def safe_response_summary(
    response: httpx.Response,
    *,
    allowlisted_body_fields: AbstractSet[str] = frozenset(),
) -> dict[str, JsonValue]:
    """Build a safe response summary without reading a streaming response body."""
    body = _buffered_response_body(response)
    body_summary: dict[str, JsonValue] = (
        {"streaming": True}
        if body is None
        else safe_body_summary(body, allowlisted_fields=allowlisted_body_fields)
    )
    return {
        "status_code": response.status_code,
        "headers": _safe_headers(response.headers),
        "body": body_summary,
    }


def safe_exception_summary(error: BaseException) -> dict[str, JsonValue]:
    """Describe an exception without rendering its potentially sensitive message."""
    summary: dict[str, JsonValue] = {"type": type(error).__name__}
    if isinstance(error, httpx.HTTPError):
        try:
            summary["request"] = safe_request_summary(error.request)
        except RuntimeError:
            pass
    if isinstance(error, httpx.HTTPStatusError):
        summary["response"] = safe_response_summary(error.response)
    return summary
