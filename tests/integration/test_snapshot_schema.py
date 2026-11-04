"""Offline normalized position/balance observation persistence boundaries."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from schema_test_support import NOW, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import UniqueConstraint, delete, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import load_only
from sqlalchemy.schema import CreateIndex, CreateTable
from test_coordination_schema import SCOPE, graph, save
from test_reconciliation_resource_schema import progress, resource
from test_reconciliation_run_schema import run

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError


async def parent(db, kind="MARGINED_POSITIONS", *, attempt=1):
    owner, recovery = await graph(db)
    reconciliation = run(owner, recovery)
    await save(db, reconciliation)
    query = dict(
        initial_cursor_direction="NONE",
        **({"product_ids": [1101]} if kind == "LATEST_POSITION" else {}),
    )
    collection = resource(
        owner,
        reconciliation,
        resource_kind=kind,
        collection_attempt=attempt,
        pagination_kind="NONE",
        query=query,
    )
    await save(db, collection)
    return owner, reconciliation, collection


def position(owner, collection, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        resource_id=collection.id,
        run_id=collection.run_id,
        resource_kind=collection.resource_kind,
        product_id=1101,
        signed_contracts=-2,
        entry_price=Decimal("100.5"),
        mark_price=Decimal("101.25"),
        liquidation_price=None,
        margin=Decimal("1.5"),
        unrealized_pnl=Decimal("-0.25"),
        margin_mode="isolated",
        exchange_updated_at=NOW - timedelta(days=2),
        requested_at=NOW,
        received_at=NOW + timedelta(seconds=1),
        recorded_at=NOW + timedelta(seconds=2),
    )
    if collection.resource_kind == "LATEST_POSITION":
        values.update(
            mark_price=None,
            liquidation_price=None,
            margin=None,
            unrealized_pnl=None,
            margin_mode=None,
            exchange_updated_at=None,
        )
    values.update(changes)
    return schema_api().PositionSnapshot.from_observation(**values)


def balance(owner, collection, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        resource_id=collection.id,
        run_id=collection.run_id,
        resource_kind=collection.resource_kind,
        asset_id=1,
        asset_symbol="USDT",
        available_balance=Decimal("-1.25"),
        balance=Decimal("0"),
        blocked_margin=Decimal("2.5"),
        requested_at=NOW,
        received_at=NOW + timedelta(seconds=1),
        recorded_at=NOW + timedelta(seconds=2),
    )
    values.update(changes)
    return schema_api().BalanceSnapshot.from_observation(**values)


def test_exact_snapshot_tables_keys_and_complete_postgres_ddl():
    api = schema_api()
    assert hasattr(api, "PositionSnapshot") and hasattr(api, "BalanceSnapshot")
    position_table = api.PositionSnapshot.__table__
    balance_table = api.BalanceSnapshot.__table__
    assert set(position_table.c.keys()) == {
        "id",
        *SCOPE,
        "resource_id",
        "run_id",
        "resource_kind",
        "product_id",
        "signed_contracts",
        "entry_price",
        "mark_price",
        "liquidation_price",
        "margin",
        "unrealized_pnl",
        "margin_mode",
        "exchange_updated_at",
        "observation_schema_version",
        "observation_sha256",
        "requested_at",
        "received_at",
        "recorded_at",
    }
    assert set(balance_table.c.keys()) == {
        "id",
        *SCOPE,
        "resource_id",
        "run_id",
        "resource_kind",
        "asset_id",
        "asset_symbol",
        "available_balance",
        "balance",
        "blocked_margin",
        "observation_schema_version",
        "observation_sha256",
        "requested_at",
        "received_at",
        "recorded_at",
    }
    assert {
        tuple(column.name for column in key.columns)
        for key in position_table.constraints
        if isinstance(key, UniqueConstraint)
    } == {
        ("id", *SCOPE),
        ("id", *SCOPE, "product_id"),
        ("resource_id", *SCOPE, "run_id", "resource_kind", "product_id"),
    }
    assert {
        tuple(column.name for column in key.columns)
        for key in balance_table.constraints
        if isinstance(key, UniqueConstraint)
    } == {
        ("id", *SCOPE),
        ("resource_id", *SCOPE, "run_id", "resource_kind", "asset_id"),
        ("resource_id", *SCOPE, "run_id", "resource_kind", "asset_symbol"),
    }
    expected = {
        "accounts": (SCOPE, ("id", *SCOPE[1:])),
        "reconciliation_resources": (
            ("resource_id", *SCOPE, "run_id", "resource_kind"),
            ("id", *SCOPE, "run_id", "resource_kind"),
        ),
    }
    for table in (position_table, balance_table):
        assert {fk.referred_table.name for fk in table.foreign_key_constraints} == set(expected)
        indexes = {tuple(column.name for column in index.columns) for index in table.indexes}
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "UUID" in ddl and "BIGINT" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl
        assert "NUMERIC(38, 18)" in ddl
        for constraint in table.constraints:
            assert constraint.name and constraint.name in ddl
        for fk in table.foreign_key_constraints:
            source, target = expected[fk.referred_table.name]
            assert tuple(fk.column_keys) == source
            assert tuple(element.column.name for element in fk.elements) == target
            assert fk.ondelete == fk.onupdate == "RESTRICT" and source in indexes
        for index in table.indexes:
            assert index.name in str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    for prior in api.metadata.tables.values():
        if prior not in {position_table, balance_table}:
            assert not {
                "position_snapshots",
                "balance_snapshots",
            } & {fk.referred_table.name for fk in prior.foreign_key_constraints}


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "snapshot_kind,parent_kind",
    [
        ("position", "MARGINED_POSITIONS"),
        ("position", "LATEST_POSITION"),
        ("balance", "BALANCES"),
    ],
)
async def test_unknown_positive_ids_are_admitted_without_catalogue_rows(
    schema_db, channel, snapshot_kind, parent_kind
):
    owner, _, collection = await parent(schema_db, parent_kind)
    row = (
        position(owner, collection, product_id=9_999_999)
        if snapshot_kind == "position"
        else balance(owner, collection, asset_id=9_999_999, asset_symbol="UNKNOWN")
    )
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert getattr(loaded, "product_id", None) == getattr(row, "product_id", None)
        assert getattr(loaded, "asset_id", None) == getattr(row, "asset_id", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["position", "balance"])
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", [*SCOPE, "resource_id", "run_id", "resource_kind"])
async def test_every_parent_scope_and_kind_dimension_is_enforced(
    schema_db, snapshot_kind, channel, field
):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = position(owner, collection) if snapshot_kind == "position" else balance(owner, collection)
    setattr(
        row,
        field,
        {
            "account_id": uuid4(),
            "region": "global",
            "environment": "prod",
            "mode": "BACKTEST",
            "resource_id": uuid4(),
            "run_id": uuid4(),
            "resource_kind": "LATEST_POSITION" if snapshot_kind == "position" else "FILLS",
        }[field],
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("mark_price", Decimal("0")),
        ("liquidation_price", Decimal("-1")),
        ("margin", Decimal("2")),
        ("unrealized_pnl", Decimal("3")),
        ("margin_mode", "portfolio"),
        ("exchange_updated_at", NOW),
    ],
)
async def test_latest_position_rejects_every_rich_margined_field(schema_db, channel, field, value):
    owner, _, collection = await parent(schema_db, "LATEST_POSITION")
    with pytest.raises(PersistenceError):
        await save(schema_db, position(owner, collection, **{field: value}), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field", ["entry_price", "mark_price", "liquidation_price", "margin", "unrealized_pnl"]
)
@pytest.mark.parametrize("value", [None, Decimal("0"), Decimal("-1.25")])
async def test_margined_nullable_decimals_preserve_null_zero_and_negative(
    schema_db, channel, field, value
):
    owner, _, collection = await parent(schema_db)
    row = position(owner, collection, **{field: value})
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        assert getattr(await session.get(type(row), row.id), field) == value


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("value", [-(2**63), -1, 0, 1, 2**63 - 1])
async def test_signed_contract_boundaries_round_trip(schema_db, channel, value):
    owner, _, collection = await parent(schema_db)
    row = position(owner, collection, signed_contracts=value)
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        assert (await session.get(type(row), row.id)).signed_contracts == value


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_same_product_is_allowed_across_attempts_but_not_within_one(schema_db, channel):
    owner, reconciliation, first = await parent(schema_db)
    second = resource(
        owner,
        reconciliation,
        resource_kind="MARGINED_POSITIONS",
        collection_attempt=2,
        pagination_kind="NONE",
        query=dict(initial_cursor_direction="NONE"),
    )
    await save(schema_db, second)
    await save(schema_db, position(owner, first), channel)
    await save(schema_db, position(owner, second), channel)
    with pytest.raises(PersistenceError):
        await save(schema_db, position(owner, first), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "changes",
    [{"asset_symbol": "USDC"}, {"asset_id": 2}],
)
async def test_balance_asset_id_and_symbol_are_independently_unique_per_resource(
    schema_db, channel, changes
):
    owner, _, collection = await parent(schema_db, "BALANCES")
    await save(schema_db, balance(owner, collection), channel)
    with pytest.raises(PersistenceError):
        await save(schema_db, balance(owner, collection, **changes), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_same_asset_is_allowed_in_a_later_attempt(schema_db, channel):
    owner, reconciliation, first = await parent(schema_db, "BALANCES")
    second = resource(
        owner,
        reconciliation,
        resource_kind="BALANCES",
        collection_attempt=2,
        pagination_kind="NONE",
        query=dict(initial_cursor_direction="NONE"),
    )
    await save(schema_db, second)
    await save(schema_db, balance(owner, first), channel)
    await save(schema_db, balance(owner, second), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("value", ["", " ", "\t", " \t ", "x" * 129, "US\x00DT", 1, True])
async def test_asset_symbol_direct_bind_is_required_bounded_nonblank_text(
    schema_db, channel, value
):
    owner, _, collection = await parent(schema_db, "BALANCES")
    row = balance(owner, collection)
    row.asset_symbol = value
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("value", ["cross", "", "ISOLATED", 1, True])
async def test_margin_mode_direct_bind_is_nullable_or_allowlisted(schema_db, channel, value):
    owner, _, collection = await parent(schema_db)
    row = position(owner, collection)
    row.margin_mode = value
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot_kind,field",
    [("position", "product_id"), ("position", "signed_contracts"), ("balance", "asset_id")],
)
@pytest.mark.parametrize("value", [True, 1.0, "1", 2**63])
async def test_exact_integer_admission(schema_db, snapshot_kind, field, value):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    with pytest.raises(PersistenceError):
        row = (
            position(owner, collection, **{field: value})
            if snapshot_kind == "position"
            else balance(owner, collection, **{field: value})
        )
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["available_balance", "balance", "blocked_margin"])
@pytest.mark.parametrize(
    "value",
    [True, 1.5, "1.5", Decimal("NaN"), Decimal("Infinity"), Decimal("1e20"), Decimal("1e-19")],
)
async def test_balance_decimals_reject_lossy_or_out_of_policy_values(schema_db, field, value):
    owner, _, collection = await parent(schema_db, "BALANCES")
    with pytest.raises(PersistenceError):
        await save(schema_db, balance(owner, collection, **{field: value}))


@pytest.mark.asyncio
async def test_exact_decimal_and_timezone_round_trip_with_exchange_clock_skew(schema_db):
    owner, _, collection = await parent(schema_db)
    offset = timezone(timedelta(hours=5, minutes=30))
    row = position(
        owner,
        collection,
        entry_price=Decimal("12345678901234567890.123456789012345678"),
        exchange_updated_at=datetime(2000, 1, 1, 5, 30, 0, 123456, tzinfo=offset),
        requested_at=datetime(2026, 11, 4, 5, 30, tzinfo=offset),
        received_at=datetime(2026, 11, 4, 5, 30, 1, tzinfo=offset),
        recorded_at=datetime(2026, 11, 4, 5, 30, 2, tzinfo=offset),
    )
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.entry_price == Decimal("12345678901234567890.123456789012345678")
        assert loaded.exchange_updated_at == datetime(2000, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
        assert loaded.requested_at == NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["position", "balance"])
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_at", NOW + timedelta(seconds=2)),
        ("received_at", NOW - timedelta(seconds=1)),
        ("recorded_at", NOW),
    ],
)
async def test_local_observation_times_are_ordered(schema_db, snapshot_kind, channel, field, value):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = (
        position(owner, collection, **{field: value})
        if snapshot_kind == "position"
        else balance(owner, collection, **{field: value})
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["position", "balance"])
@pytest.mark.parametrize("field", ["requested_at", "received_at", "recorded_at"])
async def test_naive_local_times_are_rejected(schema_db, snapshot_kind, field):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = (
        position(owner, collection, **{field: NOW.replace(tzinfo=None)})
        if snapshot_kind == "position"
        else balance(owner, collection, **{field: NOW.replace(tzinfo=None)})
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
async def test_naive_exchange_time_is_rejected(schema_db):
    owner, _, collection = await parent(schema_db)
    with pytest.raises(PersistenceError):
        position(owner, collection, exchange_updated_at=NOW.replace(tzinfo=None))


def test_factories_reject_hash_and_version_overrides_without_retaining_callers():
    api = schema_api()
    values = dict(
        product_id=1101,
        signed_contracts=0,
        entry_price=None,
        mark_price=None,
        liquidation_price=None,
        margin=None,
        unrealized_pnl=None,
        margin_mode=None,
        exchange_updated_at=None,
        resource_kind="LATEST_POSITION",
    )
    row = api.PositionSnapshot.from_observation(**values)
    values["signed_contracts"] = 9
    assert row.signed_contracts == 0
    for field, value in (
        ("observation_schema_version", 1),
        ("observation_sha256", "a" * 64),
    ):
        with pytest.raises(PersistenceValueError):
            api.PositionSnapshot.from_observation(**values, **{field: value})


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["position", "balance"])
@pytest.mark.parametrize("change", ["hash", "version", "economic"])
async def test_direct_orm_insert_validates_complete_observation_tuple(
    schema_db, snapshot_kind, change
):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = position(owner, collection) if snapshot_kind == "position" else balance(owner, collection)
    if change == "hash":
        row.observation_sha256 = "b" * 64
    elif change == "version":
        row.observation_schema_version = 2
    elif snapshot_kind == "position":
        row.signed_contracts = 9
    else:
        row.balance = Decimal("9")
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["position", "balance"])
@pytest.mark.parametrize("operation", ["load", "deferred", "refresh", "partial", "expired"])
async def test_corrupt_digest_fails_closed_on_every_orm_load_path(
    schema_db, snapshot_kind, operation
):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = position(owner, collection) if snapshot_kind == "position" else balance(owner, collection)
    await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = None
            if operation in {"refresh", "partial", "expired"}:
                loaded = await session.get(type(row), row.id)
                session.expire(loaded, ["observation_sha256"])
            await session.execute(
                text(f"UPDATE {row.__tablename__} SET observation_sha256=:v WHERE id=:id"),
                {"v": "b" * 64, "id": row.id.hex},
            )
            if operation == "load":
                await session.get(type(row), row.id)
            elif operation == "deferred":
                (await session.scalars(select(type(row)).options(load_only(type(row).id)))).all()
            elif operation == "expired":
                await session.run_sync(lambda _: loaded.observation_sha256)
            else:
                await session.refresh(
                    loaded,
                    attribute_names=["recorded_at"] if operation == "partial" else None,
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["position", "balance"])
async def test_core_cross_column_digest_limitation_is_explicit(schema_db, snapshot_kind):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = position(owner, collection) if snapshot_kind == "position" else balance(owner, collection)
    row.observation_sha256 = "b" * 64
    await save(schema_db, row, "core")
    async with schema_db.transaction() as session:
        values = (await session.execute(select(row.__table__))).mappings().one()
        assert values["observation_sha256"] == "b" * 64
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(row), row.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot_kind,field,value",
    [
        ("position", "product_id", 1.5),
        ("position", "signed_contracts", 1.5),
        ("position", "entry_price", "01"),
        ("position", "exchange_updated_at", "2026-11-04T00:00:00.000000+01:00"),
        ("balance", "asset_id", 1.5),
        ("balance", "asset_symbol", "US\x00DT"),
        ("balance", "available_balance", "01"),
        ("balance", "requested_at", "2026-11-04T00:00:00.000000+01:00"),
    ],
)
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_corrupt_typed_column_reads_fail_closed(
    schema_db, snapshot_kind, field, value, channel
):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = position(owner, collection) if snapshot_kind == "position" else balance(owner, collection)
    await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.execute(text("PRAGMA ignore_check_constraints=ON"))
            await session.execute(
                text(f"UPDATE {row.__tablename__} SET {field}=:v WHERE id=:id"),
                {"v": value, "id": row.id.hex},
            )
            if channel == "orm":
                await session.get(type(row), row.id)
            else:
                (await session.execute(select(row.__table__))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["position", "balance"])
@pytest.mark.parametrize("operation", ["update", "delete", "expired_update", "deferred_delete"])
async def test_all_orm_mutation_and_deletion_is_denied(schema_db, snapshot_kind, operation):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = position(owner, collection) if snapshot_kind == "position" else balance(owner, collection)
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = (
                (await session.scalars(select(type(row)).options(load_only(type(row).id)))).one()
                if operation == "deferred_delete"
                else await session.get(type(row), row.id)
            )
            if operation == "delete" or operation == "deferred_delete":
                await session.delete(loaded)
            else:
                if operation == "expired_update":
                    session.expire(loaded)
                loaded.recorded_at = NOW + timedelta(seconds=3)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["position", "balance"])
async def test_core_update_and_delete_history_enforcement_is_explicitly_deferred(
    schema_db, snapshot_kind
):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = position(owner, collection) if snapshot_kind == "position" else balance(owner, collection)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        await session.execute(
            row.__table__.update()
            .where(row.__table__.c.id == row.id)
            .values(recorded_at=NOW + timedelta(seconds=3))
        )
        await session.execute(delete(row.__table__).where(row.__table__.c.id == row.id))
    async with schema_db.transaction() as session:
        assert (await session.execute(select(row.__table__))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["position", "balance"])
async def test_success_and_rollback_leave_parent_and_older_truth_unchanged(
    schema_db, snapshot_kind
):
    parent_kind = "MARGINED_POSITIONS" if snapshot_kind == "position" else "BALANCES"
    owner, _, collection = await parent(schema_db, parent_kind)
    row = position(owner, collection) if snapshot_kind == "position" else balance(owner, collection)
    snapshot_tables = {"position_snapshots", "balance_snapshots"}
    prior = [
        table
        for table in schema_api().metadata.tables.values()
        if table.name not in snapshot_tables
    ]
    async with schema_db.transaction() as session:
        before = {table.name: (await session.execute(select(table))).all() for table in prior}
        parent_bytes = (await session.execute(text("SELECT * FROM reconciliation_resources"))).all()
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            duplicate = (
                position(owner, collection)
                if snapshot_kind == "position"
                else balance(owner, collection)
            )
            session.add(duplicate)
            session.add(
                position(owner, collection)
                if snapshot_kind == "position"
                else balance(owner, collection)
            )
    async with schema_db.transaction() as session:
        assert before == {
            table.name: (await session.execute(select(table))).all() for table in prior
        }
        assert (
            parent_bytes
            == (await session.execute(text("SELECT * FROM reconciliation_resources"))).all()
        )
        assert len((await session.scalars(select(type(row)))).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,model_name",
    [
        ("BALANCES", "BalanceSnapshot"),
        ("LATEST_POSITION", "PositionSnapshot"),
        ("MARGINED_POSITIONS", "PositionSnapshot"),
    ],
)
async def test_completed_empty_response_is_zero_child_rows(schema_db, kind, model_name):
    owner, recovery = await graph(schema_db)
    reconciliation = run(owner, recovery)
    await save(schema_db, reconciliation)
    query = dict(
        initial_cursor_direction="NONE",
        **({"product_ids": [1101]} if kind == "LATEST_POSITION" else {}),
    )
    collection = resource(
        owner,
        reconciliation,
        resource_kind=kind,
        pagination_kind="NONE",
        query=query,
        **progress(
            "COLLECTED",
            completion_basis="SINGLE_RESPONSE",
            after_cursor_state="NOT_APPLICABLE",
            before_cursor_state="NOT_APPLICABLE",
        ),
    )
    await save(schema_db, collection)
    async with schema_db.transaction() as session:
        assert (await session.scalars(select(getattr(schema_api(), model_name)))).all() == []


@pytest.mark.asyncio
async def test_populated_snapshot_tables_drop_and_recreate(schema_db):
    owner, reconciliation, positions = await parent(schema_db)
    await save(schema_db, position(owner, positions))
    balances = resource(
        owner,
        reconciliation,
        resource_kind="BALANCES",
        collection_attempt=1,
        pagination_kind="NONE",
        query=dict(initial_cursor_direction="NONE"),
    )
    await save(schema_db, balances)
    await save(schema_db, balance(owner, balances))
    tables = [schema_api().BalanceSnapshot.__table__, schema_api().PositionSnapshot.__table__]
    async with schema_db.engine.begin() as connection:
        for table in tables:
            await connection.run_sync(table.drop)
        for table in reversed(tables):
            await connection.run_sync(table.create)
    async with schema_db.transaction() as session:
        assert (await session.execute(select(schema_api().PositionSnapshot))).all() == []
        assert (await session.execute(select(schema_api().BalanceSnapshot))).all() == []


def test_snapshot_import_has_no_engine_tasks_files_network_or_orders(tmp_path):
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
from hmt_crypto.persistence.models import PositionSnapshot, BalanceSnapshot
assert PositionSnapshot.__tablename__ == 'position_snapshots'
assert BalanceSnapshot.__tablename__ == 'balance_snapshots'
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
