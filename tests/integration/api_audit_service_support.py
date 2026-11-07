"""Exact inert records and sessions for the D13c transaction slice."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.persistence.api_audit_contracts import (
    ApiAuditRecordV1,
    AuditCallKind,
    AuditContextKind,
    AuditEventKind,
    AuditRetentionMode,
    AuditStorageAllocation,
    AuditSummaryKind,
    AuditTarget,
    LocalRejectionReason,
    LocalRejectionSummaryV1,
    PublicAuditContext,
    VenueOutcomeKind,
    VenueRequestSummaryV1,
)
from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.api_audit_payload_contracts import AuditRetentionPolicyV1
from hmt_crypto.persistence.api_audit_payload_models import ApiAuditPayload
from hmt_crypto.persistence.api_audit_repository import ApiAuditRepository
from hmt_crypto.persistence.api_audit_service import ApiAuditTransactionService
from hmt_crypto.persistence.api_audit_service_contracts import (
    ApiAuditAppendReceipt,
    ApiAuditAppendRequest,
)

OBSERVED_AT = datetime(2026, 1, 1, tzinfo=UTC)
CALL_ID = UUID(int=700)


def public_context() -> PublicAuditContext:
    """Return one exact public/testnet audit context."""
    return PublicAuditContext(
        context_kind=AuditContextKind.PUBLIC,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )


def root_record(*, call_id: UUID = CALL_ID) -> ApiAuditRecordV1:
    """Return the canonical root for one products read call."""
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=public_context(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=call_id,
        correlation_id=call_id,
        policy_version="delta-docs-2026-11-03",
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
    )


def request_summary(*, evidence: str = "a" * 64) -> VenueRequestSummaryV1:
    """Return the canonical bounded products-request summary."""
    return VenueRequestSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.VENUE_REQUEST,
        route_id="products",
        method="GET",
        path_template="/v2/products",
        route_policy="delta-docs-2026-11-03",
        weight=3,
        request_evidence_sha256=evidence,
    )


def attempt_record(
    root_event_id: UUID,
    *,
    call_id: UUID = CALL_ID,
    evidence: str = "a" * 64,
    observed_at: datetime = OBSERVED_AT,
) -> ApiAuditRecordV1:
    """Return an exact read-attempt start tied to the supplied root."""
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.ATTEMPT_STARTED,
        context=public_context(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=call_id,
        correlation_id=call_id,
        policy_version="delta-docs-2026-11-03",
        root_event_id=root_event_id,
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
        transport_attempt_number=1,
        occurred_at=observed_at,
        summary=request_summary(evidence=evidence),
    )


def _local_terminal_record(root_event_id: UUID) -> ApiAuditRecordV1:
    """Build inert D13a terminal evidence; never a D13c append request."""
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.LOCAL_REJECTED,
        context=public_context(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=CALL_ID,
        correlation_id=CALL_ID,
        policy_version="delta-docs-2026-11-03",
        root_event_id=root_event_id,
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
        summary=LocalRejectionSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.LOCAL_REJECTION,
            reason=LocalRejectionReason.INVALID_REQUEST,
            route_id="products",
        ),
    )


def _inert_finish_record(root_event_id: UUID, *, paired: bool) -> ApiAuditRecordV1:
    """Build pre-existing D13a stream state without granting service write authority."""
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_FINISHED,
        context=public_context(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=CALL_ID,
        correlation_id=CALL_ID,
        policy_version="delta-docs-2026-11-03",
        root_event_id=root_event_id,
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
        outcome_kind=(VenueOutcomeKind.LOCAL_REJECTED if paired else VenueOutcomeKind.SUCCESS),
    )


async def seed_stream_end(
    sessions: async_sessionmaker[AsyncSession],
    root: ApiAuditAppendReceipt,
    state: Literal["terminal_only", "paired", "standalone_finish"],
) -> None:
    """Preseed exact inert D13a end-state rows outside the D13c service."""
    terminal = _local_terminal_record(root.root_event_id)
    recorded_at = root.audit_recorded_at
    async with sessions() as session, session.begin():
        if state in ("terminal_only", "paired"):
            terminal_row = ApiAuditEvent.from_record(
                terminal,
                AuditStorageAllocation(
                    id=uuid4(),
                    sequence=2,
                    audit_recorded_at=recorded_at,
                    descriptor=None,
                    closure_source=terminal,
                ),
            )
            session.add(terminal_row)
            await session.flush()
        if state in ("paired", "standalone_finish"):
            finish = _inert_finish_record(root.root_event_id, paired=state == "paired")
            finish_row = ApiAuditEvent.from_record(
                finish,
                AuditStorageAllocation(
                    id=uuid4(),
                    sequence=3 if state == "paired" else 2,
                    audit_recorded_at=recorded_at,
                    descriptor=None,
                    closure_source=terminal if state == "paired" else None,
                ),
            )
            session.add(finish_row)
            await session.flush()


def summary_only(record: ApiAuditRecordV1) -> ApiAuditAppendRequest:
    """Bind one record to the safe default retention policy."""
    return ApiAuditAppendRequest(
        record=record,
        retention=AuditRetentionPolicyV1(mode=AuditRetentionMode.SUMMARY_ONLY),
    )


def retained(
    record: ApiAuditRecordV1,
    *,
    ttl_seconds: int = 60,
    policy_version: int = 3,
) -> ApiAuditAppendRequest:
    """Bind one eligible record to an explicit retained-copy policy."""
    return ApiAuditAppendRequest(
        record=record,
        retention=AuditRetentionPolicyV1(
            mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
            ttl_seconds=ttl_seconds,
            retention_policy_version=policy_version,
        ),
    )


@pytest_asyncio.fixture
async def audit_sessions(schema_db) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Return caller-owned sessions over the disposable file-backed database."""
    sessions = async_sessionmaker(schema_db.engine, expire_on_commit=False, autoflush=False)
    try:
        yield sessions
    finally:
        async with schema_db.engine.begin() as connection:
            await connection.execute(delete(ApiAuditPayload))
            await connection.execute(
                delete(ApiAuditEvent).where(ApiAuditEvent.event_kind != "CALL_STARTED")
            )
            await connection.execute(
                delete(ApiAuditEvent).where(ApiAuditEvent.event_kind == "CALL_STARTED")
            )


@pytest.fixture
def audit_service() -> ApiAuditTransactionService:
    """Construct the narrow service with its invariant-specific repository."""
    return ApiAuditTransactionService(repository=ApiAuditRepository())


@asynccontextmanager
async def clean_session(
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield one exact caller-owned, clean, inactive session."""
    async with sessions() as session:
        yield session


async def row_counts(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    """Return committed metadata and payload row counts from a fresh session."""
    async with sessions() as session:
        events = await session.scalar(select(func.count()).select_from(ApiAuditEvent))
        payloads = await session.scalar(select(func.count()).select_from(ApiAuditPayload))
    assert type(events) is int and type(payloads) is int
    return events, payloads


async def append_root(
    service: ApiAuditTransactionService,
    sessions: async_sessionmaker[AsyncSession],
):
    """Append the canonical root and return its public committed receipt."""
    async with clean_session(sessions) as session:
        return await service.append(session=session, request=summary_only(root_record()))
