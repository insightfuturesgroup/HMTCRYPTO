"""Compatibility facade for cohesive reservation evidence codecs."""

from hmt_crypto.persistence.risk_reservation_attempt_codec import (
    derive_release_key,
    derive_reservation_key,
    encode_attempt_evidence_set,
    guard_attempt_set_canonical_bytes,
)
from hmt_crypto.persistence.risk_reservation_release_codec import (
    RiskReservationReleaseProofPayload,
    decode_risk_reservation_release_proof,
    encode_risk_reservation_release_proof,
    guard_release_proof_payload_bytes,
    validate_risk_reservation_release_proof,
)

__all__ = [
    "RiskReservationReleaseProofPayload",
    "decode_risk_reservation_release_proof",
    "derive_release_key",
    "derive_reservation_key",
    "encode_attempt_evidence_set",
    "encode_risk_reservation_release_proof",
    "guard_attempt_set_canonical_bytes",
    "guard_release_proof_payload_bytes",
    "validate_risk_reservation_release_proof",
]
