"""Exact closed contracts for immutable API-audit metadata."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
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
    DispatchLastPhase,
    LocalRejectionReason,
    LocalRejectionSummaryV1,
    PayloadSlot,
    PublicAuditContext,
    SimulatorOutcomeKind,
    SimulatorRequestSummaryV1,
    SimulatorResponseSummaryV1,
    TransferRejectionReason,
    TransferRejectionSummaryV1,
    VenueOutcomeKind,
    VenueRequestSummaryV1,
    VenueResponseSummaryV1,
)

pytestmark = pytest.mark.unit

U1 = UUID("00000000-0000-0000-0000-000000000001")
U2 = UUID("00000000-0000-0000-0000-000000000002")
U3 = UUID("00000000-0000-0000-0000-000000000003")
U4 = UUID("00000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 11, 5, tzinfo=UTC)
H1 = "a" * 64


def _public() -> PublicAuditContext:
    return PublicAuditContext(
        context_kind=AuditContextKind.PUBLIC,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )


def _account(mode: ExecutionMode = ExecutionMode.LIVE) -> AccountAuditContext:
    return AccountAuditContext(
        context_kind=AuditContextKind.ACCOUNT,
        account_id=U1,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=mode,
    )


def test_contexts_are_exact_closed_and_branch_specific() -> None:
    public = _public()
    account = _account()
    assert public.model_dump() == {
        "context_kind": AuditContextKind.PUBLIC,
        "region": DeltaRegion.INDIA,
        "environment": DeltaEnvironment.TESTNET,
    }
    assert account.account_id == U1
    assert account.account_id is not U1
    assert account.mode is ExecutionMode.LIVE
    for model, change in (
        (PublicAuditContext, {"context_kind": "PUBLIC"}),
        (PublicAuditContext, {"region": "india"}),
        (PublicAuditContext, {"account_id": U1}),
        (AccountAuditContext, {"context_kind": AuditContextKind.PUBLIC}),
        (AccountAuditContext, {"account_id": UUID(int=0)}),
        (AccountAuditContext, {"mode": "LIVE"}),
    ):
        values = (
            public.model_dump() if model is PublicAuditContext else account.model_dump()
        ) | change
        with pytest.raises(ValidationError) as captured:
            model(**values)
        assert all(detail.get("input") is None for detail in captured.value.errors())


def test_summary_variants_are_closed_and_preserve_absence() -> None:
    request = VenueRequestSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.VENUE_REQUEST,
        route_id="products",
        method="GET",
        path_template="/v2/products",
        route_policy="delta-docs-2026-11-03",
        weight=3,
        request_evidence_sha256=H1,
    )
    assert request.model_dump(exclude_none=True) == {
        "schema_version": 1,
        "summary_kind": AuditSummaryKind.VENUE_REQUEST,
        "route_id": "products",
        "method": "GET",
        "path_template": "/v2/products",
        "route_policy": "delta-docs-2026-11-03",
        "weight": 3,
        "request_evidence_sha256": H1,
    }
    response = VenueResponseSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.VENUE_RESPONSE,
        outcome_kind=VenueOutcomeKind.SUCCESS,
        http_status=200,
        completed_response_sha256=H1,
        completed_response_byte_length=3,
        duration_ns=7,
    )
    assert response.completed_response_byte_length == 3
    simulator_request = SimulatorRequestSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.SIMULATOR_REQUEST,
        simulator_backend_id="fixture-simulator",
        simulator_profile_id="fixture-profile",
        simulator_profile_version="1",
        simulator_request_evidence_sha256=H1,
    )
    simulator_response = SimulatorResponseSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.SIMULATOR_RESPONSE,
        outcome_kind=SimulatorOutcomeKind.ACCEPTED,
        duration_ns=7,
    )
    local = LocalRejectionSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.LOCAL_REJECTION,
        reason=LocalRejectionReason.INVALID_REQUEST,
        route_id="UNRESOLVED",
    )
    transfer = TransferRejectionSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.TRANSFER_REJECTION,
        reason=TransferRejectionReason.ALREADY_ENTERED,
        send_started_by_this_call=False,
        prior_dispatch_event_id=U2,
        prior_consumption_id=U3,
        prior_bytes_sent_possible=True,
    )
    assert simulator_request.summary_kind is AuditSummaryKind.SIMULATOR_REQUEST
    assert simulator_response.outcome_kind is SimulatorOutcomeKind.ACCEPTED
    assert local.route_id == "UNRESOLVED"
    assert transfer.prior_bytes_sent_possible is True


@pytest.mark.parametrize(
    ("model", "values"),
    (
        (
            VenueRequestSummaryV1,
            {
                "schema_version": 1,
                "summary_kind": AuditSummaryKind.VENUE_REQUEST,
                "route_id": "UNRESOLVED",
                "method": "GET",
            },
        ),
        (
            VenueResponseSummaryV1,
            {
                "schema_version": 1,
                "summary_kind": AuditSummaryKind.VENUE_RESPONSE,
                "outcome_kind": VenueOutcomeKind.SUCCESS,
                "completed_response_sha256": H1,
                "duration_ns": 0,
            },
        ),
        (
            SimulatorResponseSummaryV1,
            {
                "schema_version": True,
                "summary_kind": AuditSummaryKind.SIMULATOR_RESPONSE,
                "outcome_kind": SimulatorOutcomeKind.ACCEPTED,
                "duration_ns": 0,
            },
        ),
        (
            TransferRejectionSummaryV1,
            {
                "schema_version": 1,
                "summary_kind": AuditSummaryKind.TRANSFER_REJECTION,
                "reason": TransferRejectionReason.UNOWNED_OR_WRONG_TARGET_RESULT,
                "send_started_by_this_call": False,
                "prior_dispatch_event_id": U2,
            },
        ),
    ),
)
def test_summary_truth_tables_reject_partial_or_forbidden_values(
    model: type[object], values: dict[str, object]
) -> None:
    with pytest.raises(ValidationError) as captured:
        model(**values)  # type: ignore[call-arg]
    assert all(detail.get("input") is None for detail in captured.value.errors())


def test_call_started_record_is_exact_and_dispatch_identity_is_distinct() -> None:
    read = ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=_public(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=U2,
        correlation_id=U2,
        policy_version="delta-docs-2026-11-03",
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
    )
    assert read.summary is None
    dispatch = ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=_account(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.DISPATCH,
        logical_call_id=U4,
        correlation_id=U3,
        policy_version="delta-docs-2026-11-04-private-v1",
        route_family="PRIVATE",
        route_id="place_order",
        method="POST",
        path_template="/v2/orders",
        weight=5,
        command_id=U2,
        operation="PLACE_ORDER",
        order_id=U3,
        order_attempt_id=U4,
        order_attempt_number=1,
        attempt_backend="VENUE",
        command_evidence_sha256=H1,
    )
    assert dispatch.logical_call_id == dispatch.order_attempt_id
    assert dispatch.correlation_id != dispatch.logical_call_id


def test_record_truth_tables_reject_cross_context_and_wrong_dispatch_identity() -> None:
    base = {
        "event_kind": AuditEventKind.CALL_STARTED,
        "context": _account(),
        "target": AuditTarget.VENUE,
        "call_kind": AuditCallKind.DISPATCH,
        "logical_call_id": U3,
        "correlation_id": U2,
        "policy_version": "delta-docs-2026-11-04-private-v1",
        "route_family": "PRIVATE",
        "route_id": "place_order",
        "method": "POST",
        "path_template": "/v2/orders",
        "weight": 5,
        "command_id": U2,
        "operation": "PLACE_ORDER",
        "order_id": U3,
        "order_attempt_id": U4,
        "order_attempt_number": 1,
        "attempt_backend": "VENUE",
        "command_evidence_sha256": H1,
    }
    with pytest.raises(ValidationError):
        ApiAuditRecordV1(**base)
    with pytest.raises(ValidationError):
        ApiAuditRecordV1(
            event_kind=AuditEventKind.ATTEMPT_STARTED,
            context=_public(),
            target=AuditTarget.SIMULATOR,
            call_kind=AuditCallKind.READ,
            logical_call_id=U2,
            correlation_id=U2,
            policy_version="delta-docs-2026-11-03",
            root_event_id=U3,
            route_family="PUBLIC",
            route_id="products",
            method="GET",
            path_template="/v2/products",
            weight=3,
            transport_attempt_number=1,
            occurred_at=NOW,
            summary=VenueRequestSummaryV1(
                schema_version=1,
                summary_kind=AuditSummaryKind.VENUE_REQUEST,
                route_id="products",
                method="GET",
                path_template="/v2/products",
                route_policy="delta-docs-2026-11-03",
                weight=3,
            ),
        )


def test_descriptor_and_allocation_own_storage_outputs_only() -> None:
    descriptor = AuditPayloadDescriptor(
        retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
        payload_slot=PayloadSlot.REQUEST,
        payload_schema_version=1,
        payload_sha256=H1,
        payload_byte_length=200,
        payload_expires_at=NOW + timedelta(seconds=60),
        payload_ttl_seconds=60,
        retention_policy_version=1,
    )
    allocation = AuditStorageAllocation(
        id=U4,
        sequence=2,
        audit_recorded_at=NOW,
        descriptor=descriptor,
        closure_source=None,
    )
    assert allocation.descriptor is not descriptor
    with pytest.raises(ValidationError):
        AuditPayloadDescriptor(
            retention_mode=AuditRetentionMode.SUMMARY_ONLY,
            payload_slot=PayloadSlot.REQUEST,
        )
    with pytest.raises(ValidationError):
        AuditStorageAllocation(
            id=UUID(int=0),
            sequence=0,
            audit_recorded_at=NOW,
            descriptor=None,
            closure_source=None,
        )


def test_no_model_accepts_explicit_null_or_extra_free_text() -> None:
    with pytest.raises(ValidationError):
        VenueRequestSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.VENUE_REQUEST,
            route_id="products",
            method=None,
            path_template="/v2/products",
            route_policy="delta-docs-2026-11-03",
            weight=3,
        )
    with pytest.raises(ValidationError) as captured:
        PublicAuditContext(
            context_kind=AuditContextKind.PUBLIC,
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
            note="fixture-secret",
        )
    assert "fixture-secret" not in str(captured.value)


def test_terminal_records_require_closed_phase_and_summary_combinations() -> None:
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.VENUE_NO_ENTRY,
        context=_account(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.DISPATCH,
        logical_call_id=U4,
        correlation_id=U3,
        policy_version="delta-docs-2026-11-04-private-v1",
        root_event_id=U2,
        route_family="PRIVATE",
        route_id="place_order",
        method="POST",
        path_template="/v2/orders",
        weight=5,
        command_id=U2,
        operation="PLACE_ORDER",
        order_id=U3,
        order_attempt_id=U4,
        order_attempt_number=1,
        attempt_backend="VENUE",
        command_evidence_sha256=H1,
        transport_attempt_number=1,
        dispatch_event_id=UUID(int=10),
        consumption_id=UUID(int=11),
        dispatch_recorded_at=NOW,
        semantic_request_sha256="d" * 64,
        wire_request_evidence_sha256="e" * 64,
        pre_dispatch_audit_receipt_id=UUID(int=12),
        last_phase=DispatchLastPhase.AUDIT_RECORDED,
        entry_possible=False,
        reconciliation_required=False,
        summary=LocalRejectionSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.LOCAL_REJECTION,
            reason=LocalRejectionReason.DEADLINE_EXPIRED,
            route_id="place_order",
        ),
    )
    assert record.last_phase is DispatchLastPhase.AUDIT_RECORDED
    with pytest.raises(ValidationError):
        ApiAuditRecordV1.model_validate(
            record.model_copy(update={"entry_possible": True}),
        )


@pytest.mark.parametrize(
    "reason",
    (
        LocalRejectionReason.INVALID_REQUEST,
        LocalRejectionReason.ROUTE_DENIED,
        LocalRejectionReason.ADMISSION_DENIED,
        LocalRejectionReason.LIMITER_DENIED,
        LocalRejectionReason.CANCELLED,
    ),
)
def test_local_rejected_admits_only_pure_local_reason_vocabulary(
    reason: LocalRejectionReason,
) -> None:
    record = ApiAuditRecordV1(
        event_kind=AuditEventKind.LOCAL_REJECTED,
        context=_public(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=U2,
        correlation_id=U2,
        policy_version="delta-docs-2026-11-03",
        root_event_id=U3,
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
        summary=LocalRejectionSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.LOCAL_REJECTION,
            reason=reason,
            route_id="products",
        ),
    )
    assert record.summary is not None


@pytest.mark.parametrize(
    "reason",
    (
        LocalRejectionReason.SINK_BINDING_MISMATCH,
        LocalRejectionReason.PREPARATION_FAILED,
        LocalRejectionReason.AUDIT_FAILED,
        LocalRejectionReason.CURRENT_INVALIDATED,
        LocalRejectionReason.DEADLINE_EXPIRED,
        LocalRejectionReason.SIGNING_FAILED,
        LocalRejectionReason.SIGNATURE_EXPIRED,
        LocalRejectionReason.LIMITER_INVALIDATED,
        LocalRejectionReason.INTERNAL_EXCEPTION,
    ),
)
def test_local_rejected_rejects_no_entry_only_reason_vocabulary(
    reason: LocalRejectionReason,
) -> None:
    with pytest.raises(ValidationError):
        ApiAuditRecordV1(
            event_kind=AuditEventKind.LOCAL_REJECTED,
            context=_public(),
            target=AuditTarget.VENUE,
            call_kind=AuditCallKind.READ,
            logical_call_id=U2,
            correlation_id=U2,
            policy_version="delta-docs-2026-11-03",
            root_event_id=U3,
            route_family="PUBLIC",
            route_id="products",
            method="GET",
            path_template="/v2/products",
            weight=3,
            summary=LocalRejectionSummaryV1(
                schema_version=1,
                summary_kind=AuditSummaryKind.LOCAL_REJECTION,
                reason=reason,
                route_id="products",
            ),
        )


def test_transfer_rejection_requires_resolved_private_order_receiver_route() -> None:
    base: dict[str, object] = {
        "event_kind": AuditEventKind.VENUE_TRANSFER_REJECTED,
        "context": _account(),
        "target": AuditTarget.VENUE,
        "call_kind": AuditCallKind.RECEIVER_REJECTION,
        "logical_call_id": U2,
        "correlation_id": U2,
        "policy_version": "delta-docs-2026-11-04-private-v1",
        "root_event_id": U3,
        "summary": TransferRejectionSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.TRANSFER_REJECTION,
            reason=TransferRejectionReason.UNOWNED_OR_WRONG_TARGET_RESULT,
            send_started_by_this_call=False,
        ),
    }
    valid = ApiAuditRecordV1(
        **base,
        route_family="PRIVATE",
        route_id="place_order",
        method="POST",
        path_template="/v2/orders",
        weight=5,
    )
    assert valid.route_id == "place_order"
    for route in (
        {
            "route_family": "UNRESOLVED",
            "route_id": "UNRESOLVED",
        },
        {
            "route_family": "PRIVATE",
            "route_id": "balances",
            "method": "GET",
            "path_template": "/v2/wallet/balances",
            "weight": 3,
        },
    ):
        with pytest.raises(ValidationError):
            ApiAuditRecordV1(**base, **route)


def test_call_finished_and_maintenance_reject_every_cross_variant_extra() -> None:
    finished = ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_FINISHED,
        context=_public(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=U2,
        correlation_id=U2,
        policy_version="delta-docs-2026-11-03",
        root_event_id=U3,
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
        outcome_kind=VenueOutcomeKind.SUCCESS,
    )
    for name, value in (
        ("transport_attempt_number", 1),
        ("start_event_id", U4),
        ("dispatch_event_id", U4),
        ("entry_possible", False),
    ):
        with pytest.raises(ValidationError):
            ApiAuditRecordV1.model_validate(finished.model_copy(update={name: value}))

    maintenance = ApiAuditRecordV1(
        event_kind=AuditEventKind.MAINTENANCE_ROOT,
        context=_account(),
        source_event_id=U2,
        source_payload_slot=PayloadSlot.REQUEST,
        source_payload_schema_version=1,
        source_payload_sha256=H1,
        source_payload_byte_length=200,
        source_payload_expires_at=NOW,
        source_payload_ttl_seconds=60,
        source_retention_policy_version=1,
    )
    for name, value in (
        ("occurred_at", NOW),
        ("transport_attempt_number", 1),
        ("outcome_kind", VenueOutcomeKind.SUCCESS),
        ("entry_possible", False),
    ):
        with pytest.raises(ValidationError):
            ApiAuditRecordV1.model_validate(maintenance.model_copy(update={name: value}))


def test_inert_route_projection_rejects_drift_and_unregistered_response_metadata() -> None:
    root = ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=_public(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=U2,
        correlation_id=U2,
        policy_version="delta-docs-2026-11-03",
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
    )
    for change in (
        {"route_id": "not_registered"},
        {"method": "POST"},
        {"path_template": "/v2/tickers"},
        {"weight": 4},
        {"policy_version": "drifted-policy"},
    ):
        with pytest.raises(ValidationError):
            ApiAuditRecordV1.model_validate(root.model_copy(update=change))

    with pytest.raises(ValidationError):
        VenueResponseSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.VENUE_RESPONSE,
            outcome_kind=VenueOutcomeKind.AUTH_REJECTED,
            http_status=401,
            exchange_code="fixture-secret-code",
            duration_ns=1,
        )


def test_response_summary_status_request_id_and_exchange_code_branches_are_closed() -> None:
    base = {
        "schema_version": 1,
        "summary_kind": AuditSummaryKind.VENUE_RESPONSE,
        "outcome_kind": VenueOutcomeKind.TRANSPORT_ERROR,
        "duration_ns": 1,
    }
    for extra in (
        {"http_status": 503},
        {"exchange_request_id": "request-1"},
        {"completed_response_sha256": H1, "completed_response_byte_length": 1},
    ):
        with pytest.raises(ValidationError):
            VenueResponseSummaryV1(**(base | extra))
    with pytest.raises(ValidationError):
        VenueResponseSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.VENUE_RESPONSE,
            outcome_kind=VenueOutcomeKind.SUCCESS,
            exchange_request_id="request-1",
            duration_ns=1,
        )


def test_attempt_preparation_uncertain_is_only_an_independent_early_rejection() -> None:
    summary = LocalRejectionSummaryV1(
        schema_version=1,
        summary_kind=AuditSummaryKind.LOCAL_REJECTION,
        reason=LocalRejectionReason.ATTEMPT_PREPARATION_UNCERTAIN,
        route_id="UNRESOLVED",
    )
    early = ApiAuditRecordV1(
        event_kind=AuditEventKind.LOCAL_REJECTED,
        context=_public(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.LOCAL_REJECTION,
        logical_call_id=U2,
        correlation_id=U2,
        policy_version="delta-docs-2026-11-03",
        root_event_id=U3,
        route_family="UNRESOLVED",
        route_id="UNRESOLVED",
        summary=summary,
    )
    assert early.summary == summary
    with pytest.raises(ValidationError):
        ApiAuditRecordV1(
            event_kind=AuditEventKind.LOCAL_REJECTED,
            context=_account(),
            target=AuditTarget.VENUE,
            call_kind=AuditCallKind.DISPATCH,
            logical_call_id=U4,
            correlation_id=U3,
            policy_version="delta-docs-2026-11-04-private-v1",
            root_event_id=U2,
            route_family="PRIVATE",
            route_id="place_order",
            method="POST",
            path_template="/v2/orders",
            weight=5,
            command_id=U2,
            operation="PLACE_ORDER",
            order_id=U3,
            order_attempt_id=U4,
            order_attempt_number=1,
            attempt_backend="VENUE",
            command_evidence_sha256=H1,
            summary=LocalRejectionSummaryV1(
                schema_version=1,
                summary_kind=AuditSummaryKind.LOCAL_REJECTION,
                reason=LocalRejectionReason.ATTEMPT_PREPARATION_UNCERTAIN,
                route_id="place_order",
            ),
        )
