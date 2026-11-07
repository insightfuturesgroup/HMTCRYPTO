"""Round-one D9c review regressions for strict canonical ownership."""

from datetime import timedelta
from uuid import UUID

import pytest
from risk_rate_consumption_test_support import (
    ATTEMPT_PREPARED,
    ATTEMPT_RECORDED,
    CONSUMPTION_KEY,
    DECISION_EVALUATED,
    DECISION_RECORDED,
    DISPATCH_RECORDED,
    RESERVATION_CREATED,
    WINDOW_END,
    WINDOW_START,
    evidence_values,
    key_values,
)
from sqlalchemy.dialects import sqlite

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_rate_consumption_evidence import (
    MAX_EVIDENCE_BYTES,
    RiskRateConsumptionPayload,
    derive_consumption_key,
    encode_risk_rate_consumption_evidence,
)

KEY_MUTATIONS = (
    ("account_id", UUID("21000000-0000-4000-8000-000000000002")),
    ("region", "global"),
    ("environment", "prod"),
    ("mode", "BACKTEST"),
    ("reservation_id", UUID("91000000-0000-4000-8000-000000000009")),
    ("decision_recorded_at", DECISION_RECORDED + timedelta(microseconds=1)),
    ("reservation_created_at", RESERVATION_CREATED + timedelta(microseconds=1)),
    ("order_attempt_id", UUID("a1000000-0000-4000-8000-00000000000a")),
)
VALID_EVIDENCE_MUTATIONS = (
    ("consumption_id", UUID("d1000000-0000-4000-8000-00000000000d")),
    ("account_id", UUID("21000000-0000-4000-8000-000000000002")),
    ("region", "global"),
    ("environment", "prod"),
    ("mode", "BACKTEST"),
    ("reservation_id", UUID("91000000-0000-4000-8000-000000000009")),
    ("decision_id", UUID("11000000-0000-4000-8000-000000000001")),
    ("risk_state_id", UUID("31000000-0000-4000-8000-000000000003")),
    ("command_id", UUID("41000000-0000-4000-8000-000000000004")),
    ("order_id", UUID("51000000-0000-4000-8000-000000000005")),
    ("risk_effect", "REDUCE_ONLY"),
    ("reservation_plan_sha256", "e" * 64),
    ("decision_evaluated_at", DECISION_EVALUATED + timedelta(microseconds=1)),
    ("decision_recorded_at", DECISION_RECORDED + timedelta(microseconds=1)),
    ("reservation_created_at", RESERVATION_CREATED + timedelta(microseconds=1)),
    ("order_attempt_id", UUID("a1000000-0000-4000-8000-00000000000a")),
    ("attempt_number", 2),
    ("attempt_prepared_at", ATTEMPT_PREPARED + timedelta(microseconds=1)),
    ("attempt_recorded_at", ATTEMPT_RECORDED + timedelta(microseconds=1)),
    ("dispatch_event_id", UUID("b1000000-0000-4000-8000-00000000000b")),
    ("rate_policy_version", 2),
    ("rate_policy_sha256", "e" * 64),
    ("rate_window_start_at", WINDOW_START - timedelta(microseconds=1)),
    ("rate_window_end_at", WINDOW_END + timedelta(microseconds=1)),
    ("recorded_at", DISPATCH_RECORDED + timedelta(microseconds=1)),
)
REJECTED_EVIDENCE_MUTATIONS = (
    ("command_operation_kind", "MODIFY_ORDER"),
    ("product_id", 1102),
    ("decision_outcome", "DENIED"),
    ("attempt_backend", "VENUE"),
    ("attempt_evidence_schema_version", 2),
    ("attempt_evidence_payload", "{}"),
    ("attempt_evidence_sha256", "e" * 64),
    ("dispatch_event_sequence", 2),
    ("dispatch_event_kind", "RESPONSE_ACCEPTED"),
    ("dispatch_event_evidence_schema_version", 2),
    ("dispatch_event_evidence_payload", '{"outcome":"ACCEPTED"}'),
    ("dispatch_event_evidence_sha256", "e" * 64),
    ("consumed_units", 2),
)


@pytest.mark.parametrize("boundary", ["bind", "result"])
def test_payload_adapter_rejects_hostile_text_without_executing_callbacks(boundary):
    touched = False

    class HostileText(str):
        def __contains__(self, item):
            nonlocal touched
            touched = True
            raise RuntimeError("private-marker")

    payload = encode_risk_rate_consumption_evidence(1, evidence_values()).payload
    adapter = RiskRateConsumptionPayload()
    method = adapter.process_bind_param if boundary == "bind" else adapter.process_result_value

    with pytest.raises(PersistenceValueError) as error:
        method(HostileText(payload), sqlite.dialect())

    assert not touched
    assert "private-marker" not in str(error.value) + repr(error.value)


def test_exact_field_ownership_rejects_hostile_string_subclass_key_without_callback():
    touched = False

    class HostileKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            nonlocal touched
            touched = True
            return str.__eq__(self, other)

    values = key_values()
    account_id = values.pop("account_id")
    values[HostileKey("account_id")] = account_id

    with pytest.raises(PersistenceValueError):
        derive_consumption_key(1, values)

    assert not touched


@pytest.mark.parametrize("boundary", ["bind", "result"])
def test_standalone_payload_adapter_preserves_valid_payload_and_enforces_cap(boundary):
    payload = encode_risk_rate_consumption_evidence(1, evidence_values()).payload
    adapter = RiskRateConsumptionPayload()
    method = adapter.process_bind_param if boundary == "bind" else adapter.process_result_value

    assert method(payload, sqlite.dialect()) == payload
    assert method(None, sqlite.dialect()) is None
    with pytest.raises(PersistenceValueError):
        method("x" * (MAX_EVIDENCE_BYTES + 1), sqlite.dialect())


@pytest.mark.parametrize(
    "field,replacement",
    KEY_MUTATIONS,
)
def test_every_consumption_key_input_mutation_changes_the_literal_key(field, replacement):
    assert derive_consumption_key(1, key_values(**{field: replacement})) != CONSUMPTION_KEY


@pytest.mark.parametrize(
    "field,replacement",
    VALID_EVIDENCE_MUTATIONS,
)
def test_every_valid_consumption_evidence_input_mutation_changes_the_digest(field, replacement):
    original = encode_risk_rate_consumption_evidence(1, evidence_values())
    changed = encode_risk_rate_consumption_evidence(1, evidence_values(**{field: replacement}))
    assert changed.sha256 != original.sha256


@pytest.mark.parametrize(
    "field,replacement",
    REJECTED_EVIDENCE_MUTATIONS,
)
def test_every_closed_or_coupled_evidence_input_rejects_isolated_mutation(field, replacement):
    with pytest.raises(PersistenceValueError):
        encode_risk_rate_consumption_evidence(1, evidence_values(**{field: replacement}))


def test_mutation_matrix_covers_every_owned_consumption_input_field():
    exercised = {field for field, _ in (*VALID_EVIDENCE_MUTATIONS, *REJECTED_EVIDENCE_MUTATIONS)}
    assert exercised == {
        "consumption_id",
        "account_id",
        "region",
        "environment",
        "mode",
        "reservation_id",
        "decision_id",
        "risk_state_id",
        "command_id",
        "command_operation_kind",
        "order_id",
        "product_id",
        "decision_outcome",
        "risk_effect",
        "reservation_plan_sha256",
        "decision_evaluated_at",
        "decision_recorded_at",
        "reservation_created_at",
        "order_attempt_id",
        "attempt_number",
        "attempt_backend",
        "attempt_prepared_at",
        "attempt_recorded_at",
        "attempt_evidence_schema_version",
        "attempt_evidence_payload",
        "attempt_evidence_sha256",
        "dispatch_event_id",
        "dispatch_event_sequence",
        "dispatch_event_kind",
        "dispatch_event_evidence_schema_version",
        "dispatch_event_evidence_payload",
        "dispatch_event_evidence_sha256",
        "rate_policy_version",
        "rate_policy_sha256",
        "rate_window_start_at",
        "rate_window_end_at",
        "consumed_units",
        "recorded_at",
    }
