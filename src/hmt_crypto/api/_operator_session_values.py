"""Pure value validation and token codecs for operator sessions."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Final, TypeGuard
from uuid import UUID

_ENTROPY_BYTES: Final = 32
_TOKEN_TEXT_LENGTH: Final = 43
_HASH_TEXT_LENGTH: Final = 64
_MIN_YEAR: Final = 2000
_MAX_YEAR_EXCLUSIVE: Final = 2100
_MAX_IDLE_SECONDS: Final = 24 * 60 * 60
_MAX_ABSOLUTE_SECONDS: Final = 30 * 24 * 60 * 60
_MAX_REVISION: Final = (1 << 63) - 1
_TOKEN_DOMAIN: Final = b"hmt-crypto:operator-session-token:v1\x00"
_CSRF_DOMAIN: Final = b"hmt-crypto:operator-session-csrf:v1\x00"
_LOWER_HEX: Final = frozenset("0123456789abcdef")


def _valid_record_fields(
    *,
    session_id: object,
    token_sha256: object,
    csrf_sha256: object,
    issued_at: object,
    last_seen_at: object,
    idle_expires_at: object,
    absolute_expires_at: object,
    revoked_at: object,
    idle_timeout_seconds: object,
    revision: object,
) -> bool:
    if not _valid_session_id(session_id):
        return False
    if not _valid_hash(token_sha256) or not _valid_hash(csrf_sha256):
        return False
    if token_sha256 == csrf_sha256:
        return False
    if not _canonical_utc(issued_at):
        return False
    if not _canonical_utc(last_seen_at):
        return False
    if not _canonical_utc(idle_expires_at):
        return False
    if not _canonical_utc(absolute_expires_at):
        return False
    if not (issued_at <= last_seen_at < idle_expires_at <= absolute_expires_at):
        return False
    idle_seconds = idle_timeout_seconds
    if type(idle_seconds) is not int or not 1 <= idle_seconds <= _MAX_IDLE_SECONDS:
        return False
    expected_idle = min(
        last_seen_at + timedelta(seconds=idle_seconds),
        absolute_expires_at,
    )
    if idle_expires_at != expected_idle:
        return False
    absolute_duration = absolute_expires_at - issued_at
    if absolute_duration.microseconds != 0:
        return False
    if not (
        timedelta(seconds=idle_seconds)
        <= absolute_duration
        <= timedelta(seconds=_MAX_ABSOLUTE_SECONDS)
    ):
        return False
    if revoked_at is not None:
        if not _canonical_utc(revoked_at) or revoked_at < last_seen_at:
            return False
    minimum_revision = 1 + int(last_seen_at > issued_at) + int(revoked_at is not None)
    return type(revision) is int and minimum_revision <= revision <= _MAX_REVISION


def _canonical_utc(value: object) -> TypeGuard[datetime]:
    return (
        type(value) is datetime
        and value.tzinfo is UTC
        and value.fold == 0
        and _MIN_YEAR <= value.year < _MAX_YEAR_EXCLUSIVE
    )


def _duration_seconds(value: object, *, maximum: int) -> int | None:
    if type(value) is not timedelta or value.microseconds != 0:
        return None
    seconds = value.days * 86_400 + value.seconds
    if not 1 <= seconds <= maximum:
        return None
    return seconds


def _valid_entropy(value: object) -> TypeGuard[bytes]:
    return type(value) is bytes and len(value) == _ENTROPY_BYTES


def _valid_hash(value: object) -> TypeGuard[str]:
    return (
        type(value) is str
        and len(value) == _HASH_TEXT_LENGTH
        and all(character in _LOWER_HEX for character in value)
    )


def _encode_token(entropy: bytes) -> str:
    return base64.urlsafe_b64encode(entropy).rstrip(b"=").decode("ascii")


def _domain_hash(domain: bytes, entropy: bytes) -> str:
    return hashlib.sha256(domain + entropy).hexdigest()


def _token_hash(token: object, *, domain: bytes) -> str | None:
    if type(token) is not str or len(token) != _TOKEN_TEXT_LENGTH:
        return None
    try:
        encoded = token.encode("ascii")
        entropy = base64.b64decode(encoded + b"=", altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):
        return None
    if len(entropy) != _ENTROPY_BYTES or _encode_token(entropy) != token:
        return None
    return _domain_hash(domain, entropy)


def _valid_session_id(value: object) -> TypeGuard[UUID]:
    return type(value) is UUID and value.int != 0
