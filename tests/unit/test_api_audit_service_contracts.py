"""Exact authority-free contracts for one durable API-audit append."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.persistence.api_audit_contracts import (
    ApiAuditRecordV1,
    AuditCallKind,
    AuditContextKind,
    AuditEventKind,
    AuditRetentionMode,
    AuditTarget,
    PayloadSlot,
    PublicAuditContext,
    VenueOutcomeKind,
)
from hmt_crypto.persistence.api_audit_payload_contracts import AuditRetentionPolicyV1
from hmt_crypto.persistence.api_audit_service_contracts import (
    ApiAuditAppendReceipt,
    ApiAuditAppendRequest,
)
from hmt_crypto.persistence.api_audit_service_errors import (
    AuditCommitUncertain,
    AuditConflict,
    AuditEvidenceRejected,
    AuditInvariantViolation,
    AuditTransactionStateError,
    AuditUnavailable,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def _context() -> PublicAuditContext:
    return PublicAuditContext(
        context_kind=AuditContextKind.PUBLIC,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )


def _root_record() -> ApiAuditRecordV1:
    call_id = UUID(int=700)
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=_context(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=call_id,
        correlation_id=call_id,
        policy_version="delta-docs-2026-11-03",
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
    )


def _request() -> ApiAuditAppendRequest:
    return ApiAuditAppendRequest(
        record=_root_record(),
        retention=AuditRetentionPolicyV1(mode=AuditRetentionMode.SUMMARY_ONLY),
    )


def _inert_finish_record() -> ApiAuditRecordV1:
    """Return existing D13a finish evidence that D13c must never append."""
    call_id = UUID(int=700)
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_FINISHED,
        context=_context(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=call_id,
        correlation_id=call_id,
        policy_version="delta-docs-2026-11-03",
        root_event_id=UUID(int=701),
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
        outcome_kind=VenueOutcomeKind.SUCCESS,
    )


def _receipt() -> ApiAuditAppendReceipt:
    return ApiAuditAppendReceipt(
        context=_context(),
        event_kind=AuditEventKind.CALL_STARTED,
        event_id=UUID(int=701),
        root_event_id=UUID(int=701),
        sequence=1,
        event_key="a" * 64,
        audit_recorded_at=NOW,
        summary_sha256=None,
        retention_mode=AuditRetentionMode.SUMMARY_ONLY,
    )


def test_append_request_is_exact_frozen_and_accepts_no_storage_overrides() -> None:
    request = _request()
    assert tuple(type(request).model_fields) == ("record", "retention")
    assert type(request.record) is ApiAuditRecordV1
    assert type(request.retention) is AuditRetentionPolicyV1
    with pytest.raises(ValidationError):
        request.record = _root_record()  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ApiAuditAppendRequest(
            record=_root_record(),
            retention=AuditRetentionPolicyV1(mode=AuditRetentionMode.SUMMARY_ONLY),
            event_id=UUID(int=1),
        )
    with pytest.raises(ValidationError):
        ApiAuditAppendRequest(
            record=_root_record().model_dump(mode="python"),
            retention=AuditRetentionPolicyV1(mode=AuditRetentionMode.SUMMARY_ONLY),
        )


def test_append_receipt_is_safe_closed_and_contains_no_payload_or_authority() -> None:
    receipt = _receipt()
    assert tuple(type(receipt).model_fields) == (
        "context",
        "event_kind",
        "event_id",
        "root_event_id",
        "sequence",
        "event_key",
        "audit_recorded_at",
        "summary_sha256",
        "retention_mode",
        "payload_slot",
        "payload_schema_version",
        "payload_sha256",
        "payload_byte_length",
        "payload_expires_at",
        "payload_ttl_seconds",
        "retention_policy_version",
        "payload_copy_key",
    )
    assert receipt.payload_slot is None
    forbidden = {
        "payload_text",
        "raw_body",
        "headers",
        "session",
        "commit",
        "send",
        "credential",
        "signature",
        "callback",
    }
    assert forbidden.isdisjoint(type(receipt).model_fields)
    with pytest.raises(ValidationError):
        ApiAuditAppendReceipt(**receipt.model_dump(), payload_text="secret")


def test_receipt_requires_complete_retained_bundle_and_summary_shape() -> None:
    values = _receipt().model_dump(mode="python")
    values.update(
        event_kind=AuditEventKind.ATTEMPT_STARTED,
        root_event_id=UUID(int=700),
        sequence=2,
        retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
        payload_slot=PayloadSlot.REQUEST,
        payload_schema_version=1,
        payload_sha256="b" * 64,
        payload_byte_length=12,
        payload_expires_at=NOW,
        payload_ttl_seconds=60,
        retention_policy_version=3,
        payload_copy_key="c" * 64,
    )
    with pytest.raises(ValidationError):
        ApiAuditAppendReceipt(**values)
    values["summary_sha256"] = "d" * 64
    retained = ApiAuditAppendReceipt(**values)
    assert str.__str__(retained.payload_slot) == "REQUEST"
    missing = dict(values)
    missing.pop("payload_copy_key")
    with pytest.raises(ValidationError):
        ApiAuditAppendReceipt(**missing)


def test_root_cannot_request_a_retained_copy_without_an_eligible_summary() -> None:
    with pytest.raises(ValidationError):
        ApiAuditAppendRequest(
            record=_root_record(),
            retention=AuditRetentionPolicyV1(
                mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
                ttl_seconds=60,
                retention_policy_version=1,
            ),
        )


def test_finish_evidence_has_no_request_or_receipt_authority() -> None:
    with pytest.raises(ValidationError):
        ApiAuditAppendRequest(
            record=_inert_finish_record(),
            retention=AuditRetentionPolicyV1(mode=AuditRetentionMode.SUMMARY_ONLY),
        )
    values = _receipt().model_dump(mode="python")
    values.update(
        event_kind=AuditEventKind.CALL_FINISHED,
        root_event_id=UUID(int=701),
        sequence=2,
    )
    with pytest.raises(ValidationError):
        ApiAuditAppendReceipt(**values)


@pytest.mark.parametrize(
    "error_type",
    (
        AuditEvidenceRejected,
        AuditConflict,
        AuditInvariantViolation,
        AuditTransactionStateError,
        AuditUnavailable,
        AuditCommitUncertain,
    ),
)
def test_service_errors_have_fixed_safe_messages_and_no_input_chain(
    error_type: type[Exception],
) -> None:
    error = error_type()
    rendered = str(error)
    assert "secret-canary" not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None
    with pytest.raises(TypeError):
        error_type("secret-canary")
