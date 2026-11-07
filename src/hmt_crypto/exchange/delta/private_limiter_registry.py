"""PID-owned authenticated Delta limiter registry and sealed lifecycle ports."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import NoReturn, cast
from uuid import UUID, uuid4

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.private_primitives import ExactUuid, Sha256Hex, StrictPositivePid
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange import private_dispatch as dispatch
from hmt_crypto.exchange.delta import private_limiter_completion as grant
from hmt_crypto.exchange.delta import private_limiter_entry as entry
from hmt_crypto.exchange.delta import private_limiter_lifecycle as rules
from hmt_crypto.exchange.delta import private_limiter_lifecycle_store as lifecycle_store
from hmt_crypto.exchange.delta import private_limiter_owner as own
from hmt_crypto.exchange.delta import private_limiter_registry_authority as authority
from hmt_crypto.exchange.delta import private_limiter_registry_records as records
from hmt_crypto.exchange.delta import rate_limit_policy as limits
from hmt_crypto.exchange.delta.registered_limiter_grant import _stage_registered_mutation
from hmt_crypto.exchange.delta.weighted_limiter import WeightedRateLimiter
from hmt_crypto.worker import clock as trusted_clock
from hmt_crypto.worker import leadership as worker

PrivateLimiterLease = records.PrivateLimiterLease
_POLICY = limits.DELTA_RATE_LIMIT_POLICY
_Reason = grant.LimiterReservationInvalidationReason
_Store = lifecycle_store._PrivateLimiterLifecycleStore
_WorkerCurrentness = worker.Task210PrivateWorkerCurrentnessPort
_WorkerProof = worker.Task210SinglePrivateWorkerProof

_PROCESS_REGISTRIES: dict[int, PrivateLimiterRegistry] = {}
_FORK_POISONED = False


def _error(operation: str = "private_limiter_registry") -> ConfigurationError:
    return ConfigurationError(context={"operation": operation})


def _fail(operation: str = "private_limiter_registry") -> NoReturn:
    raise _error(operation) from None


def _loop_identity() -> int | None:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


class PrivateLimiterRegistry(records._OpaqueRegistryObject):
    """Sole process-local owner of one authenticated external-user limiter."""

    __slots__ = (
        "_closed",
        "_leases",
        "_lifecycle",
        "_limiter",
        "_loop_id",
        "_open_epoch",
        "_owner_capability",
        "_ownership",
        "_ownership_currentness",
        "_process_id",
        "_process_incarnation_id",
        "_registry_owner_id",
        "_scope",
        "_thread_id",
    )
    _closed: bool
    _leases: dict[UUID, PrivateLimiterLease]
    _lifecycle: _Store
    _limiter: WeightedRateLimiter
    _loop_id: int | None
    _open_epoch: int
    _owner_capability: own._PrivateLimiterOwnerCapability
    _ownership: _WorkerProof
    _ownership_currentness: _WorkerCurrentness
    _process_id: int
    _process_incarnation_id: UUID
    _registry_owner_id: UUID
    _scope: limits.RateLimitScope
    _thread_id: int

    def __init__(self, *, _constructor_nonce: object) -> None:
        del _constructor_nonce
        _fail("private_limiter_constructor")

    def _read_owner_composition(
        self,
        capability: object,
        *,
        store: object,
        phase: str,
    ) -> tuple[object, object, UUID] | None:
        del capability, store, phase
        return None

    def _assert_context(self) -> None:
        if (
            _FORK_POISONED
            or os.getpid() != self._process_id
            or threading.get_ident() != self._thread_id
            or _loop_identity() != self._loop_id
            or self._lifecycle.latch_held_by_current_thread()
            or self._limiter._accounting_gate.held_by_current_thread()
        ):
            _fail("private_limiter_context")

    def _require_current(self) -> _WorkerProof:
        self._assert_context()
        proof = self._ownership
        try:
            returned = self._ownership_currentness.revalidate_single_private_worker_nonblocking(
                proof,
                expected_external_user_id=self._scope.external_id,
                expected_region=DeltaRegion.INDIA,
                expected_environment=DeltaEnvironment(self._scope.environment),
                expected_process_id=self._process_id,
                expected_process_incarnation_id=self._process_incarnation_id,
            )
        except Exception:
            self.close()
            raise _error("private_limiter_currentness") from None
        if returned is not proof:
            self.close()
            _fail("private_limiter_currentness")
        return proof

    def lease_external_user(self, *, scope: limits.RateLimitScope) -> PrivateLimiterLease:
        """Return a distinct lease over the sole exact authenticated limiter."""
        self._require_current()
        if type(scope) is not limits.RateLimitScope or scope != self._scope:
            _fail()
        lease_id = uuid4()
        with self._limiter._accounting_gate.hold():
            if self._closed:
                _fail()
            lease = records._issue_private_limiter_lease(
                _lease_id=lease_id,
                _registry_owner_id=self._registry_owner_id,
                _ownership_proof_id=self._ownership.proof_id,
                _limiter_instance_id=self._limiter._limiter_instance_id,
                _scope=self._scope,
                _policy_version=self._limiter._policy_version,
                _process_id=self._process_id,
                _process_incarnation_id=self._process_incarnation_id,
                _registry_open_epoch=self._open_epoch,
                _registry=self,
            )
            self._leases[lease_id] = lease
        return lease

    async def acquire_mutation_once(
        self,
        *,
        intent: grant.LimiterWaitIntent,
        outcome_slot: dispatch._VenueDispatchOutcomeSlot,
    ) -> grant._RegisteredPrivateMutationAcquisition:
        """Atomically queue, charge, register, and publish one mutation grant."""
        self._require_current()
        if type(intent) is not grant.LimiterWaitIntent:
            _fail()
        try:
            owned = grant.LimiterWaitIntent.model_validate(intent.model_dump(), strict=True)
            audit_identity = dispatch._venue_dispatch_outcome_audit_identity(outcome_slot)
        except Exception:
            raise _error() from None
        account = owned.account
        if (
            owned.process_id != self._process_id
            or owned.process_incarnation_id != self._process_incarnation_id
            or account.external_user_id != self._scope.external_id
            or account.region is not DeltaRegion.INDIA
            or account.environment.value != self._scope.environment
        ):
            _fail()
        identities = tuple(uuid4() for _ in range(5))
        gate = self._limiter._accounting_gate
        with gate.hold():
            if self._closed:
                _fail()
            snapshots = (
                self._ownership.proof_id,
                self._limiter._limiter_clock_epoch,
                self._limiter._limiter_accounting_generation,
                self._limiter._cooldown_epoch,
            )
        registration, request = _stage_registered_mutation(
            owner_capability=self._owner_capability,
            limiter=self._limiter,
            store=self._lifecycle,
            intent=owned,
            outcome_slot=outcome_slot,
            audit_identity=audit_identity,
            registry_owner_id=self._registry_owner_id,
            registry_open_epoch=self._open_epoch,
            ownership_proof_id=snapshots[0],
            process_id=self._process_id,
            process_incarnation_id=self._process_incarnation_id,
            limiter_clock_epoch=snapshots[1],
            accounting_generation=snapshots[2],
            cooldown_epoch=snapshots[3],
            identities=cast(tuple[UUID, UUID, UUID, UUID, UUID], identities),
        )
        with gate.hold():
            current = (
                self._ownership.proof_id,
                self._limiter._limiter_clock_epoch,
                self._limiter._limiter_accounting_generation,
                self._limiter._cooldown_epoch,
            )
            if self._closed or current != snapshots:
                _fail()
            self._lifecycle.prepare(
                registration,
                audit_identity,
                dispatch._venue_dispatch_outcome_entry_latch(outcome_slot),
            )
        try:
            acquisition = await self._limiter._acquire_registered_mutation(
                request, registration=registration
            )
            self._require_current()
            return acquisition
        except BaseException as error:
            reason = (
                _Reason.CANCELLED
                if type(error) is asyncio.CancelledError
                else _Reason.INTERNAL_EXCEPTION
            )
            with gate.hold():
                self._lifecycle.invalidate(registration, reason)
            raise

    def consume_or_invalidate_once(
        self,
        token: grant.LimiterWaitCompleted,
        *,
        expected: grant.LimiterWaitBinding,
        actual_region: DeltaRegion,
        actual_environment: DeltaEnvironment,
        actual_mode: ExecutionMode,
    ) -> grant._ConsumedLimiterWaitEvidence:
        """Consume one issued token or atomically invalidate its reservation."""
        target_ok = rules._is_exact_venue_target(actual_region, actual_environment, actual_mode)
        return entry._consume_registered_wait_once(self, token, expected, target_ok=target_ok)

    def invalidate_issued_once(
        self,
        token: grant.LimiterWaitCompleted,
        *,
        reason: _Reason,
    ) -> None:
        """Invalidate one still-issued token after D10 pre-consume failure."""
        if reason is not _Reason.D10_PRECONSUME_REJECTION:
            _fail()
        entry._invalidate_issued_wait_once(self, token, reason)

    def invalidate_once(
        self,
        evidence: grant._ConsumedLimiterWaitEvidence,
        *,
        reason: _Reason,
    ) -> None:
        """Terminally invalidate one consumed reservation without refund."""
        if not rules._is_allowed_consumed_invalidation(reason):
            _fail()
        entry._invalidate_consumed_wait_once(self, evidence, reason)

    def attach_to_venue_handoff_once(
        self,
        evidence: grant._ConsumedLimiterWaitEvidence,
        *,
        entry_latch: dispatch._VenueDispatchEntryLatch,
    ) -> grant._VenueLimiterReservationHandle:
        """Attach the consumed reservation to one exact venue-entry latch."""
        return entry._attach_consumed_wait_once(self, evidence, entry_latch)

    def transfer_attached_once(
        self,
        handle: grant._VenueLimiterReservationHandle,
        *,
        entry_latch: dispatch._VenueDispatchEntryLatch,
        rejection_reason: _Reason | None,
    ) -> str | dispatch._VenueNoEntryDisposition:
        """Transfer one attached pair or return its canonical disposition."""
        if not rules._is_allowed_transfer_rejection(rejection_reason):
            _fail()
        return entry._transfer_attached_wait_once(
            self,
            handle,
            entry_latch,
            rejection_reason,
        )

    def invalidate_before_entry_once(
        self,
        handle: grant._VenueLimiterReservationHandle,
        *,
        entry_latch: dispatch._VenueDispatchEntryLatch,
        reason: dispatch.VenueNoEntryReason,
    ) -> dispatch._VenueNoEntryDisposition:
        """Atomically invalidate one attached reservation and its handoff."""
        if not rules._is_allowed_venue_no_entry_reason(reason):
            _fail()
        return entry._invalidate_attached_wait_once(
            self,
            handle,
            entry_latch,
            rules._venue_invalidation_reason(reason),
            reason,
        )

    def advance_pre_entry_once(
        self,
        handle: grant._VenueLimiterReservationHandle,
        *,
        entry_latch: dispatch._VenueDispatchEntryLatch,
        phase: dispatch.VenueDispatchPreEntryPhase,
        wire_request_evidence_sha256: Sha256Hex | None = None,
        pre_dispatch_audit_receipt_id: UUID | None = None,
    ) -> None:
        """Advance the owner-held venue phase and exact durable evidence once."""
        entry._advance_attached_phase_once(
            self,
            handle,
            entry_latch,
            phase,
            wire_request_evidence_sha256,
            pre_dispatch_audit_receipt_id,
        )

    def finalize_and_enter_once(
        self,
        handle: grant._VenueLimiterReservationHandle,
        *,
        expected: grant.LimiterWaitBinding,
        entry_latch: dispatch._VenueDispatchEntryLatch,
        clock: trusted_clock.Task210TrustedDispatchClock,
        clock_currentness: trusted_clock.Task210TrustedClockCurrentnessPort,
        signed_at: dispatch.TrustedDispatchInstant,
        deadline: dispatch.TrustedDispatchDeadline,
        signed_timestamp: int,
    ) -> dispatch._VenueNoEntryDisposition | dispatch._EnteredVenueDispatch:
        """Validate current accounting and atomically cross the entry boundary."""
        return entry._finalize_attached_wait_once(
            self,
            handle,
            expected,
            entry_latch,
            clock,
            clock_currentness,
            signed_at,
            deadline,
            signed_timestamp,
        )

    def close(self) -> None:
        """Terminally close registry, limiter, leases, and nonfinal grants."""
        self._assert_context()
        if self._closed:
            return
        with self._limiter._accounting_gate.hold():
            object.__setattr__(self, "_closed", True)
            self._limiter._closed = True
            self._lifecycle.invalidate_all(_Reason.REGISTRY_CLOSED)
            self._leases.clear()
            waiters = tuple(self._limiter._waiters)
        for waiter in waiters:
            waiter.event.set()


_REGISTRY_FACTORY = authority._take_private_registry_authority(PrivateLimiterRegistry)
delattr(authority, "_take_private_registry_authority")


def _compose_private_limiter_registry(
    *,
    ownership: _WorkerProof,
    ownership_currentness: _WorkerCurrentness,
    process_incarnation_id: ExactUuid,
    policy: limits.RateLimitPolicy,
) -> PrivateLimiterRegistry:
    return _REGISTRY_FACTORY._create(
        ownership=ownership,
        ownership_currentness=ownership_currentness,
        process_incarnation_id=process_incarnation_id,
        policy=policy,
    )


def _revalidate_private_limiter_lease_nonblocking(
    lease: PrivateLimiterLease,
    *,
    expected_scope: limits.RateLimitScope,
    expected_process_id: StrictPositivePid,
    expected_process_incarnation_id: ExactUuid,
) -> PrivateLimiterLease:
    if type(lease) is not PrivateLimiterLease:
        _fail()
    registry = object.__getattribute__(lease, "_registry")
    if type(registry) is not PrivateLimiterRegistry:
        _fail()
    registry._require_current()
    if (
        type(expected_scope) is not limits.RateLimitScope
        or type(expected_process_id) is not int
        or expected_process_id <= 0
        or type(expected_process_incarnation_id) is not UUID
    ):
        _fail()
    with registry._limiter._accounting_gate.hold():
        if (
            registry._closed
            or registry._leases.get(lease.lease_id) is not lease
            or lease.scope != expected_scope
            or lease.process_id != expected_process_id
            or lease.process_incarnation_id != expected_process_incarnation_id
            or lease.registry_open_epoch != registry._open_epoch
            or lease.ownership_proof_id != registry._ownership.proof_id
        ):
            _fail()
    return lease


def _replace_private_limiter_worker_proof_nonblocking(
    registry: PrivateLimiterRegistry,
    *,
    current: _WorkerProof,
    replacement: _WorkerProof,
    currentness: _WorkerCurrentness,
) -> PrivateLimiterRegistry:
    if (
        type(registry) is not PrivateLimiterRegistry
        or type(current) is not _WorkerProof
        or type(replacement) is not _WorkerProof
        or type(currentness) is not _WorkerCurrentness
    ):
        _fail()
    registry._assert_context()
    if currentness is not registry._ownership_currentness:
        _fail()
    try:
        returned_current, returned_replacement = (
            currentness.revalidate_worker_replacement_pair_nonblocking(
                current,
                replacement,
                expected_external_user_id=registry._scope.external_id,
                expected_region=DeltaRegion.INDIA,
                expected_environment=DeltaEnvironment(registry._scope.environment),
                expected_process_id=registry._process_id,
                expected_process_incarnation_id=registry._process_incarnation_id,
            )
        )
    except Exception:
        registry.close()
        raise _error("private_limiter_currentness") from None
    if (
        returned_current is not current
        or returned_replacement is not replacement
        or current is not registry._ownership
        or replacement.deployment_revision != current.deployment_revision
        or replacement.worker_incarnation_id != current.worker_incarnation_id
        or replacement.ownership_epoch <= current.ownership_epoch
        or replacement.expires_at <= current.expires_at
    ):
        _fail()
    with registry._limiter._accounting_gate.hold():
        if current is not registry._ownership or registry._closed:
            _fail()
        registry._lifecycle.invalidate_all(_Reason.LIMITER_REPLACED)
        registry._leases.clear()
        object.__setattr__(registry, "_ownership", replacement)
        object.__setattr__(registry, "_open_epoch", registry._open_epoch + 1)
    return registry


def _poison_after_fork() -> None:
    global _FORK_POISONED
    _FORK_POISONED = True


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_poison_after_fork)


__all__: list[str] = []
