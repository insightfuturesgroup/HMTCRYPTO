"""Read-only semantic parity against immutable dispatch and consumption parents."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from execution_test_support import attempt_event, venue_attempt
from execution_test_support import schema_db as schema_db
from risk_decision_schema_support import decision, decision_graph
from risk_reservation_schema_support import reservation_from_decision
from schema_test_support import NOW, schema_api
from sqlalchemy import delete, update
from test_coordination_schema import save

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.persistence.api_audit_contracts import (
    AccountAuditContext,
    ApiAuditRecordV1,
    AuditCallKind,
    AuditContextKind,
    AuditEventKind,
    AuditStorageAllocation,
    AuditSummaryKind,
    AuditTarget,
    TransferRejectionReason,
    TransferRejectionSummaryV1,
    VenueRequestSummaryV1,
)
from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_rate_consumption_evidence import derive_dispatch_event_key

pytestmark = pytest.mark.integration
HASH = "a" * 64


async def _venue_parent_graph(database):
    graph = await decision_graph(database, mode="LIVE")
    owner, recovery, command, order, leadership = (
        graph[0],
        graph[1],
        graph[6],
        graph[7],
        graph[8],
    )
    assert leadership is not None
    approved = decision(
        *[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)],
        observed_command_leader_ref=leadership.id,
        observed_command_leader_generation=leadership.generation,
        observed_command_recovery_ref=recovery.id,
        observed_command_recovery_generation=leadership.generation,
        leadership_lease_id=leadership.id,
        recovery_incarnation_id=recovery.id,
        observed_leadership_owner_id=graph[5].id,
        observed_leadership_generation=leadership.generation,
        observed_leadership_expires_at=leadership.expires_at,
        observed_leadership_version=leadership.version,
    )
    await save(database, approved)
    reservation = reservation_from_decision(approved)
    await save(database, reservation)
    prepared = venue_attempt(
        owner,
        command,
        order,
        correlation_id=command.correlation_id,
        prepared_at=NOW + timedelta(seconds=13),
        recorded_at=NOW + timedelta(seconds=14),
        original_claim_owner=command.claim_owner,
        original_claim_generation=command.claim_generation,
        original_claim_expires_at=command.claim_expires_at,
        original_leader_ref=leadership.id,
        original_leader_generation=leadership.generation,
        original_recovery_ref=recovery.id,
        original_recovery_generation=leadership.generation,
    )
    await save(database, prepared)
    event = attempt_event(
        owner,
        prepared,
        event_key=derive_dispatch_event_key(prepared.id),
        recorded_at=NOW + timedelta(seconds=15),
    )
    await save(database, event)
    consumption = schema_api().RiskRateConsumption.from_parents(
        reservation=reservation,
        attempt=prepared,
        dispatch_event=event,
    )
    await save(database, consumption)
    return owner, command, order, prepared, event, consumption


def _context(owner) -> AccountAuditContext:
    return AccountAuditContext(
        context_kind=AuditContextKind.ACCOUNT,
        account_id=owner.id,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=ExecutionMode.LIVE,
    )


def _root(owner, command, order, prepared) -> ApiAuditEvent:
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=_context(owner),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.DISPATCH,
        logical_call_id=prepared.id,
        correlation_id=prepared.correlation_id,
        policy_version="delta-docs-2026-11-04-private-v1",
        route_family="PRIVATE",
        route_id="place_order",
        method="POST",
        path_template="/v2/orders",
        weight=5,
        command_id=command.id,
        operation=command.operation_kind,
        order_id=order.id,
        order_attempt_id=prepared.id,
        order_attempt_number=prepared.attempt_number,
        attempt_backend=prepared.backend,
        command_evidence_sha256=command.evidence_sha256,
    )
    return ApiAuditEvent.from_record(
        record,
        AuditStorageAllocation(
            id=uuid4(),
            sequence=1,
            audit_recorded_at=prepared.recorded_at + timedelta(seconds=2),
            descriptor=None,
            closure_source=None,
        ),
    )


def _pre_dispatch(
    root: ApiAuditEvent,
    owner,
    command,
    order,
    prepared,
    event,
    consumption,
    *,
    dispatch_recorded_at,
    identifier=None,
) -> ApiAuditEvent:
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.VENUE_PRE_DISPATCH,
        context=_context(owner),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.DISPATCH,
        logical_call_id=prepared.id,
        correlation_id=prepared.correlation_id,
        policy_version="delta-docs-2026-11-04-private-v1",
        root_event_id=root.id,
        route_family="PRIVATE",
        route_id="place_order",
        method="POST",
        path_template="/v2/orders",
        weight=5,
        command_id=command.id,
        operation=command.operation_kind,
        order_id=order.id,
        order_attempt_id=prepared.id,
        order_attempt_number=prepared.attempt_number,
        attempt_backend=prepared.backend,
        command_evidence_sha256=command.evidence_sha256,
        transport_attempt_number=1,
        dispatch_event_id=event.id,
        consumption_id=consumption.id,
        dispatch_recorded_at=dispatch_recorded_at,
        semantic_request_sha256="d" * 64,
        wire_request_evidence_sha256="e" * 64,
        summary=VenueRequestSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.VENUE_REQUEST,
            route_id="place_order",
            method="POST",
            path_template="/v2/orders",
            route_policy="delta-docs-2026-11-04-private-v1",
            weight=5,
            request_evidence_sha256="e" * 64,
        ),
    )
    return ApiAuditEvent.from_record(
        record,
        AuditStorageAllocation(
            id=identifier or uuid4(),
            sequence=2,
            audit_recorded_at=event.recorded_at + timedelta(seconds=2),
            descriptor=None,
            closure_source=None,
        ),
    )


async def _clear_audit(database) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(
            delete(ApiAuditEvent).where(ApiAuditEvent.event_kind != "CALL_STARTED")
        )
        await connection.execute(delete(ApiAuditEvent))


@pytest_asyncio.fixture
async def audit_db(schema_db):
    try:
        yield schema_db
    finally:
        await _clear_audit(schema_db)


@pytest.mark.asyncio
async def test_parent_time_parity_is_enforced_before_insert(audit_db) -> None:
    owner, command, order, prepared, event, consumption = await _venue_parent_graph(audit_db)
    root = _root(owner, command, order, prepared)
    await save(audit_db, root)
    wrong = _pre_dispatch(
        root,
        owner,
        command,
        order,
        prepared,
        event,
        consumption,
        dispatch_recorded_at=event.recorded_at + timedelta(seconds=1),
    )
    with pytest.raises(PersistenceValueError):
        await save(audit_db, wrong)
    valid = _pre_dispatch(
        root,
        owner,
        command,
        order,
        prepared,
        event,
        consumption,
        dispatch_recorded_at=event.recorded_at,
    )
    await save(audit_db, valid)


@pytest.mark.asyncio
async def test_parent_time_parity_is_enforced_on_load(audit_db) -> None:
    owner, command, order, prepared, event, consumption = await _venue_parent_graph(audit_db)
    root = _root(owner, command, order, prepared)
    await save(audit_db, root)
    wrong = _pre_dispatch(
        root,
        owner,
        command,
        order,
        prepared,
        event,
        consumption,
        dispatch_recorded_at=event.recorded_at + timedelta(seconds=1),
    )
    await save(audit_db, wrong, channel="core")
    with pytest.raises(PersistenceValueError):
        async with audit_db.transaction() as session:
            await session.get(ApiAuditEvent, wrong.id)


@pytest.mark.asyncio
async def test_parent_time_parity_is_enforced_on_refresh(audit_db) -> None:
    owner, command, order, prepared, event, consumption = await _venue_parent_graph(audit_db)
    root = _root(owner, command, order, prepared)
    await save(audit_db, root)
    valid = _pre_dispatch(
        root,
        owner,
        command,
        order,
        prepared,
        event,
        consumption,
        dispatch_recorded_at=event.recorded_at,
    )
    await save(audit_db, valid)
    wrong = _pre_dispatch(
        root,
        owner,
        command,
        order,
        prepared,
        event,
        consumption,
        dispatch_recorded_at=event.recorded_at + timedelta(seconds=1),
        identifier=valid.id,
    )
    values = {column.name: getattr(wrong, column.name) for column in wrong.__table__.columns}
    with pytest.raises(PersistenceValueError):
        async with audit_db.transaction() as session:
            stored = await session.get(ApiAuditEvent, valid.id)
            assert stored is not None
            await session.execute(
                update(ApiAuditEvent.__table__)
                .where(ApiAuditEvent.id == valid.id)
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            await session.refresh(stored)


@pytest.mark.asyncio
async def test_already_entered_prior_parents_must_share_dispatch_and_backend(audit_db) -> None:
    owner, command, order, prepared, event, consumption = await _venue_parent_graph(audit_db)
    root = _root(owner, command, order, prepared)
    receiver_call_id = uuid4()
    receiver_record = ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=_context(owner),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.RECEIVER_REJECTION,
        logical_call_id=receiver_call_id,
        correlation_id=receiver_call_id,
        policy_version="delta-docs-2026-11-04-private-v1",
        route_family="PRIVATE",
        route_id="place_order",
        method="POST",
        path_template="/v2/orders",
        weight=5,
    )
    receiver_root = ApiAuditEvent.from_record(
        receiver_record,
        AuditStorageAllocation(
            id=uuid4(),
            sequence=1,
            audit_recorded_at=event.recorded_at + timedelta(seconds=2),
            descriptor=None,
            closure_source=None,
        ),
    )
    await save(audit_db, receiver_root)
    extra_event = attempt_event(
        owner,
        prepared,
        id=uuid4(),
        sequence=2,
        event_key="unlinked-prior-event",
        event_kind="UNCERTAIN",
        recorded_at=event.recorded_at + timedelta(seconds=1),
    )
    await save(audit_db, extra_event)

    def transfer(prior_event_id) -> ApiAuditEvent:
        record = ApiAuditRecordV1(
            event_kind=AuditEventKind.VENUE_TRANSFER_REJECTED,
            context=_context(owner),
            target=AuditTarget.VENUE,
            call_kind=AuditCallKind.RECEIVER_REJECTION,
            logical_call_id=receiver_record.logical_call_id,
            correlation_id=receiver_record.logical_call_id,
            policy_version="delta-docs-2026-11-04-private-v1",
            root_event_id=receiver_root.id,
            route_family="PRIVATE",
            route_id="place_order",
            method="POST",
            path_template="/v2/orders",
            weight=5,
            summary=TransferRejectionSummaryV1(
                schema_version=1,
                summary_kind=AuditSummaryKind.TRANSFER_REJECTION,
                reason=TransferRejectionReason.ALREADY_ENTERED,
                send_started_by_this_call=False,
                prior_dispatch_event_id=prior_event_id,
                prior_consumption_id=consumption.id,
                prior_bytes_sent_possible=True,
            ),
        )
        return ApiAuditEvent.from_record(
            record,
            AuditStorageAllocation(
                id=uuid4(),
                sequence=2,
                audit_recorded_at=event.recorded_at + timedelta(seconds=2),
                descriptor=None,
                closure_source=record,
            ),
        )

    with pytest.raises(PersistenceValueError):
        await save(audit_db, transfer(extra_event.id))
    await save(audit_db, transfer(event.id))
    del root
