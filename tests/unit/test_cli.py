"""The Phase 1 command line exposes public read diagnostics only."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from importlib import import_module
from types import SimpleNamespace
from typing import Any

import pytest
from product_catalog_test_support import complete_result, load_products, truncated_result
from typer.testing import CliRunner

from hmt_crypto.domain import Candle, Ticker
from hmt_crypto.exchange.base import CandleRequest, CandleResult, ProductRef
from hmt_crypto.logging import get_logger

pytestmark = pytest.mark.unit


def _api() -> SimpleNamespace:
    try:
        cli = import_module("hmt_crypto.cli")
        runtime = import_module("hmt_crypto.cli_runtime")
    except ImportError:
        pytest.fail("Phase 1 CLI API is missing", pytrace=False)
    return SimpleNamespace(cli=cli, runtime=runtime)


@pytest.fixture(autouse=True)
def clean_cli_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("DELTA_") or name in {"EXECUTION_MODE", "PRODUCTION_WRITES_FORBIDDEN"}:
            monkeypatch.delenv(name)


def _ticker() -> Ticker:
    return Ticker(
        product_id=1101,
        symbol="XBTUSD_CAT",
        timestamp=datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
        mark_price="70000.5",
        spot_price="69999.5",
        close="69800",
        best_bid="70000",
        best_ask="70001",
    )


def _candles() -> CandleResult:
    return CandleResult(
        items=(
            Candle(
                timestamp=datetime(2026, 11, 3, 9, 59, tzinfo=UTC),
                open="69900",
                high="70100",
                low="69800",
                close="70000",
                volume="12.5",
            ),
        ),
        possibly_truncated=False,
    )


class FakePublicClient:
    def __init__(self, *, incomplete: bool = False, failure: BaseException | None = None) -> None:
        products = load_products()
        self.product_result = (
            truncated_result(products) if incomplete else complete_result(products, page_count=2)
        )
        self.failure = failure
        self.entered = 0
        self.exited = 0
        self.product_calls = 0
        self.ticker_refs: list[ProductRef] = []
        self.candle_requests: list[CandleRequest] = []

    async def __aenter__(self) -> FakePublicClient:
        self.entered += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.exited += 1

    async def get_products(self) -> Any:
        self.product_calls += 1
        if self.failure is not None:
            raise self.failure
        return self.product_result

    async def get_ticker(self, product: ProductRef) -> Ticker:
        self.ticker_refs.append(product)
        if self.failure is not None:
            raise self.failure
        return _ticker()

    async def get_candles(self, request: CandleRequest) -> CandleResult:
        self.candle_requests.append(request)
        if self.failure is not None:
            raise self.failure
        return _candles()


def _invoke(*arguments: str) -> Any:
    return CliRunner().invoke(_api().cli.app, list(arguments))


def _payload(result: Any) -> dict[str, Any]:
    stream = result.stdout or result.stderr
    assert stream.count("\n") == 1
    value = json.loads(stream)
    assert isinstance(value, dict)
    return value


def test_help_exposes_public_reads_and_explicit_account_bootstrap_only() -> None:
    result = _invoke("--help")
    assert result.exit_code == 0
    for allowed in (
        "account",
        "database",
        "config-check",
        "clock-check",
        "products",
        "ticker",
        "candles",
    ):
        assert allowed in result.stdout
    for forbidden in (
        "order",
        "balance",
        "position",
        "leverage",
        "credential",
        "withdraw",
        "transfer",
        "request",
        "install-completion",
        "show-completion",
    ):
        assert forbidden not in result.stdout.casefold()

    products = _invoke("products", "--help")
    assert products.exit_code == 0
    assert "sync" in products.stdout and "show" in products.stdout
    account = _invoke("account", "--help")
    assert account.exit_code == 0
    assert "bootstrap" in account.stdout
    database = _invoke("database", "--help")
    assert database.exit_code == 0
    assert "init-dev" in database.stdout


def test_live_bootstrap_requires_and_forwards_explicit_locked_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_payload(
        settings: object, *, profile_name: str, confirm_locked_live: bool = False
    ) -> dict[str, object]:
        captured.update(
            settings=settings,
            profile_name=profile_name,
            confirm_locked_live=confirm_locked_live,
        )
        return {
            "account_id": "00000000-0000-0000-0000-000000000001",
            "entries_enabled": False,
            "exits_enabled": False,
        }

    marker = object()
    monkeypatch.setattr(_api().runtime, "load_settings", lambda: marker)
    monkeypatch.setattr(_api().runtime, "account_bootstrap_payload", fake_payload)
    result = _invoke(
        "account",
        "bootstrap",
        "--profile",
        "delta-india-primary",
        "--confirm-locked-live",
    )

    assert result.exit_code == 0
    assert captured == {
        "settings": marker,
        "profile_name": "delta-india-primary",
        "confirm_locked_live": True,
    }
    assert _payload(result)["entries_enabled"] is False


def test_config_check_is_local_safe_and_defaults_to_paper_india_testnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "CLI-SECRET-MUST-NOT-LEAK"
    monkeypatch.setenv("DELTA_INDIA_TESTNET_API_KEY", "CLI-KEY-MUST-NOT-LEAK")
    monkeypatch.setenv("DELTA_INDIA_TESTNET_API_SECRET", secret)
    monkeypatch.setattr(
        _api().runtime,
        "_build_public_client",
        lambda settings: pytest.fail("config-check must not create a client"),
    )

    result = _invoke("config-check")
    payload = _payload(result)
    assert result.exit_code == 0
    assert payload == {
        "api_key_fingerprint": "22f93d72f986",
        "credential_slot": "DELTA_INDIA_TESTNET",
        "environment": "testnet",
        "execution_mode": "PAPER",
        "order_write_capable": False,
        "region": "india",
        "rest_base_url": "https://cdn-ind.testnet.deltaex.org",
    }
    assert secret not in result.output
    assert "CLI-KEY-MUST-NOT-LEAK" not in result.output


def test_clock_check_claims_no_os_sync_probe_time_endpoint_or_write_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _api().runtime,
        "_build_public_client",
        lambda settings: pytest.fail("clock-check must not create a client"),
    )
    result = _invoke("clock-check")
    payload = _payload(result)

    assert result.exit_code == 0
    assert payload["local_clock_synchronized"] is None
    assert payload["signed_probe_outcome"] == "not_attempted"
    assert payload["time_endpoint_documented"] is False
    assert payload["clock_ready"] is False
    assert payload["signed_probe_ready"] is False
    assert payload["order_write_prerequisites_met"] is False


def test_products_sync_binds_complete_load_to_selected_source_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DELTA_REGION", "global")
    monkeypatch.setenv("DELTA_ENV", "prod")
    fake = FakePublicClient()
    selected: list[tuple[str, str]] = []

    def factory(settings: Any) -> FakePublicClient:
        selected.append((settings.delta_region.value, settings.delta_env.value))
        return fake

    monkeypatch.setattr(_api().runtime, "_build_public_client", factory)
    result = _invoke("products", "sync")
    payload = _payload(result)

    assert result.exit_code == 0
    assert selected == [("global", "prod")]
    assert payload["source"] == {"region": "global", "environment": "prod"}
    assert payload["status"] == "published_changed"
    assert payload["product_count"] == 2
    assert payload["revision"] == 1
    assert len(payload["content_sha256"]) == 64
    assert fake.product_calls == 1
    assert (fake.entered, fake.exited) == (1, 1)


def test_products_sync_failure_is_safe_json_nonzero_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePublicClient(incomplete=True)
    monkeypatch.setattr(_api().runtime, "_build_public_client", lambda settings: fake)

    result = _invoke("products", "sync")
    payload = _payload(result)

    assert result.exit_code != 0
    assert payload["code"] == "catalog_unavailable"
    assert fake.product_calls == 1
    assert (fake.entered, fake.exited) == (1, 1)


@pytest.mark.parametrize("selector", [("--id", "1101"), ("--symbol", "XBTUSD_CAT")])
def test_products_show_refreshes_complete_catalogue_and_uses_exact_selector(
    monkeypatch: pytest.MonkeyPatch, selector: tuple[str, str]
) -> None:
    fake = FakePublicClient()
    monkeypatch.setattr(_api().runtime, "_build_public_client", lambda settings: fake)
    result = _invoke("products", "show", *selector)
    payload = _payload(result)

    assert result.exit_code == 0
    assert payload["id"] == 1101
    assert payload["symbol"] == "XBTUSD_CAT"
    assert payload["contract_value"] == "0.001"
    assert payload["catalogue"]["source"] == {"region": "india", "environment": "testnet"}
    assert fake.product_calls == 1
    assert fake.exited == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ("products", "show"),
        ("products", "show", "--id", "1101", "--symbol", "XBTUSD_CAT"),
        ("products", "show", "--id", "not-an-id"),
    ],
)
def test_invalid_product_selector_is_safe_json_and_never_opens_transport(
    monkeypatch: pytest.MonkeyPatch, arguments: tuple[str, ...]
) -> None:
    monkeypatch.setattr(
        _api().runtime,
        "_build_public_client",
        lambda settings: pytest.fail("invalid selector must fail before client construction"),
    )
    result = _invoke(*arguments)
    payload = _payload(result)

    assert result.exit_code != 0
    assert payload["code"] == "invalid_input"


def test_incomplete_products_never_publish_for_show(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePublicClient(incomplete=True)
    monkeypatch.setattr(_api().runtime, "_build_public_client", lambda settings: fake)
    result = _invoke("products", "show", "--symbol", "XBTUSD_CAT")
    payload = _payload(result)

    assert result.exit_code != 0
    assert payload["code"] == "catalog_unavailable"
    assert fake.exited == 1


def test_ticker_builds_typed_reference_and_closes_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePublicClient()
    monkeypatch.setattr(_api().runtime, "_build_public_client", lambda settings: fake)
    result = _invoke("ticker", "XBTUSD_CAT")
    payload = _payload(result)

    assert result.exit_code == 0
    assert fake.ticker_refs == [ProductRef(symbol="XBTUSD_CAT")]
    assert payload["mark_price"] == "70000.5"
    assert payload["timestamp"] == "2026-11-03T10:00:00Z"
    assert fake.exited == 1


def test_candles_builds_strict_typed_utc_request(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePublicClient()
    monkeypatch.setattr(_api().runtime, "_build_public_client", lambda settings: fake)
    result = _invoke(
        "candles",
        "XBTUSD_CAT",
        "--resolution",
        "1m",
        "--start",
        "2026-11-03T09:00:00Z",
        "--end",
        "2026-11-03T10:00:00Z",
    )
    payload = _payload(result)

    assert result.exit_code == 0
    request = fake.candle_requests[0]
    assert isinstance(request, CandleRequest)
    assert request.product == ProductRef(symbol="XBTUSD_CAT")
    assert request.start == datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    assert request.end == datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    assert payload["possibly_truncated"] is False
    assert payload["items"][0]["volume"] == "12.5"
    assert fake.exited == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ("ticker", "bad symbol"),
        (
            "candles",
            "XBTUSD_CAT",
            "--resolution",
            "1m",
            "--start",
            "2026-11-03T09:00:00",
            "--end",
            "2026-11-03T10:00:00Z",
        ),
        (
            "candles",
            "XBTUSD_CAT",
            "--resolution",
            "7d",
            "--start",
            "2026-11-03T09:00:00Z",
            "--end",
            "2026-11-03T10:00:00Z",
        ),
    ],
)
def test_invalid_market_data_arguments_fail_as_json_before_transport(
    monkeypatch: pytest.MonkeyPatch, arguments: tuple[str, ...]
) -> None:
    monkeypatch.setattr(
        _api().runtime,
        "_build_public_client",
        lambda settings: pytest.fail("invalid arguments must not create a client"),
    )
    result = _invoke(*arguments)
    assert result.exit_code != 0
    assert _payload(result)["code"] == "invalid_input"


def test_unexpected_network_failure_is_redacted_and_client_still_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePublicClient(failure=RuntimeError("RAW-CLI-NETWORK-MARKER"))
    monkeypatch.setattr(_api().runtime, "_build_public_client", lambda settings: fake)
    result = _invoke("ticker", "XBTUSD_CAT")
    payload = _payload(result)

    assert result.exit_code != 0
    assert payload == {"code": "internal_error", "message": "Command failed safely"}
    assert "RAW-CLI-NETWORK-MARKER" not in result.output
    assert fake.exited == 1


def test_network_command_keeps_structured_redacted_logs_off_result_stdout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "CLI-LOG-SECRET-MUST-NOT-LEAK"

    class LoggingClient(FakePublicClient):
        async def get_ticker(self, product: ProductRef) -> Ticker:
            get_logger("cli.test").info("fixture_log", api_key=marker)
            return await super().get_ticker(product)

    fake = LoggingClient()
    monkeypatch.setattr(_api().runtime, "_build_public_client", lambda settings: fake)

    result = _invoke("ticker", "XBTUSD_CAT")

    assert result.exit_code == 0
    assert result.stdout.count("\n") == 1
    assert json.loads(result.stdout)["symbol"] == "XBTUSD_CAT"
    assert marker not in result.stdout + result.stderr
    assert "fixture_log" in result.stderr
