"""Pure validation helpers for registry-owned private limiter transitions."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.private_limiter_completion import (
    LimiterReservationInvalidationReason,
    LimiterWaitBinding,
    LimiterWaitCompleted,
    LimiterWaitIntent,
    _audit_matches_intent,
    _ConsumedLimiterWaitEvidence,
    _HeldLimiterReservation,
    _PrivateMutationGrantRegistration,
    _RegisteredPrivateMutationAcquisition,
    _VenueLimiterReservationHandle,
)
from hmt_crypto.exchange.delta.private_limiter_owner import (
    _private_limiter_state_objects_match,
    _PrivateLimiterOwnerCapability,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitScope, ScopeKind
from hmt_crypto.exchange.private_dispatch import (
    TrustedDispatchDeadline,
    TrustedDispatchInstant,
    VenueNoEntryReason,
    _venue_dispatch_entry_latch_owner,
    _venue_dispatch_outcome_audit_identity,
    _VenueDispatchEntryLatch,
    _VenueDispatchOutcomeSlot,
)
from hmt_crypto.worker.clock import Task210TrustedDispatchClock

_VENUE_NO_ENTRY_REASONS = (
    "SINK_BINDING_MISMATCH",
    "PREPARATION_FAILED",
    "AUDIT_FAILED",
    "CURRENT_INVALIDATED",
    "DEADLINE_EXPIRED",
    "SIGNING_FAILED",
    "SIGNATURE_EXPIRED",
    "LIMITER_INVALIDATED",
    "CANCELLED",
    "INTERNAL_EXCEPTION",
)
_CURRENT_INVALIDATIONS = frozenset(
    {
        LimiterReservationInvalidationReason.FORK_OR_PROCESS_MISMATCH,
        LimiterReservationInvalidationReason.LIMITER_CLOCK_INVALIDATED,
        LimiterReservationInvalidationReason.POST_COMMIT_CURRENTNESS_FAILED,
    }
)
_SINK_INVALIDATIONS = frozenset(
    {
        LimiterReservationInvalidationReason.VENUE_TRANSFER_TARGET_MISMATCH,
        LimiterReservationInvalidationReason.VENUE_TRANSFER_BINDING_MISMATCH,
        LimiterReservationInvalidationReason.VENUE_TRANSFER_REUSED,
        LimiterReservationInvalidationReason.HANDOFF_ATTACHMENT_FAILED,
    }
)


def _venue_reason(reason: LimiterReservationInvalidationReason) -> VenueNoEntryReason:
    if reason in _CURRENT_INVALIDATIONS:
        return "CURRENT_INVALIDATED"
    if reason in _SINK_INVALIDATIONS:
        return "SINK_BINDING_MISMATCH"
    if reason is LimiterReservationInvalidationReason.CANCELLED:
        return "CANCELLED"
    if reason is LimiterReservationInvalidationReason.INTERNAL_EXCEPTION:
        return "INTERNAL_EXCEPTION"
    return "LIMITER_INVALIDATED"


def _is_exact_venue_target(
    region: object,
    environment: object,
    mode: object,
) -> bool:
    return (
        region is DeltaRegion.INDIA
        and environment is DeltaEnvironment.TESTNET
        and mode is ExecutionMode.LIVE
    )


def _is_allowed_transfer_rejection(reason: object) -> bool:
    return (
        reason is None
        or reason is LimiterReservationInvalidationReason.VENUE_TRANSFER_TARGET_MISMATCH
        or reason is LimiterReservationInvalidationReason.VENUE_TRANSFER_BINDING_MISMATCH
    )


def _transfer_rejection_reason(
    reason: object,
    *,
    transferred: bool,
) -> LimiterReservationInvalidationReason | None:
    if type(transferred) is not bool or not _is_allowed_transfer_rejection(reason):
        raise ConfigurationError(context={"operation": "private_limiter_lifecycle"})
    if transferred and reason is None:
        return LimiterReservationInvalidationReason.VENUE_TRANSFER_REUSED
    return cast("LimiterReservationInvalidationReason | None", reason)


def _is_allowed_consumed_invalidation(reason: object) -> bool:
    return reason in (
        LimiterReservationInvalidationReason.DURABLE_REPLAY_NO_SEND,
        LimiterReservationInvalidationReason.DATABASE_FAILURE,
        LimiterReservationInvalidationReason.COMMIT_UNCERTAIN,
        LimiterReservationInvalidationReason.CANCELLED,
        LimiterReservationInvalidationReason.POST_COMMIT_CURRENTNESS_FAILED,
        LimiterReservationInvalidationReason.DEADLINE_DERIVATION_FAILED,
        LimiterReservationInvalidationReason.HANDOFF_ATTACHMENT_FAILED,
        LimiterReservationInvalidationReason.INTERNAL_EXCEPTION,
    )


def _venue_invalidation_reason(
    reason: VenueNoEntryReason,
) -> LimiterReservationInvalidationReason:
    mapping: dict[VenueNoEntryReason, LimiterReservationInvalidationReason] = {
        "SINK_BINDING_MISMATCH": (
            LimiterReservationInvalidationReason.VENUE_TRANSFER_BINDING_MISMATCH
        ),
        "CURRENT_INVALIDATED": (
            LimiterReservationInvalidationReason.POST_COMMIT_CURRENTNESS_FAILED
        ),
        "DEADLINE_EXPIRED": LimiterReservationInvalidationReason.DEADLINE_DERIVATION_FAILED,
        "CANCELLED": LimiterReservationInvalidationReason.CANCELLED,
        "INTERNAL_EXCEPTION": LimiterReservationInvalidationReason.INTERNAL_EXCEPTION,
    }
    if not _is_allowed_venue_no_entry_reason(reason):
        raise ConfigurationError(context={"operation": "private_limiter_lifecycle"})
    return mapping.get(reason, LimiterReservationInvalidationReason.INTERNAL_EXCEPTION)


def _is_allowed_venue_no_entry_reason(reason: object) -> bool:
    return type(reason) is str and any(
        str.__eq__(reason, candidate) for candidate in _VENUE_NO_ENTRY_REASONS
    )


def _is_current_entry_time(
    *,
    post: TrustedDispatchInstant,
    signed_at: TrustedDispatchInstant,
    deadline: TrustedDispatchDeadline,
    signed_timestamp: int,
    process_id: int,
    process_incarnation_id: object,
) -> bool:
    return (
        post.process_id == process_id
        and deadline.process_id == signed_at.process_id == process_id
        and post.process_incarnation_id == process_incarnation_id
        and deadline.process_incarnation_id
        == signed_at.process_incarnation_id
        == process_incarnation_id
        and post.clock_epoch == deadline.clock_epoch == signed_at.clock_epoch
        and post.correlation_epoch == deadline.correlation_epoch == signed_at.correlation_epoch
        and signed_at.utc_epoch_seconds == signed_timestamp
        and signed_at.monotonic_ns <= post.monotonic_ns
        and post.monotonic_ns - signed_at.monotonic_ns < 5_000_000_000
        and signed_timestamp <= post.utc_epoch_seconds <= signed_timestamp + 4
        and signed_at.utc_upper_bound_ns <= post.utc_upper_bound_ns
        and post.monotonic_ns < deadline.monotonic_ns
        and post.utc_upper_bound_ns < (signed_timestamp + 5) * 1_000_000_000
    )


def _is_bound_entry_evidence(
    *,
    returned_clock: object,
    clock: object,
    signed_at: TrustedDispatchInstant,
    deadline: TrustedDispatchDeadline,
    process_id: int,
    process_incarnation_id: object,
) -> bool:
    return (
        type(clock) is Task210TrustedDispatchClock
        and returned_clock is clock
        and clock.process_id == process_id
        and clock.process_incarnation_id == process_incarnation_id
        and signed_at.clock_epoch == deadline.clock_epoch == clock.clock_epoch
        and signed_at.correlation_epoch == deadline.correlation_epoch == clock.correlation_epoch
        and signed_at.process_id == deadline.process_id == process_id
        and signed_at.process_incarnation_id
        == deadline.process_incarnation_id
        == process_incarnation_id
    )


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _has_valid_phase_evidence(
    phase: object,
    wire_evidence: object,
    audit_receipt_id: object,
) -> bool:
    issued = type(phase) is str and str.__eq__(phase, "ISSUED")
    claimed = type(phase) is str and str.__eq__(phase, "CLAIMED_FOR_UNSIGNED_PREPARATION")
    audited = type(phase) is str and str.__eq__(phase, "AUDIT_RECORDED")
    wire_ok = wire_evidence is None or _is_sha256(wire_evidence)
    receipt_ok = audit_receipt_id is None or (
        type(audit_receipt_id) is UUID and audit_receipt_id.int != 0
    )
    return bool(
        wire_ok
        and receipt_ok
        and (
            (issued and wire_evidence is None and audit_receipt_id is None)
            or (claimed and audit_receipt_id is None)
            or (audited and wire_evidence is not None and audit_receipt_id is not None)
        )
    )


def _is_audited_phase_evidence(
    phase: object,
    wire_evidence: object,
    audit_receipt_id: object,
) -> bool:
    return (
        type(phase) is str
        and str.__eq__(phase, "AUDIT_RECORDED")
        and _has_valid_phase_evidence(phase, wire_evidence, audit_receipt_id)
    )


def _is_allowed_phase_transition(
    *,
    current_phase: object,
    current_wire_evidence: object,
    next_phase: object,
    next_wire_evidence: object,
    next_audit_receipt_id: object,
) -> bool:
    if not _has_valid_phase_evidence(
        current_phase, current_wire_evidence, None
    ) or not _has_valid_phase_evidence(next_phase, next_wire_evidence, next_audit_receipt_id):
        return False
    claim = (
        current_phase == "ISSUED"
        and next_phase == "CLAIMED_FOR_UNSIGNED_PREPARATION"
        and next_audit_receipt_id is None
    )
    record_wire = (
        current_phase == next_phase == "CLAIMED_FOR_UNSIGNED_PREPARATION"
        and current_wire_evidence is None
        and _is_sha256(next_wire_evidence)
        and next_audit_receipt_id is None
    )
    record_audit = (
        current_phase == "CLAIMED_FOR_UNSIGNED_PREPARATION"
        and next_phase == "AUDIT_RECORDED"
        and _is_sha256(current_wire_evidence)
        and _is_sha256(next_wire_evidence)
        and next_wire_evidence == current_wire_evidence
        and type(next_audit_receipt_id) is UUID
        and next_audit_receipt_id.int != 0
    )
    return bool(claim or record_wire or record_audit)


def _account_key(value: object) -> tuple[object, ...] | None:
    if type(value) is not AccountRef:
        return None
    parts = (
        value.account_id,
        value.external_user_id,
        value.region,
        value.environment,
        value.mode,
        value.credential_version,
    )
    if (
        type(parts[0]) is not UUID
        or parts[0].int == 0
        or type(parts[1]) is not str
        or parts[2] is not DeltaRegion.INDIA
        or parts[3] is not DeltaEnvironment.TESTNET
        or parts[4] is not ExecutionMode.LIVE
        or type(parts[5]) is not int
        or parts[5] <= 0
    ):
        return None
    return parts


def _intent_key(value: object) -> tuple[object, ...] | None:
    if type(value) not in (LimiterWaitIntent, LimiterWaitBinding):
        return None
    value = cast("LimiterWaitIntent | LimiterWaitBinding", value)
    fields = {name: object.__getattribute__(value, name) for name in LimiterWaitIntent.model_fields}
    ids: tuple[object, ...] = tuple(
        fields[name]
        for name in (
            "command_id",
            "order_id",
            "order_attempt_id",
            "root_event_id",
            "logical_call_id",
            "correlation_id",
            "process_incarnation_id",
        )
    )
    attempt_number = fields["order_attempt_number"]
    operation = fields["operation"]
    route_id = fields["route_id"]
    product_id = fields["product_id"]
    rest_weight = fields["rest_weight"]
    product_operations = fields["product_operations"]
    policy_version = fields["policy_version"]
    semantic_sha = fields["semantic_request_sha256"]
    command_sha = fields["command_evidence_sha256"]
    process_id = fields["process_id"]
    if (
        any(type(identity) is not UUID or identity.int == 0 for identity in ids)
        or type(attempt_number) is not int
        or attempt_number <= 0
        or type(operation) is not str
        or operation not in ("PLACE_ORDER", "MODIFY_ORDER")
        or route_id not in (PrivateRouteId.PLACE_ORDER, PrivateRouteId.MODIFY_ORDER)
        or type(product_id) is not int
        or product_id <= 0
        or type(rest_weight) is not int
        or rest_weight != 5
        or type(product_operations) is not int
        or product_operations != 1
        or type(policy_version) is not str
        or not _is_sha256(semantic_sha)
        or not _is_sha256(command_sha)
        or type(process_id) is not int
        or process_id <= 0
    ):
        return None
    return (
        _account_key(fields["account"]),
        *ids,
        attempt_number,
        operation,
        route_id,
        product_id,
        rest_weight,
        product_operations,
        policy_version,
        semantic_sha,
        command_sha,
        process_id,
    )


def _binding_key(value: object) -> tuple[object, ...] | None:
    if type(value) is not LimiterWaitBinding:
        return None
    ids = (
        value.registry_owner_id,
        value.limiter_instance_id,
        value.held_reservation_id,
        value.rest_window_id,
        value.product_window_id,
    )
    epochs = (
        value.limiter_clock_epoch,
        value.limiter_accounting_generation,
        value.rest_window_end_monotonic_ns,
        value.product_window_end_monotonic_ns,
        value.cooldown_epoch,
    )
    if (
        any(type(identity) is not UUID or identity.int == 0 for identity in ids)
        or any(type(epoch) is not int or epoch < 0 for epoch in epochs)
        or any(epoch <= 0 for epoch in epochs[:4])
    ):
        return None
    intent = _intent_key(value)
    return None if intent is None else (*intent, *ids, *epochs)


def _bindings_match(left: object, right: object) -> bool:
    left_key = _binding_key(left)
    return left_key is not None and left_key == _binding_key(right)


def _canonical_binding(value: object) -> LimiterWaitBinding | None:
    if type(value) is not LimiterWaitBinding:
        return None
    try:
        return LimiterWaitBinding.model_validate(value.model_dump(), strict=True)
    except Exception:
        return None


def _has_exact_owner_state(
    *,
    registry: object,
    limiter: object,
    store: object,
    state: object,
    capability: object,
) -> bool:
    try:
        registration = object.__getattribute__(state, "registration")
        binding = object.__getattribute__(state, "binding")
        token = object.__getattribute__(state, "token")
        reservation = object.__getattribute__(state, "held_reservation")
        acquisition = object.__getattribute__(state, "acquisition")
        evidence = object.__getattribute__(state, "evidence")
        handle = object.__getattribute__(state, "handle")
        audit = object.__getattribute__(state, "audit_identity")
        owner = object.__getattribute__(state, "latch_owner")
        identity_keys = object.__getattribute__(state, "identity_keys")
        object_keys = object.__getattribute__(state, "object_keys")
        owned_objects = object.__getattribute__(state, "owned_objects")
        issued_objects = object.__getattribute__(state, "issued_objects")
        owned_receipt = object.__getattribute__(state, "owned_audit_receipt_id")
        receipt = object.__getattribute__(state, "pre_dispatch_audit_receipt_id")
        slot = registration.outcome_slot
        latch = object.__getattribute__(owner, "_latch")
    except (AttributeError, TypeError):
        return False
    exact_types = (
        (capability, _PrivateLimiterOwnerCapability),
        (registration, _PrivateMutationGrantRegistration),
        (binding, LimiterWaitBinding),
        (token, LimiterWaitCompleted),
        (reservation, _HeldLimiterReservation),
        (acquisition, _RegisteredPrivateMutationAcquisition),
        (slot, _VenueDispatchOutcomeSlot),
        (latch, _VenueDispatchEntryLatch),
    )
    if (
        any(type(value) is not expected for value, expected in exact_types)
        or type(identity_keys) is not tuple
        or len(identity_keys) != 7
        or any(type(identity) is not UUID or identity.int == 0 for identity in identity_keys)
        or type(object_keys) is not tuple
        or len(object_keys) != 2
        or any(type(identity) is not int or identity <= 0 for identity in object_keys)
        or type(owned_objects) is not tuple
        or len(owned_objects) != 3
        or type(issued_objects) is not tuple
        or len(issued_objects) != 3
    ):
        return False
    registration_id, reservation_id, token_id, rest_id, product_id, dispatch_id, consumption_id = (
        identity_keys
    )
    owned_slot, owned_latch, owned_audit = owned_objects
    owned_token, owned_reservation, owned_acquisition = issued_objects
    try:
        slot_audit = _venue_dispatch_outcome_audit_identity(slot)
        latch_slot = _venue_dispatch_entry_latch_owner(latch)
    except TypeError:
        return False
    return bool(
        _private_limiter_state_objects_match(
            cast(_PrivateLimiterOwnerCapability, capability),
            limiter=limiter,
            store=store,
            state=state,
            identity_keys=identity_keys,
            object_keys=object_keys,
        )
        and registration.registration_id is registration_id
        and registration.held_reservation_id is reservation_id
        and registration.token_id is token_id
        and registration.rest_event_id is rest_id
        and registration.product_event_id is product_id
        and registration.outcome_slot is owned_slot is slot
        and token is owned_token
        and reservation is owned_reservation
        and acquisition is owned_acquisition
        and audit is owned_audit
        and latch is owned_latch
        and receipt is owned_receipt
        and (receipt is None or object.__getattribute__(store, "_identities").get(receipt) is state)
        and object_keys == (id(slot), id(latch))
        and slot_audit is audit
        and latch_slot is slot
        and object.__getattribute__(owner, "_outcome_slot") is slot
        and _audit_matches_intent(audit, registration.intent)
        and audit.dispatch_event_id is dispatch_id
        and audit.consumption_id is consumption_id
        and (evidence is None or type(evidence) is _ConsumedLimiterWaitEvidence)
        and (handle is None or type(handle) is _VenueLimiterReservationHandle)
    )


def _has_frozen_currentness_parity(
    *,
    registration: object,
    binding: object,
    registry_owner_id: object,
    registry_open_epoch: object,
    ownership_proof_id: object,
    limiter_instance_id: object,
    limiter_clock_epoch: object,
    accounting_generation: object,
    cooldown_epoch: object,
    scope: object,
    policy_version: object,
    process_id: object,
    process_incarnation_id: object,
) -> bool:
    if (
        type(registration) is not _PrivateMutationGrantRegistration
        or type(binding) is not LimiterWaitBinding
        or type(scope) is not RateLimitScope
        or scope.kind is not ScopeKind.EXTERNAL_USER
        or type(scope.region) is not str
        or type(scope.environment) is not str
        or type(scope.external_id) is not str
        or type(limiter_clock_epoch) is not int
        or type(accounting_generation) is not int
        or type(cooldown_epoch) is not int
    ):
        return False
    registration_ids = (
        registration.registration_id,
        registration.registry_owner_id,
        registration.ownership_proof_id,
        registration.limiter_instance_id,
        registration.process_incarnation_id,
        registration.held_reservation_id,
        registration.token_id,
        registration.rest_event_id,
        registration.product_event_id,
    )
    if (
        any(type(identity) is not UUID or identity.int == 0 for identity in registration_ids)
        or type(registration.registry_open_epoch) is not int
        or registration.registry_open_epoch <= 0
        or type(registration.process_id) is not int
        or registration.process_id <= 0
        or type(registration.limiter_clock_epoch) is not int
        or type(registration.limiter_accounting_generation) is not int
        or type(registration.cooldown_epoch) is not int
    ):
        return False
    binding_key = _binding_key(binding)
    intent_key = _intent_key(registration.intent)
    return (
        binding_key is not None
        and intent_key is not None
        and _intent_key(binding) == intent_key
        and type(registry_owner_id) is UUID
        and registration.registry_owner_id == binding.registry_owner_id == registry_owner_id
        and type(registry_open_epoch) is int
        and registration.registry_open_epoch == registry_open_epoch
        and type(ownership_proof_id) is UUID
        and registration.ownership_proof_id == ownership_proof_id
        and type(limiter_instance_id) is UUID
        and registration.limiter_instance_id == binding.limiter_instance_id == limiter_instance_id
        and registration.held_reservation_id == binding.held_reservation_id
        and registration.rest_event_id == binding.rest_window_id
        and registration.product_event_id == binding.product_window_id
        and registration.limiter_clock_epoch == binding.limiter_clock_epoch == limiter_clock_epoch
        and registration.limiter_accounting_generation
        == binding.limiter_accounting_generation
        == accounting_generation
        and registration.cooldown_epoch == binding.cooldown_epoch == cooldown_epoch
        and type(process_id) is int
        and registration.process_id == binding.process_id == process_id
        and type(process_incarnation_id) is UUID
        and registration.process_incarnation_id
        == binding.process_incarnation_id
        == process_incarnation_id
        and type(policy_version) is str
        and registration.intent.policy_version == binding.policy_version == policy_version
        and scope.region == "india"
        and scope.environment == "testnet"
        and scope.external_id == binding.account.external_user_id
    )


__all__: list[str] = []
