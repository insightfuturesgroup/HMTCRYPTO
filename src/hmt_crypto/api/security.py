"""Bounded in-process admission helpers for HTTP authentication and previews."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from hmt_crypto.api.control_plane import KillImpact


class AdmissionRejected(RuntimeError):
    """Report a safe HTTP admission rejection."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("request rejected")


class LoginRateLimiter:
    """Bound failed-login history without allowing peer-cardinality growth."""

    __slots__ = ("_attempts", "_clock", "_limit", "_lock", "_max_peers", "_window")

    def __init__(
        self,
        limit: int,
        *,
        window_seconds: float = 300.0,
        max_peers: int = 4096,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(max_peers) is not int
            or not 32 <= max_peers <= 10_000
            or type(window_seconds) not in {int, float}
            or not 1 <= float(window_seconds) <= 3600
        ):
            raise AdmissionRejected() from None
        self._limit = limit
        self._max_peers = max_peers
        self._window = float(window_seconds)
        self._clock = clock or time.monotonic
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def peer_bucket_count(self) -> int:
        """Return the bounded in-process peer count for diagnostics/tests."""
        return len(self._attempts)

    def _prune(self, cutoff: float) -> None:
        stale: list[str] = []
        for peer, attempts in self._attempts.items():
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if not attempts:
                stale.append(peer)
        for peer in stale:
            self._attempts.pop(peer, None)

    async def record_failure(self, peer: str) -> bool:
        """Record one failed verification; false means the peer window is full."""
        if type(peer) is not str or not peer or len(peer) > 128:
            return False
        now = self._clock()
        if type(now) not in {int, float}:
            return False
        observed = float(now)
        async with self._lock:
            self._prune(observed - self._window)
            attempts = self._attempts.get(peer)
            if attempts is None:
                while len(self._attempts) >= self._max_peers:
                    self._attempts.popitem(last=False)
                attempts = deque()
                self._attempts[peer] = attempts
            else:
                self._attempts.move_to_end(peer)
            if len(attempts) >= self._limit:
                return False
            attempts.append(observed)
            return True

    async def clear(self, peer: str) -> None:
        """Clear prior failures after a valid operator credential is proven."""
        if type(peer) is not str or not peer or len(peer) > 128:
            return
        async with self._lock:
            self._attempts.pop(peer, None)


@dataclass(frozen=True, slots=True)
class KillPreviewRecord:
    """Short-lived session-bound evidence for a deliberate kill command."""

    preview_id: UUID
    session_id: UUID
    flatten: bool
    impact: KillImpact
    expires_at: datetime


class KillPreviewRegistry:
    """Own bounded one-time preview evidence; durable commands live elsewhere."""

    __slots__ = ("_bindings", "_clock", "_lifetime", "_lock", "_max_records", "_records")

    def __init__(
        self,
        *,
        lifetime: timedelta = timedelta(seconds=60),
        max_records: int = 256,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            type(lifetime) is not timedelta
            or not timedelta(seconds=10) <= lifetime <= timedelta(minutes=5)
            or type(max_records) is not int
            or not 1 <= max_records <= 4096
        ):
            raise AdmissionRejected() from None
        self._lifetime = lifetime
        self._max_records = max_records
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: dict[UUID, KillPreviewRecord] = {}
        self._bindings: dict[UUID, UUID] = {}
        self._lock = asyncio.Lock()

    async def issue(
        self,
        *,
        session_id: UUID,
        flatten: bool,
        impact: KillImpact,
    ) -> KillPreviewRecord:
        """Mint a bounded preview after authoritative impact calculation."""
        now = self._now()
        record = KillPreviewRecord(
            preview_id=uuid4(),
            session_id=session_id,
            flatten=flatten,
            impact=impact,
            expires_at=now + self._lifetime,
        )
        async with self._lock:
            self._prune(now)
            if len(self._records) >= self._max_records:
                oldest = min(
                    self._records,
                    key=lambda key: (self._records[key].expires_at, key.int),
                )
                del self._records[oldest]
                self._bindings.pop(oldest, None)
            self._records[record.preview_id] = record
        return record

    async def bind_request(
        self,
        *,
        preview_id: UUID,
        session_id: UUID,
        flatten: bool,
        request_id: UUID,
    ) -> KillPreviewRecord | None:
        """Bind one exact request identity, allowing only its idempotent replays."""
        now = self._now()
        async with self._lock:
            self._prune(now)
            record = self._records.get(preview_id)
            if record is None or record.session_id != session_id or record.flatten is not flatten:
                return None
            bound = self._bindings.get(preview_id)
            if bound is not None and bound != request_id:
                return None
            self._bindings[preview_id] = request_id
            return record

    def _now(self) -> datetime:
        now = self._clock()
        if type(now) is not datetime or now.tzinfo is not UTC or now.fold != 0:
            raise AdmissionRejected() from None
        return now

    def _prune(self, now: datetime) -> None:
        stale = [key for key, value in self._records.items() if now >= value.expires_at]
        for key in stale:
            del self._records[key]
            self._bindings.pop(key, None)


__all__ = [
    "AdmissionRejected",
    "KillPreviewRecord",
    "KillPreviewRegistry",
    "LoginRateLimiter",
]
