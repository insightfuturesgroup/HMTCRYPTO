"""Offline coordination evidence; SQLite is not a PostgreSQL fencing proof."""

from datetime import timedelta, timezone
from itertools import product
from uuid import uuid4

import pytest
from execution_test_support import execution_db as execution_db
from schema_test_support import NOW, account, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import MetaData, UniqueConstraint, delete, insert, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from hmt_crypto.persistence.errors import PersistenceError

BASELINE = {
    "accounts",
    "credential_versions",
    "catalogue_snapshots",
    "catalogue_products",
    "signals",
    "command_operations",
    "orders",
    "order_attempts",
    "order_attempt_events",
    "order_transitions",
    "fills",
    "fill_order_links",
    "exit_intents",
    "protection_authorizations",
    "position_protections",
    "position_protection_orders",
}
NEW_TABLES = {"recovery_incarnations", "leadership_leases"}
SCOPE = ("account_id", "region", "environment", "mode")


def incarnation(owner, **changes):
    values = dict(id=uuid4(), **scope(owner), reason="INITIALIZATION", recorded_at=NOW)
    values.update(changes)
    return schema_api().RecoveryIncarnation(**values)


def lease(owner, recovery, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        recovery_incarnation_id=recovery.id,
        state="UNOWNED",
        owner_id=None,
        generation=0,
        heartbeat_at=None,
        expires_at=None,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    values.update(changes)
    return schema_api().LeadershipLease(**values)


def held(**changes):
    values = dict(
        state="HELD",
        owner_id=uuid4(),
        generation=1,
        heartbeat_at=NOW,
        expires_at=NOW + timedelta(seconds=5),
        updated_at=NOW + timedelta(seconds=1),
    )
    values.update(changes)
    return values


async def save(db, row, channel="orm"):
    async with db.transaction() as session:
        if channel == "orm":
            session.add(row)
        else:
            await session.execute(
                insert(row.__table__).values(
                    **{c.name: getattr(row, c.name) for c in row.__table__.columns}
                )
            )


async def graph(db):
    owner = account()
    recovery = incarnation(owner)
    await save(db, owner)
    await save(db, recovery)
    return owner, recovery


def test_exact_two_coordination_tables_and_aggregate_exports():
    api = schema_api()
    assert {t.name for t in api.metadata.tables.values()} == BASELINE | NEW_TABLES | {
        "worker_heartbeats",
        "scheduler_state",
        "reconciliation_runs",
        "reconciliation_resources",
    }
    assert api.RecoveryIncarnation.__tablename__ == "recovery_incarnations"
    assert api.LeadershipLease.__tablename__ == "leadership_leases"
    assert set(api.RecoveryIncarnation.__table__.c.keys()) == {
        "id",
        *SCOPE,
        "reason",
        "recorded_at",
    }
    assert set(api.LeadershipLease.__table__.c.keys()) == {
        "id",
        *SCOPE,
        "recovery_incarnation_id",
        "state",
        "owner_id",
        "generation",
        "heartbeat_at",
        "expires_at",
        "version",
        "created_at",
        "updated_at",
    }


def test_complete_named_postgres_ddl_and_immutable_scoped_foreign_keys():
    api = schema_api()
    metadata = MetaData(schema="hmt_crypto", naming_convention=api.metadata.naming_convention)
    for table in api.metadata.tables.values():
        table.to_metadata(metadata)
    dialect = postgresql.dialect()
    for table in metadata.tables.values():
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE hmt_crypto.{table.name}" in ddl
        for constraint in table.constraints:
            assert constraint.name
        for fk in table.foreign_key_constraints:
            assert fk.ondelete == fk.onupdate == "RESTRICT"
            if fk.use_alter:
                assert "ALTER TABLE" in str(AddConstraint(fk).compile(dialect=dialect))
        for index in table.indexes:
            assert index.name and "CREATE" in str(CreateIndex(index).compile(dialect=dialect))
    for name in NEW_TABLES:
        table = metadata.tables[f"hmt_crypto.{name}"]
        unique_keys = {
            tuple(c.name for c in key.columns)
            for key in table.constraints
            if isinstance(key, UniqueConstraint)
        }
        assert ("id", *SCOPE) in unique_keys
        account_key = next(
            fk for fk in table.foreign_key_constraints if fk.referred_table.name == "accounts"
        )
        assert tuple(account_key.column_keys) == SCOPE
        assert tuple(e.column.name for e in account_key.elements) == ("id", *SCOPE[1:])
        for fk in table.foreign_key_constraints:
            assert not {"generation", "version", "owner_id", "heartbeat_at", "expires_at"} & set(
                fk.column_keys
            )
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "UUID" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl
    table = api.LeadershipLease.__table__
    assert SCOPE in {
        tuple(c.name for c in key.columns)
        for key in table.constraints
        if isinstance(key, UniqueConstraint)
    }
    key = next(
        fk
        for fk in table.foreign_key_constraints
        if fk.referred_table.name == "recovery_incarnations"
    )
    assert tuple(key.column_keys) == ("recovery_incarnation_id", *SCOPE)
    assert tuple(e.column.name for e in key.elements) == ("id", *SCOPE)
    assert ("recovery_incarnation_id", *SCOPE) in {
        tuple(c.name for c in index.columns) for index in table.indexes
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("reason", ["INITIALIZATION", "RESTORE", "RECOVERY"])
async def test_multiple_immutable_incarnations_per_scope(schema_db, channel, reason):
    owner, first = await graph(schema_db)
    second = incarnation(owner, reason=reason)
    await save(schema_db, second, channel)
    async with schema_db.transaction() as session:
        assert {r.id for r in (await session.scalars(select(type(first)))).all()} == {
            first.id,
            second.id,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "shape",
    [
        {},
        {"generation": 8},
        held(),
        held(updated_at=NOW + timedelta(days=1)),
    ],
)
async def test_initial_released_held_and_expired_held_are_representable(schema_db, channel, shape):
    owner, recovery = await graph(schema_db)
    row = lease(owner, recovery, **shape)
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        assert stored.state == shape.get("state", "UNOWNED")
        assert stored.generation == shape.get("generation", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_duplicate_lease_scope_is_rejected_even_for_another_incarnation(schema_db, channel):
    owner, recovery = await graph(schema_db)
    newer = incarnation(owner, reason="RESTORE")
    await save(schema_db, newer)
    await save(schema_db, lease(owner, recovery), channel)
    with pytest.raises(PersistenceError):
        await save(schema_db, lease(owner, newer), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("state", ["UNOWNED", "HELD"])
@pytest.mark.parametrize("present", list(product([False, True], repeat=3)))
async def test_ownership_nullable_group_is_exact(schema_db, channel, state, present):
    owner, recovery = await graph(schema_db)
    shape = held(state=state)
    for field, exists in zip(("owner_id", "heartbeat_at", "expires_at"), present, strict=True):
        if not exists:
            shape[field] = None
    row = lease(owner, recovery, **shape)
    valid = present == ((True, True, True) if state == "HELD" else (False, False, False))
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "kind,changes",
    [
        ("incarnation", {"reason": "UNKNOWN"}),
        ("incarnation", {"reason": None}),
        ("incarnation", {"reason": "RESTORE\x00tail"}),
        ("lease", {"state": "EXPIRED"}),
        ("lease", {"state": None}),
        ("lease", {"generation": -1}),
        ("lease", {"generation": None}),
        ("lease", {"version": 0}),
        ("lease", {"version": None}),
        ("lease", {"recovery_incarnation_id": None}),
        ("lease", {"updated_at": NOW - timedelta(seconds=1)}),
        ("lease", held(generation=0)),
        ("lease", held(heartbeat_at=NOW - timedelta(seconds=1))),
        ("lease", held(heartbeat_at=NOW + timedelta(seconds=2))),
        ("lease", held(expires_at=NOW)),
        ("lease", held(expires_at=NOW - timedelta(seconds=1))),
        ("lease", held(owner_id=None)),
    ],
)
async def test_checked_state_reason_counts_and_time_order(schema_db, channel, kind, changes):
    owner, recovery = await graph(schema_db)
    row = (
        incarnation(owner, **changes)
        if kind == "incarnation"
        else lease(owner, recovery, **changes)
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["generation", "version"])
@pytest.mark.parametrize("value", [True, 1.5, "1", 2**63, -(2**63) - 1])
async def test_integer_binds_are_strict_even_before_orm_version_generation(
    schema_db, channel, field, value
):
    owner, recovery = await graph(schema_db)
    with pytest.raises(PersistenceError):
        await save(schema_db, lease(owner, recovery, **{field: value}), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "kind,field",
    [
        ("incarnation", "recorded_at"),
        ("lease", "created_at"),
        ("lease", "updated_at"),
        ("lease", "heartbeat_at"),
        ("lease", "expires_at"),
    ],
)
@pytest.mark.parametrize("value", [NOW.replace(tzinfo=None), "2026-11-04", None])
async def test_required_and_held_timestamps_reject_invalid_values(
    schema_db, channel, kind, field, value
):
    owner, recovery = await graph(schema_db)
    row = incarnation(owner) if kind == "incarnation" else lease(owner, recovery, **held())
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_aware_times_normalize_to_utc_with_microseconds(schema_db, channel):
    owner, _ = await graph(schema_db)
    instant = NOW.replace(microsecond=123456)
    recovery = incarnation(
        owner, recorded_at=instant.astimezone(timezone(timedelta(hours=5, minutes=30)))
    )
    await save(schema_db, recovery, channel)
    row = lease(owner, recovery, created_at=instant, updated_at=instant)
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        assert (await session.get(type(recovery), recovery.id)).recorded_at == instant
        assert (await session.get(type(row), row.id)).created_at == instant


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("incarnation", "recorded_at", "not-utc"),
        ("lease", "created_at", "2026-11-04T00:00:00.000000+01:00"),
        ("lease", "updated_at", "bad"),
        ("lease", "heartbeat_at", "bad"),
        ("lease", "expires_at", "bad"),
        ("lease", "generation", 1.5),
        ("lease", "version", 1.5),
    ],
)
async def test_corrupt_results_fail_closed_on_typed_reads(schema_db, channel, kind, field, value):
    owner, recovery = await graph(schema_db)
    row = recovery if kind == "incarnation" else lease(owner, recovery, **held())
    if kind == "lease":
        await save(schema_db, row)
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
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("kind", ["incarnation", "lease_account", "lease_incarnation"])
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
async def test_full_scope_rejects_mismatch_and_null(schema_db, channel, kind, field, value):
    owner, recovery = await graph(schema_db)
    if kind == "lease_incarnation" and value is not None:
        # Keep the account FK valid, isolating the incarnation's composite FK.
        other = account(
            profile_name="other", **{field: value} if field != "account_id" else {"id": value}
        )
        await save(schema_db, other)
        row = lease(other, recovery)
    else:
        row = incarnation(owner) if kind == "incarnation" else lease(owner, recovery)
        setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("incarnation", "id", uuid4()),
        ("incarnation", "account_id", uuid4()),
        ("incarnation", "region", "global"),
        ("incarnation", "environment", "prod"),
        ("incarnation", "mode", "LIVE"),
        ("incarnation", "reason", "RESTORE"),
        ("incarnation", "recorded_at", NOW + timedelta(seconds=1)),
        ("lease", "id", uuid4()),
        ("lease", "account_id", uuid4()),
        ("lease", "region", "global"),
        ("lease", "environment", "prod"),
        ("lease", "mode", "LIVE"),
        ("lease", "created_at", NOW - timedelta(seconds=1)),
    ],
)
async def test_orm_identity_and_incarnation_history_are_immutable(schema_db, kind, field, value):
    owner, recovery = await graph(schema_db)
    row = recovery if kind == "incarnation" else lease(owner, recovery)
    if kind == "lease":
        await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            setattr(loaded, field, value)


@pytest.mark.asyncio
async def test_unreferenced_incarnation_orm_delete_is_rejected(schema_db):
    _, recovery = await graph(schema_db)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.delete(await session.get(type(recovery), recovery.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["account", "incarnation"])
@pytest.mark.parametrize("operation", ["delete", "update"])
async def test_parent_foreign_keys_restrict_core_mutations(schema_db, target, operation):
    owner, recovery = await graph(schema_db)
    row = lease(owner, recovery)
    await save(schema_db, row)
    parent = owner if target == "account" else recovery
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            statement = (
                delete(type(parent))
                if operation == "delete"
                else update(type(parent)).values(id=uuid4())
            )
            await session.execute(statement.where(type(parent).id == parent.id))


@pytest.mark.asyncio
async def test_move_retains_incarnation_history_without_monotonic_generation_enforcement(
    schema_db,
):
    owner, first = await graph(schema_db)
    second = incarnation(owner, reason="RESTORE")
    await save(schema_db, second)
    row = lease(owner, first, **held(generation=4))
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        loaded.recovery_incarnation_id = second.id
        loaded.owner_id, loaded.generation = uuid4(), 1
        loaded.heartbeat_at, loaded.expires_at = (
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=8),
        )
        loaded.updated_at = NOW + timedelta(seconds=2)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.recovery_incarnation_id == second.id and loaded.version == 2
        assert loaded.generation == 1
        assert (await session.get(type(first), first.id)).reason == "INITIALIZATION"
        loaded.state, loaded.owner_id = "UNOWNED", None
        loaded.heartbeat_at = loaded.expires_at = None
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.state == "UNOWNED" and loaded.generation == 1 and loaded.version == 3
        assert len((await session.scalars(select(type(first)))).all()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_version", [1, 3])
async def test_optimistic_orm_version_advance_reset_rejection_and_stale_writer(
    schema_db, manual_version
):
    owner, recovery = await graph(schema_db)
    row = lease(owner, recovery)
    await save(schema_db, row)
    async with AsyncSession(schema_db.engine, expire_on_commit=False) as stale_session:
        stale = await stale_session.get(type(row), row.id)
        await stale_session.commit()  # End SQLite's read snapshot before the competing commit.
        async with schema_db.transaction() as session:
            fresh = await session.get(type(row), row.id)
            fresh.generation = 4
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                reset = await session.get(type(row), row.id)
                assert reset.version == 2
                reset.version = manual_version
        stale.generation = 9
        with pytest.raises(StaleDataError):
            await stale_session.commit()
        await stale_session.rollback()
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        assert stored.version == 2 and stored.generation == 4


@pytest.mark.asyncio
async def test_missing_initial_version_cannot_be_silently_generated(schema_db):
    owner, recovery = await graph(schema_db)
    row = lease(owner, recovery)
    del row.version
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, 1.0, False, 0, -1, "1", 2**63])
async def test_invalid_current_version_cannot_hide_behind_numeric_equality(schema_db, value):
    owner, recovery = await graph(schema_db)
    row = lease(owner, recovery)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            loaded.version = value
            loaded.generation = 2
    async with schema_db.transaction() as session:
        stored = await session.get(type(row), row.id)
        assert stored.version == 1 and stored.generation == 0


@pytest.mark.asyncio
async def test_populated_file_backed_create_drop_recreate_and_rollback(schema_db):
    api = schema_api()
    owner, recovery = await graph(schema_db)
    row = lease(owner, recovery)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(incarnation(owner, reason="RECOVERY"))
            await session.flush()
            loaded = await session.get(type(row), row.id)
            loaded.state = "HELD"  # Missing the mandatory ownership group.
    async with schema_db.transaction() as session:
        assert len((await session.scalars(select(type(recovery)))).all()) == 1
        assert (await session.get(type(row), row.id)).state == "UNOWNED"
        assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
    tables = [api.RecoveryIncarnation.__table__, api.LeadershipLease.__table__]
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(lambda sync: api.metadata.drop_all(sync, tables=tables))
        await connection.run_sync(lambda sync: api.metadata.create_all(sync, tables=tables))
    await save(schema_db, recovery, "core")
    await save(schema_db, row, "core")
    with pytest.raises(PersistenceError):
        await save(schema_db, lease(owner, recovery), "core")


def test_historical_fence_placeholders_remain_scalar_and_unattached():
    api = schema_api()
    for name, prefix in [
        ("CommandOperation", ""),
        ("OrderAttempt", "original_"),
        ("OrderAttemptEvent", "ingester_"),
        ("OrderTransition", ""),
    ]:
        table = getattr(api, name).__table__
        for kind in ("leader", "recovery"):
            ref, generation = f"{prefix}{kind}_ref", f"{prefix}{kind}_generation"
            assert table.c[ref].nullable and table.c[generation].nullable
            assert not table.c[ref].foreign_keys and not table.c[generation].foreign_keys
            assert f"ck_{table.name}_{ref}_generation" in {c.name for c in table.constraints}
        assert not NEW_TABLES & {fk.referred_table.name for fk in table.foreign_key_constraints}


@pytest.mark.asyncio
async def test_coordination_changes_do_not_rewrite_existing_execution_truth(execution_db):
    db, owner, *_ = execution_db
    tables = [t for t in schema_api().metadata.tables.values() if t.name in BASELINE]
    async with db.transaction() as session:
        before = {t.name: (await session.execute(select(t))).all() for t in tables}
    recovery = incarnation(owner)
    await save(db, recovery)
    row = lease(owner, recovery, **held())
    await save(db, row)
    async with db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        loaded.state, loaded.owner_id, loaded.heartbeat_at, loaded.expires_at = (
            "UNOWNED",
            None,
            None,
            None,
        )
    async with db.transaction() as session:
        assert before == {t.name: (await session.execute(select(t))).all() for t in tables}
