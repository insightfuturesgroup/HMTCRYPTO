"""Frozen, unsigned Delta India private-read request evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import NoReturn, TypeAlias

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.private_origin import (
    CanonicalPrivateOrigin,
    _canonical_private_origin,
)
from hmt_crypto.exchange.delta.private_query import (
    _canonical_balances_target,
    _canonical_private_read_target,
    _CanonicalPrivateReadTarget,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.delta.rate_limit_policy import POLICY_VERSION
from hmt_crypto.exchange.private_requests import (
    FillQuery,
    LatestPositionQuery,
    LeverageQuery,
    MarginedPositionQuery,
    OpenOrderQuery,
    OrderByClientIdQuery,
    OrderByIdQuery,
    OrderHistoryQuery,
    WalletTransactionQuery,
)

_PrivateReadQuery: TypeAlias = (
    WalletTransactionQuery
    | MarginedPositionQuery
    | LatestPositionQuery
    | OpenOrderQuery
    | OrderHistoryQuery
    | FillQuery
    | OrderByIdQuery
    | OrderByClientIdQuery
    | LeverageQuery
)

_REQUEST_ISSUER = object()
_EVIDENCE_PREFIX = b"HMT_PRIVATE_READ_REQUEST_V1\x00"
_READ_ROUTES = (
    PrivateRouteId.BALANCES,
    PrivateRouteId.TRANSACTIONS,
    PrivateRouteId.MARGINED_POSITIONS,
    PrivateRouteId.LATEST_POSITION,
    PrivateRouteId.OPEN_ORDERS,
    PrivateRouteId.ORDER_HISTORY,
    PrivateRouteId.FILLS,
    PrivateRouteId.ORDER_BY_ID,
    PrivateRouteId.ORDER_BY_CLIENT_ID,
    PrivateRouteId.GET_LEVERAGE,
)


def _wire_error(operation: str) -> ConfigurationError:
    return ConfigurationError(context={"operation": operation})


@dataclass(frozen=True, slots=True, eq=False, init=False, repr=False)
class _FrozenPrivateReadRequestV1:
    """One module-issued immutable request preimage without send authority."""

    _route_id: PrivateRouteId = field(init=False, repr=False)
    _region: DeltaRegion = field(init=False, repr=False)
    _environment: DeltaEnvironment = field(init=False, repr=False)
    _method: str = field(init=False, repr=False)
    _origin: str = field(init=False, repr=False)
    _path: str = field(init=False, repr=False)
    _query_string: bytes = field(init=False, repr=False)
    _payload: bytes = field(init=False, repr=False)
    _request_evidence_sha256: str = field(init=False, repr=False)
    _policy_version: str = field(init=False, repr=False)
    _origin_binding: CanonicalPrivateOrigin = field(init=False, repr=False)
    _target: _CanonicalPrivateReadTarget = field(init=False, repr=False)
    _semantic_query: _PrivateReadQuery | None = field(init=False, repr=False)

    def __init__(
        self,
        *,
        _issuer: object,
        route_id: PrivateRouteId,
        region: DeltaRegion,
        environment: DeltaEnvironment,
        method: str,
        origin: str,
        path: str,
        query_string: bytes,
        payload: bytes,
        request_evidence_sha256: str,
        policy_version: str,
        origin_binding: CanonicalPrivateOrigin,
        target: _CanonicalPrivateReadTarget,
        semantic_query: _PrivateReadQuery | None,
    ) -> None:
        if _issuer is not _REQUEST_ISSUER:
            raise TypeError("frozen private reads are module-issued")
        object.__setattr__(self, "_route_id", route_id)
        object.__setattr__(self, "_region", region)
        object.__setattr__(self, "_environment", environment)
        object.__setattr__(self, "_method", method)
        object.__setattr__(self, "_origin", origin)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_query_string", query_string)
        object.__setattr__(self, "_payload", payload)
        object.__setattr__(self, "_request_evidence_sha256", request_evidence_sha256)
        object.__setattr__(self, "_policy_version", policy_version)
        object.__setattr__(self, "_origin_binding", origin_binding)
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_semantic_query", semantic_query)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del cls, kwargs
        raise TypeError("frozen private reads cannot be subclassed")

    @property
    def route_id(self) -> PrivateRouteId:
        """Return the exact inferred private route identity."""
        return self._route_id

    @property
    def region(self) -> DeltaRegion:
        """Return the exact India region member."""
        return self._region

    @property
    def environment(self) -> DeltaEnvironment:
        """Return the exact production or testnet environment member."""
        return self._environment

    @property
    def method(self) -> str:
        """Return the exact ``GET`` method."""
        return self._method

    @property
    def origin(self) -> str:
        """Return the canonical origin captured for this request."""
        return self._origin

    @property
    def path(self) -> str:
        """Return the canonical method-relative path."""
        return self._path

    @property
    def query_string(self) -> bytes:
        """Return the exact leading-question-mark query bytes."""
        return self._query_string

    @property
    def payload(self) -> bytes:
        """Return the exact empty GET payload."""
        return self._payload

    @property
    def request_evidence_sha256(self) -> str:
        """Return the V1 length-framed evidence digest."""
        return self._request_evidence_sha256

    def __repr__(self) -> str:
        route = object.__getattribute__(self, "_route_id")
        region = object.__getattribute__(self, "_region")
        environment = object.__getattribute__(self, "_environment")
        digest = object.__getattribute__(self, "_request_evidence_sha256")
        route_name = next(
            (candidate.name for candidate in _READ_ROUTES if route is candidate), "INVALID"
        )
        region_name = "INDIA" if region is DeltaRegion.INDIA else "INVALID"
        if environment is DeltaEnvironment.PROD:
            environment_name = "PROD"
        elif environment is DeltaEnvironment.TESTNET:
            environment_name = "TESTNET"
        else:
            environment_name = "INVALID"
        if not _is_digest(digest):
            digest = "INVALID"
        return (
            "_FrozenPrivateReadRequestV1("
            f"route_id={route_name}, region={region_name}, "
            f"environment={environment_name}, request_evidence_sha256={digest!r})"
        )

    __str__ = __repr__

    def __copy__(self) -> NoReturn:
        raise TypeError("frozen private reads cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        raise TypeError("frozen private reads cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("frozen private reads cannot be serialized")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError("frozen private reads cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("frozen private reads cannot be serialized")


def _is_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _clone_query(value: object) -> _PrivateReadQuery:
    value_type = type(value)
    if value_type is WalletTransactionQuery:
        return WalletTransactionQuery.model_validate(value)
    if value_type is MarginedPositionQuery:
        return MarginedPositionQuery.model_validate(value)
    if value_type is LatestPositionQuery:
        return LatestPositionQuery.model_validate(value)
    if value_type is OpenOrderQuery:
        return OpenOrderQuery.model_validate(value)
    if value_type is OrderHistoryQuery:
        return OrderHistoryQuery.model_validate(value)
    if value_type is FillQuery:
        return FillQuery.model_validate(value)
    if value_type is OrderByIdQuery:
        return OrderByIdQuery.model_validate(value)
    if value_type is OrderByClientIdQuery:
        return OrderByClientIdQuery.model_validate(value)
    if value_type is LeverageQuery:
        return LeverageQuery.model_validate(value)
    raise TypeError("unsupported private read query")


def _resolved_origin(region: object, environment: object) -> CanonicalPrivateOrigin:
    binding = _canonical_private_origin(region, environment)  # type: ignore[arg-type]
    if type(binding) is not CanonicalPrivateOrigin:
        raise TypeError("invalid canonical origin")
    bound_region = object.__getattribute__(binding, "_region")
    bound_environment = object.__getattribute__(binding, "_environment")
    bound_value = object.__getattribute__(binding, "_value")
    if not (
        bound_region is region and bound_environment is environment and type(bound_value) is str
    ):
        raise TypeError("invalid canonical origin")
    return binding


def _target_values(target: object) -> tuple[PrivateRouteId, str, bytes]:
    if type(target) is not _CanonicalPrivateReadTarget:
        raise TypeError("invalid canonical target")
    route_id = object.__getattribute__(target, "route_id")
    path = object.__getattribute__(target, "path")
    query_string = object.__getattribute__(target, "query_string")
    if not (
        type(route_id) is PrivateRouteId
        and any(route_id is candidate for candidate in _READ_ROUTES)
        and type(path) is str
        and path.startswith("/v2/")
        and type(query_string) is bytes
        and (query_string == b"" or query_string.startswith(b"?"))
    ):
        raise TypeError("invalid canonical target")
    return route_id, path, query_string


def _evidence_sha256(
    method: str,
    origin: str,
    path: str,
    query_string: bytes,
    payload: bytes,
) -> str:
    digest = sha256()
    digest.update(_EVIDENCE_PREFIX)
    components = (
        method.encode("ascii"),
        origin.encode("ascii"),
        path.encode("ascii"),
        query_string,
        payload,
    )
    for component in components:
        digest.update(len(component).to_bytes(8, "big"))
        digest.update(component)
    return digest.hexdigest()


def _issue_request(
    *,
    region: DeltaRegion,
    environment: DeltaEnvironment,
    origin_binding: CanonicalPrivateOrigin,
    target: _CanonicalPrivateReadTarget,
    semantic_query: _PrivateReadQuery | None,
) -> _FrozenPrivateReadRequestV1:
    route_id, path, query_string = _target_values(target)
    origin = object.__getattribute__(origin_binding, "_value")
    method = "GET"
    payload = b""
    digest = _evidence_sha256(method, origin, path, query_string, payload)
    return _FrozenPrivateReadRequestV1(
        _issuer=_REQUEST_ISSUER,
        route_id=route_id,
        region=region,
        environment=environment,
        method=method,
        origin=origin,
        path=path,
        query_string=query_string,
        payload=payload,
        request_evidence_sha256=digest,
        policy_version=POLICY_VERSION,
        origin_binding=origin_binding,
        target=target,
        semantic_query=semantic_query,
    )


def _freeze_balances_private_read(
    *,
    region: DeltaRegion,
    environment: DeltaEnvironment,
) -> _FrozenPrivateReadRequestV1:
    """Freeze the query-free balances read into unsigned canonical bytes."""
    result: _FrozenPrivateReadRequestV1 | None = None
    try:
        origin = _resolved_origin(region, environment)
        target = _canonical_balances_target()
        result = _issue_request(
            region=region,
            environment=environment,
            origin_binding=origin,
            target=target,
            semantic_query=None,
        )
    except Exception:
        pass
    if result is None:
        raise _wire_error("freeze_private_read")
    return result


def _freeze_private_read(
    query: _PrivateReadQuery,
    *,
    region: DeltaRegion,
    environment: DeltaEnvironment,
) -> _FrozenPrivateReadRequestV1:
    """Freeze one exact semantic read query into unsigned canonical bytes."""
    result: _FrozenPrivateReadRequestV1 | None = None
    try:
        origin = _resolved_origin(region, environment)
        owned_query = _clone_query(query)
        target = _canonical_private_read_target(owned_query)
        result = _issue_request(
            region=region,
            environment=environment,
            origin_binding=origin,
            target=target,
            semantic_query=owned_query,
        )
    except Exception:
        pass
    if result is None:
        raise _wire_error("freeze_private_read")
    return result


def _attested_request(value: object) -> _FrozenPrivateReadRequestV1:
    if type(value) is not _FrozenPrivateReadRequestV1:
        raise TypeError("invalid frozen private read")
    route_id = object.__getattribute__(value, "_route_id")
    region = object.__getattribute__(value, "_region")
    environment = object.__getattribute__(value, "_environment")
    method = object.__getattribute__(value, "_method")
    origin = object.__getattribute__(value, "_origin")
    path = object.__getattribute__(value, "_path")
    query_string = object.__getattribute__(value, "_query_string")
    payload = object.__getattribute__(value, "_payload")
    digest = object.__getattribute__(value, "_request_evidence_sha256")
    policy_version = object.__getattribute__(value, "_policy_version")
    origin_binding = object.__getattribute__(value, "_origin_binding")
    target = object.__getattribute__(value, "_target")
    semantic_query = object.__getattribute__(value, "_semantic_query")

    if not (
        type(route_id) is PrivateRouteId
        and any(route_id is candidate for candidate in _READ_ROUTES)
        and type(region) is DeltaRegion
        and region is DeltaRegion.INDIA
        and type(environment) is DeltaEnvironment
        and (environment is DeltaEnvironment.PROD or environment is DeltaEnvironment.TESTNET)
        and type(method) is str
        and method == "GET"
        and type(origin) is str
        and type(path) is str
        and type(query_string) is bytes
        and type(payload) is bytes
        and payload == b""
        and _is_digest(digest)
        and type(policy_version) is str
        and policy_version is POLICY_VERSION
        and type(origin_binding) is CanonicalPrivateOrigin
        and type(target) is _CanonicalPrivateReadTarget
    ):
        raise TypeError("invalid frozen private read")

    canonical_origin = _resolved_origin(region, environment)
    if origin_binding is not canonical_origin:
        raise TypeError("invalid frozen private read")
    canonical_origin_value = object.__getattribute__(canonical_origin, "_value")
    if origin != canonical_origin_value:
        raise TypeError("invalid frozen private read")

    retained_route, retained_path, retained_query = _target_values(target)
    if semantic_query is None:
        regenerated = _canonical_balances_target()
    else:
        owned_query = _clone_query(semantic_query)
        regenerated = _canonical_private_read_target(owned_query)
    expected_route, expected_path, expected_query = _target_values(regenerated)

    if not (
        retained_route is expected_route
        and retained_path == expected_path
        and retained_query == expected_query
        and route_id is expected_route
        and path == expected_path
        and query_string == expected_query
        and digest == _evidence_sha256(method, origin, path, query_string, payload)
    ):
        raise TypeError("invalid frozen private read")
    return value


def _attest_frozen_private_read(
    value: _FrozenPrivateReadRequestV1,
) -> _FrozenPrivateReadRequestV1:
    """Re-render and attest every retained source before later consumption."""
    result: _FrozenPrivateReadRequestV1 | None = None
    try:
        result = _attested_request(value)
    except Exception:
        pass
    if result is None:
        raise _wire_error("attest_private_read")
    return result
