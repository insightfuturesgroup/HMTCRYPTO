"""Credential-free Delta India WebSocket endpoint and channel policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hmt_crypto.config import ExecutionMode
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.endpoints import (
    DeltaEndpointSet,
    StreamingCapability,
    _validated_endpoint_snapshot,
)


class WsPlane(StrEnum):
    """Physical Delta WebSocket planes admitted by local policy."""

    PUBLIC = "public"
    PRIVATE = "private"


class WsChannel(StrEnum):
    """Current Phase 3 channel allowlist from source-manifest F09–F17."""

    TICKER = "ticker"
    OB_L1 = "ob_l1"
    OB_L2 = "ob_l2"
    OB_UPDATES = "ob_updates"
    TRADES = "trades"
    MARK_PRICE = "mark_price"
    SPOT_PRICE = "spot_price"
    FUNDING_RATE = "funding_rate"
    PRODUCT_UPDATES = "product_updates"
    SYSTEM_STATUS = "system_status"
    ORDERS = "orders"
    POSITIONS = "positions"
    MARGINS = "margins"
    V2_USER_TRADES = "v2/user_trades"


class WsCandleResolution(StrEnum):
    """Documented streaming-candle resolutions, including WS-only 12h."""

    ONE_MINUTE = "1m"
    THREE_MINUTES = "3m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    THIRTY_MINUTES = "30m"
    ONE_HOUR = "1h"
    TWO_HOURS = "2h"
    FOUR_HOURS = "4h"
    SIX_HOURS = "6h"
    TWELVE_HOURS = "12h"
    ONE_DAY = "1d"
    ONE_WEEK = "1w"


@dataclass(frozen=True, slots=True)
class WsEndpoint:
    """An independently owned, transport-ready India WebSocket endpoint."""

    plane: WsPlane
    url: str
    region: str
    environment: str


_PUBLIC_CHANNELS = frozenset(
    {
        WsChannel.TICKER,
        WsChannel.OB_L1,
        WsChannel.OB_L2,
        WsChannel.OB_UPDATES,
        WsChannel.TRADES,
        WsChannel.MARK_PRICE,
        WsChannel.SPOT_PRICE,
        WsChannel.FUNDING_RATE,
        WsChannel.PRODUCT_UPDATES,
        WsChannel.SYSTEM_STATUS,
    }
)
_PRIVATE_CHANNELS = frozenset(
    {
        WsChannel.ORDERS,
        WsChannel.POSITIONS,
        WsChannel.MARGINS,
        WsChannel.V2_USER_TRADES,
    }
)
# Source-manifest F01: exact documented Delta India WebSocket host tuples.
_PUBLIC_ENDPOINTS = {
    ("india", "prod", "wss://public-socket.india.delta.exchange"),
    ("india", "testnet", "wss://socket-ind-pub.testnet.deltaex.org"),
}
_PRIVATE_ENDPOINTS = {
    ("india", "testnet", "wss://socket-ind.testnet.deltaex.org"),
}


def _configuration_error(operation: str) -> ConfigurationError:
    return ConfigurationError(context={"operation": operation})


def candle_channel(resolution: WsCandleResolution) -> str:
    """Return one documented candlestick channel without widening REST enums."""
    if type(resolution) is not WsCandleResolution:
        raise _configuration_error("select_delta_ws_channel")
    return f"candlestick_{resolution.value}"


def require_ws_channel(
    channel: WsChannel,
    *,
    plane: WsPlane,
    allow_testnet_assumption: bool = False,
) -> str:
    """Admit one current channel for its exact plane and capability state."""
    if (
        type(channel) is not WsChannel
        or type(plane) is not WsPlane
        or type(allow_testnet_assumption) is not bool
    ):
        raise _configuration_error("select_delta_ws_channel")
    allowed = _PUBLIC_CHANNELS if plane is WsPlane.PUBLIC else _PRIVATE_CHANNELS
    if channel not in allowed:
        raise _configuration_error("select_delta_ws_channel")
    if channel is WsChannel.PRODUCT_UPDATES and not allow_testnet_assumption:
        raise _configuration_error("select_delta_ws_channel")
    return channel.value


def validate_ws_endpoint_for_transport(endpoint: WsEndpoint) -> WsEndpoint:
    """Repeat the exact India tuple allowlist immediately before connecting."""
    if (
        type(endpoint) is not WsEndpoint
        or type(endpoint.plane) is not WsPlane
        or type(endpoint.url) is not str
        or type(endpoint.region) is not str
        or type(endpoint.environment) is not str
    ):
        raise _configuration_error("validate_delta_ws_endpoint")
    row = (endpoint.region, endpoint.environment, endpoint.url)
    allowed = _PUBLIC_ENDPOINTS if endpoint.plane is WsPlane.PUBLIC else _PRIVATE_ENDPOINTS
    if row not in allowed:
        raise _configuration_error("validate_delta_ws_endpoint")
    return WsEndpoint(
        plane=endpoint.plane,
        url=endpoint.url,
        region=endpoint.region,
        environment=endpoint.environment,
    )


def require_delta_ws_endpoint(
    endpoints: DeltaEndpointSet,
    *,
    plane: WsPlane,
    mode: ExecutionMode,
) -> WsEndpoint:
    """Select one reviewed India endpoint while retaining every disabled gate."""
    if type(plane) is not WsPlane or type(mode) is not ExecutionMode:
        raise _configuration_error("select_delta_ws_endpoint")
    try:
        source = _validated_endpoint_snapshot(endpoints)
    except ConfigurationError:
        raise _configuration_error("select_delta_ws_endpoint") from None
    if (
        source.region != "india"
        or source.is_development_override
        or source.streaming_capability is not StreamingCapability.ENABLED
    ):
        raise _configuration_error("select_delta_ws_endpoint")
    if plane is WsPlane.PRIVATE and (
        source.environment != "testnet" or mode is not ExecutionMode.LIVE
    ):
        raise _configuration_error("select_delta_ws_endpoint")
    url = source.public_ws_url if plane is WsPlane.PUBLIC else source.private_ws_url
    if type(url) is not str:
        raise _configuration_error("select_delta_ws_endpoint")
    try:
        return validate_ws_endpoint_for_transport(
            WsEndpoint(
                plane=plane,
                url=url,
                region=source.region,
                environment=source.environment,
            )
        )
    except ConfigurationError:
        raise _configuration_error("select_delta_ws_endpoint") from None


__all__ = [
    "WsCandleResolution",
    "WsChannel",
    "WsEndpoint",
    "WsPlane",
    "candle_channel",
    "require_delta_ws_endpoint",
    "require_ws_channel",
    "validate_ws_endpoint_for_transport",
]
