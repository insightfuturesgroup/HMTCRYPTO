"""Bounded, hash-only operator authentication and session ownership."""

from __future__ import annotations

import asyncio
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from hmt_crypto.api._operator_session_values import _TOKEN_DOMAIN, _token_hash
from hmt_crypto.api.operator_sessions import (
    IssuedOperatorSession,
    OperatorSessionRecord,
    authenticate_session,
    authorize_csrf,
    issue_session,
    revoke_session,
)

_PASSWORD_MIN: Final = 12
_PASSWORD_MAX: Final = 1024


class AuthenticationError(RuntimeError):
    """Report one intentionally indistinguishable authentication failure."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("authentication failed")


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """An authenticated hash-only session record and its lookup identity."""

    lookup_hash: str
    record: OperatorSessionRecord


class Argon2PasswordVerifier:
    """Own one Argon2id hash without retaining a plaintext password."""

    __slots__ = ("_encoded_hash", "_hasher")

    def __init__(self, encoded_hash: str) -> None:
        if type(encoded_hash) is not str or not encoded_hash.startswith("$argon2id$"):
            raise AuthenticationError() from None
        self._encoded_hash = encoded_hash
        self._hasher = PasswordHasher()

    def verify(self, password: object) -> bool:
        """Verify one bounded exact string and collapse all hash diagnostics."""
        if type(password) is not str or not _PASSWORD_MIN <= len(password) <= _PASSWORD_MAX:
            return False
        try:
            return self._hasher.verify(self._encoded_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        except BaseException:
            return False

    async def verify_async(self, password: object) -> bool:
        """Run memory-hard verification off the event-loop thread."""
        return await asyncio.to_thread(self.verify, password)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(encoded_hash=<redacted>)"


class OperatorSessionStore:
    """Own bounded server-side session hashes with serialized CAS-style touches."""

    __slots__ = (
        "_absolute_timeout",
        "_clock",
        "_entropy",
        "_idle_timeout",
        "_lock",
        "_max_sessions",
        "_records",
        "_uuid_factory",
    )

    def __init__(
        self,
        *,
        idle_timeout: timedelta,
        absolute_timeout: timedelta,
        max_sessions: int = 8,
        clock: Callable[[], datetime] | None = None,
        entropy: Callable[[int], bytes] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        if type(max_sessions) is not int or not 1 <= max_sessions <= 32:
            raise AuthenticationError() from None
        self._idle_timeout = idle_timeout
        self._absolute_timeout = absolute_timeout
        self._max_sessions = max_sessions
        self._clock = clock or (lambda: datetime.now(UTC))
        self._entropy = entropy or secrets.token_bytes
        self._uuid_factory = uuid_factory or uuid4
        self._records: dict[str, OperatorSessionRecord] = {}
        self._lock = asyncio.Lock()

    async def issue(self) -> IssuedOperatorSession:
        """Issue a session, retaining only hashes after returning one-time tokens."""
        now = self._now()
        try:
            issued = issue_session(
                session_id=self._uuid_factory(),
                token_entropy=self._entropy(32),
                csrf_entropy=self._entropy(32),
                now=now,
                idle_timeout=self._idle_timeout,
                absolute_timeout=self._absolute_timeout,
            )
        except BaseException:
            raise AuthenticationError() from None
        async with self._lock:
            self._prune(now)
            if len(self._records) >= self._max_sessions:
                oldest = min(
                    self._records,
                    key=lambda key: (self._records[key].last_seen_at, key),
                )
                del self._records[oldest]
            self._records[issued.record.token_sha256] = issued.record
        return issued

    async def authenticate(self, session_token: object) -> AuthenticatedSession | None:
        """Authenticate and atomically publish the idle-expiry touch."""
        lookup = self._lookup_hash(session_token)
        if lookup is None:
            return None
        now = self._now()
        async with self._lock:
            record = self._records.get(lookup)
            if record is None:
                return None
            try:
                decision = authenticate_session(
                    record,
                    session_token=session_token,
                    now=now,
                    idle_timeout=self._idle_timeout,
                )
            except BaseException:
                self._records.pop(lookup, None)
                return None
            if not decision.accepted or decision.record is None:
                self._records.pop(lookup, None)
                return None
            self._records[lookup] = decision.record
            return AuthenticatedSession(lookup_hash=lookup, record=decision.record)

    async def authorize_csrf(
        self,
        authenticated: AuthenticatedSession,
        *,
        session_token: object,
        csrf_cookie: object,
        csrf_header: object,
    ) -> bool:
        """Require an exact double-submit value and the server-side CSRF hash."""
        if type(csrf_cookie) is not str or type(csrf_header) is not str:
            return False
        if not hmac.compare_digest(csrf_cookie, csrf_header):
            return False
        async with self._lock:
            current = self._records.get(authenticated.lookup_hash)
            if current is None or current != authenticated.record:
                return False
            try:
                return authorize_csrf(
                    current,
                    session_token=session_token,
                    csrf_token=csrf_header,
                    now=self._now(),
                    idle_timeout=self._idle_timeout,
                )
            except BaseException:
                return False

    async def revoke(self, authenticated: AuthenticatedSession) -> None:
        """Revoke and remove one exact current session idempotently."""
        async with self._lock:
            current = self._records.get(authenticated.lookup_hash)
            if current is None:
                return
            if current == authenticated.record:
                try:
                    revoke_session(current, now=self._now())
                except BaseException:
                    pass
                self._records.pop(authenticated.lookup_hash, None)

    def _now(self) -> datetime:
        try:
            now = self._clock()
        except BaseException:
            raise AuthenticationError() from None
        if type(now) is not datetime or now.tzinfo is not UTC or now.fold != 0:
            raise AuthenticationError() from None
        return now

    def _prune(self, now: datetime) -> None:
        stale = [
            key
            for key, record in self._records.items()
            if record.revoked_at is not None
            or now >= record.idle_expires_at
            or now >= record.absolute_expires_at
        ]
        for key in stale:
            del self._records[key]

    @staticmethod
    def _lookup_hash(session_token: object) -> str | None:
        try:
            return _token_hash(session_token, domain=_TOKEN_DOMAIN)
        except BaseException:
            return None


__all__ = [
    "Argon2PasswordVerifier",
    "AuthenticatedSession",
    "AuthenticationError",
    "OperatorSessionStore",
]
