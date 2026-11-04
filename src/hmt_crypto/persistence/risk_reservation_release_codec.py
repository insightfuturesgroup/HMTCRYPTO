"""Canonical complete pre-dispatch release-proof encoding and storage adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, cast
from uuid import UUID

from sqlalchemy.engine import Dialect

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_reservation_attempt_codec import (
    _canonical,
    _json_value,
    _key_context,
    _owned_mapping,
    derive_release_key,
    derive_reservation_key,
)
from hmt_crypto.persistence.risk_reservation_attempt_decode import (
    validate_attempt_evidence_set_handoff,
)
from hmt_crypto.persistence.risk_reservation_contracts import (
    AttemptEvidenceSetEncoding,
    RiskReservationReleaseEncoding,
    _decimal,
    _enum,
    _fail,
    _hash,
    _integer,
    _text,
    _time,
    _timestamp,
    _uuid,
)
from hmt_crypto.persistence.risk_reservation_fields import (
    MAX_RELEASE_PROOF_ATTEMPTS,
    MAX_RELEASE_PROOF_EVENTS,
    MAX_RELEASE_PROOF_PAYLOAD_BYTES,
    RELEASE_DERIVED_INPUT_FIELDS,
    RELEASE_INPUT_FIELDS,
    RELEASE_KEY_FIELDS,
    RELEASE_PAYLOAD_FIELDS,
    RESERVATION_KEY_FIELDS,
)
from hmt_crypto.persistence.risk_reservation_intent import validate_current_intent_snapshot
from hmt_crypto.persistence.schema_support import NulFreeText


def guard_release_proof_payload_bytes(value: object) -> bytes:
    """Enforce the exact stored canonical payload byte ceiling."""
    try:
        if type(value) is not bytes or len(value) > MAX_RELEASE_PROOF_PAYLOAD_BYTES:
            _fail()
        value.decode("ascii")
        return value
    except Exception:
        raise PersistenceValueError() from None


def _release_values(value: object) -> dict[str, object]:
    if type(value) is not dict or RELEASE_DERIVED_INPUT_FIELDS & set(value):
        _fail()
    values = _owned_mapping(value, RELEASE_INPUT_FIELDS)
    key_context = _key_context({field: values[field] for field in RESERVATION_KEY_FIELDS})
    values.update(key_context)
    values["reservation_id"] = _uuid(values["reservation_id"])
    values["reservation_key"] = _hash(values["reservation_key"])
    if values["reservation_key"] != derive_reservation_key(1, key_context):
        _fail()
    for field in (
        "approved_contracts",
        "reference_asset_id",
        "collateral_asset_id",
        "planned_pending_rate_units",
        "pending_rate_units",
        "rate_policy_version",
        "observed_command_version",
        "observed_order_version",
    ):
        values[field] = _integer(values[field])
    for field in (
        "planned_open_order_slots",
        "remaining_contracts",
        "remaining_open_order_slots",
    ):
        values[field] = _integer(values[field], positive=False)
    values["approved_side"] = _enum(values["approved_side"], ("buy", "sell"))
    values["reference_asset_symbol"] = _text(values["reference_asset_symbol"])
    values["collateral_asset_symbol"] = _text(values["collateral_asset_symbol"])
    for field in (
        "planned_incremental_gross_exposure",
        "planned_worst_case_loss",
        "planned_collateral",
        "remaining_incremental_gross_exposure",
        "remaining_worst_case_loss",
        "remaining_collateral",
    ):
        values[field] = _decimal(values[field])
    values["rate_policy_sha256"] = _hash(values["rate_policy_sha256"])
    for field in (
        "rate_window_start_at",
        "rate_window_end_at",
        "reservation_created_at",
        "proof_observed_at",
    ):
        values[field] = _time(values[field])
    values["observed_terminal_command_status"] = _enum(
        values["observed_terminal_command_status"], ("FAILED", "CANCELLED")
    )
    start = cast(datetime, values["rate_window_start_at"])
    evaluated = cast(datetime, values["decision_evaluated_at"])
    recorded = cast(datetime, values["decision_recorded_at"])
    created = cast(datetime, values["reservation_created_at"])
    observed = cast(datetime, values["proof_observed_at"])
    end = cast(datetime, values["rate_window_end_at"])
    if not (start <= evaluated <= recorded <= created <= observed) or created >= end:
        _fail()
    if (
        values["planned_pending_rate_units"] != 1
        or values["pending_rate_units"] != 1
        or values["remaining_contracts"] != values["approved_contracts"]
        or values["remaining_incremental_gross_exposure"]
        != values["planned_incremental_gross_exposure"]
        or values["remaining_worst_case_loss"] != values["planned_worst_case_loss"]
        or values["remaining_collateral"] != values["planned_collateral"]
        or values["remaining_open_order_slots"] != values["planned_open_order_slots"]
    ):
        _fail()
    expected_slots = 1 if values["command_operation_kind"] == "PLACE_ORDER" else 0
    if values["planned_open_order_slots"] != expected_slots:
        _fail()
    exposure = cast(Decimal, values["planned_incremental_gross_exposure"])
    loss = cast(Decimal, values["planned_worst_case_loss"])
    collateral = cast(Decimal, values["planned_collateral"])
    if values["risk_effect"] == "RISK_INCREASE":
        if exposure <= 0 or loss <= 0 or collateral < 0:
            _fail()
    elif any(amount != 0 for amount in (exposure, loss, collateral)):
        _fail()
    return values


@dataclass(frozen=True)
class _StoredAttemptEvidenceSummary:
    sha256: str
    observed_command_attempt_count: int
    attempt_count: int
    event_count: int


_AttemptEvidenceSummary = AttemptEvidenceSetEncoding | _StoredAttemptEvidenceSummary


def _release_payload(
    values: dict[str, object], evidence: _AttemptEvidenceSummary
) -> dict[str, object]:
    release_key_values = {field: values[field] for field in RELEASE_KEY_FIELDS}
    release_key = derive_release_key(1, release_key_values)
    payload = {
        "account_id": str(values["account_id"]),
        "approved_contracts": values["approved_contracts"],
        "approved_side": values["approved_side"],
        "attempt_evidence_set_sha256": evidence.sha256,
        "collateral_asset_id": values["collateral_asset_id"],
        "collateral_asset_symbol": values["collateral_asset_symbol"],
        "command_id": str(values["command_id"]),
        "command_operation_kind": values["command_operation_kind"],
        "decision_evaluated_at": _timestamp(cast(datetime, values["decision_evaluated_at"])),
        "decision_id": str(values["decision_id"]),
        "decision_outcome": values["decision_outcome"],
        "decision_recorded_at": _timestamp(cast(datetime, values["decision_recorded_at"])),
        "environment": values["environment"],
        "mode": values["mode"],
        "observed_command_attempt_count": evidence.observed_command_attempt_count,
        "observed_command_version": values["observed_command_version"],
        "observed_order_version": values["observed_order_version"],
        "observed_terminal_command_status": values["observed_terminal_command_status"],
        "order_attempt_event_evidence_count": evidence.event_count,
        "order_attempt_evidence_count": evidence.attempt_count,
        "order_id": str(values["order_id"]),
        "planned_collateral": _json_value(values["planned_collateral"]),
        "planned_incremental_gross_exposure": _json_value(
            values["planned_incremental_gross_exposure"]
        ),
        "planned_open_order_slots": values["planned_open_order_slots"],
        "planned_pending_rate_units": values["planned_pending_rate_units"],
        "planned_worst_case_loss": _json_value(values["planned_worst_case_loss"]),
        "prior_reservation_version": 1,
        "product_id": values["product_id"],
        "proof_observed_at": _timestamp(cast(datetime, values["proof_observed_at"])),
        "rate_policy_sha256": values["rate_policy_sha256"],
        "rate_policy_version": values["rate_policy_version"],
        "rate_window_end_at": _timestamp(cast(datetime, values["rate_window_end_at"])),
        "rate_window_start_at": _timestamp(cast(datetime, values["rate_window_start_at"])),
        "reference_asset_id": values["reference_asset_id"],
        "reference_asset_symbol": values["reference_asset_symbol"],
        "region": values["region"],
        "release_key": release_key,
        "release_reason_code": "DEFINITE_NOT_DISPATCHED",
        "released_collateral": _json_value(values["remaining_collateral"]),
        "released_contracts": values["remaining_contracts"],
        "released_incremental_gross_exposure": _json_value(
            values["remaining_incremental_gross_exposure"]
        ),
        "released_open_order_slots": values["remaining_open_order_slots"],
        "released_pending_rate_units": values["pending_rate_units"],
        "released_worst_case_loss": _json_value(values["remaining_worst_case_loss"]),
        "reservation_created_at": _timestamp(cast(datetime, values["reservation_created_at"])),
        "reservation_id": str(values["reservation_id"]),
        "reservation_key": values["reservation_key"],
        "reservation_plan_sha256": values["reservation_plan_sha256"],
        "resulting_reservation_version": 2,
        "risk_effect": values["risk_effect"],
        "risk_state_id": str(values["risk_state_id"]),
    }
    if set(payload) != RELEASE_PAYLOAD_FIELDS:
        _fail()
    return payload


def encode_risk_reservation_release_proof(
    schema_version: object,
    values: object,
    attempt_evidence_set: object,
    *,
    current_intent: object,
) -> RiskReservationReleaseEncoding:
    """Own one pre-update reservation snapshot and derive the terminal proof."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            _fail()
        owned = _release_values(values)
        current = validate_current_intent_snapshot(current_intent, owned)
        attempt_set = validate_attempt_evidence_set_handoff(attempt_evidence_set, owned, current)
        payload_dict = _release_payload(owned, attempt_set)
        payload = _canonical(payload_dict)
        guard_release_proof_payload_bytes(payload.encode("ascii"))
        envelope = {
            "kind": "risk_reservation_release_proof",
            "payload": payload_dict,
            "schema_version": 1,
        }
        return RiskReservationReleaseEncoding(
            release_key=cast(str, payload_dict["release_key"]),
            payload=payload,
            sha256=hashlib.sha256(_canonical(envelope).encode("ascii")).hexdigest(),
            attempt_evidence_set_sha256=attempt_set.sha256,
            observed_command_attempt_count=attempt_set.observed_command_attempt_count,
            order_attempt_evidence_count=attempt_set.attempt_count,
            order_attempt_event_evidence_count=attempt_set.event_count,
            proof_observed_at=cast(datetime, owned["proof_observed_at"]),
            observed_terminal_command_status=cast(str, owned["observed_terminal_command_status"]),
            observed_command_version=cast(int, owned["observed_command_version"]),
            observed_order_version=cast(int, owned["observed_order_version"]),
        )
    except Exception:
        raise PersistenceValueError() from None


_RELEASE_UUID_FIELDS = {
    "account_id",
    "command_id",
    "decision_id",
    "order_id",
    "reservation_id",
    "risk_state_id",
}
_RELEASE_TIME_FIELDS = {
    "decision_evaluated_at",
    "decision_recorded_at",
    "proof_observed_at",
    "rate_window_end_at",
    "rate_window_start_at",
    "reservation_created_at",
}
_RELEASE_DECIMAL_FIELDS = {
    "planned_collateral",
    "planned_incremental_gross_exposure",
    "planned_worst_case_loss",
    "released_collateral",
    "released_incremental_gross_exposure",
    "released_worst_case_loss",
}


def decode_risk_reservation_release_proof(payload: object) -> RiskReservationReleaseEncoding:
    """Decode exact canonical payload and validate all internal derived values."""
    try:
        if type(payload) is not str:
            _fail()
        guard_release_proof_payload_bytes(payload.encode("ascii"))
        raw = json.loads(payload)
        if type(raw) is not dict or set(raw) != RELEASE_PAYLOAD_FIELDS:
            _fail()
        if _canonical(raw) != payload:
            _fail()
        values = dict(raw)
        for field in _RELEASE_UUID_FIELDS:
            values[field] = UUID(cast(str, values[field]))
        for field in _RELEASE_TIME_FIELDS:
            values[field] = datetime.fromisoformat(cast(str, values[field]))
        for field in _RELEASE_DECIMAL_FIELDS:
            values[field] = Decimal(cast(str, values[field]))
        for field in (
            "approved_contracts",
            "collateral_asset_id",
            "observed_command_attempt_count",
            "observed_command_version",
            "observed_order_version",
            "order_attempt_event_evidence_count",
            "order_attempt_evidence_count",
            "planned_open_order_slots",
            "planned_pending_rate_units",
            "prior_reservation_version",
            "product_id",
            "rate_policy_version",
            "reference_asset_id",
            "released_contracts",
            "released_open_order_slots",
            "released_pending_rate_units",
            "resulting_reservation_version",
        ):
            values[field] = _integer(
                values[field],
                positive=field
                not in {
                    "observed_command_attempt_count",
                    "order_attempt_event_evidence_count",
                    "order_attempt_evidence_count",
                    "planned_open_order_slots",
                    "released_open_order_slots",
                },
            )
        if (
            values["prior_reservation_version"] != 1
            or values["resulting_reservation_version"] != 2
            or values["release_reason_code"] != "DEFINITE_NOT_DISPATCHED"
            or values["planned_pending_rate_units"] != 1
            or values["released_pending_rate_units"] != 1
            or values["released_contracts"] != values["approved_contracts"]
            or values["released_collateral"] != values["planned_collateral"]
            or values["released_incremental_gross_exposure"]
            != values["planned_incremental_gross_exposure"]
            or values["released_worst_case_loss"] != values["planned_worst_case_loss"]
            or values["released_open_order_slots"] != values["planned_open_order_slots"]
        ):
            _fail()
        for field in (
            "attempt_evidence_set_sha256",
            "rate_policy_sha256",
            "release_key",
            "reservation_key",
            "reservation_plan_sha256",
        ):
            values[field] = _hash(values[field])
        key_values = {field: values[field] for field in RELEASE_KEY_FIELDS}
        if values["release_key"] != derive_release_key(1, key_values):
            _fail()
        command_count = cast(int, values["observed_command_attempt_count"])
        attempt_count = cast(int, values["order_attempt_evidence_count"])
        event_count = cast(int, values["order_attempt_event_evidence_count"])
        if (
            command_count > MAX_RELEASE_PROOF_ATTEMPTS
            or attempt_count > MAX_RELEASE_PROOF_ATTEMPTS
            or event_count > MAX_RELEASE_PROOF_EVENTS
            or attempt_count != event_count
        ):
            _fail()
        reconstructed = {
            field: values[field]
            for field in RELEASE_INPUT_FIELDS
            if field
            not in {
                "remaining_contracts",
                "remaining_incremental_gross_exposure",
                "remaining_worst_case_loss",
                "remaining_collateral",
                "remaining_open_order_slots",
                "pending_rate_units",
            }
        }
        reconstructed.update(
            remaining_contracts=values["released_contracts"],
            remaining_incremental_gross_exposure=values["released_incremental_gross_exposure"],
            remaining_worst_case_loss=values["released_worst_case_loss"],
            remaining_collateral=values["released_collateral"],
            remaining_open_order_slots=values["released_open_order_slots"],
            pending_rate_units=values["released_pending_rate_units"],
        )
        owned = _release_values(reconstructed)
        evidence = _StoredAttemptEvidenceSummary(
            sha256=cast(str, values["attempt_evidence_set_sha256"]),
            observed_command_attempt_count=command_count,
            attempt_count=attempt_count,
            event_count=event_count,
        )
        if _release_payload(owned, evidence) != raw:
            _fail()
        envelope = {
            "kind": "risk_reservation_release_proof",
            "payload": raw,
            "schema_version": 1,
        }
        return RiskReservationReleaseEncoding(
            release_key=cast(str, values["release_key"]),
            payload=payload,
            sha256=hashlib.sha256(_canonical(envelope).encode("ascii")).hexdigest(),
            attempt_evidence_set_sha256=cast(str, values["attempt_evidence_set_sha256"]),
            observed_command_attempt_count=command_count,
            order_attempt_evidence_count=attempt_count,
            order_attempt_event_evidence_count=event_count,
            proof_observed_at=cast(datetime, values["proof_observed_at"]),
            observed_terminal_command_status=cast(str, values["observed_terminal_command_status"]),
            observed_command_version=cast(int, values["observed_command_version"]),
            observed_order_version=cast(int, values["observed_order_version"]),
        )
    except Exception:
        raise PersistenceValueError() from None


def validate_risk_reservation_release_proof(
    schema_version: object, payload: object, digest: object
) -> None:
    """Fail closed unless the complete canonical proof and digest agree."""
    try:
        if (
            type(schema_version) is not int
            or schema_version != 1
            or decode_risk_reservation_release_proof(payload).sha256 != _hash(digest)
        ):
            _fail()
    except Exception:
        raise PersistenceValueError() from None


class RiskReservationReleaseProofPayload(NulFreeText):
    """Core adapter for standalone canonical release-proof payloads."""

    cache_ok = True
    _maximum: ClassVar[int] = MAX_RELEASE_PROOF_PAYLOAD_BYTES

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        if value is not None and type(value) is not str:
            raise PersistenceValueError()
        try:
            result = super().process_bind_param(value, dialect)
            if result is not None:
                guard_release_proof_payload_bytes(result.encode("ascii"))
                decode_risk_reservation_release_proof(result)
            return result
        except Exception:
            raise PersistenceValueError() from None

    def process_result_value(self, value: object, dialect: Dialect) -> str | None:
        return self.process_bind_param(value, dialect)
