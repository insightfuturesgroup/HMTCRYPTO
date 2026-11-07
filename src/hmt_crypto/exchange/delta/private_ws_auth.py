"""Sealed Delta India testnet private-WebSocket authentication authority."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn, Self, SupportsIndex
from uuid import UUID
from weakref import WeakKeyDictionary

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain import private_primitives as primitives
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import (
    AuthError,
    ConfigurationError,
    ExchangeDownError,
    ExchangeProtocolError,
    SignatureExpiredError,
    UnauthorizedApiAccessError,
)
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin
from hmt_crypto.exchange.delta.private_read_clock import PrivateReadTrustedInstantIssuer
from hmt_crypto.exchange.delta.websocket_codec import WsNumberToken
from hmt_crypto.exchange.delta.websocket_contracts import (
    WsEndpoint,
    WsPlane,
    validate_ws_endpoint_for_transport,
)
from hmt_crypto.worker import clock as trusted_clock

_TESTNET_PRIVATE_URL = "wss://socket-ind.testnet.deltaex.org"


def _configuration_error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "configure_delta_private_ws_auth"})


def _protocol_error() -> ExchangeProtocolError:
    return ExchangeProtocolError(
        context={"operation": "parse_delta_private_ws_auth", "scope": "private"}
    )


def _fail_configuration() -> NoReturn:
    raise _configuration_error() from None


def require_india_testnet_private_endpoint(endpoint: object) -> WsEndpoint:
    """Admit only the exact documented Delta India testnet private endpoint."""
    try:
        if type(endpoint) is not WsEndpoint:
            _fail_configuration()
        checked = validate_ws_endpoint_for_transport(endpoint)
        if (
            checked.plane is not WsPlane.PRIVATE
            or checked.region != "india"
            or checked.environment != "testnet"
            or checked.url != _TESTNET_PRIVATE_URL
        ):
            _fail_configuration()
        return checked
    except ConfigurationError:
        _fail_configuration()


def _same_account(left: AccountRef, right: AccountRef) -> bool:
    return (
        left.account_id == right.account_id
        and left.external_user_id == right.external_user_id
        and left.region is right.region
        and left.environment is right.environment
        and left.mode is right.mode
        and left.credential_version == right.credential_version
    )


@dataclass(slots=True)
class _AuthIssuerState:
    endpoint: WsEndpoint
    account: AccountRef
    signer: credentials.OwnedDeltaSignerSnapshot
    proof: credentials.AccountBindingProofSnapshot
    credential_currentness: credentials.Task27PrivateCredentialCurrentnessPort
    clock: trusted_clock.Task210TrustedDispatchClock
    clock_currentness: trusted_clock.Task210TrustedClockCurrentnessPort
    instant_issuer: PrivateReadTrustedInstantIssuer
    process_id: int
    process_incarnation_id: UUID


_ISSUER_STATES: WeakKeyDictionary[PrivateWsAuthIssuer, _AuthIssuerState]
_CLASSES_CLOSED = False


class _OpaqueAuthIssuer:
    __slots__ = ()

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private WebSocket auth issuer cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("private WebSocket auth issuer cannot be subclassed")

    def __copy__(self) -> NoReturn:
        raise TypeError("private WebSocket auth issuer cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("private WebSocket auth issuer cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private WebSocket auth issuer cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private WebSocket auth issuer cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("private WebSocket auth issuer cannot be serialized")

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("private WebSocket auth issuer is immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("private WebSocket auth issuer is immutable")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"

    __str__ = __repr__


def _issuer_state(value: object) -> _AuthIssuerState:
    if type(value) is not PrivateWsAuthIssuer:
        _fail_configuration()
    state = _ISSUER_STATES.get(value)
    if state is None:
        _fail_configuration()
    return state


def _attest_private_ws_auth_currentness(value: object) -> _AuthIssuerState:
    """Revalidate sealed credential, account, endpoint and clock without signing."""
    state = _issuer_state(value)
    try:
        require_india_testnet_private_endpoint(state.endpoint)
        returned = state.credential_currentness.revalidate_private_read_nonblocking(
            signer=state.signer,
            proof=state.proof,
            expected_account=state.account,
            expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
            expected_credential_version=state.account.credential_version,
        )
        if returned is not state.proof:
            _fail_configuration()
        state.instant_issuer.attest_association_nonblocking(
            clock=state.clock,
            clock_currentness=state.clock_currentness,
            expected_process_id=state.process_id,
            expected_process_incarnation_id=state.process_incarnation_id,
        )
    except Exception:
        _fail_configuration()
    return state


class PrivateWsAuthIssuer(_OpaqueAuthIssuer):
    """Issue one fresh account-bound auth frame from sealed credential/clock state."""

    __slots__ = ("__weakref__",)

    @property
    def account(self) -> AccountRef:
        """Return the exact bound private account as a defensive model copy."""
        return _issuer_state(self).account.model_copy()

    @property
    def endpoint(self) -> WsEndpoint:
        """Return the exact admitted testnet endpoint."""
        state = _issuer_state(self)
        return WsEndpoint(
            plane=state.endpoint.plane,
            url=state.endpoint.url,
            region=state.endpoint.region,
            environment=state.endpoint.environment,
        )

    def encode_fresh_auth(self) -> str:
        """Revalidate identity/clock, consume one instant, and encode immediately."""
        state = _attest_private_ws_auth_currentness(self)
        instant = state.instant_issuer.issue_fresh_nonblocking(
            clock=state.clock,
            clock_currentness=state.clock_currentness,
            expected_process_id=state.process_id,
            expected_process_incarnation_id=state.process_incarnation_id,
        )
        signer_state = credentials._signer_state(state.signer)
        with signer_state.lifecycle_lock:
            credentials._require_signer(signer_state)
            signed = signer_state.signer.sign_at("GET", instant.utc_epoch_seconds, "/live")
        raw = signed.as_headers()
        return json.dumps(
            {
                "type": "key-auth",
                "payload": {
                    "api-key": raw["api-key"],
                    "timestamp": instant.utc_epoch_seconds,
                    "signature": raw["signature"],
                },
            },
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )

    def invalidate_signature_expired(self, *, correlation_id: UUID) -> None:
        """Revoke both account proof and trusted clock after a venue expiry signal."""
        state = _issuer_state(self)
        checked = primitives._exact_uuid(correlation_id)
        state.credential_currentness.invalidate_signature_expired_nonblocking(
            signer=state.signer,
            proof=state.proof,
            correlation_id=checked,
        )
        state.clock_currentness.invalidate_signature_expired_nonblocking(
            state.clock,
            correlation_id=checked,
        )


_CLASSES_CLOSED = True
_ISSUER_STATES = WeakKeyDictionary()


def bind_private_ws_auth_issuer(
    *,
    endpoint: WsEndpoint,
    account: AccountRef,
    signer: credentials.OwnedDeltaSignerSnapshot,
    account_binding: credentials.AccountBindingProofSnapshot,
    credential_currentness: credentials.Task27PrivateCredentialCurrentnessPort,
    clock: trusted_clock.Task210TrustedDispatchClock,
    clock_currentness: trusted_clock.Task210TrustedClockCurrentnessPort,
    trusted_instant_issuer: PrivateReadTrustedInstantIssuer,
) -> PrivateWsAuthIssuer:
    """Bind exact existing credential, account-proof and trusted-clock authorities."""
    try:
        checked_endpoint = require_india_testnet_private_endpoint(endpoint)
        if (
            type(account) is not AccountRef
            or type(signer) is not credentials.OwnedDeltaSignerSnapshot
            or type(account_binding) is not credentials.AccountBindingProofSnapshot
            or type(credential_currentness)
            is not credentials.Task27PrivateCredentialCurrentnessPort
            or type(clock) is not trusted_clock.Task210TrustedDispatchClock
            or type(clock_currentness) is not trusted_clock.Task210TrustedClockCurrentnessPort
            or type(trusted_instant_issuer) is not PrivateReadTrustedInstantIssuer
        ):
            _fail_configuration()
        owned = AccountRef.model_validate(account).model_copy()
        if (
            owned.region is not DeltaRegion.INDIA
            or owned.environment is not DeltaEnvironment.TESTNET
            or owned.mode not in {ExecutionMode.PAPER, ExecutionMode.LIVE}
            or not _same_account(owned, account_binding.account)
            or signer.credential_slot is not credentials.IndiaCredentialSlot.INDIA_TESTNET
            or signer.credential_version != owned.credential_version
            or account_binding.credential_slot is not credentials.IndiaCredentialSlot.INDIA_TESTNET
        ):
            _fail_configuration()
        returned = credential_currentness.revalidate_private_read_nonblocking(
            signer=signer,
            proof=account_binding,
            expected_account=owned,
            expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
            expected_credential_version=owned.credential_version,
        )
        if returned is not account_binding:
            _fail_configuration()
        process_id = clock.process_id
        incarnation = clock.process_incarnation_id
        trusted_instant_issuer.attest_association_nonblocking(
            clock=clock,
            clock_currentness=clock_currentness,
            expected_process_id=process_id,
            expected_process_incarnation_id=incarnation,
        )
    except Exception:
        _fail_configuration()
    issuer = object.__new__(PrivateWsAuthIssuer)
    _ISSUER_STATES[issuer] = _AuthIssuerState(
        endpoint=checked_endpoint,
        account=owned,
        signer=signer,
        proof=account_binding,
        credential_currentness=credential_currentness,
        clock=clock,
        clock_currentness=clock_currentness,
        instant_issuer=trusted_instant_issuer,
        process_id=process_id,
        process_incarnation_id=incarnation,
    )
    return issuer


class PrivateWsAuthStatus(StrEnum):
    """Closed documented key-auth outcomes."""

    AUTHENTICATED = "authenticated"
    INCOMPLETE_PAYLOAD = "incomplete_payload"
    REQUEST_EXPIRED = "request_expired"
    API_KEY_NOT_FOUND = "api_key_not_found"
    INVALID_SIGNATURE = "invalid_signature"
    IP_NOT_WHITELISTED = "ip_not_whitelisted"
    INTERNAL_SERVER_ERROR = "internal_server_error"


@dataclass(frozen=True, slots=True)
class PrivateWsAuthResult:
    """Sanitized authentication result with no venue message retention."""

    status: PrivateWsAuthStatus
    success: bool
    status_code: int

    def __post_init__(self) -> None:
        expected = self.status is PrivateWsAuthStatus.AUTHENTICATED
        if (
            type(self.status) is not PrivateWsAuthStatus
            or type(self.success) is not bool
            or self.success is not expected
            or type(self.status_code) is not int
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("invalid private WebSocket authentication result")


_AUTH_STATUS_CODES = {
    PrivateWsAuthStatus.AUTHENTICATED: 200,
    PrivateWsAuthStatus.INCOMPLETE_PAYLOAD: 400,
    PrivateWsAuthStatus.REQUEST_EXPIRED: 408,
    PrivateWsAuthStatus.API_KEY_NOT_FOUND: 404,
    PrivateWsAuthStatus.INVALID_SIGNATURE: 401,
    PrivateWsAuthStatus.IP_NOT_WHITELISTED: 401,
    PrivateWsAuthStatus.INTERNAL_SERVER_ERROR: 500,
}


def _wire_integer(value: object) -> int:
    if type(value) is not WsNumberToken or value.value != value.value.to_integral_value():
        raise ValueError("wire integer")
    return int(value.value)


def parse_private_ws_auth_response(message: object) -> PrivateWsAuthResult:
    """Parse only the current documented key-auth success/error response shapes."""
    try:
        if type(message) is not dict:
            raise ValueError
        success = message.get("success")
        expected_keys = {"type", "success", "status_code", "status"}
        if success is False:
            expected_keys.add("message")
        if set(message) != expected_keys or message.get("type") != "key-auth":
            raise ValueError
        if type(success) is not bool or type(message.get("status")) is not str:
            raise ValueError
        status = PrivateWsAuthStatus(message["status"])
        code = _wire_integer(message["status_code"])
        if code != _AUTH_STATUS_CODES[status] or success is not (
            status is PrivateWsAuthStatus.AUTHENTICATED
        ):
            raise ValueError
        if not success:
            venue_message = message.get("message")
            if type(venue_message) is not str or not venue_message or len(venue_message) > 512:
                raise ValueError
        return PrivateWsAuthResult(status=status, success=success, status_code=code)
    except (ValueError, TypeError):
        raise _protocol_error() from None


def raise_for_private_ws_auth(result: PrivateWsAuthResult) -> None:
    """Map a documented auth rejection to one stable redacted application error."""
    if type(result) is not PrivateWsAuthResult:
        raise _protocol_error()
    if result.success:
        return
    context = {
        "operation": "authenticate_delta_private_ws",
        "scope": "private",
        "status_code": result.status_code,
        "state": result.status.value,
    }
    if result.status is PrivateWsAuthStatus.REQUEST_EXPIRED:
        raise SignatureExpiredError(context=context)
    if result.status is PrivateWsAuthStatus.IP_NOT_WHITELISTED:
        raise UnauthorizedApiAccessError(context=context)
    if result.status is PrivateWsAuthStatus.INTERNAL_SERVER_ERROR:
        raise ExchangeDownError(context=context)
    if result.status in {
        PrivateWsAuthStatus.INCOMPLETE_PAYLOAD,
        PrivateWsAuthStatus.API_KEY_NOT_FOUND,
        PrivateWsAuthStatus.INVALID_SIGNATURE,
    }:
        raise AuthError(context=context)
    raise _protocol_error()


__all__ = [
    "PrivateWsAuthIssuer",
    "PrivateWsAuthResult",
    "PrivateWsAuthStatus",
    "bind_private_ws_auth_issuer",
    "parse_private_ws_auth_response",
    "raise_for_private_ws_auth",
    "require_india_testnet_private_endpoint",
]
