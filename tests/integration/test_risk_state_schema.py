"""Offline risk-state and halt-cycle evidence; never execution authority."""

import subprocess
import sys
from datetime import timedelta, timezone
from decimal import Decimal
from itertools import product
from uuid import uuid4

import pytest
from schema_test_support import HASH, NOW, account, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import UniqueConstraint, delete, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateIndex, CreateTable
from test_coordination_schema import SCOPE, graph, save
from test_pnl_snapshot_schema import pnl
from test_reconciliation_drift_schema import drift
from test_reconciliation_run_schema import run

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError

CAUSES = ("GLOBAL_KILL", "AUTOMATIC_RISK", "DAILY_LOSS", "RECONCILIATION")
EVENTS = ("ENGAGED", "REAFFIRMED", "RELEASED")
ACTORS = ("OPERATOR", "SYSTEM", "RECONCILIATION", "RECOVERY")
REASONS = (
    "OPERATOR_KILL",
    "DAILY_LOSS_LIMIT",
    "RECONCILIATION_INCOMPLETE",
    "STALE_RECONCILIATION",
    "CRITICAL_DRIFT",
    "ACCOUNTING_UNKNOWN",
    "PROTECTION_INADEQUATE",
    "RISK_POLICY",
    "RECOVERY_REQUIRED",
    "OPERATOR_RELEASE",
    "UTC_RESET",
    "RECONCILIATION_RECOVERED",
    "ACCOUNTING_RECOVERED",
    "PROTECTION_RESTORED",
    "RECOVERY_COMPLETED",
)
ACCOUNTING_FIELDS = (
    "pnl_snapshot_id",
    "accounting_calculation_version",
    "accounting_input_set_sha256",
    "accounting_window_start_at",
    "accounting_window_end_at",
    "reference_asset_id",
    "reference_asset_symbol",
    "baseline_equity",
    "conservative_daily_loss",
)


async def full_graph(db, *, owner=None):
    if owner is None:
        owner, recovery = await graph(db)
    else:
        from test_coordination_schema import incarnation

        recovery = incarnation(owner)
        await save(db, owner)
        await save(db, recovery)
    reconciliation = run(owner, recovery)
    await save(db, reconciliation)
    calculation = pnl(owner, reconciliation)
    await save(db, calculation)
    discrepancy = drift(owner, reconciliation)
    await save(db, discrepancy)
    return owner, reconciliation, calculation, discrepancy


def alternate_account(field):
    changes = {
        "account_id": {},
        "region": {"region": "global"},
        "environment": {"environment": "prod"},
        "mode": {"mode": "BACKTEST"},
    }[field]
    return account(profile_name=f"alternate-{field}", **changes)


def risk_state(owner, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        halt_set_state="UNINITIALIZED",
        halt_revision=0,
        reconciliation_state="UNKNOWN",
        reconciliation_run_id=None,
        reconciliation_run_version=None,
        reconciliation_observed_at=None,
        accounting_state="UNKNOWN",
        pnl_snapshot_id=None,
        accounting_calculation_version=None,
        accounting_input_set_sha256=None,
        accounting_window_start_at=None,
        accounting_window_end_at=None,
        reference_asset_id=None,
        reference_asset_symbol=None,
        baseline_equity=None,
        conservative_daily_loss=None,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(changes)
    return schema_api().RiskState(**values)


def claimed_state(owner, reconciliation, calculation, **changes):
    values = dict(
        halt_set_state="INITIALIZED",
        reconciliation_state="READY_CLAIM",
        reconciliation_run_id=reconciliation.id,
        reconciliation_run_version=reconciliation.version,
        reconciliation_observed_at=NOW + timedelta(seconds=1),
        accounting_state="COMPLETE_CLAIM",
        pnl_snapshot_id=calculation.id,
        accounting_calculation_version=calculation.calculation_version,
        accounting_input_set_sha256=calculation.input_set_sha256,
        accounting_window_start_at=calculation.window_start_at,
        accounting_window_end_at=calculation.window_end_at,
        reference_asset_id=calculation.reference_asset_id,
        reference_asset_symbol=calculation.reference_asset_symbol,
        baseline_equity=calculation.baseline_equity,
        conservative_daily_loss=calculation.conservative_daily_loss,
        updated_at=NOW + timedelta(seconds=2),
    )
    values.update(changes)
    return risk_state(owner, **values)


def halt(owner, state, cause="AUTOMATIC_RISK", **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        risk_state_id=state.id,
        halt_cause=cause,
        generation=1,
        created_at=NOW + timedelta(seconds=3),
    )
    values.update(changes)
    return schema_api().RiskHalt(**values)


def halt_event(owner, cycle, **changes):
    defaults = {
        "GLOBAL_KILL": ("OPERATOR", "OPERATOR_KILL"),
        "AUTOMATIC_RISK": ("SYSTEM", "RISK_POLICY"),
        "DAILY_LOSS": ("SYSTEM", "DAILY_LOSS_LIMIT"),
        "RECONCILIATION": ("RECONCILIATION", "CRITICAL_DRIFT"),
    }
    actor, reason = defaults[cycle.halt_cause]
    values = dict(
        id=uuid4(),
        **scope(owner),
        halt_id=cycle.id,
        risk_state_id=cycle.risk_state_id,
        halt_cause=cycle.halt_cause,
        generation=cycle.generation,
        sequence=1,
        event_kind="ENGAGED",
        from_state="CLEARED",
        to_state="ACTIVE",
        idempotency_key=str(uuid4()),
        actor_kind=actor,
        reason_code=reason,
        reconciliation_run_id=None,
        pnl_snapshot_id=None,
        drift_id=None,
        supporting_evidence_sha256=None,
        occurred_at=NOW + timedelta(seconds=4),
        recorded_at=NOW + timedelta(seconds=5),
    )
    values.update(changes)
    return schema_api().RiskHaltEvent.from_evidence(**values)


def released(owner, cycle, **changes):
    values = dict(
        sequence=2,
        event_kind="RELEASED",
        from_state="ACTIVE",
        to_state="CLEARED",
        reason_code="RECOVERY_COMPLETED",
    )
    values.update(changes)
    return halt_event(owner, cycle, **values)


def test_factory_owns_payload_inputs_and_rejects_overrides_or_secrets():
    owner = account()
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    values = {"supporting_evidence_sha256": HASH}
    row = halt_event(owner, cycle, **values)
    values["supporting_evidence_sha256"] = "b" * 64
    assert row.supporting_evidence_sha256 == HASH
    for field, value in (
        ("evidence_schema_version", 1),
        ("evidence_payload", "{}"),
        ("evidence_sha256", HASH),
        ("api_secret", "SECRET-RISK-MARKER"),
    ):
        with pytest.raises(PersistenceValueError) as exc:
            halt_event(owner, cycle, **{field: value})
        assert "SECRET-RISK-MARKER" not in str(exc.value)
        assert "SECRET-RISK-MARKER" not in repr(exc.value)


def test_exact_three_tables_exports_columns_constraints_indexes_and_postgres_ddl():
    api = schema_api()
    for name in ("RiskState", "RiskHalt", "RiskHaltEvent"):
        assert hasattr(api, name), f"Missing {name} export"
    state_table = api.RiskState.__table__
    halt_table = api.RiskHalt.__table__
    event_table = api.RiskHaltEvent.__table__
    assert {state_table.name, halt_table.name, event_table.name} == {
        "risk_state",
        "risk_halts",
        "risk_halt_events",
    }
    assert set(state_table.c.keys()) == {
        "id",
        *SCOPE,
        "halt_set_state",
        "halt_revision",
        "reconciliation_state",
        "reconciliation_run_id",
        "reconciliation_run_version",
        "reconciliation_observed_at",
        "accounting_state",
        *ACCOUNTING_FIELDS,
        "version",
        "created_at",
        "updated_at",
    }
    assert set(halt_table.c.keys()) == {
        "id",
        *SCOPE,
        "risk_state_id",
        "halt_cause",
        "generation",
        "created_at",
    }
    assert set(event_table.c.keys()) == {
        "id",
        *SCOPE,
        "halt_id",
        "risk_state_id",
        "halt_cause",
        "generation",
        "sequence",
        "event_kind",
        "from_state",
        "to_state",
        "idempotency_key",
        "actor_kind",
        "reason_code",
        "reconciliation_run_id",
        "pnl_snapshot_id",
        "drift_id",
        "supporting_evidence_sha256",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
        "occurred_at",
        "recorded_at",
    }

    def unique(table):
        return {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }

    assert unique(state_table) == {("id", *SCOPE), SCOPE}
    assert unique(halt_table) == {
        ("id", *SCOPE),
        (*SCOPE, "halt_cause", "generation"),
        ("id", *SCOPE, "risk_state_id", "halt_cause", "generation"),
    }
    assert {
        ("id", *SCOPE),
        ("halt_id", *SCOPE, "sequence"),
        (*SCOPE, "idempotency_key"),
        ("id", *SCOPE, "halt_id", "halt_cause", "event_kind", "generation"),
    } <= unique(event_table)
    expected_fks = {
        "risk_state": {"accounts", "reconciliation_runs", "pnl_snapshots"},
        "risk_halts": {"accounts", "risk_state"},
        "risk_halt_events": {
            "accounts",
            "risk_halts",
            "reconciliation_runs",
            "pnl_snapshots",
            "reconciliation_drifts",
        },
    }
    dialect = postgresql.dialect()
    for table in (state_table, halt_table, event_table):
        assert {fk.referred_table.name for fk in table.foreign_key_constraints} == expected_fks[
            table.name
        ]
        indexes = {tuple(column.name for column in index.columns) for index in table.indexes}
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "UUID" in ddl and "BIGINT" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl
        for constraint in table.constraints:
            assert constraint.name and constraint.name in ddl
        for fk in table.foreign_key_constraints:
            assert fk.ondelete == fk.onupdate == "RESTRICT"
            assert tuple(fk.column_keys) in indexes or tuple(fk.column_keys) == SCOPE
        for index in table.indexes:
            compiled = str(CreateIndex(index).compile(dialect=dialect))
            assert index.name and "CREATE" in compiled
    state_ddl = str(CreateTable(state_table).compile(dialect=dialect))
    assert "NUMERIC" in state_ddl and "NUMERIC(" not in state_ddl
    release_index = next(index for index in event_table.indexes if "one_release" in index.name)
    assert release_index.unique
    assert "WHERE event_kind = 'RELEASED'" in str(
        CreateIndex(release_index).compile(dialect=dialect)
    )
    for prior in api.metadata.tables.values():
        if prior not in {state_table, halt_table, event_table}:
            assert not {"risk_state", "risk_halts", "risk_halt_events"} & {
                fk.referred_table.name for fk in prior.foreign_key_constraints
            }


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_initial_state_and_initialized_seeded_clear_have_no_cycles(schema_db, channel):
    owner, _ = await graph(schema_db)
    row = risk_state(owner)
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        assert (stored.halt_set_state, stored.halt_revision, stored.version) == (
            "UNINITIALIZED",
            0,
            1,
        )
        assert (await session.scalars(select(schema_api().RiskHalt))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "changes",
    [
        {"halt_revision": 1},
        {"reconciliation_state": "BLOCKED"},
        {"accounting_state": "PARTIAL"},
        {"version": 2},
    ],
)
async def test_uninitialized_state_is_exact_initial_shape(schema_db, channel, changes):
    owner, _ = await graph(schema_db)
    with pytest.raises(PersistenceError):
        await save(schema_db, risk_state(owner, **changes), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("state", ["UNKNOWN", "BLOCKED", "READY_CLAIM"])
@pytest.mark.parametrize("present", list(product([False, True], repeat=3)))
async def test_reconciliation_claim_tuple_is_exact(schema_db, channel, state, present):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    row = claimed_state(owner, reconciliation, calculation, reconciliation_state=state)
    for field, exists in zip(
        ("reconciliation_run_id", "reconciliation_run_version", "reconciliation_observed_at"),
        present,
        strict=True,
    ):
        if not exists:
            setattr(row, field, None)
    valid = present == ((False, False, False) if state == "UNKNOWN" else (True, True, True))
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "accounting_state,changes,valid",
    [
        ("UNKNOWN", {field: None for field in ACCOUNTING_FIELDS}, True),
        ("UNKNOWN", {"baseline_equity": Decimal("0")}, False),
        ("PARTIAL", {"baseline_equity": None, "conservative_daily_loss": None}, True),
        ("PARTIAL", {"baseline_equity": Decimal("0"), "conservative_daily_loss": None}, True),
        ("PARTIAL", {"pnl_snapshot_id": None, "conservative_daily_loss": None}, False),
        ("PARTIAL", {"accounting_input_set_sha256": None, "conservative_daily_loss": None}, False),
        ("PARTIAL", {"conservative_daily_loss": Decimal("0")}, False),
        ("COMPLETE_CLAIM", {}, True),
        ("COMPLETE_CLAIM", {"baseline_equity": None}, False),
        ("COMPLETE_CLAIM", {"conservative_daily_loss": None}, False),
    ],
)
async def test_accounting_claim_tuple_shapes(schema_db, channel, accounting_state, changes, valid):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    row = claimed_state(owner, reconciliation, calculation, accounting_state=accounting_state)
    for field, value in changes.items():
        setattr(row, field, value)
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["halt_revision", "reconciliation_run_version"])
@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1", 2**63])
async def test_risk_state_integer_admission(schema_db, channel, field, value):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    row = claimed_state(owner, reconciliation, calculation)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["baseline_equity", "conservative_daily_loss"])
@pytest.mark.parametrize(
    "value",
    [True, 1.5, "1.5", Decimal("NaN"), Decimal("Infinity"), Decimal("1e20"), Decimal("1e-19")],
)
async def test_risk_state_decimal_admission(schema_db, channel, field, value):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    row = claimed_state(owner, reconciliation, calculation)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_signed_baseline_zero_loss_and_utc_normalization(schema_db, channel):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    offset = timezone(timedelta(hours=5, minutes=30))
    row = claimed_state(
        owner,
        reconciliation,
        calculation,
        baseline_equity=Decimal("-1.250000000000000000"),
        conservative_daily_loss=Decimal("0.000000000000000000"),
        accounting_window_start_at=NOW.astimezone(offset),
        accounting_window_end_at=NOW.astimezone(offset),
    )
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        assert stored.baseline_equity == Decimal("-1.25")
        assert stored.conservative_daily_loss == Decimal("0")
        assert stored.accounting_window_start_at == NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_conservative_daily_loss_is_nonnegative(schema_db, channel):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    row = claimed_state(
        owner,
        reconciliation,
        calculation,
        conservative_daily_loss=Decimal("-0.0001"),
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("value", [None, "", " ", "\t", "x" * 129, "US\x00DT", 1, True])
async def test_accounting_symbol_is_required_bounded_exact_text(schema_db, channel, value):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    row = claimed_state(owner, reconciliation, calculation)
    row.reference_asset_symbol = value
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("value", [None, True, "A" * 64, "a" * 63, "g" * 64])
async def test_accounting_input_identity_is_lowercase_sha256(schema_db, channel, value):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    row = claimed_state(owner, reconciliation, calculation)
    row.accounting_input_set_sha256 = value
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("accounting_window_start_at", NOW + timedelta(seconds=1)),
        ("accounting_window_end_at", NOW - timedelta(microseconds=1)),
        ("updated_at", NOW - timedelta(microseconds=1)),
    ],
)
async def test_risk_state_time_order(schema_db, channel, field, value):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    row = claimed_state(owner, reconciliation, calculation)
    row.accounting_window_start_at = NOW
    row.accounting_window_end_at = NOW
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
async def test_scope_singleton_and_independent_scopes(schema_db):
    first, _ = await graph(schema_db)
    await save(schema_db, risk_state(first))
    with pytest.raises(PersistenceError):
        await save(schema_db, risk_state(first))
    second = account(profile_name="second", region="global")
    from test_coordination_schema import incarnation

    await save(schema_db, second)
    await save(schema_db, incarnation(second))
    await save(schema_db, risk_state(second))


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("parent", ["run", "pnl"])
@pytest.mark.parametrize("field", SCOPE)
async def test_risk_state_claim_parents_reject_every_alternate_valid_scope(
    schema_db, channel, parent, field
):
    _, reconciliation, calculation, _ = await full_graph(schema_db)
    other, other_reconciliation, other_calculation, _ = await full_graph(
        schema_db, owner=alternate_account(field)
    )
    row = claimed_state(other, other_reconciliation, other_calculation)
    setattr(
        row,
        "reconciliation_run_id" if parent == "run" else "pnl_snapshot_id",
        reconciliation.id if parent == "run" else calculation.id,
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("halt_set_state", "BROKEN"),
        ("reconciliation_state", "BROKEN"),
        ("reconciliation_run_version", 0),
        ("accounting_calculation_version", 0),
        ("accounting_input_set_sha256", "A" * 64),
        ("reference_asset_id", 0),
        ("reference_asset_symbol", " "),
        ("conservative_daily_loss", -1),
        (
            "accounting_window_end_at",
            (NOW - timedelta(seconds=1)).isoformat(timespec="microseconds"),
        ),
        ("created_at", (NOW + timedelta(seconds=3)).isoformat(timespec="microseconds")),
    ],
)
async def test_corrupt_risk_state_semantics_fail_closed_on_load(schema_db, field, value):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    row = claimed_state(owner, reconciliation, calculation)
    await save(schema_db, row)
    async with schema_db.engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        await connection.execute(
            text(f"UPDATE risk_state SET {field}=:value WHERE id=:id"),
            {"value": value, "id": row.id.hex},
        )
        await connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(row), row.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_version", [1, 3])
async def test_optimistic_version_reset_and_stale_writer(schema_db, manual_version):
    owner, _ = await graph(schema_db)
    row = risk_state(owner, halt_set_state="INITIALIZED")
    await save(schema_db, row)
    async with AsyncSession(schema_db.engine, expire_on_commit=False) as stale_session:
        stale = await stale_session.get(type(row), row.id)
        await stale_session.commit()
        async with schema_db.transaction() as session:
            fresh = await session.get(type(row), row.id)
            fresh.halt_revision = 1
            fresh.updated_at = NOW + timedelta(seconds=1)
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                reset = await session.get(type(row), row.id)
                reset.version = manual_version
        stale.halt_revision = 2
        stale.updated_at = NOW + timedelta(seconds=2)
        with pytest.raises(StaleDataError):
            await stale_session.commit()
        await stale_session.rollback()
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        assert (stored.version, stored.halt_revision) == (2, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("cause", CAUSES)
async def test_independent_halt_causes_and_seeded_generation(schema_db, channel, cause):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    await save(schema_db, state)
    await save(schema_db, halt(owner, state, cause), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("generation", [None, True, False, 0, -1, 1.0, "1", 2**63])
async def test_halt_generation_is_exact_positive_bigint(schema_db, channel, generation):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    await save(schema_db, state)
    row = halt(owner, state)
    row.generation = generation
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
async def test_generation_is_unique_per_cause_and_reengagement_uses_new_cycle(schema_db):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    await save(schema_db, state)
    first = halt(owner, state)
    await save(schema_db, first)
    with pytest.raises(PersistenceError):
        await save(schema_db, halt(owner, state))
    await save(schema_db, halt(owner, state, generation=2))
    await save(schema_db, halt(owner, state, cause="DAILY_LOSS", generation=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", SCOPE)
async def test_halt_requires_exact_scoped_risk_state_parent(schema_db, channel, field):
    first, _ = await graph(schema_db)
    first_state = risk_state(first, halt_set_state="INITIALIZED")
    await save(schema_db, first_state)
    second = alternate_account(field)
    from test_coordination_schema import incarnation

    await save(schema_db, second)
    await save(schema_db, incarnation(second))
    second_state = risk_state(second, halt_set_state="INITIALIZED")
    await save(schema_db, second_state)
    row = halt(second, second_state)
    row.risk_state_id = first_state.id
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "event_kind,sequence,from_state,to_state,valid",
    [
        ("ENGAGED", 1, "CLEARED", "ACTIVE", True),
        ("ENGAGED", 2, "CLEARED", "ACTIVE", False),
        ("REAFFIRMED", 2, "ACTIVE", "ACTIVE", True),
        ("REAFFIRMED", 1, "ACTIVE", "ACTIVE", False),
        ("RELEASED", 2, "ACTIVE", "CLEARED", True),
        ("RELEASED", 1, "ACTIVE", "CLEARED", False),
    ],
)
async def test_event_lifecycle_sql_shapes(
    schema_db, channel, event_kind, sequence, from_state, to_state, valid
):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    await save(schema_db, state)
    await save(schema_db, cycle)
    row = halt_event(owner, cycle)
    row.event_kind = event_kind
    row.sequence = sequence
    row.from_state = from_state
    row.to_state = to_state
    row.reason_code = "RECOVERY_COMPLETED" if event_kind == "RELEASED" else "RISK_POLICY"
    if valid:
        if channel == "orm":
            row = halt_event(
                owner,
                cycle,
                event_kind=event_kind,
                sequence=sequence,
                from_state=from_state,
                to_state=to_state,
                reason_code=row.reason_code,
            )
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["halt_id", "risk_state_id", "halt_cause", "generation"])
async def test_event_exact_cycle_identity_is_enforced(schema_db, channel, field):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    first = halt(owner, state)
    second = halt(owner, state, cause="DAILY_LOSS")
    await save(schema_db, state)
    await save(schema_db, first)
    await save(schema_db, second)
    row = halt_event(owner, first)
    setattr(
        row,
        field,
        {
            "halt_id": second.id,
            "risk_state_id": uuid4(),
            "halt_cause": "DAILY_LOSS",
            "generation": 2,
        }[field],
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["idempotency_key"])
@pytest.mark.parametrize("value", [None, "", " ", "\t", "x" * 129, "bad\x00text", 1, True])
async def test_event_identity_text_is_bounded_nonblank_and_nul_free(
    schema_db, channel, field, value
):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    await save(schema_db, state)
    await save(schema_db, cycle)
    row = halt_event(owner, cycle)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("value", [True, "A" * 64, "a" * 63, "g" * 64])
async def test_supporting_evidence_identity_is_lowercase_sha256(schema_db, channel, value):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    await save(schema_db, state)
    await save(schema_db, cycle)
    row = halt_event(owner, cycle, supporting_evidence_sha256=HASH)
    row.supporting_evidence_sha256 = value
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("reason_code", "DAILY_LOSS_LIMIT"),
        ("actor_kind", "ADMIN"),
        ("occurred_at", NOW + timedelta(seconds=6)),
        ("recorded_at", NOW + timedelta(seconds=3)),
    ],
)
async def test_event_reason_actor_and_time_sql_admission(schema_db, channel, field, value):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    await save(schema_db, state)
    await save(schema_db, cycle)
    row = halt_event(owner, cycle)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("parent", ["run", "pnl", "drift"])
@pytest.mark.parametrize("field", SCOPE)
async def test_event_optional_parent_scope_is_exact(schema_db, channel, parent, field):
    _, reconciliation, calculation, discrepancy = await full_graph(schema_db)
    owner, other_reconciliation, other_calculation, other_discrepancy = await full_graph(
        schema_db, owner=alternate_account(field)
    )
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state, cause="RECONCILIATION")
    await save(schema_db, state)
    await save(schema_db, cycle)
    references = {
        "reconciliation_run_id": other_reconciliation.id,
        "pnl_snapshot_id": other_calculation.id,
        "drift_id": other_discrepancy.id,
    }
    references[
        {"run": "reconciliation_run_id", "pnl": "pnl_snapshot_id", "drift": "drift_id"}[parent]
    ] = {"run": reconciliation.id, "pnl": calculation.id, "drift": discrepancy.id}[parent]
    row = halt_event(
        owner,
        cycle,
        **references,
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
async def test_one_release_per_cycle_and_idempotency_and_sequence_are_unique(schema_db):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    await save(schema_db, state)
    await save(schema_db, cycle)
    engagement = halt_event(owner, cycle, idempotency_key="engage")
    release = released(owner, cycle, idempotency_key="release")
    await save(schema_db, engagement)
    await save(schema_db, release)
    with pytest.raises(PersistenceError):
        await save(schema_db, released(owner, cycle, sequence=3, idempotency_key="release-2"))
    with pytest.raises(PersistenceError):
        await save(schema_db, halt_event(owner, cycle, idempotency_key="engage-2"))
    with pytest.raises(PersistenceError):
        await save(
            schema_db,
            halt_event(
                owner,
                cycle,
                sequence=3,
                event_kind="REAFFIRMED",
                from_state="ACTIVE",
                to_state="ACTIVE",
                idempotency_key="release",
            ),
        )


@pytest.mark.asyncio
async def test_post_release_history_order_is_explicit_repository_trigger_boundary(schema_db):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    await save(schema_db, state)
    await save(schema_db, cycle)
    await save(schema_db, halt_event(owner, cycle))
    await save(schema_db, released(owner, cycle))
    # SQLite static checks cannot inspect prior rows. Task 2.1f must reject this
    # raw-history shape through PostgreSQL sequencing/history triggers.
    await save(
        schema_db,
        halt_event(
            owner,
            cycle,
            sequence=3,
            event_kind="REAFFIRMED",
            from_state="ACTIVE",
            to_state="ACTIVE",
        ),
        "core",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name", ["halt", "event"])
@pytest.mark.parametrize("operation", ["update", "delete", "expired_update", "deferred_delete"])
async def test_halt_and_event_orm_mutation_is_denied(schema_db, model_name, operation):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    await save(schema_db, state)
    await save(schema_db, cycle)
    row = cycle
    if model_name == "event":
        row = halt_event(owner, cycle)
        await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = (
                (await session.scalars(select(type(row)).options(load_only(type(row).id)))).one()
                if operation == "deferred_delete"
                else await session.get(type(row), row.id)
            )
            if operation in {"delete", "deferred_delete"}:
                await session.delete(loaded)
            else:
                if operation == "expired_update":
                    session.expire(loaded)
                if model_name == "event":
                    loaded.recorded_at = loaded.recorded_at + timedelta(seconds=1)
                else:
                    loaded.created_at = loaded.created_at + timedelta(seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("halt_cause", "BROKEN"), ("generation", 0)])
@pytest.mark.parametrize("operation", ["load", "deferred", "refresh", "partial", "expired"])
async def test_corrupt_halt_header_fails_on_every_orm_load_path(schema_db, field, value, operation):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    await save(schema_db, state)
    await save(schema_db, cycle)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = None
            if operation in {"refresh", "partial", "expired"}:
                loaded = await session.get(type(cycle), cycle.id)
                session.expire(loaded, ["halt_cause", "generation"])
            await session.execute(text("PRAGMA ignore_check_constraints=ON"))
            await session.execute(
                text(f"UPDATE risk_halts SET {field}=:value WHERE id=:id"),
                {"value": value, "id": cycle.id.hex},
            )
            await session.execute(text("PRAGMA ignore_check_constraints=OFF"))
            if operation == "load":
                await session.get(type(cycle), cycle.id)
            elif operation == "deferred":
                (
                    await session.scalars(select(type(cycle)).options(load_only(type(cycle).id)))
                ).all()
            elif operation == "expired":
                await session.run_sync(lambda _: loaded.halt_cause)
            else:
                await session.refresh(
                    loaded,
                    attribute_names=["created_at"] if operation == "partial" else None,
                )


@pytest.mark.asyncio
async def test_halt_partial_refresh_preserves_pending_risk_state_without_recursive_load(schema_db):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    await save(schema_db, state)
    await save(schema_db, cycle)
    async with schema_db.transaction() as session:
        loaded_state = await session.get(type(state), state.id)
        loaded_cycle = await session.get(type(cycle), cycle.id)
        session.expire(loaded_cycle, ["halt_cause", "generation"])
        loaded_state.halt_revision = 1
        loaded_state.updated_at = NOW + timedelta(seconds=1)
        with session.no_autoflush:
            await session.refresh(loaded_cycle, attribute_names=["created_at"])
        assert (loaded_state.halt_revision, loaded_state.version) == (1, 1)
        assert "halt_cause" not in loaded_cycle.__dict__
        assert "generation" not in loaded_cycle.__dict__
    async with schema_db.transaction() as session:
        stored = await session.get(type(state), state.id)
        assert (stored.halt_revision, stored.version) == (1, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["load", "deferred", "refresh", "partial", "expired"])
async def test_corrupt_event_digest_fails_on_every_orm_load_path(schema_db, operation):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    row = halt_event(owner, cycle)
    await save(schema_db, state)
    await save(schema_db, cycle)
    await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = None
            if operation in {"refresh", "partial", "expired"}:
                loaded = await session.get(type(row), row.id)
                session.expire(loaded, ["evidence_sha256"])
            await session.execute(
                text("UPDATE risk_halt_events SET evidence_sha256=:v WHERE id=:id"),
                {"v": "c" * 64, "id": row.id.hex},
            )
            if operation == "load":
                await session.get(type(row), row.id)
            elif operation == "deferred":
                (await session.scalars(select(type(row)).options(load_only(type(row).id)))).all()
            elif operation == "expired":
                await session.run_sync(lambda _: loaded.evidence_sha256)
            else:
                await session.refresh(
                    loaded,
                    attribute_names=["recorded_at"] if operation == "partial" else None,
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["load", "deferred", "refresh", "partial", "expired"])
async def test_corrupt_event_time_order_fails_on_every_orm_load_path(schema_db, operation):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    row = halt_event(owner, cycle)
    await save(schema_db, state)
    await save(schema_db, cycle)
    await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = None
            if operation in {"refresh", "partial", "expired"}:
                loaded = await session.get(type(row), row.id)
                session.expire(loaded, ["occurred_at", "recorded_at"])
            await session.execute(text("PRAGMA ignore_check_constraints=ON"))
            await session.execute(
                text("UPDATE risk_halt_events SET occurred_at=:value WHERE id=:id"),
                {
                    "value": (NOW + timedelta(seconds=6)).isoformat(timespec="microseconds"),
                    "id": row.id.hex,
                },
            )
            await session.execute(text("PRAGMA ignore_check_constraints=OFF"))
            if operation == "load":
                await session.get(type(row), row.id)
            elif operation == "deferred":
                (await session.scalars(select(type(row)).options(load_only(type(row).id)))).all()
            elif operation == "expired":
                await session.run_sync(lambda _: loaded.occurred_at)
            else:
                await session.refresh(
                    loaded,
                    attribute_names=["idempotency_key"] if operation == "partial" else None,
                )


@pytest.mark.asyncio
async def test_core_cross_column_and_append_only_boundaries_are_explicit(schema_db):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    row = halt_event(owner, cycle)
    await save(schema_db, state)
    await save(schema_db, cycle)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        await session.execute(
            row.__table__.update()
            .where(row.__table__.c.id == row.id)
            .values(evidence_sha256="c" * 64)
        )
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(row), row.id)
    async with schema_db.transaction() as session:
        await session.execute(delete(row.__table__).where(row.__table__.c.id == row.id))


@pytest.mark.asyncio
async def test_claims_can_coexist_with_active_halt_without_becoming_authority(schema_db):
    owner, reconciliation, calculation, _ = await full_graph(schema_db)
    state = claimed_state(owner, reconciliation, calculation)
    cycle = halt(owner, state, cause="GLOBAL_KILL")
    event = halt_event(owner, cycle)
    await save(schema_db, state)
    await save(schema_db, cycle)
    await save(schema_db, event)
    forbidden = {"authorized", "ready", "can_dispatch", "write_enabled", "production_enabled"}
    for model in (type(state), type(cycle), type(event)):
        assert forbidden.isdisjoint(model.__table__.c.keys())
        assert not any(
            name.startswith(("authorize", "dispatch", "submit", "place")) for name in vars(model)
        )


@pytest.mark.asyncio
async def test_release_of_global_kill_leaves_other_causes_and_parents_unchanged(schema_db):
    owner, reconciliation, calculation, discrepancy = await full_graph(schema_db)
    state = claimed_state(owner, reconciliation, calculation)
    global_cycle = halt(owner, state, cause="GLOBAL_KILL")
    daily_cycle = halt(owner, state, cause="DAILY_LOSS")
    reconciliation_cycle = halt(owner, state, cause="RECONCILIATION")
    await save(schema_db, state)
    for cycle in (global_cycle, daily_cycle, reconciliation_cycle):
        await save(schema_db, cycle)
        await save(schema_db, halt_event(owner, cycle))
    async with schema_db.transaction() as session:
        before = {
            "daily": (await session.execute(select(type(daily_cycle).__table__))).all(),
            "run": (await session.execute(select(type(reconciliation).__table__))).all(),
            "pnl": (await session.execute(select(type(calculation).__table__))).all(),
            "drift": (await session.execute(select(type(discrepancy).__table__))).all(),
        }
    await save(schema_db, released(owner, global_cycle))
    async with schema_db.transaction() as session:
        assert before == {
            "daily": (await session.execute(select(type(daily_cycle).__table__))).all(),
            "run": (await session.execute(select(type(reconciliation).__table__))).all(),
            "pnl": (await session.execute(select(type(calculation).__table__))).all(),
            "drift": (await session.execute(select(type(discrepancy).__table__))).all(),
        }


@pytest.mark.asyncio
async def test_populated_tables_drop_and_recreate_children_first(schema_db):
    owner, _ = await graph(schema_db)
    state = risk_state(owner, halt_set_state="INITIALIZED")
    cycle = halt(owner, state)
    event = halt_event(owner, cycle)
    await save(schema_db, state)
    await save(schema_db, cycle)
    await save(schema_db, event)
    api = schema_api()
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(api.RiskHaltEvent.__table__.drop)
        await connection.run_sync(api.RiskHalt.__table__.drop)
        await connection.run_sync(api.RiskState.__table__.drop)
        await connection.run_sync(api.RiskState.__table__.create)
        await connection.run_sync(api.RiskHalt.__table__.create)
        await connection.run_sync(api.RiskHaltEvent.__table__.create)
    async with schema_db.transaction() as session:
        assert (await session.execute(select(api.RiskState))).all() == []
        assert (await session.execute(select(api.RiskHalt))).all() == []
        assert (await session.execute(select(api.RiskHaltEvent))).all() == []


def test_import_has_no_engine_tasks_files_network_orders_or_capability(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, socket, sqlalchemy, sqlalchemy.ext.asyncio
def forbidden(*a, **kw):
    raise AssertionError('import side effect')
socket.socket = socket.create_connection = forbidden
sqlalchemy.create_engine = sqlalchemy.ext.asyncio.create_async_engine = forbidden
asyncio.create_task = asyncio.run = forbidden
from hmt_crypto.persistence.models import RiskState, RiskHalt, RiskHaltEvent
assert RiskState.__tablename__ == 'risk_state'
assert RiskHalt.__tablename__ == 'risk_halts'
assert RiskHaltEvent.__tablename__ == 'risk_halt_events'
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
