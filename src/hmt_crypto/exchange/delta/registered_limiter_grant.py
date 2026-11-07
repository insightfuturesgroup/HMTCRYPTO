"""Callback-free helpers for one limiter-owned registered grant transaction."""

from __future__ import annotations

import asyncio
from uuid import UUID

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta import private_limiter_lifecycle as lifecycle
from hmt_crypto.exchange.delta.limiter_accounting import (
    _LimiterChargePlan,
    _PreallocatedQuotaEventIds,
)
from hmt_crypto.exchange.delta.private_limiter_completion import (
    LimiterReservationInvalidationReason,
    LimiterWaitBinding,
    LimiterWaitIntent,
    _PrivateMutationGrantRegistration,
    _RegisteredPrivateMutationAcquisition,
)
from hmt_crypto.exchange.delta.private_limiter_lifecycle_store import (
    _PrivateLimiterLifecycleStore,
)
from hmt_crypto.exchange.delta.private_limiter_owner import (
    _PrivateLimiterOwnerCapability,
    _require_private_limiter_owner,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.delta.rate_limit_policy import (
    RateLimitRoute,
    RatePermit,
    RateRequest,
)
from hmt_crypto.exchange.private_dispatch import (
    VenueDispatchAuditIdentity,
    _venue_dispatch_outcome_audit_identity,
    _venue_dispatch_outcome_entry_latch,
    _VenueDispatchOutcomeSlot,
)


def _error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "registered_limiter_grant"})


def _stage_registered_mutation(
    *,
    owner_capability: _PrivateLimiterOwnerCapability,
    limiter: object,
    store: _PrivateLimiterLifecycleStore,
    intent: LimiterWaitIntent,
    outcome_slot: _VenueDispatchOutcomeSlot,
    audit_identity: VenueDispatchAuditIdentity,
    registry_owner_id: UUID,
    registry_open_epoch: int,
    ownership_proof_id: UUID,
    process_id: int,
    process_incarnation_id: UUID,
    limiter_clock_epoch: int,
    accounting_generation: int,
    cooldown_epoch: int,
    identities: tuple[UUID, UUID, UUID, UUID, UUID],
) -> tuple[_PrivateMutationGrantRegistration, RateRequest]:
    try:
        gate = object.__getattribute__(limiter, "_accounting_gate")
    except AttributeError:
        raise _error() from None
    _require_private_limiter_owner(
        owner_capability,
        limiter=limiter,
        gate=gate,
        store=store,
        registry_owner_id=registry_owner_id,
    )
    matching_fields = (
        "account",
        "command_id",
        "order_id",
        "order_attempt_id",
        "root_event_id",
        "logical_call_id",
        "correlation_id",
        "order_attempt_number",
        "operation",
        "route_id",
        "policy_version",
        "semantic_request_sha256",
        "command_evidence_sha256",
    )
    if (
        type(intent) is not LimiterWaitIntent
        or type(audit_identity) is not VenueDispatchAuditIdentity
        or type(outcome_slot) is not _VenueDispatchOutcomeSlot
        or type(store) is not _PrivateLimiterLifecycleStore
        or type(registry_owner_id) is not UUID
        or registry_owner_id.int == 0
        or type(ownership_proof_id) is not UUID
        or ownership_proof_id.int == 0
        or type(process_incarnation_id) is not UUID
        or process_incarnation_id.int == 0
        or type(registry_open_epoch) is not int
        or registry_open_epoch <= 0
        or type(process_id) is not int
        or process_id <= 0
        or type(limiter_clock_epoch) is not int
        or limiter_clock_epoch <= 0
        or type(accounting_generation) is not int
        or accounting_generation <= 0
        or type(cooldown_epoch) is not int
        or cooldown_epoch < 0
        or type(identities) is not tuple
        or len(identities) != 5
        or any(type(identity) is not UUID or identity.int == 0 for identity in identities)
    ):
        raise _error()
    limiter_id = object.__getattribute__(limiter, "_limiter_instance_id")
    entry_latch = _venue_dispatch_outcome_entry_latch(outcome_slot)
    event_index = object.__getattribute__(limiter, "_event_index")
    audit_ids = (audit_identity.dispatch_event_id, audit_identity.consumption_id)
    if any(type(identity) is not UUID or identity.int == 0 for identity in audit_ids):
        raise _error()
    forbidden_ids = (
        registry_owner_id,
        ownership_proof_id,
        process_incarnation_id,
        limiter_id,
        *audit_ids,
    )
    if (
        type(limiter_id) is not UUID
        or limiter_id.int == 0
        or len(set(identities)) != 5
        or len(set(forbidden_ids)) != len(forbidden_ids)
        or any(identity in forbidden_ids for identity in identities)
        or _venue_dispatch_outcome_audit_identity(outcome_slot) is not audit_identity
        or any(identity in event_index for identity in (*identities, *forbidden_ids[-2:]))
        or any(getattr(audit_identity, name) != getattr(intent, name) for name in matching_fields)
    ):
        raise _error()
    store.assert_unbound(identities, audit_identity, outcome_slot, entry_latch)
    registration = object.__new__(_PrivateMutationGrantRegistration)
    fields = {
        "registration_id": identities[0],
        "registry_owner_id": registry_owner_id,
        "registry_open_epoch": registry_open_epoch,
        "ownership_proof_id": ownership_proof_id,
        "limiter_instance_id": limiter_id,
        "limiter_clock_epoch": limiter_clock_epoch,
        "limiter_accounting_generation": accounting_generation,
        "cooldown_epoch": cooldown_epoch,
        "process_id": process_id,
        "process_incarnation_id": process_incarnation_id,
        "intent": intent,
        "outcome_slot": outcome_slot,
        "held_reservation_id": identities[1],
        "token_id": identities[2],
        "rest_event_id": identities[3],
        "product_event_id": identities[4],
        "_limiter": limiter,
        "_gate": gate,
        "_store": store,
        "_owner_capability": owner_capability,
    }
    for name, value in fields.items():
        object.__setattr__(registration, name, value)
    route = (
        RateLimitRoute.PLACE_ORDER
        if intent.route_id is PrivateRouteId.PLACE_ORDER
        else RateLimitRoute.MODIFY_ORDER
    )
    return registration, RateRequest(route, {intent.product_id: 1})


def _registered_event_ids(
    limiter: object,
    registration: _PrivateMutationGrantRegistration,
    plan: _LimiterChargePlan,
) -> _PreallocatedQuotaEventIds:
    _validate_registration(limiter, registration)
    products = plan.product_operations
    if len(products) != 1 or products[0][0] != registration.intent.product_id:
        raise _error()
    return _PreallocatedQuotaEventIds(
        rest_event_id=registration.rest_event_id,
        product_event_ids=((products[0][0], registration.product_event_id),),
    )


def _validate_registered_grant_for_charge_locked(
    limiter: object,
    registration: _PrivateMutationGrantRegistration,
    plan: _LimiterChargePlan,
    event_ids: _PreallocatedQuotaEventIds,
) -> None:
    _validate_registered_grant_before_scheduler(limiter, registration)
    gate = object.__getattribute__(limiter, "_accounting_gate")
    gate.assert_held()
    products = plan.product_operations
    expected_ids = _PreallocatedQuotaEventIds(
        rest_event_id=registration.rest_event_id,
        product_event_ids=((registration.intent.product_id, registration.product_event_id),),
    )
    event_index = object.__getattribute__(limiter, "_event_index")
    store = object.__getattribute__(limiter, "_private_lifecycle_store")
    if (
        type(event_ids) is not _PreallocatedQuotaEventIds
        or event_ids != expected_ids
        or len(products) != 1
        or products[0] != (registration.intent.product_id, 1)
        or store._state(registration).registration_state != "PREPARED"
        or registration.limiter_clock_epoch
        != object.__getattribute__(limiter, "_limiter_clock_epoch")
        or registration.limiter_accounting_generation
        != object.__getattribute__(limiter, "_limiter_accounting_generation")
        or registration.cooldown_epoch != object.__getattribute__(limiter, "_cooldown_epoch")
        or registration.rest_event_id in event_index
        or registration.product_event_id in event_index
    ):
        raise _error()


def _validate_registered_grant_before_scheduler(
    limiter: object,
    registration: _PrivateMutationGrantRegistration,
) -> None:
    _validate_registration(limiter, registration)
    store = object.__getattribute__(limiter, "_private_lifecycle_store")
    event_index = object.__getattribute__(limiter, "_event_index")
    audit = _venue_dispatch_outcome_audit_identity(registration.outcome_slot)
    audit_ids = (audit.dispatch_event_id, audit.consumption_id)
    if any(type(identity) is not UUID or identity.int == 0 for identity in audit_ids):
        raise _error()
    identities = (
        registration.registration_id,
        registration.held_reservation_id,
        registration.token_id,
        registration.rest_event_id,
        registration.product_event_id,
        *audit_ids,
    )
    if (
        store._state(registration).registration_state != "PREPARED"
        or registration.limiter_clock_epoch
        != object.__getattribute__(limiter, "_limiter_clock_epoch")
        or registration.limiter_accounting_generation
        != object.__getattribute__(limiter, "_limiter_accounting_generation")
        or registration.cooldown_epoch != object.__getattribute__(limiter, "_cooldown_epoch")
        or any(identity in event_index for identity in identities)
    ):
        raise _error()


_validate_charge_locked = _validate_registered_grant_for_charge_locked


def _publish_registered_grant_locked(
    limiter: object,
    registration: _PrivateMutationGrantRegistration,
    permit: RatePermit,
) -> _RegisteredPrivateMutationAcquisition:
    _validate_registration(limiter, registration)
    object.__getattribute__(limiter, "_accounting_gate").assert_held()
    try:
        return _complete_registered_grant_locked(limiter, registration, permit)
    except BaseException as error:
        reason = (
            LimiterReservationInvalidationReason.CANCELLED
            if type(error) is asyncio.CancelledError
            else LimiterReservationInvalidationReason.INTERNAL_EXCEPTION
        )
        registration._store.invalidate(
            registration,
            reason,
        )
        raise


def _complete_registered_grant_locked(
    limiter: object,
    registration: _PrivateMutationGrantRegistration,
    permit: RatePermit,
) -> _RegisteredPrivateMutationAcquisition:
    _validate_registration(limiter, registration)
    gate = object.__getattribute__(limiter, "_accounting_gate")
    gate.assert_held()
    accounting = permit.accounting
    if accounting is None:
        raise _error()
    intent = registration.intent
    product_id = intent.product_id
    if (
        accounting.limiter_instance_id != registration.limiter_instance_id
        or accounting.limiter_clock_epoch != registration.limiter_clock_epoch
        or accounting.limiter_accounting_generation != registration.limiter_accounting_generation
        or accounting.cooldown_epoch != registration.cooldown_epoch
        or accounting.rest_event_id != registration.rest_event_id
        or accounting.product_event_ids != {product_id: registration.product_event_id}
    ):
        raise _error()
    event_index = object.__getattribute__(limiter, "_event_index")
    rest = event_index.get(registration.rest_event_id)
    product = event_index.get(registration.product_event_id)
    if (
        rest is None
        or product is None
        or rest.product_id is not None
        or product.product_id != product_id
    ):
        raise _error()
    binding = LimiterWaitBinding(
        **intent.model_dump(),
        registry_owner_id=registration.registry_owner_id,
        limiter_instance_id=registration.limiter_instance_id,
        held_reservation_id=registration.held_reservation_id,
        limiter_clock_epoch=accounting.limiter_clock_epoch,
        limiter_accounting_generation=accounting.limiter_accounting_generation,
        rest_window_id=accounting.rest_event_id,
        rest_window_end_monotonic_ns=accounting.rest_window_end_monotonic_ns,
        product_window_id=accounting.product_event_ids[product_id],
        product_window_end_monotonic_ns=accounting.product_window_end_monotonic_ns[product_id],
        cooldown_epoch=accounting.cooldown_epoch,
    )
    return registration._store.publish(
        registration,
        binding,
        rest_event=rest.event,
        product_event=product.event,
    )


def _invalidate_registered_grant_locked(
    limiter: object,
    registration: _PrivateMutationGrantRegistration,
    reason: LimiterReservationInvalidationReason | BaseException,
) -> None:
    _validate_registration(limiter, registration)
    object.__getattribute__(limiter, "_accounting_gate").assert_held()
    if type(reason) is asyncio.CancelledError:
        selected = LimiterReservationInvalidationReason.CANCELLED
    elif isinstance(reason, BaseException):
        selected = LimiterReservationInvalidationReason.INTERNAL_EXCEPTION
    else:
        selected = reason
    registration._store.invalidate(registration, selected)


def _invalidate_all_registered_grants_locked(
    limiter: object,
    reason: LimiterReservationInvalidationReason,
) -> None:
    gate = object.__getattribute__(limiter, "_accounting_gate")
    gate.assert_held()
    store = object.__getattribute__(limiter, "_private_lifecycle_store")
    if store is not None:
        store.invalidate_all(reason)


def _invalidate_expiring_registered_grants_locked(limiter: object, now_ns: int) -> None:
    gate = object.__getattribute__(limiter, "_accounting_gate")
    gate.assert_held()
    store = object.__getattribute__(limiter, "_private_lifecycle_store")
    if store is None:
        return
    store.invalidate_expiring(now_ns)


def _validate_registration(
    limiter: object,
    registration: _PrivateMutationGrantRegistration,
) -> None:
    if type(registration) is not _PrivateMutationGrantRegistration:
        raise _error()
    try:
        owned = (
            object.__getattribute__(registration, "_limiter"),
            object.__getattribute__(registration, "_gate"),
            object.__getattribute__(registration, "_store"),
            object.__getattribute__(registration, "_owner_capability"),
            object.__getattribute__(registration, "limiter_instance_id"),
        )
        limiter_gate = object.__getattribute__(limiter, "_accounting_gate")
        limiter_store = object.__getattribute__(limiter, "_private_lifecycle_store")
        limiter_id = object.__getattribute__(limiter, "_limiter_instance_id")
        limiter_capability = object.__getattribute__(limiter, "_private_lifecycle_capability")
        outcome_slot = registration.outcome_slot
        audit = _venue_dispatch_outcome_audit_identity(outcome_slot)
        _venue_dispatch_outcome_entry_latch(outcome_slot)
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
            audit.dispatch_event_id,
            audit.consumption_id,
        )
        positive_epochs = (
            registration.registry_open_epoch,
            registration.limiter_clock_epoch,
            registration.limiter_accounting_generation,
            registration.process_id,
        )
        cooldown_epoch = registration.cooldown_epoch
        intent = registration.intent
    except (AttributeError, TypeError):
        raise _error() from None
    if (
        owned[0] is not limiter
        or owned[1] is not limiter_gate
        or owned[2] is not limiter_store
        or owned[3] is not limiter_capability
        or any(type(identity) is not UUID or identity.int == 0 for identity in registration_ids)
        or len(set(registration_ids)) != len(registration_ids)
        or any(type(epoch) is not int or epoch <= 0 for epoch in positive_epochs)
        or type(cooldown_epoch) is not int
        or cooldown_epoch < 0
        or type(intent) is not LimiterWaitIntent
        or lifecycle._intent_key(intent) is None
        or type(outcome_slot) is not _VenueDispatchOutcomeSlot
        or type(limiter_id) is not UUID
        or type(owned[4]) is not UUID
        or owned[4] != limiter_id
    ):
        raise _error()
    _require_private_limiter_owner(
        owned[3],
        limiter=limiter,
        gate=limiter_gate,
        store=limiter_store,
        registry_owner_id=registration.registry_owner_id,
    )


__all__: list[str] = []
