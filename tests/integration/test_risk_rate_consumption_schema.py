"""Task 2.1d9c adds one immutable historical consumption table."""

import sqlite3
from datetime import timedelta

import pytest
from risk_rate_consumption_schema_support import (
    committed_consumption_graph,
    consumption_parent_graph,
    pending_consumption_graph,
)
from risk_reservation_schema_support import reservation_graph
from schema_test_support import schema_api
from schema_test_support import schema_db as schema_db
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, delete, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import load_only
from sqlalchemy.schema import CreateTable
from test_coordination_schema import save

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError
from hmt_crypto.persistence.execution_evidence import encode_evidence

SCOPE = ("account_id", "region", "environment", "mode")
RESERVATION_CHILD = (
    "reservation_id",
    *SCOPE,
    "decision_id",
    "risk_state_id",
    "command_id",
    "command_operation_kind",
    "order_id",
    "product_id",
    "decision_outcome",
    "risk_effect",
    "reservation_plan_sha256",
    "decision_evaluated_at",
    "decision_recorded_at",
    "reservation_created_at",
    "rate_policy_version",
    "rate_policy_sha256",
    "rate_window_start_at",
    "rate_window_end_at",
    "consumed_units",
)
ATTEMPT_CHILD = (
    "order_attempt_id",
    *SCOPE,
    "command_id",
    "command_operation_kind",
    "order_id",
    "attempt_number",
    "attempt_backend",
    "attempt_prepared_at",
    "attempt_recorded_at",
    "attempt_evidence_schema_version",
    "attempt_evidence_sha256",
)
EVENT_CHILD = (
    "dispatch_event_id",
    *SCOPE,
    "order_attempt_id",
    "attempt_backend",
    "dispatch_event_sequence",
    "dispatch_event_key",
    "dispatch_event_kind",
    "recorded_at",
    "dispatch_event_evidence_schema_version",
    "dispatch_event_evidence_sha256",
)


def _uniques(table):
    return {
        tuple(column.name for column in constraint.columns): constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_exact_table_columns_scoped_uniqueness_fks_and_indexes():
    api = schema_api()
    table = api.RiskRateConsumption.__table__
    assert table.name == "risk_rate_consumptions"
    assert tuple(table.c.keys()) == (
        "consumption_key",
        "reservation_id",
        "decision_id",
        "risk_state_id",
        "command_id",
        "command_operation_kind",
        "order_id",
        "product_id",
        "decision_outcome",
        "risk_effect",
        "reservation_plan_sha256",
        "decision_evaluated_at",
        "decision_recorded_at",
        "reservation_created_at",
        "order_attempt_id",
        "attempt_number",
        "attempt_backend",
        "attempt_prepared_at",
        "attempt_recorded_at",
        "attempt_evidence_schema_version",
        "attempt_evidence_payload",
        "attempt_evidence_sha256",
        "dispatch_event_id",
        "dispatch_event_sequence",
        "dispatch_event_key",
        "dispatch_event_kind",
        "dispatch_event_evidence_schema_version",
        "dispatch_event_evidence_payload",
        "dispatch_event_evidence_sha256",
        "rate_policy_version",
        "rate_policy_sha256",
        "rate_window_start_at",
        "rate_window_end_at",
        "consumed_units",
        "recorded_at",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
        "id",
        *SCOPE,
    )
    assert all(not column.nullable for column in table.c)
    assert _uniques(table) == {
        ("id", *SCOPE): "uq_risk_rate_consumptions_id_scope",
        (*SCOPE, "consumption_key"): "uq_risk_rate_consumptions_scope_consumption_key",
        (*SCOPE, "reservation_id"): "uq_risk_rate_consumptions_scope_reservation",
        (*SCOPE, "order_attempt_id"): "uq_risk_rate_consumptions_scope_attempt",
        (*SCOPE, "dispatch_event_id"): "uq_risk_rate_consumptions_scope_dispatch_event",
        (
            "id",
            *SCOPE,
            "command_id",
            "command_operation_kind",
            "order_id",
            "order_attempt_id",
            "attempt_backend",
            "dispatch_event_id",
        ): "uq_risk_rate_consumptions_api_audit_parent",
    }
    assert {
        constraint.name: tuple(constraint.column_keys)
        for constraint in table.foreign_key_constraints
        if isinstance(constraint, ForeignKeyConstraint)
    } == {
        "fk_risk_rate_consumptions_account_scope": SCOPE,
        "fk_risk_rate_consumptions_reservation": RESERVATION_CHILD,
        "fk_risk_rate_consumptions_attempt": ATTEMPT_CHILD,
        "fk_risk_rate_consumptions_dispatch_event": EVENT_CHILD,
    }
    expected_parents = {
        "accounts": ("id", "region", "environment", "mode"),
        "risk_reservations": (
            "id",
            *SCOPE,
            "decision_id",
            "risk_state_id",
            "command_id",
            "command_operation_kind",
            "order_id",
            "product_id",
            "decision_outcome",
            "risk_effect",
            "reservation_plan_sha256",
            "decision_evaluated_at",
            "decision_recorded_at",
            "created_at",
            "rate_policy_version",
            "rate_policy_sha256",
            "rate_window_start_at",
            "rate_window_end_at",
            "planned_pending_rate_units",
        ),
        "order_attempts": (
            "id",
            *SCOPE,
            "command_id",
            "operation_kind",
            "order_id",
            "attempt_number",
            "backend",
            "prepared_at",
            "recorded_at",
            "evidence_schema_version",
            "evidence_sha256",
        ),
        "order_attempt_events": (
            "id",
            *SCOPE,
            "attempt_id",
            "backend",
            "sequence",
            "event_key",
            "event_kind",
            "recorded_at",
            "evidence_schema_version",
            "evidence_sha256",
        ),
    }
    assert {
        fk.referred_table.name: tuple(element.column.name for element in fk.elements)
        for fk in table.foreign_key_constraints
    } == expected_parents
    direct_parents = {"risk_decisions", "risk_state", "command_operations", "orders"}
    assert not (direct_parents & set(expected_parents))
    assert {
        index.name: tuple(column.name for column in index.columns) for index in table.indexes
    } == {
        "ix_risk_rate_consumptions_scope": SCOPE,
        "ix_risk_rate_consumptions_reservation_parent": RESERVATION_CHILD,
        "ix_risk_rate_consumptions_attempt_parent": ATTEMPT_CHILD,
        "ix_risk_rate_consumptions_dispatch_event_parent": EVENT_CHILD,
        "ix_risk_rate_consumptions_rate_window": (
            *SCOPE,
            "rate_policy_version",
            "rate_policy_sha256",
            "rate_window_start_at",
            "rate_window_end_at",
        ),
    }


def test_exact_attempt_event_parent_candidates_and_committed_status_branch_exist():
    api = schema_api()
    assert (
        _uniques(api.OrderAttempt.__table__)[
            (
                "id",
                *SCOPE,
                "command_id",
                "operation_kind",
                "order_id",
                "attempt_number",
                "backend",
                "prepared_at",
                "recorded_at",
                "evidence_schema_version",
                "evidence_sha256",
            )
        ]
        == "uq_order_attempts_rate_consumption_parent"
    )
    assert (
        _uniques(api.OrderAttemptEvent.__table__)[
            (
                "id",
                *SCOPE,
                "attempt_id",
                "backend",
                "sequence",
                "event_key",
                "event_kind",
                "recorded_at",
                "evidence_schema_version",
                "evidence_sha256",
            )
        ]
        == "uq_order_attempt_events_rate_consumption_parent"
    )
    reservation_sql = str(
        CreateTable(api.RiskReservation.__table__).compile(dialect=postgresql.dialect())
    )
    assert "DISPATCH_COMMITTED" in reservation_sql


@pytest.mark.asyncio
async def test_pending_split_consumption_is_not_a_valid_stored_result(schema_db):
    *_, consumption = await pending_consumption_graph(schema_db)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(consumption), consumption.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["normal", "deferred"])
async def test_exact_committed_consumption_validates_complete_result_paths(schema_db, path):
    graph, _, event, consumption = await committed_consumption_graph(schema_db)
    reservation = graph[10]
    async with schema_db.transaction() as session:
        statement = select(type(consumption)).where(type(consumption).id == consumption.id)
        if path == "deferred":
            statement = statement.options(load_only(type(consumption).id))
        loaded = await session.scalar(statement)
        stored_reservation = await session.get(type(reservation), reservation.id)
        assert loaded is not None
        assert stored_reservation is not None
        if path == "deferred":
            assert loaded.id == consumption.id
        else:
            assert loaded.recorded_at == event.recorded_at
            assert loaded.recorded_at == stored_reservation.updated_at


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [timedelta(0), timedelta(microseconds=1)])
async def test_committed_reservation_rejects_window_end_equality_and_after(schema_db, offset):
    *_, reservation = await reservation_graph(schema_db)
    await save(schema_db, reservation)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            stored = await session.get(type(reservation), reservation.id)
            assert stored is not None
            stored.status = "DISPATCH_COMMITTED"
            stored.version = 2
            stored.pending_rate_units = 0
            stored.updated_at = stored.rate_window_end_at + offset
            await session.flush()


@pytest.mark.asyncio
async def test_committed_parent_time_must_equal_event_and_consumption(schema_db):
    graph, _, _, consumption = await committed_consumption_graph(schema_db)
    reservation = graph[10]
    async with schema_db.engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        await connection.exec_driver_sql(
            "UPDATE risk_reservations SET updated_at = ? WHERE id = ?",
            ((consumption.recorded_at + timedelta(microseconds=1)).isoformat(), reservation.id.hex),
        )
        await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(consumption), consumption.id)


@pytest.mark.asyncio
async def test_factory_rejects_cross_scope_or_lineage_parent_mix_and_owned_overrides(schema_db):
    first = await pending_consumption_graph(schema_db)
    second = await pending_consumption_graph(schema_db, profile_name="second", catalogue_revision=2)
    reservation = first[0][10]
    prepared, event = second[1], second[2]
    api = schema_api()
    with pytest.raises(PersistenceValueError):
        api.RiskRateConsumption.from_parents(
            reservation=reservation,
            attempt=prepared,
            dispatch_event=event,
        )
    with pytest.raises(PersistenceValueError):
        api.RiskRateConsumption.from_parents(
            reservation=reservation,
            attempt=first[1],
            dispatch_event=first[2],
            consumption_key="e" * 64,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["normal", "deferred"])
async def test_committed_consumption_is_orm_append_only_on_loaded_paths(schema_db, path):
    _, _, _, consumption = await committed_consumption_graph(schema_db)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            statement = select(type(consumption)).where(type(consumption).id == consumption.id)
            if path == "deferred":
                statement = statement.options(load_only(type(consumption).id))
            loaded = await session.scalar(statement)
            assert loaded is not None
            loaded.rate_policy_version = 2
            await session.flush()
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(consumption), consumption.id)
            assert loaded is not None
            await session.delete(loaded)
            await session.flush()


def _hostile_sqlite_update(schema_db, statement, parameters):
    database_path = schema_db.engine.url.database
    assert database_path is not None
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(statement, parameters)
        connection.commit()


@pytest.mark.asyncio
async def test_result_validation_rejects_individually_valid_current_command_divergence(schema_db):
    graph, _, _, consumption = await committed_consumption_graph(schema_db)
    command = graph[6]
    payload, digest = encode_evidence(
        "intent",
        "PLACE_ORDER",
        1,
        {
            "product_id": 1101,
            "requested_contracts": 3,
            "side": "buy",
            "order_type": "market",
            "reduce_only": False,
        },
    )
    _hostile_sqlite_update(
        schema_db,
        "UPDATE command_operations SET evidence_payload = ?, evidence_sha256 = ? WHERE id = ?",
        (payload, digest, command.id.hex),
    )
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(consumption), consumption.id)


@pytest.mark.asyncio
async def test_result_validation_rejects_individually_valid_current_order_divergence(schema_db):
    graph, _, _, consumption = await committed_consumption_graph(schema_db)
    order = graph[7]
    _hostile_sqlite_update(
        schema_db,
        "UPDATE orders SET requested_contracts = ? WHERE id = ?",
        (3, order.id.hex),
    )
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(consumption), consumption.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "PENDING_DISPATCH"),
        ("status", "RELEASED"),
        ("version", 1),
        ("pending_rate_units", 1),
        ("remaining_contracts", 1),
        ("remaining_incremental_gross_exposure", "99"),
        ("remaining_worst_case_loss", "9"),
        ("remaining_collateral", "9"),
        ("remaining_open_order_slots", 0),
        ("release_key", "e" * 64),
    ],
)
async def test_stored_consumption_rejects_every_committed_parent_shape_deviation(
    schema_db, field, value
):
    graph, _, _, consumption = await committed_consumption_graph(schema_db)
    reservation = graph[10]
    _hostile_sqlite_update(
        schema_db,
        f"UPDATE risk_reservations SET {field} = ? WHERE id = ?",
        (value, reservation.id.hex),
    )
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(consumption), consumption.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["normal", "deferred", "expired", "full_refresh", "partial_refresh"]
)
async def test_corrupt_consumption_digest_is_rejected_on_every_result_path(schema_db, path):
    _, _, _, consumption = await committed_consumption_graph(schema_db)
    if path in {"normal", "deferred"}:
        _hostile_sqlite_update(
            schema_db,
            "UPDATE risk_rate_consumptions SET evidence_sha256 = ? WHERE id = ?",
            ("e" * 64, consumption.id.hex),
        )
        with pytest.raises(PersistenceValueError):
            async with schema_db.transaction() as session:
                statement = select(type(consumption)).where(type(consumption).id == consumption.id)
                if path == "deferred":
                    statement = statement.options(load_only(type(consumption).id))
                await session.scalar(statement)
        return
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(consumption), consumption.id)
            assert loaded is not None
            await session.execute(
                text("UPDATE risk_rate_consumptions SET evidence_sha256 = :digest WHERE id = :id"),
                {"digest": "e" * 64, "id": consumption.id.hex},
            )
            if path == "expired":
                session.expire(loaded)
                await session.refresh(loaded)
            elif path == "full_refresh":
                await session.refresh(loaded)
            else:
                await session.refresh(loaded, attribute_names=["recorded_at"])


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", ["reservation", "attempt", "event"])
async def test_composite_parent_update_and_delete_are_restricted(schema_db, parent):
    graph, prepared, event, _ = await committed_consumption_graph(schema_db)
    reservation = graph[10]
    api = schema_api()
    cases = {
        "reservation": (reservation, api.RiskReservation.rate_policy_sha256, "e" * 64),
        "attempt": (prepared, api.OrderAttempt.evidence_sha256, "e" * 64),
        "event": (event, api.OrderAttemptEvent.event_key, "different-event-key"),
    }
    row, column, value = cases[parent]
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(
                update(type(row)).where(type(row).id == row.id).values({column: value})
            )
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(delete(type(row)).where(type(row).id == row.id))


@pytest.mark.asyncio
async def test_populated_consumption_table_can_drop_and_recreate(schema_db):
    _, _, _, consumption = await committed_consumption_graph(schema_db)
    table = type(consumption).__table__
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(table.drop)
        await connection.run_sync(table.create)
        names = {
            row[0]
            for row in (
                await connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ).all()
        }
    assert table.name in names


def test_consumption_model_has_no_dispatch_authority_surface():
    model = schema_api().RiskRateConsumption
    for forbidden in ("authorize", "can_dispatch", "permit", "ready", "submit"):
        assert not hasattr(model, forbidden)


@pytest.mark.asyncio
async def test_schema_only_insert_against_exact_committed_parent_does_not_mutate_parent(
    schema_db,
):
    graph, prepared, event = await consumption_parent_graph(schema_db)
    reservation = graph[10]
    async with schema_db.transaction() as session:
        stored = await session.get(type(reservation), reservation.id)
        assert stored is not None
        stored.status = "DISPATCH_COMMITTED"
        stored.version = 2
        stored.pending_rate_units = 0
        stored.updated_at = event.recorded_at
    before = {column.name: getattr(reservation, column.name) for column in reservation.__table__.c}
    before.update(
        status="DISPATCH_COMMITTED",
        version=2,
        pending_rate_units=0,
        updated_at=event.recorded_at,
    )
    consumption = schema_api().RiskRateConsumption.from_parents(
        reservation=reservation,
        attempt=prepared,
        dispatch_event=event,
    )
    await save(schema_db, consumption)
    async with schema_db.transaction() as session:
        stored = await session.get(type(reservation), reservation.id)
        loaded = await session.get(type(consumption), consumption.id)
        assert stored is not None and loaded is not None
        after = {column.name: getattr(stored, column.name) for column in stored.__table__.c}
        assert after == before


@pytest.mark.asyncio
async def test_rolled_back_consumption_insert_leaves_committed_parent_unchanged(schema_db):
    graph, prepared, event = await consumption_parent_graph(schema_db)
    reservation = graph[10]
    async with schema_db.transaction() as session:
        stored = await session.get(type(reservation), reservation.id)
        assert stored is not None
        stored.status = "DISPATCH_COMMITTED"
        stored.version = 2
        stored.pending_rate_units = 0
        stored.updated_at = event.recorded_at
    consumption = schema_api().RiskRateConsumption.from_parents(
        reservation=reservation,
        attempt=prepared,
        dispatch_event=event,
    )
    with pytest.raises(RuntimeError, match="test rollback"):
        async with schema_db.transaction() as session:
            session.add(consumption)
            await session.flush()
            raise RuntimeError("test rollback")
    async with schema_db.transaction() as session:
        stored = await session.get(type(reservation), reservation.id)
        assert stored is not None
        assert stored.status == "DISPATCH_COMMITTED"
        assert stored.pending_rate_units == 0
        assert await session.get(type(consumption), consumption.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("next_status", ["PENDING_DISPATCH", "RELEASED"])
async def test_committed_reservation_cannot_reopen_or_take_release_path(schema_db, next_status):
    graph, _, _, _ = await committed_consumption_graph(schema_db)
    reservation = graph[10]
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            stored = await session.get(type(reservation), reservation.id)
            assert stored is not None
            stored.status = next_status
            await session.flush()
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            stored = await session.get(type(reservation), reservation.id)
            assert stored is not None
            await session.delete(stored)
            await session.flush()
