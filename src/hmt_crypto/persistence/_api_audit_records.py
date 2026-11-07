"""Closed producer records and storage allocation for immutable API audit metadata."""

from datetime import timedelta
from typing import Literal, Never, Self

from pydantic import model_validator

from hmt_crypto.domain._private_model import PrivateBoundaryModel
from hmt_crypto.persistence._api_audit_contexts import (
    _EVENT_FACT_FIELDS,
    _EVENT_ID_FIELDS,
    AccountAuditContext,
    AuditCallKind,
    AuditContext,
    AuditEventKind,
    AuditRetentionMode,
    AuditTarget,
    AuditUtc,
    DispatchLastPhase,
    HttpMethod,
    LocalRejectionReason,
    NonzeroUuid,
    PayloadSlot,
    PositiveBigInt,
    SchemaVersionOne,
    Sha256Hex,
    SimulatorOutcomeKind,
    Token128,
    Token256,
    VenueOutcomeKind,
    _execution_mode_literal,
)
from hmt_crypto.persistence._api_audit_summaries import (
    _LOCAL_REJECTION_REASONS,
    _NO_ENTRY_REASONS,
    _SUMMARY_TYPES,
    AuditSummaryV1,
    LocalRejectionSummaryV1,
    SimulatorRequestSummaryV1,
    SimulatorResponseSummaryV1,
    TransferRejectionSummaryV1,
    VenueRequestSummaryV1,
    VenueResponseSummaryV1,
    _validate_route_binding,
    _validate_summary_binding,
)

_PUBLIC_MODULE = "hmt_crypto.persistence.api_audit_contracts"


def _terminal_truth_sql() -> str:
    return (
        "(event_kind <> 'VENUE_NO_ENTRY' OR (entry_possible = 0 "
        "AND reconciliation_required = 0 AND outcome_kind = 'LOCAL_REJECTED' AND "
        "((last_phase = 'ISSUED' AND wire_request_evidence_sha256 IS NULL "
        "AND pre_dispatch_audit_receipt_id IS NULL) OR "
        "(last_phase = 'CLAIMED_FOR_UNSIGNED_PREPARATION' "
        "AND pre_dispatch_audit_receipt_id IS NULL) OR "
        "(last_phase = 'AUDIT_RECORDED' AND wire_request_evidence_sha256 IS NOT NULL "
        "AND pre_dispatch_audit_receipt_id IS NOT NULL)))) AND "
        "(event_kind <> 'VENUE_ENTERED' OR (entry_possible = 1 AND last_phase IS NULL)) AND "
        "(event_kind <> 'SIMULATOR_OUTCOME' OR (last_phase IS NULL AND "
        "((entry_possible = 1 AND outcome_kind IN ('ACCEPTED','REJECTED','AMBIGUOUS') "
        "AND pre_dispatch_audit_receipt_id IS NOT NULL) OR "
        "(entry_possible = 0 AND outcome_kind = 'NOT_SUBMITTED'))))"
    )


def _invalid(model: PrivateBoundaryModel) -> Never:
    raise type(model)._safe_error("api_audit_value", "invalid API audit value") from None


class AuditPayloadDescriptor(PrivateBoundaryModel):
    """One optional bounded sanitized-copy descriptor; never payload bytes."""

    __module__ = _PUBLIC_MODULE
    retention_mode: AuditRetentionMode
    payload_slot: PayloadSlot | None = None
    payload_schema_version: SchemaVersionOne | None = None
    payload_sha256: Sha256Hex | None = None
    payload_byte_length: PositiveBigInt | None = None
    payload_expires_at: AuditUtc | None = None
    payload_ttl_seconds: PositiveBigInt | None = None
    retention_policy_version: PositiveBigInt | None = None

    @model_validator(mode="after")
    def validate_descriptor(self) -> Self:
        """Require either no copy fields or the complete bounded copy bundle."""
        bundle = (
            self.payload_slot,
            self.payload_schema_version,
            self.payload_sha256,
            self.payload_byte_length,
            self.payload_expires_at,
            self.payload_ttl_seconds,
            self.retention_policy_version,
        )
        fields = object.__getattribute__(self, "__pydantic_fields_set__")
        state = object.__getattribute__(self, "__dict__")
        if any(state.get(name) is None for name in fields):
            _invalid(self)
        if self.retention_mode is AuditRetentionMode.SUMMARY_ONLY:
            if any(value is not None for value in bundle):
                _invalid(self)
        elif any(value is None for value in bundle):
            _invalid(self)
        elif self.payload_byte_length is not None and self.payload_byte_length > 2048:
            _invalid(self)
        elif self.payload_ttl_seconds is not None and self.payload_ttl_seconds > 2_592_000:
            _invalid(self)
        return self


class ApiAuditRecordV1(PrivateBoundaryModel):
    """Exact producer-owned evidence record before storage outputs are allocated."""

    __module__ = _PUBLIC_MODULE
    event_kind: AuditEventKind
    context: AuditContext
    target: AuditTarget | None = None
    call_kind: AuditCallKind | None = None
    logical_call_id: NonzeroUuid | None = None
    correlation_id: NonzeroUuid | None = None
    policy_version: Token128 | None = None
    root_event_id: NonzeroUuid | None = None
    traversal_id: NonzeroUuid | None = None
    page_number: PositiveBigInt | None = None
    route_family: Literal["PUBLIC", "PRIVATE", "UNRESOLVED"] | None = None
    route_id: Token128 | None = None
    method: HttpMethod | None = None
    path_template: Token256 | None = None
    weight: PositiveBigInt | None = None
    command_id: NonzeroUuid | None = None
    operation: Literal["PLACE_ORDER", "MODIFY_ORDER"] | None = None
    order_id: NonzeroUuid | None = None
    order_attempt_id: NonzeroUuid | None = None
    order_attempt_number: PositiveBigInt | None = None
    attempt_backend: Literal["VENUE", "SIMULATOR"] | None = None
    command_evidence_sha256: Sha256Hex | None = None
    simulator_backend_id: Token128 | None = None
    simulator_profile_id: Token128 | None = None
    simulator_profile_version: Token128 | None = None
    transport_attempt_number: PositiveBigInt | None = None
    start_event_id: NonzeroUuid | None = None
    dispatch_event_id: NonzeroUuid | None = None
    consumption_id: NonzeroUuid | None = None
    dispatch_recorded_at: AuditUtc | None = None
    semantic_request_sha256: Sha256Hex | None = None
    wire_request_evidence_sha256: Sha256Hex | None = None
    simulator_request_evidence_sha256: Sha256Hex | None = None
    pre_dispatch_audit_receipt_id: NonzeroUuid | None = None
    source_event_id: NonzeroUuid | None = None
    source_payload_slot: PayloadSlot | None = None
    source_payload_schema_version: SchemaVersionOne | None = None
    source_payload_sha256: Sha256Hex | None = None
    source_payload_byte_length: PositiveBigInt | None = None
    source_payload_expires_at: AuditUtc | None = None
    source_payload_ttl_seconds: PositiveBigInt | None = None
    source_retention_policy_version: PositiveBigInt | None = None
    occurred_at: AuditUtc | None = None
    last_phase: DispatchLastPhase | None = None
    entry_possible: bool | None = None
    reconciliation_required: bool | None = None
    outcome_kind: VenueOutcomeKind | SimulatorOutcomeKind | None = None
    summary: AuditSummaryV1 | None = None

    @model_validator(mode="after")
    def validate_closed_variant(self) -> Self:
        """Enforce the exact context, target, identity and event-family branches."""
        self._reject_null_and_validate_common_identity()
        self._validate_exact_variant_fields()
        if self.event_kind in (AuditEventKind.MAINTENANCE_ROOT, AuditEventKind.PAYLOAD_PURGED):
            self._validate_maintenance()
        else:
            self._validate_api_identity()
            self._validate_event_shape()
        return self

    def _validate_exact_variant_fields(self) -> None:
        allowed = {
            "event_kind",
            "context",
            *_EVENT_ID_FIELDS[self.event_kind],
            *_EVENT_FACT_FIELDS[self.event_kind],
        }
        if self.event_kind in _SUMMARY_TYPES:
            allowed.add("summary")
        state = object.__getattribute__(self, "__dict__")
        if any(value is not None and name not in allowed for name, value in state.items()):
            _invalid(self)

    def _reject_null_and_validate_common_identity(self) -> None:
        fields = object.__getattribute__(self, "__pydantic_fields_set__")
        state = object.__getattribute__(self, "__dict__")
        if any(state.get(name) is None for name in fields):
            _invalid(self)
        if (self.traversal_id is None) != (self.page_number is None):
            _invalid(self)
        if self.source_payload_byte_length is not None and self.source_payload_byte_length > 2048:
            _invalid(self)
        if (
            self.source_payload_ttl_seconds is not None
            and self.source_payload_ttl_seconds > 2_592_000
        ):
            _invalid(self)

    def _validate_api_identity(self) -> None:
        required = (
            self.target,
            self.call_kind,
            self.logical_call_id,
            self.correlation_id,
            self.policy_version,
        )
        if any(value is None for value in required):
            _invalid(self)
        if self.call_kind is AuditCallKind.DISPATCH:
            self._validate_dispatch_identity()
        else:
            if self.logical_call_id != self.correlation_id:
                _invalid(self)
            self._require_absent(
                "command_id",
                "operation",
                "order_id",
                "order_attempt_id",
                "order_attempt_number",
                "attempt_backend",
                "command_evidence_sha256",
                "dispatch_event_id",
                "consumption_id",
                "dispatch_recorded_at",
                "semantic_request_sha256",
                "wire_request_evidence_sha256",
                "simulator_request_evidence_sha256",
                "pre_dispatch_audit_receipt_id",
            )
        if self.target is AuditTarget.SIMULATOR:
            self._validate_simulator_target()
        elif self.target is AuditTarget.VENUE:
            _validate_route_binding(self)

    def _validate_dispatch_identity(self) -> None:
        if type(self.context) is not AccountAuditContext:
            _invalid(self)
        self._require_present(
            "command_id",
            "operation",
            "order_id",
            "order_attempt_id",
            "order_attempt_number",
            "attempt_backend",
            "command_evidence_sha256",
        )
        if self.logical_call_id != self.order_attempt_id:
            _invalid(self)

    def _validate_simulator_target(self) -> None:
        if type(self.context) is not AccountAuditContext or _execution_mode_literal(
            self.context.mode
        ) not in ("PAPER", "BACKTEST"):
            _invalid(self)
        if self.call_kind is not AuditCallKind.DISPATCH or self.attempt_backend != "SIMULATOR":
            _invalid(self)
        if any(
            value is not None
            for value in (
                self.route_family,
                self.route_id,
                self.method,
                self.path_template,
                self.weight,
                self.transport_attempt_number,
                self.wire_request_evidence_sha256,
            )
        ):
            _invalid(self)
        if any(
            value is None
            for value in (
                self.simulator_backend_id,
                self.simulator_profile_id,
                self.simulator_profile_version,
            )
        ):
            _invalid(self)

    def _validate_event_shape(self) -> None:
        kind = self.event_kind
        if kind is AuditEventKind.CALL_STARTED:
            self._require_absent("root_event_id")
        else:
            self._require_present("root_event_id")
        if kind is AuditEventKind.CALL_FINISHED:
            self._require_present("outcome_kind")
            self._require_absent("summary")
            if self.target is AuditTarget.VENUE and type(self.outcome_kind) is not VenueOutcomeKind:
                _invalid(self)
            if (
                self.target is AuditTarget.SIMULATOR
                and type(self.outcome_kind) is not SimulatorOutcomeKind
            ):
                _invalid(self)
        elif kind is AuditEventKind.ATTEMPT_STARTED:
            self._require_venue_read_attempt(VenueRequestSummaryV1, start=False)
        elif kind is AuditEventKind.ATTEMPT_FINISHED:
            self._require_venue_read_attempt(VenueResponseSummaryV1, start=True)
        elif kind is AuditEventKind.LOCAL_REJECTED:
            self._require_summary(LocalRejectionSummaryV1)
            summary = self.summary
            assert type(summary) is LocalRejectionSummaryV1
            if str.__str__(summary.reason) not in _LOCAL_REJECTION_REASONS:
                _invalid(self)
            if (
                summary.reason is LocalRejectionReason.ATTEMPT_PREPARATION_UNCERTAIN
                and self.call_kind is not AuditCallKind.LOCAL_REJECTION
            ):
                _invalid(self)
            uncertain = str.__str__(summary.reason) == "DISPATCH_COMMIT_UNCERTAIN"
            if uncertain:
                if self.call_kind is not AuditCallKind.DISPATCH:
                    _invalid(self)
                if self.entry_possible is not False or self.reconciliation_required is not True:
                    _invalid(self)
            elif self.entry_possible is not None or self.reconciliation_required is not None:
                _invalid(self)
        elif kind is AuditEventKind.VENUE_PRE_DISPATCH:
            self._require_venue_dispatch(VenueRequestSummaryV1, terminal=False)
        elif kind is AuditEventKind.VENUE_NO_ENTRY:
            self._require_venue_dispatch(LocalRejectionSummaryV1, terminal=True)
            if self.entry_possible is not False or self.reconciliation_required is not False:
                _invalid(self)
            self._require_present("last_phase")
            summary = self.summary
            assert type(summary) is LocalRejectionSummaryV1
            if str.__str__(summary.reason) not in _NO_ENTRY_REASONS:
                _invalid(self)
            if self.last_phase is DispatchLastPhase.AUDIT_RECORDED:
                self._require_present(
                    "wire_request_evidence_sha256", "pre_dispatch_audit_receipt_id"
                )
            elif self.last_phase is DispatchLastPhase.ISSUED:
                self._require_absent(
                    "wire_request_evidence_sha256", "pre_dispatch_audit_receipt_id"
                )
            else:
                self._require_absent("pre_dispatch_audit_receipt_id")
        elif kind is AuditEventKind.VENUE_ENTERED:
            self._require_venue_dispatch(VenueResponseSummaryV1, terminal=True)
            if self.entry_possible is not True or type(self.reconciliation_required) is not bool:
                _invalid(self)
            self._require_present("wire_request_evidence_sha256", "pre_dispatch_audit_receipt_id")
            self._require_absent("last_phase")
        elif kind is AuditEventKind.SIMULATOR_PRE_DISPATCH:
            self._require_simulator_dispatch(SimulatorRequestSummaryV1, terminal=False)
        elif kind is AuditEventKind.SIMULATOR_OUTCOME:
            self._require_simulator_dispatch(SimulatorResponseSummaryV1, terminal=True)
        elif kind is AuditEventKind.VENUE_TRANSFER_REJECTED:
            if (
                self.call_kind is not AuditCallKind.RECEIVER_REJECTION
                or type(self.context) is not AccountAuditContext
                or _execution_mode_literal(self.context.mode) != "LIVE"
                or self.route_family != "PRIVATE"
                or self.route_id not in ("place_order", "modify_order")
            ):
                _invalid(self)
            self._require_summary(TransferRejectionSummaryV1)

    def _require_venue_read_attempt(
        self, summary_type: type[PrivateBoundaryModel], *, start: bool
    ) -> None:
        if self.target is not AuditTarget.VENUE or self.call_kind is not AuditCallKind.READ:
            _invalid(self)
        self._require_present("transport_attempt_number", "occurred_at")
        self._require_summary(summary_type)
        if start:
            self._require_present("start_event_id")
        else:
            self._require_absent("start_event_id")

    def _require_venue_dispatch(
        self, summary_type: type[PrivateBoundaryModel], *, terminal: bool
    ) -> None:
        if self.target is not AuditTarget.VENUE or self.call_kind is not AuditCallKind.DISPATCH:
            _invalid(self)
        self._require_present(
            "transport_attempt_number",
            "dispatch_event_id",
            "consumption_id",
            "dispatch_recorded_at",
            "semantic_request_sha256",
            "wire_request_evidence_sha256" if not terminal else "command_evidence_sha256",
        )
        if self.transport_attempt_number != 1:
            _invalid(self)
        self._require_summary(summary_type)
        if not terminal:
            self._require_absent("pre_dispatch_audit_receipt_id")

    def _require_simulator_dispatch(
        self, summary_type: type[PrivateBoundaryModel], *, terminal: bool
    ) -> None:
        if self.target is not AuditTarget.SIMULATOR:
            _invalid(self)
        self._require_present(
            "dispatch_event_id",
            "consumption_id",
            "dispatch_recorded_at",
            "semantic_request_sha256",
            "simulator_request_evidence_sha256",
        )
        self._require_summary(summary_type)
        if terminal:
            if (
                type(self.entry_possible) is not bool
                or type(self.reconciliation_required) is not bool
            ):
                _invalid(self)
            response = self.summary
            if type(response) is SimulatorResponseSummaryV1:
                if (
                    self.entry_possible
                    and response.outcome_kind is SimulatorOutcomeKind.NOT_SUBMITTED
                ):
                    _invalid(self)
                if (
                    not self.entry_possible
                    and response.outcome_kind is not SimulatorOutcomeKind.NOT_SUBMITTED
                ):
                    _invalid(self)
                if self.entry_possible and self.pre_dispatch_audit_receipt_id is None:
                    _invalid(self)
        else:
            self._require_absent(
                "pre_dispatch_audit_receipt_id",
                "entry_possible",
                "reconciliation_required",
            )

    def _validate_maintenance(self) -> None:
        self._require_present(*_EVENT_ID_FIELDS[self.event_kind])

    def _require_summary(self, summary_type: type[PrivateBoundaryModel]) -> None:
        _validate_summary_binding(self, summary_type)

    def _require_present(self, *names: str) -> None:
        if any(object.__getattribute__(self, name) is None for name in names):
            _invalid(self)

    def _require_absent(self, *names: str) -> None:
        if any(object.__getattribute__(self, name) is not None for name in names):
            _invalid(self)


class AuditStorageAllocation(PrivateBoundaryModel):
    """Repository-assigned immutable storage outputs for one producer record."""

    __module__ = _PUBLIC_MODULE
    id: NonzeroUuid
    sequence: PositiveBigInt
    audit_recorded_at: AuditUtc
    descriptor: AuditPayloadDescriptor | None
    closure_source: ApiAuditRecordV1 | None

    @model_validator(mode="after")
    def validate_allocation(self) -> Self:
        """Bind descriptor expiry to the assigned storage instant."""
        if self.descriptor is not None:
            descriptor = self.descriptor
            if descriptor.retention_mode is AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT:
                assert descriptor.payload_ttl_seconds is not None
                assert descriptor.payload_expires_at is not None
                expected = self.audit_recorded_at + timedelta(
                    seconds=descriptor.payload_ttl_seconds
                )
                if descriptor.payload_expires_at != expected:
                    _invalid(self)
        return self


__all__ = ["ApiAuditRecordV1", "AuditPayloadDescriptor", "AuditStorageAllocation"]
