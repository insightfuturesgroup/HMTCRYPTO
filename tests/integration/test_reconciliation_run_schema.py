"""Offline run-root admission; COMPLETE is a service claim, never authority.

SQLite and compiled PostgreSQL DDL prove structure/model boundaries only. Core
and raw SQL bypass ORM history/freeze guards; real PG enforcement is deferred.
"""

import subprocess
import sys
from datetime import timedelta, timezone
from itertools import product
from uuid import uuid4

import pytest
from execution_test_support import command
from execution_test_support import execution_db as execution_db
from schema_test_support import HASH, NOW, account, credential, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import UniqueConstraint, delete, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateIndex, CreateTable
from test_coordination_schema import BASELINE, SCOPE, graph, held, incarnation, lease, save
from test_scheduler_heartbeat_schema import projection

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError

STATES = ("PENDING", "COLLECTING", "FINISHED", "FAILED", "CANCELLED")
COMPLETENESS = ("UNKNOWN", "PARTIAL", "COMPLETE")
CODES = (
    "REQUEST_FAILED",
    "INVALID_RESPONSE",
    "PAGINATION_INCOMPLETE",
    "LIMIT_REACHED",
    "CANCELLED",
    "UNKNOWN",
)
REFERENCES = ("command_id", "recovery_incarnation_id", "owner_id", "lease_id")
INTEGERS = ("credential_version", "leadership_generation", "requirements_version", "version")
TIMES = ("created_at", "updated_at", "started_at", "finished_at")


def run(owner, recovery, **changes):
    api = schema_api()
    assert hasattr(api, "ReconciliationRun"), "Missing ReconciliationRun export"
    values = dict(
        id=uuid4(),
        **scope(owner),
        trigger="STARTUP",
        backend="SIMULATOR",
        recovery_incarnation_id=recovery.id,
        requirements_version=1,
        requirements_sha256=HASH,
        state="PENDING",
        completeness="UNKNOWN",
        version=1,
        created_at=NOW,
        updated_at=NOW + timedelta(seconds=2),
    )
    values.update(changes)
    return api.ReconciliationRun(**values)


def outcome(state="FINISHED", **changes):
    values = dict(
        state=state,
        completeness="PARTIAL",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        failure_code="CANCELLED"
        if state == "CANCELLED"
        else ("REQUEST_FAILED" if state == "FAILED" else None),
    )
    values.update(changes)
    return values


async def parents(db, owner=None):
    if owner is None:
        owner, recovery = await graph(db)
    else:
        recovery = incarnation(owner)
        await save(db, recovery)
    cred = credential(owner)
    cmd = command(owner)
    leader = lease(owner, recovery, **held(generation=7))
    worker = projection("worker", owner)
    for row in (cred, cmd, leader, worker):
        await save(db, row)
    return owner, recovery, cred, cmd, leader, worker


def provenance(cred, cmd, leader, worker):
    return dict(
        backend="VENUE",
        credential_version=cred.credential_version,
        command_id=cmd.id,
        owner_id=worker.id,
        lease_id=leader.id,
        leadership_generation=7,
    )


def test_exact_run_root_export_columns_complete_scoped_keys_and_postgres_ddl():
    api = schema_api()
    assert hasattr(api, "ReconciliationRun"), "Missing ReconciliationRun export"
    assert {t.name for t in api.metadata.tables.values()} == BASELINE | {
        "recovery_incarnations",
        "leadership_leases",
        "worker_heartbeats",
        "scheduler_state",
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
    }
    table = api.ReconciliationRun.__table__
    assert set(table.c.keys()) == {
        "id",
        *SCOPE,
        "trigger",
        "backend",
        "command_id",
        "credential_version",
        "recovery_incarnation_id",
        "owner_id",
        "lease_id",
        "leadership_generation",
        "requirements_version",
        "requirements_sha256",
        "state",
        "completeness",
        "started_at",
        "finished_at",
        "failure_code",
        "version",
        "created_at",
        "updated_at",
    }
    assert ("id", *SCOPE) in {
        tuple(c.name for c in key.columns)
        for key in table.constraints
        if isinstance(key, UniqueConstraint)
    }
    expected = {
        "accounts": (SCOPE, ("id", *SCOPE[1:])),
        "credential_versions": ((*SCOPE, "credential_version"), (*SCOPE, "credential_version")),
        "command_operations": (("command_id", *SCOPE), ("id", *SCOPE)),
        "recovery_incarnations": (("recovery_incarnation_id", *SCOPE), ("id", *SCOPE)),
        "worker_heartbeats": (("owner_id", *SCOPE), ("id", *SCOPE)),
        "leadership_leases": (("lease_id", *SCOPE), ("id", *SCOPE)),
    }
    assert {fk.referred_table.name for fk in table.foreign_key_constraints} == set(expected)
    indexes = {tuple(c.name for c in i.columns) for i in table.indexes}
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "UUID" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl and "BIGINT" in ddl
    for constraint in table.constraints:
        assert constraint.name and constraint.name in ddl
    for fk in table.foreign_key_constraints:
        sources, targets = expected[fk.referred_table.name]
        assert tuple(fk.column_keys) == sources
        assert tuple(e.column.name for e in fk.elements) == targets
        assert fk.ondelete == fk.onupdate == "RESTRICT" and not fk.use_alter
        assert sources in indexes
    for index in table.indexes:
        assert index.name in str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    for prior in api.metadata.tables.values():
        if prior is not table and prior.name not in {
            "reconciliation_resources",
            "reconciliation_drifts",
            "pnl_snapshots",
            "risk_state",
            "risk_halt_events",
            "risk_decisions",
        }:
            assert "reconciliation_runs" not in {
                fk.referred_table.name for fk in prior.foreign_key_constraints
            }


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "mode,backend,has_credential,valid",
    [
        ("LIVE", "VENUE", True, True),
        ("PAPER", "VENUE", True, True),
        ("BACKTEST", "VENUE", True, False),
        ("LIVE", "SIMULATOR", False, False),
        ("PAPER", "SIMULATOR", False, True),
        ("BACKTEST", "SIMULATOR", False, True),
        ("LIVE", "VENUE", False, False),
        ("PAPER", "VENUE", False, False),
        ("PAPER", "SIMULATOR", True, False),
        ("BACKTEST", "SIMULATOR", True, False),
    ],
)
async def test_backend_credential_mode_shapes(
    schema_db, channel, mode, backend, has_credential, valid
):
    owner = account(mode=mode)
    await save(schema_db, owner)
    recovery = incarnation(owner)
    await save(schema_db, recovery)
    await save(schema_db, credential(owner))
    row = run(owner, recovery, backend=backend, credential_version=1 if has_credential else None)
    if valid:
        await save(schema_db, row, channel)
        async with schema_db.transaction() as session:
            stored = await session.get(type(row), row.id)
            assert stored.state == "PENDING" and stored.completeness == "UNKNOWN"
            assert stored.command_id is stored.owner_id is stored.lease_id is None
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("present", list(product([False, True], repeat=3)))
async def test_optional_owner_fence_all_null_permutations(schema_db, channel, present):
    owner, recovery, _, _, leader, worker = await parents(schema_db)
    values = dict(
        zip(
            ("owner_id", "lease_id", "leadership_generation"),
            (worker.id, leader.id, 7),
            strict=True,
        )
    )
    for key, exists in zip(values, present, strict=True):
        if not exists:
            values[key] = None
    row = run(owner, recovery, **values)
    if present in [(False, False, False), (True, False, False), (True, True, True)]:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("state,completeness", list(product(STATES, COMPLETENESS)))
@pytest.mark.parametrize("present", list(product([False, True], repeat=3)))
async def test_every_state_completeness_start_finish_failure_null_shape(
    schema_db, channel, state, completeness, present
):
    owner, recovery = await graph(schema_db)
    start, finish, failure = present
    row = run(
        owner,
        recovery,
        state=state,
        completeness=completeness,
        started_at=NOW if start else None,
        finished_at=NOW if finish else None,
        failure_code=("CANCELLED" if state == "CANCELLED" else "REQUEST_FAILED")
        if failure
        else None,
    )
    valid = (
        (state == "PENDING" and completeness == "UNKNOWN" and present == (False, False, False))
        or (
            state == "COLLECTING"
            and completeness in ("UNKNOWN", "PARTIAL")
            and present == (True, False, False)
        )
        or (
            state == "FINISHED"
            and completeness in ("PARTIAL", "COMPLETE")
            and present == (True, True, False)
        )
        or (state in ("FAILED", "CANCELLED") and completeness != "COMPLETE" and finish and failure)
    )
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("state,code", list(product(STATES, CODES)))
async def test_safe_failure_code_classification(schema_db, channel, state, code):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery, **outcome(state, failure_code=code))
    valid = (state == "FAILED" and code != "CANCELLED") or (
        state == "CANCELLED" and code == "CANCELLED"
    )
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


INVALID = [
    (field, value)
    for field, values in [
        ("trigger", [None, "WEBSOCKET", "RECONNECT\x00tail"]),
        ("backend", [None, "REMOTE", "SIMULATOR\x00tail"]),
        ("state", [None, "COMPLETE"]),
        ("completeness", [None, "READY"]),
        ("failure_code", ["", "FAILED: unsafe", "UNKNOWN\x00tail"]),
        ("requirements_version", [None, 0, -1]),
        (
            "requirements_sha256",
            [None, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64, HASH + "\x00tail"],
        ),
        ("version", [None, 0, -1, 2]),
        ("recovery_incarnation_id", [None]),
        ("created_at", [None, NOW + timedelta(seconds=3)]),
        ("updated_at", [None, NOW - timedelta(seconds=1)]),
        ("started_at", [NOW - timedelta(seconds=1), NOW + timedelta(seconds=3)]),
        ("finished_at", [NOW - timedelta(seconds=1), NOW + timedelta(seconds=3)]),
    ]
    for value in values
]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field,value", INVALID)
async def test_checked_inputs_hash_pending_version_and_time_bounds(
    schema_db, channel, field, value
):
    owner, recovery = await graph(schema_db)
    values = outcome("FAILED") if field in ("failure_code", "started_at", "finished_at") else {}
    values[field] = value
    row = run(owner, recovery, **values)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_finish_cannot_precede_start_within_bounds(schema_db, channel):
    owner, recovery = await graph(schema_db)
    with pytest.raises(PersistenceError):
        await save(
            schema_db,
            run(owner, recovery, **outcome(started_at=NOW + timedelta(seconds=1), finished_at=NOW)),
            channel,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", INTEGERS)
@pytest.mark.parametrize("value", [True, 1.0, "1", 2**63, -(2**63) - 1, 0, -1])
async def test_exact_integer_bind_boundaries(schema_db, channel, field, value):
    owner, recovery, cred, cmd, leader, worker = await parents(schema_db)
    values = provenance(cred, cmd, leader, worker)
    values[field] = value
    row = run(owner, recovery, **values)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", TIMES)
@pytest.mark.parametrize("value", [NOW.replace(tzinfo=None), "2026-11-04"])
async def test_exact_utc_bind_boundaries(schema_db, channel, field, value):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery, **{**outcome(), field: value})
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", (*INTEGERS, *TIMES))
async def test_corrupt_typed_reads_fail_closed(schema_db, channel, field):
    owner, recovery, cred, cmd, leader, worker = await parents(schema_db)
    row = run(owner, recovery, **provenance(cred, cmd, leader, worker), **outcome())
    await save(schema_db, row)
    bad = 1.5 if field in INTEGERS else "2026-11-04T00:00:00.000000+01:00"
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            # Keep corrupt FK numerics visible until the typed read, then roll
            # back. A commit-time FK rejection would not prove the read adapter.
            await session.execute(text("PRAGMA defer_foreign_keys=ON"))
            await session.execute(text("PRAGMA ignore_check_constraints=ON"))
            await session.execute(
                text(f"UPDATE reconciliation_runs SET {field}=:v WHERE id=:id"),
                {"v": bad, "id": row.id.hex},
            )
            await session.execute(text("PRAGMA ignore_check_constraints=OFF"))
            if channel == "orm":
                await session.get(type(row), row.id)
            else:
                (await session.execute(select(row.__table__))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("reference", [None, *REFERENCES, "credential_version"])
@pytest.mark.parametrize("field", SCOPE)
async def test_each_fk_isolated_full_scope_mismatch(schema_db, channel, reference, field):
    owner, recovery, cred, cmd, leader, worker = await parents(schema_db)
    values = provenance(cred, cmd, leader, worker)
    values["recovery_incarnation_id"] = recovery.id
    new_value = {"account_id": uuid4(), "region": "global", "environment": "prod", "mode": "LIVE"}[
        field
    ]
    if reference is None:
        row = run(owner, recovery, **values)
        setattr(row, field, new_value)
    else:
        other = account(
            profile_name="other",
            **({"id": new_value} if field == "account_id" else {field: new_value}),
        )
        await save(schema_db, other)
        _, other_recovery, other_cred, other_cmd, other_leader, other_worker = await parents(
            schema_db, other
        )
        # Distinct credential versions isolate that FK even for account-id-only scope changes.
        if reference == "credential_version":
            async with schema_db.transaction() as session:
                await session.execute(
                    delete(type(other_cred)).where(type(other_cred).id == other_cred.id)
                )
            other_cred = credential(other, credential_version=2)
            await save(schema_db, other_cred)
        other_values = provenance(other_cred, other_cmd, other_leader, other_worker)
        other_values["recovery_incarnation_id"] = other_recovery.id
        other_values[reference] = values[reference]
        row = run(other, other_recovery, **other_values)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", SCOPE)
async def test_scope_nulls_rejected(schema_db, channel, field):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery, **{field: None})
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("reference", [*REFERENCES, "credential_version"])
async def test_dangling_references_rejected(schema_db, channel, reference):
    owner, recovery, cred, cmd, leader, worker = await parents(schema_db)
    values = provenance(cred, cmd, leader, worker)
    values[reference] = 2 if reference == "credential_version" else uuid4()
    row = run(owner, recovery, **values)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target", ["account", "recovery", "credential", "command", "lease", "worker"]
)
@pytest.mark.parametrize("operation", ["delete", "update"])
async def test_each_stable_parent_restricts_core_delete_and_key_update(
    schema_db, target, operation
):
    owner, recovery, cred, cmd, leader, worker = await parents(schema_db)
    await save(schema_db, run(owner, recovery, **provenance(cred, cmd, leader, worker)))
    parent = dict(
        account=owner, recovery=recovery, credential=cred, command=cmd, lease=leader, worker=worker
    )[target]
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            stmt = (
                delete(type(parent))
                if operation == "delete"
                else update(type(parent)).values(
                    **({"credential_version": 2} if target == "credential" else {"id": uuid4()})
                )
            )
            await session.execute(stmt.where(type(parent).id == parent.id))


IMMUTABLE = [
    ("id", uuid4()),
    ("account_id", uuid4()),
    ("region", "global"),
    ("environment", "prod"),
    ("mode", "LIVE"),
    ("trigger", "MANUAL"),
    ("backend", "SIMULATOR"),
    ("command_id", None),
    ("credential_version", 2),
    ("recovery_incarnation_id", uuid4()),
    ("owner_id", None),
    ("lease_id", None),
    ("leadership_generation", 8),
    ("requirements_version", 2),
    ("requirements_sha256", "b" * 64),
    ("created_at", NOW - timedelta(seconds=1)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", IMMUTABLE)
async def test_orm_request_and_provenance_identity_immutable(schema_db, field, value):
    owner, recovery, cred, cmd, leader, worker = await parents(schema_db)
    row = run(owner, recovery, **provenance(cred, cmd, leader, worker))
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            setattr(loaded, field, value)
            loaded.state, loaded.started_at = "COLLECTING", NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("state", STATES)
async def test_every_orm_delete_denied_even_unreferenced(schema_db, state):
    owner, recovery = await graph(schema_db)
    values = (
        outcome(state)
        if state in ("FINISHED", "FAILED", "CANCELLED")
        else (dict(state=state, started_at=NOW) if state == "COLLECTING" else {})
    )
    row = run(owner, recovery, **values)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.delete(await session.get(type(row), row.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["FINISHED", "FAILED", "CANCELLED"])
@pytest.mark.parametrize("mutation", ["outcome", "time", "reopen", "expired_reopen"])
async def test_prior_terminal_freeze_including_expired_state(schema_db, state, mutation):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        for key, value in outcome(state).items():
            setattr(loaded, key, value)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            assert loaded.version == 2
            if mutation == "outcome":
                loaded.completeness = "UNKNOWN" if state != "FINISHED" else "COMPLETE"
            elif mutation == "time":
                loaded.updated_at += timedelta(seconds=1)
            else:
                if mutation == "expired_reopen":
                    session.expire(loaded, ["state"])
                loaded.state, loaded.completeness = "COLLECTING", "UNKNOWN"
                loaded.finished_at = loaded.failure_code = None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, 1.0, False, 0, -1, "1", 2**63, None, 2])
@pytest.mark.parametrize("progress", [False, True])
async def test_version_edits_cannot_evade_exact_pre_generator_guard(schema_db, value, progress):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            loaded.version = value
            if progress:
                loaded.state, loaded.started_at = "COLLECTING", NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [1, 3])
async def test_version_advance_reset_and_stale_writer(schema_db, manual):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery)
    await save(schema_db, row)
    async with AsyncSession(schema_db.engine, expire_on_commit=False) as stale_session:
        stale = await stale_session.get(type(row), row.id)
        await stale_session.commit()
        async with schema_db.transaction() as session:
            fresh = await session.get(type(row), row.id)
            fresh.state, fresh.started_at = "COLLECTING", NOW
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                loaded = await session.get(type(row), row.id)
                assert loaded.version == 2
                loaded.version = manual
        stale.state, stale.started_at = "COLLECTING", NOW
        with pytest.raises(StaleDataError):
            await stale_session.commit()
        await stale_session.rollback()
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        assert stored.version == 2 and stored.state == "COLLECTING"


@pytest.mark.asyncio
async def test_missing_initial_version_and_pending_no_progress_update_rejected(schema_db):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery)
    del row.version
    with pytest.raises(PersistenceError):
        await save(schema_db, row)
    row = run(owner, recovery)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            (await session.get(type(row), row.id)).updated_at += timedelta(seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,stored_version", [("orm", 1), ("core", 9)])
async def test_nonpending_historical_version_obeys_existing_generator_boundary(
    schema_db, channel, stored_version
):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery, **outcome(), version=9)
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        assert (await session.get(type(row), row.id)).version == stored_version


@pytest.mark.asyncio
async def test_historical_context_survives_current_worker_lease_credential_changes(schema_db):
    owner, recovery, cred, cmd, leader, worker = await parents(schema_db)
    row = run(
        owner, recovery, **provenance(cred, cmd, leader, worker), **outcome(completeness="COMPLETE")
    )
    await save(schema_db, row)
    newer = incarnation(owner, reason="RESTORE")
    await save(schema_db, newer)
    await save(schema_db, credential(owner, credential_version=2))
    async with schema_db.transaction() as session:
        current = await session.get(type(leader), leader.id)
        current.recovery_incarnation_id, current.owner_id, current.generation = newer.id, uuid4(), 1
        (await session.get(type(worker), worker.id)).health_state = "STOPPED"
        (await session.get(type(owner), owner.id)).current_credential_version = 2
    # Historical incarnation and fence can differ from every current projection.
    successor = run(owner, recovery, trigger="RECOVERY", **provenance(cred, cmd, leader, worker))
    await save(schema_db, successor)
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        assert stored.recovery_incarnation_id == recovery.id
        assert stored.leadership_generation == 7 and stored.credential_version == 1
        assert stored.owner_id == worker.id and stored.requirements_version == 1
        assert stored.requirements_sha256 == HASH and stored.completeness == "COMPLETE"
        assert (await session.get(type(successor), successor.id)).completeness == "UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["STARTUP", "PERIODIC", "MANUAL", "RECOVERY", "RECONNECT"])
async def test_trigger_does_not_infer_completion_or_readiness(schema_db, trigger):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery, trigger=trigger)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        assert stored.completeness == "UNKNOWN" and stored.state == "PENDING"
        assert stored.started_at is stored.finished_at is stored.failure_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_aware_utc_microseconds_and_equal_time_bounds(schema_db, channel):
    owner, recovery = await graph(schema_db)
    instant = NOW.replace(microsecond=123456)
    local = instant.astimezone(timezone(timedelta(hours=5, minutes=30)))
    row = run(
        owner,
        recovery,
        **outcome(started_at=local, finished_at=local),
        created_at=local,
        updated_at=local,
    )
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        for field in TIMES:
            assert getattr(stored, field) == instant
            assert getattr(stored, field).utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_populated_sqlite_lifecycle_rollback_and_core_limitations(schema_db):
    owner, recovery = await graph(schema_db)
    row = run(owner, recovery, **outcome(completeness="COMPLETE"))
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(run(owner, recovery))
            await session.flush()
            session.add(run(owner, recovery, state="FINISHED"))
    async with schema_db.transaction() as session:
        assert len((await session.scalars(select(type(row)))).all()) == 1
        assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
        # A structural CHECK cannot provide historical immutability or terminal freeze.
        await session.execute(
            update(type(row))
            .where(type(row).id == row.id)
            .values(trigger="MANUAL", completeness="PARTIAL")
        )
    async with schema_db.transaction() as session:
        assert (await session.get(type(row), row.id)).trigger == "MANUAL"
    table = type(row).__table__
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(table.drop)
        await connection.run_sync(table.create)
    await save(schema_db, row, "core")
    with pytest.raises(PersistenceError):
        await save(schema_db, row, "core")
    async with schema_db.transaction() as session:
        await session.execute(delete(type(row)))
        assert (await session.scalars(select(type(row)))).all() == []


@pytest.mark.asyncio
async def test_runs_leave_all_existing_truth_and_placeholders_unchanged(execution_db):
    db, owner, *_ = execution_db
    api = schema_api()
    prior = [t for t in api.metadata.tables.values() if t.name != "reconciliation_runs"]
    recovery = incarnation(owner)
    await save(db, recovery)
    async with db.transaction() as session:
        before = {t.name: (await session.execute(select(t))).all() for t in prior}
    row = run(owner, recovery)
    await save(db, row)
    async with db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        for key, value in outcome(completeness="COMPLETE").items():
            setattr(loaded, key, value)
    async with db.transaction() as session:
        assert before == {t.name: (await session.execute(select(t))).all() for t in prior}
    assert not api.CredentialVersion.__table__.c.reconciliation_ref.foreign_keys
    for name, prefix in [
        ("CommandOperation", ""),
        ("OrderAttempt", "original_"),
        ("OrderAttemptEvent", "ingester_"),
        ("OrderTransition", ""),
    ]:
        for kind in ("leader", "recovery"):
            assert not getattr(api, name).__table__.c[f"{prefix}{kind}_ref"].foreign_keys


def test_import_has_no_engine_files_tasks_network_or_execution(tmp_path):
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
from hmt_crypto.persistence.models import ReconciliationRun
assert ReconciliationRun.__tablename__ == 'reconciliation_runs'
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
