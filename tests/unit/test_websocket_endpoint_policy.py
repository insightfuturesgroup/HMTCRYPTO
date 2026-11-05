"""India-only WebSocket endpoint admission contracts (manifest F01/F22)."""

from __future__ import annotations

from dataclasses import replace
from importlib import import_module
from typing import Any

import pytest

from hmt_crypto.config import ExecutionMode
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints


def ws_api() -> Any:
    """Load the wished-for module while keeping missing production code as RED."""
    try:
        return import_module("hmt_crypto.exchange.delta.websocket_contracts")
    except ModuleNotFoundError:
        pytest.fail("WebSocket endpoint policy is missing", pytrace=False)


@pytest.mark.parametrize(
    ("environment", "expected_url"),
    (
        ("prod", "wss://public-socket.india.delta.exchange"),
        ("testnet", "wss://socket-ind-pub.testnet.deltaex.org"),
    ),
)
def test_public_india_endpoints_use_only_documented_hosts(
    environment: str, expected_url: str
) -> None:
    api = ws_api()
    endpoint = api.require_delta_ws_endpoint(
        get_delta_endpoints("india", environment),
        plane=api.WsPlane.PUBLIC,
        mode=ExecutionMode.PAPER,
    )

    assert endpoint == api.WsEndpoint(
        plane=api.WsPlane.PUBLIC,
        url=expected_url,
        region="india",
        environment=environment,
    )
    assert api.validate_ws_endpoint_for_transport(endpoint) == endpoint


def test_private_stream_admission_is_india_testnet_live_read_only() -> None:
    api = ws_api()
    endpoint = api.require_delta_ws_endpoint(
        get_delta_endpoints("india", "testnet"),
        plane=api.WsPlane.PRIVATE,
        mode=ExecutionMode.LIVE,
    )

    assert endpoint.url == "wss://socket-ind.testnet.deltaex.org"
    assert api.validate_ws_endpoint_for_transport(endpoint) == endpoint


@pytest.mark.parametrize(
    ("region", "environment", "plane", "mode"),
    (
        ("global", "prod", "PUBLIC", ExecutionMode.PAPER),
        ("global", "testnet", "PUBLIC", ExecutionMode.PAPER),
        ("global", "testnet", "PRIVATE", ExecutionMode.LIVE),
        ("india", "prod", "PRIVATE", ExecutionMode.LIVE),
        ("india", "testnet", "PRIVATE", ExecutionMode.PAPER),
        ("india", "testnet", "PRIVATE", ExecutionMode.BACKTEST),
    ),
)
def test_unverified_or_unsafe_stream_combinations_fail_locally(
    region: str,
    environment: str,
    plane: str,
    mode: ExecutionMode,
) -> None:
    api = ws_api()
    with pytest.raises(ConfigurationError) as error:
        api.require_delta_ws_endpoint(
            get_delta_endpoints(region, environment),
            plane=api.WsPlane[plane],
            mode=mode,
        )
    assert error.value.context == {"operation": "select_delta_ws_endpoint"}


def test_development_override_fails_before_any_stream_host_is_selected() -> None:
    api = ws_api()
    endpoints = get_delta_endpoints(
        "india",
        "testnet",
        development_rest_base_url="http://127.0.0.1:9010",
        allow_development_override=True,
    )

    with pytest.raises(ConfigurationError):
        api.require_delta_ws_endpoint(
            endpoints,
            plane=api.WsPlane.PUBLIC,
            mode=ExecutionMode.PAPER,
        )


@pytest.mark.parametrize(
    "field_update",
    (
        {"url": "wss://attacker.invalid"},
        {"region": "global"},
        {"environment": "prod"},
        {"plane": "public"},
    ),
)
def test_transport_revalidates_exact_type_and_allowlisted_tuple(
    field_update: dict[str, object],
) -> None:
    api = ws_api()
    valid = api.WsEndpoint(
        plane=api.WsPlane.PUBLIC,
        url="wss://socket-ind-pub.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )
    candidate = replace(valid, **field_update)

    with pytest.raises(ConfigurationError) as error:
        api.validate_ws_endpoint_for_transport(candidate)
    assert error.value.context == {"operation": "validate_delta_ws_endpoint"}


def test_endpoint_inputs_require_exact_owned_types() -> None:
    api = ws_api()
    endpoints = get_delta_endpoints("india", "testnet")
    with pytest.raises(ConfigurationError):
        api.require_delta_ws_endpoint(
            endpoints,
            plane="public",
            mode=ExecutionMode.PAPER,
        )
    with pytest.raises(ConfigurationError):
        api.require_delta_ws_endpoint(
            endpoints,
            plane=api.WsPlane.PUBLIC,
            mode="PAPER",
        )
