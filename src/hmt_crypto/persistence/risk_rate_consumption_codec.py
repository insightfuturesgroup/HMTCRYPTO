"""Canonical v1 keys and immutable local risk-rate consumption evidence."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from typing import cast
from uuid import UUID

from sqlalchemy.engine import Dialect

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import validate_stored_evidence
from hmt_crypto.persistence.risk_rate_consumption_contracts import (
    CONSUMPTION_INPUT_FIELDS,
    CONSUMPTION_PAYLOAD_FIELDS,
    EVIDENCE_SCHEMA_VERSION,
    KEY_INPUT_FIELDS,
    MAX_EVIDENCE_BYTES,
    NEUTRAL_EVENT_PAYLOAD,
    NEUTRAL_EVENT_SHA256,
    ConsumptionKeyV1,
    DispatchStartedEvidenceEncoding,
    RiskRateConsumptionEncoding,
    RiskRateConsumptionInputV1,
    require_exact_fields,
)
from hmt_crypto.persistence.risk_rate_consumption_semantics import (
    validate_consumption_semantics,
)
from hmt_crypto.persistence.risk_reservation_contracts import _hash, _integer, _text, _uuid
from hmt_crypto.persistence.schema_support import NulFreeText

_UUID_FIELDS = frozenset(
    {
        "account_id",
        "consumption_id",
        "decision_id",
        "risk_state_id",
        "command_id",
        "order_id",
        "reservation_id",
        "order_attempt_id",
        "dispatch_event_id",
    }
)
_TIME_FIELDS = frozenset(
    {
        "decision_evaluated_at",
        "decision_recorded_at",
        "reservation_created_at",
        "attempt_prepared_at",
        "attempt_recorded_at",
        "rate_window_start_at",
        "rate_window_end_at",
        "recorded_at",
    }
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _serialized(model: RiskRateConsumptionInputV1) -> dict[str, object]:
    raw = model.model_dump(mode="python")
    for field in _UUID_FIELDS:
        raw[field] = str(raw[field])
    for field in _TIME_FIELDS:
        raw[field] = cast(datetime, raw[field]).isoformat(timespec="microseconds")
    return raw


def derive_consumption_key(schema_version: object, values: object) -> str:
    """Derive the scoped semantic replay key; generated row IDs are excluded."""
    try:
        if _integer(schema_version) != EVIDENCE_SCHEMA_VERSION:
            raise PersistenceValueError()
        raw = require_exact_fields(values, KEY_INPUT_FIELDS)
        owned = ConsumptionKeyV1.model_validate(raw)
        payload = owned.model_dump(mode="python")
        for field in ("account_id", "reservation_id", "order_attempt_id"):
            payload[field] = str(payload[field])
        for field in ("decision_recorded_at", "reservation_created_at"):
            payload[field] = cast(datetime, payload[field]).isoformat(timespec="microseconds")
        envelope = {
            "kind": "risk_rate_consumption_key",
            "payload": payload,
            "schema_version": EVIDENCE_SCHEMA_VERSION,
        }
        return sha256(_canonical(envelope).encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def derive_dispatch_event_key(order_attempt_id: object) -> str:
    """Derive the sole v1 first-event key from the immutable attempt identity."""
    try:
        identifier = _uuid(order_attempt_id)
        assert identifier is not None
        return f"dispatch-started:v1:{identifier}"
    except Exception:
        raise PersistenceValueError() from None


def encode_dispatch_started_evidence() -> DispatchStartedEvidenceEncoding:
    """Return the fixed neutral first-event evidence vector."""
    return DispatchStartedEvidenceEncoding(
        payload=NEUTRAL_EVENT_PAYLOAD,
        sha256=NEUTRAL_EVENT_SHA256,
    )


def encode_risk_rate_consumption_evidence(
    schema_version: object, values: object
) -> RiskRateConsumptionEncoding:
    """Validate all caller inputs, own derived identities, then hash the v1 envelope."""
    try:
        if _integer(schema_version) != EVIDENCE_SCHEMA_VERSION:
            raise PersistenceValueError()
        raw = require_exact_fields(values, CONSUMPTION_INPUT_FIELDS)
        owned = RiskRateConsumptionInputV1.model_validate(raw)
        validate_consumption_semantics(owned)
        validate_stored_evidence(
            "intent",
            owned.command_operation_kind,
            owned.attempt_evidence_schema_version,
            owned.attempt_evidence_payload,
            owned.attempt_evidence_sha256,
        )
        consumption_key = derive_consumption_key(
            EVIDENCE_SCHEMA_VERSION,
            {field: getattr(owned, field) for field in KEY_INPUT_FIELDS},
        )
        dispatch_key = derive_dispatch_event_key(owned.order_attempt_id)
        payload_object = _serialized(owned)
        payload_object["consumption_key"] = consumption_key
        payload_object["dispatch_event_key"] = dispatch_key
        if set(payload_object) != CONSUMPTION_PAYLOAD_FIELDS:
            raise PersistenceValueError()
        payload = _canonical(payload_object)
        envelope = {
            "kind": "risk_rate_consumption",
            "payload": payload_object,
            "schema_version": EVIDENCE_SCHEMA_VERSION,
        }
        canonical = _canonical(envelope)
        if len(canonical.encode("ascii")) > MAX_EVIDENCE_BYTES:
            raise PersistenceValueError()
        return RiskRateConsumptionEncoding(
            consumption_key=consumption_key,
            dispatch_event_key=dispatch_key,
            payload=payload,
            sha256=sha256(canonical.encode("ascii")).hexdigest(),
        )
    except Exception:
        raise PersistenceValueError() from None


def decode_risk_rate_consumption_evidence(payload: object) -> RiskRateConsumptionEncoding:
    """Decode and independently re-encode exact canonical payload bytes."""
    try:
        text = _text(payload, maximum=MAX_EVIDENCE_BYTES)
        raw = json.loads(text)
        if type(raw) is not dict or set(raw) != CONSUMPTION_PAYLOAD_FIELDS:
            raise PersistenceValueError()
        supplied_key = _hash(raw.pop("consumption_key"))
        supplied_event_key = _text(raw.pop("dispatch_event_key"))
        for field in _UUID_FIELDS:
            raw[field] = UUID(raw[field])
        for field in _TIME_FIELDS:
            raw[field] = datetime.fromisoformat(raw[field])
        encoded = encode_risk_rate_consumption_evidence(EVIDENCE_SCHEMA_VERSION, raw)
        if (
            encoded.payload != payload
            or encoded.consumption_key != supplied_key
            or encoded.dispatch_event_key != supplied_event_key
        ):
            raise PersistenceValueError()
        return encoded
    except Exception:
        raise PersistenceValueError() from None


def validate_risk_rate_consumption_evidence(
    schema_version: object, payload: object, digest: object
) -> RiskRateConsumptionEncoding:
    """Validate a stored schema/payload/digest tuple and return its owned decoding."""
    try:
        if _integer(schema_version) != EVIDENCE_SCHEMA_VERSION:
            raise PersistenceValueError()
        expected = _hash(digest)
        decoded = decode_risk_rate_consumption_evidence(payload)
        if decoded.sha256 != expected:
            raise PersistenceValueError()
        return decoded
    except Exception:
        raise PersistenceValueError() from None


class RiskRateConsumptionPayload(NulFreeText):
    """Strict Core/ORM adapter for standalone canonical internal consistency."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        if value is not None and (type(value) is not str or len(value) > MAX_EVIDENCE_BYTES):
            raise PersistenceValueError()
        try:
            result = super().process_bind_param(value, dialect)
            if result is not None:
                decode_risk_rate_consumption_evidence(result)
            return result
        except Exception:
            raise PersistenceValueError() from None

    def process_result_value(self, value: object, dialect: Dialect) -> str | None:
        return self.process_bind_param(value, dialect)
