"""Bypass constructors to check actual SQL constraints and adapter boundaries."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from execution_test_support import attempt, attempt_event, command, order, transition
from execution_test_support import execution_db as execution_db
from schema_test_support import NOW, account, schema_api
from schema_test_support import schema_db as schema_db
from sqlalchemy import insert, select, text

from hmt_crypto.persistence.errors import PersistenceError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "price",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e20"),
        Decimal("1e-19"),
        1.5,
        True,
        "2",
    ],
)
async def test_requested_prices_are_positive_exact_local_storage_values(execution_db, price):
    db, owner, publication, cmd, *_ = execution_db
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            session.add(
                order(
                    owner,
                    cmd,
                    publication,
                    client_order_id="priced",
                    requested_limit_price=price,
                    requested_order_type="limit",
                )
            )


@pytest.mark.asyncio
async def test_microseconds_exact_decimal_and_unknown_fields_round_trip(execution_db):
    db, owner, publication, cmd, *_ = execution_db
    cmd = command(
        owner,
        request_id="priced",
        evidence={
            "product_id": 1101,
            "requested_contracts": 2,
            "side": "buy",
            "order_type": "limit",
            "reduce_only": False,
            "limit_price": Decimal("12345678901234567890.123456789012345678"),
        },
    )
    async with db.transaction() as session:
        session.add(cmd)
    instant = NOW.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(microsecond=123456)
    row = order(
        owner,
        cmd,
        publication,
        client_order_id="x" * 32,
        requested_order_type="limit",
        requested_limit_price=Decimal("12345678901234567890.123456789012345678"),
        created_at=instant,
        updated_at=instant,
    )
    async with db.transaction() as session:
        session.add(row)
    async with db.transaction() as session:
        loaded = await session.get(schema_api().Order, row.id)
        assert loaded.created_at == NOW.replace(microsecond=123456)
        assert loaded.created_at.tzinfo == UTC
        assert loaded.requested_limit_price == Decimal("12345678901234567890.123456789012345678")
        assert loaded.observed_side is loaded.observed_order_type is loaded.observed_state is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["command", "order", "attempt", "event", "transition"])
@pytest.mark.parametrize("channel", ["core", "raw"])
async def test_nul_cannot_hide_execution_identifiers_or_hash_suffix(execution_db, kind, channel):
    db, owner, publication, cmd, row, prepared = execution_db
    rows = {
        "command": command(owner, request_id="other"),
        "order": order(owner, cmd, publication, client_order_id="other"),
        "attempt": attempt(owner, cmd, row, attempt_number=2),
        "event": attempt_event(owner, prepared),
        "transition": transition(owner, row),
    }
    candidate = rows[kind]
    column = "client_order_id" if kind == "order" else "evidence_sha256"
    invalid = getattr(candidate, column) + "\x00hidden"
    if channel == "raw":
        async with db.transaction() as session:
            session.add(candidate)
        with pytest.raises(PersistenceError):
            async with db.transaction() as session:
                await session.execute(
                    text(
                        f"UPDATE {candidate.__tablename__} SET {column} = :invalid WHERE id = :id"
                    ),
                    {"invalid": invalid, "id": candidate.id.hex},
                )
    else:
        values = {c.name: getattr(candidate, c.name) for c in candidate.__table__.columns}
        values[column] = invalid
        with pytest.raises(PersistenceError):
            async with db.transaction() as session:
                await session.execute(insert(candidate.__table__).values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["command", "attempt", "event", "transition"])
@pytest.mark.parametrize("change", ["hash", "extra", "noncanonical", "schema"])
async def test_direct_orm_execution_evidence_cannot_bypass_typed_validation(
    execution_db, kind, change
):
    db, owner, _, _, row, prepared = execution_db
    other_cmd = command(owner, request_id="extra-command")
    async with db.transaction() as session:
        session.add(other_cmd)
    candidate = {
        "command": command(owner, request_id="other"),
        "attempt": attempt(owner, other_cmd, row),
        "event": attempt_event(owner, prepared),
        "transition": transition(owner, row),
    }[kind]
    if change == "hash":
        candidate.evidence_sha256 = "b" * 64
    elif change == "extra":
        candidate.evidence_payload = '{"api_secret":"SECRET-MARKER"}'
    elif change == "schema":
        candidate.evidence_schema_version = 2
    else:
        candidate.evidence_payload = " " + candidate.evidence_payload
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            session.add(candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["command", "order", "attempt"])
async def test_local_semantic_evidence_cannot_change_through_orm(execution_db, kind):
    db, _, _, cmd, row, prepared = execution_db
    original = {"command": cmd, "order": row, "attempt": prepared}[kind]
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            loaded = await session.get(type(original), original.id)
            if kind == "order":
                loaded.requested_contracts = 3
            else:
                loaded.operation_kind = "CANCEL_ORDER"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"sequence": 0},
        {"event_key": ""},
        {"observed_at": NOW + timedelta(seconds=1)},
        {"recorded_at": datetime.max.replace(tzinfo=timezone(timedelta(hours=-1)))},
        {"ingester_id": uuid4()},
        {"ingester_generation": 0},
    ],
)
async def test_core_event_checks_do_not_depend_on_orm_factory(execution_db, changes):
    db, owner, _, _, _, prepared = execution_db
    candidate = attempt_event(owner, prepared)
    values = {c.name: getattr(candidate, c.name) for c in candidate.__table__.columns}
    values.update(changes)
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            await session.execute(insert(candidate.__table__).values(**values))


@pytest.mark.asyncio
async def test_prepared_attempt_without_outcome_round_trips_as_unresolved(execution_db):
    db, _, _, _, _, prepared = execution_db
    async with db.transaction() as session:
        assert await session.get(schema_api().OrderAttempt, prepared.id) is not None
        assert (await session.scalars(select(schema_api().OrderAttemptEvent))).all() == []


def test_aggregate_import_performs_no_network_engine_or_production_activation():
    code = """
import socket
import httpx
import sqlalchemy.ext.asyncio
def forbidden(*args, **kwargs):
    raise AssertionError('Schema import attempted I/O')
socket.socket = forbidden
httpx.Client.send = forbidden
httpx.AsyncClient.send = forbidden
sqlalchemy.ext.asyncio.create_async_engine = forbidden
from hmt_crypto.persistence import models
assert len(models.metadata.tables) == 18
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_order_filled_observation_is_unknown_until_observed_and_separate_from_intent(
    execution_db,
):
    db, _, _, _, row, _ = execution_db
    async with db.transaction() as session:
        loaded = await session.get(schema_api().Order, row.id)
        assert loaded.observed_filled_contracts is None
        loaded.observed_filled_contracts = 1
    async with db.transaction() as session:
        loaded = await session.get(schema_api().Order, row.id)
        assert loaded.observed_filled_contracts == 1
        assert loaded.requested_contracts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [-1, True, 1.5, "1"])
async def test_filled_order_observation_is_strict_nonnegative_integer(execution_db, invalid):
    db, _, _, _, row, _ = execution_db
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            await session.execute(
                schema_api()
                .Order.__table__.update()
                .where(schema_api().Order.id == row.id)
                .values(observed_filled_contracts=invalid)
            )


async def basic_intent(db, mode, order_type):
    owner = account(mode=mode, profile_name="basic-price-contract")
    evidence = {
        "product_id": 1101,
        "requested_contracts": 2,
        "side": "buy",
        "order_type": order_type,
        "reduce_only": False,
    }
    if order_type == "limit":
        evidence["limit_price"] = Decimal("11")
    cmd = command(owner, evidence=evidence)
    async with db.transaction() as session:
        session.add(owner)
    async with db.transaction() as session:
        session.add(cmd)
    return owner, cmd


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("mode", ["LIVE", "PAPER", "BACKTEST"])
@pytest.mark.parametrize(
    "order_type,price,stop",
    [
        ("market", None, Decimal("10")),
        ("limit", Decimal("11"), Decimal("10")),
        ("market", Decimal("10"), None),
        ("limit", None, None),
        ("limit", Decimal("0"), None),
        ("limit", Decimal("-1"), None),
    ],
)
async def test_basic_order_rejects_stop_or_inconsistent_limit_price(
    execution_db,
    channel,
    mode,
    order_type,
    price,
    stop,
):
    db, _, publication, *_ = execution_db
    owner, cmd = await basic_intent(db, mode, order_type)
    candidate = order(
        owner,
        cmd,
        publication,
        origin="LOCAL_INTENT" if mode == "LIVE" else "SIMULATOR_INTENT",
        requested_order_type=order_type,
        requested_limit_price=price,
        requested_stop_price=stop,
    )
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            if channel == "orm":
                session.add(candidate)
            else:
                values = {c.name: getattr(candidate, c.name) for c in candidate.__table__.columns}
                await session.execute(insert(candidate.__table__).values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("mode", ["LIVE", "PAPER", "BACKTEST"])
@pytest.mark.parametrize("order_type,price", [("market", None), ("limit", Decimal("11"))])
async def test_complete_basic_market_and_limit_order_round_trip(
    execution_db,
    channel,
    mode,
    order_type,
    price,
):
    db, _, publication, *_ = execution_db
    owner, cmd = await basic_intent(db, mode, order_type)
    candidate = order(
        owner,
        cmd,
        publication,
        origin="LOCAL_INTENT" if mode == "LIVE" else "SIMULATOR_INTENT",
        requested_order_type=order_type,
        requested_limit_price=price,
    )
    async with db.transaction() as session:
        if channel == "orm":
            session.add(candidate)
        else:
            values = {c.name: getattr(candidate, c.name) for c in candidate.__table__.columns}
            await session.execute(insert(candidate.__table__).values(**values))
    async with db.transaction() as session:
        loaded = await session.get(schema_api().Order, candidate.id)
        assert loaded.requested_order_type == order_type
        assert loaded.requested_limit_price == price
        assert loaded.requested_stop_price is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_stop_price", Decimal("10")),
        ("requested_limit_price", Decimal("11")),
        ("requested_order_type", "market"),
    ],
)
async def test_adopted_order_still_forbids_fabricated_price_and_type_intent(
    execution_db, field, value
):
    db, *_ = execution_db
    owner = account(mode="LIVE", profile_name="adopted-price")
    async with db.transaction() as session:
        session.add(owner)
    candidate = schema_api().Order(
        id=uuid4(),
        account_id=owner.id,
        region=owner.region,
        environment=owner.environment,
        mode=owner.mode,
        origin="ADOPTED",
        exchange_order_id="observed-order",
        product_id=1101,
        state="UNKNOWN",
        version=1,
        last_transition_sequence=0,
        created_at=NOW,
        updated_at=NOW,
    )
    async with db.transaction() as session:
        session.add(candidate)
    async with db.transaction() as session:
        assert (await session.get(schema_api().Order, candidate.id)).requested_stop_price is None
    with pytest.raises(PersistenceError):
        async with db.transaction() as session:
            await session.execute(
                candidate.__table__.update()
                .where(candidate.__table__.c.id == candidate.id)
                .values(**{field: value})
            )
