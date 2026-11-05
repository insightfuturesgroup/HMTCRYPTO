"""Canonical, authority-free A4 profiles for the nine offline mutations."""

from __future__ import annotations

from dataclasses import dataclass

from hmt_crypto.domain.private_primitives import ExactStrEnum
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


@dataclass(frozen=True, slots=True)
class _MutationRouteProfile:
    """Independent expected identity and wire shape for one reviewed A4 route."""

    route_id: PrivateRouteId
    method: str
    path_template: str
    path_fields: tuple[str, ...]
    body_fields: tuple[str, ...]
    nested_body_fields: tuple[str, ...]
    response_schema: PrivateResponseSchema
    max_response_bytes: int
    retry_operation_name: str
    rate_route_name: str
    rest_weight: int
    engine_accounting: PrivateEngineAccounting


_MUTATION_ROUTE_PROFILES = (
    _MutationRouteProfile(
        PrivateRouteId.PLACE_ORDER,
        "POST",
        "/v2/orders",
        (),
        (
            "product_id",
            "size",
            "side",
            "order_type",
            "limit_price",
            "stop_order_type",
            "stop_price",
            "trail_amount",
            "stop_trigger_method",
            "bracket_stop_trigger_method",
            "bracket_stop_loss_limit_price",
            "bracket_stop_loss_price",
            "bracket_trail_amount",
            "bracket_take_profit_limit_price",
            "bracket_take_profit_price",
            "time_in_force",
            "mmp",
            "post_only",
            "reduce_only",
            "client_order_id",
        ),
        (),
        PrivateResponseSchema.ORDER,
        1_048_576,
        "create_order",
        "place_order",
        5,
        PrivateEngineAccounting.ONE_PRODUCT_OPERATION,
    ),
    _MutationRouteProfile(
        PrivateRouteId.MODIFY_ORDER,
        "PUT",
        "/v2/orders",
        (),
        (
            "id",
            "product_id",
            "size",
            "limit_price",
            "mmp",
            "post_only",
            "stop_price",
            "trail_amount",
        ),
        (),
        PrivateResponseSchema.ORDER,
        1_048_576,
        "modify_order",
        "modify_order",
        5,
        PrivateEngineAccounting.ONE_PRODUCT_OPERATION,
    ),
    _MutationRouteProfile(
        PrivateRouteId.CANCEL_ORDER,
        "DELETE",
        "/v2/orders",
        (),
        ("id", "product_id"),
        (),
        PrivateResponseSchema.ORDER,
        1_048_576,
        "cancel_order",
        "cancel_order",
        5,
        PrivateEngineAccounting.CANCELLATION_EXEMPT,
    ),
    _MutationRouteProfile(
        PrivateRouteId.CANCEL_ALL,
        "DELETE",
        "/v2/orders/all",
        (),
        ("product_id", "cancel_limit_orders", "cancel_stop_orders", "cancel_reduce_only_orders"),
        (),
        PrivateResponseSchema.ACKNOWLEDGEMENT,
        262_144,
        "cancel_all",
        "cancel_all",
        1,
        PrivateEngineAccounting.CANCELLATION_EXEMPT,
    ),
    _MutationRouteProfile(
        PrivateRouteId.SET_LEVERAGE,
        "POST",
        "/v2/products/{product_id}/orders/leverage",
        ("product_id",),
        ("leverage",),
        (),
        PrivateResponseSchema.LEVERAGE,
        262_144,
        "set_leverage",
        "set_leverage",
        1,
        PrivateEngineAccounting.NONE,
    ),
    _MutationRouteProfile(
        PrivateRouteId.SET_MARGIN_MODE,
        "PUT",
        "/v2/users/margin_mode",
        (),
        ("margin_mode", "subaccount_user_id"),
        (),
        PrivateResponseSchema.MARGIN_MODE,
        262_144,
        "set_margin_mode",
        "set_margin_mode",
        1,
        PrivateEngineAccounting.NONE,
    ),
    _MutationRouteProfile(
        PrivateRouteId.CHANGE_POSITION_MARGIN,
        "POST",
        "/v2/positions/change_margin",
        (),
        ("product_id", "delta_margin"),
        (),
        PrivateResponseSchema.MARGINED_POSITION,
        1_048_576,
        "add_margin",
        "change_position_margin",
        5,
        PrivateEngineAccounting.NONE,
    ),
    _MutationRouteProfile(
        PrivateRouteId.CREATE_BRACKET,
        "POST",
        "/v2/orders/bracket",
        (),
        ("product_id", "stop_loss_order", "take_profit_order", "bracket_stop_trigger_method"),
        (
            "stop_loss_order.order_type",
            "stop_loss_order.stop_price",
            "stop_loss_order.trail_amount",
            "stop_loss_order.limit_price",
            "take_profit_order.order_type",
            "take_profit_order.stop_price",
            "take_profit_order.limit_price",
        ),
        PrivateResponseSchema.ACKNOWLEDGEMENT,
        262_144,
        "create_bracket",
        "create_bracket",
        1,
        PrivateEngineAccounting.UNVERIFIED_DISABLED,
    ),
    _MutationRouteProfile(
        PrivateRouteId.EDIT_BRACKET,
        "PUT",
        "/v2/orders/bracket",
        (),
        (
            "id",
            "product_id",
            "bracket_stop_loss_limit_price",
            "bracket_stop_loss_price",
            "bracket_take_profit_limit_price",
            "bracket_take_profit_price",
            "bracket_trail_amount",
            "bracket_stop_trigger_method",
        ),
        (),
        PrivateResponseSchema.ACKNOWLEDGEMENT,
        262_144,
        "edit_bracket",
        "edit_bracket",
        1,
        PrivateEngineAccounting.UNVERIFIED_DISABLED,
    ),
)


def _profile_for(route_id: object) -> _MutationRouteProfile:
    if type(route_id) is PrivateRouteId:
        for profile in _MUTATION_ROUTE_PROFILES:
            if route_id is profile.route_id:
                return profile
    raise ValueError("canonical mutation profile unavailable") from None


def _canonical_member(enum_type: type[ExactStrEnum], value: object) -> bool:
    if type(value) is not enum_type:
        return False
    return any(value is member for member in enum_type)


def _exact_string_tuple(value: object) -> bool:
    if type(value) is not tuple:
        return False
    for index in range(tuple.__len__(value)):
        if type(tuple.__getitem__(value, index)) is not str:
            return False
    return True


def _exact_spec_values(spec: PrivateRouteSpec) -> tuple[object, ...]:
    try:
        return (
            object.__getattribute__(spec, "route_id"),
            object.__getattribute__(spec, "region_support"),
            object.__getattribute__(spec, "method"),
            object.__getattribute__(spec, "path_template"),
            object.__getattribute__(spec, "path_fields"),
            object.__getattribute__(spec, "query_fields"),
            object.__getattribute__(spec, "body_fields"),
            object.__getattribute__(spec, "nested_body_fields"),
            object.__getattribute__(spec, "response_schema"),
            object.__getattribute__(spec, "max_response_bytes"),
            object.__getattribute__(spec, "pagination"),
            object.__getattribute__(spec, "default_page_size"),
            object.__getattribute__(spec, "max_page_size"),
            object.__getattribute__(spec, "max_pages"),
            object.__getattribute__(spec, "max_items"),
            object.__getattribute__(spec, "access"),
            object.__getattribute__(spec, "authentication"),
            object.__getattribute__(spec, "retry_operation_name"),
            object.__getattribute__(spec, "idempotency"),
            object.__getattribute__(spec, "rate_route_name"),
            object.__getattribute__(spec, "rest_weight"),
            object.__getattribute__(spec, "engine_accounting"),
            object.__getattribute__(spec, "dispatch_capability"),
            object.__getattribute__(spec, "profile_state"),
        )
    except (AttributeError, TypeError):
        raise ValueError("canonical mutation profile unavailable") from None


def _require_canonical_mutation_route(route_id: PrivateRouteId) -> PrivateRouteSpec:
    """Resolve and attest every A4 field used by offline mutation codecs."""
    profile = _profile_for(route_id)
    spec = get_private_route_spec(profile.route_id)
    if type(spec) is not PrivateRouteSpec:
        raise ValueError("canonical mutation profile unavailable") from None
    (
        resolved_route_id,
        region_support,
        method,
        path_template,
        path_fields,
        query_fields,
        body_fields,
        nested_body_fields,
        response_schema,
        max_response_bytes,
        pagination,
        default_page_size,
        max_page_size,
        max_pages,
        max_items,
        access,
        authentication,
        retry_operation_name,
        idempotency,
        rate_route_name,
        rest_weight,
        engine_accounting,
        dispatch_capability,
        profile_state,
    ) = _exact_spec_values(spec)
    types_are_exact = (
        _canonical_member(PrivateRouteId, resolved_route_id)
        and _canonical_member(PrivateRegionSupport, region_support)
        and type(method) is str
        and type(path_template) is str
        and _exact_string_tuple(path_fields)
        and _exact_string_tuple(query_fields)
        and _exact_string_tuple(body_fields)
        and _exact_string_tuple(nested_body_fields)
        and _canonical_member(PrivateResponseSchema, response_schema)
        and type(max_response_bytes) is int
        and _canonical_member(PrivatePaginationPolicy, pagination)
        and default_page_size is None
        and max_page_size is None
        and max_pages is None
        and type(max_items) is int
        and _canonical_member(PrivateRouteAccess, access)
        and _canonical_member(PrivateAuthentication, authentication)
        and type(retry_operation_name) is str
        and _canonical_member(PrivateIdempotencyPolicy, idempotency)
        and type(rate_route_name) is str
        and type(rest_weight) is int
        and _canonical_member(PrivateEngineAccounting, engine_accounting)
        and _canonical_member(PrivateDispatchCapability, dispatch_capability)
        and _canonical_member(PrivateProfileState, profile_state)
    )
    if not types_are_exact:
        raise ValueError("canonical mutation profile unavailable") from None
    if not (
        resolved_route_id is profile.route_id
        and method == profile.method
        and path_template == profile.path_template
        and path_fields == profile.path_fields
        and query_fields == ()
        and body_fields == profile.body_fields
        and nested_body_fields == profile.nested_body_fields
        and response_schema is profile.response_schema
        and max_response_bytes == profile.max_response_bytes
        and pagination is PrivatePaginationPolicy.NONE
        and max_items == 1
        and region_support is PrivateRegionSupport.INDIA_ONLY
        and access is PrivateRouteAccess.WRITE
        and authentication is PrivateAuthentication.HMAC_SIGNED_EXTERNAL_USER
        and retry_operation_name == profile.retry_operation_name
        and idempotency is PrivateIdempotencyPolicy.NO_EXCHANGE_GUARANTEE
        and rate_route_name == profile.rate_route_name
        and rest_weight == profile.rest_weight
        and engine_accounting is profile.engine_accounting
        and dispatch_capability is PrivateDispatchCapability.NONE
        and profile_state is PrivateProfileState.OFFLINE_CODEC_ONLY
    ):
        raise ValueError("canonical mutation profile unavailable") from None
    return spec
