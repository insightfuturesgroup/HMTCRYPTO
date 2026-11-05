"""Pure, venue-unverified Delta mutation wire candidates with no authority."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import NoReturn, SupportsIndex

from hmt_crypto.domain.private_primitives import ExactStrEnum, exact_enum_literal
from hmt_crypto.exchange.delta import private_mutation_profile as _mutation_profile
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId, PrivateRouteSpec
from hmt_crypto.exchange.delta.rate_limit_policy import POLICY_VERSION
from hmt_crypto.exchange.private_requests import (
    BracketStopLossOrder,
    BracketTakeProfitOrder,
    CancelAllRequest,
    CancelOrderRequest,
    ChangePositionMarginRequest,
    CreateBracketRequest,
    EditBracketRequest,
    ModifyOrderRequest,
    PlaceOrderRequest,
    SetLeverageRequest,
    SetMarginModeRequest,
)


class _UnverifiedBooleanEncoding(ExactStrEnum):
    JSON_BOOLEAN_CANDIDATE = "unverified_candidate_json_boolean"
    STRING_BOOLEAN_CANDIDATE = "unverified_candidate_string_boolean"


class _UnverifiedLeverageEncoding(ExactStrEnum):
    DECIMAL_STRING_CANDIDATE = "unverified_candidate_decimal_string"
    JSON_INTEGER_CANDIDATE = "unverified_candidate_json_integer"


_PROFILE_ISSUER, _CANDIDATE_ISSUER = object(), object()


def _unavailable() -> ValueError:
    return ValueError("offline mutation candidate unavailable")


def _canonical_enum(enum_type: type[ExactStrEnum], value: object) -> ExactStrEnum:
    if type(value) is enum_type:
        for member in enum_type:
            if value is member:
                return member
    raise _unavailable() from None


class _SealedOfflineValue:
    __slots__ = ()

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("offline mutation evidence is immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("offline mutation evidence is immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("offline mutation evidence cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("offline mutation evidence cannot be copied")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("offline mutation evidence cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("offline mutation evidence cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("offline mutation evidence cannot be serialized")


class _OfflineMutationCodecProfile(_SealedOfflineValue):
    __slots__ = ("__boolean_encoding", "__issuer", "__leverage_encoding")
    __boolean_encoding: _UnverifiedBooleanEncoding
    __leverage_encoding: _UnverifiedLeverageEncoding

    def __init__(
        self,
        boolean_encoding: _UnverifiedBooleanEncoding,
        leverage_encoding: _UnverifiedLeverageEncoding,
        *,
        _issuer: object,
    ) -> None:
        if _issuer is not _PROFILE_ISSUER:
            raise TypeError("offline mutation profiles require the internal issuer")
        boolean = _canonical_enum(_UnverifiedBooleanEncoding, boolean_encoding)
        leverage = _canonical_enum(_UnverifiedLeverageEncoding, leverage_encoding)
        object.__setattr__(self, "_OfflineMutationCodecProfile__boolean_encoding", boolean)
        object.__setattr__(self, "_OfflineMutationCodecProfile__leverage_encoding", leverage)
        object.__setattr__(self, "_OfflineMutationCodecProfile__issuer", _PROFILE_ISSUER)

    def __init_subclass__(cls, **_kwargs: object) -> NoReturn:
        raise TypeError("offline mutation profiles cannot be subclassed")

    @property
    def boolean_encoding(self) -> _UnverifiedBooleanEncoding:
        return self.__boolean_encoding

    @property
    def leverage_encoding(self) -> _UnverifiedLeverageEncoding:
        return self.__leverage_encoding

    @property
    def venue_verified(self) -> bool:
        return False

    @property
    def dispatchable(self) -> bool:
        return False


def _create_offline_mutation_codec_profile(
    *,
    boolean_encoding: _UnverifiedBooleanEncoding,
    leverage_encoding: _UnverifiedLeverageEncoding,
) -> _OfflineMutationCodecProfile:
    try:
        return _OfflineMutationCodecProfile(
            boolean_encoding,
            leverage_encoding,
            _issuer=_PROFILE_ISSUER,
        )
    except (TypeError, ValueError):
        raise _unavailable() from None


def _require_profile(value: object) -> _OfflineMutationCodecProfile:
    if type(value) is not _OfflineMutationCodecProfile:
        raise _unavailable() from None
    try:
        issuer = object.__getattribute__(value, "_OfflineMutationCodecProfile__issuer")
        boolean = object.__getattribute__(value, "_OfflineMutationCodecProfile__boolean_encoding")
        leverage = object.__getattribute__(value, "_OfflineMutationCodecProfile__leverage_encoding")
    except (AttributeError, TypeError):
        raise _unavailable() from None
    if issuer is not _PROFILE_ISSUER:
        raise _unavailable() from None
    _canonical_enum(_UnverifiedBooleanEncoding, boolean)
    _canonical_enum(_UnverifiedLeverageEncoding, leverage)
    return value


MutationRequest = (
    PlaceOrderRequest
    | ModifyOrderRequest
    | CancelOrderRequest
    | CancelAllRequest
    | SetLeverageRequest
    | SetMarginModeRequest
    | ChangePositionMarginRequest
    | CreateBracketRequest
    | EditBracketRequest
)


_REQUEST_ROUTES: tuple[tuple[type[MutationRequest], PrivateRouteId], ...] = (
    (PlaceOrderRequest, PrivateRouteId.PLACE_ORDER),
    (ModifyOrderRequest, PrivateRouteId.MODIFY_ORDER),
    (CancelOrderRequest, PrivateRouteId.CANCEL_ORDER),
    (CancelAllRequest, PrivateRouteId.CANCEL_ALL),
    (SetLeverageRequest, PrivateRouteId.SET_LEVERAGE),
    (SetMarginModeRequest, PrivateRouteId.SET_MARGIN_MODE),
    (ChangePositionMarginRequest, PrivateRouteId.CHANGE_POSITION_MARGIN),
    (CreateBracketRequest, PrivateRouteId.CREATE_BRACKET),
    (EditBracketRequest, PrivateRouteId.EDIT_BRACKET),
)


def _owned_request(value: object) -> tuple[MutationRequest, PrivateRouteId]:
    for model_type, route_id in _REQUEST_ROUTES:
        if type(value) is model_type:
            try:
                return model_type.model_validate(value), route_id
            except Exception:
                raise _unavailable() from None
    raise _unavailable() from None


def _enum_text(value: ExactStrEnum) -> str:
    try:
        return exact_enum_literal(type(value), value)
    except ValueError:
        raise _unavailable() from None


def _decimal_text(value: Decimal) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise _unavailable() from None
    text = format(value, "f")
    if "e" in text.casefold():
        raise _unavailable() from None
    return text


def _encoded_bool(value: bool, profile: _OfflineMutationCodecProfile) -> bool | str:
    if type(value) is not bool:
        raise _unavailable() from None
    if profile.boolean_encoding is _UnverifiedBooleanEncoding.JSON_BOOLEAN_CANDIDATE:
        return value
    return "true" if value else "false"


def _wire_scalar(value: object, profile: _OfflineMutationCodecProfile) -> object:
    if type(value) is Decimal:
        return _decimal_text(value)
    if type(value) is bool:
        return _encoded_bool(value, profile)
    if isinstance(value, ExactStrEnum):
        return _enum_text(value)
    if type(value) in (int, str):
        return value
    raise _unavailable() from None


def _bracket_leg(
    value: BracketStopLossOrder | BracketTakeProfitOrder,
    *,
    root: str,
    spec: PrivateRouteSpec,
    profile: _OfflineMutationCodecProfile,
) -> dict[str, object]:
    body: dict[str, object] = {}
    prefix = root + "."
    for dotted_name in spec.nested_body_fields:
        if dotted_name.startswith(prefix):
            name = dotted_name.removeprefix(prefix)
            if hasattr(value, name):
                candidate = object.__getattribute__(value, name)
                if candidate is not None:
                    body[name] = _wire_scalar(candidate, profile)
    return body


def _body_for(
    request: MutationRequest,
    spec: PrivateRouteSpec,
    profile: _OfflineMutationCodecProfile,
) -> dict[str, object]:
    body: dict[str, object] = {}
    for name in spec.body_fields:
        value = object.__getattribute__(request, name)
        if value is None:
            continue
        if name in ("stop_loss_order", "take_profit_order"):
            body[name] = _bracket_leg(value, root=name, spec=spec, profile=profile)
        elif spec.route_id is PrivateRouteId.SET_LEVERAGE:
            if profile.leverage_encoding is _UnverifiedLeverageEncoding.JSON_INTEGER_CANDIDATE:
                if value != value.to_integral_value():
                    raise _unavailable() from None
                body[name] = int(value)
            else:
                body[name] = _decimal_text(value)
        else:
            body[name] = _wire_scalar(value, profile)
    return body


def _require_offline_spec(route_id: PrivateRouteId) -> PrivateRouteSpec:
    try:
        return _mutation_profile._require_canonical_mutation_route(route_id)
    except (TypeError, ValueError):
        raise _unavailable() from None


def _render_path(spec: PrivateRouteSpec, request: MutationRequest) -> str:
    if not spec.path_fields:
        return spec.path_template
    if spec.route_id is not PrivateRouteId.SET_LEVERAGE or spec.path_fields != ("product_id",):
        raise _unavailable() from None
    return spec.path_template.replace("{product_id}", str(request.product_id))


class _UnverifiedMutationWireCandidate(_SealedOfflineValue):
    __slots__ = (
        "__body",
        "__issuer",
        "__method",
        "__path",
        "__policy_version",
        "__profile",
        "__profile_fingerprint",
        "__query_string",
        "__route_id",
        "__semantic_fingerprint",
        "__semantic_request",
    )
    __body: bytes
    __method: str
    __path: str
    __query_string: bytes
    __route_id: PrivateRouteId

    def __init__(
        self,
        *,
        route_id: PrivateRouteId,
        method: str,
        path: str,
        body: bytes,
        profile: _OfflineMutationCodecProfile,
        semantic_request: MutationRequest,
        _issuer: object,
    ) -> None:
        if _issuer is not _CANDIDATE_ISSUER:
            raise TypeError("offline mutation candidates require the internal issuer")
        values = {
            "_UnverifiedMutationWireCandidate__route_id": route_id,
            "_UnverifiedMutationWireCandidate__method": method,
            "_UnverifiedMutationWireCandidate__path": path,
            "_UnverifiedMutationWireCandidate__query_string": b"",
            "_UnverifiedMutationWireCandidate__body": body,
            "_UnverifiedMutationWireCandidate__profile": profile,
            "_UnverifiedMutationWireCandidate__profile_fingerprint": (
                profile,
                profile.boolean_encoding,
                profile.leverage_encoding,
            ),
            "_UnverifiedMutationWireCandidate__policy_version": POLICY_VERSION,
            "_UnverifiedMutationWireCandidate__semantic_request": semantic_request,
            "_UnverifiedMutationWireCandidate__semantic_fingerprint": body,
            "_UnverifiedMutationWireCandidate__issuer": _CANDIDATE_ISSUER,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def __init_subclass__(cls, **_kwargs: object) -> NoReturn:
        raise TypeError("offline mutation candidates cannot be subclassed")

    @property
    def route_id(self) -> PrivateRouteId:
        return self.__route_id

    @property
    def method(self) -> str:
        return self.__method

    @property
    def path(self) -> str:
        return self.__path

    @property
    def query_string(self) -> bytes:
        return self.__query_string

    @property
    def body(self) -> bytes:
        return self.__body

    @property
    def venue_verified(self) -> bool:
        return False

    @property
    def dispatchable(self) -> bool:
        return False

    def __repr__(self) -> str:
        return (
            f"_UnverifiedMutationWireCandidate(route={self.route_id.name}, bytes={len(self.body)})"
        )

    __str__ = __repr__


def _encode_owned(
    request: MutationRequest,
    route_id: PrivateRouteId,
    profile: _OfflineMutationCodecProfile,
) -> _UnverifiedMutationWireCandidate:
    spec = _require_offline_spec(route_id)
    body_mapping = _body_for(request, spec, profile)
    try:
        body = json.dumps(
            body_mapping,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, OverflowError):
        raise _unavailable() from None
    if len(body) > spec.max_response_bytes:
        raise _unavailable() from None
    return _UnverifiedMutationWireCandidate(
        route_id=route_id,
        method=spec.method,
        path=_render_path(spec, request),
        body=body,
        profile=profile,
        semantic_request=request,
        _issuer=_CANDIDATE_ISSUER,
    )


def _encode_offline_mutation_candidate(
    request: MutationRequest,
    *,
    profile: _OfflineMutationCodecProfile,
) -> _UnverifiedMutationWireCandidate:
    owned_profile = _require_profile(profile)
    owned_request, route_id = _owned_request(request)
    return _encode_owned(owned_request, route_id, owned_profile)


def _attest_unverified_mutation_candidate(
    value: _UnverifiedMutationWireCandidate,
) -> _UnverifiedMutationWireCandidate:
    if type(value) is not _UnverifiedMutationWireCandidate:
        raise _unavailable() from None
    try:
        issuer = object.__getattribute__(value, "_UnverifiedMutationWireCandidate__issuer")
        route_id = object.__getattribute__(value, "_UnverifiedMutationWireCandidate__route_id")
        method = object.__getattribute__(value, "_UnverifiedMutationWireCandidate__method")
        path = object.__getattribute__(value, "_UnverifiedMutationWireCandidate__path")
        query_string = object.__getattribute__(
            value, "_UnverifiedMutationWireCandidate__query_string"
        )
        body = object.__getattribute__(value, "_UnverifiedMutationWireCandidate__body")
        request = object.__getattribute__(
            value, "_UnverifiedMutationWireCandidate__semantic_request"
        )
        profile_value = object.__getattribute__(value, "_UnverifiedMutationWireCandidate__profile")
        profile = _require_profile(profile_value)
        profile_fingerprint = object.__getattribute__(
            value, "_UnverifiedMutationWireCandidate__profile_fingerprint"
        )
        policy = object.__getattribute__(value, "_UnverifiedMutationWireCandidate__policy_version")
        fingerprint = object.__getattribute__(
            value, "_UnverifiedMutationWireCandidate__semantic_fingerprint"
        )
    except (AttributeError, TypeError, ValueError):
        raise _unavailable() from None
    if (
        issuer is not _CANDIDATE_ISSUER
        or policy is not POLICY_VERSION
        or type(route_id) is not PrivateRouteId
        or type(method) is not str
        or type(path) is not str
        or type(query_string) is not bytes
        or type(body) is not bytes
        or type(profile_fingerprint) is not tuple
        or tuple.__len__(profile_fingerprint) != 3
        or type(fingerprint) is not bytes
    ):
        raise _unavailable() from None
    try:
        _canonical_enum(PrivateRouteId, route_id)
        fingerprint_profile = tuple.__getitem__(profile_fingerprint, 0)
        fingerprint_boolean = tuple.__getitem__(profile_fingerprint, 1)
        fingerprint_leverage = tuple.__getitem__(profile_fingerprint, 2)
        _canonical_enum(_UnverifiedBooleanEncoding, fingerprint_boolean)
        _canonical_enum(_UnverifiedLeverageEncoding, fingerprint_leverage)
    except (IndexError, TypeError, ValueError):
        raise _unavailable() from None
    if not (
        fingerprint_profile is profile
        and fingerprint_boolean is profile.boolean_encoding
        and fingerprint_leverage is profile.leverage_encoding
        and fingerprint is body
    ):
        raise _unavailable() from None
    rebuilt = _encode_offline_mutation_candidate(request, profile=profile)
    if not (
        route_id is rebuilt.route_id
        and method == rebuilt.method
        and path == rebuilt.path
        and query_string == rebuilt.query_string
        and body == rebuilt.body
        and fingerprint == rebuilt.body
    ):
        raise _unavailable() from None
    return value
