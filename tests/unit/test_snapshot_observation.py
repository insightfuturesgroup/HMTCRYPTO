"""Canonical normalized snapshot identities, independent of transport payloads."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.snapshot_observation import encode_snapshot, validate_snapshot

POSITION = {
    "product_id": 1101,
    "signed_contracts": -2,
    "entry_price": Decimal("100.5000"),
    "mark_price": Decimal("101"),
    "liquidation_price": None,
    "margin": Decimal("-0.25"),
    "unrealized_pnl": Decimal("0"),
    "margin_mode": "isolated",
    "exchange_updated_at": datetime(2026, 11, 4, 0, 0, 0, 123456, tzinfo=UTC),
}
BALANCE = {
    "asset_id": 1,
    "asset_symbol": "USDT",
    "available_balance": Decimal("-1.25"),
    "balance": Decimal("0"),
    "blocked_margin": Decimal("2.5"),
}


def test_literal_position_and_balance_hash_vectors():
    assert encode_snapshot("MARGINED_POSITIONS", 1, POSITION) == (
        "0ba5264f9702fc817620353c44ed0e3c52e95ca8a855d777f49cf7287f794905"
    )
    assert encode_snapshot("BALANCES", 1, BALANCE) == (
        "cf37669ae8baee05ab7d44b11b5ad0425304290b9b7ab7ca60d8cbc8ca515437"
    )


def test_semantically_equal_decimal_and_timezone_values_hash_identically():
    equivalent = POSITION | {
        "entry_price": Decimal("100.5"),
        "mark_price": Decimal("101.000000000000000000"),
        "margin": Decimal("-0.2500"),
        "unrealized_pnl": Decimal("0.000"),
        "exchange_updated_at": datetime(
            2026, 11, 4, 5, 30, 0, 123456, tzinfo=timezone(timedelta(hours=5, minutes=30))
        ),
    }
    assert encode_snapshot("MARGINED_POSITIONS", 1, equivalent) == encode_snapshot(
        "MARGINED_POSITIONS", 1, POSITION
    )


def test_latest_position_requires_every_rich_field_to_be_null():
    latest = POSITION | {
        "mark_price": None,
        "liquidation_price": None,
        "margin": None,
        "unrealized_pnl": None,
        "margin_mode": None,
        "exchange_updated_at": None,
    }
    digest = encode_snapshot("LATEST_POSITION", 1, latest)
    assert validate_snapshot("LATEST_POSITION", 1, latest, digest) is None
    rich_values = {
        "mark_price": Decimal("1"),
        "liquidation_price": Decimal("1"),
        "margin": Decimal("1"),
        "unrealized_pnl": Decimal("1"),
        "margin_mode": "isolated",
        "exchange_updated_at": datetime(2026, 11, 4, tzinfo=UTC),
    }
    for field, value in rich_values.items():
        with pytest.raises(PersistenceValueError):
            encode_snapshot("LATEST_POSITION", 1, latest | {field: value})


@pytest.mark.parametrize(
    "kind,values",
    [
        ("BALANCES", POSITION),
        ("MARGINED_POSITIONS", BALANCE),
        ("UNKNOWN", POSITION),
        (None, POSITION),
    ],
)
def test_kind_and_payload_shape_are_exact(kind, values):
    with pytest.raises(PersistenceValueError):
        encode_snapshot(kind, 1, values)
    with pytest.raises(PersistenceValueError):
        encode_snapshot("BALANCES", 1, BALANCE | {"api_secret": "SECRET-MARKER"})


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "1", 2**63])
def test_product_and_asset_ids_are_exact_positive_bigints(value):
    with pytest.raises(PersistenceValueError):
        encode_snapshot("MARGINED_POSITIONS", 1, POSITION | {"product_id": value})
    with pytest.raises(PersistenceValueError):
        encode_snapshot("BALANCES", 1, BALANCE | {"asset_id": value})


@pytest.mark.parametrize("value", [None, True, 1.0, "1", -(2**63) - 1, 2**63])
def test_signed_contracts_are_exact_signed_bigints(value):
    with pytest.raises(PersistenceValueError):
        encode_snapshot("MARGINED_POSITIONS", 1, POSITION | {"signed_contracts": value})


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1.25,
        "1.25",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e20"),
        Decimal("1e-19"),
    ],
)
def test_required_balance_decimals_are_exact_and_bounded(value):
    for field in ("available_balance", "balance", "blocked_margin"):
        with pytest.raises(PersistenceValueError):
            encode_snapshot("BALANCES", 1, BALANCE | {field: value})


@pytest.mark.parametrize("value", [True, 1.25, "1.25", Decimal("NaN"), Decimal("1e-19")])
def test_nullable_position_decimals_reject_nonexact_values(value):
    for field in (
        "entry_price",
        "mark_price",
        "liquidation_price",
        "margin",
        "unrealized_pnl",
    ):
        with pytest.raises(PersistenceValueError):
            encode_snapshot("MARGINED_POSITIONS", 1, POSITION | {field: value})


@pytest.mark.parametrize("value", [None, "", " ", "x" * 129, "US\x00DT", 1, True])
def test_asset_symbol_is_required_bounded_exact_text(value):
    with pytest.raises(PersistenceValueError):
        encode_snapshot("BALANCES", 1, BALANCE | {"asset_symbol": value})


@pytest.mark.parametrize("value", ["cross", "", 1, True])
def test_margin_mode_is_nullable_but_allowlisted(value):
    with pytest.raises(PersistenceValueError):
        encode_snapshot("MARGINED_POSITIONS", 1, POSITION | {"margin_mode": value})
    for admitted in (None, "isolated", "portfolio", "UNKNOWN"):
        assert encode_snapshot("MARGINED_POSITIONS", 1, POSITION | {"margin_mode": admitted})


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 11, 4), "2026-11-04T00:00:00+00:00", 0, True],
)
def test_exchange_timestamp_is_null_or_exact_aware_datetime(value):
    with pytest.raises(PersistenceValueError):
        encode_snapshot("MARGINED_POSITIONS", 1, POSITION | {"exchange_updated_at": value})


@pytest.mark.parametrize("version", [None, True, 0, 2, 1.0, "1"])
def test_schema_version_is_exactly_one(version):
    with pytest.raises(PersistenceValueError):
        encode_snapshot("BALANCES", version, BALANCE)


@pytest.mark.parametrize("digest", [None, True, "A" * 64, "a" * 63, "b" * 64])
def test_validation_requires_exact_matching_lowercase_digest(digest):
    with pytest.raises(PersistenceValueError):
        validate_snapshot("BALANCES", 1, BALANCE, digest)
