"""Sealed worker/account/egress authority for one private WebSocket supervisor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn, Self, SupportsIndex
from weakref import WeakKeyDictionary

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.private_ws_auth import (
    PrivateWsAuthIssuer,
    _attest_private_ws_auth_currentness,
    require_india_testnet_private_endpoint,
)
from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitScope, ScopeKind
from hmt_crypto.exchange.delta.websocket_contracts import WsEndpoint
from hmt_crypto.exchange.delta.websocket_transport import (
    DeltaWebsocketsConnector,
    _is_owned_physical_connector,
)
from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor
from hmt_crypto.market_data.reconnect_policy import ReconnectPolicy
from hmt_crypto.worker.leadership import (
    Task210PrivateWorkerCurrentnessPort,
    Task210SinglePrivateWorkerProof,
)


def _error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "configure_delta_private_ws_connection"})


def _fail() -> NoReturn:
    raise _error() from None


@dataclass(slots=True)
class _ConnectionState:
    connector: DeltaWebsocketsConnector
    attempts: ConnectionAttemptGovernor
    auth: PrivateWsAuthIssuer
    endpoint: WsEndpoint
    scope: RateLimitScope
    worker: Task210SinglePrivateWorkerProof
    worker_currentness: Task210PrivateWorkerCurrentnessPort
    policy: ReconnectPolicy


_STATES: WeakKeyDictionary[PrivateWsConnectionAuthority, _ConnectionState]
_CLASSES_CLOSED = False


class PrivateWsConnectionAuthority:
    """Opaque proof that socket, attempt, worker, clock, account and endpoint agree."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private WebSocket connection authority cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("private WebSocket connection authority cannot be subclassed")

    def __copy__(self) -> NoReturn:
        raise TypeError("private WebSocket connection authority cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("private WebSocket connection authority cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private WebSocket connection authority cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private WebSocket connection authority cannot be serialized")

    def __repr__(self) -> str:
        return "PrivateWsConnectionAuthority(<redacted>)"

    __str__ = __repr__


_CLASSES_CLOSED = True
_STATES = WeakKeyDictionary()


def _state(value: object) -> _ConnectionState:
    if type(value) is not PrivateWsConnectionAuthority:
        _fail()
    state = _STATES.get(value)
    if state is None:
        _fail()
    return state


def _revalidate(state: _ConnectionState) -> None:
    auth_state = _attest_private_ws_auth_currentness(state.auth)
    account = auth_state.account
    worker = state.worker_currentness.revalidate_single_private_worker_nonblocking(
        state.worker,
        expected_external_user_id=account.external_user_id,
        expected_region=DeltaRegion.INDIA,
        expected_environment=DeltaEnvironment.TESTNET,
        expected_process_id=auth_state.process_id,
        expected_process_incarnation_id=auth_state.process_incarnation_id,
    )
    if worker is not state.worker:
        _fail()
    rebound = ConnectionAttemptGovernor.for_single_process_egress(
        state.scope,
        ownership_proof=state.worker,
        ownership_currentness=state.worker_currentness,
        policy=state.policy,
    )
    if rebound is not state.attempts:
        _fail()


def bind_private_ws_connection_authority(
    *,
    connector: DeltaWebsocketsConnector,
    attempts: ConnectionAttemptGovernor,
    auth: PrivateWsAuthIssuer,
    egress_scope: RateLimitScope,
    worker_proof: Task210SinglePrivateWorkerProof,
    worker_currentness: Task210PrivateWorkerCurrentnessPort,
    reconnect_policy: ReconnectPolicy,
) -> PrivateWsConnectionAuthority:
    """Bind and revalidate the exact process owner used for every physical open."""
    try:
        if (
            type(connector) is not DeltaWebsocketsConnector
            or not _is_owned_physical_connector(connector)
            or type(attempts) is not ConnectionAttemptGovernor
            or type(auth) is not PrivateWsAuthIssuer
            or type(egress_scope) is not RateLimitScope
            or egress_scope.kind is not ScopeKind.IP
            or type(worker_proof) is not Task210SinglePrivateWorkerProof
            or type(worker_currentness) is not Task210PrivateWorkerCurrentnessPort
            or type(reconnect_policy) is not ReconnectPolicy
        ):
            _fail()
        endpoint = require_india_testnet_private_endpoint(auth.endpoint)
        if (
            egress_scope.region != endpoint.region
            or egress_scope.environment != endpoint.environment
            or attempts.region != endpoint.region
            or attempts.environment != endpoint.environment
        ):
            _fail()
        authority = object.__new__(PrivateWsConnectionAuthority)
        state = _ConnectionState(
            connector=connector,
            attempts=attempts,
            auth=auth,
            endpoint=endpoint,
            scope=egress_scope,
            worker=worker_proof,
            worker_currentness=worker_currentness,
            policy=reconnect_policy,
        )
        _revalidate(state)
    except Exception:
        _fail()
    _STATES[authority] = state
    return authority


def _attest_private_ws_connection(
    value: object,
) -> tuple[
    DeltaWebsocketsConnector,
    ConnectionAttemptGovernor,
    PrivateWsAuthIssuer,
    WsEndpoint,
    ReconnectPolicy,
]:
    state = _state(value)
    try:
        _revalidate(state)
        endpoint = require_india_testnet_private_endpoint(state.endpoint)
    except Exception:
        _fail()
    return state.connector, state.attempts, state.auth, endpoint, state.policy


__all__ = ["PrivateWsConnectionAuthority", "bind_private_ws_connection_authority"]
