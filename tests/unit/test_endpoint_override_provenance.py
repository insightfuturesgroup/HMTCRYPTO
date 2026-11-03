"""Development endpoint overrides cannot forge canonical Delta provenance."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from hmt_crypto import cli_runtime
from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode, Settings
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints

pytestmark = pytest.mark.unit

_CANONICAL_MISMATCHES = (
    ("india", "testnet", "https://api.india.delta.exchange"),
    ("india", "testnet", "https://api.delta.exchange"),
    ("india", "testnet", "https://testnet-api.delta.exchange"),
    ("global", "testnet", "https://api.delta.exchange"),
    ("global", "testnet", "https://api.india.delta.exchange"),
    ("global", "testnet", "https://cdn-ind.testnet.deltaex.org"),
)


@pytest.mark.parametrize(("region", "environment", "override"), _CANONICAL_MISMATCHES)
def test_known_canonical_host_cannot_cross_selected_source(
    region: str, environment: str, override: str
) -> None:
    with pytest.raises(ConfigurationError) as raised:
        get_delta_endpoints(
            region,
            environment,
            development_rest_base_url=override,
            allow_development_override=True,
        )

    rendered = repr(raised.value.to_dict()) + str(raised.value)
    assert override not in rendered


@pytest.mark.parametrize(
    "override",
    [
        "http://cdn-ind.testnet.deltaex.org",
        "https://cdn-ind.testnet.deltaex.org:444",
    ],
)
def test_selected_known_host_with_wrong_origin_is_not_a_custom_override(override: str) -> None:
    with pytest.raises(ConfigurationError):
        get_delta_endpoints(
            "india",
            "testnet",
            development_rest_base_url=override,
            allow_development_override=True,
        )


@pytest.mark.parametrize(
    ("region", "override", "canonical_url"),
    [
        (
            "india",
            "HTTPS://CDN-IND.TESTNET.DELTAEX.ORG:443/",
            "https://cdn-ind.testnet.deltaex.org",
        ),
        (
            "global",
            "HTTPS://TESTNET-API.DELTA.EXCHANGE:443/",
            "https://testnet-api.delta.exchange",
        ),
    ],
)
def test_exact_selected_canonical_override_collapses_to_canonical_evidence(
    region: str, override: str, canonical_url: str
) -> None:
    endpoints = get_delta_endpoints(
        region,
        "testnet",
        development_rest_base_url=override,
        allow_development_override=True,
    )
    settings = Settings(
        _env_file=None,
        DELTA_REGION=region,
        DELTA_ENV="testnet",
        DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE=True,
        DELTA_DEVELOPMENT_REST_BASE_URL=override,
    )

    assert endpoints.rest_base_url == canonical_url
    assert endpoints.is_development_override is False
    assert endpoints.streaming_capability.value != "disabled_development_override"
    assert "development_rest_host" not in settings.safe_diagnostics()


@pytest.mark.parametrize(("region", "environment", "override"), _CANONICAL_MISMATCHES)
def test_canonical_crossover_fails_before_http_transport_construction(
    monkeypatch: pytest.MonkeyPatch,
    region: str,
    environment: str,
    override: str,
) -> None:
    def transport_forbidden(*args: object, **kwargs: object) -> Any:
        pytest.fail("canonical mismatch reached HTTP transport construction")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport_forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", transport_forbidden)
    settings = SimpleNamespace(
        delta_region=DeltaRegion(region),
        delta_env=DeltaEnvironment(environment),
        delta_development_rest_base_url=override,
        delta_allow_development_endpoint_override=True,
        execution_mode=ExecutionMode.PAPER,
    )

    with pytest.raises(ConfigurationError):
        cli_runtime._build_public_client(settings)  # type: ignore[arg-type]


@pytest.mark.parametrize("command", ["sync", "show"])
@pytest.mark.asyncio
async def test_custom_override_refuses_trusted_catalogue_before_client_construction(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    marker = "catalog-override-marker.example"
    settings = Settings(
        _env_file=None,
        DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE=True,
        DELTA_DEVELOPMENT_REST_BASE_URL=f"http://{marker}:9010",
    )

    def client_forbidden(settings: Settings) -> Any:
        pytest.fail("custom catalogue override reached client construction")

    monkeypatch.setattr(cli_runtime, "_build_public_client", client_forbidden)

    with pytest.raises(ConfigurationError) as raised:
        if command == "sync":
            await cli_runtime.products_sync_payload(settings)
        else:
            await cli_runtime.product_show_payload(
                settings,
                product_id=None,
                symbol="BTCUSD",
            )

    assert marker not in repr(raised.value.to_dict()) + str(raised.value)


@pytest.mark.parametrize(("region", "environment", "override"), _CANONICAL_MISMATCHES)
def test_settings_rejects_canonical_crossover_without_reflecting_url(
    region: str, environment: str, override: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(
            _env_file=None,
            DELTA_REGION=region,
            DELTA_ENV=environment,
            DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE=True,
            DELTA_DEVELOPMENT_REST_BASE_URL=override,
        )

    rendered = str(raised.value) + repr(raised.value.errors()) + raised.value.json()
    assert override not in rendered
