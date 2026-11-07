"""D9c lifecycle-freeze and complete stored-result path regressions."""

import sqlite3
from datetime import timedelta
from uuid import UUID

import pytest
from risk_rate_consumption_schema_support import committed_consumption_graph
from risk_reservation_schema_support import apply_test_only_non_authoritative_release
from schema_test_support import schema_db as schema_db
from sqlalchemy import select, text
from sqlalchemy.orm import load_only

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_reservation_models import RELEASE_FIELDS

ALTERNATE_ATTEMPT_PAYLOAD = (
    '{"order_type":"market","product_id":1101,"reduce_only":false,'
    '"requested_contracts":3,"side":"buy"}'
)
ALTERNATE_EVENT_PAYLOAD = '{"outcome":"ACCEPTED"}'
PARENT_CORRUPTION_CASES = (
    "attempt-payload",
    "event-payload",
    "event-observed-at",
    "event-ingester-bundle",
    "event-reconciliation-ref",
    "event-leader-bundle",
    "event-recovery-bundle",
)
RESULT_PATHS = ("normal", "deferred", "expired", "full_refresh", "partial_refresh")


def _parent_corruption(case, prepared, dispatch_event):
    if case == "attempt-payload":
        return "order_attempts", prepared.id, {"evidence_payload": ALTERNATE_ATTEMPT_PAYLOAD}
    if case == "event-payload":
        return (
            "order_attempt_events",
            dispatch_event.id,
            {"evidence_payload": ALTERNATE_EVENT_PAYLOAD},
        )
    if case == "event-observed-at":
        return (
            "order_attempt_events",
            dispatch_event.id,
            {"observed_at": dispatch_event.recorded_at.isoformat(timespec="microseconds")},
        )
    if case == "event-ingester-bundle":
        return (
            "order_attempt_events",
            dispatch_event.id,
            {
                "ingester_id": UUID("81000000-0000-4000-8000-000000000001").hex,
                "ingester_generation": 1,
            },
        )
    if case == "event-reconciliation-ref":
        return (
            "order_attempt_events",
            dispatch_event.id,
            {"reconciliation_ref": UUID("82000000-0000-4000-8000-000000000002").hex},
        )
    if case == "event-leader-bundle":
        return (
            "order_attempt_events",
            dispatch_event.id,
            {
                "ingester_leader_ref": UUID("83000000-0000-4000-8000-000000000003").hex,
                "ingester_leader_generation": 1,
            },
        )
    assert case == "event-recovery-bundle"
    return (
        "order_attempt_events",
        dispatch_event.id,
        {
            "ingester_recovery_ref": UUID("84000000-0000-4000-8000-000000000004").hex,
            "ingester_recovery_generation": 1,
        },
    )


def _hostile_parent_update(db, table_name, identifier, changes):
    database_path = db.engine.url.database
    assert database_path is not None
    assignments = ", ".join(f"{field} = ?" for field in changes)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            f"UPDATE {table_name} SET {assignments} WHERE id = ?",
            (*changes.values(), identifier.hex),
        )


async def _session_parent_update(session, table_name, identifier, changes):
    assignments = ", ".join(f"{field} = :{field}" for field in changes)
    await session.execute(text("PRAGMA ignore_check_constraints=ON"))
    await session.execute(
        text(f"UPDATE {table_name} SET {assignments} WHERE id = :target_id"),
        {**changes, "target_id": identifier.hex},
    )
    await session.execute(text("PRAGMA ignore_check_constraints=OFF"))


async def _load_consumption_path(session, model, identifier, path):
    statement = select(model).where(model.id == identifier)
    if path == "deferred":
        statement = statement.options(load_only(model.id))
    loaded = await session.scalar(statement)
    assert loaded is not None
    if path == "expired":
        session.expire(loaded)
    elif path == "full_refresh":
        await session.refresh(loaded)
    elif path == "partial_refresh":
        await session.refresh(loaded, attribute_names=["recorded_at"])
    return loaded


@pytest.mark.asyncio
async def test_committed_reservation_freezes_shape_valid_timestamp_change(schema_db):
    graph, _, event, _ = await committed_consumption_graph(schema_db)
    reservation = graph[10]
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            stored = await session.get(type(reservation), reservation.id)
            assert stored is not None
            stored.updated_at = event.recorded_at + timedelta(microseconds=1)
            assert stored.created_at <= stored.updated_at < stored.rate_window_end_at
            await session.flush()


@pytest.mark.asyncio
async def test_committed_reservation_freezes_fully_shaped_release_proposal(schema_db):
    graph, _, event, _ = await committed_consumption_graph(schema_db)
    decision, reservation = graph[9], graph[10]
    command, order = graph[6], graph[7]
    release_candidate = type(reservation).from_decision(
        decision=decision,
        created_at=reservation.created_at,
        id=reservation.id,
    )
    proof = apply_test_only_non_authoritative_release(
        release_candidate,
        decision,
        command,
        order,
        [],
        [],
        proof_observed_at=event.recorded_at + timedelta(seconds=1),
    )
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            stored = await session.get(type(reservation), reservation.id)
            assert stored is not None
            for field in (
                "remaining_contracts",
                "remaining_incremental_gross_exposure",
                "remaining_worst_case_loss",
                "remaining_collateral",
                "remaining_open_order_slots",
                "pending_rate_units",
                "status",
                "version",
                "updated_at",
                *RELEASE_FIELDS,
            ):
                setattr(stored, field, getattr(release_candidate, field))
            assert stored.status == "RELEASED"
            assert stored.updated_at == proof.proof_observed_at
            assert stored.pending_rate_units == 0
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["expired", "full_refresh", "partial_refresh"])
async def test_consumption_update_is_rejected_after_non_normal_result_paths(schema_db, path):
    _, _, _, consumption = await committed_consumption_graph(schema_db)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = await _load_consumption_path(session, type(consumption), consumption.id, path)
            loaded.rate_policy_version = 2
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["deferred", "expired", "full_refresh", "partial_refresh"])
async def test_consumption_delete_is_rejected_after_non_normal_result_paths(schema_db, path):
    _, _, _, consumption = await committed_consumption_graph(schema_db)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = await _load_consumption_path(session, type(consumption), consumption.id, path)
            await session.delete(loaded)
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", RESULT_PATHS)
@pytest.mark.parametrize("corruption", PARENT_CORRUPTION_CASES)
async def test_parent_evidence_corruption_is_rejected_on_every_result_path(
    schema_db, corruption, path
):
    _, prepared, dispatch_event, consumption = await committed_consumption_graph(schema_db)
    table_name, parent_id, changes = _parent_corruption(corruption, prepared, dispatch_event)
    model = type(consumption)
    if path in {"normal", "deferred"}:
        _hostile_parent_update(schema_db, table_name, parent_id, changes)
        with pytest.raises(PersistenceValueError):
            async with schema_db.transaction() as session:
                statement = select(model).where(model.id == consumption.id)
                if path == "deferred":
                    statement = statement.options(load_only(model.id))
                await session.scalar(statement)
        return

    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = await session.get(model, consumption.id)
            assert loaded is not None
            await _session_parent_update(session, table_name, parent_id, changes)
            if path == "expired":
                session.expire(loaded)
                await session.refresh(loaded)
            elif path == "full_refresh":
                await session.refresh(loaded)
            else:
                await session.refresh(loaded, attribute_names=["recorded_at"])
