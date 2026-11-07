"""Task 2.1d9c canonical evidence is strict and deterministic."""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from risk_rate_consumption_test_support import (
    ATTEMPT_ID,
    ATTEMPT_PREPARED,
    ATTEMPT_RECORDED,
    CONSUMPTION_KEY,
    DECISION_EVALUATED,
    DECISION_RECORDED,
    EVENT_PAYLOAD,
    EVENT_SHA,
    RESERVATION_CREATED,
    WINDOW_END,
    WINDOW_START,
    evidence_values,
    key_values,
)

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import encode_evidence


def evidence():
    import hmt_crypto.persistence.risk_rate_consumption_evidence as module

    return module


def test_literal_consumption_key_and_neutral_dispatch_event_vectors():
    module = evidence()
    assert module.derive_consumption_key(1, key_values()) == CONSUMPTION_KEY
    assert module.derive_dispatch_event_key(ATTEMPT_ID) == (
        "dispatch-started:v1:a0000000-0000-4000-8000-00000000000a"
    )
    event = module.encode_dispatch_started_evidence()
    assert event.payload == EVENT_PAYLOAD
    assert event.sha256 == EVENT_SHA


def test_checked_in_literal_full_consumption_vector_is_independently_hash_stable():
    literal = json.loads(
        (Path(__file__).parents[1] / "fixtures/risk_rate_consumption_v1.json").read_text()
    )
    module = evidence()
    encoded = module.encode_risk_rate_consumption_evidence(1, evidence_values())
    assert literal["consumption_key_sha256"] == CONSUMPTION_KEY
    assert literal["attempt_evidence_payload"] == evidence_values()["attempt_evidence_payload"]
    assert literal["dispatch_event"]["payload"] == EVENT_PAYLOAD
    assert literal["dispatch_event"]["sha256"] == EVENT_SHA
    assert (encoded.payload, encoded.sha256) == (
        literal["consumption"]["payload"],
        literal["consumption"]["sha256"],
    )
    assert (
        hashlib.sha256(literal["consumption_key_envelope"].encode("ascii")).hexdigest()
        == (literal["consumption_key_sha256"])
    )
    assert (
        hashlib.sha256(literal["dispatch_event"]["envelope"].encode("ascii")).hexdigest()
        == (literal["dispatch_event"]["sha256"])
    )


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("account_id", uuid4()),
        ("reservation_id", uuid4()),
        ("order_attempt_id", uuid4()),
        ("decision_recorded_at", DECISION_RECORDED + timedelta(microseconds=1)),
        ("reservation_created_at", RESERVATION_CREATED + timedelta(microseconds=1)),
    ],
)
def test_consumption_key_binds_every_identity_field(field, replacement):
    assert evidence().derive_consumption_key(1, key_values(**{field: replacement})) != (
        CONSUMPTION_KEY
    )


@pytest.mark.parametrize(
    "change",
    [
        {"consumed_units": True},
        {"consumed_units": 1.0},
        {"consumed_units": "1"},
        {"consumed_units": 2},
        {"attempt_backend": "VENUE"},
        {"dispatch_event_sequence": 2},
        {"dispatch_event_kind": "RESPONSE_ACCEPTED"},
        {"dispatch_event_evidence_payload": '{"outcome":"ACCEPTED"}'},
        {"recorded_at": RESERVATION_CREATED - timedelta(microseconds=1)},
    ],
)
def test_consumption_evidence_rejects_hostile_or_incoherent_values(change):
    with pytest.raises(PersistenceValueError):
        evidence().encode_risk_rate_consumption_evidence(1, evidence_values(**change))


def test_consumption_factory_owns_derived_keys_versions_payload_and_hash():
    module = evidence()
    for field, replacement in (
        ("consumption_key", CONSUMPTION_KEY),
        ("dispatch_event_key", module.derive_dispatch_event_key(ATTEMPT_ID)),
        ("evidence_schema_version", 1),
        ("evidence_payload", "{}"),
        ("evidence_sha256", "a" * 64),
    ):
        with pytest.raises(PersistenceValueError):
            module.encode_risk_rate_consumption_evidence(1, evidence_values(**{field: replacement}))


def test_consumption_evidence_round_trip_and_semantic_mutation_binding():
    module = evidence()
    encoded = module.encode_risk_rate_consumption_evidence(1, evidence_values())
    decoded = module.decode_risk_rate_consumption_evidence(encoded.payload)
    assert decoded == encoded
    assert encoded.consumption_key == CONSUMPTION_KEY
    changed = module.encode_risk_rate_consumption_evidence(
        1, evidence_values(risk_effect="REDUCE_ONLY")
    )
    assert changed.sha256 != encoded.sha256


@pytest.mark.parametrize(
    "mode,backend,admitted",
    [
        ("LIVE", "VENUE", True),
        ("LIVE", "SIMULATOR", False),
        ("PAPER", "VENUE", False),
        ("PAPER", "SIMULATOR", True),
        ("BACKTEST", "VENUE", False),
        ("BACKTEST", "SIMULATOR", True),
    ],
)
def test_exhaustive_mode_backend_matrix(mode, backend, admitted):
    operation = lambda: evidence().encode_risk_rate_consumption_evidence(  # noqa: E731
        1, evidence_values(mode=mode, attempt_backend=backend)
    )
    if admitted:
        assert operation().payload
    else:
        with pytest.raises(PersistenceValueError):
            operation()


def test_attempt_intent_must_agree_with_top_level_product():
    with pytest.raises(PersistenceValueError):
        evidence().encode_risk_rate_consumption_evidence(1, evidence_values(product_id=1102))


def test_modify_attempt_target_must_agree_with_top_level_order():
    target = evidence_values()["order_id"]
    payload, digest = encode_evidence(
        "intent",
        "MODIFY_ORDER",
        1,
        {"product_id": 1101, "target_order_id": target, "requested_contracts": 2},
    )
    values = evidence_values(
        command_operation_kind="MODIFY_ORDER",
        attempt_evidence_payload=payload,
        attempt_evidence_sha256=digest,
    )
    assert evidence().encode_risk_rate_consumption_evidence(1, values).payload
    with pytest.raises(PersistenceValueError):
        evidence().encode_risk_rate_consumption_evidence(1, values | {"order_id": uuid4()})


@pytest.mark.parametrize(
    "change",
    [
        {"decision_evaluated_at": WINDOW_START},
        {"decision_recorded_at": DECISION_EVALUATED},
        {"reservation_created_at": DECISION_RECORDED},
        {"attempt_prepared_at": RESERVATION_CREATED},
        {"attempt_recorded_at": ATTEMPT_PREPARED},
        {"recorded_at": ATTEMPT_RECORDED},
    ],
)
def test_every_causal_equality_boundary_is_admitted(change):
    assert evidence().encode_risk_rate_consumption_evidence(1, evidence_values(**change)).payload


@pytest.mark.parametrize(
    "change",
    [
        {"decision_evaluated_at": WINDOW_START - timedelta(microseconds=1)},
        {"decision_recorded_at": DECISION_EVALUATED - timedelta(microseconds=1)},
        {"reservation_created_at": DECISION_RECORDED - timedelta(microseconds=1)},
        {"attempt_prepared_at": RESERVATION_CREATED - timedelta(microseconds=1)},
        {"attempt_recorded_at": ATTEMPT_PREPARED - timedelta(microseconds=1)},
        {"recorded_at": ATTEMPT_RECORDED - timedelta(microseconds=1)},
        {"recorded_at": WINDOW_END},
    ],
)
def test_every_isolated_causal_or_window_inversion_is_rejected(change):
    with pytest.raises(PersistenceValueError):
        evidence().encode_risk_rate_consumption_evidence(1, evidence_values(**change))


@pytest.mark.parametrize(
    "sample,claim,lease,expected",
    [
        (ATTEMPT_RECORDED, WINDOW_END, None, True),
        (WINDOW_END, WINDOW_END, None, False),
        (WINDOW_END + timedelta(microseconds=1), WINDOW_END, None, False),
        (ATTEMPT_RECORDED, WINDOW_END, WINDOW_END, True),
        (ATTEMPT_RECORDED, WINDOW_END, ATTEMPT_RECORDED, False),
        (ATTEMPT_RECORDED, ATTEMPT_RECORDED, WINDOW_END, False),
    ],
)
def test_one_sample_strict_expiry_truth_table(sample, claim, lease, expected):
    assert evidence().same_sample_strictly_before_expiries(sample, claim, lease) is expected


@pytest.mark.parametrize("hostile", ["2026-11-04T12:00:00Z", 1, None, datetime(2026, 1, 1)])
def test_pure_time_helpers_reject_hostile_non_utc_values(hostile):
    with pytest.raises(PersistenceValueError):
        evidence().windows_overlap(hostile, WINDOW_END, WINDOW_START, WINDOW_END)
    with pytest.raises(PersistenceValueError):
        evidence().same_sample_strictly_before_expiries(hostile, WINDOW_END, None)


def test_half_open_overlap_truth_table_and_malformed_windows():
    module = evidence()
    assert module.windows_overlap(WINDOW_START, WINDOW_END, WINDOW_START, WINDOW_END)
    assert not module.windows_overlap(
        WINDOW_START, WINDOW_END, WINDOW_END, WINDOW_END + timedelta(minutes=1)
    )
    assert module.windows_overlap(
        WINDOW_START, WINDOW_END, WINDOW_END - timedelta(microseconds=1), WINDOW_END
    )
    with pytest.raises(PersistenceValueError):
        module.windows_overlap(WINDOW_END, WINDOW_END, WINDOW_START, WINDOW_END)


@pytest.mark.parametrize(
    "change",
    [
        {"consumption_id": "d0000000-0000-4000-8000-00000000000d"},
        {"consumption_id": UUID(int=0)},
        {"product_id": True},
        {"product_id": 1.0},
        {"product_id": "1"},
        {"product_id": 0},
        {"product_id": 2**63},
        {"rate_policy_sha256": "D" * 64},
        {"rate_policy_sha256": "d" * 63},
        {"attempt_evidence_schema_version": 2},
        {"attempt_number": False},
        {"recorded_at": datetime(2026, 11, 4, 12, 0, 15)},
        {"region": "india\x00"},
        {"api_key": "forbidden"},
        {"metadata": {"secret": "forbidden"}},
    ],
)
def test_strict_scalar_and_secret_or_metadata_rejection_matrix(change):
    with pytest.raises(PersistenceValueError):
        evidence().encode_risk_rate_consumption_evidence(1, evidence_values(**change))


@pytest.mark.parametrize("operation", ["PLACE_ORDER", "MODIFY_ORDER"])
def test_largest_valid_closed_intents_remain_inside_consumption_ceiling(operation):
    maximum_integer = 2**63 - 1
    maximum_amount = Decimal("99999999999999999999.999999999999999999")
    maximum_uuid = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    if operation == "PLACE_ORDER":
        intent = {
            "product_id": maximum_integer,
            "requested_contracts": maximum_integer,
            "side": "sell",
            "order_type": "limit",
            "reduce_only": False,
            "limit_price": maximum_amount,
        }
        changes = {"product_id": maximum_integer}
    else:
        intent = {
            "product_id": maximum_integer,
            "target_order_id": maximum_uuid,
            "requested_contracts": maximum_integer,
            "limit_price": maximum_amount,
        }
        changes = {"product_id": maximum_integer, "order_id": maximum_uuid}
    payload, digest = encode_evidence("intent", operation, 1, intent)
    encoded = evidence().encode_risk_rate_consumption_evidence(
        1,
        evidence_values(
            command_operation_kind=operation,
            attempt_number=maximum_integer,
            rate_policy_version=maximum_integer,
            attempt_evidence_payload=payload,
            attempt_evidence_sha256=digest,
            **changes,
        ),
    )
    assert evidence().decode_risk_rate_consumption_evidence(encoded.payload) == encoded
    assert len(encoded.payload.encode("ascii")) + 64 < evidence().MAX_EVIDENCE_BYTES
