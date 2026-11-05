"""Sealed Task 2.7 Delta India signer and account-binding snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Annotated, NoReturn, Self, SupportsIndex, cast
from uuid import UUID

from pydantic import SecretStr

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain import private_primitives as _primitives
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.exchange import _credential_validation as _cv
from hmt_crypto.exchange.delta.private_origin import CanonicalPrivateOrigin
from hmt_crypto.exchange.delta.private_read_wire import (
    _attest_frozen_private_read,
    _FrozenPrivateReadRequestV1,
)
from hmt_crypto.exchange.delta.signer import DeltaSigner, SignedAuthHeaders

_TEST_FIXTURE_MARKER = object()


class IndiaCredentialSlot(_primitives.ExactStrEnum):
    """Exact non-fallback Delta India credential slots."""

    INDIA_TESTNET = "DELTA_INDIA_TESTNET"
    INDIA_PROD = "DELTA_INDIA_PROD"


ExactIndiaCredentialSlot = Annotated[
    IndiaCredentialSlot,
    _primitives.ExactEnumField(IndiaCredentialSlot),
]
_TESTNET_SLOT = IndiaCredentialSlot.INDIA_TESTNET
_PROD_SLOT = IndiaCredentialSlot.INDIA_PROD


def _slot_environment(value: object) -> DeltaEnvironment:
    return _cv._exact_two_member_environment(value, _TESTNET_SLOT, _PROD_SLOT)


@dataclass(slots=True, repr=False)
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


_SIGNER_STATES: dict[int, _SignerState] = {}
_ENROLLMENT_STATES: dict[int, _SignerState] = {}
_PROOF_STATES: dict[int, _ProofState] = {}
_CURRENTNESS_STATES: dict[int, _ProofState] = {}
_CLASSES_CLOSED = False


class _OpaqueCredentialAuthority:
    __slots__ = ()

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("Task 2.7 credential authority cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("Task 2.7 credential authority cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("Task 2.7 credential authority is immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("Task 2.7 credential authority is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("Task 2.7 credential authority cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("Task 2.7 credential authority cannot be copied")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("Task 2.7 credential authority cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("Task 2.7 credential authority cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("Task 2.7 credential authority cannot be serialized")

    def __hash__(self) -> NoReturn:
        raise TypeError("Task 2.7 credential authority is equality-free")

    def __eq__(self, _other: object) -> NoReturn:
        raise TypeError("Task 2.7 credential authority is equality-free")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    __str__ = __repr__


def _signer_state(value: object) -> _SignerState:
    if type(value) is not OwnedDeltaSignerSnapshot:
        _cv._fail()
    state = _SIGNER_STATES.get(id(value))
    if state is None or state.snapshot is not value:
        _cv._fail()
    return state


def _proof_state(value: object) -> _ProofState:
    if type(value) is not AccountBindingProofSnapshot:
        _cv._fail()
    state = _PROOF_STATES.get(id(value))
    if state is None or state.proof is not value:
        _cv._fail()
    return state


def _enrollment_state(value: object) -> _SignerState:
    if type(value) is not Task27EnrollmentCredentialCurrentnessPort:
        _cv._fail()
    state = _ENROLLMENT_STATES.get(id(value))
    if state is None or state.enrollment is not value:
        _cv._fail()
    return state


def _currentness_state(value: object) -> _ProofState:
    if type(value) is not Task27PrivateCredentialCurrentnessPort:
        _cv._fail()
    state = _CURRENTNESS_STATES.get(id(value))
    if state is None or state.currentness is not value:
        _cv._fail()
    return state


def _require_signer(state: _SignerState) -> None:
    if not state.active or state.current_epoch != state.credential_epoch:
        _cv._fail()


def _require_owned_signer(value: object, state: _SignerState) -> None:
    if value is not state.snapshot or _signer_state(value) is not state:
        _cv._fail()


def _require_proof(state: _ProofState) -> None:
    _require_signer(state.signer_state)
    if (
        not state.active
        or not state.permission_valid
        or state.current_epoch != state.proof_epoch
        or state.current_observed_at >= state.expires_at
        or state.credential_epoch != state.signer_state.credential_epoch
    ):
        _cv._fail()


class OwnedDeltaSignerSnapshot(_OpaqueCredentialAuthority):
    """Sealed owner-issued signer identity without a credential accessor."""

    __slots__ = ()

    @property
    def snapshot_id(self) -> UUID:
        """Return the snapshot UUID as a defensive copy."""
        return _cv._exact_uuid(_signer_state(self).snapshot_id)

    @property
    def credential_slot(self) -> IndiaCredentialSlot:
        """Return the exact selected India credential slot."""
        return _signer_state(self).credential_slot

    @property
    def credential_version(self) -> int:
        """Return the exact selected credential version."""
        return _signer_state(self).credential_version

    @property
    def fingerprint_hint(self) -> str:
        """Return the non-authoritative 12-hex diagnostic hint."""
        return _signer_state(self).fingerprint_hint


class AccountBindingProofSnapshot(_OpaqueCredentialAuthority):
    """Sealed evidence binding one account to one current credential."""

    __slots__ = ()

    @property
    def proof_id(self) -> UUID:
        """Return the proof UUID as a defensive copy."""
        return _cv._exact_uuid(_proof_state(self).proof_id)

    @property
    def account(self) -> AccountRef:
        """Return a validated defensive account snapshot."""
        return _proof_state(self).account.model_copy()

    @property
    def origin(self) -> CanonicalPrivateOrigin:
        """Return the exact canonical private origin identity."""
        return _proof_state(self).origin

    @property
    def credential_slot(self) -> IndiaCredentialSlot:
        """Return the exact selected India credential slot."""
        return _proof_state(self).signer_state.credential_slot

    @property
    def credential_version(self) -> int:
        """Return the exact selected credential version."""
        return _proof_state(self).signer_state.credential_version

    @property
    def binding_ref(self) -> UUID:
        """Return the durable binding reference as a defensive copy."""
        return _cv._exact_uuid(_proof_state(self).binding_ref)

    @property
    def probe_ref(self) -> UUID:
        """Return the durable probe reference as a defensive copy."""
        return _cv._exact_uuid(_proof_state(self).probe_ref)

    @property
    def credential_epoch(self) -> int:
        """Return the credential epoch captured by this proof."""
        return _proof_state(self).credential_epoch

    @property
    def proof_epoch(self) -> int:
        """Return the account-proof epoch captured by this proof."""
        return _proof_state(self).proof_epoch

    @property
    def observed_at(self) -> datetime:
        """Return the exact UTC observation time."""
        return _proof_state(self).observed_at

    @property
    def expires_at(self) -> datetime:
        """Return the exact UTC proof expiry."""
        return _proof_state(self).expires_at


class Task27PrivateCredentialCurrentnessPort(_OpaqueCredentialAuthority):
    """Exact nonblocking owner for one signer/account-proof pair."""

    __slots__ = ()

    def revalidate_private_read_nonblocking(
        self,
        *,
        signer: OwnedDeltaSignerSnapshot,
        proof: AccountBindingProofSnapshot,
        expected_account: AccountRef,
        expected_origin: CanonicalPrivateOrigin,
        expected_slot: IndiaCredentialSlot,
        expected_credential_version: _primitives.CredentialVersion,
    ) -> AccountBindingProofSnapshot:
        """Revalidate and return the same exact current account proof."""
        state = _currentness_state(self)
        _require_owned_signer(signer, state.signer_state)
        if proof is not state.proof or _proof_state(proof) is not state:
            _cv._fail()
        account = _cv._owned_account(expected_account)
        origin, environment = _cv._exact_origin(expected_origin)
        version = _cv._exact_int(expected_credential_version)
        slot_environment = _slot_environment(expected_slot)
        record = state.signer_state
        if (
            not _cv._same_account(account, state.account)
            or origin is not record.origin
            or environment is not record.environment
            or expected_slot is not record.credential_slot
            or slot_environment is not record.environment
            or version != record.credential_version
        ):
            _cv._fail()
        _require_proof(state)
        return proof

    def invalidate_signature_expired_nonblocking(
        self,
        *,
        signer: OwnedDeltaSignerSnapshot,
        proof: AccountBindingProofSnapshot,
        correlation_id: _primitives.ExactUuid,
    ) -> None:
        """Publish terminal signature-expiry invalidation for this pair."""
        state = _currentness_state(self)
        _require_owned_signer(signer, state.signer_state)
        if proof is not state.proof or _proof_state(proof) is not state:
            _cv._fail()
        _cv._exact_uuid(correlation_id)
        _require_proof(state)
        state.signer_state.active = False
        state.active = False


class Task27EnrollmentCredentialCurrentnessPort(_OpaqueCredentialAuthority):
    """Exact nonblocking owner for one first-enrollment signer snapshot."""

    __slots__ = ()

    def revalidate_enrollment_credential_nonblocking(
        self,
        *,
        signer: OwnedDeltaSignerSnapshot,
        expected_account_id: _primitives.ExactUuid,
        expected_external_user_id: _primitives.ExternalUserText,
        expected_origin: CanonicalPrivateOrigin,
        expected_slot: IndiaCredentialSlot,
        expected_credential_version: _primitives.CredentialVersion,
    ) -> OwnedDeltaSignerSnapshot:
        """Revalidate and return the same exact current signer snapshot."""
        state = _enrollment_state(self)
        _require_owned_signer(signer, state)
        account_id = _cv._exact_uuid(expected_account_id)
        user = _cv._external_user(expected_external_user_id)
        origin, environment = _cv._exact_origin(expected_origin)
        version = _cv._exact_int(expected_credential_version)
        slot_environment = _slot_environment(expected_slot)
        record = state
        if (
            account_id != record.account_id
            or user != record.external_user_id
            or origin is not record.origin
            or environment is not record.environment
            or expected_slot is not record.credential_slot
            or slot_environment is not record.environment
            or version != record.credential_version
        ):
            _cv._fail()
        _require_signer(state)
        return signer

    def invalidate_enrollment_signature_expired_nonblocking(
        self,
        *,
        signer: OwnedDeltaSignerSnapshot,
        expected_account_id: _primitives.ExactUuid,
        correlation_id: _primitives.ExactUuid,
    ) -> None:
        """Publish terminal signature-expiry invalidation for enrollment."""
        state = _enrollment_state(self)
        _require_owned_signer(signer, state)
        if _cv._exact_uuid(expected_account_id) != state.account_id:
            _cv._fail()
        _cv._exact_uuid(correlation_id)
        _require_signer(state)
        state.active = False


_CLASSES_CLOSED = True


def _sign_owned_private_read_at(
    snapshot: OwnedDeltaSignerSnapshot,
    request: _FrozenPrivateReadRequestV1,
    *,
    timestamp: int,
) -> SignedAuthHeaders:
    """Sign one exact A5-attested private read at an explicit timestamp."""
    result: SignedAuthHeaders | None = None
    try:
        state = _signer_state(snapshot)
        _require_signer(state)
        if type(request) is not _FrozenPrivateReadRequestV1:
            _cv._fail()
        attested = _attest_frozen_private_read(request)
        if (
            attested.region is not DeltaRegion.INDIA
            or attested.environment is not state.environment
        ):
            _cv._fail()
        result = state.signer.sign_at(
            attested.method,
            timestamp,
            attested.path,
            query_string=attested.query_string,
            payload=attested.payload,
        )
    except Exception:
        pass
    if type(result) is not SignedAuthHeaders:
        _cv._fail()
    return result


_implicit_clock_forbidden = _cv._fail


def _issue_enrollment_credential_for_tests(
    marker: object,
    *,
    snapshot_id: object,
    credential_slot: object,
    credential_version: object,
    account_id: object,
    external_user_id: object,
    origin: object,
    mode: object,
    credential_epoch: object,
    api_key: object,
    api_secret: object,
    user_agent: object,
) -> tuple[OwnedDeltaSignerSnapshot, Task27EnrollmentCredentialCurrentnessPort]:
    if marker is not _TEST_FIXTURE_MARKER:
        _cv._fail()
    try:
        environment = _slot_environment(credential_slot)
        canonical_origin, origin_environment = _cv._exact_origin(origin)
        if environment is not origin_environment:
            _cv._fail()
        if type(api_key) is not SecretStr or type(api_secret) is not SecretStr:
            _cv._fail()
        signer = DeltaSigner(
            api_key=api_key,
            api_secret=api_secret,
            user_agent=user_agent,  # type: ignore[arg-type]
            clock=_implicit_clock_forbidden,
        )
        hint = sha256(api_key.get_secret_value().encode("ascii")).hexdigest()[:12]
        checked_snapshot_id = _cv._exact_uuid(snapshot_id)
        checked_version = _cv._exact_int(credential_version)
        checked_account_id = _cv._exact_uuid(account_id)
        checked_user = _cv._external_user(external_user_id)
        checked_mode = _cv._exact_mode(mode, environment)
        checked_epoch = _cv._exact_int(credential_epoch)
    except Exception:
        _cv._fail()
    snapshot = object.__new__(OwnedDeltaSignerSnapshot)
    enrollment = object.__new__(Task27EnrollmentCredentialCurrentnessPort)
    state = _SignerState(
        snapshot=snapshot,
        enrollment=enrollment,
        signer=signer,
        snapshot_id=checked_snapshot_id,
        credential_slot=cast(IndiaCredentialSlot, credential_slot),
        credential_version=checked_version,
        fingerprint_hint=hint,
        account_id=checked_account_id,
        external_user_id=checked_user,
        origin=canonical_origin,
        environment=environment,
        mode=checked_mode,
        credential_epoch=checked_epoch,
        current_epoch=checked_epoch,
    )
    _SIGNER_STATES[id(snapshot)] = state
    _ENROLLMENT_STATES[id(enrollment)] = state
    return snapshot, enrollment


def _issue_account_binding_proof_for_tests(
    marker: object,
    signer: object,
    *,
    proof_id: object,
    account: object,
    origin: object,
    binding_ref: object,
    probe_ref: object,
    credential_epoch: object,
    proof_epoch: object,
    observed_at: object,
    expires_at: object,
) -> tuple[AccountBindingProofSnapshot, Task27PrivateCredentialCurrentnessPort]:
    if marker is not _TEST_FIXTURE_MARKER:
        _cv._fail()
    state = _signer_state(signer)
    _require_signer(state)
    owned_account = _cv._owned_account(account)
    canonical_origin, environment = _cv._exact_origin(origin)
    observed = _cv._exact_utc(observed_at)
    expires = _cv._exact_utc(expires_at)
    epoch = _cv._exact_int(credential_epoch)
    if (
        owned_account.account_id != state.account_id
        or owned_account.external_user_id != state.external_user_id
        or owned_account.region is not DeltaRegion.INDIA
        or owned_account.environment is not state.environment
        or owned_account.mode is not state.mode
        or owned_account.credential_version != state.credential_version
        or canonical_origin is not state.origin
        or environment is not state.environment
        or epoch != state.credential_epoch
        or observed >= expires
    ):
        _cv._fail()
    checked_proof_epoch = _cv._exact_int(proof_epoch)
    proof = object.__new__(AccountBindingProofSnapshot)
    currentness = object.__new__(Task27PrivateCredentialCurrentnessPort)
    proof_state = _ProofState(
        proof=proof,
        currentness=currentness,
        signer_state=state,
        proof_id=_cv._exact_uuid(proof_id),
        account=owned_account,
        origin=canonical_origin,
        binding_ref=_cv._exact_uuid(binding_ref),
        probe_ref=_cv._exact_uuid(probe_ref),
        credential_epoch=epoch,
        proof_epoch=checked_proof_epoch,
        observed_at=observed,
        expires_at=expires,
        current_observed_at=observed,
        current_epoch=checked_proof_epoch,
    )
    _PROOF_STATES[id(proof)] = proof_state
    _CURRENTNESS_STATES[id(currentness)] = proof_state
    return proof, currentness


def _task27_fixture_marker_for_tests() -> object:
    return _TEST_FIXTURE_MARKER


def _owner_signer_state(currentness: object) -> _SignerState:
    if type(currentness) is Task27PrivateCredentialCurrentnessPort:
        return _currentness_state(currentness).signer_state
    return _enrollment_state(currentness)


def _publish_test_currentness_for_tests(
    marker: object,
    currentness: object,
    publication: object,
    *,
    observed_at: object | None = None,
) -> None:
    if marker is not _TEST_FIXTURE_MARKER:
        _cv._fail()
    if type(publication) is not str:
        _cv._fail()
    if publication == "observed_at":
        state = _currentness_state(currentness)
        _require_proof(state)
        observed = _cv._exact_utc(observed_at)
        if observed < state.current_observed_at:
            _cv._fail()
        state.current_observed_at = observed
    elif publication in {"proof_epoch", "permission_invalid"}:
        state = _currentness_state(currentness)
        _require_proof(state)
        if publication == "proof_epoch":
            state.current_epoch += 1
        else:
            state.permission_valid = False
    elif publication in {"credential_epoch", "revoked"}:
        signer_state = _owner_signer_state(currentness)
        _require_signer(signer_state)
        if publication == "credential_epoch":
            signer_state.current_epoch += 1
        else:
            signer_state.active = False
    else:
        _cv._fail()
