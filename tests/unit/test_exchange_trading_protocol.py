from __future__ import annotations

import inspect

import pytest

from hmt_crypto.exchange.trading import (
    AccountTradingClient,
    PrivateComponent,
    PrivateSubscriptionRequest,
    TradingClient,
)


def test_exchange_neutral_trading_protocol_has_the_reviewed_oms_surface() -> None:
    expected = {
        "place_order",
        "modify_order",
        "cancel_order",
        "cancel_all",
        "set_leverage",
        "set_margin_mode",
        "change_position_margin",
        "create_bracket",
        "edit_bracket",
        "subscribe_private",
    }
    assert expected <= set(vars(TradingClient))
    assert inspect.isclass(AccountTradingClient)


def test_private_subscription_is_bounded_numeric_and_exchange_neutral() -> None:
    request = PrivateSubscriptionRequest(
        product_ids=(27,),
        components=(PrivateComponent.ORDERS, PrivateComponent.POSITIONS),
    )
    assert request.product_ids == (27,)
    assert request.components == (PrivateComponent.ORDERS, PrivateComponent.POSITIONS)

    with pytest.raises(ValueError):
        PrivateSubscriptionRequest(
            product_ids=(27, 27),
            components=(PrivateComponent.ORDERS,),
        )
    with pytest.raises(ValueError):
        PrivateSubscriptionRequest(product_ids=(), components=(PrivateComponent.ORDERS,))
