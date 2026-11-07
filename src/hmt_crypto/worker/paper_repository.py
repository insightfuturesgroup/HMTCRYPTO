"""Transactional journal repository for offline PAPER/BACKTEST execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hmt_crypto.persistence.identity_models import Account
from hmt_crypto.persistence.paper_execution_models import (
    PAPER_EVENT_KINDS,
    PAPER_HEAD_STATUSES,
    PaperExecutionEvent,
    PaperExecutionHead,
    encode_paper_event_payload,
)
from hmt_crypto.persistence.repository_contracts import AccountScope


class PaperJournalConflict(Exception):
    """A replay or compare-and-swap identity conflicts with durable evidence."""


@dataclass(frozen=True, slots=True)
class JournalEvent:
    """One canonical append request."""

    event_kind: str
    logical_key: str
    occurred_at: datetime
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if self.event_kind not in PAPER_EVENT_KINDS:
            raise ValueError("unknown paper journal event kind")
        if (
            type(self.logical_key) is not str
            or not 1 <= len(self.logical_key) <= 256
            or "\x00" in self.logical_key
            or type(self.occurred_at) is not datetime
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() != UTC.utcoffset(self.occurred_at)
            or type(self.payload) is not dict
        ):
            raise ValueError("invalid paper journal event")
        encode_paper_event_payload(self.payload)


@dataclass(frozen=True, slots=True)
class PaperJournalHead:
    """Immutable checkpoint receipt."""

    scope: AccountScope
    head_id: UUID
    request_id: str
    intent_sha256: str
    status: str
    last_sequence: int
    version: int
    claim_owner: UUID | None
    claim_generation: int


@dataclass(frozen=True, slots=True)
class PaperJournalClaim:
    """A versioned exclusive head claim."""

    head: PaperJournalHead
    owner_id: UUID
    generation: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PaperIntentReceipt:
    """Idempotent intent creation result."""

    head: PaperJournalHead
    created: bool


@dataclass(frozen=True, slots=True)
class PaperAppendReceipt:
    """CAS append result."""

    head: PaperJournalHead
    appended: int


@dataclass(frozen=True, slots=True)
class StoredJournalEvent:
    """Owned canonical event returned in replay order."""

    sequence: int
    scope_sequence: int
    event_key: str
    event_kind: str
    occurred_at: datetime
    recorded_at: datetime
    payload: dict[str, object]
    payload_sha256: str


_TERMINAL = frozenset({"DENIED", "COMPLETE", "INCOMPLETE"})


def _scope(value: AccountScope) -> AccountScope:
    if type(value) is not AccountScope or value.mode not in {"PAPER", "BACKTEST"}:
        raise ValueError("offline AccountScope required")
    return AccountScope.model_validate(value)


def _utc(value: datetime) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise ValueError("UTC timestamp required")
    return datetime(
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
        tzinfo=UTC,
        fold=value.fold,
    )


def _predicates(model: Any, scope: AccountScope) -> tuple[Any, ...]:
    return tuple(
        getattr(model, field) == getattr(scope, field)
        for field in (
            "account_id",
            "region",
            "environment",
            "mode",
        )
    )


def _head(scope: AccountScope, row: PaperExecutionHead) -> PaperJournalHead:
    return PaperJournalHead(
        scope=AccountScope.model_validate(scope),
        head_id=row.id,
        request_id=row.request_id,
        intent_sha256=row.intent_sha256,
        status=row.status,
        last_sequence=row.last_sequence,
        version=row.version,
        claim_owner=row.claim_owner,
        claim_generation=row.claim_generation,
    )


def _event_key(scope: AccountScope, request_id: str, event_kind: str, logical_key: str) -> str:
    value = json.dumps(
        {
            "account_id": str(scope.account_id),
            "environment": scope.environment,
            "event_kind": event_kind,
            "logical_key": logical_key,
            "mode": scope.mode,
            "region": scope.region,
            "request_id": request_id,
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(value.encode("ascii")).hexdigest()


async def _lock_scope(session: AsyncSession, scope: AccountScope) -> None:
    account_id = await session.scalar(
        select(Account.id)
        .where(
            Account.id == scope.account_id,
            Account.region == scope.region,
            Account.environment == scope.environment,
            Account.mode == scope.mode,
        )
        .with_for_update()
    )
    if account_id != scope.account_id:
        raise PaperJournalConflict("paper account scope does not exist")


async def _last_scope_sequence(session: AsyncSession, scope: AccountScope) -> int:
    value = await session.scalar(
        select(func.max(PaperExecutionEvent.scope_sequence)).where(
            *_predicates(PaperExecutionEvent, scope)
        )
    )
    return 0 if value is None else int(value)


class PaperJournalRepository:
    """Transactional monotonic journal with compare-and-swap claims."""

    async def lock_scope(self, session: AsyncSession, scope: AccountScope) -> None:
        """Serialize aggregate decisions for one exact offline account scope."""

        await _lock_scope(session, _scope(scope))

    async def ensure_intent(
        self,
        session: AsyncSession,
        scope: AccountScope,
        *,
        request_id: str,
        intent_payload: dict[str, object],
        occurred_at: datetime,
    ) -> PaperIntentReceipt:
        """Persist or replay the sole sequence-one durable intent."""

        scope = _scope(scope)
        occurred = _utc(occurred_at)
        if (
            type(request_id) is not str
            or not 1 <= len(request_id) <= 128
            or "\x00" in request_id
            or not request_id.strip()
        ):
            raise ValueError("invalid request identity")
        payload, digest = encode_paper_event_payload(intent_payload)
        await _lock_scope(session, scope)
        statement = select(PaperExecutionHead).where(
            *_predicates(PaperExecutionHead, scope),
            PaperExecutionHead.request_id == request_id,
        )
        existing = await session.scalar(statement)
        if existing is not None:
            first = await session.scalar(
                select(PaperExecutionEvent).where(
                    *_predicates(PaperExecutionEvent, scope),
                    PaperExecutionEvent.head_id == existing.id,
                    PaperExecutionEvent.sequence == 1,
                )
            )
            if (
                existing.intent_sha256 != digest
                or first is None
                or first.event_kind != "INTENT"
                or first.payload != payload
            ):
                raise PaperJournalConflict("request identity conflicts with durable intent")
            return PaperIntentReceipt(_head(scope, existing), False)
        identifier = uuid4()
        scope_sequence = await _last_scope_sequence(session, scope) + 1
        row = PaperExecutionHead(
            id=identifier,
            account_id=scope.account_id,
            region=scope.region,
            environment=scope.environment,
            mode=scope.mode,
            request_id=request_id,
            intent_sha256=digest,
            status="INTENT_RECORDED",
            last_sequence=1,
            claim_owner=None,
            claim_generation=0,
            claim_expires_at=None,
            version=1,
            created_at=occurred,
            updated_at=occurred,
            completed_at=None,
        )
        session.add(row)
        await session.flush()
        session.add(
            PaperExecutionEvent(
                id=uuid4(),
                account_id=scope.account_id,
                region=scope.region,
                environment=scope.environment,
                mode=scope.mode,
                head_id=identifier,
                request_id=request_id,
                sequence=1,
                scope_sequence=scope_sequence,
                event_key=_event_key(scope, request_id, "INTENT", "intent-v1"),
                event_kind="INTENT",
                occurred_at=occurred,
                recorded_at=occurred,
                payload_schema_version=1,
                payload=payload,
                payload_sha256=digest,
            )
        )
        await session.flush()
        return PaperIntentReceipt(_head(scope, row), True)

    async def claim(
        self,
        session: AsyncSession,
        scope: AccountScope,
        request_id: str,
        *,
        owner_id: UUID,
        now: datetime,
        duration: timedelta,
    ) -> PaperJournalClaim | None:
        """Acquire one head by versioned CAS, permitting expiry recovery."""

        scope = _scope(scope)
        sampled = _utc(now)
        if (
            type(owner_id) is not UUID
            or type(duration) is not timedelta
            or duration <= timedelta(0)
            or duration > timedelta(hours=1)
        ):
            raise ValueError("invalid journal claim")
        row = await session.scalar(
            select(PaperExecutionHead).where(
                *_predicates(PaperExecutionHead, scope),
                PaperExecutionHead.request_id == request_id,
            )
        )
        if row is None:
            raise PaperJournalConflict("paper request does not exist")
        if row.status in _TERMINAL:
            return None
        if sampled < row.updated_at:
            raise PaperJournalConflict("claim clock precedes durable checkpoint")
        if (
            row.claim_owner is not None
            and row.claim_owner != owner_id
            and row.claim_expires_at is not None
            and row.claim_expires_at > sampled
        ):
            return None
        expires = sampled + duration
        generation = row.claim_generation + 1
        updated_id = await session.scalar(
            update(PaperExecutionHead)
            .where(
                PaperExecutionHead.id == row.id,
                *_predicates(PaperExecutionHead, scope),
                PaperExecutionHead.version == row.version,
                or_(
                    PaperExecutionHead.claim_owner.is_(None),
                    PaperExecutionHead.claim_owner == owner_id,
                    PaperExecutionHead.claim_expires_at <= sampled,
                ),
            )
            .values(
                claim_owner=owner_id,
                claim_generation=generation,
                claim_expires_at=expires,
                version=row.version + 1,
                updated_at=sampled,
            )
            .returning(PaperExecutionHead.id)
        )
        if updated_id is None:
            return None
        await session.flush()
        refreshed = await session.scalar(
            select(PaperExecutionHead).where(PaperExecutionHead.id == row.id)
        )
        assert refreshed is not None
        receipt = _head(scope, refreshed)
        return PaperJournalClaim(receipt, owner_id, generation, expires)

    async def append(
        self,
        session: AsyncSession,
        claim: PaperJournalClaim,
        *,
        events: tuple[JournalEvent, ...],
        status: str,
        recorded_at: datetime,
    ) -> PaperAppendReceipt:
        """Append novel keys consecutively and advance the claimed head by CAS."""

        if type(claim) is not PaperJournalClaim:
            raise ValueError("exact journal claim required")
        scope = _scope(claim.head.scope)
        recorded = _utc(recorded_at)
        if (
            type(events) is not tuple
            or any(type(item) is not JournalEvent for item in events)
            or status not in PAPER_HEAD_STATUSES
        ):
            raise ValueError("invalid journal append")
        row = await session.scalar(
            select(PaperExecutionHead).where(
                PaperExecutionHead.id == claim.head.head_id,
                *_predicates(PaperExecutionHead, scope),
                PaperExecutionHead.request_id == claim.head.request_id,
            )
        )
        if (
            row is None
            or row.version != claim.head.version
            or row.claim_owner != claim.owner_id
            or row.claim_generation != claim.generation
            or row.claim_expires_at != claim.expires_at
            or row.claim_expires_at is None
            or row.claim_expires_at <= recorded
            or recorded < row.updated_at
            or row.status in _TERMINAL
        ):
            raise PaperJournalConflict("journal claim is stale")
        await _lock_scope(session, scope)
        appended = 0
        sequence = row.last_sequence
        scope_sequence = await _last_scope_sequence(session, scope)
        for item in events:
            payload, digest = encode_paper_event_payload(item.payload)
            key = _event_key(scope, row.request_id, item.event_kind, item.logical_key)
            existing = await session.scalar(
                select(PaperExecutionEvent).where(
                    *_predicates(PaperExecutionEvent, scope),
                    PaperExecutionEvent.event_key == key,
                )
            )
            if existing is not None:
                if (
                    existing.head_id != row.id
                    or existing.event_kind != item.event_kind
                    or existing.occurred_at != item.occurred_at
                    or existing.payload != payload
                    or existing.payload_sha256 != digest
                ):
                    raise PaperJournalConflict("event key conflicts with durable evidence")
                continue
            if item.occurred_at > recorded:
                raise ValueError("event cannot be recorded before it occurred")
            sequence += 1
            scope_sequence += 1
            appended += 1
            session.add(
                PaperExecutionEvent(
                    id=uuid4(),
                    account_id=scope.account_id,
                    region=scope.region,
                    environment=scope.environment,
                    mode=scope.mode,
                    head_id=row.id,
                    request_id=row.request_id,
                    sequence=sequence,
                    scope_sequence=scope_sequence,
                    event_key=key,
                    event_kind=item.event_kind,
                    occurred_at=item.occurred_at,
                    recorded_at=recorded,
                    payload_schema_version=1,
                    payload=payload,
                    payload_sha256=digest,
                )
            )
        await session.flush()
        terminal = status in _TERMINAL
        values: dict[str, object] = {
            "status": status,
            "last_sequence": sequence,
            "version": row.version + 1,
            "updated_at": recorded,
        }
        if terminal:
            values.update(
                claim_owner=None,
                claim_expires_at=None,
                completed_at=recorded,
            )
        updated_id = await session.scalar(
            update(PaperExecutionHead)
            .where(
                PaperExecutionHead.id == row.id,
                *_predicates(PaperExecutionHead, scope),
                PaperExecutionHead.version == row.version,
                PaperExecutionHead.claim_owner == claim.owner_id,
                PaperExecutionHead.claim_generation == claim.generation,
            )
            .values(**values)
            .returning(PaperExecutionHead.id)
        )
        if updated_id is None:
            raise PaperJournalConflict("journal compare-and-swap failed")
        await session.flush()
        refreshed = await session.scalar(
            select(PaperExecutionHead).where(PaperExecutionHead.id == row.id)
        )
        assert refreshed is not None
        return PaperAppendReceipt(_head(scope, refreshed), appended)

    async def load_events(
        self,
        session: AsyncSession,
        scope: AccountScope,
        request_id: str,
    ) -> tuple[StoredJournalEvent, ...]:
        """Load one request in deterministic replay order."""

        scope = _scope(scope)
        rows = (
            await session.scalars(
                select(PaperExecutionEvent)
                .where(
                    *_predicates(PaperExecutionEvent, scope),
                    PaperExecutionEvent.request_id == request_id,
                )
                .order_by(PaperExecutionEvent.sequence)
            )
        ).all()
        return tuple(
            StoredJournalEvent(
                sequence=row.sequence,
                scope_sequence=row.scope_sequence,
                event_key=row.event_key,
                event_kind=row.event_kind,
                occurred_at=row.occurred_at,
                recorded_at=row.recorded_at,
                payload=json.loads(row.payload),
                payload_sha256=row.payload_sha256,
            )
            for row in rows
        )

    async def get_head(
        self, session: AsyncSession, scope: AccountScope, request_id: str
    ) -> PaperJournalHead | None:
        """Return the current exact checkpoint without claiming it."""

        scope = _scope(scope)
        row = await session.scalar(
            select(PaperExecutionHead).where(
                *_predicates(PaperExecutionHead, scope),
                PaperExecutionHead.request_id == request_id,
            )
        )
        return None if row is None else _head(scope, row)

    async def load_scope_events(
        self, session: AsyncSession, scope: AccountScope
    ) -> tuple[StoredJournalEvent, ...]:
        """Load all scope events in stable persisted order for aggregate rebuilding."""

        scope = _scope(scope)
        rows = (
            await session.scalars(
                select(PaperExecutionEvent)
                .where(*_predicates(PaperExecutionEvent, scope))
                .order_by(PaperExecutionEvent.scope_sequence)
            )
        ).all()
        return tuple(
            StoredJournalEvent(
                row.sequence,
                row.scope_sequence,
                row.event_key,
                row.event_kind,
                row.occurred_at,
                row.recorded_at,
                json.loads(row.payload),
                row.payload_sha256,
            )
            for row in rows
        )


__all__ = [
    "JournalEvent",
    "PaperAppendReceipt",
    "PaperIntentReceipt",
    "PaperJournalClaim",
    "PaperJournalConflict",
    "PaperJournalHead",
    "PaperJournalRepository",
    "StoredJournalEvent",
]
