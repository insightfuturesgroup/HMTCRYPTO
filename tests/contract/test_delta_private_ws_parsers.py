"""Official-sample contracts for Delta private channel parsers."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.private_ws_models import (
    MarginUpdate,
    OrderSnapshot,
    OrderUpdate,
    PositionSnapshot,
    PositionUpdate,
    UserTradeUpdate,
)
from hmt_crypto.exchange.delta.private_ws_parsers import parse_private_ws_observation
from hmt_crypto.exchange.delta.websocket_codec import decode_ws_object


def parse(payload: dict[str, object]):
    frame = json.dumps(payload, separators=(",", ":"))
    return parse_private_ws_observation(
        decode_ws_object(frame, max_bytes=64 * 1024, stream_label="private")
    )


def margin_payload() -> dict[str, object]:
    return {
        "action": "update",
        "asset_id": 2,
        "asset_symbol": "BTC",
        "available_balance": "9.385",
        "available_balance_for_robo": "9.385",
        "balance": "10",
        "blocked_margin": "0.615",
        "commission": "0.001",
        "cross_asset_liability": "0",
        "cross_commission": "0.002",
        "cross_locked_collateral": "0.003",
        "cross_order_margin": "0.004",
        "cross_position_margin": "0.005",
        "id": 1,
        "interest_credit": "0",
        "order_margin": "0.1",
        "pending_referral_bonus": "0",
        "pending_trading_fee_credit": "0",
        "portfolio_margin": "0.2",
        "position_margin": "0.3",
        "robo_trading_equity": "0",
        "timestamp": 1_719_397_302_569_921,
        "trading_fee_credit": "0",
        "type": "margins",
        "unvested_amount": "0",
        "user_id": 1,
    }


def position_snapshot_payload() -> dict[str, object]:
    return {
        "result": [
            {
                "adl_level": "4.3335",
                "auto_topup": False,
                "bankruptcy_price": "261.82",
                "commission": "17.6571408",
                "created_at": "2021-04-29T07:25:59Z",
                "entry_price": "238.023457888493475682",
                "liquidation_price": "260.63",
                "margin": "4012.99",
                "product_id": 357,
                "product_symbol": "ZECUSD",
                "realized_funding": "-3.08",
                "realized_pnl": "6364.57",
                "size": -1686,
                "updated_at": "2021-04-29T10:00:05Z",
                "user_id": 1,
                "symbol": "ZECUSD",
            }
        ],
        "success": True,
        "type": "positions",
        "action": "snapshot",
    }


def order_snapshot_payload(symbol: str = "BTCUSD", sequence: int = 7) -> dict[str, object]:
    return {
        "meta": {"seq_no": sequence, "timestamp": 1_594_149_235_554_045},
        "result": [
            {
                "id": 1_592_130,
                "limit_price": "9000",
                "order_type": "limit_order",
                "product_id": 13,
                "reduce_only": False,
                "side": "buy",
                "size": 1,
                "state": "open",
                "stop_order_type": None,
                "stop_price": None,
                "time_in_force": "gtc",
                "trail_amount": None,
                "unfilled_size": 1,
                "average_fill_price": "8999.00",
                "user_id": 1132,
            }
        ],
        "success": True,
        "symbol": symbol,
        "type": "orders",
        "action": "snapshot",
    }


def order_update_payload(sequence: int = 8) -> dict[str, object]:
    return {
        "type": "orders",
        "action": "update",
        "reason": "fill",
        "symbol": "BTCUSD",
        "product_id": 27,
        "order_id": 1234,
        "client_order_id": "HMT-1",
        "size": 100,
        "unfilled_size": 55,
        "average_fill_price": "8999.00",
        "limit_price": "9000.00",
        "side": "buy",
        "cancellation_reason": None,
        "stop_order_type": "stop_loss_order",
        "bracket_order": False,
        "state": "open",
        "seq_no": sequence,
        "timestamp": 1_594_105_083_998_848,
        "stop_price": "9010.00",
        "trigger_price_max_or_min": "9020.00",
        "bracket_stop_loss_price": "8090.00",
        "bracket_stop_loss_limit_price": "8090.00",
        "bracket_take_profit_price": "9020",
        "bracket_take_profit_limit_price": "9020",
        "bracket_trail_amount": "10.00",
    }


def user_trade_payload(sequence: int = 4) -> dict[str, object]:
    return {
        "type": "v2/user_trades",
        "sy": "BTCUSD",
        "f": "1234-abcd-qwer-3456",
        "R": "normal",
        "u": 1998,
        "o": 3283999,
        "S": "buy",
        "s": 190,
        "p": "17289.2",
        "po": 5,
        "r": "taker",
        "c": "GA123",
        "t": 1_685_794_274_866_438,
        "se": sequence,
    }


def test_current_official_private_samples_parse_to_exact_types() -> None:
    margin = parse(margin_payload())
    position = parse(position_snapshot_payload())
    order = parse(order_snapshot_payload())
    trade = parse(user_trade_payload())

    assert isinstance(margin, MarginUpdate)
    assert margin.balance == Decimal("10")
    assert isinstance(position, PositionSnapshot)
    assert position.rows[0].size == -1686
    assert isinstance(order, OrderSnapshot)
    assert order.sequence == 7
    assert isinstance(trade, UserTradeUpdate)
    assert trade.fee_provisional is True
    assert trade.price == Decimal("17289.2")


def test_documented_incremental_position_and_order_parse() -> None:
    position = parse(
        {
            "type": "positions",
            "action": "update",
            "reason": None,
            "symbol": "BTCUSD",
            "product_id": 1,
            "size": -100,
            "margin": "0.0121",
            "entry_price": "3500.0",
            "liquidation_price": "3356.0",
            "bankruptcy_price": "3300.0",
            "commission": "0.00001212",
        }
    )
    order = parse(order_update_payload())
    assert isinstance(position, PositionUpdate)
    assert position.row.size == -100
    assert isinstance(order, OrderUpdate)
    assert order.sequence == 8


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"secret": "must-not-be-retained"}),
        lambda value: value.update({"se": 0}),
        lambda value: value.update({"S": "hold"}),
        lambda value: value.update({"p": "NaN"}),
        lambda value: value.pop("f"),
    ],
)
def test_unknown_malformed_or_missing_trade_fields_fail_closed(mutate) -> None:
    payload = user_trade_payload()
    mutate(payload)
    with pytest.raises(ExchangeProtocolError) as captured:
        parse(payload)
    assert "must-not-be-retained" not in str(captured.value)


def test_unknown_channels_and_snapshot_shape_drift_fail_closed() -> None:
    with pytest.raises(ExchangeProtocolError):
        parse({"type": "portfolio_margins"})
    payload = order_snapshot_payload()
    payload["meta"] = {"seq_id": 7, "timestamp": 1_594_149_235_554_045}
    with pytest.raises(ExchangeProtocolError):
        parse(payload)


def test_oversized_frame_and_snapshot_row_count_fail_before_publication() -> None:
    oversized = json.dumps({"type": "unknown", "padding": "x" * (64 * 1024)})
    with pytest.raises(ExchangeProtocolError):
        decode_ws_object(oversized, max_bytes=64 * 1024, stream_label="private")

    payload = order_snapshot_payload()
    payload["result"] = [payload["result"][0]] * 65  # type: ignore[index]
    with pytest.raises(ExchangeProtocolError):
        parse(payload)
