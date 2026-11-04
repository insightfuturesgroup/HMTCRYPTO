"""Offline resource admission. Structural completion never implies coverage.

Typed Core shape checks do not establish sibling query hash agreement or history
immutability. Compiled PostgreSQL DDL is not real PostgreSQL enforcement.
"""

import subprocess
import sys
from datetime import timedelta
from itertools import product
from uuid import uuid4

import pytest
from execution_test_support import execution_db as execution_db
from schema_test_support import HASH, NOW, account, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import UniqueConstraint, delete, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.schema import CreateIndex, CreateTable
from test_coordination_schema import SCOPE, graph, incarnation, save
from test_fill_schema import fill
from test_protection_schema import authorization, intent, protection
from test_reconciliation_run_schema import run

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError

STATES = ("PENDING", "COLLECTING", "COLLECTED", "TRUNCATED", "FAILED", "CANCELLED")
CODES = (
    None,
    "REQUEST_FAILED",
    "INVALID_RESPONSE",
    "PAGINATION_INCOMPLETE",
    "LIMIT_REACHED",
    "CANCELLED",
    "UNKNOWN",
)
KINDS = (
    "BALANCES",
    "LATEST_POSITION",
    "MARGINED_POSITIONS",
    "WALLET_TRANSACTIONS",
    "OPEN_ORDERS",
    "ORDER_HISTORY",
    "FILLS",
)
INTEGERS = (
    "collection_attempt",
    "query_schema_version",
    "max_pages",
    "max_items",
    "pages_received",
    "items_received",
    "reported_total",
    "version",
)
TIMES = ("first_requested_at", "last_received_at", "finished_at", "created_at", "updated_at")


def resource(owner, parent, *, query=None, **changes):
    api = schema_api()
    assert hasattr(api, "ReconciliationResource"), "Missing ReconciliationResource export"
    values = dict(
        id=uuid4(),
        **scope(owner),
        run_id=parent.id,
        resource_kind="FILLS",
        collection_attempt=1,
        pagination_kind="CURSOR",
        max_pages=None,
        max_items=None,
        state="PENDING",
        pages_received=0,
        items_received=0,
        reported_total=None,
        after_cursor_state="UNKNOWN",
        after_cursor_sha256=None,
        before_cursor_state="UNKNOWN",
        before_cursor_sha256=None,
        completion_basis="UNKNOWN",
        first_requested_at=None,
        last_received_at=None,
        finished_at=None,
        failure_code=None,
        version=1,
        created_at=NOW,
        updated_at=NOW + timedelta(seconds=2),
    )
    values.update(changes)
    if values["pagination_kind"] == "NONE":
        for side in ("after", "before"):
            if f"{side}_cursor_state" not in changes:
                values[f"{side}_cursor_state"] = "NOT_APPLICABLE"
    return api.ReconciliationResource.from_query(
        query=query if query is not None else dict(initial_cursor_direction="NONE"), **values
    )


async def setup(db):
    owner, recovery = await graph(db)
    parent = run(owner, recovery)
    await save(db, parent)
    return owner, parent


def progress(state="COLLECTED", **changes):
    values = dict(
        state=state,
        pages_received=1,
        items_received=0,
        first_requested_at=NOW,
        last_received_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        completion_basis="FORWARD_EXHAUSTED" if state == "COLLECTED" else "UNKNOWN",
        after_cursor_state="ABSENT",
        failure_code={
            "TRUNCATED": "LIMIT_REACHED",
            "FAILED": "REQUEST_FAILED",
            "CANCELLED": "CANCELLED",
        }.get(state),
    )
    if state == "COLLECTING":
        values.update(finished_at=None, completion_basis="UNKNOWN")
    values.update(changes)
    return values


def test_exact_resource_keys_columns_and_complete_postgres_ddl():
    api = schema_api()
    assert hasattr(api, "ReconciliationResource"), "Missing ReconciliationResource export"
    table = api.ReconciliationResource.__table__
    assert set(table.c.keys()) == {
        "id",
        *SCOPE,
        "run_id",
        "resource_kind",
        "collection_attempt",
        "query_schema_version",
        "query_payload",
        "query_sha256",
        "pagination_kind",
        "max_pages",
        "max_items",
        "state",
        "pages_received",
        "items_received",
        "reported_total",
        "after_cursor_state",
        "after_cursor_sha256",
        "before_cursor_state",
        "before_cursor_sha256",
        "completion_basis",
        *TIMES,
        "failure_code",
        "version",
    }
    assert {
        tuple(c.name for c in key.columns)
        for key in table.constraints
        if isinstance(key, UniqueConstraint)
    } == {
        ("id", *SCOPE),
        (*SCOPE, "run_id", "resource_kind", "query_sha256", "collection_attempt"),
        ("id", *SCOPE, "run_id", "resource_kind"),
    }
    expected = {
        "accounts": (SCOPE, ("id", *SCOPE[1:])),
        "reconciliation_runs": (("run_id", *SCOPE), ("id", *SCOPE)),
    }
    assert {fk.referred_table.name for fk in table.foreign_key_constraints} == set(expected)
    indexes = {tuple(c.name for c in i.columns) for i in table.indexes}
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "UUID" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl and "BIGINT" in ddl
    for constraint in table.constraints:
        assert constraint.name and constraint.name in ddl
    for fk in table.foreign_key_constraints:
        source, target = expected[fk.referred_table.name]
        assert tuple(fk.column_keys) == source
        assert tuple(e.column.name for e in fk.elements) == target
        assert fk.ondelete == fk.onupdate == "RESTRICT" and source in indexes
    for index in table.indexes:
        assert index.name in str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    for prior in api.metadata.tables.values():
        if prior is not table and prior.name not in {
            "position_snapshots",
            "balance_snapshots",
            "wallet_transactions",
        }:
            assert "reconciliation_resources" not in {
                fk.referred_table.name for fk in prior.foreign_key_constraints
            }


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("kind,pagination", list(product(KINDS, ["NONE", "CURSOR", "UNKNOWN"])))
async def test_resource_pagination_compatibility(schema_db, channel, kind, pagination):
    owner, parent = await setup(schema_db)
    row = resource(
        owner,
        parent,
        resource_kind=kind,
        pagination_kind=pagination,
        query=dict(
            initial_cursor_direction="NONE",
            **({"product_ids": [1]} if kind == "LATEST_POSITION" else {}),
        ),
    )
    valid = (
        pagination == "NONE"
        if kind in {"BALANCES", "LATEST_POSITION", "MARGINED_POSITIONS"}
        else pagination in {"CURSOR", "UNKNOWN"}
    )
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("side", ["after", "before"])
@pytest.mark.parametrize("state", ["UNKNOWN", "ABSENT", "PRESENT", "NOT_APPLICABLE", "BAD", None])
@pytest.mark.parametrize("digest", [None, HASH, "A" * 64, "a" * 63, "secret"])
async def test_cursor_hash_is_two_valued_including_nulls(schema_db, channel, side, state, digest):
    owner, parent = await setup(schema_db)
    row = resource(
        owner,
        parent,
        **(
            progress("COLLECTING")
            | {f"{side}_cursor_state": state, f"{side}_cursor_sha256": digest}
        ),
    )
    valid = (state in {"UNKNOWN", "ABSENT"} and digest is None) or (
        state == "PRESENT" and digest == HASH
    )
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("state,code", list(product(STATES, CODES)))
async def test_all_state_failure_code_null_branches(schema_db, channel, state, code):
    owner, parent = await setup(schema_db)
    values = {} if state == "PENDING" else progress(state)
    values["failure_code"] = code
    row = resource(owner, parent, **values)
    valid = (
        code is None
        if state in {"PENDING", "COLLECTING", "COLLECTED"}
        else code
        in {
            "TRUNCATED": {"LIMIT_REACHED", "PAGINATION_INCOMPLETE"},
            "FAILED": {"REQUEST_FAILED", "INVALID_RESPONSE", "PAGINATION_INCOMPLETE", "UNKNOWN"},
            "CANCELLED": {"CANCELLED"},
        }[state]
    )
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "basis", ["UNKNOWN", "SINGLE_RESPONSE", "FORWARD_EXHAUSTED", "BACKWARD_EXHAUSTED"]
)
@pytest.mark.parametrize("after,before", list(product(["UNKNOWN", "ABSENT", "PRESENT"], repeat=2)))
async def test_collected_requires_explicit_terminal_cursor(
    schema_db, channel, basis, after, before
):
    owner, parent = await setup(schema_db)
    values = progress(
        completion_basis=basis,
        after_cursor_state=after,
        before_cursor_state=before,
        after_cursor_sha256=HASH if after == "PRESENT" else None,
        before_cursor_sha256=HASH if before == "PRESENT" else None,
    )
    row = resource(owner, parent, **values)
    valid = (basis == "FORWARD_EXHAUSTED" and after == "ABSENT") or (
        basis == "BACKWARD_EXHAUSTED" and before == "ABSENT"
    )
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize(
    "pages,items,received",
    [(0, 0, None), (0, 1, None), (0, 0, NOW), (1, 0, None), (1, 0, NOW), (2, 3, NOW)],
)
async def test_state_page_item_receive_shapes(schema_db, channel, state, pages, items, received):
    owner, parent = await setup(schema_db)
    values = {} if state == "PENDING" else progress(state)
    values.update(pages_received=pages, items_received=items, last_received_at=received)
    row = resource(owner, parent, **values)
    valid = (
        ((pages == 0 and items == 0 and received is None) or (pages > 0 and received is not None))
        and (state != "PENDING" or pages == 0)
        and (state not in {"COLLECTED", "TRUNCATED"} or pages > 0)
    )
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("reported_total", 0),
        ("first_requested_at", NOW),
        ("finished_at", NOW),
        ("after_cursor_state", "ABSENT"),
        ("before_cursor_state", "ABSENT"),
        ("completion_basis", "FORWARD_EXHAUSTED"),
    ],
)
async def test_pending_exact_initial_shape(schema_db, channel, field, value):
    owner, parent = await setup(schema_db)
    with pytest.raises(PersistenceError):
        await save(schema_db, resource(owner, parent, **{field: value}), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("first_requested_at", None),
        ("last_received_at", None),
        ("finished_at", None),
        ("first_requested_at", NOW + timedelta(seconds=1)),
        ("finished_at", NOW - timedelta(seconds=1)),
        ("last_received_at", NOW + timedelta(seconds=3)),
        ("updated_at", NOW - timedelta(seconds=1)),
        ("created_at", NOW + timedelta(seconds=1)),
        ("reported_total", -1),
        ("pages_received", -1),
        ("items_received", -1),
        ("collection_attempt", 0),
        ("version", 0),
        ("max_pages", 0),
        ("max_items", 0),
        ("query_schema_version", 2),
    ],
)
async def test_required_counts_and_time_relations(schema_db, channel, field, value):
    owner, parent = await setup(schema_db)
    with pytest.raises(PersistenceError):
        await save(schema_db, resource(owner, parent, **(progress() | {field: value})), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("pages", [0, 1, 2])
async def test_none_is_at_most_one_response_with_nullable_budgets(schema_db, channel, state, pages):
    owner, parent = await setup(schema_db)
    values = {} if state == "PENDING" else progress(state)
    values.update(
        pages_received=pages,
        items_received=0,
        last_received_at=NOW if pages else None,
        after_cursor_state="NOT_APPLICABLE",
        before_cursor_state="NOT_APPLICABLE",
        completion_basis="SINGLE_RESPONSE" if state == "COLLECTED" else "UNKNOWN",
    )
    row = resource(owner, parent, resource_kind="BALANCES", pagination_kind="NONE", **values)
    valid = (
        pages <= 1
        and state != "TRUNCATED"
        and (pages == 0 if state == "PENDING" else pages == 1 if state == "COLLECTED" else True)
    )
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("pagination", ["NONE", "UNKNOWN"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("max_pages", 1),
        ("max_items", 1),
        ("after_cursor_state", "ABSENT"),
        ("before_cursor_state", "PRESENT"),
    ],
)
async def test_none_and_unknown_config_restrictions(schema_db, pagination, field, value):
    owner, parent = await setup(schema_db)
    with pytest.raises(PersistenceError):
        await save(
            schema_db,
            resource(
                owner,
                parent,
                resource_kind="BALANCES" if pagination == "NONE" else "FILLS",
                pagination_kind=pagination,
                **{field: value},
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["COLLECTED", "TRUNCATED"])
async def test_unknown_pagination_cannot_claim_terminal_collection(schema_db, state):
    owner, parent = await setup(schema_db)
    with pytest.raises(PersistenceError):
        await save(schema_db, resource(owner, parent, pagination_kind="UNKNOWN", **progress(state)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        dict(initial_cursor_direction="NONE", page_size=10),
        dict(initial_cursor_direction="BEFORE", initial_cursor_sha256=HASH),
    ],
)
async def test_unknown_query_cannot_claim_known_pagination_configuration(schema_db, query):
    owner, parent = await setup(schema_db)
    with pytest.raises(PersistenceError):
        await save(schema_db, resource(owner, parent, query=query, pagination_kind="UNKNOWN"))


@pytest.mark.asyncio
async def test_budgets_and_totals_are_not_count_caps_or_completeness(schema_db):
    owner, parent = await setup(schema_db)
    row = resource(
        owner,
        parent,
        max_pages=1,
        max_items=1,
        **progress(
            "COLLECTING",
            pages_received=2,
            items_received=20,
            reported_total=0,
            after_cursor_state="PRESENT",
            after_cursor_sha256=HASH,
        ),
    )
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.state == "COLLECTING" and loaded.completion_basis == "UNKNOWN"
        assert (
            loaded.pages_received == 2
            and loaded.items_received == 20
            and loaded.reported_total == 0
        )
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            (await session.get(type(row), row.id)).state = "COLLECTED"


@pytest.mark.asyncio
async def test_populated_lifecycle_new_attempt_and_query_rollback_parent_unchanged(schema_db):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent)
    tables = [
        t for t in schema_api().metadata.tables.values() if t.name != "reconciliation_resources"
    ]
    async with schema_db.transaction() as session:
        before = {t.name: (await session.execute(select(t))).all() for t in tables}
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(resource(owner, parent, collection_attempt=2))
            await session.flush()
            session.add(resource(owner, parent))
    for changes in (
        {"collection_attempt": 2},
        {"query": dict(initial_cursor_direction="NONE", page_size=10)},
    ):
        await save(schema_db, resource(owner, parent, **changes))
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        for key, value in progress("COLLECTING").items():
            setattr(loaded, key, value)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        for key, value in progress().items():
            setattr(loaded, key, value)
    async with schema_db.transaction() as session:
        assert before == {t.name: (await session.execute(select(t))).all() for t in tables}
        assert (await session.get(type(row), row.id)).version == 3
        assert len((await session.scalars(select(type(row)))).all()) == 3
        assert (await session.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
    table = type(row).__table__
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(table.drop)
        await connection.run_sync(table.create)
    await save(schema_db, row, "core")
    async with schema_db.transaction() as session:
        await session.execute(delete(type(row)))


@pytest.mark.asyncio
@pytest.mark.parametrize("field", SCOPE)
async def test_run_fk_uses_every_scope_dimension(schema_db, field):
    owner, parent = await setup(schema_db)
    new_value = {
        "account_id": uuid4(),
        "region": "global",
        "environment": "prod",
        "mode": "BACKTEST",
    }[field]
    other_owner = account(
        profile_name="other", **({"id": new_value} if field == "account_id" else {field: new_value})
    )
    await save(schema_db, other_owner)
    recovery = incarnation(other_owner)
    await save(schema_db, recovery)
    other_parent = run(other_owner, recovery, mode=other_owner.mode)
    await save(schema_db, other_parent)
    # Use an existing full child scope and the other run: isolates the run FK.
    with pytest.raises(PersistenceError):
        await save(schema_db, resource(other_owner, parent))
    row = resource(owner, parent, **{field: None})
    with pytest.raises(PersistenceError):
        await save(schema_db, row)
    with pytest.raises(PersistenceError):
        await save(schema_db, resource(owner, other_parent))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "update"])
async def test_parent_restricts_core_key_mutations(schema_db, operation):
    owner, parent = await setup(schema_db)
    await save(schema_db, resource(owner, parent))
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            stmt = (
                delete(type(parent))
                if operation == "delete"
                else update(type(parent)).values(id=uuid4())
            )
            await session.execute(stmt.where(type(parent).id == parent.id))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("id", uuid4()),
        ("run_id", uuid4()),
        ("resource_kind", "ORDER_HISTORY"),
        ("collection_attempt", 2),
        ("query_schema_version", 2),
        ("query_payload", "{}"),
        ("query_sha256", "b" * 64),
        ("pagination_kind", "UNKNOWN"),
        ("max_pages", 1),
        ("max_items", 1),
        ("created_at", NOW - timedelta(seconds=1)),
        *[(f, None) for f in SCOPE],
    ],
)
async def test_orm_identity_and_configuration_are_immutable(schema_db, field, value):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            setattr(loaded, field, value)
            loaded.state, loaded.first_requested_at = "COLLECTING", NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("state", STATES)
async def test_every_orm_delete_is_denied(schema_db, state):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent, **({} if state == "PENDING" else progress(state)))
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.delete(await session.get(type(row), row.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["COLLECTED", "TRUNCATED", "FAILED", "CANCELLED"])
@pytest.mark.parametrize("expired", [False, True])
async def test_persisted_prior_terminal_state_freezes_even_expired_reopen(
    schema_db, state, expired
):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent, **progress(state))
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            if expired:
                session.expire(loaded, ["state"])
            for key, value in progress("COLLECTING").items():
                setattr(loaded, key, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, True, False, 1.0, "1", 0, -1, 2**63, 2])
@pytest.mark.parametrize("operation", ["insert", "update", "progress"])
async def test_pending_and_manual_versions_cannot_evade_generator(schema_db, value, operation):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent)
    if operation == "insert":
        row.version = value
        with pytest.raises(PersistenceError):
            await save(schema_db, row)
    else:
        await save(schema_db, row)
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                loaded = await session.get(type(row), row.id)
                loaded.version = value
                if operation == "progress":
                    loaded.state, loaded.first_requested_at = "COLLECTING", NOW


@pytest.mark.asyncio
async def test_optimistic_stale_writer_and_pending_noop_version_trap(schema_db):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            (await session.get(type(row), row.id)).updated_at += timedelta(seconds=1)
    async with AsyncSession(schema_db.engine, expire_on_commit=False) as stale_session:
        stale = await stale_session.get(type(row), row.id)
        await stale_session.commit()
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            loaded.state, loaded.first_requested_at = "COLLECTING", NOW
        stale.state, stale.first_requested_at = "COLLECTING", NOW
        with pytest.raises(StaleDataError):
            await stale_session.commit()
        await stale_session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,expected", [("orm", 1), ("core", 9)])
async def test_nonpending_historical_version_matches_d3a(schema_db, channel, expected):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent, **progress(), version=9)
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        assert (await session.get(type(row), row.id)).version == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("field", INTEGERS)
@pytest.mark.parametrize("value", [True, 1.0, "1", 2**63])
async def test_exact_integer_admission(schema_db, field, value):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent, **progress())
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", TIMES)
async def test_naive_datetime_admission(schema_db, field):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent, **(progress() | {field: NOW.replace(tzinfo=None)}))
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", [*INTEGERS, *TIMES])
async def test_corrupt_typed_reads_fail_closed(schema_db, channel, field):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent, **progress())
    await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.execute(text("PRAGMA ignore_check_constraints=ON"))
            await session.execute(
                text(f"UPDATE reconciliation_resources SET {field}=:v WHERE id=:id"),
                {
                    "v": 1.5 if field in INTEGERS else "2026-11-04T00:00:00.000000+01:00",
                    "id": row.id.hex,
                },
            )
            if channel == "orm":
                await session.get(type(row), row.id)
            else:
                (await session.execute(select(row.__table__))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("query_sha256", "b" * 64),
        ("resource_kind", "BALANCES"),
        ("query_schema_version", 2),
        ("query_payload", "{}"),
    ],
)
@pytest.mark.parametrize(
    "operation", ["load", "deferred", "refresh", "partial", "expired", "unexpired_partial"]
)
async def test_full_tuple_validation_on_all_orm_load_paths(schema_db, field, value, operation):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent, query=dict(initial_cursor_direction="NONE", product_ids=[1]))
    await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = None
            if operation in {"refresh", "partial", "expired", "unexpired_partial"}:
                loaded = await session.get(type(row), row.id)
                if operation != "unexpired_partial":
                    session.expire(
                        loaded,
                        ["query_payload", "query_sha256", "query_schema_version", "resource_kind"],
                    )
            await session.execute(text("PRAGMA ignore_check_constraints=ON"))
            await session.execute(
                text(f"UPDATE reconciliation_resources SET {field}=:v WHERE id=:id"),
                {"v": value, "id": row.id.hex},
            )
            if operation == "load":
                await session.get(type(row), row.id)
            elif operation == "deferred":
                (await session.scalars(select(type(row)).options(load_only(type(row).id)))).all()
            elif operation == "expired":
                await session.run_sync(lambda _: loaded.query_payload)
            elif operation == "unexpired_partial":
                await session.refresh(loaded, attribute_names=[field])
            else:
                await session.refresh(
                    loaded, attribute_names=["updated_at"] if operation == "partial" else None
                )


@pytest.mark.asyncio
async def test_partial_refresh_preserves_pending_progress_and_does_not_recursive_load(schema_db):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        session.expire(loaded, ["query_payload", "query_sha256", "resource_kind"])
        loaded.state, loaded.first_requested_at = "COLLECTING", NOW
        await session.refresh(loaded, attribute_names=["updated_at"])
        assert loaded.state == "COLLECTING" and loaded.first_requested_at == NOW
        assert "query_payload" not in loaded.__dict__
    async with schema_db.transaction() as session:
        assert (await session.get(type(row), row.id)).version == 2


@pytest.mark.asyncio
async def test_core_standalone_shape_and_cross_column_digest_limitation(schema_db):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent)
    row.query_sha256 = "b" * 64
    await save(schema_db, row, "core")
    async with schema_db.transaction() as session:
        values = (await session.execute(select(row.__table__))).mappings().one()
        assert values["query_sha256"] == "b" * 64
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(row), row.id)
    bad = resource(owner, parent, collection_attempt=2)
    bad.query_payload = '{"api_key":"secret"}'
    with pytest.raises(PersistenceError):
        await save(schema_db, bad, "core")
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.execute(text("UPDATE reconciliation_resources SET query_payload='{}'"))
            (await session.execute(select(row.__table__))).all()


def test_query_constructor_owns_callers_and_rejects_identity_overrides():
    api = schema_api()
    assert hasattr(api, "ReconciliationResource"), "Missing ReconciliationResource export"
    selectors = [2, 1]
    raw = dict(product_ids=selectors, initial_cursor_direction="NONE")
    row = api.ReconciliationResource.from_query(resource_kind="FILLS", query=raw)
    selectors.append(3)
    raw["page_size"] = 10
    assert '"product_ids":[1,2]' in row.query_payload and "page_size" not in row.query_payload
    for field in ("query_payload", "query_sha256"):
        with pytest.raises(PersistenceValueError):
            api.ReconciliationResource.from_query(
                resource_kind="FILLS", query=raw, **{field: "override"}
            )


def test_import_has_no_engine_tasks_files_network_or_orders(tmp_path):
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
from hmt_crypto.persistence.models import ReconciliationResource
assert ReconciliationResource.__tablename__ == 'reconciliation_resources'
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("requested,received,finished", list(product([False, True], repeat=3)))
async def test_exhaustive_state_time_presence_null_matrix(
    schema_db, channel, state, requested, received, finished
):
    owner, parent = await setup(schema_db)
    values = {} if state == "PENDING" else progress(state)
    values.update(
        first_requested_at=NOW if requested else None,
        last_received_at=NOW if received else None,
        finished_at=NOW if finished else None,
        pages_received=1 if received else 0,
        items_received=0,
    )
    row = resource(owner, parent, **values)
    valid = {
        "PENDING": not requested and not received and not finished,
        "COLLECTING": requested and not finished,
        "COLLECTED": requested and received and finished,
        "TRUNCATED": requested and received and finished,
        "FAILED": finished,
        "CANCELLED": finished,
    }[state] and (not received or requested)
    if valid:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field",
    [
        *SCOPE,
        "run_id",
        "resource_kind",
        "collection_attempt",
        "query_schema_version",
        "query_payload",
        "query_sha256",
        "pagination_kind",
        "state",
        "pages_received",
        "items_received",
        "after_cursor_state",
        "before_cursor_state",
        "completion_basis",
        "version",
        "created_at",
        "updated_at",
    ],
)
async def test_required_columns_cannot_hide_sql_unknown_with_null(schema_db, channel, field):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent)
    setattr(row, field, None)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("query_sha256", "b" * 64),
        ("resource_kind", "BALANCES"),
        ("query_schema_version", 2),
        ("query_payload", "{}"),
    ],
)
async def test_orm_insert_checks_complete_query_tuple(schema_db, field, value):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent, query=dict(initial_cursor_direction="NONE", product_ids=[1]))
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["load", "deferred", "refresh", "partial"])
async def test_valid_orm_load_paths_validate_without_recursive_loading(schema_db, operation):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        if operation == "deferred":
            loaded = (
                await session.scalars(select(type(row)).options(load_only(type(row).id)))
            ).one()
            assert "query_payload" not in loaded.__dict__
        else:
            loaded = await session.get(type(row), row.id)
            if operation != "load":
                await session.refresh(
                    loaded, attribute_names=["query_sha256"] if operation == "partial" else None
                )
            assert loaded.query_payload == row.query_payload


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["COLLECTED", "TRUNCATED", "FAILED", "CANCELLED"])
@pytest.mark.parametrize(
    "field,value",
    [("items_received", 1), ("reported_total", 0), ("updated_at", NOW + timedelta(seconds=3))],
)
async def test_terminal_freeze_covers_outcome_and_time_edits(schema_db, state, field, value):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent, **progress(state))
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            session.expire(loaded, ["state"])
            setattr(loaded, field, value)


@pytest.mark.asyncio
async def test_partial_refresh_validates_instance_tuple_without_rereading_loaded_siblings(
    schema_db,
):
    owner, parent = await setup(schema_db)
    row = resource(owner, parent)
    await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            await session.execute(
                text("UPDATE reconciliation_resources SET query_sha256=:v WHERE id=:id"),
                {"v": "b" * 64, "id": row.id.hex},
            )
            # The current instance still holds a coherent, validated query tuple.
            # Refreshing an unrelated field does not promise a persisted snapshot.
            await session.refresh(loaded, attribute_names=["updated_at"])
            assert loaded.query_sha256 == row.query_sha256
            # Refreshing the modified sibling now exposes the inconsistency.
            await session.refresh(loaded, attribute_names=["query_sha256"])


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["COLLECTED", "TRUNCATED", "FAILED", "CANCELLED"])
async def test_resource_outcomes_leave_populated_execution_and_protection_truth_unchanged(
    execution_db, state
):
    db, owner, _, command, order, *_ = execution_db
    requested = intent(owner, command, order)
    await save(db, requested)
    auth = authorization(owner, requested)
    await save(db, auth)
    await save(db, protection(owner, auth))
    await save(db, fill(owner))
    recovery = incarnation(owner)
    await save(db, recovery)
    parent = run(owner, recovery)
    await save(db, parent)
    tables = [
        table
        for table in schema_api().metadata.tables.values()
        if table.name != "reconciliation_resources"
    ]
    async with db.transaction() as session:
        before = {table.name: (await session.execute(select(table))).all() for table in tables}
        parent_bytes = (await session.execute(text("SELECT * FROM reconciliation_runs"))).all()
    row = resource(owner, parent)
    await save(db, row)
    async with db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        for key, value in progress(state).items():
            setattr(loaded, key, value)
    async with db.transaction() as session:
        assert before == {
            table.name: (await session.execute(select(table))).all() for table in tables
        }
        assert (
            parent_bytes == (await session.execute(text("SELECT * FROM reconciliation_runs"))).all()
        )
        stored_parent = await session.get(type(parent), parent.id)
        assert stored_parent.state == "PENDING" and stored_parent.completeness == "UNKNOWN"
        assert stored_parent.version == 1
