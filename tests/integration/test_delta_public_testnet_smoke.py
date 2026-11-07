"""Explicitly opted-in public reads against both isolated Delta testnets."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import pytest

import hmt_crypto.cli_runtime as cli_runtime_module
from hmt_crypto.cli_runtime import _build_public_client, products_sync_payload
from hmt_crypto.config import Settings
from hmt_crypto.domain import ContractType, ProductState, TradingStatus
from hmt_crypto.errors import (
    CatalogUnavailableError,
    ExchangeDownError,
    ExchangeError,
    ExchangeProtocolError,
    ExchangeTimeoutError,
    HmtCryptoError,
    PaginationLimitError,
    RateLimitError,
)
from hmt_crypto.exchange.base import CandleRequest, CandleResolution, ProductRef
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints

pytestmark = [pytest.mark.integration, pytest.mark.testnet]

_CANONICAL_TESTNET_URLS = {
    "india": "https://cdn-ind.testnet.deltaex.org",
    "global": "https://testnet-api.delta.exchange",
}
_SAFE_UNVERIFIED_ERRORS: dict[type[HmtCryptoError], str] = {
    CatalogUnavailableError: "catalog_unavailable",
    ExchangeDownError: "exchange_down",
    ExchangeError: "exchange_error",
    ExchangeProtocolError: "exchange_protocol_error",
    ExchangeTimeoutError: "exchange_timeout",
    PaginationLimitError: "pagination_limit",
    RateLimitError: "rate_limit",
}
_SAFE_UNVERIFIED_CODES = frozenset(_SAFE_UNVERIFIED_ERRORS.values())


def _enabled() -> bool:
    return os.getenv("RUN_DELTA_PUBLIC_TESTNET_SMOKE") == "1"


def _canonical_testnet_settings(region: str) -> Settings:
    settings = Settings(
        _env_file=None,
        EXECUTION_MODE="PAPER",
        DELTA_REGION=region,
        DELTA_ENV="testnet",
        DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE=False,
        DELTA_DEVELOPMENT_REST_BASE_URL=None,
    )
    endpoints = get_delta_endpoints(settings.delta_region, settings.delta_env)
    assert endpoints.rest_base_url == _CANONICAL_TESTNET_URLS[region]
    assert endpoints.is_development_override is False
    return settings


def _skip_code(code: object) -> NoReturn:
    if type(code) is not str or code not in _SAFE_UNVERIFIED_CODES:
        raise AssertionError("testnet smoke error code is not allowlisted")
    pytest.skip(f"public testnet not verified: {code}")


def _skip_unverified(error: HmtCryptoError) -> NoReturn:
    code = _SAFE_UNVERIFIED_ERRORS.get(type(error))
    if code is None:
        raise error
    _skip_code(code)


@pytest.mark.skipif(not _enabled(), reason="set RUN_DELTA_PUBLIC_TESTNET_SMOKE=1 explicitly")
@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["india", "global"])
async def test_complete_public_product_catalogue_on_testnet(region: str) -> None:
    settings = _canonical_testnet_settings(region)
    try:
        result = await products_sync_payload(settings)
    except HmtCryptoError as error:
        _skip_unverified(error)
    if result["status"] not in {"published_changed", "published_unchanged"}:
        _skip_code(result["safe_error_code"])

    assert isinstance(result["product_count"], int)
    assert result["product_count"] > 0
    assert result["persistence"] == "process_local_only"


@pytest.mark.skipif(not _enabled(), reason="set RUN_DELTA_PUBLIC_TESTNET_SMOKE=1 explicitly")
@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["india", "global"])
async def test_public_ticker_and_candles_on_testnet(region: str) -> None:
    settings = _canonical_testnet_settings(region)
    try:
        async with _build_public_client(settings) as client:
            products = await client.get_products()
            assert products.complete is True
            assert products.items
            eligible = next(
                (
                    product
                    for product in products.items
                    if product.contract_type
                    in {ContractType.PERPETUAL_FUTURES, ContractType.FUTURES}
                    and product.state is ProductState.LIVE
                    and product.trading_status is TradingStatus.OPERATIONAL
                ),
                None,
            )
            if eligible is None:
                pytest.skip("public testnet not verified: no operational linear product")
            selected = ProductRef(symbol=eligible.symbol)
            ticker = await client.get_ticker(selected)
            end = datetime.now(UTC).replace(microsecond=0)
            candles = await client.get_candles(
                CandleRequest(
                    product=selected,
                    resolution=CandleResolution.ONE_HOUR,
                    start=end - timedelta(hours=24),
                    end=end,
                )
            )
    except HmtCryptoError as error:
        _skip_unverified(error)

    assert ticker.symbol == selected.symbol
    assert len(candles) <= 2_000


@pytest.mark.asyncio
@pytest.mark.parametrize("smoke", ["catalogue", "market_data"])
@pytest.mark.parametrize(
    ("region", "ambient_override", "expected"),
    [
        ("india", "https://api.delta.exchange", "https://cdn-ind.testnet.deltaex.org"),
        ("global", "https://api.india.delta.exchange", "https://testnet-api.delta.exchange"),
    ],
)
async def test_smoke_neutralizes_ambient_override_before_any_io(
    monkeypatch: pytest.MonkeyPatch,
    smoke: str,
    region: str,
    ambient_override: str,
    expected: str,
) -> None:
    monkeypatch.setenv("DELTA_ALLOW_DEVELOPMENT_ENDPOINT_OVERRIDE", "true")
    monkeypatch.setenv("DELTA_DEVELOPMENT_REST_BASE_URL", ambient_override)
    module = sys.modules[__name__]

    def assert_canonical(settings: Settings) -> None:
        endpoints = get_delta_endpoints(
            settings.delta_region,
            settings.delta_env,
            development_rest_base_url=settings.delta_development_rest_base_url,
            allow_development_override=settings.delta_allow_development_endpoint_override,
            live_requested=False,
        )
        assert endpoints.rest_base_url == expected
        assert endpoints.is_development_override is False

    if smoke == "catalogue":

        async def products(settings: Settings) -> dict[str, object]:
            assert_canonical(settings)
            return {
                "status": "published_changed",
                "product_count": 1,
                "persistence": "process_local_only",
            }

        monkeypatch.setattr(module, "products_sync_payload", products)
        await test_complete_public_product_catalogue_on_testnet(region)
    else:

        def client(settings: Settings) -> NoReturn:
            assert_canonical(settings)
            raise ExchangeDownError(context={"operation": "testnet_smoke"})

        monkeypatch.setattr(module, "_build_public_client", client)
        with pytest.raises(pytest.skip.Exception):
            await test_public_ticker_and_candles_on_testnet(region)


@pytest.mark.asyncio
async def test_product_smoke_treats_safe_exchange_failure_as_unverified_without_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "RAW-SMOKE-FAILURE-MARKER"

    class FailingProductClient:
        async def __aenter__(self) -> FailingProductClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get_products(self) -> NoReturn:
            raise ExchangeDownError(
                context={"operation": "products", "path": f"/v2/products?token={marker}"}
            )

    monkeypatch.setattr(
        cli_runtime_module,
        "_build_public_client",
        lambda settings: FailingProductClient(),
    )

    with pytest.raises(pytest.skip.Exception) as skipped:
        await test_complete_public_product_catalogue_on_testnet("india")

    assert str(skipped.value) == "public testnet not verified: catalog_unavailable"
    assert marker not in str(skipped.value)


@pytest.mark.asyncio
async def test_product_smoke_rejects_unallowlisted_result_code_without_rendering_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "RAW-UNALLOWLISTED-SMOKE-CODE"

    async def unavailable(settings: Settings) -> dict[str, object]:
        return {
            "status": "unavailable",
            "safe_error_code": marker,
            "product_count": 0,
            "persistence": "process_local_only",
        }

    monkeypatch.setattr(sys.modules[__name__], "products_sync_payload", unavailable)

    with pytest.raises((AssertionError, pytest.skip.Exception)) as raised:
        await test_complete_public_product_catalogue_on_testnet("india")

    assert type(raised.value) is AssertionError
    assert marker not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [AssertionError, RuntimeError])
async def test_product_smoke_does_not_swallow_programming_failures(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    async def fail(settings: Settings) -> NoReturn:
        raise error_type("programming-failure-marker")

    monkeypatch.setattr(sys.modules[__name__], "products_sync_payload", fail)

    with pytest.raises(error_type, match="programming-failure-marker"):
        await test_complete_public_product_catalogue_on_testnet("india")
