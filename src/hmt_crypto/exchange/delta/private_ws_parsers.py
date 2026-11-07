"""Strict parsers for current documented Delta India private WebSocket frames."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.private_ws_models import (
    MarginUpdate,
    OrderRow,
    OrderSnapshot,
    OrderUpdate,
    PositionRow,
    PositionSnapshot,
    PositionUpdate,
    PrivateWsAction,
    PrivateWsObservation,
    UserTradeUpdate,
)
from hmt_crypto.exchange.delta.websocket_codec import WsNumberToken

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9_.:/-]{0,63}\Z")
_MAX_ROWS = 64


def _error() -> ExchangeProtocolError:
    return ExchangeProtocolError(
        context={"operation": "parse_delta_private_ws", "scope": "private"}
    )


def _keys(value: object, required: set[str], optional: set[str] | None = None) -> dict[str, object]:
    admitted = set() if optional is None else optional
    if type(value) is not dict or not required <= set(value) <= required | admitted:
        raise ValueError("schema keys")
    return value


def _text(value: object, *, maximum: int = 256, empty: bool = False) -> str:
    if (
        type(value) is not str
        or (not empty and not value)
        or len(value) > maximum
        or any(not character.isprintable() for character in value)
    ):
        raise ValueError("text")
    return value


def _symbol(value: object) -> str:
    raw = _text(value, maximum=64)
    if _SYMBOL.fullmatch(raw) is None:
        raise ValueError("symbol")
    return raw


def _number(value: object) -> Decimal:
    if type(value) is WsNumberToken:
        parsed = value.value
    elif type(value) is str and len(value) <= 1024 and _NUMBER.fullmatch(value) is not None:
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            raise ValueError("number") from None
    else:
        raise ValueError("number")
    if not parsed.is_finite():
        raise ValueError("number")
    return parsed


def _integer(value: object, *, nonnegative: bool = True) -> int:
    parsed = _number(value)
    if parsed != parsed.to_integral_value() or (nonnegative and parsed < 0):
        raise ValueError("integer")
    result = int(parsed)
    if abs(result) >= 2**63:
        raise ValueError("integer bound")
    return result


def _positive_integer(value: object) -> int:
    result = _integer(value)
    if result <= 0:
        raise ValueError("positive integer")
    return result


def _microseconds(value: object) -> datetime:
    raw = _integer(value)
    try:
        return _EPOCH + timedelta(microseconds=raw)
    except OverflowError:
        raise ValueError("timestamp") from None


def _optional_number(value: object) -> Decimal | None:
    return None if value is None else _number(value)


def _optional_text(value: object, *, empty_is_none: bool = False) -> str | None:
    if value is None:
        return None
    result = _text(value, empty=True)
    return None if empty_is_none and result == "" else result


_MARGIN_KEYS = {
    "action",
    "asset_id",
    "asset_symbol",
    "available_balance",
    "available_balance_for_robo",
    "balance",
    "blocked_margin",
    "commission",
    "cross_asset_liability",
    "cross_commission",
    "cross_locked_collateral",
    "cross_order_margin",
    "cross_position_margin",
    "id",
    "interest_credit",
    "order_margin",
    "pending_referral_bonus",
    "pending_trading_fee_credit",
    "portfolio_margin",
    "position_margin",
    "robo_trading_equity",
    "timestamp",
    "trading_fee_credit",
    "type",
    "unvested_amount",
    "user_id",
}
_MARGIN_DECIMALS = _MARGIN_KEYS - {
    "action",
    "asset_id",
    "asset_symbol",
    "id",
    "timestamp",
    "type",
    "user_id",
}


def _parse_margin(message: object) -> MarginUpdate:
    fields = _keys(message, _MARGIN_KEYS)
    if fields["type"] != "margins" or fields["action"] != "update":
        raise ValueError("margin control")
    for name in _MARGIN_DECIMALS:
        _number(fields[name])
    return MarginUpdate(
        asset_id=_positive_integer(fields["asset_id"]),
        asset_symbol=_text(fields["asset_symbol"], maximum=32),
        wallet_id=_positive_integer(fields["id"]),
        user_id=_positive_integer(fields["user_id"]),
        balance=_number(fields["balance"]),
        available_balance=_number(fields["available_balance"]),
        blocked_margin=_number(fields["blocked_margin"]),
        observed_at=_microseconds(fields["timestamp"]),
    )


_POSITION_UPDATE_KEYS = {
    "type",
    "action",
    "reason",
    "symbol",
    "product_id",
    "size",
    "margin",
    "entry_price",
    "liquidation_price",
    "bankruptcy_price",
    "commission",
}
_POSITION_SNAPSHOT_ROW_KEYS = {
    "adl_level",
    "auto_topup",
    "bankruptcy_price",
    "commission",
    "created_at",
    "entry_price",
    "liquidation_price",
    "margin",
    "product_id",
    "product_symbol",
    "realized_funding",
    "realized_pnl",
    "size",
    "updated_at",
    "user_id",
    "symbol",
}


def _position_row(fields: dict[str, object], *, snapshot: bool) -> PositionRow:
    symbol = _symbol(fields["symbol"])
    if snapshot and _symbol(fields["product_symbol"]) != symbol:
        raise ValueError("position symbol mismatch")
    if snapshot:
        if type(fields["auto_topup"]) is not bool:
            raise ValueError("auto topup")
        for name in ("adl_level", "realized_funding", "realized_pnl"):
            _number(fields[name])
        for name in ("created_at", "updated_at"):
            raw = _text(fields[name])
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("position time") from None
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("position time")
    return PositionRow(
        symbol=symbol,
        product_id=_positive_integer(fields["product_id"]),
        user_id=_positive_integer(fields["user_id"]) if snapshot else None,
        size=_integer(fields["size"], nonnegative=False),
        margin=_number(fields["margin"]),
        entry_price=_number(fields["entry_price"]),
        liquidation_price=_number(fields["liquidation_price"]),
        bankruptcy_price=_number(fields["bankruptcy_price"]),
        commission=_number(fields["commission"]),
    )


def _parse_positions(message: object) -> PositionUpdate | PositionSnapshot:
    if type(message) is not dict:
        raise ValueError("position object")
    action = message.get("action")
    if action == "snapshot":
        outer = _keys(message, {"type", "action", "success", "result"})
        if outer["type"] != "positions" or outer["success"] is not True:
            raise ValueError("position snapshot")
        rows = outer["result"]
        if type(rows) is not list or len(rows) > _MAX_ROWS:
            raise ValueError("position rows")
        return PositionSnapshot(
            rows=tuple(
                _position_row(_keys(row, _POSITION_SNAPSHOT_ROW_KEYS), snapshot=True)
                for row in rows
            )
        )
    fields = _keys(message, _POSITION_UPDATE_KEYS)
    if fields["type"] != "positions":
        raise ValueError("position type")
    parsed_action = PrivateWsAction(_text(action))
    if parsed_action not in {
        PrivateWsAction.CREATE,
        PrivateWsAction.UPDATE,
        PrivateWsAction.DELETE,
    }:
        raise ValueError("position action")
    reason = _optional_text(fields["reason"], empty_is_none=True)
    if reason not in {None, "auto_topup"}:
        raise ValueError("position reason")
    return PositionUpdate(
        action=parsed_action,
        reason=reason,
        row=_position_row(fields, snapshot=False),
    )


_ORDER_UPDATE_KEYS = {
    "type",
    "action",
    "reason",
    "symbol",
    "product_id",
    "order_id",
    "client_order_id",
    "size",
    "unfilled_size",
    "average_fill_price",
    "limit_price",
    "side",
    "cancellation_reason",
    "stop_order_type",
    "bracket_order",
    "state",
    "seq_no",
    "timestamp",
    "stop_price",
    "trigger_price_max_or_min",
    "bracket_stop_loss_price",
    "bracket_stop_loss_limit_price",
    "bracket_take_profit_price",
    "bracket_take_profit_limit_price",
    "bracket_trail_amount",
}
_ORDER_SNAPSHOT_ROW_KEYS = {
    "id",
    "limit_price",
    "order_type",
    "product_id",
    "reduce_only",
    "side",
    "size",
    "state",
    "stop_order_type",
    "stop_price",
    "time_in_force",
    "trail_amount",
    "unfilled_size",
    "average_fill_price",
    "user_id",
}
_ORDER_REASONS = {
    None,
    "fill",
    "stop_update",
    "stop_trigger",
    "stop_cancel",
    "liquidation",
    "self_trade",
}


def _order_update_row(fields: dict[str, object]) -> OrderRow:
    if type(fields["bracket_order"]) is not bool:
        raise ValueError("bracket order")
    side = _text(fields["side"])
    if side not in {"buy", "sell"}:
        raise ValueError("side")
    state = _text(fields["state"])
    if state not in {"open", "pending", "closed", "cancelled"}:
        raise ValueError("order state")
    for name in (
        "cancellation_reason",
        "stop_order_type",
        "stop_price",
        "trigger_price_max_or_min",
        "bracket_stop_loss_price",
        "bracket_stop_loss_limit_price",
        "bracket_take_profit_price",
        "bracket_take_profit_limit_price",
        "bracket_trail_amount",
    ):
        value = fields[name]
        if value is not None:
            if name.endswith("reason") or name.endswith("type"):
                _text(value, empty=True)
            else:
                _number(value)
    return OrderRow(
        symbol=_symbol(fields["symbol"]),
        product_id=_positive_integer(fields["product_id"]),
        order_id=_positive_integer(fields["order_id"]),
        user_id=None,
        client_order_id=_optional_text(fields["client_order_id"], empty_is_none=True),
        side=side,
        size=_integer(fields["size"]),
        unfilled_size=_integer(fields["unfilled_size"]),
        state=state,
        limit_price=_optional_number(fields["limit_price"]),
        average_fill_price=_optional_number(fields["average_fill_price"]),
        reduce_only=None,
    )


def _order_snapshot_row(value: object, symbol: str) -> OrderRow:
    fields = _keys(value, _ORDER_SNAPSHOT_ROW_KEYS)
    if type(fields["reduce_only"]) is not bool:
        raise ValueError("reduce only")
    side = _text(fields["side"])
    state = _text(fields["state"])
    if side not in {"buy", "sell"} or state not in {"open", "pending"}:
        raise ValueError("snapshot order state")
    for name in ("order_type", "time_in_force"):
        _text(fields[name])
    for name in ("stop_order_type",):
        _optional_text(fields[name])
    for name in ("stop_price", "trail_amount"):
        _optional_number(fields[name])
    user_id = _positive_integer(fields["user_id"])
    return OrderRow(
        symbol=symbol,
        product_id=_positive_integer(fields["product_id"]),
        order_id=_positive_integer(fields["id"]),
        user_id=user_id,
        client_order_id=None,
        side=side,
        size=_integer(fields["size"]),
        unfilled_size=_integer(fields["unfilled_size"]),
        state=state,
        limit_price=_optional_number(fields["limit_price"]),
        average_fill_price=_optional_number(fields["average_fill_price"]),
        reduce_only=fields["reduce_only"],
    )


def _parse_orders(message: object) -> OrderUpdate | OrderSnapshot:
    if type(message) is not dict:
        raise ValueError("order object")
    if message.get("action") == "snapshot":
        outer = _keys(message, {"meta", "result", "success", "symbol", "type", "action"})
        if outer["type"] != "orders" or outer["success"] is not True:
            raise ValueError("order snapshot")
        meta = _keys(outer["meta"], {"seq_no", "timestamp"})
        symbol = _symbol(outer["symbol"])
        rows = outer["result"]
        if type(rows) is not list or len(rows) > _MAX_ROWS:
            raise ValueError("order rows")
        return OrderSnapshot(
            symbol=symbol,
            sequence=_positive_integer(meta["seq_no"]),
            observed_at=_microseconds(meta["timestamp"]),
            rows=tuple(_order_snapshot_row(row, symbol) for row in rows),
        )
    fields = _keys(message, _ORDER_UPDATE_KEYS)
    if fields["type"] != "orders":
        raise ValueError("order type")
    action = PrivateWsAction(_text(fields["action"]))
    if action not in {PrivateWsAction.CREATE, PrivateWsAction.UPDATE, PrivateWsAction.DELETE}:
        raise ValueError("order action")
    reason = _optional_text(fields["reason"], empty_is_none=True)
    if reason not in _ORDER_REASONS:
        raise ValueError("order reason")
    return OrderUpdate(
        action=action,
        reason=reason,
        sequence=_positive_integer(fields["seq_no"]),
        observed_at=_microseconds(fields["timestamp"]),
        row=_order_update_row(fields),
    )


_TRADE_KEYS = {"type", "sy", "f", "R", "u", "o", "S", "s", "p", "po", "r", "c", "t", "se"}


def _parse_user_trade(message: object) -> UserTradeUpdate:
    fields = _keys(message, _TRADE_KEYS)
    if fields["type"] != "v2/user_trades":
        raise ValueError("trade type")
    reason = _text(fields["R"])
    side = _text(fields["S"])
    role = _text(fields["r"])
    if reason not in {"normal", "adl", "liquidation"}:
        raise ValueError("trade reason")
    if side not in {"buy", "sell"} or role not in {"maker", "taker"}:
        raise ValueError("trade classification")
    return UserTradeUpdate(
        symbol=_symbol(fields["sy"]),
        fill_id=_text(fields["f"]),
        reason=reason,
        user_id=_positive_integer(fields["u"]),
        order_id=_positive_integer(fields["o"]),
        side=side,
        size=_integer(fields["s"]),
        price=_number(fields["p"]),
        position_after=_integer(fields["po"], nonnegative=False),
        role=role,
        client_order_id=_text(fields["c"], empty=True),
        observed_at=_microseconds(fields["t"]),
        sequence=_positive_integer(fields["se"]),
    )


def parse_private_ws_observation(message: object) -> PrivateWsObservation:
    """Parse one decoded private data frame, rejecting controls and unknown fields."""
    try:
        if type(message) is not dict or type(message.get("type")) is not str:
            raise ValueError("message")
        channel = message["type"]
        if channel == "margins":
            return _parse_margin(message)
        if channel == "positions":
            return _parse_positions(message)
        if channel == "orders":
            return _parse_orders(message)
        if channel == "v2/user_trades":
            return _parse_user_trade(message)
        raise ValueError("channel")
    except (ValueError, TypeError, InvalidOperation, OverflowError):
        raise _error() from None


__all__ = ["parse_private_ws_observation"]
