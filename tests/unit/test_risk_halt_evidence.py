"""Canonical halt lifecycle evidence; never readiness or dispatch authority."""

import importlib
from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import sqlite

from hmt_crypto.persistence.errors import PersistenceValueError

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
DRIFT_ID = UUID("22222222-2222-4222-8222-222222222222")
HASH_A = "a" * 64
VALUES = {
    "actor_kind": "RECONCILIATION",
    "reason_code": "CRITICAL_DRIFT",
    "reconciliation_run_id": RUN_ID,
    "pnl_snapshot_id": None,
    "drift_id": DRIFT_ID,
    "supporting_evidence_sha256": HASH_A,
}
ENVELOPE = {
    "halt_cause": "RECONCILIATION",
    "generation": 2,
    "sequence": 3,
    "event_kind": "REAFFIRMED",
    "from_state": "ACTIVE",
    "to_state": "ACTIVE",
}


def evidence():
    return importlib.import_module("hmt_crypto.persistence.risk_evidence")


def encode(values=None, **changes):
    envelope = ENVELOPE | changes
    return evidence().encode_halt_event_evidence(
        1,
        envelope["halt_cause"],
        envelope["generation"],
        envelope["sequence"],
        envelope["event_kind"],
        envelope["from_state"],
        envelope["to_state"],
        VALUES if values is None else values,
    )


def test_literal_payload_and_hash_vector():
    assert encode() == (
        '{"actor_kind":"RECONCILIATION","drift_id":'
        '"22222222-2222-4222-8222-222222222222","pnl_snapshot_id":null,'
        '"reason_code":"CRITICAL_DRIFT","reconciliation_run_id":'
        '"11111111-1111-4111-8111-111111111111",'
        '"supporting_evidence_sha256":"' + HASH_A + '"}',
        "525673c85c31e018712cbea7cd72e1d99b47fc01b1acb1d3125261e20b0b6d58",
    )


@pytest.mark.parametrize("field", sorted(VALUES))
def test_every_payload_field_is_required(field):
    with pytest.raises(PersistenceValueError):
        encode({key: value for key, value in VALUES.items() if key != field})


def test_extra_or_secret_bearing_fields_are_rejected_without_echo():
    marker = "SECRET-HALT-MARKER"
    with pytest.raises(PersistenceValueError) as exc:
        encode(VALUES | {"api_secret": marker})
    assert marker not in str(exc.value) and marker not in repr(exc.value)


def test_payload_model_is_strict_frozen_extra_forbidden_and_secret_safe():
    module = evidence()
    payload = module.HaltEventEvidenceV1.model_validate(dict(VALUES))
    assert payload.model_dump() == VALUES
    with pytest.raises(ValidationError):
        payload.actor_kind = "RECOVERY"
    with pytest.raises(ValidationError) as exc:
        module.HaltEventEvidenceV1.model_validate(VALUES | {"api_secret": "SECRET-HALT-MARKER"})
    assert "SECRET-HALT-MARKER" not in str(exc.value)
    assert "SECRET-HALT-MARKER" not in repr(exc.value)
    with pytest.raises(PersistenceValueError):
        module.HaltEventEvidenceV1.model_validate(VALUES | {"reconciliation_run_id": str(RUN_ID)})


def test_payload_model_and_decoded_payload_are_owned_frozen_values():
    module = evidence()
    model = module.HaltEventEvidenceV1.model_validate(dict(VALUES))
    assert encode(model) == encode(VALUES)
    payload, _ = encode()
    decoded = module.decode_halt_event_evidence(payload)
    assert type(decoded) is module.HaltEventEvidenceV1
    assert decoded.model_dump() == VALUES
    with pytest.raises(ValidationError):
        decoded.reason_code = "STALE_RECONCILIATION"


@pytest.mark.parametrize("version", [None, True, False, 0, 2, 1.0, "1"])
def test_schema_version_is_exactly_one(version):
    module = evidence()
    with pytest.raises(PersistenceValueError):
        module.encode_halt_event_evidence(
            version,
            ENVELOPE["halt_cause"],
            ENVELOPE["generation"],
            ENVELOPE["sequence"],
            ENVELOPE["event_kind"],
            ENVELOPE["from_state"],
            ENVELOPE["to_state"],
            VALUES,
        )


@pytest.mark.parametrize("field", ["generation", "sequence"])
@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "1", 2**63])
def test_generation_and_sequence_are_exact_positive_bigints(field, value):
    with pytest.raises(PersistenceValueError):
        encode(**{field: value})


@pytest.mark.parametrize(
    "field,values",
    [
        ("halt_cause", [None, "", "OTHER", 1, True]),
        ("event_kind", [None, "", "CLEARED", 1, True]),
        ("from_state", [None, "", "UNKNOWN", 1, True]),
        ("to_state", [None, "", "UNKNOWN", 1, True]),
    ],
)
def test_envelope_enums_are_closed(field, values):
    for value in values:
        with pytest.raises(PersistenceValueError):
            encode(**{field: value})


@pytest.mark.parametrize(
    "event_kind,sequence,from_state,to_state,valid",
    [
        ("ENGAGED", 1, "CLEARED", "ACTIVE", True),
        ("ENGAGED", 2, "CLEARED", "ACTIVE", False),
        ("ENGAGED", 1, "ACTIVE", "ACTIVE", False),
        ("REAFFIRMED", 2, "ACTIVE", "ACTIVE", True),
        ("REAFFIRMED", 1, "ACTIVE", "ACTIVE", False),
        ("REAFFIRMED", 2, "CLEARED", "ACTIVE", False),
        ("RELEASED", 2, "ACTIVE", "CLEARED", True),
        ("RELEASED", 1, "ACTIVE", "CLEARED", False),
        ("RELEASED", 2, "ACTIVE", "ACTIVE", False),
    ],
)
def test_exact_lifecycle_shapes(event_kind, sequence, from_state, to_state, valid):
    values = dict(VALUES)
    if event_kind == "ENGAGED":
        values["reason_code"] = "CRITICAL_DRIFT"
    elif event_kind == "RELEASED":
        values["reason_code"] = "RECONCILIATION_RECOVERED"

    def call():
        return encode(
            values,
            event_kind=event_kind,
            sequence=sequence,
            from_state=from_state,
            to_state=to_state,
        )

    if valid:
        assert call()
    else:
        with pytest.raises(PersistenceValueError):
            call()


ENGAGEMENT_REASONS = {
    "GLOBAL_KILL": {"OPERATOR_KILL"},
    "AUTOMATIC_RISK": {
        "ACCOUNTING_UNKNOWN",
        "PROTECTION_INADEQUATE",
        "RISK_POLICY",
        "RECOVERY_REQUIRED",
    },
    "DAILY_LOSS": {"DAILY_LOSS_LIMIT"},
    "RECONCILIATION": {
        "RECONCILIATION_INCOMPLETE",
        "STALE_RECONCILIATION",
        "CRITICAL_DRIFT",
    },
}
RELEASE_REASONS = {
    "OPERATOR_RELEASE",
    "UTC_RESET",
    "RECONCILIATION_RECOVERED",
    "ACCOUNTING_RECOVERED",
    "PROTECTION_RESTORED",
    "RECOVERY_COMPLETED",
}


@pytest.mark.parametrize("cause", sorted(ENGAGEMENT_REASONS))
@pytest.mark.parametrize("event_kind", ["ENGAGED", "REAFFIRMED", "RELEASED"])
@pytest.mark.parametrize(
    "reason",
    sorted(set().union(*ENGAGEMENT_REASONS.values(), RELEASE_REASONS)),
)
def test_complete_cause_event_reason_matrix(cause, event_kind, reason):
    values = VALUES | {
        "actor_kind": "OPERATOR" if cause == "GLOBAL_KILL" else "SYSTEM",
        "reason_code": reason,
    }
    changes = {
        "halt_cause": cause,
        "event_kind": event_kind,
        "sequence": 1 if event_kind == "ENGAGED" else 2,
        "from_state": "CLEARED" if event_kind == "ENGAGED" else "ACTIVE",
        "to_state": "CLEARED" if event_kind == "RELEASED" else "ACTIVE",
    }
    valid = reason in (RELEASE_REASONS if event_kind == "RELEASED" else ENGAGEMENT_REASONS[cause])
    if valid:
        assert encode(values, **changes)
    else:
        with pytest.raises(PersistenceValueError):
            encode(values, **changes)


@pytest.mark.parametrize("actor", ["SYSTEM", "RECONCILIATION", "RECOVERY"])
def test_every_global_kill_event_requires_operator_actor(actor):
    with pytest.raises(PersistenceValueError):
        encode(
            VALUES | {"actor_kind": actor, "reason_code": "OPERATOR_KILL"},
            halt_cause="GLOBAL_KILL",
            event_kind="ENGAGED",
            sequence=1,
            from_state="CLEARED",
            to_state="ACTIVE",
        )


@pytest.mark.parametrize("actor", [None, "", "ADMIN", 1, True])
def test_actor_vocabulary_is_closed(actor):
    with pytest.raises(PersistenceValueError):
        encode(VALUES | {"actor_kind": actor})


@pytest.mark.parametrize("field", ["reconciliation_run_id", "pnl_snapshot_id", "drift_id"])
@pytest.mark.parametrize("value", [True, 1, str(RUN_ID), UUID(int=0)])
def test_optional_parent_ids_are_exact_nonzero_uuids(field, value):
    with pytest.raises(PersistenceValueError):
        encode(VALUES | {field: value})


@pytest.mark.parametrize("value", [True, "A" * 64, "a" * 63, "g" * 64])
def test_supporting_hash_is_nullable_exact_lowercase_sha256(value):
    with pytest.raises(PersistenceValueError):
        encode(VALUES | {"supporting_evidence_sha256": value})


def test_nullable_semantic_fields_are_explicit_json_null():
    payload, digest = encode(
        VALUES
        | {
            "reconciliation_run_id": None,
            "pnl_snapshot_id": None,
            "drift_id": None,
            "supporting_evidence_sha256": None,
        }
    )
    for field in (
        "reconciliation_run_id",
        "pnl_snapshot_id",
        "drift_id",
        "supporting_evidence_sha256",
    ):
        assert f'"{field}":null' in payload
    evidence().validate_halt_event_evidence(1, *ENVELOPE.values(), payload, digest)


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "[]",
        "null",
        '{"api_secret":"SECRET"}',
        "x" * 2049,
        None,
        True,
    ],
)
def test_stored_payload_must_be_exact_complete_and_canonical(payload):
    with pytest.raises(PersistenceValueError):
        evidence().decode_halt_event_evidence(payload)


def test_noncanonical_payload_bytes_are_rejected():
    payload, _ = encode()
    with pytest.raises(PersistenceValueError):
        evidence().decode_halt_event_evidence(payload.replace(":null", ": null", 1))


def test_stored_payload_round_trip_and_complete_digest_validation():
    module = evidence()
    payload, digest = encode()
    assert module.decode_halt_event_evidence(payload).model_dump() == VALUES
    assert module.validate_halt_event_evidence(1, *ENVELOPE.values(), payload, digest) is None


@pytest.mark.parametrize("digest", [None, True, "A" * 64, "a" * 63, "b" * 64])
def test_validation_requires_exact_matching_lowercase_digest(digest):
    payload, _ = encode()
    with pytest.raises(PersistenceValueError):
        evidence().validate_halt_event_evidence(1, *ENVELOPE.values(), payload, digest)


def test_payload_adapter_validates_bind_and_result_paths():
    adapter = evidence().HaltEventEvidencePayload()
    payload, _ = encode()
    dialect = sqlite.dialect()
    assert adapter.process_bind_param(payload, dialect) == payload
    assert adapter.process_result_value(payload, dialect) == payload
    with pytest.raises(PersistenceValueError):
        adapter.process_bind_param("{}", dialect)
    with pytest.raises(PersistenceValueError):
        adapter.process_result_value("{}", dialect)


def test_caller_container_is_not_retained():
    values = deepcopy(VALUES)
    payload, digest = encode(values)
    values["actor_kind"] = "RECOVERY"
    assert evidence().validate_halt_event_evidence(1, *ENVELOPE.values(), payload, digest) is None
