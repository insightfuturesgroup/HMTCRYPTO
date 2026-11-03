"""Explicitly opted-in public reads against both isolated Delta testnets."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from hmt_crypto.cli_runtime import _build_public_client, products_sync_payload
from hmt_crypto.config import Settings
from hmt_crypto.domain import ContractType, ProductState, TradingStatus
from hmt_crypto.errors import HmtCryptoError
from hmt_crypto.exchange.base import CandleRequest, CandleResolution, ProductRef

pytestmark = [pytest.mark.integration, pytest.mark.testnet]


def _enabled() -> bool:
    return os.getenv("RUN_DELTA_PUBLIC_TESTNET_SMOKE") == "1"


@pytest.mark.skipif(not _enabled(), reason="set RUN_DELTA_PUBLIC_TESTNET_SMOKE=1 explicitly")
@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["india", "global"])
async def test_complete_public_product_catalogue_on_testnet(region: str) -> None:
    settings = Settings(
        _env_file=None,
        EXECUTION_MODE="PAPER",
        DELTA_REGION=region,
        DELTA_ENV="testnet",
    )
    result = await products_sync_payload(settings)
    if result["status"] not in {"published_changed", "published_unchanged"}:
        pytest.skip(f"public testnet not verified: {result['safe_error_code']}")

    assert isinstance(result["product_count"], int)
    assert result["product_count"] > 0
    assert result["persistence"] == "process_local_only"


@pytest.mark.skipif(not _enabled(), reason="set RUN_DELTA_PUBLIC_TESTNET_SMOKE=1 explicitly")
@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["india", "global"])
async def test_public_ticker_and_candles_on_testnet(region: str) -> None:
    settings = Settings(
        _env_file=None,
        EXECUTION_MODE="PAPER",
        DELTA_REGION=region,
        DELTA_ENV="testnet",
    )
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
        pytest.skip(f"public testnet not verified: {error.code}")

    assert ticker.symbol == selected.symbol
    assert len(candles) <= 2_000
