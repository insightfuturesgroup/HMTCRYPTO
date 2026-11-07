"""Pure, exchange-neutral row grammar for reviewed Delta private payloads."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter

from hmt_crypto.domain import private_orders as orders
from hmt_crypto.domain import private_positions as positions
from hmt_crypto.domain.private_primitives import (
    ExternalUserText,
    PrivateId,
    exact_enum_member_for_literal,
    parse_epoch_microseconds,
    parse_position_iso_datetime,
    parse_private_decimal_text,
)
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.private_routes import (
    PrivateResponseSchema,
    PrivateRouteId,
    get_private_route_spec,
)

_USER = TypeAdapter(ExternalUserText)
_PRIVATE_ID = TypeAdapter(PrivateId)
_MAX_PRODUCT_NODES, _MAX_PRODUCT_DEPTH = 4096, 64

_ORDER_REQUIRED = (
    "id",
    "user_id",
    "size",
    "unfilled_size",
    "side",
    "order_type",
    "limit_price",
    "stop_order_type",
    "stop_price",
    "average_fill_price",
    "trail_amount",
    "cancellation_reason",
    "updated_at",
    "reduce_only",
    "state",
    "created_at",
    "product_id",
    "product_symbol",
)
_ORDER_PRESENCE = (
    "bracket_order",
    "bracket_stop_loss_price",
    "bracket_stop_loss_limit_price",
    "bracket_take_profit_price",
    "bracket_take_profit_limit_price",
    "bracket_trail_amount",
    "mmp",
    "stop_trigger_method",
    "time_in_force",
    "paid_commission",
    "commission",
    "client_order_id",
)
_POSITION_FIELDS = (
    "user_id",
    "size",
    "entry_price",
    "margin",
    "liquidation_price",
    "bankruptcy_price",
    "adl_level",
    "product_id",
    "product_symbol",
    "product",
    "commission",
    "realized_pnl",
    "realized_funding",
    "realized_cashflow",
    "realized_holding_cost",
    "unrealized_pnl",
    "unrealized_cashflow",
    "mark_price",
    "margin_mode",
    "auto_topup",
    "created_at",
    "updated_at",
)


def _operation(value: object) -> str:
    return value.value if type(value) is PrivateRouteId else "parse_private_payload"


def _route(
    endpoint_id: object, schemas: tuple[PrivateResponseSchema, ...]
) -> tuple[PrivateRouteId, int]:
    if type(endpoint_id) is not PrivateRouteId:
        raise ValueError
    route = get_private_route_spec(endpoint_id)
    if not any(route.response_schema is schema for schema in schemas):
        raise ValueError
    return endpoint_id, route.max_response_bytes


def _user(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    return _USER.validate_python(value)


def _product_id(value: object) -> int:
    if type(value) is not int:
        raise ValueError
    return _PRIVATE_ID.validate_python(value)


def _allowed(value: object) -> tuple[int, ...] | None:
    if value is None:
        return None
    if type(value) is not tuple or not 1 <= tuple.__len__(value) <= 10:
        raise ValueError
    owned: list[int] = []
    for index in range(tuple.__len__(value)):
        item = _product_id(tuple.__getitem__(value, index))
        if item in owned:
            raise ValueError
        owned.append(item)
    return tuple(owned)


def _object(
    value: object, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError
    if any(not dict.__contains__(value, name) for name in required):
        raise ValueError
    allowed = required + optional
    for key in dict.keys(value):
        if type(key) is not str or key not in allowed:
            raise ValueError
    return value


def _item(value: dict[str, object], name: str) -> object:
    return dict.__getitem__(value, name)


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError
    return value


def _wire_user(value: object, expected: str) -> str:
    raw = _integer(value)
    if not 1 <= raw <= 2**63 - 1:
        raise ValueError
    owned = str(raw)
    if owned != expected:
        raise ValueError
    return owned


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError
    return value


def _decimal(value: object) -> Decimal:
    return parse_private_decimal_text(_text(value))


def _nullable_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _nullable_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _nullable_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _observed(value: object, enum: Any, model: Any) -> Any:
    raw = _text(value)
    known = exact_enum_member_for_literal(enum, raw)
    return model(raw=raw, known=known)


def _nullable_observed(value: object, enum: Any, model: Any) -> Any:
    return None if value is None else _observed(value, enum, model)


def _presence(
    value: dict[str, object], name: str, parser: Callable[[object], object]
) -> dict[str, object]:
    if not dict.__contains__(value, name):
        return {"kind": "MISSING"}
    raw = _item(value, name)
    if raw is None:
        return {"kind": "NULL"}
    return {"kind": "VALUE", "value": parser(raw)}


def _reduce_only(value: object) -> orders.ObservedReduceOnly:
    if type(value) is bool:
        return orders.ObservedReduceOnly(
            value=value, encoding=orders.ReduceOnlyEncoding.JSON_BOOLEAN
        )
    if type(value) is str and value in ("true", "false"):
        return orders.ObservedReduceOnly(
            value=value == "true", encoding=orders.ReduceOnlyEncoding.STRING_BOOLEAN
        )
    raise ValueError


def _order_unsafe(
    value: object,
    endpoint_id: object,
    expected_user_id: object,
    allowed_product_ids: object,
) -> orders.Order:
    _route(endpoint_id, (PrivateResponseSchema.ORDER, PrivateResponseSchema.ORDER_PAGE))
    expected = _user(expected_user_id)
    allowed = _allowed(allowed_product_ids)
    row = _object(value, _ORDER_REQUIRED, _ORDER_PRESENCE)
    product_id = _product_id(_item(row, "product_id"))
    if allowed is not None and product_id not in allowed:
        raise ValueError
    payload: dict[str, object] = {
        "id": _integer(_item(row, "id")),
        "user_id": _wire_user(_item(row, "user_id"), expected),
        "size": _integer(_item(row, "size")),
        "unfilled_size": _integer(_item(row, "unfilled_size")),
        "side": _observed(_item(row, "side"), orders.OrderSide, orders.ObservedOrderSide),
        "order_type": _observed(
            _item(row, "order_type"), orders.OrderType, orders.ObservedOrderType
        ),
        "limit_price": _nullable_decimal(_item(row, "limit_price")),
        "stop_order_type": _nullable_observed(
            _item(row, "stop_order_type"),
            orders.StopOrderType,
            orders.ObservedStopOrderType,
        ),
        "stop_price": _nullable_decimal(_item(row, "stop_price")),
        "average_fill_price": _nullable_decimal(_item(row, "average_fill_price")),
        "bracket_order": _presence(row, "bracket_order", _boolean),
        "bracket_stop_loss_price": _presence(row, "bracket_stop_loss_price", _decimal),
        "bracket_stop_loss_limit_price": _presence(row, "bracket_stop_loss_limit_price", _decimal),
        "bracket_take_profit_price": _presence(row, "bracket_take_profit_price", _decimal),
        "bracket_take_profit_limit_price": _presence(
            row, "bracket_take_profit_limit_price", _decimal
        ),
        "bracket_trail_amount": _presence(row, "bracket_trail_amount", _decimal),
        "trail_amount": _nullable_decimal(_item(row, "trail_amount")),
        "cancellation_reason": _nullable_text(_item(row, "cancellation_reason")),
        "mmp": _presence(
            row,
            "mmp",
            lambda raw: _observed(raw, orders.MmpLevel, orders.ObservedMmpLevel),
        ),
        "stop_trigger_method": _presence(
            row,
            "stop_trigger_method",
            lambda raw: _observed(raw, orders.StopTriggerMethod, orders.ObservedStopTriggerMethod),
        ),
        "time_in_force": _presence(
            row,
            "time_in_force",
            lambda raw: _observed(raw, orders.TimeInForce, orders.ObservedTimeInForce),
        ),
        "updated_at": parse_epoch_microseconds(_item(row, "updated_at")),
        "paid_commission": _presence(row, "paid_commission", _decimal),
        "commission": _presence(row, "commission", _decimal),
        "reduce_only": _reduce_only(_item(row, "reduce_only")),
        "client_order_id": _presence(row, "client_order_id", _text),
        "state": _observed(_item(row, "state"), orders.OrderState, orders.ObservedOrderState),
        "created_at": parse_epoch_microseconds(_item(row, "created_at")),
        "product_id": product_id,
        "product_symbol": _text(_item(row, "product_symbol")),
    }
    return orders.Order.model_validate(payload)


def _validate_product_tree(value: object, scalar_limit: int) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = list.pop(stack)
        nodes += 1
        if nodes > _MAX_PRODUCT_NODES or depth > _MAX_PRODUCT_DEPTH:
            raise ValueError
        if type(current) is dict:
            for key, child in dict.items(current):
                if type(key) is not str or len(key.encode("utf-8")) > scalar_limit:
                    raise ValueError
                stack.append((child, depth + 1))
        elif type(current) is list:
            for index in range(list.__len__(current)):
                stack.append((list.__getitem__(current, index), depth + 1))
        elif type(current) is str:
            if len(current.encode("utf-8")) > scalar_limit:
                raise ValueError
        elif type(current) is int:
            if len(str(current)) > scalar_limit:
                raise ValueError
        elif type(current) is Decimal:
            if not current.is_finite() or len(str(current)) > scalar_limit:
                raise ValueError
        elif current is not None and type(current) is not bool:
            raise ValueError


def _position_unsafe(
    value: object,
    endpoint_id: object,
    expected_user_id: object,
    allowed_product_ids: object,
) -> positions.MarginedPosition:
    _, scalar_limit = _route(
        endpoint_id,
        (PrivateResponseSchema.MARGINED_POSITION, PrivateResponseSchema.MARGINED_POSITION_LIST),
    )
    expected = _user(expected_user_id)
    allowed = _allowed(allowed_product_ids)
    row = _object(value, _POSITION_FIELDS)
    product_id = _product_id(_item(row, "product_id"))
    if allowed is not None and product_id not in allowed:
        raise ValueError
    product = _item(row, "product")
    _validate_product_tree(product, scalar_limit)
    if type(product) is not dict:
        raise ValueError
    extras = tuple(
        key for key in dict.keys(product) if type(key) is str and key not in ("id", "symbol")
    )
    product_row = _object(product, ("id", "symbol"), extras)
    nested_id = _product_id(_item(product_row, "id"))
    symbol = _text(_item(row, "product_symbol"))
    nested_symbol = _text(_item(product_row, "symbol"))
    if nested_id != product_id or nested_symbol != symbol:
        raise ValueError
    payload = {
        "user_id": _wire_user(_item(row, "user_id"), expected),
        "size": _integer(_item(row, "size")),
        "entry_price": _decimal(_item(row, "entry_price")),
        "margin": _decimal(_item(row, "margin")),
        "liquidation_price": _nullable_decimal(_item(row, "liquidation_price")),
        "bankruptcy_price": _nullable_decimal(_item(row, "bankruptcy_price")),
        "adl_level": _nullable_integer(_item(row, "adl_level")),
        "product_id": product_id,
        "product_symbol": symbol,
        "product": {"id": nested_id, "symbol": nested_symbol},
        "commission": _decimal(_item(row, "commission")),
        "realized_pnl": _decimal(_item(row, "realized_pnl")),
        "realized_funding": _decimal(_item(row, "realized_funding")),
        "realized_cashflow": _decimal(_item(row, "realized_cashflow")),
        "realized_holding_cost": _decimal(_item(row, "realized_holding_cost")),
        "unrealized_pnl": _decimal(_item(row, "unrealized_pnl")),
        "unrealized_cashflow": _decimal(_item(row, "unrealized_cashflow")),
        "mark_price": _decimal(_item(row, "mark_price")),
        "margin_mode": _observed(
            _item(row, "margin_mode"), positions.MarginMode, positions.ObservedMarginMode
        ),
        "auto_topup": _boolean(_item(row, "auto_topup")),
        "created_at": parse_position_iso_datetime(_item(row, "created_at")),
        "updated_at": parse_position_iso_datetime(_item(row, "updated_at")),
        "potentially_delayed": True,
    }
    return positions.MarginedPosition.model_validate(payload)


def _leverage_unsafe(
    value: object, endpoint_id: object, expected_product_id: object
) -> orders.Leverage:
    _route(endpoint_id, (PrivateResponseSchema.LEVERAGE,))
    expected = _product_id(expected_product_id)
    row = _object(value, ("leverage", "order_margin", "product_id"))
    product_id = _product_id(_item(row, "product_id"))
    if product_id != expected:
        raise ValueError
    raw = _item(row, "leverage")
    if type(raw) is str:
        leverage = parse_private_decimal_text(raw)
        if raw != format(leverage, "f") or leverage <= 0:
            raise ValueError
        encoding = orders.LeverageEncoding.DECIMAL_STRING
    elif type(raw) is int and raw > 0:
        leverage = Decimal(raw)
        encoding = orders.LeverageEncoding.JSON_INTEGER
    else:
        raise ValueError
    return orders.Leverage(
        leverage=leverage,
        leverage_encoding=encoding,
        order_margin=_decimal(_item(row, "order_margin")),
        product_id=product_id,
    )


def _margin_mode_unsafe(
    value: object, endpoint_id: object, expected_user_id: object
) -> orders.MarginModeResult:
    _route(endpoint_id, (PrivateResponseSchema.MARGIN_MODE,))
    expected = _user(expected_user_id)
    row = _object(value, ("id", "margin_mode"))
    identity = _user(_item(row, "id"))
    if identity != expected:
        raise ValueError
    return orders.MarginModeResult(
        id=identity,
        margin_mode=_observed(
            _item(row, "margin_mode"), positions.MarginMode, positions.ObservedMarginMode
        ),
    )


def _parse_order_payload(
    value: object,
    *,
    endpoint_id: PrivateRouteId,
    expected_user_id: ExternalUserText,
    allowed_product_ids: tuple[PrivateId, ...] | None,
) -> orders.Order:
    """Parse one complete Order row without envelope or write semantics."""
    operation = _operation(endpoint_id)
    try:
        parsed = _order_unsafe(value, endpoint_id, expected_user_id, allowed_product_ids)
    except Exception:
        parsed = None
    if parsed is None:
        del value, endpoint_id, expected_user_id, allowed_product_ids
        raise ExchangeProtocolError(context={"operation": operation}) from None
    return parsed


def _parse_margined_position_payload(
    value: object,
    *,
    endpoint_id: PrivateRouteId,
    expected_user_id: ExternalUserText,
    allowed_product_ids: tuple[PrivateId, ...] | None,
) -> positions.MarginedPosition:
    """Parse one complete rich Position row and discard Product extras."""
    operation = _operation(endpoint_id)
    try:
        parsed = _position_unsafe(value, endpoint_id, expected_user_id, allowed_product_ids)
    except Exception:
        parsed = None
    if parsed is None:
        del value, endpoint_id, expected_user_id, allowed_product_ids
        raise ExchangeProtocolError(context={"operation": operation}) from None
    return parsed


def _parse_leverage_payload(
    value: object,
    *,
    endpoint_id: PrivateRouteId,
    expected_product_id: PrivateId,
) -> orders.Leverage:
    """Parse one exact leverage result and preserve its observed encoding."""
    operation = _operation(endpoint_id)
    try:
        parsed = _leverage_unsafe(value, endpoint_id, expected_product_id)
    except Exception:
        parsed = None
    if parsed is None:
        del value, endpoint_id, expected_product_id
        raise ExchangeProtocolError(context={"operation": operation}) from None
    return parsed


def _parse_margin_mode_payload(
    value: object,
    *,
    endpoint_id: PrivateRouteId,
    expected_user_id: ExternalUserText,
) -> orders.MarginModeResult:
    """Parse one exact margin-mode result with its route-specific string ID."""
    operation = _operation(endpoint_id)
    try:
        parsed = _margin_mode_unsafe(value, endpoint_id, expected_user_id)
    except Exception:
        parsed = None
    if parsed is None:
        del value, endpoint_id, expected_user_id
        raise ExchangeProtocolError(context={"operation": operation}) from None
    return parsed
