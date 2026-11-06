"""Pure, persistence-neutral single-operator session and CSRF contracts."""

from __future__ import annotations

import hmac
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Never, TypeGuard, final
from uuid import UUID

from hmt_crypto.api._operator_session_values import (
    _CSRF_DOMAIN,
    _MAX_ABSOLUTE_SECONDS,
    _MAX_IDLE_SECONDS,
    _MAX_REVISION,
    _TOKEN_DOMAIN,
    _canonical_utc,
    _domain_hash,
    _duration_seconds,
    _encode_token,
    _token_hash,
    _valid_entropy,
    _valid_record_fields,
    _valid_session_id,
)

_ERROR_TEXT: Final = "invalid operator session input"


class SessionInputError(ValueError):
    """Report an invalid trusted session input without reflecting its value."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(_ERROR_TEXT)


class SessionDecisionReason(StrEnum):
    """Stable reasons returned by bearer-session authentication."""

    ACCEPTED = "accepted"
    INVALID_TOKEN = "invalid_token"
    REVOKED = "revoked"
    IDLE_EXPIRED = "idle_expired"
    ABSOLUTE_EXPIRED = "absolute_expired"


@dataclass(frozen=True, slots=True, init=False)
class OperatorSessionRecord:
    """Persistable, CAS-ready session state containing hashes only."""

    session_id: UUID
    token_sha256: str
    csrf_sha256: str
    issued_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None
    idle_timeout_seconds: int
    revision: int

    def __init__(
        self,
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
    ) -> None:
        try:
            valid = _valid_record_fields(
                session_id=session_id,
                token_sha256=token_sha256,
                csrf_sha256=csrf_sha256,
                issued_at=issued_at,
                last_seen_at=last_seen_at,
                idle_expires_at=idle_expires_at,
                absolute_expires_at=absolute_expires_at,
                revoked_at=revoked_at,
                idle_timeout_seconds=idle_timeout_seconds,
                revision=revision,
            )
        except BaseException:
            valid = False
        if not valid:
            del (
                session_id,
                token_sha256,
                csrf_sha256,
                issued_at,
                last_seen_at,
                idle_expires_at,
                absolute_expires_at,
                revoked_at,
                idle_timeout_seconds,
                revision,
            )
            _raise_input_error()
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "token_sha256", token_sha256)
        object.__setattr__(self, "csrf_sha256", csrf_sha256)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "last_seen_at", last_seen_at)
        object.__setattr__(self, "idle_expires_at", idle_expires_at)
        object.__setattr__(self, "absolute_expires_at", absolute_expires_at)
        object.__setattr__(self, "revoked_at", revoked_at)
        object.__setattr__(self, "idle_timeout_seconds", idle_timeout_seconds)
        object.__setattr__(self, "revision", revision)


@dataclass(frozen=True, slots=True, repr=False)
class IssuedOperatorSession:
    """One-time raw bearer/CSRF delivery paired with its hash-only record."""

    session_token: str
    csrf_token: str
    record: OperatorSessionRecord

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(session_token=<redacted>, csrf_token=<redacted>, "
            f"record={self.record!r})"
        )


@dataclass(frozen=True, slots=True)
class SessionDecision:
    """An authentication decision and optional CAS replacement record."""

    accepted: bool
    reason: SessionDecisionReason
    record: OperatorSessionRecord | None


@final
class _InputFailure:
    __slots__ = ()


_INPUT_FAILURE: Final = _InputFailure()


def issue_session(
    *,
    session_id: object,
    token_entropy: object,
    csrf_entropy: object,
    now: object,
    idle_timeout: object,
    absolute_timeout: object,
) -> IssuedOperatorSession:
    """Issue raw tokens once and return the hash-only initial session record."""
    try:
        outcome = _try_issue(
            session_id=session_id,
            token_entropy=token_entropy,
            csrf_entropy=csrf_entropy,
            now=now,
            idle_timeout=idle_timeout,
            absolute_timeout=absolute_timeout,
        )
    except BaseException:
        outcome = _INPUT_FAILURE
    finally:
        del session_id, token_entropy, csrf_entropy, now, idle_timeout, absolute_timeout
    if isinstance(outcome, _InputFailure):
        _raise_input_error()
    return outcome


def authenticate_session(
    record: object,
    *,
    session_token: object,
    now: object,
    idle_timeout: object,
) -> SessionDecision:
    """Authenticate a bearer token and return a versioned idle-touch record."""
    try:
        outcome = _try_authenticate(
            record,
            session_token=session_token,
            now=now,
            idle_timeout=idle_timeout,
        )
    except BaseException:
        outcome = _INPUT_FAILURE
    finally:
        del record, session_token, now, idle_timeout
    if isinstance(outcome, _InputFailure):
        _raise_input_error()
    return outcome


def authorize_csrf(
    record: object,
    *,
    session_token: object,
    csrf_token: object,
    now: object,
    idle_timeout: object,
) -> bool:
    """Verify CSRF separately, but only after authenticating a live bearer session."""
    try:
        outcome = _try_authorize_csrf(
            record,
            session_token=session_token,
            csrf_token=csrf_token,
            now=now,
            idle_timeout=idle_timeout,
        )
    except BaseException:
        outcome = _INPUT_FAILURE
    finally:
        del record, session_token, csrf_token, now, idle_timeout
    if isinstance(outcome, _InputFailure):
        _raise_input_error()
    return outcome


def revoke_session(record: object, *, now: object) -> OperatorSessionRecord:
    """Revoke a live or expired record with one monotonic CAS revision."""
    try:
        outcome = _try_revoke(record, now=now)
    except BaseException:
        outcome = _INPUT_FAILURE
    finally:
        del record, now
    if isinstance(outcome, _InputFailure):
        _raise_input_error()
    return outcome


def _try_issue(
    *,
    session_id: object,
    token_entropy: object,
    csrf_entropy: object,
    now: object,
    idle_timeout: object,
    absolute_timeout: object,
) -> IssuedOperatorSession | _InputFailure:
    try:
        if not _valid_session_id(session_id):
            return _INPUT_FAILURE
        if not _valid_entropy(token_entropy):
            return _INPUT_FAILURE
        if not _valid_entropy(csrf_entropy):
            return _INPUT_FAILURE
        if token_entropy == csrf_entropy or not _canonical_utc(now):
            return _INPUT_FAILURE
        idle_seconds = _duration_seconds(idle_timeout, maximum=_MAX_IDLE_SECONDS)
        absolute_seconds = _duration_seconds(
            absolute_timeout,
            maximum=_MAX_ABSOLUTE_SECONDS,
        )
        if idle_seconds is None or absolute_seconds is None or absolute_seconds < idle_seconds:
            return _INPUT_FAILURE

        token_text = _encode_token(token_entropy)
        csrf_text = _encode_token(csrf_entropy)
        record = OperatorSessionRecord(
            session_id=session_id,
            token_sha256=_domain_hash(_TOKEN_DOMAIN, token_entropy),
            csrf_sha256=_domain_hash(_CSRF_DOMAIN, csrf_entropy),
            issued_at=now,
            last_seen_at=now,
            idle_expires_at=now + timedelta(seconds=idle_seconds),
            absolute_expires_at=now + timedelta(seconds=absolute_seconds),
            revoked_at=None,
            idle_timeout_seconds=idle_seconds,
            revision=1,
        )
        return IssuedOperatorSession(
            session_token=token_text,
            csrf_token=csrf_text,
            record=record,
        )
    except BaseException:
        return _INPUT_FAILURE


def _try_authenticate(
    record: object,
    *,
    session_token: object,
    now: object,
    idle_timeout: object,
) -> SessionDecision | _InputFailure:
    try:
        if not _valid_record(record) or not _canonical_utc(now):
            return _INPUT_FAILURE
        reason = _live_bearer_reason(
            record,
            session_token=session_token,
            now=now,
            idle_timeout=idle_timeout,
        )
        if isinstance(reason, _InputFailure):
            return reason
        if reason is not SessionDecisionReason.ACCEPTED:
            return SessionDecision(accepted=False, reason=reason, record=None)
        if record.revision >= _MAX_REVISION:
            return _INPUT_FAILURE
        next_idle_expiry = min(
            now + timedelta(seconds=record.idle_timeout_seconds),
            record.absolute_expires_at,
        )
        touched = replace(
            record,
            last_seen_at=now,
            idle_expires_at=next_idle_expiry,
            revision=record.revision + 1,
        )
        return SessionDecision(
            accepted=True,
            reason=SessionDecisionReason.ACCEPTED,
            record=touched,
        )
    except BaseException:
        return _INPUT_FAILURE


def _try_authorize_csrf(
    record: object,
    *,
    session_token: object,
    csrf_token: object,
    now: object,
    idle_timeout: object,
) -> bool | _InputFailure:
    try:
        if not _valid_record(record):
            return _INPUT_FAILURE
        reason = _live_bearer_reason(
            record,
            session_token=session_token,
            now=now,
            idle_timeout=idle_timeout,
        )
        if isinstance(reason, _InputFailure):
            return reason
        if reason is not SessionDecisionReason.ACCEPTED:
            return False
        candidate_hash = _token_hash(csrf_token, domain=_CSRF_DOMAIN)
        if candidate_hash is None:
            return False
        if not hmac.compare_digest(candidate_hash, record.csrf_sha256):
            return False
        if record.revision >= _MAX_REVISION:
            return _INPUT_FAILURE
        return True
    except BaseException:
        return _INPUT_FAILURE


def _try_revoke(record: object, *, now: object) -> OperatorSessionRecord | _InputFailure:
    try:
        if not _valid_record(record):
            return _INPUT_FAILURE
        if (
            not _canonical_utc(now)
            or now < record.last_seen_at
            or (record.revoked_at is not None and now < record.revoked_at)
        ):
            return _INPUT_FAILURE
        if record.revoked_at is not None:
            return record
        if record.revision >= _MAX_REVISION:
            return _INPUT_FAILURE
        return replace(record, revoked_at=now, revision=record.revision + 1)
    except BaseException:
        return _INPUT_FAILURE


def _live_bearer_reason(
    record: object,
    *,
    session_token: object,
    now: object,
    idle_timeout: object,
) -> SessionDecisionReason | _InputFailure:
    if not _valid_record(record):
        return _INPUT_FAILURE
    if (
        not _canonical_utc(now)
        or now < record.last_seen_at
        or (record.revoked_at is not None and now < record.revoked_at)
    ):
        return _INPUT_FAILURE
    idle_seconds = _duration_seconds(idle_timeout, maximum=_MAX_IDLE_SECONDS)
    if idle_seconds is None or idle_seconds != record.idle_timeout_seconds:
        return _INPUT_FAILURE
    candidate_hash = _token_hash(session_token, domain=_TOKEN_DOMAIN)
    if candidate_hash is None or not hmac.compare_digest(candidate_hash, record.token_sha256):
        return SessionDecisionReason.INVALID_TOKEN
    if record.revoked_at is not None:
        return SessionDecisionReason.REVOKED
    if now >= record.absolute_expires_at:
        return SessionDecisionReason.ABSOLUTE_EXPIRED
    if now >= record.idle_expires_at:
        return SessionDecisionReason.IDLE_EXPIRED
    return SessionDecisionReason.ACCEPTED


def _valid_record(record: object) -> TypeGuard[OperatorSessionRecord]:
    if type(record) is not OperatorSessionRecord:
        return False
    return _valid_record_fields(
        session_id=record.session_id,
        token_sha256=record.token_sha256,
        csrf_sha256=record.csrf_sha256,
        issued_at=record.issued_at,
        last_seen_at=record.last_seen_at,
        idle_expires_at=record.idle_expires_at,
        absolute_expires_at=record.absolute_expires_at,
        revoked_at=record.revoked_at,
        idle_timeout_seconds=record.idle_timeout_seconds,
        revision=record.revision,
    )


def _raise_input_error() -> Never:
    raise SessionInputError() from None


__all__ = [
    "IssuedOperatorSession",
    "OperatorSessionRecord",
    "SessionDecision",
    "SessionDecisionReason",
    "SessionInputError",
    "authenticate_session",
    "authorize_csrf",
    "issue_session",
    "revoke_session",
]
