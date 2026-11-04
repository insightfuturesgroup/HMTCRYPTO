"""Offline PnL calculation evidence; no formula, readiness, or authorization."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from schema_test_support import HASH, NOW, account, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import UniqueConstraint, delete, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import load_only
from sqlalchemy.schema import CreateIndex, CreateTable
from test_coordination_schema import SCOPE, graph, incarnation, save
from test_reconciliation_run_schema import run

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError

MEASURES = (
    "baseline_equity",
    "observed_equity",
    "reconciled_realized_pnl",
    "mark_to_market_unrealized_pnl",
    "equity_drawdown_loss",
    "unresolved_loss_reserve",
    "conservative_daily_loss",
)
INTEGERS = ("reconciliation_version", "reference_asset_id", "calculation_version")
TIMES = ("window_start_at", "window_end_at", "calculated_at", "recorded_at")


async def parent(db, owner=None, **changes):
    if owner is None:
        owner, recovery = await graph(db)
    else:
        recovery = incarnation(owner)
        await save(db, owner)
        await save(db, recovery)
    reconciliation = run(owner, recovery, **changes)
    await save(db, reconciliation)
    return owner, reconciliation


def pnl(owner, reconciliation, completeness="COMPLETE", **changes):
    measures = {
        "baseline_equity": Decimal("100.5"),
        "observed_equity": Decimal("95"),
        "reconciled_realized_pnl": Decimal("-2"),
        "mark_to_market_unrealized_pnl": Decimal("-1.5"),
        "equity_drawdown_loss": Decimal("5.5"),
        "unresolved_loss_reserve": Decimal("1.25"),
        "conservative_daily_loss": Decimal("6.75"),
    }
    if completeness == "UNKNOWN":
        measures = {field: None for field in MEASURES}
    elif completeness == "PARTIAL":
        measures = {field: None for field in MEASURES} | {"baseline_equity": Decimal("0")}
    values = dict(
        id=uuid4(),
        **scope(owner),
        snapshot_key="pnl-1",
        run_id=reconciliation.id,
        reconciliation_version=reconciliation.version,
        window_start_at=NOW,
        window_end_at=NOW + timedelta(hours=1),
        calculated_at=NOW + timedelta(hours=2),
        recorded_at=NOW + timedelta(hours=3),
        reference_asset_id=1,
        reference_asset_symbol="USDT",
        completeness=completeness,
        **measures,
        calculation_version=2,
        calculation_policy_sha256=HASH,
        input_set_sha256="b" * 64,
    )
    values.update(changes)
    return schema_api().PnlSnapshot.from_evidence(**values)


def test_factory_owns_values_and_rejects_version_payload_hash_or_secret_overrides():
    api = schema_api()
    values = dict(
        snapshot_key="owned",
        run_id=uuid4(),
        reconciliation_version=1,
        window_start_at=NOW,
        window_end_at=NOW,
        reference_asset_id=1,
        reference_asset_symbol="USDT",
        completeness="UNKNOWN",
        **{field: None for field in MEASURES},
        calculation_version=1,
        calculation_policy_sha256=HASH,
        input_set_sha256="b" * 64,
    )
    row = api.PnlSnapshot.from_evidence(**values)
    values["reference_asset_symbol"] = "BTC"
    assert row.reference_asset_symbol == "USDT"
    for field, value in (
        ("evidence_schema_version", 1),
        ("evidence_payload", "{}"),
        ("evidence_sha256", HASH),
        ("api_secret", "SECRET-PNL-MARKER"),
    ):
        with pytest.raises(PersistenceValueError) as exc:
            api.PnlSnapshot.from_evidence(**values, **{field: value})
        assert "SECRET-PNL-MARKER" not in str(exc.value)
        assert "SECRET-PNL-MARKER" not in repr(exc.value)


def test_exact_table_keys_fks_indexes_and_postgres_ddl():
    api = schema_api()
    assert hasattr(api, "PnlSnapshot"), "Missing PnlSnapshot export"
    table = api.PnlSnapshot.__table__
    assert set(table.c.keys()) == {
        "id",
        *SCOPE,
        "snapshot_key",
        "run_id",
        "reconciliation_version",
        "window_start_at",
        "window_end_at",
        "calculated_at",
        "recorded_at",
        "reference_asset_id",
        "reference_asset_symbol",
        "completeness",
        *MEASURES,
        "calculation_version",
        "calculation_policy_sha256",
        "input_set_sha256",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
    }
    assert {
        tuple(column.name for column in key.columns)
        for key in table.constraints
        if isinstance(key, UniqueConstraint)
    } == {("id", *SCOPE), (*SCOPE, "snapshot_key")}
    expected = {
        "accounts": (SCOPE, ("id", *SCOPE[1:])),
        "reconciliation_runs": (("run_id", *SCOPE), ("id", *SCOPE)),
    }
    assert {fk.referred_table.name for fk in table.foreign_key_constraints} == set(expected)
    indexes = {tuple(column.name for column in index.columns) for index in table.indexes}
    assert (*SCOPE, "window_start_at", "window_end_at") in indexes
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
        if prior is not table and prior.name not in {"risk_state", "risk_halt_events"}:
            assert "pnl_snapshots" not in {
                fk.referred_table.name for fk in prior.foreign_key_constraints
            }


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", [*SCOPE, "run_id"])
async def test_every_parent_scope_and_run_dimension_is_enforced(schema_db, channel, field):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    setattr(
        row,
        field,
        {
            "account_id": uuid4(),
            "region": "global",
            "environment": "prod",
            "mode": "BACKTEST",
            "run_id": uuid4(),
        }[field],
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_snapshot_key_is_scoped_and_distinct_recalculations_are_admitted(schema_db, channel):
    owner, reconciliation = await parent(schema_db)
    await save(schema_db, pnl(owner, reconciliation), channel)
    with pytest.raises(PersistenceError):
        await save(schema_db, pnl(owner, reconciliation), channel)
    await save(
        schema_db,
        pnl(
            owner,
            reconciliation,
            snapshot_key="pnl-2",
            input_set_sha256="c" * 64,
        ),
        channel,
    )


@pytest.mark.asyncio
async def test_same_snapshot_key_is_admitted_in_independent_scope(schema_db):
    first_owner, first_run = await parent(schema_db)
    second_owner = account(profile_name="second", region="global")
    second_owner, second_run = await parent(schema_db, second_owner)
    await save(schema_db, pnl(first_owner, first_run))
    await save(schema_db, pnl(second_owner, second_run))


@pytest.mark.asyncio
async def test_unknown_asset_is_admitted_without_catalogue_membership(schema_db):
    owner, reconciliation = await parent(schema_db)
    row = pnl(
        owner,
        reconciliation,
        reference_asset_id=9_999_999,
        reference_asset_symbol="UNKNOWN",
    )
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert (loaded.reference_asset_id, loaded.reference_asset_symbol) == (
            9_999_999,
            "UNKNOWN",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", INTEGERS)
@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "1", 2**63])
async def test_exact_positive_integer_admission(schema_db, channel, field, value):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["snapshot_key", "reference_asset_symbol"])
@pytest.mark.parametrize("value", [None, "", " ", "\t", "x" * 129, "bad\x00text", 1, True])
async def test_required_bounded_exact_text(schema_db, channel, field, value):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "completeness,changes,valid",
    [
        ("UNKNOWN", {field: None for field in MEASURES}, True),
        ("UNKNOWN", {field: None for field in MEASURES} | {"baseline_equity": Decimal("0")}, False),
        ("PARTIAL", {field: None for field in MEASURES} | {"baseline_equity": Decimal("0")}, True),
        ("PARTIAL", {field: None for field in MEASURES}, False),
        (
            "PARTIAL",
            {field: None for field in MEASURES}
            | {"baseline_equity": Decimal("0"), "conservative_daily_loss": Decimal("0")},
            False,
        ),
        ("COMPLETE", {}, True),
        ("COMPLETE", {"unresolved_loss_reserve": None}, False),
    ],
)
async def test_completeness_measure_sql_null_shapes(
    schema_db, channel, completeness, changes, valid
):
    owner, reconciliation = await parent(schema_db)
    if valid:
        row = pnl(owner, reconciliation, completeness, **changes)
        await save(schema_db, row, channel)
    else:
        row = pnl(owner, reconciliation)
        row.completeness = completeness
        for field, value in changes.items():
            setattr(row, field, value)
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field", ["equity_drawdown_loss", "unresolved_loss_reserve", "conservative_daily_loss"]
)
async def test_loss_measures_cannot_be_negative(schema_db, channel, field):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    setattr(row, field, Decimal("-0.0001"))
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", MEASURES)
@pytest.mark.parametrize(
    "value",
    [True, 1.25, "1.25", Decimal("NaN"), Decimal("Infinity"), Decimal("1e20"), Decimal("1e-19")],
)
async def test_decimal_values_reject_loss_or_coercion(schema_db, channel, field, value):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
async def test_signed_values_zero_and_timezone_normalization_round_trip(schema_db):
    owner, reconciliation = await parent(schema_db)
    offset = timezone(timedelta(hours=5, minutes=30))
    row = pnl(
        owner,
        reconciliation,
        baseline_equity=Decimal("-1.125000000000000000"),
        observed_equity=Decimal("0.000000000000000001"),
        reconciled_realized_pnl=Decimal("2.25"),
        mark_to_market_unrealized_pnl=Decimal("-3.5"),
        equity_drawdown_loss=Decimal("0.000000000000000001"),
        unresolved_loss_reserve=Decimal("0.25"),
        conservative_daily_loss=Decimal("0.250000000000000000"),
        window_start_at=datetime(2026, 11, 4, 5, 30, tzinfo=offset),
        window_end_at=datetime(2026, 11, 4, 5, 30, tzinfo=offset),
        calculated_at=datetime(2026, 11, 4, 5, 30, tzinfo=offset),
        recorded_at=datetime(2026, 11, 4, 5, 30, tzinfo=offset),
    )
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert tuple(getattr(loaded, field) for field in MEASURES) == (
            Decimal("-1.125"),
            Decimal("0.000000000000000001"),
            Decimal("2.25"),
            Decimal("-3.5"),
            Decimal("0.000000000000000001"),
            Decimal("0.25"),
            Decimal("0.25"),
        )
        assert loaded.window_start_at == datetime(2026, 11, 4, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("window_start_at", NOW + timedelta(hours=1, microseconds=1)),
        ("window_end_at", NOW - timedelta(microseconds=1)),
        ("calculated_at", NOW + timedelta(minutes=59)),
        ("recorded_at", NOW + timedelta(hours=1, minutes=59)),
    ],
)
async def test_all_time_orderings_are_enforced(schema_db, channel, field, value):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", TIMES)
async def test_naive_datetime_is_rejected(schema_db, field):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    setattr(row, field, NOW.replace(tzinfo=None))
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["payload", "hash", "version", "economic"])
async def test_direct_orm_insert_validates_complete_evidence_tuple(schema_db, change):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    if change == "payload":
        row.evidence_payload = "{}"
    elif change == "hash":
        row.evidence_sha256 = "c" * 64
    elif change == "version":
        row.evidence_schema_version = 2
    else:
        row.baseline_equity = Decimal("999")
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["load", "deferred", "refresh", "partial", "expired"])
async def test_corrupt_digest_fails_closed_on_every_orm_load_path(schema_db, operation):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = None
            if operation in {"refresh", "partial", "expired"}:
                loaded = await session.get(type(row), row.id)
                session.expire(loaded, ["evidence_sha256"])
            await session.execute(
                text("UPDATE pnl_snapshots SET evidence_sha256=:v WHERE id=:id"),
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
async def test_core_standalone_payload_and_cross_column_digest_limitation(schema_db):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    row.evidence_sha256 = "c" * 64
    await save(schema_db, row, "core")
    async with schema_db.transaction() as session:
        values = (await session.execute(select(row.__table__))).mappings().one()
        assert values["evidence_sha256"] == "c" * 64
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(row), row.id)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.execute(text("UPDATE pnl_snapshots SET evidence_payload='{}'"))
            (await session.execute(select(row.__table__))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("reconciliation_version", 1.5),
        ("reference_asset_id", 1.5),
        ("calculation_version", 1.5),
        ("reference_asset_symbol", "US\x00DT"),
        ("snapshot_key", "bad\x00key"),
        *[(field, "01") for field in MEASURES],
        ("window_start_at", "2026-11-04T00:00:00.000000+01:00"),
    ],
)
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_corrupt_typed_column_reads_fail_closed(schema_db, field, value, channel):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.execute(text("PRAGMA ignore_check_constraints=ON"))
            await session.execute(
                text(f"UPDATE pnl_snapshots SET {field}=:v WHERE id=:id"),
                {"v": value, "id": row.id.hex},
            )
            if channel == "orm":
                await session.get(type(row), row.id)
            else:
                (await session.execute(select(row.__table__))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize("completeness", ["UNKNOWN", "PARTIAL", "COMPLETE"])
@pytest.mark.parametrize("operation", ["update", "delete", "expired_update", "deferred_delete"])
async def test_all_orm_mutation_and_delete_is_denied(schema_db, operation, completeness):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation, completeness)
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
                loaded.recorded_at = NOW + timedelta(hours=4)


@pytest.mark.asyncio
async def test_core_update_delete_append_only_enforcement_is_explicitly_deferred(schema_db):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        await session.execute(
            row.__table__.update()
            .where(row.__table__.c.id == row.id)
            .values(recorded_at=NOW + timedelta(hours=4))
        )
        await session.execute(delete(row.__table__).where(row.__table__.c.id == row.id))
    async with schema_db.transaction() as session:
        assert (await session.execute(select(row.__table__))).all() == []


@pytest.mark.asyncio
async def test_current_parent_state_and_version_are_not_authority_or_mutated(schema_db):
    owner, reconciliation = await parent(schema_db)
    async with schema_db.transaction() as session:
        current = await session.get(type(reconciliation), reconciliation.id)
        current.state = "COLLECTING"
        current.started_at = NOW
        current.updated_at = NOW + timedelta(seconds=3)
    row = pnl(owner, reconciliation, reconciliation_version=1)
    async with schema_db.transaction() as session:
        before = (await session.execute(select(type(reconciliation).__table__))).all()
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        assert before == (await session.execute(select(type(reconciliation).__table__))).all()
        current = await session.get(type(reconciliation), reconciliation.id)
        assert current.state == "COLLECTING" and current.version == 2
        assert (await session.get(type(row), row.id)).reconciliation_version == 1


@pytest.mark.asyncio
async def test_success_and_rollback_leave_parent_and_prior_truth_unchanged(schema_db):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    prior = [
        table for table in schema_api().metadata.tables.values() if table.name != "pnl_snapshots"
    ]
    async with schema_db.transaction() as session:
        before = {table.name: (await session.execute(select(table))).all() for table in prior}
    await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(pnl(owner, reconciliation, snapshot_key="collision"))
            session.add(pnl(owner, reconciliation, snapshot_key="collision"))
    async with schema_db.transaction() as session:
        assert before == {
            table.name: (await session.execute(select(table))).all() for table in prior
        }
        assert len((await session.scalars(select(type(row)))).all()) == 1


@pytest.mark.asyncio
async def test_populated_table_drop_and_recreate(schema_db):
    owner, reconciliation = await parent(schema_db)
    row = pnl(owner, reconciliation)
    await save(schema_db, row)
    table = row.__table__
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(table.drop)
        await connection.run_sync(table.create)
    async with schema_db.transaction() as session:
        assert (await session.execute(select(table))).all() == []


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
from hmt_crypto.persistence.models import PnlSnapshot
assert PnlSnapshot.__tablename__ == 'pnl_snapshots'
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
