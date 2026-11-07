"""Pure closed semantics for v1 local risk-rate consumption evidence."""

import json
from uuid import UUID

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_rate_consumption_contracts import (
    NEUTRAL_EVENT_PAYLOAD,
    NEUTRAL_EVENT_SHA256,
    RiskRateConsumptionInputV1,
)
from hmt_crypto.persistence.risk_reservation_contracts import _time


def validate_consumption_semantics(value: RiskRateConsumptionInputV1) -> None:
    """Validate exact backend, event, unit, causal-chain and half-open-window rules."""
    expected_backend = "VENUE" if value.mode == "LIVE" else "SIMULATOR"
    if (
        value.attempt_backend != expected_backend
        or value.attempt_evidence_schema_version != 1
        or value.dispatch_event_sequence != 1
        or value.dispatch_event_kind != "DISPATCH_STARTED"
        or value.dispatch_event_evidence_schema_version != 1
        or value.dispatch_event_evidence_payload != NEUTRAL_EVENT_PAYLOAD
        or value.dispatch_event_evidence_sha256 != NEUTRAL_EVENT_SHA256
        or value.consumed_units != 1
    ):
        raise PersistenceValueError()
    if not (
        value.rate_window_start_at
        <= value.decision_evaluated_at
        <= value.decision_recorded_at
        <= value.reservation_created_at
        <= value.attempt_prepared_at
        <= value.attempt_recorded_at
        <= value.recorded_at
        < value.rate_window_end_at
    ):
        raise PersistenceValueError()
    try:
        intent = json.loads(value.attempt_evidence_payload)
        if type(intent) is not dict or intent.get("product_id") != value.product_id:
            raise PersistenceValueError()
        if value.command_operation_kind == "MODIFY_ORDER":
            if UUID(intent["target_order_id"]) != value.order_id:
                raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None


def windows_overlap(
    existing_start: object,
    existing_end: object,
    target_start: object,
    target_end: object,
) -> bool:
    """Return exact half-open overlap after rejecting malformed windows."""
    try:
        left_start = _time(existing_start)
        left_end = _time(existing_end)
        right_start = _time(target_start)
        right_end = _time(target_end)
        if left_start is None or left_end is None or right_start is None or right_end is None:
            raise PersistenceValueError()
        if not left_start < left_end or not right_start < right_end:
            raise PersistenceValueError()
        return left_start < right_end and right_start < left_end
    except Exception:
        raise PersistenceValueError() from None


def same_sample_strictly_before_expiries(
    recorded_at: object,
    command_claim_expires_at: object,
    leadership_expires_at: object | None,
) -> bool:
    """Pure future-barrier expiry predicate; equality is expired."""
    try:
        sample = _time(recorded_at)
        claim = _time(command_claim_expires_at)
        lease = _time(leadership_expires_at, optional=True)
        assert sample is not None and claim is not None
        return sample < claim and (lease is None or sample < lease)
    except Exception:
        raise PersistenceValueError() from None
