"""Canonical discrepancy and operator-review evidence, never safety authority."""

from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import sqlite

from hmt_crypto.persistence import reconciliation_evidence as evidence_module
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.reconciliation_evidence import (
    AcknowledgementEvidencePayload,
    DriftEvidencePayload,
    decode_acknowledgement_evidence,
    decode_drift_evidence,
    encode_acknowledgement_evidence,
    encode_drift_evidence,
    validate_acknowledgement_evidence,
    validate_drift_evidence,
)

DRIFT_ID = UUID("11111111-1111-4111-8111-111111111111")
HASH_A = "a" * 64
HASH_B = "b" * 64
DRIFT_VALUES = {
    "comparison": "UNCERTAINTY",
    "subject_key_sha256": HASH_A,
    "product_id": 84,
    "asset_id": None,
    "expected_sha256": HASH_B,
    "observed_sha256": None,
}
ACKNOWLEDGEMENT_VALUES = {
    "actor_ref": "ops@example.invalid",
    "idempotency_key": "ack-001",
    "reason_code": "OPERATOR_NOTED",
    "supporting_evidence_sha256": None,
}


@pytest.mark.parametrize(
    "name,values,mutation",
    [
        ("DriftEvidenceV1", DRIFT_VALUES, ("comparison", "STATE")),
        (
            "AcknowledgementEvidenceV1",
            ACKNOWLEDGEMENT_VALUES,
            ("actor_ref", "changed"),
        ),
    ],
)
def test_payload_models_are_strict_frozen_and_extra_forbidden(name, values, mutation):
    model = getattr(evidence_module, name)
    instance = model.model_validate(values)
    with pytest.raises(ValidationError):
        model.model_validate(values | {"api_secret": "SECRET"})
    with pytest.raises(ValidationError):
        setattr(instance, *mutation)


@pytest.mark.parametrize(
    "name,values,changes",
    [
        ("DriftEvidenceV1", DRIFT_VALUES, {"product_id": True}),
        (
            "AcknowledgementEvidenceV1",
            ACKNOWLEDGEMENT_VALUES,
            {"actor_ref": 1},
        ),
    ],
)
def test_payload_models_reject_type_coercion(name, values, changes):
    model = getattr(evidence_module, name)
    with pytest.raises((ValidationError, PersistenceValueError)):
        model.model_validate(values | changes)


def test_literal_drift_payload_and_hash_vector():
    assert encode_drift_evidence(
        1,
        "UNKNOWN_EXPOSURE",
        "ACCOUNT",
        "CRITICAL",
        "NON_OVERRIDABLE",
        DRIFT_VALUES,
    ) == (
        '{"asset_id":null,"comparison":"UNCERTAINTY","expected_sha256":"'
        + HASH_B
        + '","observed_sha256":null,"product_id":84,"subject_key_sha256":"'
        + HASH_A
        + '"}',
        "ebb57154b8fec4b75f42e5accfd50fd4467c0e47eb288a48c2055fd45cddf936",
    )


def test_literal_acknowledgement_payload_and_hash_vector():
    assert encode_acknowledgement_evidence(
        1,
        DRIFT_ID,
        "NOTICE_EVIDENCE",
        "NON_OVERRIDABLE",
        "NONE",
        ACKNOWLEDGEMENT_VALUES,
    ) == (
        '{"actor_ref":"ops@example.invalid","idempotency_key":"ack-001",'
        '"reason_code":"OPERATOR_NOTED","supporting_evidence_sha256":null}',
        "75e6c747bc4c9bc470f007f45e802857e1d383402d8742f6ba3d56b4fc92f502",
    )


@pytest.mark.parametrize("field", sorted(DRIFT_VALUES))
def test_every_drift_payload_field_is_required(field):
    values = {key: value for key, value in DRIFT_VALUES.items() if key != field}
    with pytest.raises(PersistenceValueError):
        encode_drift_evidence(
            1,
            "UNKNOWN_EXPOSURE",
            "ACCOUNT",
            "CRITICAL",
            "NON_OVERRIDABLE",
            values,
        )


@pytest.mark.parametrize("field", sorted(ACKNOWLEDGEMENT_VALUES))
def test_every_acknowledgement_payload_field_is_required(field):
    values = {key: value for key, value in ACKNOWLEDGEMENT_VALUES.items() if key != field}
    with pytest.raises(PersistenceValueError):
        encode_acknowledgement_evidence(
            1,
            DRIFT_ID,
            "NOTICE_EVIDENCE",
            "NON_OVERRIDABLE",
            "NONE",
            values,
        )


@pytest.mark.parametrize("encoder", ["drift", "acknowledgement"])
def test_extra_or_secret_bearing_fields_are_rejected_without_echo(encoder):
    marker = "SECRET-RECONCILIATION-MARKER"
    with pytest.raises(PersistenceValueError) as exc:
        if encoder == "drift":
            encode_drift_evidence(
                1,
                "UNKNOWN_EXPOSURE",
                "ACCOUNT",
                "CRITICAL",
                "NON_OVERRIDABLE",
                DRIFT_VALUES | {"api_secret": marker},
            )
        else:
            encode_acknowledgement_evidence(
                1,
                DRIFT_ID,
                "NOTICE_EVIDENCE",
                "NON_OVERRIDABLE",
                "NONE",
                ACKNOWLEDGEMENT_VALUES | {"signature": marker},
            )
    assert marker not in str(exc.value) and marker not in repr(exc.value)


@pytest.mark.parametrize("version", [None, True, False, 0, 2, 1.0, "1"])
@pytest.mark.parametrize("encoder", ["drift", "acknowledgement"])
def test_evidence_schema_version_is_exactly_one(encoder, version):
    with pytest.raises(PersistenceValueError):
        if encoder == "drift":
            encode_drift_evidence(
                version,
                "UNKNOWN_EXPOSURE",
                "ACCOUNT",
                "CRITICAL",
                "NON_OVERRIDABLE",
                DRIFT_VALUES,
            )
        else:
            encode_acknowledgement_evidence(
                version,
                DRIFT_ID,
                "NOTICE_EVIDENCE",
                "NON_OVERRIDABLE",
                "NONE",
                ACKNOWLEDGEMENT_VALUES,
            )


@pytest.mark.parametrize(
    "field,values",
    [
        ("comparison", [None, "", "OTHER", 1, True]),
        ("drift_kind", [None, "", "READY", 1, True]),
        ("subject_kind", [None, "", "EXCHANGE_ID", 1, True]),
        ("severity", [None, "", "SAFE", 1, True]),
        ("acknowledgement_policy", [None, "", "OVERRIDES", 1, True]),
    ],
)
def test_drift_envelope_and_payload_enums_are_closed(field, values):
    for value in values:
        arguments = {
            "drift_kind": "UNKNOWN_EXPOSURE",
            "subject_kind": "ACCOUNT",
            "severity": "CRITICAL",
            "acknowledgement_policy": "NON_OVERRIDABLE",
        }
        payload = dict(DRIFT_VALUES)
        if field == "comparison":
            payload[field] = value
        else:
            arguments[field] = value
        with pytest.raises(PersistenceValueError):
            encode_drift_evidence(1, values=payload, **arguments)


@pytest.mark.parametrize("field", ["product_id", "asset_id"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", 2**63])
def test_optional_product_and_asset_ids_are_exact_positive_bigints(field, value):
    with pytest.raises(PersistenceValueError):
        encode_drift_evidence(
            1,
            "UNKNOWN_EXPOSURE",
            "ACCOUNT",
            "CRITICAL",
            "NON_OVERRIDABLE",
            DRIFT_VALUES | {field: value},
        )


def test_optional_drift_ids_and_hashes_are_explicit_null():
    values = DRIFT_VALUES | {
        "product_id": None,
        "asset_id": None,
        "expected_sha256": None,
        "observed_sha256": None,
    }
    payload, digest = encode_drift_evidence(
        1,
        "UNRESOLVED_OPERATION",
        "COMMAND",
        "CRITICAL",
        "NON_OVERRIDABLE",
        values,
    )
    for field in ("product_id", "asset_id", "expected_sha256", "observed_sha256"):
        assert f'"{field}":null' in payload
    validate_drift_evidence(
        1,
        "UNRESOLVED_OPERATION",
        "COMMAND",
        "CRITICAL",
        "NON_OVERRIDABLE",
        payload,
        digest,
    )


@pytest.mark.parametrize("field", ["subject_key_sha256", "expected_sha256", "observed_sha256"])
@pytest.mark.parametrize("value", [True, "A" * 64, "a" * 63, "g" * 64])
def test_drift_hashes_are_exact_lowercase_sha256(field, value):
    with pytest.raises(PersistenceValueError):
        encode_drift_evidence(
            1,
            "UNKNOWN_EXPOSURE",
            "ACCOUNT",
            "CRITICAL",
            "NON_OVERRIDABLE",
            DRIFT_VALUES | {field: value},
        )


@pytest.mark.parametrize(
    "drift_kind",
    [
        "RESOURCE_INCOMPLETE",
        "UNRESOLVED_OPERATION",
        "UNKNOWN_EXPOSURE",
        "INADEQUATE_PROTECTION",
        "UNCLASSIFIED",
    ],
)
@pytest.mark.parametrize(
    "severity,policy",
    [
        ("INFO", "REVIEWABLE"),
        ("WARNING", "NON_OVERRIDABLE"),
        ("CRITICAL", "REVIEWABLE"),
    ],
)
def test_hard_drift_kinds_are_critical_and_non_overridable(drift_kind, severity, policy):
    with pytest.raises(PersistenceValueError):
        encode_drift_evidence(
            1,
            drift_kind,
            "ACCOUNT",
            severity,
            policy,
            DRIFT_VALUES,
        )


@pytest.mark.parametrize(
    "field,values",
    [
        ("acknowledgement_kind", [None, "", "CLEARS_BLOCK", 1, True]),
        ("acknowledgement_policy", [None, "", "OVERRIDES", 1, True]),
        ("safety_effect", [None, "", "SAFE", 1, True]),
        ("reason_code", [None, "", "READY", 1, True]),
    ],
)
def test_acknowledgement_envelope_and_payload_enums_are_closed(field, values):
    for value in values:
        arguments = {
            "acknowledgement_kind": "NOTICE_EVIDENCE",
            "acknowledgement_policy": "NON_OVERRIDABLE",
            "safety_effect": "NONE",
        }
        payload = dict(ACKNOWLEDGEMENT_VALUES)
        if field == "reason_code":
            payload[field] = value
        else:
            arguments[field] = value
        with pytest.raises(PersistenceValueError):
            encode_acknowledgement_evidence(1, DRIFT_ID, values=payload, **arguments)


@pytest.mark.parametrize("value", [None, True, 1, str(DRIFT_ID), UUID(int=0)])
def test_parent_drift_id_is_an_exact_nonzero_uuid(value):
    with pytest.raises(PersistenceValueError):
        encode_acknowledgement_evidence(
            1,
            value,
            "NOTICE_EVIDENCE",
            "NON_OVERRIDABLE",
            "NONE",
            ACKNOWLEDGEMENT_VALUES,
        )


@pytest.mark.parametrize("field", ["actor_ref", "idempotency_key"])
@pytest.mark.parametrize("value", [None, "", " ", "\t", "x" * 129, "bad\x00text", 1, True])
def test_acknowledgement_identity_text_is_bounded_nonblank_and_nul_free(field, value):
    with pytest.raises(PersistenceValueError):
        encode_acknowledgement_evidence(
            1,
            DRIFT_ID,
            "NOTICE_EVIDENCE",
            "NON_OVERRIDABLE",
            "NONE",
            ACKNOWLEDGEMENT_VALUES | {field: value},
        )


@pytest.mark.parametrize("value", [True, "A" * 64, "a" * 63, "g" * 64])
def test_supporting_evidence_hash_is_nullable_exact_lowercase_sha256(value):
    with pytest.raises(PersistenceValueError):
        encode_acknowledgement_evidence(
            1,
            DRIFT_ID,
            "NOTICE_EVIDENCE",
            "NON_OVERRIDABLE",
            "NONE",
            ACKNOWLEDGEMENT_VALUES | {"supporting_evidence_sha256": value},
        )


@pytest.mark.parametrize(
    "policy,kind,reason,valid",
    [
        ("REVIEWABLE", "REVIEW_EVIDENCE", "EXPECTED_EXTERNAL_ACTIVITY", True),
        ("REVIEWABLE", "REVIEW_EVIDENCE", "ADOPTION_REVIEWED", True),
        ("REVIEWABLE", "REVIEW_EVIDENCE", "FALSE_POSITIVE", True),
        ("REVIEWABLE", "REVIEW_EVIDENCE", "OPERATOR_NOTED", True),
        ("REVIEWABLE", "NOTICE_EVIDENCE", "OPERATOR_NOTED", False),
        ("NON_OVERRIDABLE", "REVIEW_EVIDENCE", "OPERATOR_NOTED", False),
        ("NON_OVERRIDABLE", "NOTICE_EVIDENCE", "EXPECTED_EXTERNAL_ACTIVITY", False),
        ("NON_OVERRIDABLE", "NOTICE_EVIDENCE", "ADOPTION_REVIEWED", False),
        ("NON_OVERRIDABLE", "NOTICE_EVIDENCE", "FALSE_POSITIVE", False),
        ("NON_OVERRIDABLE", "NOTICE_EVIDENCE", "OPERATOR_NOTED", True),
    ],
)
def test_acknowledgement_policy_kind_reason_shape(policy, kind, reason, valid):
    def call():
        return encode_acknowledgement_evidence(
            1,
            DRIFT_ID,
            kind,
            policy,
            "NONE",
            ACKNOWLEDGEMENT_VALUES | {"reason_code": reason},
        )

    if valid:
        assert call()
    else:
        with pytest.raises(PersistenceValueError):
            call()


@pytest.mark.parametrize(
    "decoder,payload",
    [
        (decode_drift_evidence, "{}"),
        (decode_drift_evidence, "[]"),
        (decode_drift_evidence, '{"api_secret":"SECRET"}'),
        (decode_acknowledgement_evidence, "{}"),
        (decode_acknowledgement_evidence, "null"),
        (decode_acknowledgement_evidence, '{"operator_note":"SECRET"}'),
        (decode_drift_evidence, "x" * 2049),
        (decode_acknowledgement_evidence, "x" * 2049),
        (decode_drift_evidence, None),
        (decode_acknowledgement_evidence, True),
    ],
)
def test_stored_payload_must_be_exact_complete_and_canonical(decoder, payload):
    with pytest.raises(PersistenceValueError):
        decoder(payload)


def test_noncanonical_payload_bytes_are_rejected():
    drift_payload, _ = encode_drift_evidence(
        1,
        "UNKNOWN_EXPOSURE",
        "ACCOUNT",
        "CRITICAL",
        "NON_OVERRIDABLE",
        DRIFT_VALUES,
    )
    ack_payload, _ = encode_acknowledgement_evidence(
        1,
        DRIFT_ID,
        "NOTICE_EVIDENCE",
        "NON_OVERRIDABLE",
        "NONE",
        ACKNOWLEDGEMENT_VALUES,
    )
    with pytest.raises(PersistenceValueError):
        decode_drift_evidence(drift_payload.replace(":null", ": null", 1))
    with pytest.raises(PersistenceValueError):
        decode_acknowledgement_evidence(ack_payload.replace(":null", ": null", 1))


def test_stored_payload_round_trip_and_complete_digest_validation():
    drift_payload, drift_digest = encode_drift_evidence(
        1,
        "UNKNOWN_EXPOSURE",
        "ACCOUNT",
        "CRITICAL",
        "NON_OVERRIDABLE",
        DRIFT_VALUES,
    )
    assert decode_drift_evidence(drift_payload) == DRIFT_VALUES
    validate_drift_evidence(
        1,
        "UNKNOWN_EXPOSURE",
        "ACCOUNT",
        "CRITICAL",
        "NON_OVERRIDABLE",
        drift_payload,
        drift_digest,
    )
    ack_payload, ack_digest = encode_acknowledgement_evidence(
        1,
        DRIFT_ID,
        "NOTICE_EVIDENCE",
        "NON_OVERRIDABLE",
        "NONE",
        ACKNOWLEDGEMENT_VALUES,
    )
    assert decode_acknowledgement_evidence(ack_payload) == ACKNOWLEDGEMENT_VALUES
    validate_acknowledgement_evidence(
        1,
        DRIFT_ID,
        "NOTICE_EVIDENCE",
        "NON_OVERRIDABLE",
        "NONE",
        ack_payload,
        ack_digest,
    )


@pytest.mark.parametrize("digest", [None, True, "A" * 64, "a" * 63, "b" * 64])
@pytest.mark.parametrize("kind", ["drift", "acknowledgement"])
def test_validation_requires_exact_matching_lowercase_digest(kind, digest):
    if kind == "drift":
        payload, _ = encode_drift_evidence(
            1,
            "UNKNOWN_EXPOSURE",
            "ACCOUNT",
            "CRITICAL",
            "NON_OVERRIDABLE",
            DRIFT_VALUES,
        )
        with pytest.raises(PersistenceValueError):
            validate_drift_evidence(
                1,
                "UNKNOWN_EXPOSURE",
                "ACCOUNT",
                "CRITICAL",
                "NON_OVERRIDABLE",
                payload,
                digest,
            )
    else:
        payload, _ = encode_acknowledgement_evidence(
            1,
            DRIFT_ID,
            "NOTICE_EVIDENCE",
            "NON_OVERRIDABLE",
            "NONE",
            ACKNOWLEDGEMENT_VALUES,
        )
        with pytest.raises(PersistenceValueError):
            validate_acknowledgement_evidence(
                1,
                DRIFT_ID,
                "NOTICE_EVIDENCE",
                "NON_OVERRIDABLE",
                "NONE",
                payload,
                digest,
            )


def test_payload_adapters_validate_bind_and_result_paths():
    dialect = sqlite.dialect()
    drift_payload, _ = encode_drift_evidence(
        1,
        "UNKNOWN_EXPOSURE",
        "ACCOUNT",
        "CRITICAL",
        "NON_OVERRIDABLE",
        DRIFT_VALUES,
    )
    ack_payload, _ = encode_acknowledgement_evidence(
        1,
        DRIFT_ID,
        "NOTICE_EVIDENCE",
        "NON_OVERRIDABLE",
        "NONE",
        ACKNOWLEDGEMENT_VALUES,
    )
    for adapter, payload in (
        (DriftEvidencePayload(), drift_payload),
        (AcknowledgementEvidencePayload(), ack_payload),
    ):
        assert adapter.process_bind_param(payload, dialect) == payload
        assert adapter.process_result_value(payload, dialect) == payload
        with pytest.raises(PersistenceValueError):
            adapter.process_bind_param("{}", dialect)
        with pytest.raises(PersistenceValueError):
            adapter.process_result_value("{}", dialect)


def test_caller_containers_are_not_retained():
    drift = deepcopy(DRIFT_VALUES)
    drift_payload, drift_digest = encode_drift_evidence(
        1,
        "UNKNOWN_EXPOSURE",
        "ACCOUNT",
        "CRITICAL",
        "NON_OVERRIDABLE",
        drift,
    )
    drift["subject_key_sha256"] = "c" * 64
    validate_drift_evidence(
        1,
        "UNKNOWN_EXPOSURE",
        "ACCOUNT",
        "CRITICAL",
        "NON_OVERRIDABLE",
        drift_payload,
        drift_digest,
    )
    acknowledgement = deepcopy(ACKNOWLEDGEMENT_VALUES)
    ack_payload, ack_digest = encode_acknowledgement_evidence(
        1,
        DRIFT_ID,
        "NOTICE_EVIDENCE",
        "NON_OVERRIDABLE",
        "NONE",
        acknowledgement,
    )
    acknowledgement["actor_ref"] = "changed"
    validate_acknowledgement_evidence(
        1,
        DRIFT_ID,
        "NOTICE_EVIDENCE",
        "NON_OVERRIDABLE",
        "NONE",
        ack_payload,
        ack_digest,
    )
