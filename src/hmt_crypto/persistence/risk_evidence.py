"""Canonical halt lifecycle evidence; never readiness or dispatch authority."""

import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.engine import Dialect

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.schema_support import NulFreeText

HALT_CAUSES = ("GLOBAL_KILL", "AUTOMATIC_RISK", "DAILY_LOSS", "RECONCILIATION")
HALT_EVENT_KINDS = ("ENGAGED", "REAFFIRMED", "RELEASED")
HALT_STATES = ("CLEARED", "ACTIVE")
HALT_ACTORS = ("OPERATOR", "SYSTEM", "RECONCILIATION", "RECOVERY")
ENGAGEMENT_REASONS = {
    "GLOBAL_KILL": frozenset({"OPERATOR_KILL"}),
    "AUTOMATIC_RISK": frozenset(
        {"ACCOUNTING_UNKNOWN", "PROTECTION_INADEQUATE", "RISK_POLICY", "RECOVERY_REQUIRED"}
    ),
    "DAILY_LOSS": frozenset({"DAILY_LOSS_LIMIT"}),
    "RECONCILIATION": frozenset(
        {"RECONCILIATION_INCOMPLETE", "STALE_RECONCILIATION", "CRITICAL_DRIFT"}
    ),
}
RELEASE_REASONS = frozenset(
    {
        "OPERATOR_RELEASE",
        "UTC_RESET",
        "RECONCILIATION_RECOVERED",
        "ACCOUNTING_RECOVERED",
        "PROTECTION_RESTORED",
        "RECOVERY_COMPLETED",
    }
)
HALT_REASONS = tuple(sorted(set().union(*ENGAGEMENT_REASONS.values(), RELEASE_REASONS)))
PAYLOAD_FIELDS = frozenset(
    {
        "actor_kind",
        "reason_code",
        "reconciliation_run_id",
        "pnl_snapshot_id",
        "drift_id",
        "supporting_evidence_sha256",
    }
)


def _enum(value: object, allowed: tuple[str, ...] | frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        raise PersistenceValueError()
    return value


def _positive_bigint(value: object) -> int:
    if type(value) is not int or not 1 <= value < 2**63:
        raise PersistenceValueError()
    return value


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    if type(value) is not UUID or value.int == 0:
        raise PersistenceValueError()
    return value


def _optional_hash(value: object) -> str | None:
    if value is None:
        return None
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PersistenceValueError()
    return value


class HaltEventEvidenceV1(BaseModel):
    """Strict frozen lifecycle payload with no free-form or secret fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    actor_kind: Literal["OPERATOR", "SYSTEM", "RECONCILIATION", "RECOVERY"]
    reason_code: Literal[
        "OPERATOR_KILL",
        "DAILY_LOSS_LIMIT",
        "RECONCILIATION_INCOMPLETE",
        "STALE_RECONCILIATION",
        "CRITICAL_DRIFT",
        "ACCOUNTING_UNKNOWN",
        "PROTECTION_INADEQUATE",
        "RISK_POLICY",
        "RECOVERY_REQUIRED",
        "OPERATOR_RELEASE",
        "UTC_RESET",
        "RECONCILIATION_RECOVERED",
        "ACCOUNTING_RECOVERED",
        "PROTECTION_RESTORED",
        "RECOVERY_COMPLETED",
    ]
    reconciliation_run_id: UUID | None
    pnl_snapshot_id: UUID | None
    drift_id: UUID | None
    supporting_evidence_sha256: str | None

    @field_validator("reconciliation_run_id", "pnl_snapshot_id", "drift_id", mode="before")
    @classmethod
    def exact_optional_uuids(cls, value: object) -> UUID | None:
        return _optional_uuid(value)

    @field_validator("supporting_evidence_sha256", mode="before")
    @classmethod
    def exact_optional_supporting_hash(cls, value: object) -> str | None:
        return _optional_hash(value)


def _canonical_payload(values: object) -> tuple[HaltEventEvidenceV1, str]:
    if type(values) is HaltEventEvidenceV1:
        payload = values
    elif type(values) is dict and set(values) == PAYLOAD_FIELDS:
        payload = HaltEventEvidenceV1.model_validate(dict(values))
    else:
        raise PersistenceValueError()
    encoded = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(encoded.encode("ascii")) > 2048:
        raise PersistenceValueError()
    return payload, encoded


def _validate_semantics(
    halt_cause: object,
    generation: object,
    sequence: object,
    event_kind: object,
    from_state: object,
    to_state: object,
    values: HaltEventEvidenceV1,
) -> tuple[str, int, int, str, str, str]:
    cause = _enum(halt_cause, HALT_CAUSES)
    generation_value = _positive_bigint(generation)
    sequence_value = _positive_bigint(sequence)
    kind = _enum(event_kind, HALT_EVENT_KINDS)
    before = _enum(from_state, HALT_STATES)
    after = _enum(to_state, HALT_STATES)
    if (
        (kind == "ENGAGED" and (sequence_value != 1 or before != "CLEARED" or after != "ACTIVE"))
        or (
            kind == "REAFFIRMED" and (sequence_value < 2 or before != "ACTIVE" or after != "ACTIVE")
        )
        or (kind == "RELEASED" and (sequence_value < 2 or before != "ACTIVE" or after != "CLEARED"))
    ):
        raise PersistenceValueError()
    actor = _enum(values.actor_kind, HALT_ACTORS)
    reason = _enum(values.reason_code, frozenset(HALT_REASONS))
    valid_reasons = RELEASE_REASONS if kind == "RELEASED" else ENGAGEMENT_REASONS[cause]
    if reason not in valid_reasons or (cause == "GLOBAL_KILL" and actor != "OPERATOR"):
        raise PersistenceValueError()
    return cause, generation_value, sequence_value, kind, before, after


def encode_halt_event_evidence(
    schema_version: object,
    halt_cause: object,
    generation: object,
    sequence: object,
    event_kind: object,
    from_state: object,
    to_state: object,
    values: object,
) -> tuple[str, str]:
    """Own typed halt semantics and return canonical payload plus digest."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            raise PersistenceValueError()
        payload_values, payload = _canonical_payload(values)
        cause, generation_value, sequence_value, kind, before, after = _validate_semantics(
            halt_cause,
            generation,
            sequence,
            event_kind,
            from_state,
            to_state,
            payload_values,
        )
        envelope = (
            '{"event_kind":"'
            + kind
            + '","from_state":"'
            + before
            + '","generation":'
            + str(generation_value)
            + ',"halt_cause":"'
            + cause
            + '","payload":'
            + payload
            + ',"schema_version":1,"sequence":'
            + str(sequence_value)
            + ',"to_state":"'
            + after
            + '"}'
        )
        return payload, hashlib.sha256(envelope.encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def decode_halt_event_evidence(payload: object) -> HaltEventEvidenceV1:
    """Restore exact typed values only from the standalone canonical v1 shape."""
    try:
        if type(payload) is not str or len(payload.encode("utf-8")) > 2048:
            raise PersistenceValueError()
        raw = json.loads(payload)
        if type(raw) is not dict:
            raise PersistenceValueError()
        for field in ("reconciliation_run_id", "pnl_snapshot_id", "drift_id"):
            if field in raw and raw[field] is not None:
                raw[field] = UUID(raw[field])
        typed_payload, canonical = _canonical_payload(raw)
        if canonical != payload:
            raise PersistenceValueError()
        return typed_payload
    except Exception:
        raise PersistenceValueError() from None


def validate_halt_event_evidence(
    schema_version: object,
    halt_cause: object,
    generation: object,
    sequence: object,
    event_kind: object,
    from_state: object,
    to_state: object,
    payload: object,
    digest: object,
) -> None:
    """Fail closed unless payload, envelope and sibling digest agree exactly."""
    try:
        values = decode_halt_event_evidence(payload)
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or encode_halt_event_evidence(
                schema_version,
                halt_cause,
                generation,
                sequence,
                event_kind,
                from_state,
                to_state,
                values,
            )
            != (payload, digest)
        ):
            raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None


class HaltEventEvidencePayload(NulFreeText):
    """Canonical standalone halt-event payload admission on bind/result paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        decode_halt_event_evidence(value)
        assert isinstance(value, str)
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)
