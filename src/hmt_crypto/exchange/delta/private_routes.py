"""Dependency-light, inert Delta India private REST route metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from hmt_crypto.domain.private_primitives import ExactStrEnum, exact_enum_member_for_literal
from hmt_crypto.exchange.delta._private_route_rows import PRIVATE_ROUTE_ROWS


class PrivateRouteId(ExactStrEnum):
    """Stable identities for every reviewed private read or inert codec route."""

    BALANCES = "balances"
    TRANSACTIONS = "transactions"
    MARGINED_POSITIONS = "margined_positions"
    LATEST_POSITION = "latest_position"
    OPEN_ORDERS = "open_orders"
    ORDER_HISTORY = "order_history"
    FILLS = "fills"
    ORDER_BY_ID = "order_by_id"
    ORDER_BY_CLIENT_ID = "order_by_client_id"
    GET_LEVERAGE = "get_leverage"
    PLACE_ORDER = "place_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    CANCEL_ALL = "cancel_all"
    SET_LEVERAGE = "set_leverage"
    SET_MARGIN_MODE = "set_margin_mode"
    CHANGE_POSITION_MARGIN = "change_position_margin"
    CREATE_BRACKET = "create_bracket"
    EDIT_BRACKET = "edit_bracket"


class PrivateRegionSupport(ExactStrEnum):
    """Regions supported by one private profile."""

    INDIA_ONLY = "india_only"


class PrivateRouteAccess(ExactStrEnum):
    """Read or write classification independent of HTTP retry behavior."""

    READ = "read"
    WRITE = "write"


class PrivateAuthentication(ExactStrEnum):
    """Authentication and documented authenticated-quota owner marker."""

    HMAC_SIGNED_EXTERNAL_USER = "hmac_signed_external_user"


class PrivatePaginationPolicy(ExactStrEnum):
    """Bounded local pagination policy."""

    NONE = "none"
    FORWARD_CURSOR = "forward_cursor"


class PrivateIdempotencyPolicy(ExactStrEnum):
    """Retry/idempotency evidence available for a route."""

    SAFE_READ = "safe_read"
    NO_EXCHANGE_GUARANTEE = "no_exchange_guarantee"


class PrivateEngineAccounting(ExactStrEnum):
    """Matching-engine product-operation accounting classification."""

    NONE = "none"
    ONE_PRODUCT_OPERATION = "one_product_operation"
    CANCELLATION_EXEMPT = "cancellation_exempt"
    UNVERIFIED_DISABLED = "unverified_disabled"


class PrivateDispatchCapability(ExactStrEnum):
    """Dispatch authority deliberately granted by a reviewed profile."""

    READ_ONLY = "read_only"
    NONE = "none"


class PrivateProfileState(ExactStrEnum):
    """Evidence status of a reviewed profile."""

    REVIEWED = "reviewed"
    DISABLED_PENDING_TESTNET = "disabled_pending_testnet"
    OFFLINE_CODEC_ONLY = "offline_codec_only"


class PrivateResponseSchema(ExactStrEnum):
    """Closed response-model identity without importing response modules."""

    BALANCE_SNAPSHOT = "balance_snapshot"
    TRANSACTION_PAGE = "transaction_page"
    MARGINED_POSITION_LIST = "margined_position_list"
    LATEST_POSITION = "latest_position"
    ORDER_PAGE = "order_page"
    FILL_PAGE = "fill_page"
    ORDER = "order"
    LEVERAGE = "leverage"
    MARGIN_MODE = "margin_mode"
    MARGINED_POSITION = "margined_position"
    ACKNOWLEDGEMENT = "acknowledgement"


E = TypeVar("E", bound=ExactStrEnum)


def _canonical(enum_type: type[E], value: object, *, name: str) -> E:
    if type(value) is enum_type:
        for member in enum_type:
            if value is member:
                return member
    raise ValueError(f"{name} must be a canonical exact enum member")


def _exact_text(value: object, *, name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{name} must be a nonempty exact string")
    return value


def _owned_strings(value: object, *, name: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{name} must be an exact tuple")
    owned: list[str] = []
    for index in range(tuple.__len__(value)):
        item = tuple.__getitem__(value, index)
        if type(item) is not str or not item or any(item == prior for prior in owned):
            raise ValueError(f"{name} must contain unique nonempty exact strings")
        owned.append(item)
    return tuple(item for item in owned)


def _positive_int(value: object, *, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be an exact positive integer")
    return value


def _optional_positive_int(value: object, *, name: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, name=name)


@dataclass(frozen=True, slots=True, eq=False)
class PrivateRouteSpec:
    """Validated inert metadata for one reviewed private REST template."""

    route_id: PrivateRouteId
    region_support: PrivateRegionSupport
    method: str
    path_template: str
    path_fields: tuple[str, ...]
    query_fields: tuple[str, ...]
    body_fields: tuple[str, ...]
    nested_body_fields: tuple[str, ...]
    response_schema: PrivateResponseSchema
    max_response_bytes: int
    pagination: PrivatePaginationPolicy
    default_page_size: int | None
    max_page_size: int | None
    max_pages: int | None
    max_items: int
    access: PrivateRouteAccess
    authentication: PrivateAuthentication
    retry_operation_name: str
    idempotency: PrivateIdempotencyPolicy
    rate_route_name: str
    rest_weight: int
    engine_accounting: PrivateEngineAccounting
    dispatch_capability: PrivateDispatchCapability
    profile_state: PrivateProfileState

    def __post_init__(self) -> None:
        route_id = _canonical(PrivateRouteId, self.route_id, name="route_id")
        region = _canonical(PrivateRegionSupport, self.region_support, name="region_support")
        method = _exact_text(self.method, name="method")
        path = _exact_text(self.path_template, name="path_template")
        path_fields = _owned_strings(self.path_fields, name="path_fields")
        query_fields = _owned_strings(self.query_fields, name="query_fields")
        body_fields = _owned_strings(self.body_fields, name="body_fields")
        nested_fields = _owned_strings(self.nested_body_fields, name="nested_body_fields")
        response = _canonical(PrivateResponseSchema, self.response_schema, name="response_schema")
        max_bytes = _positive_int(self.max_response_bytes, name="max_response_bytes")
        pagination = _canonical(PrivatePaginationPolicy, self.pagination, name="pagination")
        default_size = _optional_positive_int(self.default_page_size, name="default_page_size")
        max_size = _optional_positive_int(self.max_page_size, name="max_page_size")
        max_pages = _optional_positive_int(self.max_pages, name="max_pages")
        max_items = _positive_int(self.max_items, name="max_items")
        access = _canonical(PrivateRouteAccess, self.access, name="access")
        authentication = _canonical(
            PrivateAuthentication, self.authentication, name="authentication"
        )
        retry_name = _exact_text(self.retry_operation_name, name="retry_operation_name")
        idempotency = _canonical(PrivateIdempotencyPolicy, self.idempotency, name="idempotency")
        rate_name = _exact_text(self.rate_route_name, name="rate_route_name")
        rest_weight = _positive_int(self.rest_weight, name="rest_weight")
        engine = _canonical(
            PrivateEngineAccounting, self.engine_accounting, name="engine_accounting"
        )
        dispatch = _canonical(
            PrivateDispatchCapability, self.dispatch_capability, name="dispatch_capability"
        )
        state = _canonical(PrivateProfileState, self.profile_state, name="profile_state")

        if region is not PrivateRegionSupport.INDIA_ONLY:
            raise ValueError("private profiles must remain India-only")
        if authentication is not PrivateAuthentication.HMAC_SIGNED_EXTERNAL_USER:
            raise ValueError("private profiles require signed external-user authentication")
        if method not in ("GET", "POST", "PUT", "DELETE"):
            raise ValueError("method is not reviewed")
        if any(path.count("{" + field + "}") != 1 for field in path_fields):
            raise ValueError("path fields must occur once in the path template")
        if any("{" + field + "}" in path for field in (*query_fields, *body_fields)):
            raise ValueError("non-path fields cannot be path placeholders")
        if any(field.split(".", 1)[0] not in body_fields for field in nested_fields):
            raise ValueError("nested fields require a declared body root")
        if pagination is PrivatePaginationPolicy.NONE:
            if (default_size, max_size, max_pages) != (None, None, None):
                raise ValueError("non-paginated routes cannot define page bounds")
        elif None in (default_size, max_size, max_pages) or not {
            "after",
            "before",
            "page_size",
        }.issubset(query_fields):
            raise ValueError("cursor routes require complete local bounds and cursor fields")
        elif default_size > max_size:  # type: ignore[operator]
            raise ValueError("default page size cannot exceed its maximum")
        is_read = access is PrivateRouteAccess.READ
        if is_read != (method == "GET"):
            raise ValueError("access must agree with the reviewed method")
        if is_read:
            if (
                retry_name != "safe_read"
                or idempotency is not PrivateIdempotencyPolicy.SAFE_READ
                or engine is not PrivateEngineAccounting.NONE
                or state is PrivateProfileState.OFFLINE_CODEC_ONLY
            ):
                raise ValueError("read profile classification is inconsistent")
            expected_dispatch = (
                PrivateDispatchCapability.NONE
                if state is PrivateProfileState.DISABLED_PENDING_TESTNET
                else PrivateDispatchCapability.READ_ONLY
            )
            if dispatch is not expected_dispatch:
                raise ValueError("read dispatch state is inconsistent")
        elif (
            retry_name == "safe_read"
            or idempotency is not PrivateIdempotencyPolicy.NO_EXCHANGE_GUARANTEE
            or dispatch is not PrivateDispatchCapability.NONE
            or state is not PrivateProfileState.OFFLINE_CODEC_ONLY
        ):
            raise ValueError("write profiles must remain offline and non-idempotent")

        for field_name, value in (
            ("route_id", route_id),
            ("region_support", region),
            ("method", method),
            ("path_template", path),
            ("path_fields", path_fields),
            ("query_fields", query_fields),
            ("body_fields", body_fields),
            ("nested_body_fields", nested_fields),
            ("response_schema", response),
            ("max_response_bytes", max_bytes),
            ("pagination", pagination),
            ("default_page_size", default_size),
            ("max_page_size", max_size),
            ("max_pages", max_pages),
            ("max_items", max_items),
            ("access", access),
            ("authentication", authentication),
            ("retry_operation_name", retry_name),
            ("idempotency", idempotency),
            ("rate_route_name", rate_name),
            ("rest_weight", rest_weight),
            ("engine_accounting", engine),
            ("dispatch_capability", dispatch),
            ("profile_state", state),
        ):
            object.__setattr__(self, field_name, value)

    @property
    def is_read(self) -> bool:
        """Return whether this profile is a read."""
        return self.access is PrivateRouteAccess.READ

    @property
    def is_write(self) -> bool:
        """Return whether this profile is a write."""
        return self.access is PrivateRouteAccess.WRITE

    @property
    def auth_required(self) -> bool:
        """Return the exact signed-auth requirement."""
        return self.authentication is PrivateAuthentication.HMAC_SIGNED_EXTERNAL_USER

    @property
    def retry_safe(self) -> bool:
        """Return whether the shared retry operation is the safe-read class."""
        return self.retry_operation_name == "safe_read"


def _raw_member(enum_type: type[E], value: object, *, name: str) -> E:
    if type(value) is not str:
        raise ValueError(f"invalid trusted {name}")
    member = exact_enum_member_for_literal(enum_type, value)
    if member is None:
        raise ValueError(f"invalid trusted {name}")
    return member


def _spec_from_row(row: object) -> PrivateRouteSpec:
    if type(row) is not tuple or tuple.__len__(row) != 24:
        raise ValueError("invalid trusted private route row")
    values = tuple(tuple.__getitem__(row, index) for index in range(24))
    return PrivateRouteSpec(
        route_id=_raw_member(PrivateRouteId, values[0], name="route ID"),
        region_support=_raw_member(PrivateRegionSupport, values[1], name="region"),
        method=_exact_text(values[2], name="method"),
        path_template=_exact_text(values[3], name="path_template"),
        path_fields=_owned_strings(values[4], name="path_fields"),
        query_fields=_owned_strings(values[5], name="query_fields"),
        body_fields=_owned_strings(values[6], name="body_fields"),
        nested_body_fields=_owned_strings(values[7], name="nested_body_fields"),
        response_schema=_raw_member(PrivateResponseSchema, values[8], name="response"),
        max_response_bytes=_positive_int(values[9], name="max_response_bytes"),
        pagination=_raw_member(PrivatePaginationPolicy, values[10], name="pagination"),
        default_page_size=_optional_positive_int(values[11], name="default_page_size"),
        max_page_size=_optional_positive_int(values[12], name="max_page_size"),
        max_pages=_optional_positive_int(values[13], name="max_pages"),
        max_items=_positive_int(values[14], name="max_items"),
        access=_raw_member(PrivateRouteAccess, values[15], name="access"),
        authentication=_raw_member(PrivateAuthentication, values[16], name="authentication"),
        retry_operation_name=_exact_text(values[17], name="retry_operation_name"),
        idempotency=_raw_member(PrivateIdempotencyPolicy, values[18], name="idempotency"),
        rate_route_name=_exact_text(values[19], name="rate_route_name"),
        rest_weight=_positive_int(values[20], name="rest_weight"),
        engine_accounting=_raw_member(PrivateEngineAccounting, values[21], name="engine"),
        dispatch_capability=_raw_member(PrivateDispatchCapability, values[22], name="dispatch"),
        profile_state=_raw_member(PrivateProfileState, values[23], name="profile state"),
    )


def _build_registry(rows: object) -> tuple[PrivateRouteSpec, ...]:
    if type(rows) is not tuple or tuple.__len__(rows) != len(tuple(PrivateRouteId)):
        raise ValueError("invalid trusted private route registry")
    built = tuple(_spec_from_row(tuple.__getitem__(rows, index)) for index in range(19))
    for expected, spec in zip(PrivateRouteId, built, strict=True):
        if spec.route_id is not expected:
            raise ValueError("private route row order is inconsistent")
    pairs = tuple((spec.method, spec.path_template) for spec in built)
    if len(set(pairs)) != len(pairs):
        raise ValueError("private route templates must be unique")
    return built


PRIVATE_ROUTE_SPECS: tuple[PrivateRouteSpec, ...] = _build_registry(PRIVATE_ROUTE_ROWS)


def _resolution_error() -> Exception:
    from hmt_crypto.errors import ConfigurationError

    return ConfigurationError(context={"operation": "resolve_private_route"})


def get_private_route_spec(route_id: PrivateRouteId) -> PrivateRouteSpec:
    """Resolve only one canonical route-ID identity to inert metadata."""
    if type(route_id) is PrivateRouteId:
        for index, member in enumerate(PrivateRouteId):
            if route_id is member:
                return PRIVATE_ROUTE_SPECS[index]
    raise _resolution_error()


def resolve_private_route_template(method: object, path_template: object) -> PrivateRouteSpec:
    """Resolve one exact reviewed method/template pair without rendering it."""
    if type(method) is str and type(path_template) is str:
        for spec in PRIVATE_ROUTE_SPECS:
            if method == spec.method and path_template == spec.path_template:
                return spec
    raise _resolution_error()


__all__ = [
    "PrivateRouteId",
    "PrivateRegionSupport",
    "PrivateRouteAccess",
    "PrivateAuthentication",
    "PrivatePaginationPolicy",
    "PrivateIdempotencyPolicy",
    "PrivateEngineAccounting",
    "PrivateDispatchCapability",
    "PrivateProfileState",
    "PrivateResponseSchema",
    "PrivateRouteSpec",
    "PRIVATE_ROUTE_SPECS",
    "get_private_route_spec",
    "resolve_private_route_template",
]
