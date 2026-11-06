"""Closed canonical target codec for inert Delta India private reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NoReturn, TypeVar

from pydantic import ValidationError

from hmt_crypto.domain.private_primitives import ExactStrEnum, PrivateBoundaryModel
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta._private_route_rows import PRIVATE_ROUTE_ROWS
from hmt_crypto.exchange.delta.private_routes import (
    PrivateAuthentication,
    PrivateDispatchCapability,
    PrivateEngineAccounting,
    PrivateIdempotencyPolicy,
    PrivatePaginationPolicy,
    PrivateProfileState,
    PrivateRegionSupport,
    PrivateResponseSchema,
    PrivateRouteAccess,
    PrivateRouteId,
    PrivateRouteSpec,
    get_private_route_spec,
)
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

_TARGET_ISSUER = object()
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MIN_QUERY_TIME = datetime(2000, 1, 1, tzinfo=UTC)
_MAX_QUERY_TIME = datetime(2100, 1, 1, tzinfo=UTC)
_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
Q = TypeVar("Q", bound=PrivateBoundaryModel)
_READ_ROUTES = tuple(PrivateRouteId)[:10]
_PROFILE_FIELDS = (
    "route_id",
    "region_support",
    "method",
    "path_template",
    "path_fields",
    "query_fields",
    "body_fields",
    "nested_body_fields",
    "response_schema",
    "max_response_bytes",
    "pagination",
    "default_page_size",
    "max_page_size",
    "max_pages",
    "max_items",
    "access",
    "authentication",
    "retry_operation_name",
    "idempotency",
    "rate_route_name",
    "rest_weight",
    "engine_accounting",
    "dispatch_capability",
    "profile_state",
)
_ENUM_PROFILE_FIELDS: tuple[tuple[int, type[ExactStrEnum]], ...] = (
    (0, PrivateRouteId),
    (1, PrivateRegionSupport),
    (8, PrivateResponseSchema),
    (10, PrivatePaginationPolicy),
    (15, PrivateRouteAccess),
    (16, PrivateAuthentication),
    (18, PrivateIdempotencyPolicy),
    (21, PrivateEngineAccounting),
    (22, PrivateDispatchCapability),
    (23, PrivateProfileState),
)
_TEXT_PROFILE_FIELDS = (2, 3, 17, 19)
_TUPLE_PROFILE_FIELDS = (4, 5, 6, 7)
_INT_PROFILE_FIELDS = (9, 14, 20)
_OPTIONAL_INT_PROFILE_FIELDS = (11, 12, 13)


def _target_error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "canonical_private_read_target"})


@dataclass(frozen=True, slots=True, eq=False, repr=False, init=False)
class _CanonicalPrivateReadTarget:
    """Module-issued owned method-relative target bytes."""

    route_id: PrivateRouteId = field(init=False)
    path: str = field(init=False)
    query_string: bytes = field(init=False)

    def __init__(
        self,
        *,
        _issuer: object,
        route_id: PrivateRouteId,
        path: str,
        query_string: bytes,
    ) -> None:
        if _issuer is not _TARGET_ISSUER:
            raise TypeError("canonical private read targets are module-issued")
        object.__setattr__(self, "route_id", route_id)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "query_string", query_string)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del cls, kwargs
        raise TypeError("canonical private read targets cannot be subclassed")

    def __repr__(self) -> str:
        route = object.__getattribute__(self, "route_id")
        if type(route) is PrivateRouteId:
            for candidate in PrivateRouteId:
                if route is candidate:
                    return f"_CanonicalPrivateReadTarget(route_id={candidate.name})"
        return "_CanonicalPrivateReadTarget(route_id=INVALID)"

    __str__ = __repr__

    def __copy__(self) -> NoReturn:
        raise TypeError("canonical private read targets cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        raise TypeError("canonical private read targets cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("canonical private read targets cannot be serialized")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError("canonical private read targets cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("canonical private read targets cannot be serialized")


def _revalidate_query(value: object, expected: type[Q]) -> Q:
    if type(value) is not expected:
        raise _target_error()
    try:
        owned = expected.model_validate(value)
    except ValidationError:
        owned = None
    if owned is None:
        raise _target_error()
    if type(owned) is not expected:
        raise _target_error()
    return owned


def _enum_profile_field(value: object, expected: object, enum_type: type[ExactStrEnum]) -> bool:
    if type(expected) is not str or type(value) is not enum_type:
        return False
    for member in enum_type:
        literal = object.__getattribute__(member, "_value_")
        if type(literal) is str and literal == expected:
            return value is member
    return False


def _text_profile_field(value: object, expected: object) -> bool:
    return type(value) is str and type(expected) is str and value == expected


def _tuple_profile_field(value: object, expected: object) -> bool:
    if type(value) is not tuple or type(expected) is not tuple:
        return False
    if tuple.__len__(value) != tuple.__len__(expected):
        return False
    for index in range(tuple.__len__(expected)):
        item = tuple.__getitem__(value, index)
        expected_item = tuple.__getitem__(expected, index)
        if not (type(item) is str and type(expected_item) is str and item == expected_item):
            return False
    return True


def _int_profile_field(value: object, expected: object) -> bool:
    return type(value) is int and type(expected) is int and value == expected


def _optional_int_profile_field(value: object, expected: object) -> bool:
    if expected is None:
        return value is None
    return _int_profile_field(value, expected)


def _read_profile_row(route_id: PrivateRouteId) -> tuple[object, ...]:
    if type(route_id) is PrivateRouteId and type(PRIVATE_ROUTE_ROWS) is tuple:
        for index, candidate in enumerate(_READ_ROUTES):
            if route_id is candidate:
                row = tuple.__getitem__(PRIVATE_ROUTE_ROWS, index)
                if type(row) is tuple and tuple.__len__(row) == 24:
                    return row
                break
    raise _target_error()


def _profile_matches(
    spec: object,
    route_id: PrivateRouteId,
    expected: tuple[object, ...],
) -> bool:
    if type(spec) is not PrivateRouteSpec:
        return False
    actual = tuple(object.__getattribute__(spec, field) for field in _PROFILE_FIELDS)
    for index, enum_type in _ENUM_PROFILE_FIELDS:
        if not _enum_profile_field(actual[index], expected[index], enum_type):
            return False
    for index in _TEXT_PROFILE_FIELDS:
        if not _text_profile_field(actual[index], expected[index]):
            return False
    for index in _TUPLE_PROFILE_FIELDS:
        if not _tuple_profile_field(actual[index], expected[index]):
            return False
    for index in _INT_PROFILE_FIELDS:
        if not _int_profile_field(actual[index], expected[index]):
            return False
    for index in _OPTIONAL_INT_PROFILE_FIELDS:
        if not _optional_int_profile_field(actual[index], expected[index]):
            return False
    return actual[0] is route_id


def _reviewed_spec(route_id: PrivateRouteId) -> PrivateRouteSpec:
    expected = _read_profile_row(route_id)
    spec = get_private_route_spec(route_id)
    if not _profile_matches(spec, route_id, expected):
        raise _target_error()
    return spec


def _epoch_microseconds(value: datetime) -> str:
    if not _MIN_QUERY_TIME <= value < _MAX_QUERY_TIME:
        raise _target_error()
    delta = value - _EPOCH
    result = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    if type(result) is not int or not 1 <= result <= 2**63 - 1:
        raise _target_error()
    return str(result)


def _percent_encode(value: str) -> str:
    try:
        raw = value.encode("ascii")
    except UnicodeEncodeError:
        raise _target_error() from None
    encoded: list[str] = []
    for byte in raw:
        encoded.append(chr(byte) if byte in _UNRESERVED else f"%{byte:02X}")
    return "".join(encoded)


def _enum_values(values: tuple[ExactStrEnum, ...]) -> str:
    return ",".join(member.value for member in values)


def _id_values(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values)


def _query_bytes(spec: PrivateRouteSpec, pairs: tuple[tuple[str, str], ...]) -> bytes:
    keys = tuple(key for key, _value in pairs)
    expected = tuple(field for field in spec.query_fields if field in keys)
    if keys != expected or len(keys) != len(set(keys)):
        raise _target_error()
    if not pairs:
        return b""
    try:
        return ("?" + "&".join(f"{key}={value}" for key, value in pairs)).encode("ascii")
    except UnicodeEncodeError:
        raise _target_error() from None


def _issue_target(
    spec: PrivateRouteSpec,
    *,
    path: str,
    pairs: tuple[tuple[str, str], ...] = (),
) -> _CanonicalPrivateReadTarget:
    if type(path) is not str or not path.startswith("/v2/"):
        raise _target_error()
    return _CanonicalPrivateReadTarget(
        _issuer=_TARGET_ISSUER,
        route_id=spec.route_id,
        path=str(path),
        query_string=bytes(_query_bytes(spec, pairs)),
    )


def _page_pairs(query: object) -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    start_time = object.__getattribute__(query, "start_time")
    end_time = object.__getattribute__(query, "end_time")
    after = object.__getattribute__(query, "after")
    before = object.__getattribute__(query, "before")
    page_size = object.__getattribute__(query, "page_size")
    if start_time is not None:
        pairs.append(("start_time", _epoch_microseconds(start_time)))
    if end_time is not None:
        pairs.append(("end_time", _epoch_microseconds(end_time)))
    if after is not None:
        pairs.append(("after", _percent_encode(after)))
    if before is not None:
        pairs.append(("before", _percent_encode(before)))
    pairs.append(("page_size", str(page_size)))
    return tuple(pairs)


def _balances_target() -> _CanonicalPrivateReadTarget:
    spec = _reviewed_spec(PrivateRouteId.BALANCES)
    return _issue_target(spec, path=spec.path_template)


def _canonical_balances_target() -> _CanonicalPrivateReadTarget:
    """Build the sole query-free balances target from attested A4 evidence."""
    result: _CanonicalPrivateReadTarget | None = None
    try:
        result = _balances_target()
    except Exception:
        pass
    if type(result) is not _CanonicalPrivateReadTarget:
        raise _target_error()
    return result


def _wallet_target(value: object) -> _CanonicalPrivateReadTarget:
    query = _revalidate_query(value, WalletTransactionQuery)
    spec = _reviewed_spec(PrivateRouteId.TRANSACTIONS)
    pairs: list[tuple[str, str]] = []
    if query.asset_id is not None:
        pairs.append(("asset_ids", str(query.asset_id)))
    if query.transaction_types:
        pairs.append(("transaction_types", _enum_values(query.transaction_types)))
    pairs.extend(_page_pairs(query))
    return _issue_target(spec, path=spec.path_template, pairs=tuple(pairs))


def _margined_target(value: object) -> _CanonicalPrivateReadTarget:
    query = _revalidate_query(value, MarginedPositionQuery)
    spec = _reviewed_spec(PrivateRouteId.MARGINED_POSITIONS)
    pairs: list[tuple[str, str]] = []
    if query.product_ids:
        pairs.append(("product_ids", _id_values(query.product_ids)))
    if query.contract_types:
        pairs.append(("contract_types", _enum_values(query.contract_types)))
    return _issue_target(spec, path=spec.path_template, pairs=tuple(pairs))


def _latest_target(value: object) -> _CanonicalPrivateReadTarget:
    query = _revalidate_query(value, LatestPositionQuery)
    spec = _reviewed_spec(PrivateRouteId.LATEST_POSITION)
    return _issue_target(
        spec,
        path=spec.path_template,
        pairs=(("product_id", str(query.product_id)),),
    )


def _open_orders_target(value: object) -> _CanonicalPrivateReadTarget:
    query = _revalidate_query(value, OpenOrderQuery)
    spec = _reviewed_spec(PrivateRouteId.OPEN_ORDERS)
    pairs: list[tuple[str, str]] = []
    if query.product_ids:
        pairs.append(("product_ids", _id_values(query.product_ids)))
    if query.states:
        pairs.append(("states", _enum_values(query.states)))
    if query.contract_types:
        pairs.append(("contract_types", _enum_values(query.contract_types)))
    if query.order_types:
        pairs.append(("order_types", _enum_values(query.order_types)))
    pairs.extend(_page_pairs(query))
    return _issue_target(spec, path=spec.path_template, pairs=tuple(pairs))


def _history_target(value: object) -> _CanonicalPrivateReadTarget:
    query = _revalidate_query(value, OrderHistoryQuery)
    spec = _reviewed_spec(PrivateRouteId.ORDER_HISTORY)
    pairs: list[tuple[str, str]] = []
    if query.product_ids:
        pairs.append(("product_ids", _id_values(query.product_ids)))
    if query.contract_types:
        pairs.append(("contract_types", _enum_values(query.contract_types)))
    if query.order_types:
        pairs.append(("order_types", _enum_values(query.order_types)))
    pairs.extend(_page_pairs(query))
    return _issue_target(spec, path=spec.path_template, pairs=tuple(pairs))


def _fills_target(value: object) -> _CanonicalPrivateReadTarget:
    query = _revalidate_query(value, FillQuery)
    spec = _reviewed_spec(PrivateRouteId.FILLS)
    pairs: list[tuple[str, str]] = []
    if query.product_ids:
        pairs.append(("product_ids", _id_values(query.product_ids)))
    if query.contract_types:
        pairs.append(("contract_types", _enum_values(query.contract_types)))
    pairs.extend(_page_pairs(query))
    return _issue_target(spec, path=spec.path_template, pairs=tuple(pairs))


def _order_id_target(value: object) -> _CanonicalPrivateReadTarget:
    query = _revalidate_query(value, OrderByIdQuery)
    spec = _reviewed_spec(PrivateRouteId.ORDER_BY_ID)
    return _issue_target(spec, path=f"/v2/orders/{query.order_id}")


def _client_id_target(value: object) -> _CanonicalPrivateReadTarget:
    query = _revalidate_query(value, OrderByClientIdQuery)
    spec = _reviewed_spec(PrivateRouteId.ORDER_BY_CLIENT_ID)
    segment = _percent_encode(query.client_order_id)
    if segment == "." or segment == "..":
        raise _target_error()
    return _issue_target(spec, path=f"/v2/orders/client_order_id/{segment}")


def _leverage_target(value: object) -> _CanonicalPrivateReadTarget:
    query = _revalidate_query(value, LeverageQuery)
    spec = _reviewed_spec(PrivateRouteId.GET_LEVERAGE)
    return _issue_target(spec, path=f"/v2/products/{query.product_id}/orders/leverage")


def _render_private_read_target(query: object) -> _CanonicalPrivateReadTarget:
    query_type = type(query)
    if query_type is WalletTransactionQuery:
        return _wallet_target(query)
    if query_type is MarginedPositionQuery:
        return _margined_target(query)
    if query_type is LatestPositionQuery:
        return _latest_target(query)
    if query_type is OpenOrderQuery:
        return _open_orders_target(query)
    if query_type is OrderHistoryQuery:
        return _history_target(query)
    if query_type is FillQuery:
        return _fills_target(query)
    if query_type is OrderByIdQuery:
        return _order_id_target(query)
    if query_type is OrderByClientIdQuery:
        return _client_id_target(query)
    if query_type is LeverageQuery:
        return _leverage_target(query)
    raise _target_error()


def _canonical_private_read_target(
    query: WalletTransactionQuery
    | MarginedPositionQuery
    | LatestPositionQuery
    | OpenOrderQuery
    | OrderHistoryQuery
    | FillQuery
    | OrderByIdQuery
    | OrderByClientIdQuery
    | LeverageQuery,
) -> _CanonicalPrivateReadTarget:
    """Infer one target and collapse all corrupt A3/A4 state safely."""
    result: _CanonicalPrivateReadTarget | None = None
    try:
        result = _render_private_read_target(query)
    except Exception:
        pass
    if type(result) is not _CanonicalPrivateReadTarget:
        raise _target_error()
    return result
