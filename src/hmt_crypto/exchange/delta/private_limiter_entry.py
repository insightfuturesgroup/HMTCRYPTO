"""Owner-bound gate-to-latch primitives for private limiter entry."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, NoReturn, cast
from uuid import UUID

from hmt_crypto.domain.private_primitives import Sha256Hex
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta import private_limiter_lifecycle as lifecycle
from hmt_crypto.exchange.delta.private_limiter_completion import (
    LimiterReservationInvalidationReason,
    LimiterWaitBinding,
    LimiterWaitCompleted,
    _ConsumedLimiterWaitEvidence,
    _VenueLimiterReservationHandle,
)
from hmt_crypto.exchange.delta.private_limiter_owner import (
    _private_limiter_owner_for_store,
)
from hmt_crypto.exchange.private_dispatch import (
    TrustedDispatchDeadline,
    TrustedDispatchInstant,
    VenueDispatchPreEntryPhase,
    VenueNoEntryReason,
    _EnteredVenueDispatch,
    _venue_dispatch_entry_latch_owner,
    _VenueDispatchEntryLatch,
    _VenueDispatchOutcomeSlot,
    _VenueNoEntryDisposition,
)
from hmt_crypto.worker import clock as task210_clock

if TYPE_CHECKING:
    from hmt_crypto.exchange.delta.limiter_accounting import _LimiterAccountingGate
    from hmt_crypto.exchange.delta.private_limiter_lifecycle_store import (
        _GrantState,
        _PrivateLimiterLifecycleStore,
    )
    from hmt_crypto.exchange.delta.weighted_limiter import WeightedRateLimiter
    from hmt_crypto.worker.leadership import Task210SinglePrivateWorkerProof


def _deny_latch_operation(*_args: object, **_kwargs: object) -> NoReturn:
    raise TypeError("private limiter entry latch owner operation unavailable")


class _OwnedVenueEntryLatch:
    __slots__ = (
        "_active_holder",
        "_attached",
        "_latch",
        "_lock",
        "_outcome_slot",
        "_owner_pid",
        "_owner_thread_id",
        "_terminal",
        "_transfer_used",
    )

    __new__ = _deny_latch_operation
    __setattr__ = _deny_latch_operation
    __delattr__ = _deny_latch_operation
    __copy__ = _deny_latch_operation
    __deepcopy__ = _deny_latch_operation
    __reduce_ex__ = _deny_latch_operation
    __reduce__ = _deny_latch_operation
    __getstate__ = _deny_latch_operation

    def held_by_current_thread(self) -> bool:
        active = object.__getattribute__(self, "_active_holder")
        return bool(type(active) is int and active == threading.get_ident())

    def transfer_was_used(self) -> bool:
        return bool(cast(Any, _assert_owned_latch(self))._transfer_used)

    @contextmanager
    def hold_after_gate(self, gate: _LimiterAccountingGate) -> Iterator[None]:
        gate.assert_held()
        _assert_owned_latch(self)
        holder = threading.get_ident()
        lock = object.__getattribute__(self, "_lock")
        if object.__getattribute__(self, "_active_holder") is not None or not lock.acquire(
            blocking=False
        ):
            raise _error()
        object.__setattr__(self, "_active_holder", holder)
        try:
            yield
        finally:
            object.__setattr__(self, "_active_holder", None)
            lock.release()


def _error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "private_limiter_entry"})


def _assert_owned_latch(owner: object) -> _OwnedVenueEntryLatch:
    if type(owner) is not _OwnedVenueEntryLatch:
        raise _error()
    try:
        latch = object.__getattribute__(owner, "_latch")
        slot = object.__getattribute__(owner, "_outcome_slot")
        lock = object.__getattribute__(owner, "_lock")
        pid = object.__getattribute__(owner, "_owner_pid")
        thread_id = object.__getattribute__(owner, "_owner_thread_id")
        attached = object.__getattribute__(owner, "_attached")
        transfer_used = object.__getattribute__(owner, "_transfer_used")
        terminal = object.__getattribute__(owner, "_terminal")
    except AttributeError:
        raise _error() from None
    terminal_text = type(terminal) is str and terminal in ("INVALIDATED", "ENTERED_TRANSPORT")
    valid_terminal = terminal is None or terminal_text
    if (
        type(latch) is not _VenueDispatchEntryLatch
        or type(slot) is not _VenueDispatchOutcomeSlot
        or _venue_dispatch_entry_latch_owner(latch) is not slot
        or type(lock) is not type(threading.Lock())
        or (type(pid), type(thread_id)) != (int, int)
        or (pid, thread_id) != (os.getpid(), threading.get_ident())
        or (type(attached), type(transfer_used)) != (bool, bool)
        or not valid_terminal
    ):
        raise _error()
    return cast("_OwnedVenueEntryLatch", owner)


def _bind_owned_latch(
    gate: _LimiterAccountingGate,
    latch: _VenueDispatchEntryLatch,
    outcome_slot: _VenueDispatchOutcomeSlot,
) -> _OwnedVenueEntryLatch:
    gate.assert_held()
    if _venue_dispatch_entry_latch_owner(latch) is not outcome_slot:
        raise _error()
    owner: _OwnedVenueEntryLatch = object.__new__(cast(Any, _OwnedVenueEntryLatch))
    values = (
        ("_active_holder", None),
        ("_attached", False),
        ("_latch", latch),
        ("_lock", threading.Lock()),
        ("_outcome_slot", outcome_slot),
        ("_owner_pid", os.getpid()),
        ("_owner_thread_id", threading.get_ident()),
        ("_terminal", None),
        ("_transfer_used", False),
    )
    for name, value in values:
        object.__setattr__(owner, name, value)
    return owner


def _registry_parts(
    registry: object,
) -> tuple[WeightedRateLimiter, _PrivateLimiterLifecycleStore]:
    try:
        return (
            cast("WeightedRateLimiter", object.__getattribute__(registry, "_limiter")),
            cast("_PrivateLimiterLifecycleStore", object.__getattribute__(registry, "_lifecycle")),
        )
    except AttributeError:
        raise _error() from None


def _prepare_transition(
    registry: object,
) -> tuple[
    Task210SinglePrivateWorkerProof,
    tuple[float, int],
    WeightedRateLimiter,
    _PrivateLimiterLifecycleStore,
]:
    try:
        proof = cast(
            "Task210SinglePrivateWorkerProof",
            object.__getattribute__(registry, "_require_current")(),
        )
        limiter, store = _registry_parts(registry)
        sample = limiter._sample_monotonic()
    except ConfigurationError:
        raise
    except Exception:
        raise _error() from None
    return proof, sample, limiter, store


def _current_invalidation_reason(
    registry: object,
    limiter: WeightedRateLimiter,
    state: _GrantState,
    proof: Task210SinglePrivateWorkerProof,
    now_ns: int,
) -> LimiterReservationInvalidationReason | None:
    binding = state.binding
    registration = state.registration
    store = object.__getattribute__(registry, "_lifecycle")
    capability = _private_limiter_owner_for_store(
        (
            object.__getattribute__(registry, "_owner_capability"),
            object.__getattribute__(limiter, "_private_lifecycle_capability"),
            object.__getattribute__(store, "_owner_capability"),
            object.__getattribute__(registration, "_owner_capability"),
        ),
        store=store,
    )
    if (
        object.__getattribute__(registry, "_closed")
        or limiter._closed
        or object.__getattribute__(registry, "_ownership") is not proof
        or capability is None
        or type(state.latch_owner) is not _OwnedVenueEntryLatch
        or not lifecycle._has_frozen_currentness_parity(
            registration=registration,
            binding=binding,
            registry_owner_id=object.__getattribute__(registry, "_registry_owner_id"),
            registry_open_epoch=object.__getattribute__(registry, "_open_epoch"),
            ownership_proof_id=proof.proof_id,
            limiter_instance_id=limiter._limiter_instance_id,
            limiter_clock_epoch=limiter._limiter_clock_epoch,
            accounting_generation=limiter._limiter_accounting_generation,
            cooldown_epoch=limiter._cooldown_epoch,
            scope=object.__getattribute__(registry, "_scope"),
            policy_version=limiter._policy_version,
            process_id=object.__getattribute__(registry, "_process_id"),
            process_incarnation_id=object.__getattribute__(registry, "_process_incarnation_id"),
        )
        or not lifecycle._has_exact_owner_state(
            registry=registry,
            limiter=limiter,
            store=store,
            state=state,
            capability=capability,
        )
    ):
        return LimiterReservationInvalidationReason.LIMITER_REPLACED
    binding = cast(LimiterWaitBinding, binding)
    if limiter._clock_poisoned or binding.limiter_clock_epoch != limiter._limiter_clock_epoch:
        return LimiterReservationInvalidationReason.LIMITER_CLOCK_INVALIDATED
    if (
        limiter._accounting_poisoned
        or binding.limiter_accounting_generation != limiter._limiter_accounting_generation
    ):
        return LimiterReservationInvalidationReason.ACCOUNTING_EVENT_REPLACED
    if binding.cooldown_epoch != limiter._cooldown_epoch:
        return LimiterReservationInvalidationReason.COOLDOWN_ADVANCED
    rest = limiter._event_index.get(binding.rest_window_id)
    product = limiter._event_index.get(binding.product_window_id)
    rest_event = state.rest_event
    product_event = state.product_event
    if (
        rest is None
        or product is None
        or rest.event is not rest_event
        or product.event is not product_event
        or rest.product_id is not None
        or product.product_id != binding.product_id
        or object.__getattribute__(rest_event, "event_id") != binding.rest_window_id
        or object.__getattribute__(product_event, "event_id") != binding.product_window_id
        or object.__getattribute__(rest_event, "amount") != binding.rest_weight
        or object.__getattribute__(product_event, "amount") != binding.product_operations
        or object.__getattribute__(rest_event, "expires_at_monotonic_ns")
        != binding.rest_window_end_monotonic_ns
        or object.__getattribute__(product_event, "expires_at_monotonic_ns")
        != binding.product_window_end_monotonic_ns
        or object.__getattribute__(rest_event, "occurred_at_monotonic_ns")
        != binding.rest_window_end_monotonic_ns - limiter._rest_window_ns
        or object.__getattribute__(product_event, "occurred_at_monotonic_ns")
        != binding.product_window_end_monotonic_ns - 1_000_000_000
    ):
        return LimiterReservationInvalidationReason.ACCOUNTING_EVENT_REPLACED
    if (
        now_ns >= binding.rest_window_end_monotonic_ns
        or now_ns >= binding.product_window_end_monotonic_ns
    ):
        return LimiterReservationInvalidationReason.ACCOUNTING_WINDOW_EXPIRED
    return None


def _apply_transition_sample(
    registry: object,
    limiter: WeightedRateLimiter,
    store: _PrivateLimiterLifecycleStore,
    state: _GrantState,
    proof: Task210SinglePrivateWorkerProof,
    sample: tuple[float, int],
) -> LimiterReservationInvalidationReason | None:
    try:
        limiter._accept_clock_sample_locked(*sample)
        limiter._prune_locked(sample[1])
    except ConfigurationError:
        if state.terminal_reason is not None:
            return state.terminal_reason
        raise
    reason = _current_invalidation_reason(registry, limiter, state, proof, sample[1])
    if reason is not None:
        store._invalidate_state(state, reason)
    return reason


def _consume_registered_wait_once(
    registry: object,
    token: LimiterWaitCompleted,
    expected: LimiterWaitBinding,
    *,
    target_ok: bool,
) -> _ConsumedLimiterWaitEvidence:
    owned_expected = lifecycle._canonical_binding(expected)
    proof, sample, limiter, store = _prepare_transition(registry)
    with limiter._accounting_gate.hold():
        state = store._token_state(token)
        if _apply_transition_sample(registry, limiter, store, state, proof, sample) is not None:
            raise _error()
        if owned_expected is None or not lifecycle._bindings_match(state.binding, owned_expected):
            store.invalidate(
                state.registration,
                LimiterReservationInvalidationReason.EXPECTED_BINDING_MISMATCH,
            )
            raise _error()
        return store.consume(token, target_ok=target_ok)


def _invalidate_issued_wait_once(
    registry: object,
    token: LimiterWaitCompleted,
    reason: LimiterReservationInvalidationReason,
) -> None:
    proof, sample, limiter, store = _prepare_transition(registry)
    with limiter._accounting_gate.hold():
        state = store._token_state(token)
        if _apply_transition_sample(registry, limiter, store, state, proof, sample) is not None:
            raise _error()
        store.invalidate_issued(token, reason)


def _invalidate_consumed_wait_once(
    registry: object,
    evidence: _ConsumedLimiterWaitEvidence,
    reason: LimiterReservationInvalidationReason,
) -> None:
    proof, sample, limiter, store = _prepare_transition(registry)
    with limiter._accounting_gate.hold():
        state = store._evidence_state(evidence)
        if _apply_transition_sample(registry, limiter, store, state, proof, sample) is not None:
            raise _error()
        store.invalidate_consumed(evidence, reason)


def _attach_consumed_wait_once(
    registry: object,
    evidence: _ConsumedLimiterWaitEvidence,
    entry_latch: _VenueDispatchEntryLatch,
) -> _VenueLimiterReservationHandle:
    proof, sample, limiter, store = _prepare_transition(registry)
    with limiter._accounting_gate.hold():
        state = store._evidence_state(evidence)
        if _apply_transition_sample(registry, limiter, store, state, proof, sample) is not None:
            raise _error()
        return store.attach(evidence, entry_latch)


def _transfer_attached_wait_once(
    registry: object,
    handle: _VenueLimiterReservationHandle,
    entry_latch: _VenueDispatchEntryLatch,
    rejection_reason: LimiterReservationInvalidationReason | None,
) -> str | _VenueNoEntryDisposition:
    try:
        proof, sample, limiter, store = _prepare_transition(registry)
    except ConfigurationError:
        terminal = _terminal_entry_result(
            registry,
            handle,
            LimiterReservationInvalidationReason.POST_COMMIT_CURRENTNESS_FAILED,
            "CURRENT_INVALIDATED",
        )
        result: object = "ALREADY_ENTERED" if type(terminal) is _EnteredVenueDispatch else terminal
        return cast("str | _VenueNoEntryDisposition", result)
    with limiter._accounting_gate.hold():
        state = store._handle_state(handle)
        reason = _apply_transition_sample(registry, limiter, store, state, proof, sample)
        if reason is not None:
            if state.disposition is None:
                raise _error()
            return state.disposition
        with state.latch_owner.hold_after_gate(limiter._accounting_gate):
            return store.transfer(
                handle,
                entry_latch,
                rejection_reason=rejection_reason,
            )


def _invalidate_attached_wait_once(
    registry: object,
    handle: _VenueLimiterReservationHandle,
    entry_latch: _VenueDispatchEntryLatch,
    limiter_reason: LimiterReservationInvalidationReason,
    venue_reason: VenueNoEntryReason,
) -> _VenueNoEntryDisposition:
    try:
        proof, sample, limiter, store = _prepare_transition(registry)
    except ConfigurationError:
        terminal = _terminal_entry_result(
            registry,
            handle,
            LimiterReservationInvalidationReason.POST_COMMIT_CURRENTNESS_FAILED,
            "CURRENT_INVALIDATED",
        )
        if type(terminal) is _EnteredVenueDispatch:
            raise _error() from None
        return cast("_VenueNoEntryDisposition", terminal)
    with limiter._accounting_gate.hold():
        state = store._handle_state(handle)
        reason = _apply_transition_sample(registry, limiter, store, state, proof, sample)
        if reason is not None:
            if state.disposition is None:
                raise _error()
            return state.disposition
        with state.latch_owner.hold_after_gate(limiter._accounting_gate):
            return store.invalidate_attached(
                handle,
                entry_latch,
                limiter_reason,
                venue_reason,
            )


def _advance_attached_phase_once(
    registry: object,
    handle: _VenueLimiterReservationHandle,
    entry_latch: _VenueDispatchEntryLatch,
    phase: VenueDispatchPreEntryPhase,
    wire_evidence: Sha256Hex | None,
    audit_receipt_id: UUID | None,
) -> None:
    proof, sample, limiter, store = _prepare_transition(registry)
    with limiter._accounting_gate.hold():
        state = store._handle_state(handle)
        if _apply_transition_sample(registry, limiter, store, state, proof, sample) is not None:
            raise _error()
        with state.latch_owner.hold_after_gate(limiter._accounting_gate):
            store.advance_phase(
                handle,
                entry_latch,
                phase,
                wire_evidence,
                audit_receipt_id,
            )


def _terminal_entry_result(
    registry: object,
    handle: _VenueLimiterReservationHandle,
    reason: LimiterReservationInvalidationReason,
    venue_reason: VenueNoEntryReason,
) -> _VenueNoEntryDisposition | _EnteredVenueDispatch:
    limiter, store = _registry_parts(registry)
    if store.latch_held_by_current_thread():
        raise _error()
    with limiter._accounting_gate.hold():
        return store.terminal_entry_result(handle, reason, venue_reason)


def _finalize_attached_wait_once(
    registry: object,
    handle: _VenueLimiterReservationHandle,
    expected: LimiterWaitBinding,
    entry_latch: _VenueDispatchEntryLatch,
    clock: object,
    clock_currentness: object,
    signed_at: object,
    deadline: object,
    signed_timestamp: object,
) -> _VenueNoEntryDisposition | _EnteredVenueDispatch:
    invalid = LimiterReservationInvalidationReason.POST_COMMIT_CURRENTNESS_FAILED
    owned_expected = lifecycle._canonical_binding(expected)
    if owned_expected is None:
        return _terminal_entry_result(
            registry,
            handle,
            LimiterReservationInvalidationReason.VENUE_TRANSFER_BINDING_MISMATCH,
            "SINK_BINDING_MISMATCH",
        )
    if (
        type(clock) is not task210_clock.Task210TrustedDispatchClock
        or type(clock_currentness) is not task210_clock.Task210TrustedClockCurrentnessPort
        or type(signed_at) is not TrustedDispatchInstant
        or type(deadline) is not TrustedDispatchDeadline
        or type(signed_timestamp) is not int
        or signed_timestamp < 0
    ):
        return _terminal_entry_result(registry, handle, invalid, "CURRENT_INVALIDATED")
    try:
        owned_signed_at = TrustedDispatchInstant.model_validate(signed_at.model_dump(), strict=True)
        owned_deadline = TrustedDispatchDeadline.model_validate(deadline.model_dump(), strict=True)
    except Exception:
        return _terminal_entry_result(registry, handle, invalid, "CURRENT_INVALIDATED")
    process_id = object.__getattribute__(registry, "_process_id")
    incarnation = object.__getattribute__(registry, "_process_incarnation_id")
    try:
        returned = clock_currentness.revalidate_dispatch_clock_nonblocking(
            clock,
            expected_process_id=process_id,
            expected_process_incarnation_id=incarnation,
        )
        if not lifecycle._is_bound_entry_evidence(
            returned_clock=returned,
            clock=clock,
            signed_at=owned_signed_at,
            deadline=owned_deadline,
            process_id=process_id,
            process_incarnation_id=incarnation,
        ):
            raise _error()
        proof, sample, limiter, store = _prepare_transition(registry)
        returned = clock_currentness.revalidate_dispatch_clock_nonblocking(
            clock,
            expected_process_id=process_id,
            expected_process_incarnation_id=incarnation,
        )
        if returned is not clock:
            raise _error()
    except Exception:
        return _terminal_entry_result(registry, handle, invalid, "CURRENT_INVALIDATED")
    with limiter._accounting_gate.hold():
        state = store._handle_state(handle)
        if state.entered is not None:
            return state.entered
        if state.disposition is not None:
            return state.disposition
        wrong_clock = state.entry_clock is not None and state.entry_clock is not clock
        wrong_port = (
            state.entry_clock_currentness is not None
            and state.entry_clock_currentness is not clock_currentness
        )
        if wrong_clock or wrong_port:
            return store.terminal_entry_result(handle, invalid, "CURRENT_INVALIDATED")
        state.entry_clock, state.entry_clock_currentness = clock, clock_currentness
        reason = _apply_transition_sample(registry, limiter, store, state, proof, sample)
        if reason is not None:
            disposition = object.__getattribute__(state, "disposition")
            if type(disposition) is not _VenueNoEntryDisposition:
                raise _error()
            return disposition
        owner = state.latch_owner
        with owner.hold_after_gate(limiter._accounting_gate):
            bound_latch = object.__getattribute__(owner, "_latch")
            if (
                bound_latch is not entry_latch
                or not lifecycle._bindings_match(state.binding, owned_expected)
                or not owner.transfer_was_used()
                or not lifecycle._is_audited_phase_evidence(
                    state.phase,
                    state.wire_request_evidence_sha256,
                    state.pre_dispatch_audit_receipt_id,
                )
            ):
                return store.invalidate_attached(
                    handle,
                    bound_latch,
                    LimiterReservationInvalidationReason.VENUE_TRANSFER_BINDING_MISMATCH,
                    "SINK_BINDING_MISMATCH",
                )
            try:
                post = clock.observe_nonblocking(
                    expected_process_incarnation_id=incarnation,
                    expected_clock_epoch=owned_deadline.clock_epoch,
                )
            except Exception:
                return store.invalidate_attached(
                    handle, bound_latch, invalid, "CURRENT_INVALIDATED"
                )
            time_ok = lifecycle._is_current_entry_time(
                post=post,
                signed_at=owned_signed_at,
                deadline=owned_deadline,
                signed_timestamp=signed_timestamp,
                process_id=process_id,
                process_incarnation_id=incarnation,
            )
            current = _current_invalidation_reason(
                registry, limiter, state, proof, post.monotonic_ns
            )
            if not time_ok or current is not None:
                selected = current or (
                    LimiterReservationInvalidationReason.DEADLINE_DERIVATION_FAILED
                )
                venue: VenueNoEntryReason = (
                    "CURRENT_INVALIDATED" if current is not None else "DEADLINE_EXPIRED"
                )
                return store.invalidate_attached(handle, bound_latch, selected, venue)
            return store.enter(handle)


__all__: list[str] = []
