"""Offline fill evidence and authoritative association acceptance boundaries."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from execution_test_support import execution_db as execution_db
from execution_test_support import order
from schema_test_support import NOW, account, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import MetaData, UniqueConstraint, insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from hmt_crypto.persistence.errors import PersistenceError


def fill(owner, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        exchange_fill_id="fill-1",
        exchange_order_reference=None,
        product_id=1101,
        size=Decimal("0.125"),
        price=None,
        side="UNKNOWN",
        fill_type="UNKNOWN",
        notional=None,
        commission=None,
        settling_asset_id=None,
        settling_asset_symbol=None,
        source="VENUE",
        occurred_at=None,
        requested_at=NOW,
        received_at=NOW,
        recorded_at=NOW,
        evidence={"role": "UNKNOWN"},
    )
    values.update(changes)
    return schema_api().Fill.from_evidence(**values)


def link(owner, observation, local_order, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        fill_id=observation.id,
        order_id=local_order.id,
        product_id=observation.product_id,
        source="RECONCILIATION",
        recorded_at=NOW,
    )
    values.update(changes)
    return schema_api().FillOrderLink(**values)


async def save(db, row, channel="orm"):
    async with db.transaction() as session:
        if channel == "orm":
            session.add(row)
        else:
            values = {c.name: getattr(row, c.name) for c in row.__table__.columns}
            await session.execute(insert(row.__table__).values(**values))


def test_fill_schema_adds_only_two_tables_with_no_direct_order_foreign_key():
    api = schema_api()
    baseline = {
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
        "exit_intents",
        "protection_authorizations",
        "position_protections",
        "position_protection_orders",
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
        "risk_reservations",
        "risk_rate_consumptions",
        "api_audit_events",
    }
    assert {table.name for table in api.metadata.tables.values()} - baseline == {
        "fills",
        "fill_order_links",
    }, "Immutable fill evidence and authoritative association tables are missing"
    assert "order_id" not in api.Fill.__table__.c
    assert {fk.referred_table.name for fk in api.Fill.__table__.foreign_key_constraints} == {
        "accounts"
    }


def test_fill_postgres_ddl_has_exact_product_qualified_link_keys():
    api = schema_api()
    metadata = MetaData(schema="hmt_crypto", naming_convention=api.metadata.naming_convention)
    for table in api.metadata.tables.values():
        table.to_metadata(metadata)
    candidate = ("id", "account_id", "region", "environment", "mode", "product_id")
    for name in ("fills", "orders"):
        assert candidate in {
            tuple(c.name for c in key.columns)
            for key in metadata.tables[f"hmt_crypto.{name}"].constraints
            if isinstance(key, UniqueConstraint)
        }
    for fk in api.FillOrderLink.__table__.foreign_key_constraints:
        if fk.referred_table.name in {"fills", "orders"}:
            assert tuple(element.column.name for element in fk.elements) == candidate
            assert tuple(fk.column_keys) == (
                "fill_id" if fk.referred_table.name == "fills" else "order_id",
                *candidate[1:],
            )
        assert fk.ondelete == fk.onupdate == "RESTRICT"
    for name in ("fills", "fill_order_links"):
        table = metadata.tables[f"hmt_crypto.{name}"]
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "TIMESTAMP WITH TIME ZONE" in ddl
        if name == "fills":
            assert "NUMERIC" in ddl and "NUMERIC(" not in ddl
        assert all(c.name for c in table.constraints)
        for index in table.indexes:
            assert index.name
            assert str(CreateIndex(index).compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("reference", [None, "000123", "b6cafb45-a0e7-4df0-ae14-6a3af4ff4076"])
@pytest.mark.parametrize("price", [None, Decimal("0"), Decimal("-1.25")])
async def test_settlement_observation_round_trips_uncertainty_and_opaque_reference(
    execution_db, channel, reference, price
):
    db, owner, *_ = execution_db
    instant = NOW.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(microsecond=123456)
    row = fill(
        owner,
        exchange_order_reference=reference,
        size=Decimal("12345678901234567890.123456789012345678"),
        price=price,
        fill_type="settlement",
        commission=Decimal("-0.000000000000000001"),
        notional=Decimal("-2.125"),
        settling_asset_id=1,
        settling_asset_symbol="USD",
        requested_at=instant,
        received_at=instant,
        recorded_at=instant,
    )
    await save(db, row, channel)
    async with db.transaction() as session:
        loaded = await session.get(schema_api().Fill, row.id)
        assert loaded.size == Decimal("12345678901234567890.123456789012345678")
        assert loaded.price == price
        assert loaded.commission == Decimal("-0.000000000000000001")
        assert loaded.notional == Decimal("-2.125")
        assert loaded.exchange_order_reference == reference
        assert loaded.side == "UNKNOWN" and loaded.fill_type == "settlement"
        assert loaded.occurred_at is None
        assert (
            loaded.requested_at
            == loaded.received_at
            == loaded.recorded_at
            == NOW.replace(microsecond=123456)
        )
        assert loaded.recorded_at.tzinfo == UTC
        assert loaded.settling_asset_id == 1 and loaded.settling_asset_symbol == "USD"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["size", "price", "notional", "commission"])
@pytest.mark.parametrize(
    "value",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("1e20"), Decimal("1e-19"), 0.125, True, "0.125"],
)
async def test_fill_decimals_reject_loss_and_coercion(execution_db, channel, field, value):
    db, owner, *_ = execution_db
    with pytest.raises(PersistenceError):
        await save(db, fill(owner, **{field: value}), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("value", [None, Decimal("0"), Decimal("-0.125")])
async def test_fill_size_must_be_positive(execution_db, channel, value):
    db, owner, *_ = execution_db
    with pytest.raises(PersistenceError):
        await save(db, fill(owner, size=value), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "change",
    [
        {"product_id": 0},
        {"product_id": True},
        {"product_id": 1.5},
        {"settling_asset_id": 0},
        {"side": "long"},
        {"fill_type": "invented"},
        {"source": "invented"},
        {"exchange_fill_id": ""},
        {"exchange_fill_id": "x\x00hidden"},
        {"exchange_order_reference": ""},
        {"exchange_order_reference": "x" * 129},
        {"received_at": NOW - timedelta(seconds=1)},
        {"recorded_at": NOW - timedelta(seconds=1)},
        {"requested_at": datetime(2026, 11, 4)},
    ],
)
async def test_fill_admission_constraints(execution_db, channel, change):
    db, owner, *_ = execution_db
    with pytest.raises(PersistenceError):
        await save(db, fill(owner, **change), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope_change",
    [
        {"profile_name": "other"},
        {"region": "global"},
        {"environment": "prod"},
        {"mode": "BACKTEST"},
    ],
)
async def test_fill_identity_is_account_scoped_and_duplicates_fail(execution_db, scope_change):
    db, owner, *_ = execution_db
    other = account(**scope_change)
    await save(db, other)
    await save(db, fill(owner))
    await save(db, fill(other), "core")
    for channel in ("orm", "core"):
        with pytest.raises(PersistenceError):
            await save(db, fill(owner), channel)
    async with db.transaction() as session:
        assert len((await session.scalars(select(schema_api().Fill))).all()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_scoped_fill_identity_cannot_be_reused_under_changed_product(execution_db, channel):
    db, owner, *_ = execution_db
    await save(db, fill(owner))
    with pytest.raises(PersistenceError):
        await save(db, fill(owner, product_id=999), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fill_type", ["normal", "adl", "liquidation", "settlement", "otc", "UNKNOWN"]
)
@pytest.mark.parametrize("side", ["buy", "sell", "UNKNOWN"])
async def test_observed_vocabulary_does_not_infer_economics(execution_db, fill_type, side):
    db, owner, *_ = execution_db
    row = fill(owner, fill_type=fill_type, side=side, evidence={"role": "taker"})
    await save(db, row)
    async with db.transaction() as session:
        loaded = await session.get(schema_api().Fill, row.id)
        assert loaded.fill_type == fill_type and loaded.side == side
        assert loaded.commission is loaded.notional is loaded.price is None
        assert loaded.evidence_payload == '{"role":"taker"}'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"evidence_sha256": "b" * 64},
        {"evidence_schema_version": 2},
        {"evidence_payload": '{"role":"maker"}'},
    ],
)
async def test_direct_orm_fill_evidence_hash_and_version_cannot_be_forged(execution_db, change):
    db, owner, *_ = execution_db
    row = fill(owner)
    for field, value in change.items():
        setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("parent", ["fill", "order"])
@pytest.mark.parametrize(
    "mismatch", ["product", "account", "region", "environment", "mode", "missing"]
)
async def test_links_reject_product_scope_or_missing_parent(
    execution_db, channel, parent, mismatch
):
    db, owner, _, _, local_order, *_ = execution_db
    other = account(
        **{
            "account": {"profile_name": "other"},
            "region": {"region": "global"},
            "environment": {"environment": "prod"},
            "mode": {"mode": "BACKTEST"},
        }.get(mismatch, {"profile_name": "other"})
    )
    await save(db, other)
    scoped_other = mismatch not in {"product", "missing"}
    observation = fill(
        other if scoped_other else owner, product_id=999 if mismatch == "product" else 1101
    )
    await save(db, observation)
    candidate = link(
        other if parent == "order" and scoped_other else owner,
        observation,
        local_order,
        product_id=999 if parent == "order" and mismatch == "product" else 1101,
    )
    if mismatch == "missing":
        setattr(candidate, "fill_id" if parent == "fill" else "order_id", uuid4())
    with pytest.raises(PersistenceError):
        await save(db, candidate, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_one_authoritative_link_and_no_order_or_other_table_mutations(execution_db, channel):
    db, owner, publication, cmd, local_order, *_ = execution_db
    observation = fill(owner)
    async with db.transaction() as session:
        tables = [
            t
            for t in schema_api().metadata.tables.values()
            if t.name not in {"fills", "fill_order_links"}
        ]
        before = {t.name: (await session.execute(select(t))).all() for t in tables}
    await save(db, observation, channel)
    association = link(owner, observation, local_order)
    await save(db, association, channel)
    second_order = order(owner, cmd, publication, client_order_id="second")
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            session.add(second_order)
            await session.flush()
            duplicate = link(owner, observation, second_order)
            if channel == "orm":
                session.add(duplicate)
            else:
                await session.execute(
                    insert(duplicate.__table__).values(
                        **{c.name: getattr(duplicate, c.name) for c in duplicate.__table__.columns}
                    )
                )
    async with db.transaction() as session:
        after = {t.name: (await session.execute(select(t))).all() for t in tables}
        assert before == after
        loaded = await session.get(schema_api().Fill, observation.id)
        assert loaded.commission is loaded.notional is loaded.price is None
        assert loaded.evidence_payload == '{"role":"UNKNOWN"}'
        assert len((await session.scalars(select(schema_api().FillOrderLink))).all()) == 1


@pytest.mark.asyncio
async def test_fill_factory_owns_evidence_and_rejects_arbitrary_input(execution_db):
    db, owner, *_ = execution_db
    evidence = {"role": "maker"}
    observation = fill(owner, evidence=evidence)
    evidence["role"] = "taker"
    await save(db, observation)
    async with db.transaction() as session:
        loaded = await session.get(schema_api().Fill, observation.id)
        assert loaded.evidence_payload == '{"role":"maker"}'
    for value in ({"api_secret": "marker"}, {"role": []}, {"role": "invented"}, [], "{}"):
        with pytest.raises(PersistenceError):
            fill(owner, evidence=value)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("payload", [' {"role":"maker"}', '{"api_secret":"marker"}', '{"role":[]}'])
async def test_direct_evidence_payload_cannot_bypass_allowlist(execution_db, channel, payload):
    db, owner, *_ = execution_db
    row = fill(owner)
    row.evidence_payload = payload
    with pytest.raises(PersistenceError):
        await save(db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["fill", "link"])
@pytest.mark.parametrize("action", ["update", "delete"])
async def test_fill_and_authoritative_link_are_immutable_through_orm(execution_db, kind, action):
    db, owner, _, _, local_order, *_ = execution_db
    observation = fill(owner)
    await save(db, observation)
    association = link(owner, observation, local_order)
    await save(db, association)
    row = observation if kind == "fill" else association
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            loaded = await session.get(type(row), row.id)
            if action == "delete":
                await session.delete(loaded)
            elif kind == "fill":
                loaded.commission = Decimal("1")
            else:
                loaded.source = "VENUE"


@pytest.mark.asyncio
async def test_fill_and_link_transaction_roll_back_together(execution_db):
    db, owner, _, _, local_order, *_ = execution_db
    observation = fill(owner)
    with pytest.raises(RuntimeError):
        async with db.transaction() as session:
            session.add(observation)
            await session.flush()
            session.add(link(owner, observation, local_order))
            await session.flush()
            raise RuntimeError("abort")
    async with db.transaction() as session:
        assert (await session.scalars(select(schema_api().Fill))).all() == []
        assert (await session.scalars(select(schema_api().FillOrderLink))).all() == []


@pytest.mark.asyncio
async def test_file_backed_populated_fill_tables_create_and_drop(execution_db):
    db, owner, _, _, local_order, *_ = execution_db
    api = schema_api()
    observation = fill(owner)
    association = link(owner, observation, local_order, source="VENUE")
    async with db.transaction() as session:
        session.add(observation)
        await session.flush()
        session.add(association)
    async with db.transaction() as session:
        assert await session.get(api.FillOrderLink, association.id) is not None
        assert (
            await session.get(api.Fill, observation.id)
        ).evidence_payload == '{"role":"UNKNOWN"}'
        database_file = (await session.execute(text("PRAGMA database_list"))).all()[0][2]
        assert database_file.endswith("schema.db")
    async with db.engine.begin() as connection:
        # Drop the new tables while populated; the pre-existing cyclic account graph
        # still uses the fixture's established explicit cleanup procedure.
        await connection.run_sync(api.FillOrderLink.__table__.drop)
        await connection.run_sync(api.Fill.__table__.drop)
        names = (
            (await connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")))
            .scalars()
            .all()
        )
        assert "fills" not in names and "fill_order_links" not in names
        await connection.run_sync(api.metadata.create_all)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field", ["fill_id", "order_id", "product_id", "account_id", "region", "environment", "mode"]
)
async def test_link_membership_cannot_escape_through_null_columns(execution_db, channel, field):
    db, owner, _, _, local_order, *_ = execution_db
    observation = fill(owner)
    await save(db, observation)
    association = link(owner, observation, local_order, **{field: None})
    with pytest.raises(PersistenceError):
        await save(db, association, channel)


@pytest.mark.asyncio
async def test_fill_and_link_restrict_parent_deletion_at_database_boundary(execution_db):
    from sqlalchemy import delete

    db, owner, _, _, local_order, *_ = execution_db
    observation = fill(owner)
    await save(db, observation)
    await save(db, link(owner, observation, local_order))
    for parent in (observation, local_order, owner):
        with pytest.raises(PersistenceError):
            async with db.transaction() as session:
                await session.execute(
                    delete(parent.__table__).where(parent.__table__.c.id == parent.id)
                )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("size", "0"),
        ("size", "-1"),
        ("size", "01"),
        ("price", "1.00"),
        ("commission", "NaN"),
        ("notional", "1e-19"),
        ("recorded_at", "not-a-time"),
        ("evidence_payload", '{"api_secret":"marker"}'),
    ],
)
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_corrupt_fill_stored_values_fail_closed(execution_db, field, value, channel):
    db, owner, *_ = execution_db
    row = fill(owner)
    await save(db, row)
    # Simulate legacy corruption, bypassing this adapter and SQL CHECKs deliberately.
    async with db.engine.connect() as connection:
        await connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        await connection.execute(
            text(f"UPDATE fills SET {field} = :v WHERE id = :id"), {"v": value, "id": row.id.hex}
        )
        await connection.commit()
        await connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
        await connection.commit()
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            if channel == "orm":
                await session.get(schema_api().Fill, row.id)
            else:
                (await session.execute(select(schema_api().Fill.__table__))).all()
