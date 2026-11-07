"""Strict release-handoff decoder for complete attempt-set evidence."""

import hashlib
import json
from datetime import datetime
from typing import cast
from uuid import UUID

from hmt_crypto.persistence.risk_reservation_attempt_codec import (
    _canonical,
    _owned_attempt,
    _stored_attempt,
    _validate_attempt,
)
from hmt_crypto.persistence.risk_reservation_contracts import (
    AttemptEventEvidenceV1,
    AttemptEvidenceSetEncoding,
    AttemptEvidenceV1,
    _enum,
    _fail,
    _hash,
    _integer,
    _is_owned_attempt_evidence_set_encoding,
    _timestamp,
)
from hmt_crypto.persistence.risk_reservation_fields import (
    ATTEMPT_SET_PAYLOAD_FIELDS,
    MAX_ATTEMPT_SET_CANONICAL_BYTES,
    MAX_RELEASE_PROOF_ATTEMPTS,
    MAX_RELEASE_PROOF_EVENTS,
)

_ATTEMPT_UUID_FIELDS = {
    "id",
    "account_id",
    "command_id",
    "order_id",
    "correlation_id",
    "original_claim_owner",
    "original_leader_ref",
    "original_recovery_ref",
}
_ATTEMPT_TIME_FIELDS = {
    "prepared_at",
    "recorded_at",
    "original_claim_expires_at",
}
_EVENT_UUID_FIELDS = {
    "id",
    "account_id",
    "attempt_id",
    "ingester_id",
    "reconciliation_ref",
    "ingester_leader_ref",
    "ingester_recovery_ref",
}
_EVENT_TIME_FIELDS = {"observed_at", "recorded_at"}


def _stored_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    if type(value) is not str:
        _fail()
    parsed = UUID(value)
    if str(parsed) != value:
        _fail()
    return parsed


def _stored_time(value: object) -> datetime | None:
    if value is None:
        return None
    if type(value) is not str:
        _fail()
    parsed = datetime.fromisoformat(value)
    if _timestamp(parsed) != value:
        _fail()
    return parsed


def _stored_event_input(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != set(AttemptEventEvidenceV1.model_fields):
        _fail()
    result = dict(value)
    for field in _EVENT_UUID_FIELDS:
        result[field] = _stored_uuid(result[field])
    for field in _EVENT_TIME_FIELDS:
        result[field] = _stored_time(result[field])
    return result


def _stored_attempt_input(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != set(AttemptEvidenceV1.model_fields):
        _fail()
    result = dict(value)
    for field in _ATTEMPT_UUID_FIELDS:
        result[field] = _stored_uuid(result[field])
    for field in _ATTEMPT_TIME_FIELDS:
        result[field] = _stored_time(result[field])
    events = result["events"]
    if type(events) is not list or len(events) != 1:
        _fail()
    result["events"] = [_stored_event_input(events[0])]
    return result


def _strict_attempt_rows(
    attempts: list[object],
    release_values: dict[str, object],
    current_intent: dict[str, object],
) -> None:
    if not attempts:
        return
    converted = [_stored_attempt_input(item) for item in attempts]
    context = {
        "account_id": release_values["account_id"],
        "region": release_values["region"],
        "environment": release_values["environment"],
        "mode": release_values["mode"],
        "reservation_id": release_values["reservation_id"],
        "decision_id": release_values["decision_id"],
        "risk_state_id": release_values["risk_state_id"],
        "command_id": release_values["command_id"],
        "command_operation_kind": release_values["command_operation_kind"],
        "order_id": release_values["order_id"],
        "product_id": release_values["product_id"],
        "proof_observed_at": release_values["proof_observed_at"],
        "command_evidence_schema_version": current_intent[
            "current_command_evidence_schema_version"
        ],
        "command_evidence_payload": current_intent["current_command_evidence_payload"],
        "command_evidence_sha256": current_intent["current_command_evidence_sha256"],
    }
    owned = tuple(
        _validate_attempt(
            _owned_attempt(item, remaining_event_count=len(converted) - index),
            context,
        )
        for index, item in enumerate(converted)
    )
    ordered = tuple(sorted(owned, key=lambda item: (item.attempt_number, item.id.int)))
    if owned != ordered:
        _fail()
    if len({item.id for item in owned}) != len(owned) or len(
        {item.attempt_number for item in owned}
    ) != len(owned):
        _fail()
    event_ids = [event.id for item in owned for event in item.events]
    event_keys = [event.event_key for item in owned for event in item.events]
    if len(set(event_ids)) != len(event_ids) or len(set(event_keys)) != len(event_keys):
        _fail()
    if [_stored_attempt(item) for item in owned] != attempts:
        _fail()


def _strict_outer_payload(value: object) -> dict[str, object]:
    if type(value) is not dict or set(value) != ATTEMPT_SET_PAYLOAD_FIELDS:
        _fail()
    result = dict(value)
    for field in (
        "account_id",
        "command_id",
        "decision_id",
        "order_id",
        "reservation_id",
        "risk_state_id",
    ):
        parsed = _stored_uuid(result[field])
        if parsed is None:
            _fail()
        result[field] = parsed
    for field in (
        "observed_command_attempt_count",
        "order_attempt_evidence_count",
        "order_attempt_event_evidence_count",
    ):
        result[field] = _integer(result[field], positive=False)
    result["product_id"] = _integer(result["product_id"])
    observed = _stored_time(result["proof_observed_at"])
    if observed is None:
        _fail()
    result["proof_observed_at"] = observed
    result["command_operation_kind"] = _enum(
        result["command_operation_kind"], ("PLACE_ORDER", "MODIFY_ORDER")
    )
    result["region"] = _enum(result["region"], ("india", "global"))
    result["environment"] = _enum(result["environment"], ("testnet", "prod"))
    result["mode"] = _enum(result["mode"], ("BACKTEST", "PAPER", "LIVE"))
    if type(result["attempts"]) is not list:
        _fail()
    return result


def validate_attempt_evidence_set_handoff(
    value: object,
    release_values: dict[str, object],
    current_intent: dict[str, object],
) -> AttemptEvidenceSetEncoding:
    """Revalidate the complete canonical set and exact release lineage."""
    if not _is_owned_attempt_evidence_set_encoding(value):
        _fail()
    assert isinstance(value, AttemptEvidenceSetEncoding)
    if type(value.canonical) is not str or len(value.canonical) > MAX_ATTEMPT_SET_CANONICAL_BYTES:
        _fail()
    canonical_bytes = value.canonical.encode("ascii")
    if len(canonical_bytes) > MAX_ATTEMPT_SET_CANONICAL_BYTES or hashlib.sha256(
        canonical_bytes
    ).hexdigest() != _hash(value.sha256):
        _fail()
    command_count = _integer(value.observed_command_attempt_count, positive=False)
    attempt_count = _integer(value.attempt_count, positive=False)
    event_count = _integer(value.event_count, positive=False)
    assert command_count is not None and attempt_count is not None and event_count is not None
    if (
        command_count > MAX_RELEASE_PROOF_ATTEMPTS
        or attempt_count > MAX_RELEASE_PROOF_ATTEMPTS
        or event_count > MAX_RELEASE_PROOF_EVENTS
        or attempt_count != event_count
    ):
        _fail()
    raw = json.loads(value.canonical)
    if (
        type(raw) is not dict
        or set(raw) != {"kind", "payload", "schema_version"}
        or type(raw["kind"]) is not str
        or raw["kind"] != "risk_reservation_release_attempt_evidence_set"
        or type(raw["schema_version"]) is not int
        or raw["schema_version"] != 1
        or _canonical(raw) != value.canonical
    ):
        _fail()
    payload = _strict_outer_payload(raw["payload"])
    expected = {
        "account_id": release_values["account_id"],
        "command_id": release_values["command_id"],
        "command_operation_kind": release_values["command_operation_kind"],
        "decision_id": release_values["decision_id"],
        "environment": release_values["environment"],
        "mode": release_values["mode"],
        "order_id": release_values["order_id"],
        "product_id": release_values["product_id"],
        "proof_observed_at": cast(datetime, release_values["proof_observed_at"]),
        "region": release_values["region"],
        "reservation_id": release_values["reservation_id"],
        "risk_state_id": release_values["risk_state_id"],
    }
    if any(payload[field] != expected[field] for field in expected):
        _fail()
    attempts = payload["attempts"]
    if type(attempts) is not list or len(attempts) != attempt_count:
        _fail()
    _strict_attempt_rows(attempts, release_values, current_intent)
    nested_event_count = 0
    for attempt in attempts:
        if type(attempt) is not dict or type(attempt.get("events")) is not list:
            _fail()
        nested_event_count += len(attempt["events"])
    if (
        payload["observed_command_attempt_count"] != command_count
        or payload["order_attempt_evidence_count"] != attempt_count
        or payload["order_attempt_event_evidence_count"] != event_count
        or nested_event_count != event_count
    ):
        _fail()
    return value
