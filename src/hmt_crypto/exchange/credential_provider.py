"""Fail-closed production ownership for selected Delta India credentials."""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from threading import RLock
from typing import TYPE_CHECKING, NamedTuple, NoReturn, Protocol, Self

from pydantic import SecretStr

from hmt_crypto.config import DeltaEnvironment, ExecutionMode, Settings
from hmt_crypto.domain import private_primitives as _primitives
from hmt_crypto.exchange import _credential_validation as _cv
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange._credential_file import _load_selected_credential_file
from hmt_crypto.exchange._credential_lifecycle import (
    _register_signer_state,
    _revoke_signer_state,
    _SignerState,
)
from hmt_crypto.exchange._credential_source import (
    _MAX_API_SECRET_BYTES,
    _selected_india_source,
    _validated_secret,
)
from hmt_crypto.exchange.delta.private_origin import CanonicalPrivateOrigin
from hmt_crypto.exchange.delta.signer import DeltaSigner


class CredentialSourceKind(StrEnum):
    """Supported mutually exclusive sources for one selected credential slot."""

    INLINE = "inline"
    SECRET_FILE = "secret_file"


def _deny(*_args: object, **_kwargs: object) -> NoReturn:
    raise TypeError("Task 2.7 credential-provider authority is unavailable")


_CLASSES_CLOSED = False


class _OpaqueProviderAuthority:
    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("Task 2.7 credential-provider authority is owner-issued")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("Task 2.7 credential-provider authority cannot be subclassed")

    __setattr__ = _deny
    __delattr__ = _deny
    __copy__ = _deny
    __deepcopy__ = _deny
    __reduce__ = _deny
    __reduce_ex__ = _deny
    __getstate__ = _deny
    __hash__ = _deny
    __eq__ = _deny

    def __repr__(self) -> str:
        if not _ATTEST_AUTHORITY(self):
            _cv._fail()
        return f"{type(self).__name__}(<redacted>)"

    __str__ = __repr__


class CredentialProvider(_OpaqueProviderAuthority):
    """Opaque owner of exactly one selected India credential source."""

    __slots__ = ()

    @property
    def source_kind(self) -> CredentialSourceKind:
        """Return whether this provider owns inline or file-backed material."""
        result = _PROVIDER_SOURCE_KIND(self)
        if result is None:
            _cv._fail()
        return result

    @property
    def credential_slot(self) -> credentials.IndiaCredentialSlot:
        """Return the exact selected Delta India credential slot."""
        result = _PROVIDER_SLOT(self)
        if result is None:
            _cv._fail()
        return result

    @property
    def fingerprint_hint(self) -> str:
        """Return a diagnostic key hash prefix, never credential material."""
        result = _PROVIDER_FINGERPRINT(self)
        if result is None:
            _cv._fail()
        return result

    @property
    def enrollment_issuer(self) -> Task27EnrollmentCapabilityIssuer:
        """Return this provider's exact sealed enrollment capability issuer."""
        result = _PROVIDER_ISSUER(self)
        if result is None:
            _cv._fail()
        return result

    def reload(self) -> None:
        """Invalidate all prior issuance and reload this provider's exact source."""
        if not _RELOAD_PROVIDER(self):
            _cv._fail()

    def close(self) -> None:
        """Idempotently invalidate issued material and close this provider."""
        if not _CLOSE_PROVIDER(self):
            _cv._fail()


class Task27EnrollmentCapabilityIssuer(_OpaqueProviderAuthority):
    """Exact sealed capability for issuing inert enrollment credential identity."""

    __slots__ = ()

    def issue_enrollment_credential(
        self,
        *,
        snapshot_id: _primitives.ExactUuid,
        credential_version: _primitives.CredentialVersion,
        account_id: _primitives.ExactUuid,
        external_user_id: _primitives.ExternalUserText,
        origin: CanonicalPrivateOrigin,
        mode: ExecutionMode,
        credential_epoch: _primitives.StrictEpoch,
        user_agent: str,
    ) -> tuple[
        credentials.OwnedDeltaSignerSnapshot,
        credentials.Task27EnrollmentCredentialCurrentnessPort,
    ]:
        """Issue one exact signer identity/currentness pair without I/O or time."""
        result = _ISSUE_ENROLLMENT_SAFE(
            self,
            snapshot_id=snapshot_id,
            credential_version=credential_version,
            account_id=account_id,
            external_user_id=external_user_id,
            origin=origin,
            mode=mode,
            credential_epoch=credential_epoch,
            user_agent=user_agent,
        )
        if result is None:
            _cv._fail()
        return result


_CLASSES_CLOSED = True


if TYPE_CHECKING:

    class Task27EnrollmentCapabilityIssuerPort(Protocol):
        def issue_enrollment_credential(
            self,
            *,
            snapshot_id: _primitives.ExactUuid,
            credential_version: _primitives.CredentialVersion,
            account_id: _primitives.ExactUuid,
            external_user_id: _primitives.ExternalUserText,
            origin: CanonicalPrivateOrigin,
            mode: ExecutionMode,
            credential_epoch: _primitives.StrictEpoch,
            user_agent: str,
        ) -> tuple[
            credentials.OwnedDeltaSignerSnapshot,
            credentials.Task27EnrollmentCredentialCurrentnessPort,
        ]: ...


@dataclass(slots=True, repr=False)
class _ProviderState:
    source_kind: CredentialSourceKind
    credential_slot: credentials.IndiaCredentialSlot
    environment: DeltaEnvironment
    mode: ExecutionMode
    credential_file: SecretStr | None
    api_key: SecretStr
    api_secret: SecretStr
    fingerprint_hint: str
    issuer: Task27EnrollmentCapabilityIssuer
    issued: list[_SignerState] = field(default_factory=list)
    ready: bool = True
    closed: bool = False


class _AuthorityRecord(NamedTuple):
    provider: weakref.ReferenceType[CredentialProvider]
    issuer: weakref.ReferenceType[Task27EnrollmentCapabilityIssuer]
    state: _ProviderState


class _IssueOperation(Protocol):
    def __call__(
        self,
        value: object,
        *,
        snapshot_id: object,
        credential_version: object,
        account_id: object,
        external_user_id: object,
        origin: object,
        mode: object,
        credential_epoch: object,
        user_agent: object,
    ) -> (
        tuple[
            credentials.OwnedDeltaSignerSnapshot,
            credentials.Task27EnrollmentCredentialCurrentnessPort,
        ]
        | None
    ): ...


def _forbid_implicit_clock() -> NoReturn:
    _cv._fail()


def _build_authority() -> tuple[
    Callable[[object], CredentialProvider | None],
    Callable[[object], CredentialSourceKind | None],
    Callable[[object], credentials.IndiaCredentialSlot | None],
    Callable[[object], str | None],
    Callable[[object], Task27EnrollmentCapabilityIssuer | None],
    Callable[[object], bool],
    Callable[[object], bool],
    _IssueOperation,
    Callable[[object], bool],
]:
    lock = RLock()
    providers: dict[int, _AuthorityRecord] = {}
    issuers: dict[int, _AuthorityRecord] = {}

    def invalidate(state: _ProviderState) -> None:
        for issued in tuple(state.issued):
            _revoke_signer_state(issued)
        state.issued.clear()

    def provider_state(value: object) -> _ProviderState | None:
        try:
            if type(value) is not CredentialProvider:
                return None
            record = providers.get(id(value))
            if record is None or record.provider() is not value:
                return None
            return record.state
        except Exception:
            return None

    def issuer_state(value: object) -> _ProviderState | None:
        try:
            if type(value) is not Task27EnrollmentCapabilityIssuer:
                return None
            record = issuers.get(id(value))
            if (
                record is None
                or record.issuer() is not value
                or record.state.issuer is not value
                or record.provider() is None
            ):
                return None
            return record.state
        except Exception:
            return None

    def build(value: object) -> CredentialProvider | None:
        try:
            selected = _selected_india_source(value)
            if selected is None:
                return None
            file_backed, slot, environment, mode, path, key, secret = selected
            source = (
                CredentialSourceKind.SECRET_FILE if file_backed else CredentialSourceKind.INLINE
            )
            provider = object.__new__(CredentialProvider)
            issuer = object.__new__(Task27EnrollmentCapabilityIssuer)
            state = _ProviderState(
                source_kind=source,
                credential_slot=slot,
                environment=environment,
                mode=mode,
                credential_file=path,
                api_key=key,
                api_secret=secret,
                fingerprint_hint=sha256(key.get_secret_value().encode("ascii")).hexdigest()[:12],
                issuer=issuer,
            )
            provider_id = id(provider)
            issuer_id = id(issuer)

            def discard_provider(reference: weakref.ReferenceType[CredentialProvider]) -> None:
                try:
                    with lock:
                        record = providers.get(provider_id)
                        if record is not None and record.provider is reference:
                            invalidate(record.state)
                            record.state.ready = False
                            record.state.closed = True
                            record.state.credential_file = None
                            record.state.api_key = SecretStr("")
                            record.state.api_secret = SecretStr("")
                            providers.pop(provider_id, None)
                            issuers.pop(issuer_id, None)
                except Exception:
                    pass

            provider_reference = weakref.ref(provider, discard_provider)
            issuer_reference = weakref.ref(issuer)
            record = _AuthorityRecord(provider_reference, issuer_reference, state)
            with lock:
                providers[provider_id] = record
                issuers[issuer_id] = record
            return provider
        except Exception:
            return None

    def source_kind(value: object) -> CredentialSourceKind | None:
        try:
            with lock:
                state = provider_state(value)
                return None if state is None or state.closed else state.source_kind
        except Exception:
            return None

    def slot(value: object) -> credentials.IndiaCredentialSlot | None:
        try:
            with lock:
                state = provider_state(value)
                return None if state is None or state.closed else state.credential_slot
        except Exception:
            return None

    def fingerprint(value: object) -> str | None:
        try:
            with lock:
                state = provider_state(value)
                if state is None or state.closed or not state.ready:
                    return None
                return state.fingerprint_hint
        except Exception:
            return None

    def provider_issuer(value: object) -> Task27EnrollmentCapabilityIssuer | None:
        try:
            with lock:
                state = provider_state(value)
                if state is None or state.closed or issuer_state(state.issuer) is not state:
                    return None
                return state.issuer
        except Exception:
            return None

    def reload(value: object) -> bool:
        state: _ProviderState | None = None
        try:
            with lock:
                state = provider_state(value)
                if state is None or state.closed:
                    return False
                invalidate(state)
                state.ready = False
                key: SecretStr | None
                secret: SecretStr | None
                if state.source_kind is CredentialSourceKind.SECRET_FILE:
                    state.api_key = SecretStr("")
                    state.api_secret = SecretStr("")
                    loaded = _load_selected_credential_file(state.credential_file)
                    if loaded is None:
                        return False
                    key, secret = loaded
                else:
                    key = _validated_secret(state.api_key, maximum=256)
                    secret = _validated_secret(state.api_secret, maximum=_MAX_API_SECRET_BYTES)
                if key is None or secret is None:
                    state.api_key = SecretStr("")
                    state.api_secret = SecretStr("")
                    return False
                state.api_key = key
                state.api_secret = secret
                state.fingerprint_hint = sha256(key.get_secret_value().encode("ascii")).hexdigest()[
                    :12
                ]
                state.ready = True
                return True
        except Exception:
            if state is not None:
                state.ready = False
                state.api_key = SecretStr("")
                state.api_secret = SecretStr("")
            return False

    def close(value: object) -> bool:
        try:
            with lock:
                state = provider_state(value)
                if state is None:
                    return False
                if not state.closed:
                    invalidate(state)
                    state.ready = False
                    state.closed = True
                    state.credential_file = None
                    state.api_key = SecretStr("")
                    state.api_secret = SecretStr("")
                return True
        except Exception:
            return False

    def issue(
        value: object,
        *,
        snapshot_id: object,
        credential_version: object,
        account_id: object,
        external_user_id: object,
        origin: object,
        mode: object,
        credential_epoch: object,
        user_agent: object,
    ) -> (
        tuple[
            credentials.OwnedDeltaSignerSnapshot,
            credentials.Task27EnrollmentCredentialCurrentnessPort,
        ]
        | None
    ):
        signer_state: _SignerState | None = None
        try:
            with lock:
                state = issuer_state(value)
                if state is None or state.closed or not state.ready or type(user_agent) is not str:
                    return None
                checked_snapshot_id = _cv._exact_uuid(snapshot_id)
                checked_version = _cv._exact_int(credential_version)
                checked_account_id = _cv._exact_uuid(account_id)
                checked_user = _cv._external_user(external_user_id)
                canonical_origin, origin_environment = _cv._exact_origin(origin)
                checked_mode = _cv._exact_mode(mode, state.environment)
                checked_epoch = _cv._exact_int(credential_epoch)
                if origin_environment is not state.environment or checked_mode is not state.mode:
                    _cv._fail()
                signer = DeltaSigner(
                    api_key=state.api_key,
                    api_secret=state.api_secret,
                    user_agent=user_agent,
                    clock=_forbid_implicit_clock,
                )
                snapshot = object.__new__(credentials.OwnedDeltaSignerSnapshot)
                enrollment = object.__new__(credentials.Task27EnrollmentCredentialCurrentnessPort)
                signer_state = _SignerState(
                    snapshot=snapshot,
                    enrollment=enrollment,
                    signer=signer,
                    snapshot_id=checked_snapshot_id,
                    credential_slot=state.credential_slot,
                    credential_version=checked_version,
                    fingerprint_hint=state.fingerprint_hint,
                    account_id=checked_account_id,
                    external_user_id=checked_user,
                    origin=canonical_origin,
                    environment=state.environment,
                    mode=checked_mode,
                    credential_epoch=checked_epoch,
                    current_epoch=checked_epoch,
                )
                if not _register_signer_state(signer_state):
                    return None
                state.issued.append(signer_state)
                return snapshot, enrollment
        except Exception:
            if signer_state is not None:
                try:
                    _revoke_signer_state(signer_state)
                except Exception:
                    pass
            return None

    def attest(value: object) -> bool:
        try:
            with lock:
                if type(value) is CredentialProvider:
                    return provider_state(value) is not None
                if type(value) is Task27EnrollmentCapabilityIssuer:
                    return issuer_state(value) is not None
                return False
        except Exception:
            return False

    return build, source_kind, slot, fingerprint, provider_issuer, reload, close, issue, attest


(
    _BUILD_PROVIDER,
    _PROVIDER_SOURCE_KIND,
    _PROVIDER_SLOT,
    _PROVIDER_FINGERPRINT,
    _PROVIDER_ISSUER,
    _RELOAD_PROVIDER,
    _CLOSE_PROVIDER,
    _ISSUE_ENROLLMENT_SAFE,
    _ATTEST_AUTHORITY,
) = _build_authority()


def _ISSUE_ENROLLMENT(
    value: object,
    *,
    snapshot_id: object,
    credential_version: object,
    account_id: object,
    external_user_id: object,
    origin: object,
    mode: object,
    credential_epoch: object,
    user_agent: object,
) -> tuple[
    credentials.OwnedDeltaSignerSnapshot,
    credentials.Task27EnrollmentCredentialCurrentnessPort,
]:
    result = _ISSUE_ENROLLMENT_SAFE(
        value,
        snapshot_id=snapshot_id,
        credential_version=credential_version,
        account_id=account_id,
        external_user_id=external_user_id,
        origin=origin,
        mode=mode,
        credential_epoch=credential_epoch,
        user_agent=user_agent,
    )
    if result is None:
        _cv._fail()
    return result


def build_credential_provider(settings: Settings) -> CredentialProvider:
    """Build the exact production provider for one validated India slot."""
    result = _BUILD_PROVIDER(settings)
    del settings
    if result is None:
        _cv._fail()
    return result


__all__ = [
    "CredentialProvider",
    "CredentialSourceKind",
    "Task27EnrollmentCapabilityIssuer",
    "build_credential_provider",
]
