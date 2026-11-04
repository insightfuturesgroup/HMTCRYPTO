"""D9c scoped replay identities reject each duplicate independently."""

import sqlite3
from uuid import UUID

import pytest
from risk_rate_consumption_schema_support import committed_consumption_graph
from schema_test_support import schema_db as schema_db


def _database_path(db):
    path = db.engine.url.database
    assert path is not None
    return path


def _stored_row(db, consumption):
    with sqlite3.connect(_database_path(db)) as connection:
        connection.row_factory = sqlite3.Row
        stored = connection.execute(
            "SELECT * FROM risk_rate_consumptions WHERE id = ?",
            (consumption.id.hex,),
        ).fetchone()
    assert stored is not None
    return dict(stored)


def _raw_insert(connection, values):
    columns = tuple(values)
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO risk_rate_consumptions ({','.join(columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "duplicate_field,new_key,new_reservation,new_attempt,new_event",
    [
        (
            "consumption_key",
            None,
            UUID("91000000-0000-4000-8000-000000000009"),
            UUID("a1000000-0000-4000-8000-00000000000a"),
            UUID("b1000000-0000-4000-8000-00000000000b"),
        ),
        (
            "reservation_id",
            "1" * 64,
            None,
            UUID("a2000000-0000-4000-8000-00000000000a"),
            UUID("b2000000-0000-4000-8000-00000000000b"),
        ),
        (
            "order_attempt_id",
            "2" * 64,
            UUID("92000000-0000-4000-8000-000000000009"),
            None,
            UUID("b3000000-0000-4000-8000-00000000000b"),
        ),
        (
            "dispatch_event_id",
            "3" * 64,
            UUID("93000000-0000-4000-8000-000000000009"),
            UUID("a3000000-0000-4000-8000-00000000000a"),
            None,
        ),
    ],
)
async def test_each_scoped_consumption_identity_rejects_an_isolated_duplicate(
    schema_db,
    duplicate_field,
    new_key,
    new_reservation,
    new_attempt,
    new_event,
):
    _, _, _, consumption = await committed_consumption_graph(schema_db)
    values = _stored_row(schema_db, consumption)
    values["id"] = "d100000000004000800000000000000d"
    if new_key is not None:
        values["consumption_key"] = new_key
    if new_reservation is not None:
        values["reservation_id"] = new_reservation.hex
    if new_attempt is not None:
        values["order_attempt_id"] = new_attempt.hex
    if new_event is not None:
        values["dispatch_event_id"] = new_event.hex

    with sqlite3.connect(_database_path(schema_db)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        with pytest.raises(sqlite3.IntegrityError) as error:
            _raw_insert(connection, values)

    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE
    assert values[duplicate_field] == _stored_row(schema_db, consumption)[duplicate_field]


@pytest.mark.asyncio
async def test_scoped_consumption_identities_are_independent_for_another_account_scope(schema_db):
    _, _, _, consumption = await committed_consumption_graph(schema_db)
    values = _stored_row(schema_db, consumption)
    values["id"] = "d200000000004000800000000000000d"
    values["account_id"] = "22000000000040008000000000000002"

    with sqlite3.connect(_database_path(schema_db)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        _raw_insert(connection, values)
        count = connection.execute("SELECT count(*) FROM risk_rate_consumptions").fetchone()[0]

    assert count == 2
