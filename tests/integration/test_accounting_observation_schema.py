"""Offline wallet/funding evidence and explicitly non-additive boundaries."""

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
from test_reconciliation_resource_schema import resource
from test_reconciliation_run_schema import run
from test_snapshot_schema import position

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError


async def wallet_parent(db, *, attempt=1, owner=None):
    if owner is None:
        owner, recovery = await graph(db)
    else:
        recovery = incarnation(owner)
        await save(db, owner)
        await save(db, recovery)
    reconciliation = run(owner, recovery)
    await save(db, reconciliation)
    collection = resource(
        owner,
        reconciliation,
        resource_kind="WALLET_TRANSACTIONS",
        collection_attempt=attempt,
        pagination_kind="CURSOR",
        query=dict(initial_cursor_direction="NONE"),
    )
    await save(db, collection)
    return owner, reconciliation, collection


async def position_parent(db):
    owner, recovery = await graph(db)
    reconciliation = run(owner, recovery)
    await save(db, reconciliation)
    collection = resource(
        owner,
        reconciliation,
        resource_kind="MARGINED_POSITIONS",
        pagination_kind="NONE",
        query=dict(initial_cursor_direction="NONE"),
    )
    await save(db, collection)
    snapshot = position(owner, collection)
    await save(db, snapshot)
    return owner, reconciliation, collection, snapshot


def wallet(owner, collection, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        resource_id=collection.id,
        run_id=collection.run_id,
        resource_kind=collection.resource_kind,
        exchange_transaction_id="tx-1",
        transaction_type="future_funding",
        product_id=1101,
        asset_id=1,
        asset_symbol="USDT",
        amount=Decimal("-1.25"),
        post_balance=Decimal("10.5"),
        effective_at=None,
        requested_at=NOW,
        received_at=NOW + timedelta(seconds=1),
        recorded_at=NOW + timedelta(seconds=2),
    )
    values.update(changes)
    return schema_api().WalletTransaction.from_evidence(**values)


def wallet_funding(owner, transaction, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        observation_key="wallet-funding-1",
        source_kind="WALLET_TRANSACTION",
        wallet_transaction_id=transaction.id,
        position_snapshot_id=None,
        product_id=transaction.product_id,
        asset_id=transaction.asset_id,
        asset_symbol=transaction.asset_symbol,
        amount=transaction.amount,
        rate=None,
        effective_at=transaction.effective_at,
        accounting_role="NON_ADDITIVE_OBSERVATION",
        observed_at=NOW,
        recorded_at=NOW + timedelta(seconds=1),
    )
    values.update(changes)
    return schema_api().FundingSnapshot.from_evidence(**values)


def position_funding(owner, snapshot, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        observation_key="position-funding-1",
        source_kind="POSITION_AGGREGATE",
        wallet_transaction_id=None,
        position_snapshot_id=snapshot.id,
        product_id=snapshot.product_id,
        asset_id=None,
        asset_symbol=None,
        amount=Decimal("-0.5"),
        rate=None,
        effective_at=None,
        accounting_role="NON_ADDITIVE_OBSERVATION",
        observed_at=NOW,
        recorded_at=NOW + timedelta(seconds=1),
    )
    values.update(changes)
    return schema_api().FundingSnapshot.from_evidence(**values)


def market_rate(owner, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        observation_key="market-rate-1",
        source_kind="MARKET_RATE",
        wallet_transaction_id=None,
        position_snapshot_id=None,
        product_id=1101,
        asset_id=None,
        asset_symbol=None,
        amount=None,
        rate=Decimal("0.0001"),
        effective_at=NOW,
        accounting_role="NON_ADDITIVE_OBSERVATION",
        observed_at=NOW,
        recorded_at=NOW + timedelta(seconds=1),
    )
    values.update(changes)
    return schema_api().FundingSnapshot.from_evidence(**values)


def test_factories_own_values_and_reject_payload_hash_version_or_secret_overrides():
    api = schema_api()
    values = dict(
        observation_key="owned",
        source_kind="MARKET_RATE",
        wallet_transaction_id=None,
        position_snapshot_id=None,
        product_id=1101,
        asset_id=None,
        asset_symbol=None,
        amount=None,
        rate=Decimal("0.0001"),
        effective_at=NOW,
        accounting_role="NON_ADDITIVE_OBSERVATION",
    )
    row = api.FundingSnapshot.from_evidence(**values)
    values["rate"] = Decimal("9")
    assert row.rate == Decimal("0.0001")
    for field, value in (
        ("evidence_schema_version", 1),
        ("evidence_payload", "{}"),
        ("evidence_sha256", HASH),
        ("api_secret", "SECRET-MARKER"),
    ):
        with pytest.raises(PersistenceValueError) as exc:
            api.FundingSnapshot.from_evidence(**values, **{field: value})
        assert "SECRET-MARKER" not in str(exc.value) and "SECRET-MARKER" not in repr(exc.value)


def test_exact_tables_keys_fks_indexes_and_postgres_ddl():
    api = schema_api()
    assert hasattr(api, "WalletTransaction") and hasattr(api, "FundingSnapshot")
    wallet_table = api.WalletTransaction.__table__
    funding_table = api.FundingSnapshot.__table__
    assert set(wallet_table.c.keys()) == {
        "id",
        *SCOPE,
        "resource_id",
        "run_id",
        "resource_kind",
        "exchange_transaction_id",
        "transaction_type",
        "product_id",
        "asset_id",
        "asset_symbol",
        "amount",
        "post_balance",
        "effective_at",
        "requested_at",
        "received_at",
        "recorded_at",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
    }
    assert set(funding_table.c.keys()) == {
        "id",
        *SCOPE,
        "observation_key",
        "source_kind",
        "wallet_transaction_id",
        "position_snapshot_id",
        "product_id",
        "asset_id",
        "asset_symbol",
        "amount",
        "rate",
        "effective_at",
        "accounting_role",
        "observed_at",
        "recorded_at",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
    }
    assert {
        tuple(column.name for column in constraint.columns)
        for constraint in wallet_table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {
        ("id", *SCOPE),
        (*SCOPE, "exchange_transaction_id"),
        ("id", *SCOPE, "asset_id"),
    }
    assert {
        tuple(column.name for column in constraint.columns)
        for constraint in funding_table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {
        ("id", *SCOPE),
        (*SCOPE, "observation_key"),
        ("wallet_transaction_id", *SCOPE),
        ("position_snapshot_id", *SCOPE),
    }
    expected = {
        "wallet_transactions": {
            "accounts": (SCOPE, ("id", *SCOPE[1:])),
            "reconciliation_resources": (
                ("resource_id", *SCOPE, "run_id", "resource_kind"),
                ("id", *SCOPE, "run_id", "resource_kind"),
            ),
        },
        "funding_snapshots": {
            "accounts": (SCOPE, ("id", *SCOPE[1:])),
            "wallet_transactions": (
                ("wallet_transaction_id", *SCOPE, "asset_id"),
                ("id", *SCOPE, "asset_id"),
            ),
            "position_snapshots": (
                ("position_snapshot_id", *SCOPE, "product_id"),
                ("id", *SCOPE, "product_id"),
            ),
        },
    }
    for table in (wallet_table, funding_table):
        references = expected[table.name]
        assert {fk.referred_table.name for fk in table.foreign_key_constraints} == set(references)
        indexes = {tuple(column.name for column in index.columns) for index in table.indexes}
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "UUID" in ddl and "BIGINT" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl
        assert "NUMERIC(38, 18)" in ddl and "TEXT" in ddl
        for constraint in table.constraints:
            assert constraint.name and constraint.name in ddl
        for fk in table.foreign_key_constraints:
            source, target = references[fk.referred_table.name]
            assert tuple(fk.column_keys) == source
            assert tuple(element.column.name for element in fk.elements) == target
            assert fk.ondelete == fk.onupdate == "RESTRICT" and source in indexes
        for index in table.indexes:
            assert index.name in str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    for prior in api.metadata.tables.values():
        if prior not in {wallet_table, funding_table}:
            assert not {"wallet_transactions", "funding_snapshots"} & {
                fk.referred_table.name for fk in prior.foreign_key_constraints
            }


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", [*SCOPE, "resource_id", "run_id", "resource_kind"])
async def test_wallet_full_resource_parent_identity_is_enforced(schema_db, channel, field):
    owner, _, collection = await wallet_parent(schema_db)
    row = wallet(owner, collection)
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
            "resource_kind": "FILLS",
        }[field],
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_transaction_identity_is_deduplicated_per_scope(schema_db, channel):
    owner, reconciliation, first = await wallet_parent(schema_db)
    await save(schema_db, wallet(owner, first), channel)
    second = resource(
        owner,
        reconciliation,
        resource_kind="WALLET_TRANSACTIONS",
        collection_attempt=2,
        pagination_kind="CURSOR",
        query=dict(initial_cursor_direction="NONE"),
    )
    await save(schema_db, second)
    with pytest.raises(PersistenceError):
        await save(schema_db, wallet(owner, second), channel)
    await save(schema_db, wallet(owner, second, exchange_transaction_id="tx-2"), channel)


@pytest.mark.asyncio
async def test_same_transaction_and_observation_keys_are_independent_across_scopes(schema_db):
    first_owner, _, first_collection = await wallet_parent(schema_db)
    second_owner, _, second_collection = await wallet_parent(
        schema_db, owner=account(profile_name="second")
    )
    first = wallet(first_owner, first_collection)
    second = wallet(second_owner, second_collection)
    await save(schema_db, first)
    await save(schema_db, second)
    await save(schema_db, market_rate(first_owner, observation_key="shared"))
    await save(schema_db, market_rate(second_owner, observation_key="shared"))


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("exchange_transaction_id", ""),
        ("exchange_transaction_id", "\t"),
        ("exchange_transaction_id", "x" * 129),
        ("transaction_type", "bad\x00type"),
        ("transaction_type", " "),
        ("asset_symbol", "\t"),
        ("asset_symbol", "x" * 129),
    ],
)
async def test_wallet_text_bindaries_are_exact_bounded_nonblank(schema_db, channel, field, value):
    owner, _, collection = await wallet_parent(schema_db)
    row = wallet(owner, collection)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["asset_id", "product_id"])
@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1", 2**63])
async def test_wallet_ids_are_exact_positive_bigints(schema_db, channel, field, value):
    owner, _, collection = await wallet_parent(schema_db)
    row = wallet(owner, collection)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["amount", "post_balance"])
@pytest.mark.parametrize(
    "value",
    [True, 1.25, "1.25", Decimal("NaN"), Decimal("Infinity"), Decimal("1e20"), Decimal("1e-19")],
)
async def test_wallet_decimals_reject_lossy_or_out_of_policy_values(
    schema_db, channel, field, value
):
    owner, _, collection = await wallet_parent(schema_db)
    row = wallet(owner, collection)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("post_balance,effective_at", [(None, None), (Decimal("0"), NOW)])
async def test_wallet_unknown_is_distinct_from_exact_zero_and_time(
    schema_db, post_balance, effective_at
):
    owner, _, collection = await wallet_parent(schema_db)
    row = wallet(owner, collection, post_balance=post_balance, effective_at=effective_at)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.post_balance == post_balance
        assert loaded.effective_at == effective_at
        assert loaded.transaction_type == "future_funding"


@pytest.mark.asyncio
async def test_wallet_nullable_product_remains_unknown_without_catalogue_dependency(schema_db):
    owner, _, collection = await wallet_parent(schema_db)
    row = wallet(owner, collection, product_id=None)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        assert (await session.get(type(row), row.id)).product_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_at", NOW + timedelta(seconds=2)),
        ("received_at", NOW - timedelta(seconds=1)),
        ("recorded_at", NOW),
        ("requested_at", NOW.replace(tzinfo=None)),
        ("effective_at", NOW.replace(tzinfo=None)),
    ],
)
async def test_wallet_provenance_time_order_and_aware_values(schema_db, channel, field, value):
    owner, _, collection = await wallet_parent(schema_db)
    row = wallet(owner, collection)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("source", ["WALLET_TRANSACTION", "POSITION_AGGREGATE", "MARKET_RATE"])
async def test_all_funding_sources_are_non_additive_and_admitted(schema_db, channel, source):
    if source == "POSITION_AGGREGATE":
        owner, _, _, snapshot = await position_parent(schema_db)
        row = position_funding(owner, snapshot)
    elif source == "WALLET_TRANSACTION":
        owner, _, collection = await wallet_parent(schema_db)
        transaction = wallet(owner, collection)
        await save(schema_db, transaction)
        row = wallet_funding(owner, transaction)
    else:
        owner, _ = await graph(schema_db)
        row = market_rate(owner)
    await save(schema_db, row, channel)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.accounting_role == "NON_ADDITIVE_OBSERVATION"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "source,changes",
    [
        ("WALLET_TRANSACTION", {"wallet_transaction_id": None}),
        ("WALLET_TRANSACTION", {"position_snapshot_id": uuid4()}),
        ("WALLET_TRANSACTION", {"amount": None}),
        ("WALLET_TRANSACTION", {"rate": Decimal("0.1")}),
        ("WALLET_TRANSACTION", {"asset_id": None, "asset_symbol": None}),
        ("POSITION_AGGREGATE", {"position_snapshot_id": None}),
        ("POSITION_AGGREGATE", {"wallet_transaction_id": uuid4()}),
        ("POSITION_AGGREGATE", {"product_id": None}),
        ("POSITION_AGGREGATE", {"amount": None}),
        ("POSITION_AGGREGATE", {"rate": Decimal("0.1")}),
        ("MARKET_RATE", {"wallet_transaction_id": uuid4()}),
        ("MARKET_RATE", {"position_snapshot_id": uuid4()}),
        ("MARKET_RATE", {"product_id": None}),
        ("MARKET_RATE", {"amount": Decimal("1")}),
        ("MARKET_RATE", {"rate": None}),
        ("MARKET_RATE", {"effective_at": None}),
    ],
)
async def test_funding_source_shape_sql_null_branches(schema_db, channel, source, changes):
    owner, _ = await graph(schema_db)
    base = market_rate(owner)
    base.source_kind = source
    if source == "WALLET_TRANSACTION":
        base.wallet_transaction_id = uuid4()
        base.asset_id, base.asset_symbol = 1, "USDT"
        base.amount, base.rate, base.effective_at = Decimal("1"), None, None
    elif source == "POSITION_AGGREGATE":
        base.position_snapshot_id = uuid4()
        base.amount, base.rate, base.effective_at = Decimal("1"), None, None
    for field, value in changes.items():
        setattr(base, field, value)
    # The factory is bypassed above so SQL checks independently cover NULL logic.
    with pytest.raises(PersistenceError):
        await save(schema_db, base, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("source", ["WALLET_TRANSACTION", "POSITION_AGGREGATE"])
async def test_funding_parent_scope_and_economic_identity_are_exact(schema_db, channel, source):
    if source == "WALLET_TRANSACTION":
        owner, _, collection = await wallet_parent(schema_db)
        transaction = wallet(owner, collection)
        await save(schema_db, transaction)
        row = wallet_funding(owner, transaction, asset_id=2)
    else:
        owner, _, _, snapshot = await position_parent(schema_db)
        row = position_funding(owner, snapshot, product_id=2)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_funding_parent_and_observation_keys_are_single_use(schema_db, channel):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    await save(schema_db, transaction)
    first = wallet_funding(owner, transaction)
    await save(schema_db, first, channel)
    with pytest.raises(PersistenceError):
        await save(schema_db, wallet_funding(owner, transaction, observation_key="other"), channel)
    other = market_rate(owner, observation_key=first.observation_key)
    with pytest.raises(PersistenceError):
        await save(schema_db, other, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_position_parent_is_single_use(schema_db, channel):
    owner, _, _, snapshot = await position_parent(schema_db)
    await save(schema_db, position_funding(owner, snapshot), channel)
    with pytest.raises(PersistenceError):
        await save(
            schema_db,
            position_funding(owner, snapshot, observation_key="other-position-funding"),
            channel,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("observation_key", ""),
        ("observation_key", "\t"),
        ("observation_key", "x" * 129),
        ("asset_symbol", "bad\x00asset"),
        ("asset_symbol", "\t"),
        ("accounting_role", "ADDITIVE"),
        ("source_kind", "UNKNOWN"),
    ],
)
async def test_funding_text_and_role_boundaries_are_enforced(schema_db, channel, field, value):
    owner, _ = await graph(schema_db)
    row = market_rate(owner, asset_id=1, asset_symbol="USDT")
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["product_id", "asset_id"])
@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1", 2**63])
async def test_funding_optional_ids_are_exact_positive_bigints(schema_db, channel, field, value):
    owner, _ = await graph(schema_db)
    row = market_rate(owner, asset_id=1, asset_symbol="USDT")
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["amount", "rate"])
@pytest.mark.parametrize(
    "value",
    [True, 1.25, "1.25", Decimal("NaN"), Decimal("Infinity"), Decimal("1e20"), Decimal("1e-19")],
)
async def test_funding_decimals_reject_lossy_or_out_of_policy_values(
    schema_db, channel, field, value
):
    owner, _ = await graph(schema_db)
    row = market_rate(owner)
    if field == "amount":
        row.source_kind = "POSITION_AGGREGATE"
        row.position_snapshot_id = uuid4()
        row.amount, row.rate, row.effective_at = value, None, None
    else:
        row.rate = value
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_at", NOW + timedelta(seconds=2)),
        ("recorded_at", NOW - timedelta(seconds=1)),
        ("observed_at", NOW.replace(tzinfo=None)),
        ("effective_at", NOW.replace(tzinfo=None)),
    ],
)
async def test_funding_times_are_aware_and_locally_ordered(schema_db, channel, field, value):
    owner, _ = await graph(schema_db)
    row = market_rate(owner)
    setattr(row, field, value)
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
async def test_signed_zero_rate_and_timezone_normalization_round_trip(schema_db):
    owner, _ = await graph(schema_db)
    offset = timezone(timedelta(hours=5, minutes=30))
    instant = datetime(2026, 11, 4, 5, 30, 0, 123456, tzinfo=offset)
    row = market_rate(owner, rate=Decimal("0"), effective_at=instant)
    await save(schema_db, row)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(row), row.id)
        assert loaded.rate == Decimal("0")
        assert loaded.effective_at == datetime(2026, 11, 4, 0, 0, 0, 123456, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["wallet", "funding"])
@pytest.mark.parametrize("change", ["payload", "hash", "version", "semantic"])
async def test_direct_orm_insert_validates_complete_evidence_tuple(
    schema_db, snapshot_kind, change
):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    row = transaction
    if snapshot_kind == "funding":
        await save(schema_db, transaction)
        row = wallet_funding(owner, transaction)
    if change == "payload":
        row.evidence_payload = "{}"
    elif change == "hash":
        row.evidence_sha256 = "b" * 64
    elif change == "version":
        row.evidence_schema_version = 2
    elif snapshot_kind == "wallet":
        row.amount = Decimal("9")
    else:
        row.amount = Decimal("9")
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["wallet", "funding"])
@pytest.mark.parametrize("operation", ["load", "deferred", "refresh", "partial", "expired"])
async def test_corrupt_digest_fails_closed_on_every_orm_load_path(
    schema_db, snapshot_kind, operation
):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    await save(schema_db, transaction)
    row = transaction
    if snapshot_kind == "funding":
        row = wallet_funding(owner, transaction)
        await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = None
            if operation in {"refresh", "partial", "expired"}:
                loaded = await session.get(type(row), row.id)
                session.expire(loaded, ["evidence_sha256"])
            await session.execute(
                text(f"UPDATE {row.__tablename__} SET evidence_sha256=:v WHERE id=:id"),
                {"v": "b" * 64, "id": row.id.hex},
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
@pytest.mark.parametrize("snapshot_kind", ["wallet", "funding"])
async def test_core_standalone_payload_and_cross_column_digest_limit(schema_db, snapshot_kind):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    row = transaction
    if snapshot_kind == "funding":
        await save(schema_db, transaction)
        row = wallet_funding(owner, transaction)
    row.evidence_sha256 = "b" * 64
    await save(schema_db, row, "core")
    async with schema_db.transaction() as session:
        assert (await session.execute(select(row.__table__))).mappings().one()[
            "evidence_sha256"
        ] == "b" * 64
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(row), row.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["wallet", "funding"])
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_corrupt_noncanonical_payload_reads_fail_closed(schema_db, snapshot_kind, channel):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    await save(schema_db, transaction)
    row = transaction if snapshot_kind == "wallet" else market_rate(owner)
    if snapshot_kind == "funding":
        await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.execute(
                text(f"UPDATE {row.__tablename__} SET evidence_payload='{{}}' WHERE id=:id"),
                {"id": row.id.hex},
            )
            if channel == "orm":
                await session.get(type(row), row.id)
            else:
                (await session.execute(select(row.__table__))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot_kind,field,value",
    [
        ("wallet", "asset_id", 1.5),
        ("wallet", "amount", "01"),
        ("wallet", "effective_at", "2026-11-04T00:00:00.000000+01:00"),
        ("wallet", "asset_symbol", "US\x00DT"),
        ("funding", "product_id", 1.5),
        ("funding", "rate", "01"),
        ("funding", "effective_at", "2026-11-04T00:00:00.000000+01:00"),
        ("funding", "observation_key", "bad\x00key"),
    ],
)
@pytest.mark.parametrize("channel", ["orm", "core"])
async def test_corrupt_typed_column_reads_fail_closed(
    schema_db, snapshot_kind, field, value, channel
):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    await save(schema_db, transaction)
    row = transaction if snapshot_kind == "wallet" else market_rate(owner)
    if snapshot_kind == "funding":
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
@pytest.mark.parametrize("snapshot_kind", ["wallet", "funding"])
@pytest.mark.parametrize("operation", ["update", "delete", "expired_update", "deferred_delete"])
async def test_all_orm_mutation_and_delete_is_denied(schema_db, snapshot_kind, operation):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    await save(schema_db, transaction)
    row = transaction
    if snapshot_kind == "funding":
        row = wallet_funding(owner, transaction)
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
                loaded.recorded_at = NOW + timedelta(seconds=4)


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot_kind", ["wallet", "funding"])
async def test_core_update_delete_append_only_enforcement_is_explicitly_deferred(
    schema_db, snapshot_kind
):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    await save(schema_db, transaction)
    row = transaction if snapshot_kind == "wallet" else market_rate(owner)
    if snapshot_kind == "funding":
        await save(schema_db, row)
    async with schema_db.transaction() as session:
        await session.execute(
            row.__table__.update()
            .where(row.__table__.c.id == row.id)
            .values(recorded_at=NOW + timedelta(seconds=5))
        )
        await session.execute(delete(row.__table__).where(row.__table__.c.id == row.id))
    async with schema_db.transaction() as session:
        assert (await session.execute(select(row.__table__))).all() == []


@pytest.mark.asyncio
async def test_success_and_rollback_leave_parent_and_prior_truth_unchanged(schema_db):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    new_tables = {"wallet_transactions", "funding_snapshots"}
    prior = [
        table for table in schema_api().metadata.tables.values() if table.name not in new_tables
    ]
    async with schema_db.transaction() as session:
        before = {table.name: (await session.execute(select(table))).all() for table in prior}
    await save(schema_db, transaction)
    funding = wallet_funding(owner, transaction)
    await save(schema_db, funding)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(market_rate(owner, observation_key=funding.observation_key))
    async with schema_db.transaction() as session:
        assert before == {
            table.name: (await session.execute(select(table))).all() for table in prior
        }
        assert len((await session.scalars(select(type(transaction)))).all()) == 1
        assert len((await session.scalars(select(type(funding)))).all()) == 1


@pytest.mark.asyncio
async def test_populated_tables_drop_and_recreate(schema_db):
    owner, _, collection = await wallet_parent(schema_db)
    transaction = wallet(owner, collection)
    await save(schema_db, transaction)
    await save(schema_db, wallet_funding(owner, transaction))
    tables = [schema_api().FundingSnapshot.__table__, schema_api().WalletTransaction.__table__]
    async with schema_db.engine.begin() as connection:
        for table in tables:
            await connection.run_sync(table.drop)
        for table in reversed(tables):
            await connection.run_sync(table.create)
    async with schema_db.transaction() as session:
        assert (await session.execute(select(schema_api().WalletTransaction))).all() == []
        assert (await session.execute(select(schema_api().FundingSnapshot))).all() == []


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
from hmt_crypto.persistence.models import WalletTransaction, FundingSnapshot
assert WalletTransaction.__tablename__ == 'wallet_transactions'
assert FundingSnapshot.__tablename__ == 'funding_snapshots'
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
