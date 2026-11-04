"""Immutable risk-decision evidence is never a current dispatch authorization."""

import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from execution_test_support import command
from risk_decision_schema_support import (
    check_results,
    decision,
    decision_graph,
    reservation_plan,
)
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
from test_coordination_schema import lease as leadership_lease
from test_coordination_schema import save
from test_pnl_snapshot_schema import pnl
from test_reconciliation_run_schema import outcome as reconciliation_outcome
from test_reconciliation_run_schema import run as reconciliation_run

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
            "command_operation_kind",
            "order_id",
            "product_id",
            "outcome",
            "risk_effect",
            "reservation_plan_sha256",
            "evaluated_at",
            "recorded_at",
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
    assert len(table_names) == 35
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


def _live_decision(graph, **changes):
    owner, recovery, reconciliation, calculation, state, claim_owner, cmd, row, leadership = graph
    assert leadership is not None
    live = dict(
        observed_command_leader_ref=leadership.id,
        observed_command_leader_generation=leadership.generation,
        observed_command_recovery_ref=recovery.id,
        observed_command_recovery_generation=leadership.generation,
        leadership_lease_id=leadership.id,
        recovery_incarnation_id=recovery.id,
        observed_leadership_owner_id=claim_owner.id,
        observed_leadership_generation=leadership.generation,
        observed_leadership_expires_at=leadership.expires_at,
        observed_leadership_version=leadership.version,
    )
    live.update(changes)
    return decision(
        owner,
        reconciliation,
        calculation,
        state,
        claim_owner,
        cmd,
        row,
        **live,
    )


@pytest.mark.asyncio
async def test_live_decision_round_trip_binds_complete_fence_bundle(schema_db):
    graph = await decision_graph(schema_db, mode="LIVE", profile_name="live-fixture")
    row = _live_decision(graph)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.leadership_lease_id == graph[8].id
        assert loaded.observed_command_recovery_ref == graph[1].id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "leadership_lease_id",
        "recovery_incarnation_id",
        "observed_leadership_owner_id",
        "observed_leadership_generation",
        "observed_leadership_expires_at",
        "observed_leadership_version",
        "observed_command_leader_ref",
        "observed_command_leader_generation",
        "observed_command_recovery_ref",
        "observed_command_recovery_generation",
    ],
)
async def test_core_live_bundle_members_cannot_bypass_checks_with_null(schema_db, field):
    graph = await decision_graph(schema_db, mode="LIVE", profile_name="live-fixture")
    row = _live_decision(graph)
    values = {column.name: getattr(row, column.name) for column in row.__table__.c}
    values[field] = None
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(row.__table__).values(**values))


@pytest.mark.asyncio
async def test_scoped_decision_key_is_idempotent_and_independent_across_accounts(schema_db):
    graph = await decision_graph(schema_db)
    first = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, first)
    duplicate = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    with pytest.raises(PersistenceError):
        await save(schema_db, duplicate)

    other_graph = await decision_graph(schema_db, catalogue_revision=2, profile_name="fixture-2")
    independent = decision(*[other_graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, independent)
    async with schema_db.transaction() as session:
        assert len((await session.scalars(select(type(first)))).all()) == 2


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


async def _alternate_scope_graphs(schema_db, dimension):
    first = dict(profile_name="scope-a", catalogue_revision=1)
    second = dict(profile_name="scope-b", catalogue_revision=2)
    if dimension == "region":
        first["region"] = "global"
        second["catalogue_revision"] = 1
    elif dimension == "environment":
        first["environment"] = "prod"
        second["catalogue_revision"] = 1
    elif dimension == "mode":
        second["mode"] = "BACKTEST"
    return await decision_graph(schema_db, **first), await decision_graph(schema_db, **second)


async def _modify_decision(schema_db, graph, suffix):
    owner, reconciliation, calculation, state, claim_owner, _, row = [
        graph[index] for index in (0, 2, 3, 4, 5, 6, 7)
    ]
    modify = command(
        owner,
        request_id=f"scope-modify-{suffix}",
        operation_kind="MODIFY_ORDER",
        status="CLAIMED",
        claim_owner=claim_owner.id,
        claim_generation=1,
        claim_expires_at=graph[6].claim_expires_at,
        evidence={
            "product_id": row.product_id,
            "target_order_id": row.id,
            "requested_contracts": 3,
        },
    )
    await save(schema_db, modify)
    return modify, decision(
        owner,
        reconciliation,
        calculation,
        state,
        claim_owner,
        modify,
        row,
        decision_key=f"scope-modify-{suffix}",
        reservation_plan_override=reservation_plan(
            approved_contracts=3,
            reserved_open_order_slots=0,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", ["account_id", "region", "environment", "mode"])
@pytest.mark.parametrize("edge", ["risk_state", "command", "order", "reconciliation", "pnl"])
async def test_each_non_live_parent_scope_edge_is_isolated_with_alternate_valid_graphs(
    schema_db, dimension, edge
):
    alternate, child = await _alternate_scope_graphs(schema_db, dimension)
    if edge in {"command", "order"}:
        alternate_modify, _ = await _modify_decision(schema_db, alternate, "a")
        _, row = await _modify_decision(schema_db, child, "b")
    else:
        alternate_modify = None
        row = decision(*[child[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    values = {column.name: getattr(row, column.name) for column in row.__table__.c}
    if edge == "risk_state":
        values["risk_state_id"] = alternate[4].id
    elif edge == "command":
        assert alternate_modify is not None
        values.update(
            command_id=alternate_modify.id,
            command_operation_kind=alternate_modify.operation_kind,
            command_evidence_sha256=alternate_modify.evidence_sha256,
        )
    elif edge == "order":
        other_order = alternate[7]
        values.update(
            order_id=other_order.id,
            product_id=other_order.product_id,
            order_origin_command_id=other_order.command_id,
            order_requested_contracts=other_order.requested_contracts,
            order_requested_side=other_order.requested_side,
            order_requested_reduce_only=other_order.requested_reduce_only,
        )
    elif edge == "reconciliation":
        other_run = alternate[2]
        values.update(
            reconciliation_run_id=other_run.id,
            reconciliation_requirements_version=other_run.requirements_version,
            reconciliation_requirements_sha256=other_run.requirements_sha256,
        )
    else:
        other_pnl = alternate[3]
        values.update(
            pnl_snapshot_id=other_pnl.id,
            pnl_completeness=other_pnl.completeness,
            pnl_calculation_version=other_pnl.calculation_version,
            pnl_reference_asset_id=other_pnl.reference_asset_id,
            pnl_reference_asset_symbol=other_pnl.reference_asset_symbol,
            pnl_evidence_sha256=other_pnl.evidence_sha256,
            pnl_input_set_sha256=other_pnl.input_set_sha256,
        )
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(row.__table__).values(**values))


async def _alternate_live_scope_graphs(schema_db, dimension):
    alternate = dict(
        profile_name="live-scope-a",
        catalogue_revision=1,
        mode="LIVE",
    )
    child = dict(
        profile_name="live-scope-b",
        catalogue_revision=2,
        mode="LIVE",
    )
    if dimension == "region":
        alternate["region"] = "global"
        child["catalogue_revision"] = 1
    elif dimension == "environment":
        alternate["environment"] = "prod"
        child["catalogue_revision"] = 1
    elif dimension == "mode":
        alternate["mode"] = "PAPER"
    first = list(await decision_graph(schema_db, **alternate))
    second = await decision_graph(schema_db, **child)
    if first[8] is None:
        first[8] = leadership_lease(
            first[0],
            first[1],
            state="HELD",
            owner_id=first[5].id,
            generation=3,
            heartbeat_at=first[5].last_seen_at,
            expires_at=first[6].claim_expires_at,
            version=1,
            updated_at=first[5].last_seen_at + timedelta(seconds=1),
        )
        await save(schema_db, first[8])
    return tuple(first), second


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", ["account_id", "region", "environment", "mode"])
@pytest.mark.parametrize("edge", ["leadership", "recovery"])
async def test_each_live_identity_parent_scope_edge_is_isolated_with_alternate_valid_graphs(
    schema_db, dimension, edge
):
    alternate, child = await _alternate_live_scope_graphs(schema_db, dimension)
    row = _live_decision(child)
    values = {column.name: getattr(row, column.name) for column in row.__table__.c}
    if edge == "leadership":
        values["leadership_lease_id"] = alternate[8].id
        values["observed_command_leader_ref"] = alternate[8].id
    else:
        values["recovery_incarnation_id"] = alternate[1].id
        values["observed_command_recovery_ref"] = alternate[1].id
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(row.__table__).values(**values))


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
@pytest.mark.parametrize(
    "field",
    [
        "command_requested_contracts",
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
    ],
)
async def test_core_required_branch_members_cannot_bypass_checks_with_null(schema_db, field):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    values = {column.name: getattr(row, column.name) for column in row.__table__.c}
    values[field] = None
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(row.__table__).values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize("field", PLAN_FIELDS)
async def test_core_approved_plan_members_are_all_required(schema_db, field):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    values = {column.name: getattr(row, column.name) for column in row.__table__.c}
    values[field] = None
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(row.__table__).values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {
            "evaluation_reference_asset_id": None,
            "evaluation_reference_asset_symbol": None,
        },
        {
            "evaluation_collateral_asset_id": None,
            "evaluation_collateral_asset_symbol": None,
        },
    ],
)
async def test_core_required_evaluation_denominations_cannot_be_all_null(schema_db, changes):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    values = {column.name: getattr(row, column.name) for column in row.__table__.c}
    values.update(changes)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(row.__table__).values(**values))


class _AlwaysEqualString(str):
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    __hash__ = str.__hash__


class _AlwaysEqualUUID(UUID):
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    __hash__ = UUID.__hash__


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["integer_boolean", "string_subclass", "uuid_subclass"])
async def test_orm_sibling_agreement_rejects_coercion_and_hostile_subclasses(schema_db, kind):
    graph = await decision_graph(schema_db)
    row = decision(
        *[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)],
        decision_key=f"hostile-{kind}",
    )
    if kind == "integer_boolean":
        row.order_requested_reduce_only = 0
    elif kind == "string_subclass":
        row.policy_sha256 = _AlwaysEqualString("f" * 64)
    else:
        row.observed_claim_owner_id = _AlwaysEqualUUID(str(uuid4()))
    with pytest.raises(PersistenceValueError):
        await save(schema_db, row)


@pytest.mark.asyncio
async def test_genuine_size_changing_modify_and_price_only_denial_persist(schema_db):
    graph = await decision_graph(schema_db)
    owner, reconciliation, calculation, state, claim_owner, _, row = [
        graph[index] for index in (0, 2, 3, 4, 5, 6, 7)
    ]
    modify = command(
        owner,
        request_id="modify-size",
        operation_kind="MODIFY_ORDER",
        status="CLAIMED",
        claim_owner=claim_owner.id,
        claim_generation=1,
        claim_expires_at=row.updated_at + timedelta(minutes=3),
        evidence={
            "product_id": row.product_id,
            "target_order_id": row.id,
            "requested_contracts": 3,
        },
    )
    await save(schema_db, modify)
    changed = decision(
        owner,
        reconciliation,
        calculation,
        state,
        claim_owner,
        modify,
        row,
        decision_key="modify-size",
        reservation_plan_override=reservation_plan(
            approved_contracts=3,
            reserved_open_order_slots=0,
        ),
    )
    await save(schema_db, changed)

    price_only = command(
        owner,
        request_id="modify-price",
        operation_kind="MODIFY_ORDER",
        status="CLAIMED",
        claim_owner=claim_owner.id,
        claim_generation=1,
        claim_expires_at=modify.claim_expires_at,
        evidence={
            "product_id": row.product_id,
            "target_order_id": row.id,
            "limit_price": Decimal("11"),
        },
    )
    await save(schema_db, price_only)
    denied_checks = check_results()
    denied_checks["DUPLICATE_ORDER"] = {
        **denied_checks["DUPLICATE_ORDER"],
        "outcome": "FAIL",
        "reason_code": "DUPLICATE_ORDER",
    }
    denied_checks["REDUCE_ONLY_NO_REVERSAL"] = {
        **denied_checks["REDUCE_ONLY_NO_REVERSAL"],
        "outcome": "UNKNOWN",
        "reason_code": "MISSING_EVIDENCE",
    }
    denied = decision(
        owner,
        reconciliation,
        calculation,
        state,
        claim_owner,
        price_only,
        row,
        decision_key="modify-price-denied",
        outcome="DENIED",
        check_results_override=denied_checks,
        reservation_plan_override=None,
    )
    await save(schema_db, denied)
    async with schema_db.transaction() as session:
        stored = (await session.scalars(select(type(changed)))).all()
        assert {(item.command_requested_contracts, item.outcome) for item in stored} == {
            (3, "APPROVED"),
            (None, "DENIED"),
        }


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
async def test_unrelated_partial_refresh_validates_complete_current_stored_row(schema_db):
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
            await session.refresh(loaded, attribute_names=["decision_key"])


@pytest.mark.asyncio
async def test_partial_refresh_validation_does_not_discard_pending_changes(schema_db):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        loaded.decision_key = "pending-local-change"
        with session.no_autoflush:
            await session.refresh(loaded, attribute_names=["recorded_at"])
        assert loaded.decision_key == "pending-local-change"
        session.expunge(loaded)


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


def _denied_checks(*, reconciliation_outcome="PASS"):
    results = check_results()
    results["DUPLICATE_ORDER"] = {
        **results["DUPLICATE_ORDER"],
        "outcome": "FAIL",
        "reason_code": "DUPLICATE_ORDER",
    }
    if reconciliation_outcome == "UNKNOWN":
        results["FRESHNESS_RECONCILIATION"] = {
            **results["FRESHNESS_RECONCILIATION"],
            "outcome": "UNKNOWN",
            "reason_code": "MISSING_EVIDENCE",
            "metric_kind": "NONE",
            "unit": "NONE",
            "observed_integer": None,
            "limit_integer": None,
            "evidence_observed_at": None,
        }
    elif reconciliation_outcome == "FAIL":
        results["FRESHNESS_RECONCILIATION"] = {
            **results["FRESHNESS_RECONCILIATION"],
            "outcome": "FAIL",
            "reason_code": "RECONCILIATION_NOT_READY",
        }
    return results


@pytest.mark.asyncio
async def test_denied_reconciliation_accounting_and_denomination_variants_round_trip(schema_db):
    graph = await decision_graph(schema_db)
    owner, recovery, reconciliation, _, state, claim_owner, cmd, order = graph[:8]

    unknown = decision(
        owner,
        reconciliation,
        graph[3],
        state,
        claim_owner,
        cmd,
        order,
        decision_key="denied-unknown",
        outcome="DENIED",
        observed_reconciliation_state="UNKNOWN",
        reconciliation_run_id=None,
        reconciliation_run_version=None,
        reconciliation_requirements_version=None,
        reconciliation_requirements_sha256=None,
        reconciliation_observed_at=None,
        reconciliation_completeness=None,
        observed_accounting_state="UNKNOWN",
        pnl_snapshot_id=None,
        pnl_completeness=None,
        pnl_calculation_version=None,
        pnl_reference_asset_id=None,
        pnl_reference_asset_symbol=None,
        pnl_evidence_sha256=None,
        pnl_input_set_sha256=None,
        check_results_override=_denied_checks(reconciliation_outcome="UNKNOWN"),
        reservation_plan_override=None,
    )
    await save(schema_db, unknown)

    partial_run = reconciliation_run(
        owner,
        recovery,
        **reconciliation_outcome(completeness="PARTIAL"),
    )
    await save(schema_db, partial_run)
    partial_pnl = pnl(
        owner,
        partial_run,
        "PARTIAL",
        snapshot_key="pnl-partial-risk-decision",
    )
    await save(schema_db, partial_pnl)
    partial = decision(
        owner,
        partial_run,
        partial_pnl,
        state,
        claim_owner,
        cmd,
        order,
        decision_key="denied-partial",
        outcome="DENIED",
        observed_reconciliation_state="BLOCKED",
        observed_accounting_state="PARTIAL",
        check_results_override=_denied_checks(reconciliation_outcome="FAIL"),
        reservation_plan_override=None,
    )
    await save(schema_db, partial)

    btc_pnl = pnl(
        owner,
        reconciliation,
        snapshot_key="pnl-btc-risk-decision",
        reference_asset_id=77,
        reference_asset_symbol="BTC",
    )
    await save(schema_db, btc_pnl)
    denominated = decision(
        owner,
        reconciliation,
        btc_pnl,
        state,
        claim_owner,
        cmd,
        order,
        decision_key="denied-btc",
        outcome="DENIED",
        evaluation_reference_asset_id=77,
        evaluation_reference_asset_symbol="BTC",
        check_results_override=_denied_checks(),
        reservation_plan_override=None,
    )
    await save(schema_db, denominated)

    async with schema_db.transaction() as session:
        stored = {
            row.decision_key: row for row in (await session.scalars(select(type(unknown)))).all()
        }
        assert stored["denied-unknown"].reservation_plan_sha256 is None
        assert stored["denied-unknown"].observed_accounting_state == "UNKNOWN"
        assert stored["denied-partial"].pnl_completeness == "PARTIAL"
        assert stored["denied-btc"].pnl_reference_asset_symbol == "BTC"


@pytest.mark.asyncio
async def test_expired_decision_reload_validates_complete_evidence(schema_db):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        session.expire(loaded)
        await session.run_sync(lambda _: loaded.evidence_sha256)
        assert loaded.decision_key == "decision-1"
        session.expire(loaded, ["check_set_sha256"])
        await session.run_sync(lambda _: loaded.check_set_sha256)
        assert loaded.check_set_sha256 == row.check_set_sha256


@pytest.mark.asyncio
async def test_expired_decision_reload_rejects_corrupt_stored_sibling(schema_db):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        session.expire(loaded)
        await session.execute(
            update(row.__table__)
            .where(row.__table__.c.id == row.id)
            .values(check_set_sha256="f" * 64)
        )
        with pytest.raises(PersistenceValueError):
            await session.run_sync(lambda _: loaded.evidence_sha256)


@pytest.mark.asyncio
async def test_success_and_rollback_leave_every_parent_byte_for_byte_unchanged(schema_db):
    graph = await decision_graph(schema_db)
    row = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    parent_tables = [
        table for table in schema_api().metadata.tables.values() if table.name != "risk_decisions"
    ]
    async with schema_db.transaction() as session:
        before = {
            table.name: (await session.execute(select(table))).all() for table in parent_tables
        }
    await save(schema_db, row)
    collision_one = decision(
        *[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)],
        decision_key="rollback-collision",
    )
    collision_two = decision(
        *[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)],
        decision_key="rollback-collision",
    )
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add_all([collision_one, collision_two])
    async with schema_db.transaction() as session:
        assert before == {
            table.name: (await session.execute(select(table))).all() for table in parent_tables
        }
        assert len((await session.scalars(select(type(row)))).all()) == 1


@pytest.mark.asyncio
async def test_maximum_valid_decision_round_trips_through_database(schema_db):
    graph = await decision_graph(schema_db)
    owner, _, reconciliation, _, state, claim_owner, cmd, order = graph[:8]
    maximum_integer = 2**63 - 1
    maximum_decimal = Decimal("99999999999999999999.999999999999999999")
    maximum_reference = "r" * 128
    maximum_collateral = "c" * 128
    maximum_pnl = pnl(
        owner,
        reconciliation,
        snapshot_key="maximum-risk-decision-pnl",
        reference_asset_id=maximum_integer,
        reference_asset_symbol=maximum_reference,
    )
    await save(schema_db, maximum_pnl)
    results = check_results()
    for item in results.values():
        if item["metric_kind"] == "DECIMAL":
            item["observed_decimal"] = maximum_decimal
            item["limit_decimal"] = maximum_decimal
    maximum = decision(
        owner,
        reconciliation,
        maximum_pnl,
        state,
        claim_owner,
        cmd,
        order,
        decision_key="x" * 128,
        evaluation_reference_asset_id=maximum_integer,
        evaluation_reference_asset_symbol=maximum_reference,
        evaluation_collateral_asset_id=maximum_integer,
        evaluation_collateral_asset_symbol=maximum_collateral,
        check_results_override=results,
        reservation_plan_override=reservation_plan(
            reference_asset_id=maximum_integer,
            reference_asset_symbol=maximum_reference,
            collateral_asset_id=maximum_integer,
            collateral_asset_symbol=maximum_collateral,
            reserved_incremental_gross_exposure=maximum_decimal,
            reserved_worst_case_loss=maximum_decimal,
            reserved_collateral=maximum_decimal,
        ),
    )
    assert len(maximum.evidence_payload.encode("ascii")) <= 16_384
    await save(schema_db, maximum)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(maximum), maximum.id)
        assert loaded.decision_key == "x" * 128
        assert loaded.reserved_collateral == maximum_decimal
        assert loaded.pnl_reference_asset_symbol == maximum_reference


@pytest.mark.asyncio
async def test_import_and_metadata_have_no_engine_file_socket_or_trading_side_effect(monkeypatch):
    import hmt_crypto.persistence.models as models

    assert models.RiskDecision.__tablename__ == "risk_decisions"
    assert getattr(models.metadata, "bind", None) is None
    assert not any(
        name in models.__dict__ for name in ("client", "engine", "signer", "limiter", "order")
    )
