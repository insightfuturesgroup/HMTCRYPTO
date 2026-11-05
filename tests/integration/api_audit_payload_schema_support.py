"""Inert D13a descriptor/source fixtures for D13b payload schema tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.persistence.api_audit_codec import encode_api_audit_summary
from hmt_crypto.persistence.api_audit_contracts import (
    AccountAuditContext,
    ApiAuditRecordV1,
    AuditCallKind,
    AuditContextKind,
    AuditEventKind,
    AuditPayloadDescriptor,
    AuditRetentionMode,
    AuditStorageAllocation,
    AuditSummaryKind,
    AuditTarget,
    PayloadSlot,
    PublicAuditContext,
    VenueRequestSummaryV1,
)
from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.api_audit_payload_contracts import AuditPayloadSourceV1
from hmt_crypto.persistence.api_audit_payload_models import ApiAuditPayload
from hmt_crypto.persistence.identity_models import Account

NOW = datetime(2026, 11, 5, 0, 0, 1, tzinfo=UTC)
ROOT_ID = UUID("00000000-0000-0000-0000-000000000008")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000065")
ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
ACCOUNT_ROOT_ID = UUID("00000000-0000-0000-0000-000000000108")
ACCOUNT_EVENT_ID = UUID("00000000-0000-0000-0000-000000000165")


def public_context() -> PublicAuditContext:
    return PublicAuditContext(
        context_kind=AuditContextKind.PUBLIC,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )


def venue_request_summary() -> VenueRequestSummaryV1:
    return VenueRequestSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.VENUE_REQUEST,
        route_id="products",
        method="GET",
        path_template="/v2/products",
        route_policy="delta-docs-2026-11-03",
        weight=3,
        request_evidence_sha256="a" * 64,
    )


def retained_descriptor() -> AuditPayloadDescriptor:
    payload = encode_api_audit_summary(venue_request_summary())
    return AuditPayloadDescriptor(
        retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
        payload_slot=PayloadSlot.REQUEST,
        payload_schema_version=1,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_byte_length=len(payload),
        payload_expires_at=NOW + timedelta(seconds=60),
        payload_ttl_seconds=60,
        retention_policy_version=3,
    )


def public_root() -> ApiAuditEvent:
    call_id = UUID(int=6)
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=public_context(),
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
    return ApiAuditEvent.from_record(
        record,
        AuditStorageAllocation(
            id=ROOT_ID,
            sequence=1,
            audit_recorded_at=NOW,
            descriptor=None,
            closure_source=None,
        ),
    )


def retained_public_event() -> ApiAuditEvent:
    call_id = UUID(int=6)
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.ATTEMPT_STARTED,
        context=public_context(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=call_id,
        correlation_id=call_id,
        policy_version="delta-docs-2026-11-03",
        root_event_id=ROOT_ID,
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
        transport_attempt_number=1,
        occurred_at=NOW - timedelta(seconds=1),
        summary=venue_request_summary(),
    )
    return ApiAuditEvent.from_record(
        record,
        AuditStorageAllocation(
            id=EVENT_ID,
            sequence=2,
            audit_recorded_at=NOW,
            descriptor=retained_descriptor(),
            closure_source=None,
        ),
    )


def public_source() -> AuditPayloadSourceV1:
    event = retained_public_event()
    assert event.summary_payload is not None
    assert event.summary_sha256 is not None
    return AuditPayloadSourceV1(
        owning_event_id=event.id,
        context=public_context(),
        event_kind=AuditEventKind.ATTEMPT_STARTED,
        summary_kind=AuditSummaryKind.VENUE_REQUEST,
        summary_payload=event.summary_payload,
        summary_sha256=event.summary_sha256,
        audit_recorded_at=event.audit_recorded_at,
        descriptor=retained_descriptor(),
    )


def public_payload() -> ApiAuditPayload:
    return ApiAuditPayload.from_source(public_source())


def account_context(account_id: UUID = ACCOUNT_ID) -> AccountAuditContext:
    return AccountAuditContext(
        context_kind=AuditContextKind.ACCOUNT,
        account_id=account_id,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=ExecutionMode.LIVE,
    )


def account_owner(account_id: UUID = ACCOUNT_ID) -> Account:
    return Account(
        id=account_id,
        region="india",
        environment="testnet",
        mode="LIVE",
        profile_name=f"fixture-{account_id.int}",
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def account_request_summary() -> VenueRequestSummaryV1:
    return VenueRequestSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.VENUE_REQUEST,
        route_id="balances",
        method="GET",
        path_template="/v2/wallet/balances",
        route_policy="delta-docs-2026-11-04-private-v1",
        weight=3,
        request_evidence_sha256="c" * 64,
    )


def account_descriptor() -> AuditPayloadDescriptor:
    payload = encode_api_audit_summary(account_request_summary())
    return AuditPayloadDescriptor(
        retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
        payload_slot=PayloadSlot.REQUEST,
        payload_schema_version=1,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_byte_length=len(payload),
        payload_expires_at=NOW + timedelta(seconds=60),
        payload_ttl_seconds=60,
        retention_policy_version=4,
    )


def account_root() -> ApiAuditEvent:
    call_id = UUID(int=106)
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=account_context(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=call_id,
        correlation_id=call_id,
        policy_version="delta-docs-2026-11-04-private-v1",
        route_family="PRIVATE",
        route_id="balances",
        method="GET",
        path_template="/v2/wallet/balances",
        weight=3,
    )
    return ApiAuditEvent.from_record(
        record,
        AuditStorageAllocation(
            id=ACCOUNT_ROOT_ID,
            sequence=1,
            audit_recorded_at=NOW,
            descriptor=None,
            closure_source=None,
        ),
    )


def retained_account_event() -> ApiAuditEvent:
    call_id = UUID(int=106)
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.ATTEMPT_STARTED,
        context=account_context(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=call_id,
        correlation_id=call_id,
        policy_version="delta-docs-2026-11-04-private-v1",
        root_event_id=ACCOUNT_ROOT_ID,
        route_family="PRIVATE",
        route_id="balances",
        method="GET",
        path_template="/v2/wallet/balances",
        weight=3,
        transport_attempt_number=1,
        occurred_at=NOW - timedelta(seconds=1),
        summary=account_request_summary(),
    )
    return ApiAuditEvent.from_record(
        record,
        AuditStorageAllocation(
            id=ACCOUNT_EVENT_ID,
            sequence=2,
            audit_recorded_at=NOW,
            descriptor=account_descriptor(),
            closure_source=None,
        ),
    )


def account_source() -> AuditPayloadSourceV1:
    event = retained_account_event()
    assert event.summary_payload is not None
    assert event.summary_sha256 is not None
    return AuditPayloadSourceV1(
        owning_event_id=event.id,
        context=account_context(),
        event_kind=AuditEventKind.ATTEMPT_STARTED,
        summary_kind=AuditSummaryKind.VENUE_REQUEST,
        summary_payload=event.summary_payload,
        summary_sha256=event.summary_sha256,
        audit_recorded_at=event.audit_recorded_at,
        descriptor=account_descriptor(),
    )


def account_payload() -> ApiAuditPayload:
    return ApiAuditPayload.from_source(account_source())
