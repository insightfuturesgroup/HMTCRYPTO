"""Pure response parsing for offline-only Delta mutation contracts."""

from __future__ import annotations

from typing import Any, cast

from pydantic import TypeAdapter

from hmt_crypto.domain import private_orders as orders
from hmt_crypto.domain import private_positions as positions
from hmt_crypto.domain.private_primitives import ExternalUserText
from hmt_crypto.errors import (
    AmbiguousExchangeResult,
    AuthError,
    OrderRejected,
    SignatureExpiredError,
    UnauthorizedApiAccessError,
)
from hmt_crypto.exchange.delta import private_mutation_profile as _mutation_profile
from hmt_crypto.exchange.delta.private_payload_parsers import (
    _parse_leverage_payload,
    _parse_margin_mode_payload,
    _parse_margined_position_payload,
    _parse_order_payload,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.delta.wire_json import decode_json_object
from hmt_crypto.exchange.private_requests import (
    CancelAllRequest,
    CancelOrderRequest,
    ChangePositionMarginRequest,
    CreateBracketRequest,
    EditBracketRequest,
    ModifyOrderRequest,
    PlaceOrderRequest,
    RequestMarginMode,
    SetLeverageRequest,
    SetMarginModeRequest,
)

_USER = TypeAdapter(ExternalUserText)
_COMMON_REJECTIONS = frozenset(
    (
        "insufficient_margin",
        "close_position_insufficient_margin",
        "insufficient_commission",
        "order_size_exceed_available",
        "order_size_not_available_in_orderbook",
        "close_order_position_unavailable",
        "risk_limits_breached",
        "invalid_contract",
        "immediate_liquidation",
        "out_of_bankruptcy",
        "self_matching_disrupted_post_only",
        "overlapping_buy_sell_orders",
        "immediate_execution_post_only",
        "naked_short_restricted",
        "portfolio_risk_limits_breached",
        "liquidation_risk_limits_breached",
        "greeks_limits_breached",
        "no_position_for_reduce_only",
        "no_position_left_for_reduce_only",
        "max_leverage_exceeded",
        "min_leverage_exceeded",
        "leverage_limit_exceeded",
        "order_already_filled",
        "less_than_order_min_size",
        "order_max_size_exceeded",
        "reduce_only_orders_allowed_sell_side",
        "reduce_only_orders_allowed",
        "trading_blocked",
        "kyc_pending",
        "trading_credits_breached",
        "derivatives_trading_blocked",
        "spot_trading_blocked",
        "spot_blocked_country",
        "account_risk_limits_breached",
        "position_under_liquidation",
        "position_bracket_exist_or_invalid_data",
        "no_open_position",
        "stale_order",
        "offset_by_reduce_only",
        "unsupported",
        "no_orders",
        "no_liquidity_for_market_order",
        "family_position_limit_exceeded",
        "trading_not_allowed_on_current_margin_mode",
        "max_orders_count_exceeded",
    )
)
_PLACE_ONLY = frozenset(
    (
        "limit_order_not_allowed_for_binary_options",
        "stop_orders_not_allowed_for_binary_options",
        "not_a_binary_options_product",
        "invalid_index_symbol",
    )
)
_MODIFY_ONLY = frozenset(("open_order_not_found",))
_TOP_AUTH: dict[str, type[AuthError]] = {
    "SignatureExpired": SignatureExpiredError,
    "UnauthorizedApiAccess": UnauthorizedApiAccessError,
    "Forbidden": AuthError,
}
_NESTED_AUTH = frozenset(
    ("invalid_api_key", "ip_not_whitelisted_for_api_key", "Signature Mismatch")
)
_ACK_ROUTES = frozenset(
    (PrivateRouteId.CANCEL_ALL, PrivateRouteId.CREATE_BRACKET, PrivateRouteId.EDIT_BRACKET)
)


def _context(route_id: PrivateRouteId, status: int, code: str | None = None) -> dict[str, object]:
    context: dict[str, object] = {"operation": route_id.value, "http_status": status}
    if code is not None:
        context["exchange_code"] = code
    return context


def _ambiguous(route_id: PrivateRouteId, status: object) -> AmbiguousExchangeResult:
    context: dict[str, object] = {"operation": route_id.value}
    if type(status) is int:
        context["http_status"] = status
    return AmbiguousExchangeResult(context=context)


def _owned_request(value: object, expected: Any) -> Any:
    if type(value) is not expected:
        raise ValueError
    validator = cast(Any, type.__getattribute__(expected, "model_validate"))
    return validator(value)


def _external_user(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    return _USER.validate_python(value)


def _exact_route(route_id: PrivateRouteId) -> int:
    route = _mutation_profile._require_canonical_mutation_route(route_id)
    return route.max_response_bytes


def _exact_keys(value: object, keys: tuple[str, ...]) -> dict[str, object]:
    if type(value) is not dict or dict.__len__(value) != len(keys):
        raise ValueError
    if any(not dict.__contains__(value, key) for key in keys):
        raise ValueError
    return value


def _visible_code(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 128
        or not value.isascii()
        or any(not 0x20 <= ord(character) <= 0x7E for character in value)
    ):
        raise ValueError
    return value


def _classified_error(
    envelope: dict[str, object], route_id: PrivateRouteId, status: int
) -> Exception | None:
    if status == 429 or not (status == 200 or 400 <= status <= 499):
        return None
    if dict.__len__(envelope) == 2 and all(
        dict.__contains__(envelope, key) for key in ("error", "message")
    ):
        code = _visible_code(dict.__getitem__(envelope, "error"))
        message = dict.__getitem__(envelope, "message")
        if type(message) is not str or len(message) > 256 or not message.isprintable():
            return None
        if code in _TOP_AUTH:
            return _TOP_AUTH[code](context=_context(route_id, status))
        return None
    row = _exact_keys(envelope, ("success", "error"))
    if dict.__getitem__(row, "success") is not False:
        return None
    nested = _exact_keys(dict.__getitem__(row, "error"), ("code",))
    code = _visible_code(dict.__getitem__(nested, "code"))
    if code in _NESTED_AUTH:
        return AuthError(context=_context(route_id, status))
    allowed = _COMMON_REJECTIONS
    if route_id is PrivateRouteId.PLACE_ORDER:
        allowed = allowed | _PLACE_ONLY
    elif route_id is PrivateRouteId.MODIFY_ORDER:
        allowed = allowed | _MODIFY_ONLY
    else:
        return None
    if status in (200, 400) and code in allowed:
        return OrderRejected(context=_context(route_id, status, code))
    return None


def _presence_value(value: object) -> object | None:
    if object.__getattribute__(value, "kind") != "VALUE":
        return None
    return cast(object, object.__getattribute__(value, "value"))


def _select_order(order: orders.Order, request: object, route_id: PrivateRouteId) -> None:
    if route_id is PrivateRouteId.PLACE_ORDER:
        placed = cast(PlaceOrderRequest, request)
        valid = (
            order.product_id == placed.product_id
            and order.size == placed.size
            and order.side.known is placed.side
            and order.order_type.known is placed.order_type
            and order.limit_price == placed.limit_price
            and (placed.reduce_only is None or order.reduce_only.value is placed.reduce_only)
        )
        client = _presence_value(order.client_order_id)
        valid = valid and (
            client is None if placed.client_order_id is None else client == placed.client_order_id
        )
        if not valid:
            raise ValueError
    elif route_id is PrivateRouteId.MODIFY_ORDER:
        modified = cast(ModifyOrderRequest, request)
        valid = (
            order.id == modified.id
            and order.product_id == modified.product_id
            and order.size == modified.size
        )
        for requested, observed in (
            (modified.limit_price, order.limit_price),
            (modified.stop_price, order.stop_price),
            (modified.trail_amount, order.trail_amount),
        ):
            if requested is not None and requested != observed:
                valid = False
        if modified.mmp is not None:
            observed_mmp = _presence_value(order.mmp)
            valid = (
                valid
                and observed_mmp is not None
                and object.__getattribute__(observed_mmp, "known") is modified.mmp
            )
        if not valid:
            raise ValueError
    else:
        cancelled = cast(CancelOrderRequest, request)
        if order.id != cancelled.id or order.product_id != cancelled.product_id:
            raise ValueError


def _success(
    envelope: dict[str, object], route_id: PrivateRouteId, request: object, user_id: str | None
) -> object:
    if route_id in _ACK_ROUTES:
        row = _exact_keys(envelope, ("success",))
        if dict.__getitem__(row, "success") is not True:
            raise ValueError
        return orders.MutationAcknowledgement(success=True)
    row = _exact_keys(envelope, ("success", "result"))
    if dict.__getitem__(row, "success") is not True:
        raise ValueError
    result = dict.__getitem__(row, "result")
    if route_id in (
        PrivateRouteId.PLACE_ORDER,
        PrivateRouteId.MODIFY_ORDER,
        PrivateRouteId.CANCEL_ORDER,
    ):
        if user_id is None:
            raise ValueError
        product_id = object.__getattribute__(request, "product_id")
        order = _parse_order_payload(
            result,
            endpoint_id=route_id,
            expected_user_id=user_id,
            allowed_product_ids=(product_id,),
        )
        _select_order(order, request, route_id)
        return order
    if route_id is PrivateRouteId.SET_LEVERAGE:
        leverage_request = cast(SetLeverageRequest, request)
        leverage = _parse_leverage_payload(
            result, endpoint_id=route_id, expected_product_id=leverage_request.product_id
        )
        if leverage.leverage != leverage_request.leverage:
            raise ValueError
        return leverage
    if route_id is PrivateRouteId.SET_MARGIN_MODE:
        mode_request = cast(SetMarginModeRequest, request)
        mode = _parse_margin_mode_payload(
            result, endpoint_id=route_id, expected_user_id=mode_request.subaccount_user_id
        )
        expected = (
            positions.MarginMode.ISOLATED
            if mode_request.margin_mode is RequestMarginMode.ISOLATED
            else positions.MarginMode.PORTFOLIO
        )
        if mode.margin_mode.known is not expected:
            raise ValueError
        return mode
    position_request = cast(ChangePositionMarginRequest, request)
    if user_id is None:
        raise ValueError
    return _parse_margined_position_payload(
        result,
        endpoint_id=route_id,
        expected_user_id=user_id,
        allowed_product_ids=(position_request.product_id,),
    )


def _parse_checked(
    body: object,
    status_code: object,
    request: object,
    external_user_id: object,
    route_id: PrivateRouteId,
    request_type: type[Any],
) -> object:
    owned = _owned_request(request, request_type)
    user_id = _external_user(external_user_id) if external_user_id is not None else None
    ceiling = _exact_route(route_id)
    if (
        type(status_code) is not int
        or not 100 <= status_code <= 599
        or type(body) is not bytes
        or bytes.__len__(body) > ceiling
    ):
        raise ValueError
    if status_code != 200 and not (400 <= status_code <= 499 and status_code != 429):
        raise ValueError
    envelope = decode_json_object(body, endpoint_id=route_id.value)
    if (
        status_code == 200
        and dict.__contains__(envelope, "success")
        and dict.__getitem__(envelope, "success") is True
    ):
        return _success(envelope, route_id, owned, user_id)
    classified = _classified_error(envelope, route_id, status_code)
    if classified is not None:
        return classified
    raise ValueError


def _parse(
    body: object,
    status_code: object,
    request: object,
    external_user_id: object,
    route_id: PrivateRouteId,
    request_type: type[Any],
) -> object:
    try:
        outcome = _parse_checked(
            body, status_code, request, external_user_id, route_id, request_type
        )
    except Exception:
        outcome = _ambiguous(route_id, status_code)
    return outcome


def _finish(outcome: object) -> object:
    if isinstance(outcome, Exception):
        raise outcome from None
    return outcome


def parse_place_order_response(
    body: bytes, *, status_code: int, request: PlaceOrderRequest, external_user_id: ExternalUserText
) -> orders.Order:
    """Parse one place-order outcome or raise a safe typed mutation error."""
    outcome = _parse(
        body, status_code, request, external_user_id, PrivateRouteId.PLACE_ORDER, PlaceOrderRequest
    )
    del body, request, external_user_id
    return cast(orders.Order, _finish(outcome))


def parse_modify_order_response(
    body: bytes,
    *,
    status_code: int,
    request: ModifyOrderRequest,
    external_user_id: ExternalUserText,
) -> orders.Order:
    """Parse one modify-order outcome or raise a safe typed mutation error."""
    outcome = _parse(
        body,
        status_code,
        request,
        external_user_id,
        PrivateRouteId.MODIFY_ORDER,
        ModifyOrderRequest,
    )
    del body, request, external_user_id
    return cast(orders.Order, _finish(outcome))


def parse_cancel_order_response(
    body: bytes,
    *,
    status_code: int,
    request: CancelOrderRequest,
    external_user_id: ExternalUserText,
) -> orders.Order:
    """Parse one cancel-order outcome or raise a safe typed mutation error."""
    outcome = _parse(
        body,
        status_code,
        request,
        external_user_id,
        PrivateRouteId.CANCEL_ORDER,
        CancelOrderRequest,
    )
    del body, request, external_user_id
    return cast(orders.Order, _finish(outcome))


def parse_cancel_all_response(
    body: bytes, *, status_code: int, request: CancelAllRequest
) -> orders.MutationAcknowledgement:
    """Parse an exact cancel-all acknowledgement without inferring state."""
    outcome = _parse(body, status_code, request, None, PrivateRouteId.CANCEL_ALL, CancelAllRequest)
    del body, request
    return cast(orders.MutationAcknowledgement, _finish(outcome))


def parse_set_leverage_response(
    body: bytes, *, status_code: int, request: SetLeverageRequest
) -> orders.Leverage:
    """Parse one set-leverage outcome with exact request selector parity."""
    outcome = _parse(
        body, status_code, request, None, PrivateRouteId.SET_LEVERAGE, SetLeverageRequest
    )
    del body, request
    return cast(orders.Leverage, _finish(outcome))


def parse_set_margin_mode_response(
    body: bytes, *, status_code: int, request: SetMarginModeRequest
) -> orders.MarginModeResult:
    """Parse one set-margin-mode outcome with exact account parity."""
    outcome = _parse(
        body, status_code, request, None, PrivateRouteId.SET_MARGIN_MODE, SetMarginModeRequest
    )
    del body, request
    return cast(orders.MarginModeResult, _finish(outcome))


def parse_change_position_margin_response(
    body: bytes,
    *,
    status_code: int,
    request: ChangePositionMarginRequest,
    external_user_id: ExternalUserText,
) -> positions.MarginedPosition:
    """Parse one position-margin outcome with exact account/product parity."""
    outcome = _parse(
        body,
        status_code,
        request,
        external_user_id,
        PrivateRouteId.CHANGE_POSITION_MARGIN,
        ChangePositionMarginRequest,
    )
    del body, request, external_user_id
    return cast(positions.MarginedPosition, _finish(outcome))


def parse_create_bracket_response(
    body: bytes, *, status_code: int, request: CreateBracketRequest
) -> orders.MutationAcknowledgement:
    """Parse an exact create-bracket acknowledgement without inferring protection."""
    outcome = _parse(
        body, status_code, request, None, PrivateRouteId.CREATE_BRACKET, CreateBracketRequest
    )
    del body, request
    return cast(orders.MutationAcknowledgement, _finish(outcome))


def parse_edit_bracket_response(
    body: bytes, *, status_code: int, request: EditBracketRequest
) -> orders.MutationAcknowledgement:
    """Parse an exact edit-bracket acknowledgement without inferring protection."""
    outcome = _parse(
        body, status_code, request, None, PrivateRouteId.EDIT_BRACKET, EditBracketRequest
    )
    del body, request
    return cast(orders.MutationAcknowledgement, _finish(outcome))
