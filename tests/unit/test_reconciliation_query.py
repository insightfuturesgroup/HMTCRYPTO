"""Owned selector evidence and exact semantic identity, entirely offline."""

import importlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import sqlite

from hmt_crypto.persistence.errors import PersistenceValueError

KINDS = (
    "BALANCES",
    "LATEST_POSITION",
    "MARGINED_POSITIONS",
    "WALLET_TRANSACTIONS",
    "OPEN_ORDERS",
    "ORDER_HISTORY",
    "FILLS",
)


def api():
    name = "hmt_crypto.persistence.reconciliation_query"
    assert importlib.util.find_spec(name), "Missing canonical reconciliation query contract"
    return importlib.import_module(name)


def query(**changes):
    return dict(initial_cursor_direction="NONE", initial_cursor_sha256=None, **changes)


def test_golden_canonical_bytes_and_resource_bound_digest():
    module = api()
    raw = query(product_ids=[9, 2], page_size=10, start_at=datetime(2026, 11, 4, tzinfo=UTC))
    payload, digest = module.encode_query("FILLS", 1, raw)
    assert payload == (
        '{"initial_cursor_direction":"NONE","initial_cursor_sha256":null,'
        '"page_size":10,"product_ids":[2,9],'
        '"start_at":"2026-11-04T00:00:00.000000+00:00"}'
    )
    # Independent literal vector: sha256 of the compact sorted-key envelope.
    assert digest == "947f408b85bd899f306ffff5ca2fcf8bf15bca94848e7ca3a16c82ffc2d91e9c"
    raw["product_ids"].append(7)
    assert json.loads(payload)["product_ids"] == [2, 9]
    assert (
        module.encode_query("ORDER_HISTORY", 1, query())[1]
        != module.encode_query("FILLS", 1, query())[1]
    )


def test_selector_order_timezone_and_omitted_page_identity():
    module = api()
    instant = datetime(2026, 11, 4, 0, 0, 0, 123456, tzinfo=UTC)
    local = instant.astimezone(timezone(timedelta(hours=5, minutes=30)))
    a = module.encode_query("FILLS", 1, query(product_ids=[9, 2], start_at=instant))
    b = module.encode_query("FILLS", 1, query(product_ids=(2, 9), start_at=local))
    assert a == b
    assert module.encode_query("FILLS", 1, query(page_size=10)) != module.encode_query(
        "FILLS", 1, query()
    )
    owned = module.ReconciliationQuery(**query(product_ids=[9, 2]))
    assert owned.product_ids == (2, 9)
    with pytest.raises(ValueError):
        owned.product_ids = (7,)


@pytest.mark.parametrize("field", ["product_ids", "asset_ids", "start_at", "end_at", "page_size"])
def test_explicit_optional_null_rejected(field):
    with pytest.raises((PersistenceValueError, ValueError)):
        api().encode_query("FILLS", 1, query(**{field: None}))


@pytest.mark.parametrize(
    "ids", [[], [1, 1], [True], [1.0], ["1"], [0], [-1], [2**63], list(range(1, 12)), {1, 2}]
)
def test_nonexact_duplicate_empty_or_excessive_selectors_denied(ids):
    with pytest.raises((PersistenceValueError, ValueError)):
        api().encode_query("FILLS", 1, query(product_ids=ids))


@pytest.mark.parametrize("value", [True, 1.0, "1", 0, 51, None])
def test_page_size_is_strict_local_admission(value):
    with pytest.raises((PersistenceValueError, ValueError)):
        api().encode_query("FILLS", 1, query(page_size=value))


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_cursor", "secret-token"),
        ("api_key", "secret"),
        ("contract_type", "perpetual"),
        ("transaction_type", "deposit"),
        ("underlying_symbol", "BTC"),
        ("order_type", "limit"),
        ("state", "open"),
        ("arbitrary", {}),
    ],
)
def test_secrets_and_unreviewed_filters_are_denied(field, value):
    with pytest.raises((PersistenceValueError, ValueError)) as error:
        api().encode_query("FILLS", 1, query(**{field: value}))
    assert "secret-token" not in str(error.value)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "field", ["product_ids", "asset_ids", "start_at", "end_at", "page_size", "cursor"]
)
def test_resource_specific_query_fields(kind, field):
    values = query(**({"product_ids": [1]} if kind == "LATEST_POSITION" else {}))
    value = {
        "product_ids": [1],
        "asset_ids": [1],
        "start_at": datetime(2026, 1, 1, tzinfo=UTC),
        "end_at": datetime(2026, 1, 2, tzinfo=UTC),
        "page_size": 1,
    }
    if field == "cursor":
        values.update(initial_cursor_direction="BEFORE", initial_cursor_sha256="a" * 64)
    else:
        values[field] = value[field]
    allowed = (
        (
            field == "product_ids"
            and kind
            in {"LATEST_POSITION", "MARGINED_POSITIONS", "OPEN_ORDERS", "ORDER_HISTORY", "FILLS"}
        )
        or (field == "asset_ids" and kind == "WALLET_TRANSACTIONS")
        or (
            field in {"start_at", "end_at", "page_size", "cursor"}
            and kind in {"WALLET_TRANSACTIONS", "OPEN_ORDERS", "ORDER_HISTORY", "FILLS"}
        )
    )
    if allowed:
        api().encode_query(kind, 1, values)
    else:
        with pytest.raises(PersistenceValueError):
            api().encode_query(kind, 1, values)


@pytest.mark.parametrize("direction", ["NONE", "AFTER", "BEFORE", "INVALID", None])
@pytest.mark.parametrize("digest", [None, "a" * 64, "A" * 64, "a" * 63, "raw-token"])
def test_initial_cursor_pair(direction, digest):
    values = dict(initial_cursor_direction=direction, initial_cursor_sha256=digest)
    if (direction == "NONE" and digest is None) or (
        direction in {"AFTER", "BEFORE"} and digest == "a" * 64
    ):
        api().encode_query("FILLS", 1, values)
    else:
        with pytest.raises(PersistenceValueError):
            api().encode_query("FILLS", 1, values)


@pytest.mark.parametrize(
    "values",
    [
        {},
        query(start_at=datetime(2026, 1, 1)),
        query(start_at="2026-01-01T00:00:00+00:00"),
        query(start_at=datetime(2026, 2, 1, tzinfo=UTC), end_at=datetime(2026, 1, 1, tzinfo=UTC)),
        query(product_ids=[1, 2]),
    ],
)
def test_missing_direction_naive_dates_and_latest_cardinality(values):
    with pytest.raises(PersistenceValueError):
        api().encode_query("LATEST_POSITION", 1, values)


@pytest.mark.parametrize("version", [True, 1.0, "1", 0, 2, None])
def test_schema_version_is_exact(version):
    with pytest.raises(PersistenceValueError):
        api().encode_query("FILLS", version, query())


@pytest.mark.parametrize(
    "payload",
    [
        "null",
        "[]",
        "{}",
        '{"initial_cursor_direction":"NONE","initial_cursor_sha256":null,"api_key":"secret"}',
        '{"initial_cursor_sha256":null,"initial_cursor_direction":"NONE"}',
        '{"initial_cursor_direction":"NONE","initial_cursor_sha256":null,"product_ids":[2,1]}',
        '{"initial_cursor_direction":"NONE","initial_cursor_sha256":null,"start_at":"2026-01-01T00:00:00+00:00"}',
        '{"initial_cursor_direction":"NONE","initial_cursor_sha256":null,"page_size":null}',
        '{"initial_cursor_direction":"NONE","initial_cursor_sha256":null,"page_size":1,"page_size":2}',
        "x" * 2049,
    ],
)
@pytest.mark.parametrize("path", ["process_bind_param", "process_result_value"])
def test_standalone_adapter_denies_malformed_or_noncanonical_stored_values(payload, path):
    with pytest.raises(PersistenceValueError):
        getattr(api().QueryPayload(), path)(payload, sqlite.dialect())
