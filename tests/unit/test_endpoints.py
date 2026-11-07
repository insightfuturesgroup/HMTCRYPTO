"""Delta endpoint registry and development-override safety contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hmt_crypto.config import Settings
from hmt_crypto.errors import ConfigurationError


def endpoint_api() -> Any:
    """Load the wished-for endpoint API without turning RED into collection failure."""
    try:
        return import_module("hmt_crypto.exchange.delta.endpoints")
    except ModuleNotFoundError:
        pytest.fail("Delta endpoint registry API is missing", pytrace=False)


@pytest.mark.parametrize(
    ("region", "environment", "rest", "public_ws", "private_ws", "capability"),
    [
        (
            "india",
            "prod",
            "https://api.india.delta.exchange",
            "wss://public-socket.india.delta.exchange",
            "wss://socket.india.delta.exchange",
            "enabled",
        ),
        (
            "india",
            "testnet",
            "https://cdn-ind.testnet.deltaex.org",
            "wss://socket-ind-pub.testnet.deltaex.org",
            "wss://socket-ind.testnet.deltaex.org",
            "enabled",
        ),
        (
            "global",
            "prod",
            "https://api.delta.exchange",
            "wss://public-socket.india.delta.exchange",
            "wss://socket.delta.exchange",
            "disabled_pending_testnet_contract",
        ),
        (
            "global",
            "testnet",
            "https://testnet-api.delta.exchange",
            "wss://socket-ind-pub.testnet.deltaex.org",
            "wss://testnet-socket.delta.exchange",
            "disabled_pending_testnet_contract",
        ),
    ],
)
def test_exact_documented_endpoint_matrix(
    region: str,
    environment: str,
    rest: str,
    public_ws: str,
    private_ws: str,
    capability: str,
) -> None:
    api = endpoint_api()
    endpoints = api.get_delta_endpoints(region, environment)
    assert endpoints.region == region
    assert endpoints.environment == environment
    assert endpoints.rest_base_url == rest
    assert endpoints.public_ws_url == public_ws
    assert endpoints.private_ws_url == private_ws
    assert endpoints.streaming_capability.value == capability
    assert endpoints.is_development_override is False
    assert endpoints.private_rest_allowed is False


@pytest.mark.parametrize(
    ("region", "environment"),
    [("INDIA", "testnet"), ("india", "demo"), ("us", "prod"), ("", "")],
)
def test_unknown_or_misspelled_selection_fails_closed(region: str, environment: str) -> None:
    with pytest.raises(ConfigurationError) as error:
        endpoint_api().get_delta_endpoints(region, environment)
    assert error.value.code == "configuration_error"


def test_endpoint_metadata_is_frozen() -> None:
    endpoints = endpoint_api().get_delta_endpoints("india", "testnet")
    with pytest.raises(FrozenInstanceError):
        endpoints.rest_base_url = "https://attacker.example"


@pytest.mark.parametrize(
    "path",
    [
        "v2/products",
        "",
        "/",
        "//attacker.example/v2/products",
        "/v2/../orders",
        "/v2/products?api-key=secret",
        "/v2/products#secret",
        "/v2\\products",
        "/%2f%2fattacker.example/v2/products",
        "https://attacker.example/v2/products",
    ],
)
def test_rest_path_join_rejects_ambiguous_or_unsafe_paths(path: str) -> None:
    endpoints = endpoint_api().get_delta_endpoints("india", "testnet")
    with pytest.raises(ConfigurationError):
        endpoints.rest_url(path)


def test_rest_path_join_preserves_canonical_host_and_absolute_path() -> None:
    endpoints = endpoint_api().get_delta_endpoints("global", "testnet")
    assert (
        endpoints.rest_url("/v2/history/candles")
        == "https://testnet-api.delta.exchange/v2/history/candles"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/v2/prod\x00ucts",
        "/v2/prod\ructs",
        "/v2/prod\nucts",
        "/v2/prod\tucts",
        "/v2/prod ucts",
        "/v2/products%00",
        "/v2/products%0d",
        "/v2%2fproducts",
        "/v2%5cproducts",
        "/v2/products%3fsecret",
        "/v2/products%23secret",
        "/v2/products%25ff",
        "/v2/products%ZZ",
    ],
)
def test_rest_path_rejects_raw_or_encoded_controls_and_delimiters(path: str) -> None:
    endpoints = endpoint_api().get_delta_endpoints("india", "testnet")
    with pytest.raises(ConfigurationError):
        endpoints.rest_url(path)


@pytest.mark.parametrize(
    "override",
    [
        "not-a-url",
        "ftp://localhost:9000",
        "http:///missing-host",
        "http://user:fixture-secret@localhost:9000",
        "http://localhost:9000/base",
        "http://localhost:9000/?token=fixture-secret",
        "http://localhost:9000/#fixture-secret",
    ],
)
def test_development_override_rejects_malformed_or_credential_bearing_urls(
    override: str,
) -> None:
    with pytest.raises(ConfigurationError) as error:
        endpoint_api().get_delta_endpoints(
            "india",
            "testnet",
            development_rest_base_url=override,
            allow_development_override=True,
        )
    rendered = str(error.value) + repr(error.value) + str(error.value.to_dict())
    assert "fixture-secret" not in rendered


@pytest.mark.parametrize(
    "override",
    [
        "http://local\x00host:9000",
        "http://local\rhost:9000",
        "http://local\nhost:9000",
        "http://local\thost:9000",
        "http://local%00host:9000",
        "http://local%2fhost:9000",
        "http://exa_mple.example:9000",
        "http://-bad.example:9000",
        "http://bad-.example:9000",
        "http://a..b:9000",
        "http://xn--.example:9000",
    ],
)
def test_development_override_rejects_control_encoded_or_invalid_idna_hosts(
    override: str,
) -> None:
    with pytest.raises(ConfigurationError):
        endpoint_api().get_delta_endpoints(
            "india",
            "testnet",
            development_rest_base_url=override,
            allow_development_override=True,
        )


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("http://localhost:9000", "http://localhost:9000"),
        ("http://127.0.0.1:9000", "http://127.0.0.1:9000"),
        ("http://[::1]:9000", "http://[::1]:9000"),
        ("https://例え.テスト:9000", "https://xn--r8jz45g.xn--zckzah:9000"),
    ],
)
def test_development_override_accepts_valid_local_ip_and_international_hosts(
    override: str, expected: str
) -> None:
    endpoints = endpoint_api().get_delta_endpoints(
        "india",
        "testnet",
        development_rest_base_url=override,
        allow_development_override=True,
    )
    assert endpoints.rest_base_url == expected


def test_development_override_requires_explicit_flag() -> None:
    with pytest.raises(ConfigurationError):
        endpoint_api().get_delta_endpoints(
            "india", "testnet", development_rest_base_url="http://127.0.0.1:9010"
        )


@pytest.mark.parametrize("flag", [1, "true", object()])
def test_development_override_flag_must_be_an_actual_boolean(flag: object) -> None:
    with pytest.raises(ConfigurationError):
        endpoint_api().get_delta_endpoints(
            "india",
            "testnet",
            development_rest_base_url="http://127.0.0.1:9010",
            allow_development_override=flag,
        )


def test_development_override_is_impossible_for_live_or_production_selection() -> None:
    api = endpoint_api()
    with pytest.raises(ConfigurationError):
        api.get_delta_endpoints(
            "india",
            "testnet",
            development_rest_base_url="http://127.0.0.1:9010",
            allow_development_override=True,
            live_requested=True,
        )
    with pytest.raises(ConfigurationError):
        api.get_delta_endpoints(
            "india",
            "prod",
            development_rest_base_url="http://127.0.0.1:9010",
            allow_development_override=True,
        )


def test_explicit_testnet_development_override_is_public_only() -> None:
    endpoints = endpoint_api().get_delta_endpoints(
        "india",
        "testnet",
        development_rest_base_url="http://127.0.0.1:9010/",
        allow_development_override=True,
    )
    assert endpoints.rest_base_url == "http://127.0.0.1:9010"
    assert endpoints.is_development_override is True
    assert endpoints.private_rest_allowed is False
    assert endpoints.streaming_capability.value == "disabled_development_override"
    assert endpoints.public_ws_url is None
    assert endpoints.private_ws_url is None


def config(**kwargs: Any) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_settings_delegate_exact_rest_endpoint_to_registry() -> None:
    api = endpoint_api()
    selected = config(DELTA_REGION="global", DELTA_ENV="prod")
    assert selected.rest_base_url == api.get_delta_endpoints("global", "prod").rest_base_url


def test_settings_constructor_accepts_only_explicit_testnet_override() -> None:
    selected = config(
        DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE=True,
        DELTA_DEVELOPMENT_REST_BASE_URL="http://127.0.0.1:9010/",
    )
    assert selected.rest_base_url == "http://127.0.0.1:9010"
    assert selected.order_write_capable is False
    assert selected.safe_diagnostics()["development_rest_host"] == "127.0.0.1"


def test_settings_environment_accepts_explicit_testnet_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE", "true")
    monkeypatch.setenv("DELTA_DEVELOPMENT_REST_BASE_URL", "http://localhost:9010")
    selected = config()
    assert selected.rest_base_url == "http://localhost:9010"
    assert selected.safe_diagnostics()["development_rest_host"] == "localhost"


def test_settings_dotenv_accepts_explicit_testnet_override(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE=true\n"
        "DELTA_DEVELOPMENT_REST_BASE_URL=http://127.0.0.1:9010\n"
    )
    selected = Settings(_env_file=dotenv)
    assert selected.rest_base_url == "http://127.0.0.1:9010"
    assert selected.order_write_capable is False


@pytest.mark.parametrize(
    "values",
    [
        {"DELTA_DEVELOPMENT_REST_BASE_URL": "http://127.0.0.1:9010"},
        {"DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE": True},
        {
            "DELTA_ENV": "prod",
            "DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE": True,
            "DELTA_DEVELOPMENT_REST_BASE_URL": "http://127.0.0.1:9010",
        },
        {
            "EXECUTION_MODE": "LIVE",
            "DELTA_LIVE_TRADING_ENABLED": True,
            "DELTA_LIVE_CONFIRMATION": "I_UNDERSTAND_THIS_CAN_PLACE_REAL_ORDERS",
            "DELTA_INDIA_TESTNET_API_KEY": "fixture-secret-key",
            "DELTA_INDIA_TESTNET_API_SECRET": "fixture-secret-value",
            "DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE": True,
            "DELTA_DEVELOPMENT_REST_BASE_URL": "http://127.0.0.1:9010",
        },
    ],
)
def test_settings_reject_inconsistent_or_live_override(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as error:
        config(**values)
    rendered = str(error.value) + str(error.value.errors()) + error.value.json()
    assert "fixture-secret" not in rendered


@pytest.mark.parametrize("source", ["environment", "dotenv"])
def test_invalid_override_source_never_leaks_structured_inputs(
    source: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = {
        "DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE": "true",
        "DELTA_DEVELOPMENT_REST_BASE_URL": ("http://user:fixture-secret-override@localhost:9010"),
    }
    with pytest.raises(ValidationError) as error:
        if source == "environment":
            for name, value in values.items():
                monkeypatch.setenv(name, value)
            config()
        else:
            dotenv = tmp_path / ".env"
            dotenv.write_text("".join(f"{name}={value}\n" for name, value in values.items()))
            Settings(_env_file=dotenv)
    rendered = str(error.value) + str(error.value.errors()) + error.value.json()
    assert "fixture-secret-override" not in rendered
