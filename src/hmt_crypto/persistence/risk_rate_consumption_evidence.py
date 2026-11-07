"""Stable public facade for reviewed Task 2.1d9c historical evidence."""

from hmt_crypto.persistence.risk_rate_consumption_codec import (
    RiskRateConsumptionPayload,
    decode_risk_rate_consumption_evidence,
    derive_consumption_key,
    derive_dispatch_event_key,
    encode_dispatch_started_evidence,
    encode_risk_rate_consumption_evidence,
    validate_risk_rate_consumption_evidence,
)
from hmt_crypto.persistence.risk_rate_consumption_contracts import (
    CONSUMPTION_INPUT_FIELDS,
    CONSUMPTION_PAYLOAD_FIELDS,
    MAX_EVIDENCE_BYTES,
    NEUTRAL_EVENT_PAYLOAD,
    NEUTRAL_EVENT_SHA256,
    DispatchStartedEvidenceEncoding,
    RiskRateConsumptionEncoding,
    RiskRateConsumptionInputV1,
)
from hmt_crypto.persistence.risk_rate_consumption_semantics import (
    same_sample_strictly_before_expiries,
    validate_consumption_semantics,
    windows_overlap,
)

__all__ = [
    "CONSUMPTION_INPUT_FIELDS",
    "CONSUMPTION_PAYLOAD_FIELDS",
    "MAX_EVIDENCE_BYTES",
    "NEUTRAL_EVENT_PAYLOAD",
    "NEUTRAL_EVENT_SHA256",
    "DispatchStartedEvidenceEncoding",
    "RiskRateConsumptionEncoding",
    "RiskRateConsumptionInputV1",
    "RiskRateConsumptionPayload",
    "decode_risk_rate_consumption_evidence",
    "derive_consumption_key",
    "derive_dispatch_event_key",
    "encode_dispatch_started_evidence",
    "encode_risk_rate_consumption_evidence",
    "same_sample_strictly_before_expiries",
    "validate_consumption_semantics",
    "validate_risk_rate_consumption_evidence",
    "windows_overlap",
]
