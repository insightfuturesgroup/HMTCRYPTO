"""D9c three-way immutable intent and protected-order acceptance matrix."""

import sqlite3
from uuid import UUID

import pytest
from risk_rate_consumption_schema_support import (
    committed_consumption_graph,
    committed_modify_consumption_graph,
    divergent_attempt_consumption_graph,
)
from schema_test_support import schema_db as schema_db

from hmt_crypto.persistence.errors import PersistenceValueError


def _hostile_update(db, statement, parameters):
    database_path = db.engine.url.database
    assert database_path is not None
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(statement, parameters)


@pytest.mark.asyncio
async def test_valid_modify_graph_round_trips_through_orm_parent_validation(schema_db):
    graph = await committed_modify_consumption_graph(schema_db)
    target_order, reservation, prepared, event, consumption = (
        graph[0][7],
        graph[3],
        graph[4],
        graph[5],
        graph[6],
    )

    async with schema_db.transaction() as session:
        loaded = await session.get(type(consumption), consumption.id)
        stored_reservation = await session.get(type(reservation), reservation.id)

    assert loaded is not None and stored_reservation is not None
    assert loaded.command_operation_kind == "MODIFY_ORDER"
    assert loaded.order_id == target_order.id
    assert loaded.product_id == 1101
    assert loaded.attempt_evidence_payload == prepared.evidence_payload
    assert loaded.recorded_at == event.recorded_at == stored_reservation.updated_at


@pytest.mark.asyncio
async def test_canonical_attempt_and_consumption_divergence_from_agreed_d9a_is_rejected(
    schema_db,
):
    graph, prepared, _, consumption = await divergent_attempt_consumption_graph(schema_db)
    command, order, decision = graph[6], graph[7], graph[9]
    assert (
        decision.command_evidence_schema_version,
        decision.command_evidence_payload,
        decision.command_evidence_sha256,
    ) == (
        command.evidence_schema_version,
        command.evidence_payload,
        command.evidence_sha256,
    )
    assert (
        decision.order_id,
        decision.product_id,
        decision.order_origin_command_id,
        decision.order_requested_contracts,
        decision.order_requested_side,
        decision.order_requested_reduce_only,
    ) == (
        order.id,
        order.product_id,
        order.command_id,
        order.requested_contracts,
        order.requested_side,
        order.requested_reduce_only,
    )
    assert (
        consumption.attempt_evidence_schema_version,
        consumption.attempt_evidence_payload,
        consumption.attempt_evidence_sha256,
    ) == (
        prepared.evidence_schema_version,
        prepared.evidence_payload,
        prepared.evidence_sha256,
    )
    assert prepared.evidence_payload != decision.command_evidence_payload

    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            session.add(consumption)
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,replacement",
    [
        ("id", UUID("51000000-0000-4000-8000-000000000005").hex),
        ("product_id", 1102),
        ("command_id", UUID("41000000-0000-4000-8000-000000000004").hex),
        ("requested_contracts", 3),
        ("requested_side", "sell"),
        ("requested_reduce_only", 1),
    ],
)
async def test_each_d9a_copy_and_current_protected_order_mismatch_is_rejected(
    schema_db, field, replacement
):
    graph, _, _, consumption = await committed_consumption_graph(schema_db)
    order = graph[7]
    _hostile_update(
        schema_db,
        f"UPDATE orders SET {field} = ? WHERE id = ?",
        (replacement, order.id.hex),
    )

    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(consumption), consumption.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,replacement",
    [
        ("requested_order_type", "limit"),
        ("requested_limit_price", "11"),
        ("requested_stop_price", "11"),
    ],
)
async def test_each_current_target_order_type_or_price_shape_deviation_is_rejected(
    schema_db, field, replacement
):
    graph, _, _, consumption = await committed_consumption_graph(schema_db)
    order = graph[7]
    _hostile_update(
        schema_db,
        f"UPDATE orders SET {field} = ? WHERE id = ?",
        (replacement, order.id.hex),
    )

    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(consumption), consumption.id)
