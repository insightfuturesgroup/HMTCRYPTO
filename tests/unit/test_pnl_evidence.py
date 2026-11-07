"""Canonical PnL calculation evidence, never readiness or accounting policy."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.pnl_evidence import (
    decode_pnl_evidence,
    encode_pnl_evidence,
    validate_pnl_evidence,
)

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
HASH_A = "a" * 64
HASH_B = "b" * 64
MEASURES = (
    "baseline_equity",
    "observed_equity",
    "reconciled_realized_pnl",
    "mark_to_market_unrealized_pnl",
    "equity_drawdown_loss",
    "unresolved_loss_reserve",
    "conservative_daily_loss",
)
VALUES = {
    "baseline_equity": Decimal("100.5000"),
    "calculation_policy_sha256": HASH_A,
    "calculation_version": 2,
    "completeness": "COMPLETE",
    "conservative_daily_loss": Decimal("6.75"),
    "equity_drawdown_loss": Decimal("5.5"),
    "input_set_sha256": HASH_B,
    "mark_to_market_unrealized_pnl": Decimal("-1.5"),
    "observed_equity": Decimal("95"),
    "reconciled_realized_pnl": Decimal("-2"),
    "reconciliation_run_id": RUN_ID,
    "reconciliation_version": 3,
    "reference_asset_id": 1,
    "reference_asset_symbol": "USDT",
    "unresolved_loss_reserve": Decimal("1.25"),
    "window_end_at": datetime(2026, 11, 4, 1, tzinfo=UTC),
    "window_start_at": datetime(2026, 11, 4, tzinfo=UTC),
}


def test_literal_payload_and_hash_vector():
    assert encode_pnl_evidence(1, VALUES) == (
        '{"baseline_equity":"100.5","calculation_policy_sha256":"'
        + HASH_A
        + '","calculation_version":2,"completeness":"COMPLETE",'
        '"conservative_daily_loss":"6.75","equity_drawdown_loss":"5.5",'
        '"input_set_sha256":"' + HASH_B + '","mark_to_market_unrealized_pnl":"-1.5",'
        '"observed_equity":"95","reconciled_realized_pnl":"-2",'
        '"reconciliation_run_id":"11111111-1111-4111-8111-111111111111",'
        '"reconciliation_version":3,"reference_asset_id":1,'
        '"reference_asset_symbol":"USDT","unresolved_loss_reserve":"1.25",'
        '"window_end_at":"2026-11-04T01:00:00.000000+00:00",'
        '"window_start_at":"2026-11-04T00:00:00.000000+00:00"}',
        "f4a1166bd14d487067d925703727cacb38fc288d7e43c39e660ed61a152195b4",
    )


def test_decimal_and_timezone_equivalence_is_canonical():
    equivalent = VALUES | {
        "baseline_equity": Decimal("100.5"),
        "observed_equity": Decimal("95.000000000000000000"),
        "window_start_at": datetime(
            2026, 11, 4, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
        ),
        "window_end_at": datetime(
            2026, 11, 4, 6, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
        ),
    }
    assert encode_pnl_evidence(1, equivalent) == encode_pnl_evidence(1, VALUES)


@pytest.mark.parametrize("field", sorted(VALUES))
def test_every_semantic_field_is_required(field):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, {key: value for key, value in VALUES.items() if key != field})


def test_extra_or_secret_bearing_fields_are_rejected_without_echo():
    marker = "SECRET-PNL-MARKER"
    with pytest.raises(PersistenceValueError) as exc:
        encode_pnl_evidence(1, VALUES | {"api_secret": marker})
    assert marker not in str(exc.value) and marker not in repr(exc.value)


@pytest.mark.parametrize("version", [None, True, False, 0, 2, 1.0, "1"])
def test_evidence_schema_version_is_exactly_one(version):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(version, VALUES)


@pytest.mark.parametrize(
    "field", ["calculation_version", "reconciliation_version", "reference_asset_id"]
)
@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "1", 2**63])
def test_ids_and_versions_are_exact_positive_bigints(field, value):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, VALUES | {field: value})


@pytest.mark.parametrize("value", [None, True, 1, str(RUN_ID), UUID(int=0)])
def test_reconciliation_run_id_is_an_exact_nonzero_uuid(value):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, VALUES | {"reconciliation_run_id": value})


@pytest.mark.parametrize("field", ["calculation_policy_sha256", "input_set_sha256"])
@pytest.mark.parametrize("value", [None, True, "A" * 64, "a" * 63, "g" * 64])
def test_identity_hashes_are_exact_lowercase_sha256(field, value):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, VALUES | {field: value})


@pytest.mark.parametrize("value", [None, "", " ", "\t", "x" * 129, "US\x00DT", 1, True])
def test_reference_asset_symbol_is_required_bounded_exact_text(value):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, VALUES | {"reference_asset_symbol": value})


def test_unknown_requires_every_measure_to_be_explicit_null():
    unknown = VALUES | {"completeness": "UNKNOWN"} | {field: None for field in MEASURES}
    payload, digest = encode_pnl_evidence(1, unknown)
    assert all(f'"{field}":null' in payload for field in MEASURES)
    assert validate_pnl_evidence(1, payload, digest) is None
    for field in MEASURES:
        with pytest.raises(PersistenceValueError):
            encode_pnl_evidence(1, unknown | {field: Decimal("0")})


def test_partial_requires_a_known_measure_and_never_final_daily_loss():
    partial = VALUES | {"completeness": "PARTIAL"} | {field: None for field in MEASURES}
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, partial)
    assert encode_pnl_evidence(1, partial | {"baseline_equity": Decimal("0")})
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(
            1,
            partial | {"baseline_equity": Decimal("0"), "conservative_daily_loss": Decimal("0")},
        )


@pytest.mark.parametrize("completeness", ["UNKNOWN", "PARTIAL", "COMPLETE"])
def test_every_completeness_measure_null_shape(completeness):
    for mask in range(1 << len(MEASURES)):
        measures = {
            field: Decimal("0") if mask & (1 << position) else None
            for position, field in enumerate(MEASURES)
        }
        valid = (
            (completeness == "UNKNOWN" and mask == 0)
            or (completeness == "PARTIAL" and bool(mask & 0b0111111) and not mask & 0b1000000)
            or (completeness == "COMPLETE" and mask == 0b1111111)
        )
        if valid:
            assert encode_pnl_evidence(1, VALUES | {"completeness": completeness} | measures)
        else:
            with pytest.raises(PersistenceValueError):
                encode_pnl_evidence(1, VALUES | {"completeness": completeness} | measures)


@pytest.mark.parametrize("field", MEASURES)
def test_complete_requires_every_measure(field):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, VALUES | {field: None})


@pytest.mark.parametrize("completeness", [None, "", "READY", 1, True])
def test_completeness_is_closed(completeness):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, VALUES | {"completeness": completeness})


@pytest.mark.parametrize(
    "field", ["equity_drawdown_loss", "unresolved_loss_reserve", "conservative_daily_loss"]
)
def test_loss_and_reserve_measures_are_nonnegative(field):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, VALUES | {field: Decimal("-0.0001")})


@pytest.mark.parametrize(
    "value",
    [True, 1.25, "1.25", Decimal("NaN"), Decimal("Infinity"), Decimal("1e20"), Decimal("1e-19")],
)
@pytest.mark.parametrize("field", MEASURES)
def test_measure_values_are_exact_bounded_decimals(field, value):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, VALUES | {field: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"window_start_at": datetime(2026, 11, 4)},
        {"window_end_at": "2026-11-04T01:00:00+00:00"},
        {"window_start_at": VALUES["window_end_at"] + timedelta(microseconds=1)},
    ],
)
def test_window_times_are_exact_aware_and_ordered(changes):
    with pytest.raises(PersistenceValueError):
        encode_pnl_evidence(1, VALUES | changes)


def test_zero_length_window_is_valid():
    assert encode_pnl_evidence(1, VALUES | {"window_end_at": VALUES["window_start_at"]})


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "[]",
        "null",
        '{"baseline_equity":"100.5000"}',
        '{"api_secret":"SECRET"}',
        "x" * 2049,
        None,
        True,
    ],
)
def test_stored_payload_must_be_exact_complete_and_canonical(payload):
    with pytest.raises(PersistenceValueError):
        decode_pnl_evidence(payload)


def test_stored_payload_round_trip_restores_typed_values():
    payload, digest = encode_pnl_evidence(1, VALUES)
    restored = decode_pnl_evidence(payload)
    assert restored["baseline_equity"] == Decimal("100.5")
    assert restored["reconciliation_run_id"] == RUN_ID
    assert restored["window_start_at"] == VALUES["window_start_at"]
    assert validate_pnl_evidence(1, payload, digest) is None


@pytest.mark.parametrize("digest", [None, True, "A" * 64, "a" * 63, "b" * 64])
def test_validation_requires_exact_matching_lowercase_digest(digest):
    payload, _ = encode_pnl_evidence(1, VALUES)
    with pytest.raises(PersistenceValueError):
        validate_pnl_evidence(1, payload, digest)


def test_caller_container_is_not_retained():
    values = dict(VALUES)
    payload, digest = encode_pnl_evidence(1, values)
    values["baseline_equity"] = Decimal("999")
    assert validate_pnl_evidence(1, payload, digest) is None
