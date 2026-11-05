"""Current Delta WebSocket channel allowlist contracts (manifest F07/F12/F17)."""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.base import CandleResolution


def ws_api() -> Any:
    try:
        return import_module("hmt_crypto.exchange.delta.websocket_contracts")
    except ModuleNotFoundError:
        pytest.fail("WebSocket channel contracts are missing", pytrace=False)


def test_current_channel_allowlist_is_exact_and_legacy_names_are_absent() -> None:
    api = ws_api()
    assert tuple(channel.value for channel in api.WsChannel) == (
        "ticker",
        "ob_l1",
        "ob_l2",
        "ob_updates",
        "trades",
        "mark_price",
        "spot_price",
        "funding_rate",
        "product_updates",
        "system_status",
        "orders",
        "positions",
        "margins",
        "v2/user_trades",
    )
    for legacy in (
        "v2/ticker",
        "l1_orderbook",
        "l2_orderbook",
        "l2_updates",
        "l2_update",
        "all_trades",
        "v2/spot_price",
        "auth",
        "user_trades",
        "spot_30mtwap_price",
    ):
        with pytest.raises(ValueError):
            api.WsChannel(legacy)


def test_plane_specific_channel_admission_rejects_cross_plane_values() -> None:
    api = ws_api()
    assert api.require_ws_channel(api.WsChannel.TICKER, plane=api.WsPlane.PUBLIC) == "ticker"
    assert api.require_ws_channel(api.WsChannel.ORDERS, plane=api.WsPlane.PRIVATE) == "orders"
    with pytest.raises(ConfigurationError):
        api.require_ws_channel(api.WsChannel.ORDERS, plane=api.WsPlane.PUBLIC)
    with pytest.raises(ConfigurationError):
        api.require_ws_channel(api.WsChannel.TICKER, plane=api.WsPlane.PRIVATE)
    with pytest.raises(ConfigurationError):
        api.require_ws_channel("ticker", plane=api.WsPlane.PUBLIC)


def test_websocket_candle_resolutions_are_exact_and_include_ws_only_twelve_hours() -> None:
    api = ws_api()
    expected = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w")
    assert tuple(resolution.value for resolution in api.WsCandleResolution) == expected
    assert tuple(api.candle_channel(resolution) for resolution in api.WsCandleResolution) == tuple(
        f"candlestick_{resolution}" for resolution in expected
    )
    assert "12h" not in {resolution.value for resolution in CandleResolution}
    with pytest.raises(ConfigurationError):
        api.candle_channel("1m")


def test_product_updates_is_named_but_default_runtime_admission_is_off() -> None:
    api = ws_api()
    with pytest.raises(ConfigurationError):
        api.require_ws_channel(api.WsChannel.PRODUCT_UPDATES, plane=api.WsPlane.PUBLIC)
    assert (
        api.require_ws_channel(
            api.WsChannel.PRODUCT_UPDATES,
            plane=api.WsPlane.PUBLIC,
            allow_testnet_assumption=True,
        )
        == "product_updates"
    )
