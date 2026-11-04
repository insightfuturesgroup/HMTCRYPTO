"""Stored consumption parents must retain the complete immutable D9a plan."""

import pytest
from risk_rate_consumption_schema_support import committed_consumption_graph
from schema_test_support import schema_db as schema_db
from sqlalchemy import select
from sqlalchemy.orm import load_only

from hmt_crypto.persistence.errors import PersistenceValueError

RESULT_PATHS = ("normal", "deferred", "expired", "full_refresh", "partial_refresh")
OTHER_UUID = "91000000000040008000000000000001"
# These literal cases cover every D9a -> reservation immutable field. Paired
# changes preserve remaining == planned, so lifecycle checks cannot mask the gap.
PLAN_CORRUPTIONS = (
    ("decision_id", OTHER_UUID, None),
    ("account_id", OTHER_UUID, None),
    ("region", "global", None),
    ("environment", "prod", None),
    ("mode", "BACKTEST", None),
    ("risk_state_id", OTHER_UUID, None),
    ("command_id", OTHER_UUID, None),
    ("command_operation_kind", "MODIFY_ORDER", None),
    ("order_id", OTHER_UUID, None),
    ("product_id", 1102, None),
    ("decision_outcome", "REJECTED", None),
    ("risk_effect", "REDUCE_ONLY", None),
    ("reservation_plan_sha256", "e" * 64, None),
    ("decision_evaluated_at", "2026-11-04T00:00:09.000000+00:00", None),
    ("decision_recorded_at", "2026-11-04T00:00:11.000001+00:00", None),
    ("approved_contracts", 3, "remaining_contracts"),
    ("approved_side", "sell", None),
    ("reference_asset_id", 3, None),
    ("reference_asset_symbol", "USD", None),
    ("collateral_asset_id", 3, None),
    ("collateral_asset_symbol", "USD", None),
    ("planned_incremental_gross_exposure", "101", "remaining_incremental_gross_exposure"),
    ("planned_worst_case_loss", "11", "remaining_worst_case_loss"),
    ("planned_collateral", "11", "remaining_collateral"),
    ("planned_open_order_slots", 2, "remaining_open_order_slots"),
    ("planned_pending_rate_units", 2, None),
    ("rate_policy_version", 2, None),
    ("rate_policy_sha256", "e" * 64, None),
    ("rate_window_start_at", "2026-11-03T23:59:59.000000+00:00", None),
    ("rate_window_end_at", "2026-11-04T00:03:00.000000+00:00", None),
)


async def _corrupt_only_reservation(session, identifier, changes):
    connection = await session.connection()
    untouched_tables = (
        "risk_decisions",
        "order_attempts",
        "order_attempt_events",
        "risk_rate_consumptions",
    )
    before = {
        table: (await connection.exec_driver_sql(f"SELECT * FROM {table}")).mappings().all()
        for table in untouched_tables
    }
    reservation_before = (
        (
            await connection.exec_driver_sql(
                "SELECT * FROM risk_reservations WHERE id = ?", (identifier.hex,)
            )
        )
        .mappings()
        .one()
    )
    # Deliberately corrupt only this disposable transaction; rollback restores
    # the valid graph, including candidate-FK cases whose checks are deferred.
    await connection.exec_driver_sql("PRAGMA defer_foreign_keys=ON")
    await connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
    assignments = ", ".join(f"{field} = ?" for field in changes)
    await connection.exec_driver_sql(
        f"UPDATE risk_reservations SET {assignments} WHERE id = ?",
        (*changes.values(), identifier.hex),
    )
    await connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    reservation_after = (
        (
            await connection.exec_driver_sql(
                "SELECT * FROM risk_reservations WHERE id = ?", (identifier.hex,)
            )
        )
        .mappings()
        .one()
    )
    assert {
        field
        for field in reservation_before
        if reservation_before[field] != reservation_after[field]
    } == set(changes)
    for table in untouched_tables:
        assert (
            await connection.exec_driver_sql(f"SELECT * FROM {table}")
        ).mappings().all() == before[table]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", RESULT_PATHS)
@pytest.mark.parametrize(
    "field,replacement,remaining", PLAN_CORRUPTIONS, ids=[case[0] for case in PLAN_CORRUPTIONS]
)
async def test_committed_parent_plan_corruption_is_rejected_on_every_result_path(
    schema_db, field, replacement, remaining, path
):
    graph, _, _, consumption = await committed_consumption_graph(schema_db)
    reservation = graph[10]
    changes = {field: replacement}
    if remaining is not None:
        changes[remaining] = replacement
    model = type(consumption)
    async with schema_db.transaction() as session:
        # Establish that the graph is valid before corruption, without loading
        # the reservation into this session or its identity map.
        loaded = await session.get(model, consumption.id)
        assert loaded is not None
        if path in {"normal", "deferred"}:
            session.expunge(loaded)
        await _corrupt_only_reservation(session, reservation.id, changes)

        with pytest.raises(PersistenceValueError):
            if path in {"normal", "deferred"}:
                statement = select(model).where(model.id == consumption.id)
                if path == "deferred":
                    statement = statement.options(load_only(model.id))
                await session.scalar(statement)
            elif path == "expired":
                session.expire(loaded)
                await session.run_sync(lambda _: loaded.recorded_at)
            elif path == "full_refresh":
                await session.refresh(loaded)
            else:
                await session.refresh(loaded, attribute_names=["recorded_at"])

        # The consumption's narrow Core lookup must reject the same immutable
        # disagreement that a direct reservation ORM load already rejects.
        with pytest.raises(PersistenceValueError):
            await session.get(type(reservation), reservation.id)
        await session.rollback()
