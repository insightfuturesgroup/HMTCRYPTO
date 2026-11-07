"""Shared lifecycle state for sealed Delta credential capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import TYPE_CHECKING
from uuid import UUID

from hmt_crypto.config import DeltaEnvironment, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.exchange import _credential_validation as _cv
from hmt_crypto.exchange.delta.private_origin import CanonicalPrivateOrigin
from hmt_crypto.exchange.delta.signer import DeltaSigner

if TYPE_CHECKING:
    from hmt_crypto.exchange.credentials import (
        AccountBindingProofSnapshot,
        IndiaCredentialSlot,
        OwnedDeltaSignerSnapshot,
        Task27EnrollmentCredentialCurrentnessPort,
        Task27PrivateCredentialCurrentnessPort,
    )


@dataclass(slots=True, repr=False, weakref_slot=True)
class _SignerState:
    snapshot: OwnedDeltaSignerSnapshot
    enrollment: Task27EnrollmentCredentialCurrentnessPort
    signer: DeltaSigner
    snapshot_id: UUID
    credential_slot: IndiaCredentialSlot
    credential_version: int
    fingerprint_hint: str
    account_id: UUID
    external_user_id: str
    origin: CanonicalPrivateOrigin
    environment: DeltaEnvironment
    mode: ExecutionMode
    credential_epoch: int
    current_epoch: int
    active: bool = True
    lifecycle_lock: RLock = field(default_factory=RLock, repr=False)


@dataclass(slots=True, repr=False)
class _ProofState:
    proof: AccountBindingProofSnapshot
    currentness: Task27PrivateCredentialCurrentnessPort
    signer_state: _SignerState
    proof_id: UUID
    account: AccountRef
    origin: CanonicalPrivateOrigin
    binding_ref: UUID
    probe_ref: UUID
    credential_epoch: int
    proof_epoch: int
    observed_at: datetime
    expires_at: datetime
    current_observed_at: datetime
    current_epoch: int
    permission_valid: bool = True
    active: bool = True


_REGISTRY_LOCK = RLock()
_SIGNER_STATES: dict[int, _SignerState] = {}
_ENROLLMENT_STATES: dict[int, _SignerState] = {}
_PROOF_STATES: dict[int, _ProofState] = {}
_CURRENTNESS_STATES: dict[int, _ProofState] = {}


def _register_signer_state(state: _SignerState) -> bool:
    """Publish both exact signer identities atomically, or fail closed."""
    with state.lifecycle_lock, _REGISTRY_LOCK:
        snapshot_id = id(state.snapshot)
        enrollment_id = id(state.enrollment)
        if not state.active or snapshot_id in _SIGNER_STATES or enrollment_id in _ENROLLMENT_STATES:
            return False
        _SIGNER_STATES[snapshot_id] = state
        _ENROLLMENT_STATES[enrollment_id] = state
        return True


def _register_proof_state(state: _ProofState) -> bool:
    """Publish both exact proof identities while their signer is current."""
    signer = state.signer_state
    with signer.lifecycle_lock, _REGISTRY_LOCK:
        proof_id = id(state.proof)
        currentness_id = id(state.currentness)
        if (
            not signer.active
            or signer.current_epoch != signer.credential_epoch
            or proof_id in _PROOF_STATES
            or currentness_id in _CURRENTNESS_STATES
        ):
            return False
        _PROOF_STATES[proof_id] = state
        _CURRENTNESS_STATES[currentness_id] = state
        return True


def _revoke_signer_state(state: _SignerState) -> None:
    """Drain in-flight use, revoke descendants, and release registry ownership."""
    with state.lifecycle_lock, _REGISTRY_LOCK:
        state.active = False
        proof_states = {
            id(proof_state): proof_state
            for proof_state in (*_PROOF_STATES.values(), *_CURRENTNESS_STATES.values())
            if proof_state.signer_state is state
        }
        for proof_state in proof_states.values():
            proof_state.active = False
            proof_state.permission_valid = False
            if _PROOF_STATES.get(id(proof_state.proof)) is proof_state:
                _PROOF_STATES.pop(id(proof_state.proof), None)
            if _CURRENTNESS_STATES.get(id(proof_state.currentness)) is proof_state:
                _CURRENTNESS_STATES.pop(id(proof_state.currentness), None)
        if _SIGNER_STATES.get(id(state.snapshot)) is state:
            _SIGNER_STATES.pop(id(state.snapshot), None)
        if _ENROLLMENT_STATES.get(id(state.enrollment)) is state:
            _ENROLLMENT_STATES.pop(id(state.enrollment), None)


def _signer_is_current(state: _SignerState, signer: object) -> bool:
    with _REGISTRY_LOCK:
        return (
            signer is state.snapshot
            and _SIGNER_STATES.get(id(state.snapshot)) is state
            and state.active
            and state.current_epoch == state.credential_epoch
        )


def _proof_is_current(state: _ProofState, proof: object) -> bool:
    with _REGISTRY_LOCK:
        return (
            _signer_is_current(state.signer_state, state.signer_state.snapshot)
            and proof is state.proof
            and _PROOF_STATES.get(id(state.proof)) is state
            and state.active
            and state.permission_valid
            and state.current_epoch == state.proof_epoch
            and state.current_observed_at < state.expires_at
            and state.credential_epoch == state.signer_state.credential_epoch
        )


def _try_revalidate_proof(
    state: _ProofState | None,
    *,
    signer: object,
    proof: object,
    expected_account: object,
    expected_origin: object,
    expected_slot: object,
    expected_credential_version: object,
) -> AccountBindingProofSnapshot | None:
    try:
        if state is None:
            return None
        record = state.signer_state
        with record.lifecycle_lock:
            account = _cv._owned_account(expected_account)
            origin, environment = _cv._exact_origin(expected_origin)
            version = _cv._exact_int(expected_credential_version)
            if (
                not _signer_is_current(record, signer)
                or not _proof_is_current(state, proof)
                or not _cv._same_account(account, state.account)
                or origin is not record.origin
                or environment is not record.environment
                or expected_slot is not record.credential_slot
                or version != record.credential_version
            ):
                return None
            return state.proof
    except Exception:
        return None


def _try_invalidate_proof(
    state: _ProofState | None,
    *,
    signer: object,
    proof: object,
    correlation_id: object,
) -> bool:
    try:
        if state is None:
            return False
        record = state.signer_state
        with record.lifecycle_lock:
            _cv._exact_uuid(correlation_id)
            if not _signer_is_current(record, signer) or not _proof_is_current(state, proof):
                return False
            _revoke_signer_state(record)
            return True
    except Exception:
        return False


def _try_revalidate_enrollment(
    state: _SignerState | None,
    *,
    signer: object,
    expected_account_id: object,
    expected_external_user_id: object,
    expected_origin: object,
    expected_slot: object,
    expected_credential_version: object,
) -> OwnedDeltaSignerSnapshot | None:
    try:
        if state is None:
            return None
        with state.lifecycle_lock:
            account_id = _cv._exact_uuid(expected_account_id)
            user = _cv._external_user(expected_external_user_id)
            origin, environment = _cv._exact_origin(expected_origin)
            version = _cv._exact_int(expected_credential_version)
            if (
                not _signer_is_current(state, signer)
                or account_id != state.account_id
                or user != state.external_user_id
                or origin is not state.origin
                or environment is not state.environment
                or expected_slot is not state.credential_slot
                or version != state.credential_version
            ):
                return None
            return state.snapshot
    except Exception:
        return None


def _try_invalidate_enrollment(
    state: _SignerState | None,
    *,
    signer: object,
    expected_account_id: object,
    correlation_id: object,
) -> bool:
    try:
        if state is None:
            return False
        with state.lifecycle_lock:
            account_id = _cv._exact_uuid(expected_account_id)
            _cv._exact_uuid(correlation_id)
            if not _signer_is_current(state, signer) or account_id != state.account_id:
                return False
            _revoke_signer_state(state)
            return True
    except Exception:
        return False


__all__: list[str] = []
