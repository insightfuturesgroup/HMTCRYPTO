"""Offline projection contracts; no scheduler, heartbeat loop or fencing protocol."""

import subprocess
import sys
from datetime import timedelta, timezone
from itertools import product
from uuid import uuid4

import pytest
from execution_test_support import execution_db as execution_db
from schema_test_support import NOW, account, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import UniqueConstraint, delete, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateIndex, CreateTable
from test_coordination_schema import BASELINE, SCOPE, graph, held, incarnation, lease, save

from hmt_crypto.persistence.errors import PersistenceError

ADDED = {"worker_heartbeats", "scheduler_state"}
WORKER_FENCE = (
    "observed_recovery_incarnation_id",
    "observed_lease_id",
    "observed_leadership_generation",
)
CLAIM_FENCE = ("claim_recovery_incarnation_id", "claim_lease_id", "claim_leadership_generation")
CLAIM = ("claim_owner_id", "claimed_at", "claim_expires_at")


def projection(kind, owner, **changes):
    api = schema_api()
    name = "WorkerHeartbeat" if kind == "worker" else "SchedulerState"
    assert hasattr(api, name), f"Missing {name} projection export"
    values = dict(id=uuid4(), **scope(owner), version=1)
    if kind == "worker":
        values.update(started_at=NOW, last_seen_at=NOW, health_state="HEALTHY")
    else:
        values.update(
            job_key="reconcile-local",
            state="IDLE",
            claim_generation=0,
            next_eligible_at=NOW - timedelta(days=1),
            created_at=NOW,
            updated_at=NOW,
        )
    values.update(changes)
    return getattr(api, name)(**values)


def claimed(worker, **changes):
    values = dict(
        state="CLAIMED",
        claim_owner_id=worker.id,
        claimed_at=NOW,
        claim_expires_at=NOW + timedelta(seconds=1),
        claim_generation=1,
        updated_at=NOW + timedelta(days=1),
    )
    values.update(changes)
    return values


async def populated(db):
    owner, recovery = await graph(db)
    leader = lease(owner, recovery, **held(generation=7))
    await save(db, leader)
    worker = projection("worker", owner)
    await save(db, worker)
    return owner, recovery, leader, worker


def evidence(recovery, leader, fields):
    return dict(zip(fields, (recovery.id, leader.id, 7), strict=True))


def test_exact_two_added_tables_exports_and_acyclic_stable_scoped_keys():
    api = schema_api()
    assert {t.name for t in api.metadata.tables.values()} == BASELINE | {
        "recovery_incarnations",
        "leadership_leases",
        *ADDED,
        "reconciliation_runs",
        "reconciliation_resources",
        "reconciliation_drifts",
        "reconciliation_acknowledgements",
        "position_snapshots",
        "balance_snapshots",
        "wallet_transactions",
        "funding_snapshots",
        "pnl_snapshots",
        "risk_state",
        "risk_halts",
        "risk_halt_events",
        "risk_decisions",
        "risk_reservations",
    }
    expected = {
        "WorkerHeartbeat": {
            "id",
            *SCOPE,
            "started_at",
            "last_seen_at",
            "health_state",
            *WORKER_FENCE,
            "version",
        },
        "SchedulerState": {
            "id",
            *SCOPE,
            "job_key",
            "state",
            *CLAIM,
            *CLAIM_FENCE,
            "claim_generation",
            "last_started_at",
            "last_success_at",
            "last_failure_at",
            "last_failure_code",
            "next_eligible_at",
            "version",
            "created_at",
            "updated_at",
        },
    }
    for name, columns in expected.items():
        assert hasattr(api, name), f"Missing {name} projection export"
        table = getattr(api, name).__table__
        assert set(table.c.keys()) == columns
        keys = {
            tuple(c.name for c in k.columns)
            for k in table.constraints
            if isinstance(k, UniqueConstraint)
        }
        assert ("id", *SCOPE) in keys
        parents = {fk.referred_table.name for fk in table.foreign_key_constraints}
        assert parents == {"accounts", "recovery_incarnations", "leadership_leases"} | (
            {"worker_heartbeats"} if name == "SchedulerState" else set()
        )
        prefix = "observed" if name == "WorkerHeartbeat" else "claim"
        expected_sources = {
            "accounts": SCOPE,
            "recovery_incarnations": (f"{prefix}_recovery_incarnation_id", *SCOPE),
            "leadership_leases": (f"{prefix}_lease_id", *SCOPE),
            "worker_heartbeats": ("claim_owner_id", *SCOPE),
        }
        for fk in table.foreign_key_constraints:
            assert fk.name and fk.ondelete == fk.onupdate == "RESTRICT"
            assert tuple(fk.column_keys) == expected_sources[fk.referred_table.name]
            assert tuple(e.column.name for e in fk.elements) == (
                ("id", *SCOPE) if fk.referred_table.name != "accounts" else ("id", *SCOPE[1:])
            )
            if fk.referred_table.name != "accounts":
                assert tuple(fk.column_keys) in {
                    tuple(c.name for c in index.columns) for index in table.indexes
                }
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "UUID" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl
        for constraint in table.constraints:
            assert constraint.name and constraint.name in ddl
        for index in table.indexes:
            assert index.name in str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    for table in api.metadata.tables.values():
        if table.name not in ADDED | {"reconciliation_runs"}:
            assert not ADDED & {fk.referred_table.name for fk in table.foreign_key_constraints}
    scheduler = api.SchedulerState.__table__
    assert (*SCOPE, "job_key") in {
        tuple(c.name for c in k.columns)
        for k in scheduler.constraints
        if isinstance(k, UniqueConstraint)
    }
    assert (*SCOPE, "state", "next_eligible_at") in {
        tuple(c.name for c in i.columns) for i in scheduler.indexes
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_health_without_authority_and_claim_release_preserves_history(schema_db, channel):
    owner, recovery, leader, worker = await populated(schema_db)
    second = projection("worker", owner, health_state="STOPPED")
    await save(schema_db, second, channel)
    row = projection(
        "scheduler",
        owner,
        **claimed(worker),
        **evidence(recovery, leader, CLAIM_FENCE),
        last_started_at=NOW + timedelta(hours=2),
        last_success_at=NOW,
        last_failure_at=NOW + timedelta(hours=1),
        last_failure_code="CANCELLED",
    )
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.claim_expires_at < loaded.updated_at  # Expired remains evidence.
        assert loaded.next_eligible_at < loaded.created_at
        loaded.state = "IDLE"
        for field in (*CLAIM, *CLAIM_FENCE):
            setattr(loaded, field, None)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.claim_generation == 1 and loaded.last_failure_code == "CANCELLED"
        assert loaded.last_started_at > loaded.last_failure_at > loaded.last_success_at
        assert (await session.get(type(worker), worker.id)).observed_lease_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("state", ["IDLE", "CLAIMED"])
@pytest.mark.parametrize("present", list(product([False, True], repeat=3)))
async def test_claim_owner_time_null_group(schema_db, channel, state, present):
    owner, _, _, worker = await populated(schema_db)
    changes = claimed(worker, state=state)
    for field, exists in zip(CLAIM, present, strict=True):
        if not exists:
            changes[field] = None
    row = projection("scheduler", owner, **changes)
    if present == ((True, True, True) if state == "CLAIMED" else (False, False, False)):
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "kind,state", [("worker", None), ("scheduler", "IDLE"), ("scheduler", "CLAIMED")]
)
@pytest.mark.parametrize("present", list(product([False, True], repeat=3)))
async def test_fence_trios_have_no_sql_null_loopholes(schema_db, channel, kind, state, present):
    owner, recovery, leader, worker = await populated(schema_db)
    fields = WORKER_FENCE if kind == "worker" else CLAIM_FENCE
    changes = evidence(recovery, leader, fields)
    for field, exists in zip(fields, present, strict=True):
        if not exists:
            changes[field] = None
    if kind == "scheduler":
        changes.update(claimed(worker) if state == "CLAIMED" else {})
    row = projection(kind, owner, **changes)
    valid = not any(present) or (all(present) and state != "IDLE")
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("present", list(product([False, True], repeat=2)))
async def test_failure_time_and_safe_code_are_exact_pair(schema_db, channel, present):
    owner, _ = await graph(schema_db)
    row = projection(
        "scheduler",
        owner,
        last_failure_at=NOW if present[0] else None,
        last_failure_code="FAILED" if present[1] else None,
    )
    if present[0] == present[1]:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


INVALID = (
    [("worker", {"health_state": v}) for v in (None, "READY", "HEALTHY\x00tail")]
    + [("worker", {"last_seen_at": NOW - timedelta(seconds=1)})]
    + [("worker", {"observed_leadership_generation": v}) for v in (0, -1)]
    + [("scheduler", {"state": v}) for v in (None, "RUNNING")]
    + [("scheduler", {"job_key": v}) for v in (None, "", " ", "x" * 129, "x\x00tail")]
    + [
        ("scheduler", {"last_failure_at": NOW, "last_failure_code": v})
        for v in ("ERROR: secret", "", "RETRY")
    ]
    + [
        ("scheduler", {field: value})
        for field in ("last_started_at", "last_success_at", "last_failure_at")
        for value in (NOW - timedelta(seconds=1), NOW + timedelta(seconds=1))
    ]
    + [
        ("scheduler", {"updated_at": NOW - timedelta(seconds=1)}),
        ("scheduler", {"claim_generation": -1}),
    ]
)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("kind,changes", INVALID)
async def test_invalid_states_keys_counts_and_time_bounds(schema_db, channel, kind, changes):
    owner, _ = await graph(schema_db)
    if "last_failure_at" in changes and "last_failure_code" not in changes:
        changes = {**changes, "last_failure_code": "UNKNOWN"}
    with pytest.raises(PersistenceError):
        await save(schema_db, projection(kind, owner, **changes), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "changes",
    [
        {"claim_generation": 0},
        {"claimed_at": NOW - timedelta(seconds=1)},
        {"claimed_at": NOW + timedelta(days=2)},
        {"claim_expires_at": NOW},
        {"claim_expires_at": NOW - timedelta(seconds=1)},
        {"claim_leadership_generation": 0},
    ],
)
async def test_claim_time_and_positive_generations(schema_db, channel, changes):
    owner, recovery, leader, worker = await populated(schema_db)
    values = {**claimed(worker), **evidence(recovery, leader, CLAIM_FENCE), **changes}
    with pytest.raises(PersistenceError):
        await save(schema_db, projection("scheduler", owner, **values), channel)


INTEGER_FIELDS = [
    ("worker", "version"),
    ("worker", WORKER_FENCE[2]),
    ("scheduler", "version"),
    ("scheduler", "claim_generation"),
    ("scheduler", CLAIM_FENCE[2]),
]
TIME_FIELDS = [("worker", "started_at"), ("worker", "last_seen_at")] + [
    ("scheduler", f)
    for f in (
        "created_at",
        "updated_at",
        "next_eligible_at",
        "claimed_at",
        "claim_expires_at",
        "last_started_at",
        "last_success_at",
        "last_failure_at",
    )
]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("kind,field", INTEGER_FIELDS)
@pytest.mark.parametrize("value", [True, 1.0, "1", 2**63, -(2**63) - 1, None])
async def test_integer_bind_boundaries(schema_db, channel, kind, field, value):
    owner, recovery, leader, worker = await populated(schema_db)
    values = evidence(recovery, leader, WORKER_FENCE if kind == "worker" else CLAIM_FENCE)
    if kind == "scheduler":
        values.update(claimed(worker))
    values[field] = value
    with pytest.raises(PersistenceError):
        await save(schema_db, projection(kind, owner, **values), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("kind,field", TIME_FIELDS)
@pytest.mark.parametrize("value", [NOW.replace(tzinfo=None), "2026-11-04"])
async def test_utc_binds_reject_naive_and_non_datetime(schema_db, channel, kind, field, value):
    owner, _, _, worker = await populated(schema_db)
    values = claimed(worker) if kind == "scheduler" else {}
    values[field] = value
    with pytest.raises(PersistenceError):
        await save(schema_db, projection(kind, owner, **values), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "kind,field",
    [
        ("worker", "started_at"),
        ("worker", "last_seen_at"),
        ("scheduler", "created_at"),
        ("scheduler", "updated_at"),
        ("scheduler", "next_eligible_at"),
    ],
)
async def test_required_timestamps_cannot_be_null(schema_db, channel, kind, field):
    owner, _ = await graph(schema_db)
    with pytest.raises(PersistenceError):
        await save(schema_db, projection(kind, owner, **{field: None}), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "kind,field", [("worker", "version"), ("scheduler", "version"), ("worker", WORKER_FENCE[2])]
)
@pytest.mark.parametrize("value", [0, -1])
async def test_initial_positive_versions_and_observed_generation(
    schema_db, channel, kind, field, value
):
    owner, recovery, leader, _ = await populated(schema_db)
    values = evidence(recovery, leader, WORKER_FENCE) if kind == "worker" else {}
    values[field] = value
    with pytest.raises(PersistenceError):
        await save(schema_db, projection(kind, owner, **values), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["worker", "scheduler"])
@pytest.mark.parametrize("value", [True, 1.0])
async def test_equal_noninteger_version_edit_alone_is_rejected(schema_db, kind, value):
    owner, _ = await graph(schema_db)
    row = projection(kind, owner)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            (await session.get(type(row), row.id)).version = value


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_maximum_logical_key_and_stopped_worker_are_mutable(schema_db, channel):
    owner, _ = await graph(schema_db)
    worker = projection("worker", owner, health_state="STOPPED")
    await save(schema_db, worker, channel)
    await save(schema_db, projection("scheduler", owner, job_key="x" * 128), channel)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(worker), worker.id)
        loaded.health_state = "HEALTHY"
        loaded.last_seen_at = NOW + timedelta(days=1)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(worker), worker.id)
        assert loaded.health_state == "HEALTHY" and loaded.version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("kind,field", TIME_FIELDS + INTEGER_FIELDS)
async def test_corrupt_result_adapters_fail_closed(schema_db, channel, kind, field):
    owner, _, _, worker = await populated(schema_db)
    row = projection(kind, owner, **(claimed(worker) if kind == "scheduler" else {}))
    await save(schema_db, row)
    value = 1.5 if (kind, field) in INTEGER_FIELDS else "2026-11-04T00:00:00.000000+01:00"
    async with schema_db.engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        await connection.execute(
            text(f"UPDATE {row.__tablename__} SET {field}=:v WHERE id=:id"),
            {"v": value, "id": row.id.hex},
        )
        await connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            if channel == "orm":
                await session.get(type(row), row.id)
            else:
                (await session.execute(select(row.__table__))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["worker", "scheduler"])
@pytest.mark.parametrize("value", [True, 1.0, False, 0, -1, "1", 2**63, None])
async def test_version_edits_cannot_hide_behind_equal_numeric_history(schema_db, kind, value):
    owner, _ = await graph(schema_db)
    row = projection(kind, owner)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            loaded.version = value
            # Force a legitimate projection change alongside numerically equal edits.
            setattr(
                loaded,
                "health_state" if kind == "worker" else "claim_generation",
                "DEGRADED" if kind == "worker" else 3,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["worker", "scheduler"])
@pytest.mark.parametrize("manual", [1, 3])
async def test_version_advance_reset_and_stale_writer(schema_db, kind, manual):
    owner, _ = await graph(schema_db)
    row = projection(kind, owner)
    await save(schema_db, row)
    field, fresh_value, stale_value = (
        ("health_state", "DEGRADED", "STOPPED") if kind == "worker" else ("claim_generation", 3, 9)
    )
    async with AsyncSession(schema_db.engine, expire_on_commit=False) as stale_session:
        stale = await stale_session.get(type(row), row.id)
        await stale_session.commit()
        async with schema_db.transaction() as session:
            fresh = await session.get(type(row), row.id)
            setattr(fresh, field, fresh_value)
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                loaded = await session.get(type(row), row.id)
                assert loaded.version == 2
                loaded.version = manual
        setattr(stale, field, stale_value)
        with pytest.raises(StaleDataError):
            await stale_session.commit()
        await stale_session.rollback()
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.version == 2 and getattr(loaded, field) == fresh_value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,field,value",
    [
        (kind, field, value)
        for kind in ("worker", "scheduler")
        for field, value in (
            ("id", uuid4()),
            ("account_id", uuid4()),
            ("region", "global"),
            ("environment", "prod"),
            ("mode", "LIVE"),
        )
    ]
    + [
        ("worker", "started_at", NOW - timedelta(seconds=1)),
        ("scheduler", "created_at", NOW - timedelta(seconds=1)),
        ("scheduler", "job_key", "other"),
    ],
)
async def test_orm_identity_immutability(schema_db, kind, field, value):
    owner, _ = await graph(schema_db)
    row = projection(kind, owner)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            setattr(loaded, field, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "kind,reference",
    [
        ("worker", None),
        ("worker", WORKER_FENCE[0]),
        ("worker", WORKER_FENCE[1]),
        ("scheduler", None),
        ("scheduler", CLAIM[0]),
        ("scheduler", CLAIM_FENCE[0]),
        ("scheduler", CLAIM_FENCE[1]),
    ],
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", uuid4()),
        ("region", "global"),
        ("environment", "prod"),
        ("mode", "LIVE"),
        ("account_id", None),
        ("region", None),
        ("environment", None),
        ("mode", None),
    ],
)
async def test_each_fk_complete_scope_isolated(schema_db, channel, kind, reference, field, value):
    owner, recovery, leader, worker = await populated(schema_db)
    fields = WORKER_FENCE if kind == "worker" else CLAIM_FENCE
    # For reference FKs, keep all other references in the new, valid scope.
    if reference is not None and value is not None:
        other = account(
            profile_name="other", **({"id": value} if field == "account_id" else {field: value})
        )
        await save(schema_db, other)
        other_recovery = incarnation(other)
        await save(schema_db, other_recovery)
        other_leader = lease(other, other_recovery)
        await save(schema_db, other_leader)
        other_worker = projection("worker", other)
        await save(schema_db, other_worker)
        values = evidence(other_recovery, other_leader, fields)
        if kind == "scheduler":
            values.update(claimed(other_worker))
        values[reference] = {fields[0]: recovery.id, fields[1]: leader.id, CLAIM[0]: worker.id}[
            reference
        ]
        row = projection(kind, other, **values)
    else:
        values = evidence(recovery, leader, fields)
        if kind == "scheduler":
            values.update(claimed(worker))
        row = projection(kind, owner, **values)
        setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_same_job_other_scope_and_distinct_worker_instances(schema_db, channel):
    owner, _, _, worker = await populated(schema_db)
    await save(schema_db, projection("worker", owner), channel)
    await save(schema_db, projection("scheduler", owner), channel)
    with pytest.raises(PersistenceError):
        await save(schema_db, projection("scheduler", owner), channel)
    with pytest.raises(PersistenceError):
        await save(schema_db, projection("worker", owner, id=worker.id), channel)
    other = account(profile_name="other")
    await save(schema_db, other)
    await save(schema_db, projection("scheduler", other), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,reference",
    [("worker", f) for f in WORKER_FENCE[:2]]
    + [("scheduler", f) for f in (CLAIM[0], *CLAIM_FENCE[:2])],
)
async def test_dangling_stable_identity_rejected(schema_db, kind, reference):
    owner, recovery, leader, worker = await populated(schema_db)
    values = evidence(recovery, leader, WORKER_FENCE if kind == "worker" else CLAIM_FENCE)
    if kind == "scheduler":
        values.update(claimed(worker))
    values[reference] = uuid4()
    with pytest.raises(PersistenceError):
        await save(schema_db, projection(kind, owner, **values), "core")


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["account", "recovery", "lease", "worker"])
@pytest.mark.parametrize("operation", ["delete", "update"])
async def test_stable_parent_keys_restrict_mutation(schema_db, target, operation):
    owner, recovery, leader, worker = await populated(schema_db)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(worker), worker.id)
        for key, value in evidence(recovery, leader, WORKER_FENCE).items():
            setattr(loaded, key, value)
    row = projection(
        "scheduler", owner, **claimed(worker), **evidence(recovery, leader, CLAIM_FENCE)
    )
    await save(schema_db, row)
    parent = {"account": owner, "recovery": recovery, "lease": leader, "worker": worker}[target]
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            stmt = (
                delete(type(parent))
                if operation == "delete"
                else update(type(parent)).values(id=uuid4())
            )
            await session.execute(stmt.where(type(parent).id == parent.id))


@pytest.mark.asyncio
async def test_historical_observations_survive_lease_takeover_and_restore(schema_db):
    owner, recovery, leader, worker = await populated(schema_db)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(worker), worker.id)
        for key, value in evidence(recovery, leader, WORKER_FENCE).items():
            setattr(loaded, key, value)
    row = projection(
        "scheduler", owner, **claimed(worker), **evidence(recovery, leader, CLAIM_FENCE)
    )
    await save(schema_db, row)
    restored = incarnation(owner, reason="RESTORE")
    await save(schema_db, restored)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(leader), leader.id)
        loaded.recovery_incarnation_id, loaded.owner_id, loaded.generation = restored.id, uuid4(), 1
    async with schema_db.transaction() as session:
        observed = await session.get(type(worker), worker.id)
        claim = await session.get(type(row), row.id)
        assert (
            observed.observed_recovery_incarnation_id
            == claim.claim_recovery_incarnation_id
            == recovery.id
        )
        assert observed.observed_leadership_generation == claim.claim_leadership_generation == 7
        observed.health_state = "UNKNOWN"
        claim.last_success_at = NOW


@pytest.mark.asyncio
async def test_file_backed_populated_drop_recreate_and_transaction_rollback(schema_db):
    owner, _, _, worker = await populated(schema_db)
    row = projection("scheduler", owner)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(projection("worker", owner))
            await session.flush()
            (await session.get(type(row), row.id)).state = "CLAIMED"
    async with schema_db.transaction() as session:
        assert len((await session.scalars(select(type(worker)))).all()) == 1
        assert (await session.get(type(row), row.id)).state == "IDLE"
        assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
    tables = [type(worker).__table__, type(row).__table__]
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(lambda sync: schema_api().metadata.drop_all(sync, tables=tables))
        await connection.run_sync(
            lambda sync: schema_api().metadata.create_all(sync, tables=tables)
        )
    await save(schema_db, worker, "core")
    await save(schema_db, row, "core")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["worker", "scheduler"])
async def test_aware_microseconds_and_missing_initial_version(schema_db, kind):
    owner, _ = await graph(schema_db)
    instant = NOW.replace(microsecond=123456).astimezone(timezone(timedelta(hours=5, minutes=30)))
    changes = (
        dict(started_at=instant, last_seen_at=instant)
        if kind == "worker"
        else dict(created_at=instant, updated_at=instant)
    )
    row = projection(kind, owner, **changes)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        stamp = loaded.started_at if kind == "worker" else loaded.created_at
        assert stamp == NOW.replace(microsecond=123456) and stamp.utcoffset() == timedelta(0)
    missing = projection(kind, owner, **({"job_key": "other"} if kind == "scheduler" else {}))
    del missing.version
    with pytest.raises(PersistenceError):
        await save(schema_db, missing)


@pytest.mark.asyncio
async def test_projection_mutations_leave_execution_and_protection_truth_unchanged(execution_db):
    db, owner, *_ = execution_db
    tables = [t for t in schema_api().metadata.tables.values() if t.name in BASELINE]
    async with db.transaction() as session:
        before = {t.name: (await session.execute(select(t))).all() for t in tables}
    worker = projection("worker", owner)
    await save(db, worker)
    row = projection("scheduler", owner, **claimed(worker))
    await save(db, row)
    async with db.transaction() as session:
        (await session.get(type(worker), worker.id)).health_state = "STOPPED"
        (await session.get(type(row), row.id)).last_failure_at = NOW
        (await session.get(type(row), row.id)).last_failure_code = "UNKNOWN"
    async with db.transaction() as session:
        assert before == {t.name: (await session.execute(select(t))).all() for t in tables}
    for name, prefix in [
        ("CommandOperation", ""),
        ("OrderAttempt", "original_"),
        ("OrderAttemptEvent", "ingester_"),
        ("OrderTransition", ""),
    ]:
        table = getattr(schema_api(), name).__table__
        for kind in ("leader", "recovery"):
            assert not table.c[f"{prefix}{kind}_ref"].foreign_keys
        assert not ADDED & {fk.referred_table.name for fk in table.foreign_key_constraints}


def test_imports_create_no_engine_network_runtime_or_files(tmp_path):
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
from hmt_crypto.persistence.models import WorkerHeartbeat, SchedulerState
assert WorkerHeartbeat.__tablename__ == 'worker_heartbeats'
assert SchedulerState.__tablename__ == 'scheduler_state'
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
