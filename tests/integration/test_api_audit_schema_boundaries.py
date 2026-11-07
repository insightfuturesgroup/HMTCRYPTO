"""Complete-row factory, corruption, load, and ORM immutability boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer, load_only

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.persistence.api_audit_codec import (
    _decode_api_audit_evidence,
    _record_from_producer,
    encode_api_audit_summary,
    hash_api_audit_payload_copy,
)
from hmt_crypto.persistence.api_audit_contracts import (
    ApiAuditRecordV1,
    AuditCallKind,
    AuditContextKind,
    AuditEventKind,
    AuditPayloadDescriptor,
    AuditRetentionMode,
    AuditStorageAllocation,
    AuditSummaryKind,
    AuditTarget,
    PayloadSlot,
    PublicAuditContext,
    VenueRequestSummaryV1,
)
from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.api_audit_validation import (
    _maintenance_expected,
    validate_api_audit_row,
)
from hmt_crypto.persistence.db import APP_SCHEMA
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.models import metadata

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 5, 0, 0, 1, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "api_audit"


def _record() -> ApiAuditRecordV1:
    call_id = UUID(int=6)
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=PublicAuditContext(
            context_kind=AuditContextKind.PUBLIC,
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
        ),
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


def _row() -> ApiAuditEvent:
    return ApiAuditEvent.from_record(
        _record(),
        AuditStorageAllocation(
            id=UUID(int=8),
            sequence=1,
            audit_recorded_at=NOW,
            descriptor=None,
            closure_source=None,
        ),
    )


def _engine():
    engine = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {APP_SCHEMA: None}},
    )

    @event.listens_for(engine, "connect")
    def enable_fk(connection, record) -> None:  # type: ignore[no-untyped-def]
        del record
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _fixture_parts(name: str) -> tuple[dict[str, object], bytes]:
    fixtures = {
        row["fixture"]: row
        for row in (
            json.loads(line)
            for line in (FIXTURES / "v1_event_vectors.jsonl").read_text().splitlines()
        )
    }
    fixture = fixtures[name]
    payload = json.dumps(
        {
            "domain": "HMT_API_AUDIT_EVENT_V1",
            "producer": fixture["producer"],
            "storage": fixture["storage"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    record, allocation, raw = _decode_api_audit_evidence(payload, fixture["expected_event_sha256"])
    if name in ("MAINTENANCE_ROOT", "PAYLOAD_PURGED"):
        columns = tuple(column.name for column in ApiAuditEvent.__table__.columns)
        target = SimpleNamespace(
            evidence_payload=payload.decode("ascii"),
            evidence_sha256=fixture["expected_event_sha256"],
        )
        return _maintenance_expected(target, record, allocation, raw, columns), payload
    if name == "CALL_FINISHED_CLOSURE":
        terminal = _record_from_producer(fixtures["VENUE_ENTERED"]["producer"])
        allocation = allocation.model_copy(update={"closure_source": terminal})
    row = ApiAuditEvent.from_record(record, allocation)
    values = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    return values, payload


def _fixture_row(name: str) -> ApiAuditEvent:
    values, _payload = _fixture_parts(name)
    return ApiAuditEvent(**values)


def _raw_insert_rejected(name: str, **changes: object) -> None:
    engine = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {APP_SCHEMA: None}},
    )
    metadata.create_all(engine)
    values, _payload = _fixture_parts(name)
    values.update(changes)
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(ApiAuditEvent.__table__.insert().values(**values))
    metadata.drop_all(engine)
    engine.dispose()


def test_factory_owns_generated_hashes_and_complete_root_shape() -> None:
    row = _row()
    assert row.root_event_id == row.id == UUID(int=8)
    assert row.root_event_kind == "CALL_STARTED"
    assert row.event_key == ("afde671dc33ab2bf4442d78f7ade42000697dc0a8ac4e2d33e23a4b32a47e852")
    assert row.evidence_sha256 == (
        "954668fec1a02f37cc56b24978ecd197de14c5ae4d620668c03a1105e8a1cd84"
    )
    validate_api_audit_row(row)
    with pytest.raises(PersistenceValueError):
        ApiAuditEvent.from_record(
            _record(),
            AuditStorageAllocation(
                id=UUID(int=8),
                sequence=2,
                audit_recorded_at=NOW,
                descriptor=None,
                closure_source=None,
            ),
        )


def test_factory_and_validator_accept_legitimate_non_root_attempt_started() -> None:
    call_id = UUID(int=6)
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.ATTEMPT_STARTED,
        context=PublicAuditContext(
            context_kind=AuditContextKind.PUBLIC,
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
        ),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=call_id,
        correlation_id=call_id,
        policy_version="delta-docs-2026-11-03",
        root_event_id=UUID(int=8),
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
        transport_attempt_number=1,
        occurred_at=datetime(2026, 11, 5, tzinfo=UTC),
        summary=VenueRequestSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.VENUE_REQUEST,
            route_id="products",
            method="GET",
            path_template="/v2/products",
            route_policy="delta-docs-2026-11-03",
            weight=3,
            request_evidence_sha256="a" * 64,
        ),
    )
    row = ApiAuditEvent.from_record(
        record,
        AuditStorageAllocation(
            id=UUID(int=102),
            sequence=2,
            audit_recorded_at=NOW,
            descriptor=None,
            closure_source=None,
        ),
    )
    validate_api_audit_row(row)


def test_factory_binds_fixed_descriptor_copy_and_exact_closure_source() -> None:
    rows = {
        row["fixture"]: row
        for row in (
            json.loads(line)
            for line in (FIXTURES / "v1_event_vectors.jsonl").read_text().splitlines()
        )
    }
    attempt = _record_from_producer(rows["ATTEMPT_STARTED"]["producer"])
    assert attempt.summary is not None
    summary_bytes = encode_api_audit_summary(attempt.summary)
    descriptor = AuditPayloadDescriptor(
        retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
        payload_slot=PayloadSlot.REQUEST,
        payload_schema_version=1,
        payload_sha256=hash_api_audit_payload_copy(attempt.summary),
        payload_byte_length=len(summary_bytes),
        payload_expires_at=datetime(2026, 11, 5, 0, 1, 1, tzinfo=UTC),
        payload_ttl_seconds=60,
        retention_policy_version=1,
    )
    allocation = AuditStorageAllocation(
        id=UUID(int=102),
        sequence=2,
        audit_recorded_at=NOW,
        descriptor=descriptor,
        closure_source=None,
    )
    validate_api_audit_row(ApiAuditEvent.from_record(attempt, allocation))
    for change in (
        {"payload_slot": PayloadSlot.RESPONSE},
        {"payload_sha256": "0" * 64},
        {"payload_byte_length": len(summary_bytes) + 1},
    ):
        wrong = AuditPayloadDescriptor.model_validate(descriptor.model_copy(update=change))
        with pytest.raises(PersistenceValueError):
            ApiAuditEvent.from_record(
                attempt,
                allocation.model_copy(update={"descriptor": wrong}),
            )

    terminal = _record_from_producer(rows["VENUE_ENTERED"]["producer"])
    finish = _record_from_producer(rows["CALL_FINISHED_CLOSURE"]["producer"])
    with pytest.raises(PersistenceValueError):
        ApiAuditEvent.from_record(
            terminal,
            AuditStorageAllocation(
                id=UUID(int=103),
                sequence=3,
                audit_recorded_at=NOW,
                descriptor=None,
                closure_source=None,
            ),
        )
    paired = AuditStorageAllocation(
        id=UUID(int=104),
        sequence=4,
        audit_recorded_at=NOW,
        descriptor=None,
        closure_source=terminal,
    )
    validate_api_audit_row(ApiAuditEvent.from_record(finish, paired))
    for container, field, value in (
        ("identity", "root_event_id", str(UUID(int=999))),
        ("context", 1, str(UUID(int=999))),
    ):
        producer = json.loads(json.dumps(rows["VENUE_ENTERED"]["producer"]))
        producer[container][field] = value
        wrong_source = _record_from_producer(producer)
        with pytest.raises(PersistenceValueError):
            ApiAuditEvent.from_record(
                finish,
                paired.model_copy(update={"closure_source": wrong_source}),
            )
    wrong_finish_producer = json.loads(json.dumps(rows["CALL_FINISHED_CLOSURE"]["producer"]))
    wrong_finish_producer["facts"]["outcome_kind"] = "AMBIGUOUS"
    wrong_finish = _record_from_producer(wrong_finish_producer)
    with pytest.raises(PersistenceValueError):
        ApiAuditEvent.from_record(wrong_finish, paired)
    with pytest.raises(PersistenceValueError):
        ApiAuditEvent.from_record(_record(), paired)


def test_transfer_rejection_finish_derives_local_rejected_outcome() -> None:
    fixtures = {
        row["fixture"]: row
        for row in (
            json.loads(line)
            for line in (FIXTURES / "v1_event_vectors.jsonl").read_text().splitlines()
        )
    }
    transfer = _record_from_producer(fixtures["VENUE_TRANSFER_REJECTED"]["producer"])
    finish_producer = json.loads(json.dumps(fixtures["VENUE_TRANSFER_REJECTED"]["producer"]))
    finish_producer["event_kind"] = "CALL_FINISHED"
    finish_producer["facts"] = {"outcome_kind": "LOCAL_REJECTED"}
    finish_producer.pop("summary")
    finish = _record_from_producer(finish_producer)
    allocation = AuditStorageAllocation(
        id=UUID(int=400),
        sequence=3,
        audit_recorded_at=NOW,
        descriptor=None,
        closure_source=transfer,
    )
    validate_api_audit_row(ApiAuditEvent.from_record(finish, allocation))


def test_complete_row_validation_rejects_hash_and_context_corruption() -> None:
    row = _row()
    row.evidence_sha256 = "0" * 64
    with pytest.raises(PersistenceValueError):
        validate_api_audit_row(row)
    row = _row()
    row.account_id = UUID(int=1)
    with pytest.raises(PersistenceValueError):
        validate_api_audit_row(row)
    row = _row()
    row.sequence = 2
    with pytest.raises(PersistenceValueError):
        validate_api_audit_row(row)


def test_sqlite_insert_load_update_and_delete_boundaries() -> None:
    engine = _engine()
    metadata.create_all(engine)
    with Session(engine) as session:
        session.add(_row())
        session.commit()
    with Session(engine) as session:
        loaded = session.get(ApiAuditEvent, UUID(int=8))
        assert loaded is not None
        loaded.sequence = 2
        with pytest.raises(PersistenceValueError):
            session.commit()
        session.rollback()
    with Session(engine) as session:
        loaded = session.get(ApiAuditEvent, UUID(int=8))
        assert loaded is not None
        session.delete(loaded)
        with pytest.raises(PersistenceValueError):
            session.commit()
        session.rollback()
    with Session(engine) as session:
        loaded = session.scalars(select(ApiAuditEvent).options(load_only(ApiAuditEvent.id))).one()
        assert loaded.evidence_sha256 == (
            "954668fec1a02f37cc56b24978ecd197de14c5ae4d620668c03a1105e8a1cd84"
        )
        assert not loaded.__dict__.get("_sa_instance_state").unloaded
    metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.parametrize(
    ("fixture", "changes"),
    (
        ("CALL_FINISHED_CLOSURE", {"closure_key": None, "closure_sha256": None}),
        ("CALL_STARTED", {"command_id": UUID(int=2)}),
        ("CALL_STARTED", {"simulator_backend_id": "not-simulator"}),
        (
            "CALL_STARTED",
            {
                "route_family": "PRIVATE",
                "route_id": "place_order",
                "method": "POST",
                "path_template": "/v2/orders",
                "policy_version": "delta-docs-2026-11-04-private-v1",
                "weight": 5,
            },
        ),
        (
            "CALL_STARTED_DISPATCH",
            {
                "route_family": "PUBLIC",
                "route_id": "products",
                "method": "GET",
                "path_template": "/v2/products",
                "policy_version": "delta-docs-2026-11-03",
                "weight": 3,
            },
        ),
        ("ATTEMPT_FINISHED", {"start_event_kind": "WRONG"}),
        ("VENUE_PRE_DISPATCH", {"dispatch_event_sequence": 2}),
        ("VENUE_PRE_DISPATCH", {"dispatch_event_kind": "WRONG"}),
        ("VENUE_PRE_DISPATCH", {"transport_attempt_number": 0}),
        (
            "VENUE_PRE_DISPATCH",
            {"dispatch_recorded_at": datetime(2026, 11, 5, 0, 0, 2, tzinfo=UTC)},
        ),
        ("VENUE_ENTERED", {"pre_dispatch_event_kind": "SIMULATOR_PRE_DISPATCH"}),
        ("VENUE_NO_ENTRY", {"entry_possible": True}),
        ("VENUE_NO_ENTRY", {"reconciliation_required": True}),
        ("VENUE_ENTERED", {"entry_possible": False}),
        ("SIMULATOR_OUTCOME", {"outcome_kind": "NOT_SUBMITTED"}),
        ("VENUE_ENTERED", {"http_status": 500}),
        (
            "VENUE_ENTERED",
            {"outcome_kind": "TRANSPORT_ERROR", "http_status": 503},
        ),
    ),
)
def test_sqlite_rejects_stage_terminal_and_response_structural_attacks(
    fixture: str, changes: dict[str, object]
) -> None:
    _raw_insert_rejected(fixture, **changes)


@pytest.mark.parametrize(
    ("fixture", "changes"),
    (
        ("LOCAL_REJECTED", {"last_phase": "ISSUED"}),
        ("VENUE_PRE_DISPATCH", {"simulator_request_evidence_sha256": "e" * 64}),
        ("VENUE_PRE_DISPATCH", {"pre_dispatch_audit_receipt_id": UUID(int=12)}),
        ("SIMULATOR_PRE_DISPATCH", {"transport_attempt_number": 1}),
        ("SIMULATOR_PRE_DISPATCH", {"wire_request_evidence_sha256": "a" * 64}),
        (
            "SIMULATOR_PRE_DISPATCH",
            {
                "pre_dispatch_audit_receipt_id": UUID(int=12),
                "pre_dispatch_event_kind": "SIMULATOR_PRE_DISPATCH",
            },
        ),
        ("SIMULATOR_OUTCOME", {"transport_attempt_number": 1}),
        ("SIMULATOR_OUTCOME", {"wire_request_evidence_sha256": "a" * 64}),
        ("SIMULATOR_OUTCOME", {"pre_dispatch_event_kind": "VENUE_PRE_DISPATCH"}),
    ),
)
def test_sqlite_rejects_every_cross_variant_transport_phase_and_receipt_field(
    fixture: str, changes: dict[str, object]
) -> None:
    _raw_insert_rejected(fixture, **changes)


@pytest.mark.parametrize(
    "changes",
    (
        {
            "route_family": "UNRESOLVED",
            "route_id": "UNRESOLVED",
            "method": None,
            "path_template": None,
            "weight": None,
        },
        {
            "route_family": "PRIVATE",
            "route_id": "balances",
            "method": "GET",
            "path_template": "/v2/wallet/balances",
            "weight": 3,
        },
    ),
)
def test_sqlite_transfer_rejection_requires_resolved_private_order_route(
    changes: dict[str, object],
) -> None:
    _raw_insert_rejected("VENUE_TRANSFER_REJECTED", **changes)


@pytest.mark.parametrize(
    ("fixture", "changes"),
    (
        ("CALL_STARTED", {"event_key": "0" * 64}),
        ("ATTEMPT_FINISHED", {"summary_payload": '{"schema_version":1}'}),
        ("ATTEMPT_FINISHED", {"duration_ns": 8}),
        ("VENUE_PRE_DISPATCH", {"payload_sha256": "0" * 64}),
        ("VENUE_ENTERED", {"closure_sha256": "0" * 64}),
        ("VENUE_ENTERED", {"correlation_id": UUID(int=99)}),
        ("MAINTENANCE_ROOT", {"actor_role_oid": 16_385}),
        ("PAYLOAD_PURGED", {"maintenance_root_id": UUID(int=999)}),
    ),
)
def test_every_reconstructed_field_family_is_checked_on_orm_load(
    fixture: str, changes: dict[str, object]
) -> None:
    engine = create_engine(
        "sqlite://",
        execution_options={"schema_translate_map": {APP_SCHEMA: None}},
    )
    metadata.create_all(engine)
    values, _payload = _fixture_parts(fixture)
    values.update(changes)
    with engine.begin() as connection:
        connection.execute(ApiAuditEvent.__table__.insert().values(**values))
    with Session(engine) as session, pytest.raises(PersistenceValueError):
        session.get(ApiAuditEvent, values["id"])
    metadata.drop_all(engine)
    engine.dispose()


def test_full_deferred_expired_and_explicit_refresh_paths_revalidate_complete_row() -> None:
    engine = _engine()
    metadata.create_all(engine)
    with Session(engine) as session:
        session.add(_row())
        session.commit()
    with Session(engine) as session:
        full = session.get(ApiAuditEvent, UUID(int=8))
        assert full is not None
        session.expire(full)
        assert full.event_key == _row().event_key
        assert not full.__dict__["_sa_instance_state"].unloaded
        session.refresh(full, ["id"])
        assert not full.__dict__["_sa_instance_state"].unloaded
    with Session(engine) as session:
        deferred = session.scalars(
            select(ApiAuditEvent).options(defer(ApiAuditEvent.evidence_payload))
        ).one()
        assert deferred.evidence_payload == _row().evidence_payload
        assert not deferred.__dict__["_sa_instance_state"].unloaded
    metadata.drop_all(engine)
    engine.dispose()
