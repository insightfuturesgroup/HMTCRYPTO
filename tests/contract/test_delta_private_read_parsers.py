"""Contract tests for reviewed Delta India private-read response grammars."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.private_read_fill_parsers import _parse_fill_page_payload
from hmt_crypto.exchange.delta.private_read_parsers import (
    _parse_balance_snapshot_payload,
    _parse_cursor_meta,
    _parse_latest_position_payload,
    _parse_order_page_payload,
    _parse_wallet_transaction_page_payload,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

pytestmark = pytest.mark.contract

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "delta" / "private" / "mutations"


def _order_payload() -> dict[str, object]:
    envelope = json.loads(
        (_FIXTURE_ROOT / "orders" / "offline_candidate_order_success.json").read_text()
    )
    assert type(envelope) is dict
    result = envelope["result"]
    assert type(result) is dict
    return result


def _balance_payload() -> dict[str, object]:
    return {
        "asset_id": 11,
        "asset_symbol": "USDT",
        "available_balance": "100.25",
        "available_balance_for_robo": "10",
        "balance": "120.25",
        "blocked_margin": "1",
        "commission": "-0.5",
        "cross_asset_liability": "-2",
        "cross_commission": "0.1",
        "cross_locked_collateral": "3",
        "cross_order_margin": "4",
        "cross_position_margin": "5",
        "id": 12,
        "interest_credit": "0.25",
        "order_margin": "6",
        "pending_referral_bonus": "0",
        "pending_trading_fee_credit": "0.75",
        "portfolio_margin": "7",
        "position_margin": "8",
        "trading_fee_credit": "1.25",
        "unvested_amount": "9",
        "user_id": 12345,
    }


def _transaction_payload() -> dict[str, object]:
    return {
        "id": 41,
        "amount": "-1.25",
        "balance": "98.75",
        "transaction_type": "funding",
        "meta_data": {},
        "product_id": 27,
        "asset_id": 11,
        "asset_symbol": "USDT",
        "created_at": "2026-11-05T12:00:00Z",
    }


def _fill_payload() -> dict[str, object]:
    asset = {"symbol": "USDT", "precision": 8, "minimum_precision": 2}
    return {
        "id": "071b551365574d0aad99557931137dbd",
        "size": "1",
        "notional": "123.5",
        "product": {
            "id": 27,
            "symbol": "BTCUSD",
            "contract_type": "perpetual_futures",
            "tick_size": "0.5",
            "contract_value": "1",
            "contract_unit_currency": "BTC",
            "notional_type": "vanilla",
            "quoting_asset": asset,
            "underlying_asset": {"symbol": "BTC", "precision": 8, "minimum_precision": 1},
            "settling_asset": asset,
            "spot_index": {"symbol": "BTCUSD"},
        },
        "fill_type": "normal",
        "side": "buy",
        "price": "123.5",
        "role": "taker",
        "commission": "0.01",
        "created_at": "2026-11-05T12:00:00Z",
        "product_id": 27,
        "product_symbol": "BTCUSD",
        "order_id": "7001",
        "settling_asset_id": 11,
        "settling_asset_symbol": "USDT",
        "meta_data": {
            "commission_deto": "0.01",
            "commission_deto_in_settling_asset": "0.01",
            "effective_commission_rate": "0.001",
            "liquidation_fee_deto": "0",
            "liquidation_fee_deto_in_settling_asset": "0",
            "order_price": "123.5",
            "order_size": "1",
            "order_type": "limit_order",
            "order_unfilled_size": "0",
            "tfc_used_for_commission": "0",
            "tfc_used_for_liquidation_fee": "0",
            "total_commission_in_settling_asset": "0.01",
            "total_liquidation_fee_in_settling_asset": "0",
            "ip": "127.0.0.1",
            "is_commission_on_notional": True,
            "liquidation_fee_in_settling_asset": "0",
            "mark": "123.5",
            "rack_commision": "0",
            "source": "api",
            "spot": "123.5",
            "cut": {"equity": 1.25, "api_trader": -0.5},
            "trading_fee_credits_used": "0",
            "original_referrer_commission": "0",
            "new_position": {
                "size": 1,
                "commission": "0.01",
                "liquidation_price": None,
                "bankruptcy_price": None,
            },
        },
    }


def test_balance_parser_owns_documented_rows_and_equity_meta() -> None:
    """Catch balance rows or signed decimals being coerced, omitted, or inferred."""
    parsed = _parse_balance_snapshot_payload(
        [_balance_payload()],
        {"net_equity": "118.25", "robo_trading_equity": "10"},
        expected_user_id="12345",
    )

    assert type(parsed.balances) is tuple
    assert parsed.balances[0].asset_symbol == "USDT"
    assert parsed.balances[0].cross_asset_liability == Decimal("-2")
    assert parsed.net_equity == Decimal("118.25")
    assert parsed.robo_trading_equity == Decimal("10")


def test_private_read_objects_accept_documented_keys_in_any_json_member_order() -> None:
    """Catch an invalid dependency on JSON object member ordering."""
    balance = _balance_payload()
    reversed_balance = dict(reversed(tuple(balance.items())))

    parsed = _parse_balance_snapshot_payload(
        [reversed_balance],
        {"robo_trading_equity": "10", "net_equity": "118.25"},
        expected_user_id="12345",
    )
    latest = _parse_latest_position_payload(
        {"entry_price": "123.45", "size": -2},
        expected_product_id=27,
    )
    after, before = _parse_cursor_meta(
        {"before": None, "after": "next:abc"},
        endpoint_id=PrivateRouteId.OPEN_ORDERS,
    )

    assert parsed.balances[0].asset_id == 11
    assert latest.signed_contracts == -2
    assert (after, before) == ("next:abc", None)


@pytest.mark.parametrize(
    ("result", "meta"),
    (
        (
            [_balance_payload() | {"user_id": 54321}],
            {"net_equity": "1", "robo_trading_equity": "0"},
        ),
        (
            [_balance_payload() | {"future_field": "x"}],
            {"net_equity": "1", "robo_trading_equity": "0"},
        ),
        ([_balance_payload()], {"net_equity": 1, "robo_trading_equity": "0"}),
        ([_balance_payload()], {"net_equity": "1"}),
    ),
)
def test_balance_parser_fails_closed_on_identity_schema_or_decimal_drift(
    result: object, meta: object
) -> None:
    """Catch a foreign wallet, undocumented key, or coercive equity value crossing the boundary."""
    with pytest.raises(ExchangeProtocolError):
        _parse_balance_snapshot_payload(result, meta, expected_user_id="12345")


def test_latest_position_parser_binds_query_product_id_to_documented_summary() -> None:
    """Catch the result's missing product identity being treated as exchange-provided evidence."""
    parsed = _parse_latest_position_payload(
        {"size": -2, "entry_price": "123.45"},
        expected_product_id=27,
    )

    assert parsed.product_id == 27
    assert parsed.signed_contracts == -2
    assert parsed.entry_price == Decimal("123.45")


@pytest.mark.parametrize(
    "payload",
    (
        {"size": True, "entry_price": "123.45"},
        {"size": -2, "entry_price": 123},
        {"size": -2, "entry_price": "-1"},
        {"size": -2, "entry_price": "123.45", "product_id": 27},
    ),
)
def test_latest_position_parser_rejects_coercion_negative_price_and_extra_fields(
    payload: object,
) -> None:
    """Catch coercive or undocumented latest-position payloads being accepted."""
    with pytest.raises(ExchangeProtocolError):
        _parse_latest_position_payload(payload, expected_product_id=27)


def test_order_page_parser_preserves_both_explicit_cursors_and_complete_rows() -> None:
    """Catch order optionals or one of Delta's two cursor fields being discarded."""
    items, after, before = _parse_order_page_payload(
        [_order_payload()],
        {"after": "next:abc", "before": None},
        endpoint_id=PrivateRouteId.OPEN_ORDERS,
        expected_user_id="12345",
        allowed_product_ids=(27,),
        page_size=10,
    )

    assert len(items) == 1
    assert items[0].time_in_force.kind == "VALUE"
    assert items[0].time_in_force.value.known.value == "gtc"
    assert after == "next:abc"
    assert before is None


@pytest.mark.parametrize(
    "meta",
    (
        {},
        {"after": None},
        {"after": None, "before": None, "extra": "x"},
        {"after": "white space", "before": None},
        {"after": 1, "before": None},
    ),
)
def test_cursor_meta_rejects_missing_extra_or_nonopaque_values(meta: object) -> None:
    """Catch pagination completeness being inferred from malformed metadata."""
    with pytest.raises(ExchangeProtocolError):
        _parse_cursor_meta(meta, endpoint_id=PrivateRouteId.OPEN_ORDERS)


def test_order_page_rejects_more_rows_than_the_requested_page_size() -> None:
    """Catch an oversized response bypassing the local bounded paginator."""
    with pytest.raises(ExchangeProtocolError):
        _parse_order_page_payload(
            [_order_payload(), _order_payload()],
            {"after": None, "before": None},
            endpoint_id=PrivateRouteId.ORDER_HISTORY,
            expected_user_id="12345",
            allowed_product_ids=(27,),
            page_size=1,
        )


def test_order_page_rejects_forward_cursor_without_any_items() -> None:
    """Catch a non-progressing empty page from driving an unbounded cursor walk."""
    with pytest.raises(ExchangeProtocolError):
        _parse_order_page_payload(
            [],
            {"after": "next:cursor", "before": None},
            endpoint_id=PrivateRouteId.ORDER_HISTORY,
            expected_user_id="12345",
            allowed_product_ids=None,
            page_size=10,
        )


def test_wallet_and_fill_pages_parse_reviewed_rows_and_cursors() -> None:
    """Catch either formerly-held cursor feed regressing to raw or offline data."""
    transactions, transaction_after, _ = _parse_wallet_transaction_page_payload(
        [_transaction_payload()],
        {"after": "txn:next", "before": None},
        allowed_asset_id=11,
        allowed_transaction_types=("funding",),
        page_size=10,
    )
    fills, fill_after, _ = _parse_fill_page_payload(
        [_fill_payload()],
        {"after": "fill:next", "before": None},
        allowed_product_ids=(27,),
        allowed_contract_types=("perpetual_futures",),
        page_size=10,
    )

    assert transactions[0].transaction_type.known.value == "funding"
    assert transactions[0].meta_data.value.profile.value == "EMPTY_OBJECT"
    assert transaction_after == "txn:next"
    assert fills[0].id.hex == "071b551365574d0aad99557931137dbd"
    assert fills[0].order_id.order_id == 7001
    assert fills[0].meta_data.value.profile.value == "NORMAL_V1"
    assert fill_after == "fill:next"


@pytest.mark.parametrize(
    ("parser", "payload", "kwargs"),
    (
        (
            _parse_wallet_transaction_page_payload,
            _transaction_payload() | {"meta_data": {"unreviewed": "secret"}},
            {
                "allowed_asset_id": 11,
                "allowed_transaction_types": None,
                "page_size": 10,
            },
        ),
        (
            _parse_fill_page_payload,
            _fill_payload() | {"meta_data": _fill_payload()["meta_data"] | {"future_field": "x"}},
            {
                "allowed_product_ids": (27,),
                "allowed_contract_types": None,
                "page_size": 10,
            },
        ),
    ),
)
def test_wallet_and_fill_pages_fail_closed_on_unreviewed_metadata(
    parser: object,
    payload: object,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ExchangeProtocolError):
        parser(  # type: ignore[operator]
            [payload],
            {"after": None, "before": None},
            **kwargs,
        )


def test_wallet_and_fill_pages_reject_rows_outside_requested_filters() -> None:
    """Catch an exchange off-filter row being relabelled as a requested result."""
    with pytest.raises(ExchangeProtocolError):
        _parse_wallet_transaction_page_payload(
            [_transaction_payload()],
            {"after": None, "before": None},
            allowed_asset_id=11,
            allowed_transaction_types=("commission",),
            page_size=10,
        )
    with pytest.raises(ExchangeProtocolError):
        _parse_fill_page_payload(
            [_fill_payload()],
            {"after": None, "before": None},
            allowed_product_ids=(27,),
            allowed_contract_types=("futures",),
            page_size=10,
        )
