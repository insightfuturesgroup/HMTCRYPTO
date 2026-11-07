"""Sole registry-owned store for private limiter grant lifecycle records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from hmt_crypto.domain.private_primitives import Sha256Hex
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange import private_dispatch as dispatch
from hmt_crypto.exchange.delta import private_limiter_completion as grant
from hmt_crypto.exchange.delta import private_limiter_entry as entry
from hmt_crypto.exchange.delta import private_limiter_lifecycle as lifecycle
from hmt_crypto.exchange.delta import private_limiter_owner as own

if TYPE_CHECKING:
    from hmt_crypto.exchange.delta.limiter_accounting import _LimiterAccountingGate


@dataclass(slots=True)
class _GrantState:
    registration: grant._PrivateMutationGrantRegistration
    audit_identity: dispatch.VenueDispatchAuditIdentity
    latch_owner: entry._OwnedVenueEntryLatch
    identity_keys: tuple[UUID, UUID, UUID, UUID, UUID, UUID, UUID]
    object_keys: tuple[int, int]
    owned_objects: tuple[
        dispatch._VenueDispatchOutcomeSlot, dispatch._VenueDispatchEntryLatch, object
    ]
    issued_objects: tuple[object, object, object] | None = None
    owned_audit_receipt_id: UUID | None = None
    registration_state: str = "PREPARED"
    token_state: str = "UNISSUED"
    reservation_state: str = "UNISSUED"
    binding: grant.LimiterWaitBinding | None = None
    token: grant.LimiterWaitCompleted | None = None
    held_reservation: grant._HeldLimiterReservation | None = None
    acquisition: grant._RegisteredPrivateMutationAcquisition | None = None
    evidence: grant._ConsumedLimiterWaitEvidence | None = None
    handle: grant._VenueLimiterReservationHandle | None = None
    disposition: dispatch._VenueNoEntryDisposition | None = None
    entered: dispatch._EnteredVenueDispatch | None = None
    terminal_reason: grant.LimiterReservationInvalidationReason | None = None
    phase: dispatch.VenueDispatchPreEntryPhase = "ISSUED"
    wire_request_evidence_sha256: Sha256Hex | None = None
    pre_dispatch_audit_receipt_id: UUID | None = None
    entry_clock: object | None = None
    entry_clock_currentness: object | None = None
    rest_event: object | None = None
    product_event: object | None = None


def _error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "private_limiter_lifecycle"})


class _PrivateLimiterLifecycleStore:
    """Strong owner of registrations, tokens, reservations, latches, and tombstones."""

    __slots__ = (
        "_gate",
        "_grants",
        "_handles",
        "_identities",
        "_latches",
        "_owner_capability",
        "_outcomes",
        "_reservations",
        "_tokens",
    )

    def __init__(self, *, owner_capability: own._PrivateLimiterOwnerCapability) -> None:
        self._owner_capability = owner_capability
        self._gate = cast(
            "_LimiterAccountingGate", own._bind_private_limiter_store(owner_capability, self)
        )
        self._grants: dict[UUID, _GrantState] = {}
        self._identities: dict[UUID, _GrantState] = {}
        self._tokens: dict[UUID, _GrantState] = {}
        self._reservations: dict[UUID, _GrantState] = {}
        self._handles: dict[int, _GrantState] = {}
        self._latches: dict[int, _GrantState] = {}
        self._outcomes: dict[int, _GrantState] = {}

    def assert_unbound(
        self,
        identities: tuple[UUID, UUID, UUID, UUID, UUID],
        audit_identity: dispatch.VenueDispatchAuditIdentity,
        outcome_slot: dispatch._VenueDispatchOutcomeSlot,
        entry_latch: dispatch._VenueDispatchEntryLatch,
    ) -> None:
        if (
            type(identities) is not tuple
            or len(identities) != 5
            or any(type(identity) is not UUID or identity.int == 0 for identity in identities)
            or type(audit_identity) is not dispatch.VenueDispatchAuditIdentity
            or type(outcome_slot) is not dispatch._VenueDispatchOutcomeSlot
            or type(entry_latch) is not dispatch._VenueDispatchEntryLatch
        ):
            raise _error()
        owned = (*identities, audit_identity.dispatch_event_id, audit_identity.consumption_id)
        if (
            len(set(owned)) != len(owned)
            or any(identity in self._identities for identity in owned)
            or id(outcome_slot) in self._outcomes
            or id(entry_latch) in self._latches
        ):
            raise _error()

    def prepare(
        self,
        registration: grant._PrivateMutationGrantRegistration,
        audit_identity: dispatch.VenueDispatchAuditIdentity,
        entry_latch: dispatch._VenueDispatchEntryLatch,
    ) -> None:
        self._gate.assert_held()
        if (
            type(registration) is not grant._PrivateMutationGrantRegistration
            or object.__getattribute__(registration, "_owner_capability")
            is not self._owner_capability
            or type(audit_identity) is not dispatch.VenueDispatchAuditIdentity
            or type(entry_latch) is not dispatch._VenueDispatchEntryLatch
        ):
            raise _error()
        identities = (
            registration.registration_id,
            registration.held_reservation_id,
            registration.token_id,
            registration.rest_event_id,
            registration.product_event_id,
        )
        self.assert_unbound(identities, audit_identity, registration.outcome_slot, entry_latch)
        owner = entry._bind_owned_latch(self._gate, entry_latch, registration.outcome_slot)
        owned = (*identities, audit_identity.dispatch_event_id, audit_identity.consumption_id)
        state = _GrantState(
            registration,
            audit_identity,
            owner,
            owned,
            (id(registration.outcome_slot), id(entry_latch)),
            (registration.outcome_slot, entry_latch, audit_identity),
        )
        self._grants[registration.registration_id] = state
        self._tokens[registration.token_id] = state
        self._reservations[registration.held_reservation_id] = state
        self._outcomes[id(registration.outcome_slot)] = state
        self._latches[id(entry_latch)] = state
        for identity in (
            *identities,
            audit_identity.dispatch_event_id,
            audit_identity.consumption_id,
        ):
            self._identities[identity] = state

    def publish(
        self,
        registration: grant._PrivateMutationGrantRegistration,
        binding: grant.LimiterWaitBinding,
        *,
        rest_event: object,
        product_event: object,
    ) -> grant._RegisteredPrivateMutationAcquisition:
        self._gate.assert_held()
        state = self._state(registration)
        if (
            state.registration_state != "PREPARED"
            or type(binding) is not grant.LimiterWaitBinding
            or state.token is not None
            or state.held_reservation is not None
            or state.acquisition is not None
            or state.disposition is not None
            or rest_event is None
            or product_event is None
        ):
            raise _error()
        token = object.__new__(grant.LimiterWaitCompleted)
        object.__setattr__(token, "_grant", state)
        object.__setattr__(token, "_token_id", registration.token_id)
        reservation = object.__new__(grant._HeldLimiterReservation)
        object.__setattr__(reservation, "_grant", state)
        object.__setattr__(reservation, "_reservation_id", registration.held_reservation_id)
        acquisition = object.__new__(grant._RegisteredPrivateMutationAcquisition)
        object.__setattr__(acquisition, "_binding", binding)
        object.__setattr__(acquisition, "_token", token)
        state.binding, state.token = binding, token
        state.held_reservation, state.acquisition = reservation, acquisition
        state.issued_objects = (token, reservation, acquisition)
        state.rest_event, state.product_event = rest_event, product_event
        state.registration_state = "PUBLISHED"
        state.token_state, state.reservation_state = "ISSUED", "HELD"
        return acquisition

    def invalidate(
        self,
        registration: grant._PrivateMutationGrantRegistration,
        reason: grant.LimiterReservationInvalidationReason,
    ) -> dispatch._VenueNoEntryDisposition | None:
        self._gate.assert_held()
        return self._invalidate_state(self._state(registration), reason)

    def invalidate_all(self, reason: grant.LimiterReservationInvalidationReason) -> None:
        self._gate.assert_held()
        if type(reason) is not grant.LimiterReservationInvalidationReason:
            raise _error()
        for state in self._owned_states():
            self._invalidate_state(state, reason)

    def invalidate_expiring(self, now_ns: int) -> None:
        self._gate.assert_held()
        if type(now_ns) is not int or now_ns < 0:
            raise _error()
        for state in self._owned_states():
            if state.reservation_state in {"UNISSUED", "FINALIZED_FOR_ENTRY", "INVALIDATED"}:
                continue
            events = (state.rest_event, state.product_event)
            if any(
                event is not None
                and object.__getattribute__(event, "expires_at_monotonic_ns") <= now_ns
                for event in events
            ):
                self._invalidate_state(
                    state,
                    grant.LimiterReservationInvalidationReason.ACCOUNTING_WINDOW_EXPIRED,
                )

    def consume(
        self,
        token: grant.LimiterWaitCompleted,
        *,
        target_ok: bool,
    ) -> grant._ConsumedLimiterWaitEvidence:
        self._gate.assert_held()
        state = self._token_state(token)
        if state.token_state != "ISSUED" or state.binding is None:
            raise _error()
        if not target_ok:
            self._invalidate_state(
                state, grant.LimiterReservationInvalidationReason.WRONG_MODE_OR_TARGET
            )
            raise _error()
        evidence = object.__new__(grant._ConsumedLimiterWaitEvidence)
        object.__setattr__(evidence, "_grant", state)
        object.__setattr__(evidence, "_binding", state.binding)
        state.evidence = evidence
        state.token_state, state.reservation_state = "CONSUMED_BY_D10", "CONSUMED_BY_D10"
        return evidence

    def invalidate_issued(
        self,
        token: grant.LimiterWaitCompleted,
        reason: grant.LimiterReservationInvalidationReason,
    ) -> None:
        self._gate.assert_held()
        state = self._token_state(token)
        if state.token_state != "ISSUED":
            raise _error()
        self._invalidate_state(state, reason)

    def invalidate_consumed(
        self,
        evidence: grant._ConsumedLimiterWaitEvidence,
        reason: grant.LimiterReservationInvalidationReason,
    ) -> None:
        self._gate.assert_held()
        state = self._evidence_state(evidence)
        if state.reservation_state != "CONSUMED_BY_D10":
            raise _error()
        self._invalidate_state(state, reason)

    def attach(
        self,
        evidence: grant._ConsumedLimiterWaitEvidence,
        latch: dispatch._VenueDispatchEntryLatch,
    ) -> grant._VenueLimiterReservationHandle:
        self._gate.assert_held()
        state = self._evidence_state(evidence)
        owner = state.latch_owner
        if (
            state.reservation_state != "CONSUMED_BY_D10"
            or type(latch) is not dispatch._VenueDispatchEntryLatch
            or object.__getattribute__(owner, "_latch") is not latch
            or state.handle is not None
        ):
            if state.reservation_state == "CONSUMED_BY_D10":
                self._invalidate_state(
                    state,
                    grant.LimiterReservationInvalidationReason.HANDOFF_ATTACHMENT_FAILED,
                )
            raise _error()
        exact = cast(Any, entry._assert_owned_latch(owner))
        if exact._attached or exact._terminal is not None:
            raise _error()
        object.__setattr__(exact, "_attached", True)
        handle = object.__new__(grant._VenueLimiterReservationHandle)
        object.__setattr__(handle, "_grant", state)
        object.__setattr__(handle, "_binding", state.binding)
        state.handle = handle
        state.reservation_state = "ATTACHED_TO_VENUE_HANDOFF"
        self._handles[id(handle)] = state
        return handle

    def transfer(
        self,
        handle: grant._VenueLimiterReservationHandle,
        latch: dispatch._VenueDispatchEntryLatch,
        *,
        rejection_reason: grant.LimiterReservationInvalidationReason | None,
    ) -> str | dispatch._VenueNoEntryDisposition:
        self._gate.assert_held()
        state = self._handle_state(handle)
        owner = state.latch_owner
        if state.reservation_state == "FINALIZED_FOR_ENTRY":
            return "ALREADY_ENTERED"
        if state.disposition is not None:
            return state.disposition
        if not owner.held_by_current_thread():
            raise _error()
        if object.__getattribute__(owner, "_latch") is not latch:
            rejection_reason = (
                grant.LimiterReservationInvalidationReason.VENUE_TRANSFER_BINDING_MISMATCH
            )
        if rejection_reason is None and owner.transfer_was_used():
            rejection_reason = grant.LimiterReservationInvalidationReason.VENUE_TRANSFER_REUSED
        if rejection_reason is not None:
            disposition = self._invalidate_state(
                state,
                rejection_reason,
                latch_held=True,
            )
            if disposition is None:
                raise _error()
            return disposition
        if state.reservation_state != "ATTACHED_TO_VENUE_HANDOFF":
            raise _error()
        exact = cast(Any, entry._assert_owned_latch(owner))
        if exact._transfer_used or exact._terminal is not None:
            raise _error()
        object.__setattr__(exact, "_transfer_used", True)
        return "TRANSFERRED"

    def invalidate_attached(
        self,
        handle: grant._VenueLimiterReservationHandle,
        latch: dispatch._VenueDispatchEntryLatch,
        reason: grant.LimiterReservationInvalidationReason,
        venue_reason: dispatch.VenueNoEntryReason,
    ) -> dispatch._VenueNoEntryDisposition:
        self._gate.assert_held()
        state = self._handle_state(handle)
        owner = state.latch_owner
        if state.reservation_state == "FINALIZED_FOR_ENTRY":
            raise _error()
        if state.disposition is not None:
            return state.disposition
        if not owner.held_by_current_thread():
            raise _error()
        if object.__getattribute__(owner, "_latch") is not latch:
            reason = grant.LimiterReservationInvalidationReason.VENUE_TRANSFER_BINDING_MISMATCH
            venue_reason = "SINK_BINDING_MISMATCH"
        disposition = self._invalidate_state(
            state,
            reason,
            venue_reason=venue_reason,
            latch_held=True,
        )
        if disposition is None:
            raise _error()
        return disposition

    def advance_phase(
        self,
        handle: grant._VenueLimiterReservationHandle,
        latch: dispatch._VenueDispatchEntryLatch,
        phase: dispatch.VenueDispatchPreEntryPhase,
        wire_evidence: Sha256Hex | None,
        audit_receipt_id: UUID | None,
    ) -> None:
        self._gate.assert_held()
        state = self._handle_state(handle)
        owner = state.latch_owner
        valid = (
            owner.held_by_current_thread()
            and object.__getattribute__(owner, "_latch") is latch
            and state.reservation_state == "ATTACHED_TO_VENUE_HANDOFF"
            and state.disposition is None
            and lifecycle._is_allowed_phase_transition(
                current_phase=state.phase,
                current_wire_evidence=state.wire_request_evidence_sha256,
                next_phase=phase,
                next_wire_evidence=wire_evidence,
                next_audit_receipt_id=audit_receipt_id,
            )
            and (audit_receipt_id is None or audit_receipt_id not in self._identities)
        )
        if not valid:
            self._invalidate_state(
                state,
                grant.LimiterReservationInvalidationReason.INTERNAL_EXCEPTION,
                latch_held=owner.held_by_current_thread(),
            )
            raise _error()
        state.phase = phase
        state.wire_request_evidence_sha256 = wire_evidence
        state.pre_dispatch_audit_receipt_id = audit_receipt_id
        state.owned_audit_receipt_id = audit_receipt_id
        if audit_receipt_id is not None:
            self._identities[audit_receipt_id] = state

    def enter(self, handle: grant._VenueLimiterReservationHandle) -> dispatch._EnteredVenueDispatch:
        self._gate.assert_held()
        state = self._handle_state(handle)
        owner = state.latch_owner
        if (
            not owner.held_by_current_thread()
            or state.disposition is not None
            or state.reservation_state != "ATTACHED_TO_VENUE_HANDOFF"
            or not owner.transfer_was_used()
            or not lifecycle._is_audited_phase_evidence(
                state.phase,
                state.wire_request_evidence_sha256,
                state.pre_dispatch_audit_receipt_id,
            )
        ):
            raise _error()
        state.reservation_state = "FINALIZED_FOR_ENTRY"
        exact = cast(Any, entry._assert_owned_latch(owner))
        if exact._terminal is not None or state.entered is not None:
            raise _error()
        object.__setattr__(exact, "_terminal", "ENTERED_TRANSPORT")
        state.entered = object.__new__(dispatch._EnteredVenueDispatch)
        return state.entered

    def terminal_entry_result(
        self,
        handle: grant._VenueLimiterReservationHandle,
        reason: grant.LimiterReservationInvalidationReason,
        venue_reason: dispatch.VenueNoEntryReason,
    ) -> dispatch._VenueNoEntryDisposition | dispatch._EnteredVenueDispatch:
        self._gate.assert_held()
        state = self._handle_state(handle)
        if state.entered is not None:
            return state.entered
        if state.disposition is not None:
            return state.disposition
        owner = state.latch_owner
        latch = object.__getattribute__(owner, "_latch")
        with owner.hold_after_gate(self._gate):
            return self.invalidate_attached(handle, latch, reason, venue_reason)

    def latch_held_by_current_thread(self) -> bool:
        return any(state.latch_owner.held_by_current_thread() for state in self._owned_states())

    def _owned_states(self) -> tuple[_GrantState, ...]:
        states = {
            id(state): state for state in self._identities.values() if type(state) is _GrantState
        }
        return tuple(states.values())

    def _invalidate_state(
        self,
        state: _GrantState,
        reason: grant.LimiterReservationInvalidationReason,
        *,
        venue_reason: dispatch.VenueNoEntryReason | None = None,
        latch_held: bool = False,
    ) -> dispatch._VenueNoEntryDisposition | None:
        if type(reason) is not grant.LimiterReservationInvalidationReason or (
            venue_reason is not None
            and not lifecycle._is_allowed_venue_no_entry_reason(venue_reason)
        ):
            raise _error()
        self._restore_state(state)
        if state.reservation_state == "FINALIZED_FOR_ENTRY":
            return None
        if state.disposition is not None:
            return state.disposition
        state.registration_state = "INVALIDATED"
        if state.token_state == "ISSUED":
            state.token_state = "INVALIDATED"
        if state.reservation_state != "UNISSUED":
            state.reservation_state = "INVALIDATED"
        state.terminal_reason = reason
        selected = venue_reason if venue_reason is not None else lifecycle._venue_reason(reason)
        owner = state.latch_owner

        def finish() -> dispatch._VenueNoEntryDisposition:
            exact = cast(Any, entry._assert_owned_latch(owner))
            if (
                self._grants.get(state.identity_keys[0]) is not state
                or exact._terminal not in (None, "INVALIDATED")
                or state.disposition is not None
            ):
                raise _error()
            object.__setattr__(exact, "_terminal", "INVALIDATED")
            result = object.__new__(dispatch._VenueNoEntryDisposition)
            values = {
                "_audit_identity": state.owned_objects[2],
                "_last_phase": state.phase,
                "_reason": selected,
                "_wire_request_evidence_sha256": state.wire_request_evidence_sha256,
                "_pre_dispatch_audit_receipt_id": state.owned_audit_receipt_id,
            }
            for name, value in values.items():
                object.__setattr__(result, name, value)
            state.disposition = result
            return result

        attached = cast(Any, entry._assert_owned_latch(owner))._attached
        if attached and not latch_held:
            with owner.hold_after_gate(self._gate):
                return finish()
        return finish()

    def _restore_state(self, state: _GrantState) -> None:
        if type(state) is not _GrantState:
            raise _error()
        registration = state.registration
        candidates = (
            self._owner_capability,
            object.__getattribute__(registration, "_owner_capability"),
        )
        capability = own._private_limiter_owner_for_store(candidates, store=self)
        if capability is None:
            raise _error()
        registry, limiter, gate, owner_id = own._private_limiter_owner_parts(capability, store=self)
        object.__setattr__(registry, "_owner_capability", capability)
        object.__setattr__(registry, "_limiter", limiter)
        object.__setattr__(registry, "_lifecycle", self)
        object.__setattr__(registry, "_registry_owner_id", owner_id)
        exact_limiter = cast(Any, limiter)
        exact_limiter._private_lifecycle_capability = capability
        exact_limiter._private_lifecycle_store = self
        object.__setattr__(state.registration, "registry_owner_id", owner_id)
        self._gate = cast("_LimiterAccountingGate", gate)
        self._owner_capability = capability
        owned_slot, owned_latch, owned_audit = state.owned_objects
        ids = state.identity_keys
        state.audit_identity = cast(dispatch.VenueDispatchAuditIdentity, owned_audit)
        state.pre_dispatch_audit_receipt_id = state.owned_audit_receipt_id
        exact_owner = cast(Any, state.latch_owner)
        object.__setattr__(exact_owner, "_latch", owned_latch)
        object.__setattr__(exact_owner, "_outcome_slot", owned_slot)
        self._grants[ids[0]] = state
        self._reservations[ids[1]] = state
        self._tokens[ids[2]] = state
        self._outcomes[state.object_keys[0]] = state
        self._latches[state.object_keys[1]] = state
        for identity in ids:
            self._identities[identity] = state
        if state.owned_audit_receipt_id is not None:
            self._identities[state.owned_audit_receipt_id] = state

    def _state(self, registration: grant._PrivateMutationGrantRegistration) -> _GrantState:
        if type(registration) is not grant._PrivateMutationGrantRegistration:
            raise _error()
        registration_id = registration.registration_id
        if type(registration_id) is not UUID or registration_id.int == 0:
            raise _error()
        state = self._grants.get(registration_id)
        if state is None or state.registration is not registration:
            raise _error()
        return state

    def _token_state(self, token: grant.LimiterWaitCompleted) -> _GrantState:
        state = self._record_state(token, grant.LimiterWaitCompleted)
        try:
            token_id = object.__getattribute__(token, "_token_id")
        except AttributeError:
            raise _error() from None
        if type(token_id) is not UUID:
            raise _error()
        return state

    def _evidence_state(self, evidence: grant._ConsumedLimiterWaitEvidence) -> _GrantState:
        state = self._record_state(evidence, grant._ConsumedLimiterWaitEvidence)
        if state.evidence is not evidence:
            raise _error()
        return state

    def _handle_state(self, handle: grant._VenueLimiterReservationHandle) -> _GrantState:
        return self._record_state(handle, grant._VenueLimiterReservationHandle)

    @staticmethod
    def _record_state(record: object, expected_type: type[object]) -> _GrantState:
        if type(record) is not expected_type:
            raise _error()
        try:
            state = object.__getattribute__(record, "_grant")
        except AttributeError:
            raise _error() from None
        if type(state) is not _GrantState:
            raise _error()
        return state


__all__: list[str] = []
