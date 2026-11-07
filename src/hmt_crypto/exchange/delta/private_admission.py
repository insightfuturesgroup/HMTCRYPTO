"""Static, sealed, and non-authorizing private-read admission evidence."""

from __future__ import annotations

import weakref
from collections.abc import Callable
from hashlib import sha256
from typing import NamedTuple, NoReturn, Self, cast
from uuid import UUID

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.delta.private_admission_policy import (
    _require_admissible_private_read,
)
from hmt_crypto.exchange.delta.private_limiter_registry import (
    PrivateLimiterLease,
    _revalidate_private_limiter_lease_nonblocking,
)
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.delta.rate_limit_policy import (
    POLICY_VERSION,
    RateLimitScope,
    ScopeKind,
)
from hmt_crypto.worker import clock as trusted_clock
from hmt_crypto.worker import leadership

_EVIDENCE_PREFIX = b"HMT_PRIVATE_READ_ADMISSION_V1\x00"


def _fail() -> NoReturn:
    raise ConfigurationError(context={"operation": "private_read_admission"}) from None


def _uuid_copy(value: UUID) -> UUID:
    if type(value) is not UUID or value.int == 0:
        _fail()
    return UUID(int=value.int)


class _AdmissionState(NamedTuple):
    route_id: PrivateRouteId
    region: DeltaRegion
    environment: DeltaEnvironment
    mode: ExecutionMode
    account_id: UUID
    external_user_id: str
    credential_version: int
    request_evidence_sha256: str
    credential_snapshot_id: UUID
    account_proof_id: UUID
    worker_proof_id: UUID
    process_id: int
    process_incarnation_id: UUID
    clock_owner_id: UUID
    clock_epoch: int
    correlation_epoch: int
    limiter_lease_id: UUID
    registry_owner_id: UUID
    limiter_instance_id: UUID
    policy_version: str
    admission_evidence_sha256: str


def _deny(*_args: object, **_kwargs: object) -> NoReturn:
    raise TypeError("private read admission operation unavailable")


class PrivateReadAdmission:
    """Opaque point-in-time evidence with no sign, send, or quota authority."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private read admissions are owner-issued")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("private read admissions cannot be subclassed")

    __setattr__ = _deny
    __delattr__ = _deny
    __copy__ = _deny
    __deepcopy__ = _deny
    __reduce__ = _deny
    __reduce_ex__ = _deny
    __getstate__ = _deny
    __hash__ = _deny
    __eq__ = _deny

    @property
    def route_id(self) -> PrivateRouteId:
        """Return the exact admitted route identity."""
        return cast(PrivateRouteId, _admission_field(self, "route_id"))

    @property
    def region(self) -> DeltaRegion:
        """Return the exact admitted region."""
        return cast(DeltaRegion, _admission_field(self, "region"))

    @property
    def environment(self) -> DeltaEnvironment:
        """Return the exact admitted environment."""
        return cast(DeltaEnvironment, _admission_field(self, "environment"))

    @property
    def account_id(self) -> UUID:
        """Return a defensive account UUID."""
        return _uuid_copy(cast(UUID, _admission_field(self, "account_id")))

    @property
    def external_user_id(self) -> str:
        """Return the external-user quota identity."""
        return cast(str, _admission_field(self, "external_user_id"))

    @property
    def credential_version(self) -> int:
        """Return the admitted credential version."""
        return cast(int, _admission_field(self, "credential_version"))

    @property
    def request_evidence_sha256(self) -> str:
        """Return the exact A5 request evidence digest."""
        return cast(str, _admission_field(self, "request_evidence_sha256"))

    @property
    def credential_snapshot_id(self) -> UUID:
        """Return a defensive credential-snapshot UUID."""
        return _uuid_copy(cast(UUID, _admission_field(self, "credential_snapshot_id")))

    @property
    def account_proof_id(self) -> UUID:
        """Return a defensive account-proof UUID."""
        return _uuid_copy(cast(UUID, _admission_field(self, "account_proof_id")))

    @property
    def worker_proof_id(self) -> UUID:
        """Return a defensive worker-proof UUID."""
        return _uuid_copy(cast(UUID, _admission_field(self, "worker_proof_id")))

    @property
    def process_id(self) -> int:
        """Return the exact admitted process ID."""
        return cast(int, _admission_field(self, "process_id"))

    @property
    def process_incarnation_id(self) -> UUID:
        """Return a defensive process-incarnation UUID."""
        return _uuid_copy(cast(UUID, _admission_field(self, "process_incarnation_id")))

    @property
    def clock_owner_id(self) -> UUID:
        """Return a defensive trusted-clock owner UUID."""
        return _uuid_copy(cast(UUID, _admission_field(self, "clock_owner_id")))

    @property
    def clock_epoch(self) -> int:
        """Return the trusted clock epoch captured at admission."""
        return cast(int, _admission_field(self, "clock_epoch"))

    @property
    def correlation_epoch(self) -> int:
        """Return the trusted UTC/monotonic correlation epoch."""
        return cast(int, _admission_field(self, "correlation_epoch"))

    @property
    def limiter_lease_id(self) -> UUID:
        """Return a defensive limiter-lease UUID."""
        return _uuid_copy(cast(UUID, _admission_field(self, "limiter_lease_id")))

    @property
    def registry_owner_id(self) -> UUID:
        """Return a defensive registry-owner UUID."""
        return _uuid_copy(cast(UUID, _admission_field(self, "registry_owner_id")))

    @property
    def limiter_instance_id(self) -> UUID:
        """Return a defensive limiter-instance UUID."""
        return _uuid_copy(cast(UUID, _admission_field(self, "limiter_instance_id")))

    @property
    def policy_version(self) -> str:
        """Return the frozen Delta private quota-policy version."""
        return cast(str, _admission_field(self, "policy_version"))

    def __repr__(self) -> str:
        _admission_field(self, "route_id")
        return "PrivateReadAdmission(<redacted>)"

    __str__ = __repr__


def _build_admission_authority() -> tuple[
    Callable[..., PrivateReadAdmission],
    Callable[[object, str], object],
    Callable[[object, object], PrivateReadAdmission],
]:
    states: dict[
        int,
        tuple[weakref.ReferenceType[PrivateReadAdmission], _AdmissionState],
    ] = {}

    def admit(
        *,
        request: object,
        account: object,
        signer: object,
        account_binding: object,
        credential_currentness: object,
        worker_proof: object,
        worker_currentness: object,
        clock: object,
        clock_currentness: object,
        limiter_lease: object,
    ) -> PrivateReadAdmission:
        """Validate static evidence and publish one inert admission atomically."""
        state = _validated_private_read_admission_state(
            request=request,
            account=account,
            signer=signer,
            account_binding=account_binding,
            credential_currentness=credential_currentness,
            worker_proof=worker_proof,
            worker_currentness=worker_currentness,
            clock=clock,
            clock_currentness=clock_currentness,
            limiter_lease=limiter_lease,
        )
        if type(state) is not _AdmissionState:
            _fail()
        admission = object.__new__(PrivateReadAdmission)
        identity = id(admission)

        def discard(reference: weakref.ReferenceType[PrivateReadAdmission]) -> None:
            current = states.get(identity)
            if current is not None and current[0] is reference:
                states.pop(identity, None)

        states[identity] = (weakref.ref(admission, discard), state)
        return admission

    def read(value: object) -> _AdmissionState:
        if type(value) is not PrivateReadAdmission:
            _fail()
        record = states.get(id(value))
        if record is None or record[0]() is not value or type(record[1]) is not _AdmissionState:
            _fail()
        return record[1]

    def field(value: object, name: str) -> object:
        state = read(value)
        if type(name) is not str or name not in _AdmissionState._fields:
            _fail()
        return getattr(state, name)

    def attest(value: object, request: object) -> PrivateReadAdmission:
        state = read(value)
        frozen = _require_admissible_private_read(request)
        digest_values = tuple(
            getattr(state, name)
            for name in _AdmissionState._fields
            if name != "admission_evidence_sha256"
        )
        if (
            state.route_id is not frozen.route_id
            or state.region is not frozen.region
            or state.environment is not frozen.environment
            or state.request_evidence_sha256 != frozen.request_evidence_sha256
            or state.policy_version != POLICY_VERSION
            or state.admission_evidence_sha256 != _admission_digest(*digest_values)
        ):
            _fail()
        return cast(PrivateReadAdmission, value)

    return admit, field, attest


def _digest_component(value: object) -> bytes:
    if type(value) is UUID:
        return value.hex.encode("ascii")
    if type(value) is int:
        return str(value).encode("ascii")
    if type(value) is str:
        return value.encode("ascii")
    if type(value) is PrivateRouteId:
        return value.value.encode("ascii")
    if type(value) is DeltaRegion:
        return value.value.encode("ascii")
    if type(value) is DeltaEnvironment:
        return value.value.encode("ascii")
    if type(value) is ExecutionMode:
        return value.value.encode("ascii")
    _fail()


def _admission_digest(*values: object) -> str:
    digest = sha256(_EVIDENCE_PREFIX)
    for value in values:
        component = _digest_component(value)
        digest.update(len(component).to_bytes(8, "big"))
        digest.update(component)
    return digest.hexdigest()


def _require_exact_inputs(
    *,
    account: object,
    signer: object,
    account_binding: object,
    credential_currentness: object,
    worker_proof: object,
    worker_currentness: object,
    clock: object,
    clock_currentness: object,
    limiter_lease: object,
) -> None:
    expected = (
        (account, AccountRef),
        (signer, credentials.OwnedDeltaSignerSnapshot),
        (account_binding, credentials.AccountBindingProofSnapshot),
        (credential_currentness, credentials.Task27PrivateCredentialCurrentnessPort),
        (worker_proof, leadership.Task210SinglePrivateWorkerProof),
        (worker_currentness, leadership.Task210PrivateWorkerCurrentnessPort),
        (clock, trusted_clock.Task210TrustedDispatchClock),
        (clock_currentness, trusted_clock.Task210TrustedClockCurrentnessPort),
        (limiter_lease, PrivateLimiterLease),
    )
    if any(type(value) is not expected_type for value, expected_type in expected):
        _fail()


def _slot(environment: DeltaEnvironment) -> credentials.IndiaCredentialSlot:
    if environment is DeltaEnvironment.TESTNET:
        return credentials.IndiaCredentialSlot.INDIA_TESTNET
    if environment is DeltaEnvironment.PROD:
        return credentials.IndiaCredentialSlot.INDIA_PROD
    _fail()


def _validated_private_read_admission_state(
    *,
    request: object,
    account: object,
    signer: object,
    account_binding: object,
    credential_currentness: object,
    worker_proof: object,
    worker_currentness: object,
    clock: object,
    clock_currentness: object,
    limiter_lease: object,
) -> _AdmissionState:
    try:
        _require_exact_inputs(
            account=account,
            signer=signer,
            account_binding=account_binding,
            credential_currentness=credential_currentness,
            worker_proof=worker_proof,
            worker_currentness=worker_currentness,
            clock=clock,
            clock_currentness=clock_currentness,
            limiter_lease=limiter_lease,
        )
        owned_signer = cast(credentials.OwnedDeltaSignerSnapshot, signer)
        owned_binding = cast(credentials.AccountBindingProofSnapshot, account_binding)
        owned_credential_currentness = cast(
            credentials.Task27PrivateCredentialCurrentnessPort,
            credential_currentness,
        )
        owned_worker = cast(leadership.Task210SinglePrivateWorkerProof, worker_proof)
        owned_worker_currentness = cast(
            leadership.Task210PrivateWorkerCurrentnessPort,
            worker_currentness,
        )
        owned_clock = cast(trusted_clock.Task210TrustedDispatchClock, clock)
        owned_clock_currentness = cast(
            trusted_clock.Task210TrustedClockCurrentnessPort,
            clock_currentness,
        )
        owned_lease = cast(PrivateLimiterLease, limiter_lease)
        frozen = _require_admissible_private_read(request)
        owned_account = AccountRef.model_validate(account).model_copy()
        if (
            owned_account.region is not DeltaRegion.INDIA
            or frozen.region is not owned_account.region
            or frozen.environment is not owned_account.environment
        ):
            _fail()
        origin = _canonical_private_origin(owned_account.region, owned_account.environment)
        returned_binding = owned_credential_currentness.revalidate_private_read_nonblocking(
            signer=owned_signer,
            proof=owned_binding,
            expected_account=owned_account,
            expected_origin=origin,
            expected_slot=_slot(owned_account.environment),
            expected_credential_version=owned_account.credential_version,
        )
        if returned_binding is not owned_binding:
            _fail()
        process_id = owned_worker.process_id
        incarnation = owned_worker.process_incarnation_id
        returned_worker = owned_worker_currentness.revalidate_single_private_worker_nonblocking(
            owned_worker,
            expected_external_user_id=owned_account.external_user_id,
            expected_region=owned_account.region,
            expected_environment=owned_account.environment,
            expected_process_id=process_id,
            expected_process_incarnation_id=incarnation,
        )
        if returned_worker is not owned_worker:
            _fail()
        returned_clock = owned_clock_currentness.revalidate_dispatch_clock_nonblocking(
            owned_clock,
            expected_process_id=process_id,
            expected_process_incarnation_id=incarnation,
        )
        if returned_clock is not owned_clock:
            _fail()
        scope = RateLimitScope(
            owned_account.region.value,
            owned_account.environment.value,
            ScopeKind.EXTERNAL_USER,
            owned_account.external_user_id,
        )
        returned_lease = _revalidate_private_limiter_lease_nonblocking(
            owned_lease,
            expected_scope=scope,
            expected_process_id=process_id,
            expected_process_incarnation_id=incarnation,
        )
        if (
            returned_lease is not owned_lease
            or object.__getattribute__(
                object.__getattribute__(owned_lease, "_registry"),
                "_ownership",
            )
            is not owned_worker
            or owned_lease.ownership_proof_id != owned_worker.proof_id
            or owned_lease.policy_version != POLICY_VERSION
        ):
            _fail()
        values = (
            frozen.route_id,
            owned_account.region,
            owned_account.environment,
            owned_account.mode,
            owned_account.account_id,
            owned_account.external_user_id,
            owned_account.credential_version,
            frozen.request_evidence_sha256,
            owned_signer.snapshot_id,
            owned_binding.proof_id,
            owned_worker.proof_id,
            process_id,
            incarnation,
            owned_clock.clock_owner_id,
            owned_clock.clock_epoch,
            owned_clock.correlation_epoch,
            owned_lease.lease_id,
            owned_lease.registry_owner_id,
            owned_lease.limiter_instance_id,
            POLICY_VERSION,
        )
        return _AdmissionState(*values, _admission_digest(*values))
    except Exception:
        _fail()


(
    _admit_private_read_nonblocking,
    _admission_field,
    _admission_attester,
) = _build_admission_authority()
del _build_admission_authority


def _attest_private_read_admission(
    value: object,
    *,
    request: object,
) -> PrivateReadAdmission:
    """Reattest inert admission identity against one exact frozen request."""
    try:
        return _admission_attester(value, request)
    except Exception:
        _fail()


__all__: list[str] = []
