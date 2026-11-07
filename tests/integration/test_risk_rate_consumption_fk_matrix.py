"""D9c composite-FK mismatch matrix at the SQLite Core boundary."""

from datetime import timedelta
from uuid import UUID

import pytest
from risk_rate_consumption_schema_support import consumption_parent_graph
from schema_test_support import schema_api
from schema_test_support import schema_db as schema_db
from sqlalchemy import insert, text

from hmt_crypto.persistence.errors import PersistenceError


def _row_values(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.c}


async def _core_insert_ignoring_checks(db, table, values):
    try:
        async with db.transaction() as session:
            await session.execute(text("PRAGMA ignore_check_constraints=ON"))
            await session.execute(insert(table).values(**values))
    finally:
        async with db.engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,replacement",
    [
        ("reservation_id", UUID("91000000-0000-4000-8000-000000000009")),
        ("account_id", UUID("21000000-0000-4000-8000-000000000002")),
        ("region", "global"),
        ("environment", "prod"),
        ("mode", "BACKTEST"),
        ("decision_id", UUID("11000000-0000-4000-8000-000000000001")),
        ("risk_state_id", UUID("31000000-0000-4000-8000-000000000003")),
        ("command_id", UUID("41000000-0000-4000-8000-000000000004")),
        ("command_operation_kind", "MODIFY_ORDER"),
        ("order_id", UUID("51000000-0000-4000-8000-000000000005")),
        ("product_id", 1102),
        ("decision_outcome", "DENIED"),
        ("risk_effect", "REDUCE_ONLY"),
        ("reservation_plan_sha256", "e" * 64),
        ("decision_evaluated_at", timedelta(microseconds=1)),
        ("decision_recorded_at", timedelta(microseconds=1)),
        ("reservation_created_at", timedelta(microseconds=1)),
        ("rate_policy_version", 2),
        ("rate_policy_sha256", "e" * 64),
        ("rate_window_start_at", -timedelta(microseconds=1)),
        ("rate_window_end_at", timedelta(microseconds=1)),
        ("consumed_units", 2),
        ("order_attempt_id", UUID("a1000000-0000-4000-8000-00000000000a")),
        ("attempt_number", 2),
        ("attempt_backend", "VENUE"),
        ("attempt_prepared_at", timedelta(microseconds=1)),
        ("attempt_recorded_at", timedelta(microseconds=1)),
        ("attempt_evidence_schema_version", 2),
        ("attempt_evidence_sha256", "e" * 64),
        ("dispatch_event_id", UUID("b1000000-0000-4000-8000-00000000000b")),
        ("dispatch_event_sequence", 2),
        ("dispatch_event_key", "dispatch-started:v1:a1000000-0000-4000-8000-00000000000a"),
        ("dispatch_event_kind", "RESPONSE_ACCEPTED"),
        ("dispatch_event_evidence_schema_version", 2),
        ("dispatch_event_evidence_sha256", "e" * 64),
    ],
)
async def test_every_composite_fk_identity_scope_and_semantic_column_rejects_isolated_mismatch(
    schema_db, field, replacement
):
    graph, prepared, event = await consumption_parent_graph(schema_db)
    reservation = graph[10]
    row = schema_api().RiskRateConsumption.from_parents(
        reservation=reservation,
        attempt=prepared,
        dispatch_event=event,
    )
    values = _row_values(row)
    if isinstance(replacement, timedelta):
        values[field] = values[field] + replacement
    else:
        values[field] = replacement

    with pytest.raises(PersistenceError):
        await _core_insert_ignoring_checks(schema_db, row.__table__, values)


@pytest.mark.asyncio
async def test_event_and_consumption_recorded_times_must_match_through_composite_fk(schema_db):
    graph, prepared, event = await consumption_parent_graph(schema_db)
    row = schema_api().RiskRateConsumption.from_parents(
        reservation=graph[10],
        attempt=prepared,
        dispatch_event=event,
    )
    values = _row_values(row)
    values["recorded_at"] = event.recorded_at + timedelta(microseconds=1)

    with pytest.raises(PersistenceError):
        await _core_insert_ignoring_checks(schema_db, row.__table__, values)
