"""Canonical reservation keys and bounded complete attempt-set evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import validate_stored_evidence
from hmt_crypto.persistence.risk_reservation_contracts import (
    AttemptEventEvidenceV1,
    AttemptEvidenceSetEncoding,
    AttemptEvidenceV1,
    _enum,
    _fail,
    _hash,
    _integer,
    _new_attempt_evidence_set_encoding,
    _time,
    _timestamp,
    _uuid,
)
from hmt_crypto.persistence.risk_reservation_fields import (
    ATTEMPT_FIELDS,
    ATTEMPT_SET_INPUT_FIELDS,
    ATTEMPT_SET_PAYLOAD_FIELDS,
    MAX_ATTEMPT_SET_CANONICAL_BYTES,
    MAX_RELEASE_PROOF_ATTEMPTS,
    MAX_RELEASE_PROOF_EVENTS,
    RELEASE_KEY_FIELDS,
    RESERVATION_KEY_FIELDS,
)
from hmt_crypto.persistence.risk_reservation_intent import validate_current_intent_snapshot
from hmt_crypto.persistence.types import _decimal_text


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _owned_mapping(value: object, fields: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        _fail()
    return dict(value)


def _json_value(value: object) -> object:
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return _timestamp(value)
    if type(value) is Decimal:
        return _decimal_text(value, 38, 18)
    if value is None or type(value) in {str, int, bool}:
        return value
    _fail()


def _key_context(value: object) -> dict[str, object]:
    values = _owned_mapping(value, RESERVATION_KEY_FIELDS)
    for field in ("account_id", "command_id", "decision_id", "order_id", "risk_state_id"):
        values[field] = _uuid(values[field])
    values["command_operation_kind"] = _enum(
        values["command_operation_kind"], ("PLACE_ORDER", "MODIFY_ORDER")
    )
    values["decision_outcome"] = _enum(values["decision_outcome"], ("APPROVED",))
    values["risk_effect"] = _enum(values["risk_effect"], ("RISK_INCREASE", "REDUCE_ONLY"))
    values["region"] = _enum(values["region"], ("india", "global"))
    values["environment"] = _enum(values["environment"], ("testnet", "prod"))
    values["mode"] = _enum(values["mode"], ("BACKTEST", "PAPER", "LIVE"))
    values["product_id"] = _integer(values["product_id"])
    values["reservation_plan_sha256"] = _hash(values["reservation_plan_sha256"])
    values["decision_evaluated_at"] = _time(values["decision_evaluated_at"])
    values["decision_recorded_at"] = _time(values["decision_recorded_at"])
    evaluated = cast(datetime, values["decision_evaluated_at"])
    recorded = cast(datetime, values["decision_recorded_at"])
    if evaluated > recorded:
        _fail()
    return values


def derive_reservation_key(schema_version: object, values: object) -> str:
    """Derive the scoped idempotency key for one immutable approved decision."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            _fail()
        context = _key_context(values)
        payload = {field: _json_value(context[field]) for field in RESERVATION_KEY_FIELDS}
        envelope = {
            "kind": "risk_reservation_key",
            "payload": payload,
            "schema_version": 1,
        }
        return hashlib.sha256(_canonical(envelope).encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def _release_key_context(value: object) -> dict[str, object]:
    values = _owned_mapping(value, RELEASE_KEY_FIELDS)
    for field in ("account_id", "command_id", "decision_id", "order_id", "reservation_id"):
        values[field] = _uuid(values[field])
    values["command_operation_kind"] = _enum(
        values["command_operation_kind"], ("PLACE_ORDER", "MODIFY_ORDER")
    )
    values["observed_terminal_command_status"] = _enum(
        values["observed_terminal_command_status"], ("FAILED", "CANCELLED")
    )
    values["region"] = _enum(values["region"], ("india", "global"))
    values["environment"] = _enum(values["environment"], ("testnet", "prod"))
    values["mode"] = _enum(values["mode"], ("BACKTEST", "PAPER", "LIVE"))
    values["product_id"] = _integer(values["product_id"])
    values["reservation_key"] = _hash(values["reservation_key"])
    return values


def derive_release_key(schema_version: object, values: object) -> str:
    """Derive the terminal release identity; callers cannot select its reason/version."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            _fail()
        context = _release_key_context(values)
        payload = {field: _json_value(context[field]) for field in RELEASE_KEY_FIELDS}
        payload.update(
            release_proof_version=1,
            release_reason_code="DEFINITE_NOT_DISPATCHED",
        )
        envelope = {
            "kind": "risk_reservation_release_key",
            "payload": payload,
            "schema_version": 1,
        }
        return hashlib.sha256(_canonical(envelope).encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def guard_attempt_set_canonical_bytes(value: object) -> bytes:
    """Admit an exact bounded ASCII byte sequence without claiming semantic validity."""
    try:
        if type(value) is not bytes or len(value) > MAX_ATTEMPT_SET_CANONICAL_BYTES:
            _fail()
        value.decode("ascii")
        return value
    except Exception:
        raise PersistenceValueError() from None


def _attempt_set_context(value: object) -> dict[str, object]:
    values = _owned_mapping(value, ATTEMPT_SET_INPUT_FIELDS)
    for field in (
        "account_id",
        "reservation_id",
        "decision_id",
        "risk_state_id",
        "command_id",
        "order_id",
    ):
        values[field] = _uuid(values[field])
    values["region"] = _enum(values["region"], ("india", "global"))
    values["environment"] = _enum(values["environment"], ("testnet", "prod"))
    values["mode"] = _enum(values["mode"], ("BACKTEST", "PAPER", "LIVE"))
    values["command_operation_kind"] = _enum(
        values["command_operation_kind"], ("PLACE_ORDER", "MODIFY_ORDER")
    )
    values["product_id"] = _integer(values["product_id"])
    values["proof_observed_at"] = _time(values["proof_observed_at"])
    return values


def _owned_attempt(value: object, *, remaining_event_count: int) -> AttemptEvidenceV1:
    if type(value) is AttemptEvidenceV1:
        value = value.model_dump(mode="python")
    if type(value) is not dict or set(value) != ATTEMPT_FIELDS:
        _fail()
    raw = dict(value)
    events = raw["events"]
    if type(events) not in {list, tuple}:
        _fail()
    source_events = cast(list[object] | tuple[object, ...], events)
    if len(source_events) != 1 or remaining_event_count < 1:
        _fail()
    raw["events"] = tuple(
        AttemptEventEvidenceV1.model_validate(
            item.model_dump(mode="python") if type(item) is AttemptEventEvidenceV1 else item
        )
        for item in source_events
    )
    return AttemptEvidenceV1.model_validate(raw)


def _pair(ref: object, generation: object) -> bool:
    return (ref is None and generation is None) or (ref is not None and generation is not None)


def _validate_attempt(attempt: AttemptEvidenceV1, context: dict[str, object]) -> AttemptEvidenceV1:
    proof_observed_at = cast(datetime, context["proof_observed_at"])
    expected_scope = (
        context["account_id"],
        context["region"],
        context["environment"],
        context["mode"],
    )
    if (
        (attempt.account_id, attempt.region, attempt.environment, attempt.mode) != expected_scope
        or attempt.command_id != context["command_id"]
        or attempt.order_id != context["order_id"]
        or attempt.operation_kind != context["command_operation_kind"]
        or attempt.evidence_schema_version != context["command_evidence_schema_version"]
        or attempt.evidence_payload != context["command_evidence_payload"]
        or attempt.evidence_sha256 != context["command_evidence_sha256"]
        or attempt.recorded_at < attempt.prepared_at
        or attempt.prepared_at > proof_observed_at
        or attempt.recorded_at > proof_observed_at
        or not _pair(attempt.original_leader_ref, attempt.original_leader_generation)
        or not _pair(attempt.original_recovery_ref, attempt.original_recovery_generation)
    ):
        _fail()
    claim = (
        attempt.original_claim_owner,
        attempt.original_claim_generation,
        attempt.original_claim_expires_at,
    )
    if not (all(item is None for item in claim) or all(item is not None for item in claim)):
        _fail()
    if attempt.original_claim_expires_at is not None and (
        attempt.original_claim_expires_at <= attempt.prepared_at
    ):
        _fail()
    if attempt.mode == "LIVE":
        if (
            attempt.backend != "VENUE"
            or attempt.simulator_kind is not None
            or attempt.credential_version is None
            or attempt.original_claim_owner is None
            or attempt.original_leader_ref is None
        ):
            _fail()
    elif (
        attempt.backend != "SIMULATOR"
        or attempt.simulator_kind != attempt.mode
        or attempt.credential_version is not None
        or any(item is not None for item in claim)
        or attempt.original_leader_ref is not None
        or attempt.original_recovery_ref is not None
    ):
        _fail()
    validate_stored_evidence(
        "intent",
        attempt.operation_kind,
        attempt.evidence_schema_version,
        attempt.evidence_payload,
        attempt.evidence_sha256,
    )
    if len(attempt.events) != 1:
        _fail()
    event = AttemptEventEvidenceV1.model_validate(attempt.events[0])
    if (
        (event.account_id, event.region, event.environment, event.mode) != expected_scope
        or event.attempt_id != attempt.id
        or event.backend != attempt.backend
        or event.sequence != 1
        or event.event_kind != "DEFINITE_NOT_SENT"
        or event.recorded_at > proof_observed_at
        or (event.observed_at is not None and event.observed_at > event.recorded_at)
        or not _pair(event.ingester_id, event.ingester_generation)
        or not _pair(event.ingester_leader_ref, event.ingester_leader_generation)
        or not _pair(event.ingester_recovery_ref, event.ingester_recovery_generation)
    ):
        _fail()
    validate_stored_evidence(
        "event",
        event.event_kind,
        event.evidence_schema_version,
        event.evidence_payload,
        event.evidence_sha256,
    )
    if json.loads(event.evidence_payload) != {"outcome": "NOT_SENT"}:
        _fail()
    return attempt.model_copy(update={"events": (event,)})


def _stored_event(event: AttemptEventEvidenceV1) -> dict[str, object]:
    return {
        field: _json_value(getattr(event, field))
        for field in sorted(AttemptEventEvidenceV1.model_fields)
    }


def _stored_attempt(attempt: AttemptEvidenceV1) -> dict[str, object]:
    result = {
        field: _json_value(getattr(attempt, field))
        for field in sorted(AttemptEvidenceV1.model_fields)
        if field != "events"
    }
    result["events"] = [_stored_event(event) for event in attempt.events]
    return result


def _append_bounded(buffer: bytearray, fragment: str) -> None:
    raw = fragment.encode("ascii")
    if len(buffer) + len(raw) > MAX_ATTEMPT_SET_CANONICAL_BYTES:
        _fail()
    buffer.extend(raw)


def encode_attempt_evidence_set(
    schema_version: object,
    values: object,
    attempts: object,
    *,
    current_intent: object,
    observed_command_attempt_count: object,
    locked_attempt_count: object,
    locked_event_count: object,
) -> AttemptEvidenceSetEncoding:
    """Validate one complete bounded query result and hash it incrementally."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            _fail()
        context = _attempt_set_context(values)
        current = validate_current_intent_snapshot(current_intent, context)
        for field in ("schema_version", "payload", "sha256"):
            context[f"command_evidence_{field}"] = current[f"current_command_evidence_{field}"]
        command_count = _integer(observed_command_attempt_count, positive=False)
        attempt_count = _integer(locked_attempt_count, positive=False)
        event_count = _integer(locked_event_count, positive=False)
        assert command_count is not None and attempt_count is not None and event_count is not None
        if type(attempts) not in {list, tuple}:
            _fail()
        source_attempts = cast(list[object] | tuple[object, ...], attempts)
        if (
            command_count > MAX_RELEASE_PROOF_ATTEMPTS
            or attempt_count > MAX_RELEASE_PROOF_ATTEMPTS
            or event_count > MAX_RELEASE_PROOF_EVENTS
            or event_count != attempt_count
            or len(source_attempts) != attempt_count
            or len(source_attempts) > MAX_RELEASE_PROOF_ATTEMPTS
        ):
            _fail()
        remaining_event_count = event_count
        owned_values: list[AttemptEvidenceV1] = []
        for item in source_attempts:
            owned_values.append(
                _validate_attempt(
                    _owned_attempt(item, remaining_event_count=remaining_event_count),
                    context,
                )
            )
            remaining_event_count -= 1
        if remaining_event_count != 0:
            _fail()
        owned = tuple(owned_values)
        ordered = tuple(sorted(owned, key=lambda item: (item.attempt_number, item.id.int)))
        if len({item.id for item in ordered}) != len(ordered) or len(
            {item.attempt_number for item in ordered}
        ) != len(ordered):
            _fail()
        event_ids = [event.id for item in ordered for event in item.events]
        event_keys = [event.event_key for item in ordered for event in item.events]
        if len(set(event_ids)) != len(event_ids) or len(set(event_keys)) != len(event_keys):
            _fail()
        payload: dict[str, object] = {
            "account_id": str(context["account_id"]),
            "attempts": None,
            "command_id": str(context["command_id"]),
            "command_operation_kind": context["command_operation_kind"],
            "decision_id": str(context["decision_id"]),
            "environment": context["environment"],
            "mode": context["mode"],
            "observed_command_attempt_count": command_count,
            "order_attempt_event_evidence_count": event_count,
            "order_attempt_evidence_count": attempt_count,
            "order_id": str(context["order_id"]),
            "product_id": context["product_id"],
            "proof_observed_at": _timestamp(cast(datetime, context["proof_observed_at"])),
            "region": context["region"],
            "reservation_id": str(context["reservation_id"]),
            "risk_state_id": str(context["risk_state_id"]),
        }
        if set(payload) != ATTEMPT_SET_PAYLOAD_FIELDS:
            _fail()
        prefix = '{"kind":"risk_reservation_release_attempt_evidence_set","payload":{'
        prefix += '"account_id":' + _canonical(payload.pop("account_id"))
        prefix += ',"attempts":['
        buffer = bytearray()
        _append_bounded(buffer, prefix)
        for index, item in enumerate(ordered):
            if index:
                _append_bounded(buffer, ",")
            _append_bounded(buffer, _canonical(_stored_attempt(item)))
        _append_bounded(buffer, "]")
        for key in sorted(payload):
            if key == "attempts":
                continue
            _append_bounded(buffer, "," + _canonical(key) + ":" + _canonical(payload[key]))
        _append_bounded(buffer, '},"schema_version":1}')
        canonical_bytes = guard_attempt_set_canonical_bytes(bytes(buffer))
        return _new_attempt_evidence_set_encoding(
            canonical=canonical_bytes.decode("ascii"),
            sha256=hashlib.sha256(canonical_bytes).hexdigest(),
            observed_command_attempt_count=command_count,
            attempt_count=attempt_count,
            event_count=event_count,
        )
    except Exception:
        raise PersistenceValueError() from None
