"""Canonical reconciliation evidence; classification never grants safety authority."""

import hashlib
import json
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.engine import Dialect

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.schema_support import NulFreeText

DRIFT_KINDS = (
    "RESOURCE_INCOMPLETE",
    "UNRESOLVED_OPERATION",
    "UNMANAGED_ORDER",
    "ORDER_MISSING_AT_EXCHANGE",
    "ORDER_STATE_MISMATCH",
    "FILL_MISMATCH",
    "UNMANAGED_POSITION",
    "POSITION_MISMATCH",
    "BALANCE_MISMATCH",
    "ACCOUNTING_MISMATCH",
    "UNKNOWN_EXPOSURE",
    "INADEQUATE_PROTECTION",
    "UNCLASSIFIED",
)
HARD_NON_OVERRIDABLE_KINDS = frozenset(
    {
        "RESOURCE_INCOMPLETE",
        "UNRESOLVED_OPERATION",
        "UNKNOWN_EXPOSURE",
        "INADEQUATE_PROTECTION",
        "UNCLASSIFIED",
    }
)
SUBJECT_KINDS = (
    "ACCOUNT",
    "RESOURCE",
    "COMMAND",
    "ORDER",
    "FILL",
    "POSITION",
    "BALANCE",
    "WALLET_TRANSACTION",
    "PROTECTION",
    "ACCOUNTING",
)
COMPARISONS = (
    "PRESENCE",
    "STATE",
    "SIZE",
    "AMOUNT",
    "COVERAGE",
    "COMPLETENESS",
    "UNCERTAINTY",
    "OTHER_CLASSIFIED",
)
SEVERITIES = ("INFO", "WARNING", "CRITICAL")
ACKNOWLEDGEMENT_POLICIES = ("REVIEWABLE", "NON_OVERRIDABLE")
ACKNOWLEDGEMENT_KINDS = ("REVIEW_EVIDENCE", "NOTICE_EVIDENCE")
REASON_CODES = (
    "EXPECTED_EXTERNAL_ACTIVITY",
    "ADOPTION_REVIEWED",
    "FALSE_POSITIVE",
    "OPERATOR_NOTED",
)
DRIFT_FIELDS = frozenset(
    {
        "asset_id",
        "comparison",
        "expected_sha256",
        "observed_sha256",
        "product_id",
        "subject_key_sha256",
    }
)
ACKNOWLEDGEMENT_FIELDS = frozenset(
    {"actor_ref", "idempotency_key", "reason_code", "supporting_evidence_sha256"}
)


def _enum(value: object, choices: tuple[str, ...]) -> str:
    if type(value) is not str or value not in choices:
        raise PersistenceValueError()
    return value


def _sha256(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PersistenceValueError()
    return value


def _positive_bigint(value: object, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or not 1 <= value < 2**63:
        raise PersistenceValueError()
    return value


def _uuid(value: object) -> str:
    if type(value) is not UUID or value.int == 0:
        raise PersistenceValueError()
    return str(value)


def _text(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128 or not value.strip() or "\x00" in value:
        raise PersistenceValueError()
    return value


class DriftEvidenceV1(BaseModel):
    """Strict frozen discrepancy payload with no raw exchange identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    asset_id: int | None
    comparison: Literal[
        "PRESENCE",
        "STATE",
        "SIZE",
        "AMOUNT",
        "COVERAGE",
        "COMPLETENESS",
        "UNCERTAINTY",
        "OTHER_CLASSIFIED",
    ]
    expected_sha256: str | None
    observed_sha256: str | None
    product_id: int | None
    subject_key_sha256: str

    @field_validator("asset_id", "product_id", mode="before")
    @classmethod
    def positive_optional_ids(cls, value: object) -> int | None:
        return _positive_bigint(value, nullable=True)

    @field_validator("expected_sha256", "observed_sha256", mode="before")
    @classmethod
    def optional_hashes(cls, value: object) -> str | None:
        return _sha256(value, nullable=True)

    @field_validator("subject_key_sha256", mode="before")
    @classmethod
    def subject_hash(cls, value: object) -> str:
        result = _sha256(value)
        assert isinstance(result, str)
        return result


class AcknowledgementEvidenceV1(BaseModel):
    """Strict frozen operator evidence payload with no free-form note."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    actor_ref: str
    idempotency_key: str
    reason_code: Literal[
        "EXPECTED_EXTERNAL_ACTIVITY",
        "ADOPTION_REVIEWED",
        "FALSE_POSITIVE",
        "OPERATOR_NOTED",
    ]
    supporting_evidence_sha256: str | None

    @field_validator("actor_ref", "idempotency_key", mode="before")
    @classmethod
    def bounded_identity_text(cls, value: object) -> str:
        return _text(value)

    @field_validator("supporting_evidence_sha256", mode="before")
    @classmethod
    def optional_supporting_hash(cls, value: object) -> str | None:
        return _sha256(value, nullable=True)


def _canonical_json(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(encoded.encode("ascii")) > 2048:
        raise PersistenceValueError()
    return encoded


def _drift_payload(values: object) -> dict[str, object]:
    if type(values) is DriftEvidenceV1:
        payload = values
    elif type(values) is dict:
        payload = DriftEvidenceV1.model_validate(dict(values))
    else:
        raise PersistenceValueError()
    return payload.model_dump()


def _acknowledgement_payload(values: object) -> dict[str, object]:
    if type(values) is AcknowledgementEvidenceV1:
        payload = values
    elif type(values) is dict:
        payload = AcknowledgementEvidenceV1.model_validate(dict(values))
    else:
        raise PersistenceValueError()
    return payload.model_dump()


def _drift_identity(
    drift_kind: object,
    subject_kind: object,
    severity: object,
    acknowledgement_policy: object,
) -> tuple[str, str, str, str]:
    kind = _enum(drift_kind, DRIFT_KINDS)
    subject = _enum(subject_kind, SUBJECT_KINDS)
    level = _enum(severity, SEVERITIES)
    policy = _enum(acknowledgement_policy, ACKNOWLEDGEMENT_POLICIES)
    if kind in HARD_NON_OVERRIDABLE_KINDS and (level != "CRITICAL" or policy != "NON_OVERRIDABLE"):
        raise PersistenceValueError()
    return kind, subject, level, policy


def _acknowledgement_identity(
    drift_id: object,
    acknowledgement_kind: object,
    acknowledgement_policy: object,
    safety_effect: object,
    reason_code: object,
) -> tuple[str, str, str, str]:
    parent = _uuid(drift_id)
    kind = _enum(acknowledgement_kind, ACKNOWLEDGEMENT_KINDS)
    policy = _enum(acknowledgement_policy, ACKNOWLEDGEMENT_POLICIES)
    effect = _enum(safety_effect, ("NONE",))
    reason = _enum(reason_code, REASON_CODES)
    if (policy == "REVIEWABLE") != (kind == "REVIEW_EVIDENCE"):
        raise PersistenceValueError()
    if policy == "NON_OVERRIDABLE" and reason != "OPERATOR_NOTED":
        raise PersistenceValueError()
    return parent, kind, policy, effect


def encode_drift_evidence(
    schema_version: object,
    drift_kind: object,
    subject_kind: object,
    severity: object,
    acknowledgement_policy: object,
    values: object,
) -> tuple[str, str]:
    """Return owned canonical discrepancy payload bytes and their bound digest."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            raise PersistenceValueError()
        kind, subject, level, policy = _drift_identity(
            drift_kind, subject_kind, severity, acknowledgement_policy
        )
        payload = _canonical_json(_drift_payload(values))
        envelope = _canonical_json(
            {
                "acknowledgement_policy": policy,
                "drift_kind": kind,
                "kind": "reconciliation_drift",
                "payload": json.loads(payload),
                "schema_version": 1,
                "severity": level,
                "subject_kind": subject,
            }
        )
        return payload, hashlib.sha256(envelope.encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def decode_drift_evidence(payload: object) -> dict[str, object]:
    """Decode only the complete standalone canonical drift payload shape."""
    try:
        if type(payload) is not str or len(payload.encode("utf-8")) > 2048:
            raise PersistenceValueError()
        raw = cast(dict[str, object], json.loads(payload))
        canonical = _canonical_json(_drift_payload(raw))
        if canonical != payload:
            raise PersistenceValueError()
        return raw
    except Exception:
        raise PersistenceValueError() from None


def validate_drift_evidence(
    schema_version: object,
    drift_kind: object,
    subject_kind: object,
    severity: object,
    acknowledgement_policy: object,
    payload: object,
    digest: object,
) -> None:
    """Fail closed unless drift payload and every sibling identity agree."""
    try:
        values = decode_drift_evidence(payload)
        if (
            type(digest) is not str
            or _sha256(digest) != digest
            or encode_drift_evidence(
                schema_version,
                drift_kind,
                subject_kind,
                severity,
                acknowledgement_policy,
                values,
            )
            != (payload, digest)
        ):
            raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None


def encode_acknowledgement_evidence(
    schema_version: object,
    drift_id: object,
    acknowledgement_kind: object,
    acknowledgement_policy: object,
    safety_effect: object,
    values: object,
) -> tuple[str, str]:
    """Return canonical operator-review evidence with an explicit no-effect digest."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            raise PersistenceValueError()
        normalized = _acknowledgement_payload(values)
        parent, kind, policy, effect = _acknowledgement_identity(
            drift_id,
            acknowledgement_kind,
            acknowledgement_policy,
            safety_effect,
            normalized["reason_code"],
        )
        payload = _canonical_json(normalized)
        envelope = _canonical_json(
            {
                "acknowledgement_kind": kind,
                "acknowledgement_policy": policy,
                "drift_id": parent,
                "kind": "reconciliation_acknowledgement",
                "payload": json.loads(payload),
                "safety_effect": effect,
                "schema_version": 1,
            }
        )
        return payload, hashlib.sha256(envelope.encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def decode_acknowledgement_evidence(payload: object) -> dict[str, object]:
    """Decode only the complete canonical acknowledgement payload shape."""
    try:
        if type(payload) is not str or len(payload.encode("utf-8")) > 2048:
            raise PersistenceValueError()
        raw = cast(dict[str, object], json.loads(payload))
        canonical = _canonical_json(_acknowledgement_payload(raw))
        if canonical != payload:
            raise PersistenceValueError()
        return raw
    except Exception:
        raise PersistenceValueError() from None


def validate_acknowledgement_evidence(
    schema_version: object,
    drift_id: object,
    acknowledgement_kind: object,
    acknowledgement_policy: object,
    safety_effect: object,
    payload: object,
    digest: object,
) -> None:
    """Fail closed unless acknowledgement payload and parent identity agree."""
    try:
        values = decode_acknowledgement_evidence(payload)
        if (
            type(digest) is not str
            or _sha256(digest) != digest
            or encode_acknowledgement_evidence(
                schema_version,
                drift_id,
                acknowledgement_kind,
                acknowledgement_policy,
                safety_effect,
                values,
            )
            != (payload, digest)
        ):
            raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None


class DriftEvidencePayload(NulFreeText):
    """Canonical standalone drift payload admission on bind and result paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        decode_drift_evidence(value)
        assert isinstance(value, str)
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)


class AcknowledgementEvidencePayload(NulFreeText):
    """Canonical standalone acknowledgement admission on bind and result paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        decode_acknowledgement_evidence(value)
        assert isinstance(value, str)
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)
