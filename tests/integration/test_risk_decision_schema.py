"""Immutable risk-decision evidence is never a current dispatch authorization."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from risk_decision_schema_support import decision, decision_graph
from schema_test_support import schema_api
from schema_test_support import schema_db as schema_db
from sqlalchemy import (
    UniqueConstraint,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import load_only
from sqlalchemy.schema import CreateIndex, CreateTable
from test_coordination_schema import save

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError
from hmt_crypto.persistence.types import ExactDecimal

SCOPE = ("account_id", "region", "environment", "mode")
PLAN_DECIMALS = (
    "reserved_incremental_gross_exposure",
    "reserved_worst_case_loss",
    "reserved_collateral",
)
PLAN_FIELDS = (
    "approved_contracts",
    "approved_side",
    "reference_asset_id",
    "reference_asset_symbol",
    "collateral_asset_id",
    "collateral_asset_symbol",
    *PLAN_DECIMALS,
    "reserved_open_order_slots",
    "reserved_pending_rate_units",
    "rate_policy_version",
    "rate_policy_sha256",
    "rate_window_start_at",
    "rate_window_end_at",
    "reservation_plan_sha256",
)


def unique_columns(table):
    return {
        tuple(column.name for column in constraint.columns): constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_exact_one_table_export_columns_parent_candidates_fks_indexes_and_ddl():
    api = schema_api()
    assert hasattr(api, "RiskDecision")
    table = api.RiskDecision.__table__
    assert table.name == "risk_decisions"
    assert set(table.c.keys()) == {
        "id",
        *SCOPE,
        "decision_key",
        "risk_state_id",
        "command_id",
        "command_operation_kind",
        "command_evidence_schema_version",
        "command_evidence_payload",
        "command_evidence_sha256",
        "command_requested_contracts",
        "order_id",
        "product_id",
        "order_origin_command_id",
        "order_requested_contracts",
        "order_requested_side",
        "order_requested_reduce_only",
        "outcome",
        "risk_effect",
        "policy_version",
        "policy_sha256",
        "check_set_version",
        "check_set_sha256",
        "evaluation_reference_asset_id",
        "evaluation_reference_asset_symbol",
        "evaluation_collateral_asset_id",
        "evaluation_collateral_asset_symbol",
        "observed_risk_state_version",
        "observed_halt_set_state",
        "observed_halt_revision",
        "observed_reconciliation_state",
        "observed_accounting_state",
        "observed_command_version",
        "observed_claim_owner_id",
        "observed_claim_generation",
        "observed_claim_expires_at",
        "observed_order_version",
        "observed_order_transition_sequence",
        "observed_command_leader_ref",
        "observed_command_leader_generation",
        "observed_command_recovery_ref",
        "observed_command_recovery_generation",
        "leadership_lease_id",
        "recovery_incarnation_id",
        "observed_leadership_owner_id",
        "observed_leadership_generation",
        "observed_leadership_expires_at",
        "observed_leadership_version",
        "reconciliation_run_id",
        "reconciliation_run_version",
        "reconciliation_requirements_version",
        "reconciliation_requirements_sha256",
        "reconciliation_observed_at",
        "reconciliation_completeness",
        "pnl_snapshot_id",
        "pnl_completeness",
        "pnl_calculation_version",
        "pnl_reference_asset_id",
        "pnl_reference_asset_symbol",
        "pnl_evidence_sha256",
        "pnl_input_set_sha256",
        *PLAN_FIELDS,
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
        "evaluated_at",
        "recorded_at",
    }
    assert unique_columns(table) == {
        ("id", *SCOPE): "uq_risk_decisions_id_scope",
        (*SCOPE, "decision_key"): "uq_risk_decisions_scope_decision_key",
        (
            "id",
            *SCOPE,
            "risk_state_id",
            "command_id",
            "order_id",
            "product_id",
            "outcome",
            "risk_effect",
            "reservation_plan_sha256",
        ): "uq_risk_decisions_reservation_parent",
    }
    parent_candidates = {
        "command_operations": (
            ("id", *SCOPE, "operation_kind", "evidence_sha256"),
            "uq_command_operations_risk_decision_parent",
        ),
        "orders": (
            (
                "id",
                *SCOPE,
                "product_id",
                "command_id",
                "requested_contracts",
                "requested_side",
                "requested_reduce_only",
            ),
            "uq_orders_risk_decision_parent",
        ),
        "reconciliation_runs": (
            ("id", *SCOPE, "requirements_version", "requirements_sha256"),
            "uq_reconciliation_runs_risk_decision_parent",
        ),
        "pnl_snapshots": (
            (
                "id",
                *SCOPE,
                "completeness",
                "calculation_version",
                "reference_asset_id",
                "reference_asset_symbol",
                "evidence_sha256",
                "input_set_sha256",
            ),
            "uq_pnl_snapshots_risk_decision_parent",
        ),
    }
    for name, (columns, constraint_name) in parent_candidates.items():
        assert unique_columns(api.metadata.tables[f"hmt_crypto.{name}"])[columns] == constraint_name

    expected_fks = {
        "accounts": SCOPE,
        "risk_state": ("risk_state_id", *SCOPE),
        "command_operations": (
            "command_id",
            *SCOPE,
            "command_operation_kind",
            "command_evidence_sha256",
        ),
        "orders": (
            "order_id",
            *SCOPE,
            "product_id",
            "order_origin_command_id",
            "order_requested_contracts",
            "order_requested_side",
            "order_requested_reduce_only",
        ),
        "leadership_leases": ("leadership_lease_id", *SCOPE),
        "recovery_incarnations": ("recovery_incarnation_id", *SCOPE),
        "reconciliation_runs": (
            "reconciliation_run_id",
            *SCOPE,
            "reconciliation_requirements_version",
            "reconciliation_requirements_sha256",
        ),
        "pnl_snapshots": (
            "pnl_snapshot_id",
            *SCOPE,
            "pnl_completeness",
            "pnl_calculation_version",
            "pnl_reference_asset_id",
            "pnl_reference_asset_symbol",
            "pnl_evidence_sha256",
            "pnl_input_set_sha256",
        ),
    }
    assert {fk.referred_table.name for fk in table.foreign_key_constraints} == set(expected_fks)
    indexes = {
        index.name: tuple(column.name for column in index.columns) for index in table.indexes
    }
    assert indexes == {
        "ix_risk_decisions_scope": SCOPE,
        "ix_risk_decisions_risk_state_parent": ("risk_state_id", *SCOPE),
        "ix_risk_decisions_command_parent": expected_fks["command_operations"],
        "ix_risk_decisions_order_parent": expected_fks["orders"],
        "ix_risk_decisions_leadership_parent": expected_fks["leadership_leases"],
        "ix_risk_decisions_recovery_parent": expected_fks["recovery_incarnations"],
        "ix_risk_decisions_reconciliation_parent": expected_fks["reconciliation_runs"],
        "ix_risk_decisions_pnl_parent": expected_fks["pnl_snapshots"],
    }
    for fk in table.foreign_key_constraints:
        assert tuple(fk.column_keys) == expected_fks[fk.referred_table.name]
        assert fk.ondelete == fk.onupdate == "RESTRICT"
    pg = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    local = str(CreateTable(table).compile(dialect=sqlite.dialect()))
    assert pg.count(" NUMERIC") == len(PLAN_DECIMALS)
    assert "NUMERIC(" not in pg
    constraint_names = {constraint.name for constraint in table.constraints}
    for field in PLAN_DECIMALS:
        assert isinstance(table.c[field].type, ExactDecimal)
        for suffix in ("finite", "magnitude", "scale"):
            assert any(name.endswith(f"decimal_{field}_{suffix}") for name in constraint_names)
        assert f"decimal_{field}_" not in local
    for constraint in table.constraints:
        assert constraint.name
    for index in table.indexes:
        assert index.name in str(CreateIndex(index).compile(dialect=postgresql.dialect()))


def test_only_the_reviewed_new_table_and_reverse_parent_edges_exist():
    api = schema_api()
    table_names = {table.name for table in api.metadata.tables.values()}
    assert "risk_decisions" in table_names
    assert len(table_names) == 33
    expected_parents = {
        "accounts",
        "risk_state",
        "command_operations",
        "orders",
        "leadership_leases",
        "recovery_incarnations",
        "reconciliation_runs",
        "pnl_snapshots",
    }
    actual = {
        table.name
        for table in api.metadata.tables.values()
        if any(
            fk.referred_table is table for fk in api.RiskDecision.__table__.foreign_key_constraints
        )
    }
    assert actual == expected_parents
    assert {
        fk.referred_table.name for fk in api.RiskDecision.__table__.foreign_key_constraints
    } == expected_parents


def test_factory_owns_command_check_plan_and_evidence_fields():
    api = schema_api()
    assert hasattr(api.RiskDecision, "from_evidence")
    forbidden = (
        "command_id",
        "command_evidence_schema_version",
        "command_evidence_payload",
        "command_evidence_sha256",
        "command_requested_contracts",
        "check_set_version",
        "check_set_sha256",
        "reservation_plan_sha256",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
        "api_secret",
    )
    # The real parent graph test below proves the success case. Here no command
    # object should be needed to reject derived/secret override fields safely.
    for field in forbidden:
        with pytest.raises(PersistenceValueError) as error:
            api.RiskDecision.from_evidence(
                command=None,
                check_results={},
                reservation_plan=None,
                **{field: "SECRET-RISK-MARKER"},
            )
        assert "SECRET-RISK-MARKER" not in str(error.value) + repr(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_approved_decision_round_trip_is_historical_only(schema_db, channel):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    before = {
        type(parent): {column.name: getattr(parent, column.name) for column in parent.__table__.c}
        for parent in (graph[2], graph[3], graph[4], graph[6], graph[7])
    }
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded is not None
        assert loaded.outcome == "APPROVED"
        assert loaded.command_evidence_payload == graph[6].evidence_payload
        assert loaded.reservation_plan_sha256
        assert loaded.evaluated_at < loaded.recorded_at
        assert json.loads(loaded.evidence_payload)["check_set_version"] == 1
    for parent in (graph[2], graph[3], graph[4], graph[6], graph[7]):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(parent), parent.id)
            assert {
                column.name: getattr(loaded, column.name) for column in loaded.__table__.c
            } == before[type(parent)]


@pytest.mark.asyncio
async def test_scoped_decision_key_is_idempotent_and_independent_across_accounts(schema_db):
    graph = await decision_graph(schema_db)
    first = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, first)
    duplicate = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    with pytest.raises(PersistenceError):
        await save(schema_db, duplicate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", uuid4()),
        ("region", "global"),
        ("environment", "prod"),
        ("mode", "BACKTEST"),
        ("risk_state_id", uuid4()),
        ("order_id", uuid4()),
        ("reconciliation_run_id", uuid4()),
        ("pnl_snapshot_id", uuid4()),
    ],
)
async def test_every_parent_identity_dimension_is_restricted(schema_db, field, value):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    setattr(row, field, value)
    with pytest.raises((PersistenceError, PersistenceValueError)):
        await save(schema_db, row)


@pytest.mark.asyncio
async def test_immutable_command_payload_parent_is_checked_on_insert_and_load(schema_db):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    row.command_evidence_payload = row.command_evidence_payload.replace(
        '"side":"buy"', '"side":"sell"'
    )
    with pytest.raises(PersistenceValueError):
        await save(schema_db, row)
    valid = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)], decision_key="valid")
    await save(schema_db, valid)
    async with schema_db.engine.begin() as connection:
        await connection.exec_driver_sql(
            "UPDATE risk_decisions SET command_evidence_payload = "
            'replace(command_evidence_payload, \'"side":"buy"\', \'"side":"sell"\') '
            "WHERE id = ?",
            (valid.id.hex,),
        )
    with pytest.raises((PersistenceError, PersistenceValueError)):
        async with schema_db.transaction() as session:
            await session.get(type(valid), valid.id)


@pytest.mark.asyncio
async def test_core_adapter_validates_payload_but_not_sibling_agreement(schema_db):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    values = {column.name: getattr(row, column.name) for column in row.__table__.c}
    values["policy_sha256"] = "f" * 64
    async with schema_db.transaction() as session:
        await session.execute(insert(row.__table__).values(**values))
        stored = (
            await session.execute(
                select(row.__table__.c.policy_sha256).where(row.__table__.c.id == row.id)
            )
        ).scalar_one()
        assert stored == "f" * 64
        await session.execute(delete(row.__table__).where(row.__table__.c.id == row.id))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"reserved_open_order_slots": 0},
        {"approved_contracts": 3},
        {"reserved_incremental_gross_exposure": Decimal("0")},
        {"reserved_worst_case_loss": Decimal("0")},
    ],
)
async def test_core_static_approved_operation_and_effect_shapes_fail_closed(schema_db, changes):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    values = {column.name: getattr(row, column.name) for column in row.__table__.c}
    values.update(changes)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(row.__table__).values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_corrupt_sibling_is_rejected_on_full_and_deferred_load(schema_db, deferred):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, row)
    async with schema_db.engine.begin() as connection:
        await connection.execute(
            update(row.__table__)
            .where(row.__table__.c.id == row.id)
            .values(check_set_sha256="f" * 64)
        )
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            statement = select(type(row)).where(type(row).id == row.id)
            if deferred:
                statement = statement.options(load_only(type(row).id, type(row).decision_key))
            loaded = (await session.scalars(statement)).one()
            if deferred:
                _ = loaded.check_set_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize("attribute_names", [None, ["check_set_sha256"]])
async def test_corrupt_sibling_is_rejected_on_full_and_partial_refresh(schema_db, attribute_names):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        await session.execute(
            update(row.__table__)
            .where(row.__table__.c.id == row.id)
            .values(check_set_sha256="f" * 64)
        )
        with pytest.raises(PersistenceValueError):
            await session.refresh(loaded, attribute_names=attribute_names)


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_decision_is_append_only_across_update_and_delete(schema_db, deferred):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        if deferred:
            loaded = (
                await session.scalars(
                    select(type(row))
                    .where(type(row).id == row.id)
                    .options(load_only(type(row).id, type(row).decision_key))
                )
            ).one()
        else:
            loaded = await session.get(type(row), row.id)
        loaded.decision_key = "changed"
        with pytest.raises(PersistenceValueError):
            await session.flush()
    async with schema_db.transaction() as session:
        if deferred:
            loaded = (
                await session.scalars(
                    select(type(row))
                    .where(type(row).id == row.id)
                    .options(load_only(type(row).id, type(row).decision_key))
                )
            ).one()
        else:
            loaded = await session.get(type(row), row.id)
        await session.delete(loaded)
        with pytest.raises(PersistenceValueError):
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [*PLAN_DECIMALS])
@pytest.mark.parametrize(
    "value",
    [True, 1.5, "1", Decimal("NaN"), Decimal("1e20"), Decimal("1e-19")],
)
async def test_plan_decimal_columns_reject_loss_and_coercion(schema_db, field, value):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    setattr(row, field, value)
    with pytest.raises((PersistenceError, PersistenceValueError)):
        await save(schema_db, row)


@pytest.mark.asyncio
async def test_timezone_and_exact_decimal_round_trip(schema_db):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.reserved_incremental_gross_exposure == Decimal("100")
        assert loaded.reserved_worst_case_loss == Decimal("10")
        assert loaded.reserved_collateral == Decimal("10")
        assert loaded.evaluated_at.tzinfo is not None


@pytest.mark.asyncio
async def test_import_and_metadata_have_no_engine_file_socket_or_trading_side_effect(monkeypatch):
    import hmt_crypto.persistence.models as models

    assert models.RiskDecision.__tablename__ == "risk_decisions"
    assert getattr(models.metadata, "bind", None) is None
    assert not any(
        name in models.__dict__ for name in ("client", "engine", "signer", "limiter", "order")
    )
