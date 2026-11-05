"""Complete-row admission and append-only boundaries for D13b payload copies."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from api_audit_payload_schema_support import (
    NOW,
    account_owner,
    account_payload,
    account_root,
    public_payload,
    public_root,
    public_source,
    retained_account_event,
    retained_public_event,
)
from execution_test_support import schema_db as schema_db
from sqlalchemy import create_engine, delete, event, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only
from test_api_audit_parent_validation import (
    _clear_audit,
    _context,
    _pre_dispatch,
    _root,
    _venue_parent_graph,
)
from test_coordination_schema import save

from hmt_crypto.persistence.api_audit_codec import encode_api_audit_summary
from hmt_crypto.persistence.api_audit_contracts import (
    ApiAuditRecordV1,
    AuditCallKind,
    AuditEventKind,
    AuditPayloadDescriptor,
    AuditRetentionMode,
    AuditStorageAllocation,
    AuditSummaryKind,
    AuditTarget,
    PayloadSlot,
    VenueOutcomeKind,
    VenueResponseSummaryV1,
)
from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.api_audit_payload_contracts import AuditPayloadSourceV1
from hmt_crypto.persistence.api_audit_payload_models import ApiAuditPayload
from hmt_crypto.persistence.api_audit_purge_contract import (
    decode_audit_payload_purge_result,
)
from hmt_crypto.persistence.db import APP_SCHEMA
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.models import metadata

pytestmark = pytest.mark.integration


@pytest.fixture
def engine() -> Iterator[Engine]:
    """Create one isolated SQLite schema with enforced descriptor relations."""
    database = create_engine("sqlite://")

    @event.listens_for(database, "connect")
    def enable_foreign_keys(connection: object, record: object) -> None:
        del record
        connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    translated = database.execution_options(schema_translate_map={APP_SCHEMA: None})
    metadata.create_all(translated)
    try:
        yield translated
    finally:
        database.dispose()


def _insert_public_parents(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(public_root())
        session.commit()
        session.add(retained_public_event())
        session.commit()


def _insert_account_parents(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(account_owner())
        session.commit()
        session.add(account_root())
        session.commit()
        session.add(retained_account_event())
        session.commit()


def _values(row: ApiAuditPayload) -> dict[str, object]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _corrupted_payload(row: ApiAuditPayload) -> str:
    corrupted = row.payload_text.replace('"GET"', '"PUT"', 1)
    assert corrupted != row.payload_text
    assert len(corrupted.encode("ascii")) == row.payload_byte_length
    return corrupted


def _venue_response_summary() -> VenueResponseSummaryV1:
    return VenueResponseSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.VENUE_RESPONSE,
        outcome_kind=VenueOutcomeKind.SUCCESS,
        http_status=200,
        completed_response_sha256="f" * 64,
        completed_response_byte_length=3,
        duration_ns=7,
    )


def _venue_entered(
    root: ApiAuditEvent,
    pre_dispatch: ApiAuditEvent,
    owner: object,
    command: object,
    order: object,
    prepared: object,
    dispatch_event: object,
    consumption: object,
) -> ApiAuditEvent:
    summary = _venue_response_summary()
    audit_recorded_at = dispatch_event.recorded_at + timedelta(seconds=3)  # type: ignore[attr-defined]
    payload = encode_api_audit_summary(summary)
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.VENUE_ENTERED,
        context=_context(owner),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.DISPATCH,
        logical_call_id=prepared.id,  # type: ignore[attr-defined]
        correlation_id=prepared.correlation_id,  # type: ignore[attr-defined]
        policy_version="delta-docs-2026-11-04-private-v1",
        root_event_id=root.id,
        route_family="PRIVATE",
        route_id="place_order",
        method="POST",
        path_template="/v2/orders",
        weight=5,
        command_id=command.id,  # type: ignore[attr-defined]
        operation=command.operation_kind,  # type: ignore[attr-defined]
        order_id=order.id,  # type: ignore[attr-defined]
        order_attempt_id=prepared.id,  # type: ignore[attr-defined]
        order_attempt_number=prepared.attempt_number,  # type: ignore[attr-defined]
        attempt_backend=prepared.backend,  # type: ignore[attr-defined]
        command_evidence_sha256=command.evidence_sha256,  # type: ignore[attr-defined]
        transport_attempt_number=1,
        dispatch_event_id=dispatch_event.id,  # type: ignore[attr-defined]
        consumption_id=consumption.id,  # type: ignore[attr-defined]
        dispatch_recorded_at=dispatch_event.recorded_at,  # type: ignore[attr-defined]
        semantic_request_sha256="d" * 64,
        wire_request_evidence_sha256="e" * 64,
        pre_dispatch_audit_receipt_id=pre_dispatch.id,
        entry_possible=True,
        reconciliation_required=False,
        summary=summary,
    )
    descriptor = AuditPayloadDescriptor(
        retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
        payload_slot=PayloadSlot.RESPONSE,
        payload_schema_version=1,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_byte_length=len(payload),
        payload_expires_at=audit_recorded_at + timedelta(seconds=60),
        payload_ttl_seconds=60,
        retention_policy_version=9,
    )
    return ApiAuditEvent.from_record(
        record,
        AuditStorageAllocation(
            id=uuid4(),
            sequence=3,
            audit_recorded_at=audit_recorded_at,
            descriptor=descriptor,
            closure_source=record,
        ),
    )


def _venue_entered_source(event_row: ApiAuditEvent, owner: object) -> AuditPayloadSourceV1:
    assert event_row.summary_payload is not None
    assert event_row.summary_sha256 is not None
    return AuditPayloadSourceV1(
        owning_event_id=event_row.id,
        context=_context(owner),
        event_kind=AuditEventKind.VENUE_ENTERED,
        summary_kind=AuditSummaryKind.VENUE_RESPONSE,
        summary_payload=event_row.summary_payload,
        summary_sha256=event_row.summary_sha256,
        audit_recorded_at=event_row.audit_recorded_at,
        descriptor=AuditPayloadDescriptor(
            retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
            payload_slot=PayloadSlot.RESPONSE,
            payload_schema_version=event_row.payload_schema_version,
            payload_sha256=event_row.payload_sha256,
            payload_byte_length=event_row.payload_byte_length,
            payload_expires_at=event_row.payload_expires_at,
            payload_ttl_seconds=event_row.payload_ttl_seconds,
            retention_policy_version=event_row.retention_policy_version,
        ),
    )


@pytest_asyncio.fixture
async def venue_entered_graph(schema_db):
    database = schema_db
    owner, command, order, prepared, dispatch_event, consumption = await _venue_parent_graph(
        database
    )
    root = _root(owner, command, order, prepared)
    await save(database, root)
    pre_dispatch = _pre_dispatch(
        root,
        owner,
        command,
        order,
        prepared,
        dispatch_event,
        consumption,
        dispatch_recorded_at=dispatch_event.recorded_at,
    )
    await save(database, pre_dispatch)
    entered = _venue_entered(
        root,
        pre_dispatch,
        owner,
        command,
        order,
        prepared,
        dispatch_event,
        consumption,
    )
    await save(database, entered)
    try:
        yield database, entered, _venue_entered_source(entered, owner)
    finally:
        async with database.engine.begin() as connection:
            await connection.execute(delete(ApiAuditPayload))
            await connection.execute(
                delete(ApiAuditEvent).where(ApiAuditEvent.event_kind == "VENUE_ENTERED")
            )
            await connection.execute(
                delete(ApiAuditEvent).where(ApiAuditEvent.event_kind == "VENUE_PRE_DISPATCH")
            )
        await _clear_audit(database)


@pytest.mark.asyncio
async def test_venue_entered_response_copy_inserts_from_true_parent_fact(
    venue_entered_graph,
) -> None:
    database, entered, source = venue_entered_graph
    row = ApiAuditPayload.from_source(source)
    await save(database, row)
    async with database.transaction() as session:
        stored = await session.get(ApiAuditPayload, (entered.id, "RESPONSE"))
        assert stored is not None
        assert stored.payload_text == row.payload_text
        await session.refresh(stored)
        assert stored.payload_text == row.payload_text
    async with database.transaction() as session:
        partially_loaded = (
            await session.scalars(
                select(ApiAuditPayload).options(
                    load_only(
                        ApiAuditPayload.owning_event_id,
                        ApiAuditPayload.payload_slot,
                    )
                )
            )
        ).one()
        assert _values(partially_loaded) == _values(row)


@pytest.mark.asyncio
async def test_venue_entered_response_rejects_corrupted_parent_and_copy(
    venue_entered_graph,
) -> None:
    database, entered, source = venue_entered_graph
    row = ApiAuditPayload.from_source(source)
    await save(database, row)
    corrupted_copy = row.payload_text.replace('"SUCCESS"', '"FAILURE"')
    assert len(corrupted_copy) == len(row.payload_text)
    with pytest.raises(PersistenceValueError):
        async with database.transaction() as session:
            stored = await session.get(ApiAuditPayload, (entered.id, "RESPONSE"))
            assert stored is not None
            await session.execute(
                update(ApiAuditPayload.__table__)
                .where(ApiAuditPayload.owning_event_id == entered.id)
                .values(payload_text=corrupted_copy)
                .execution_options(synchronize_session=False)
            )
            await session.refresh(stored)

    assert entered.summary_payload is not None
    corrupted_parent = entered.summary_payload.replace('"SUCCESS"', '"FAILURE"')
    assert len(corrupted_parent) == len(entered.summary_payload)
    with pytest.raises(PersistenceValueError):
        async with database.transaction() as session:
            stored = await session.get(ApiAuditPayload, (entered.id, "RESPONSE"))
            assert stored is not None
            await session.execute(
                update(ApiAuditEvent.__table__)
                .where(ApiAuditEvent.id == entered.id)
                .values(summary_payload=corrupted_parent)
                .execution_options(synchronize_session=False)
            )
            await session.refresh(stored)


def test_valid_insert_and_partial_load_reconstruct_the_exact_parent_copy(
    engine: Engine,
) -> None:
    _insert_public_parents(engine)
    with Session(engine) as session:
        session.add(public_payload())
        session.commit()

    with Session(engine) as session:
        loaded = session.scalars(
            select(ApiAuditPayload).options(
                load_only(ApiAuditPayload.owning_event_id, ApiAuditPayload.payload_slot)
            )
        ).one()
        assert _values(loaded) == _values(public_payload())


def test_insert_rejects_same_length_payload_corruption_and_missing_parent(
    engine: Engine,
) -> None:
    _insert_public_parents(engine)
    corrupted = public_payload()
    corrupted.payload_text = _corrupted_payload(corrupted)
    with Session(engine) as session:
        session.add(corrupted)
        with pytest.raises(PersistenceValueError):
            session.flush()

    orphan = public_payload()
    orphan.owning_event_id = UUID(int=999)
    with Session(engine) as session:
        session.add(orphan)
        with pytest.raises(PersistenceValueError):
            session.flush()


@pytest.mark.parametrize(
    ("column", "replacement"),
    (
        ("owning_event_id", UUID(int=999)),
        ("context_kind", "ACCOUNT"),
        ("region", "global"),
        ("environment", "prod"),
        ("account_id", UUID(int=2)),
        ("mode", "LIVE"),
        ("payload_slot", "RESPONSE"),
        ("payload_schema_version", 2),
        ("payload_sha256", "b" * 64),
        ("payload_byte_length", 261),
        ("payload_expires_at", NOW + timedelta(seconds=120)),
        ("payload_ttl_seconds", 61),
        ("retention_policy_version", 4),
        ("copy_key", "b" * 64),
    ),
)
def test_insert_rejects_every_non_payload_stored_field_corruption(
    engine: Engine, column: str, replacement: object
) -> None:
    _insert_public_parents(engine)
    row = public_payload()
    setattr(row, column, replacement)
    with Session(engine) as session:
        session.add(row)
        with pytest.raises(PersistenceValueError):
            session.flush()


def test_insert_rejects_attached_source_that_disagrees_with_the_parent(
    engine: Engine,
) -> None:
    _insert_public_parents(engine)
    row = public_payload()
    row.__dict__["_api_audit_payload_source"] = public_source().model_copy(
        update={"owning_event_id": UUID(int=999)}
    )
    with Session(engine) as session:
        session.add(row)
        with pytest.raises(PersistenceValueError):
            session.flush()


def test_load_and_refresh_reject_same_length_raw_storage_corruption(
    engine: Engine,
) -> None:
    _insert_public_parents(engine)
    row = public_payload()
    values = _values(row)
    values["payload_text"] = _corrupted_payload(row)
    with engine.begin() as connection:
        connection.execute(insert(ApiAuditPayload).values(**values))
    with Session(engine) as session:
        with pytest.raises(PersistenceValueError):
            session.get(ApiAuditPayload, (row.owning_event_id, row.payload_slot))

    with engine.begin() as connection:
        connection.execute(ApiAuditPayload.__table__.delete())
    with Session(engine) as session:
        stored = public_payload()
        session.add(stored)
        session.commit()
        session.connection().execute(
            ApiAuditPayload.__table__.update().values(payload_text=_corrupted_payload(stored))
        )
        session.commit()
        with pytest.raises(PersistenceValueError):
            session.refresh(stored)


def test_orm_update_and_delete_are_unconditionally_denied(engine: Engine) -> None:
    _insert_public_parents(engine)
    with Session(engine) as session:
        stored = public_payload()
        session.add(stored)
        session.commit()
        stored.copy_key = "b" * 64
        with pytest.raises(PersistenceValueError):
            session.flush()
        session.rollback()

    with Session(engine) as session:
        stored = session.get(
            ApiAuditPayload,
            (public_source().owning_event_id, "REQUEST"),
        )
        assert stored is not None
        session.delete(stored)
        with pytest.raises(PersistenceValueError):
            session.flush()


def test_duplicate_copy_is_rejected_and_historical_expiry_is_not_resampled(
    engine: Engine,
) -> None:
    _insert_public_parents(engine)
    first = public_payload()
    assert first.payload_expires_at < datetime(2026, 11, 6, tzinfo=UTC)
    with Session(engine) as session:
        session.add(first)
        session.commit()
    with Session(engine) as session:
        session.add(public_payload())
        with pytest.raises(IntegrityError):
            session.flush()


def test_account_copy_uses_active_account_descriptor_and_rejects_wrong_account(
    engine: Engine,
) -> None:
    _insert_account_parents(engine)
    with Session(engine) as session:
        session.add(account_payload())
        session.commit()
        stored = session.get(
            ApiAuditPayload,
            (retained_account_event().id, "REQUEST"),
        )
        assert stored is not None
        assert stored.context_kind == "ACCOUNT"
        assert stored.account_id == account_owner().id
        assert stored.mode == "LIVE"

    other_id = UUID(int=2)
    other = account_owner(other_id)
    with Session(engine) as session:
        session.add(other)
        session.commit()
    values = _values(account_payload())
    values["account_id"] = other_id
    values["copy_key"] = "b" * 64
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(insert(ApiAuditPayload).values(**values))


def test_core_delete_returns_no_purge_receipt_and_preserves_permanent_metadata(
    engine: Engine,
) -> None:
    _insert_public_parents(engine)
    with Session(engine) as session:
        session.add(public_payload())
        session.commit()
    with engine.begin() as connection:
        result = connection.execute(delete(ApiAuditPayload))
        assert result.rowcount == 1
        assert (
            connection.scalar(
                select(ApiAuditEvent.id).where(ApiAuditEvent.id == public_source().owning_event_id)
            )
            == public_source().owning_event_id
        )
    for candidate in (result, {"rowcount": 1}):
        with pytest.raises(PersistenceValueError):
            decode_audit_payload_purge_result(candidate)


def test_validation_errors_are_fixed_and_do_not_retain_payload_input(
    engine: Engine,
) -> None:
    _insert_public_parents(engine)
    secret = "D13B_SECRET_CANARY"
    row = public_payload()
    row.payload_text = secret + "X" * (row.payload_byte_length - len(secret))
    with Session(engine) as session:
        session.add(row)
        with pytest.raises(PersistenceValueError) as captured:
            session.flush()
    error = captured.value
    assert str(error) == "persistence_value_error: Database value is invalid"
    assert secret not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
