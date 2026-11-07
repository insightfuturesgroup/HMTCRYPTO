"""One literal respx route proves compatibility with the plan's named tool."""

from __future__ import annotations

import httpx
import pytest
import respx
from delta_public_test_support import fixture_bytes

from hmt_crypto.exchange.base import ProductRef
from hmt_crypto.exchange.delta.client import DeltaPublicClient
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints
from hmt_crypto.exchange.delta.rate_limiter import (
    RateLimitScope,
    ScopeKind,
    WeightedRateLimiter,
)

pytestmark = pytest.mark.contract


@pytest.mark.asyncio
@respx.mock
async def test_respx_routes_one_public_ticker_without_credentials() -> None:
    route = respx.get("https://cdn-ind.testnet.deltaex.org/v2/tickers/XBTUSD_TEST").mock(
        return_value=httpx.Response(
            200,
            content=fixture_bytes("public_ticker.json"),
            headers={"Content-Type": "application/json"},
        )
    )
    limiter = WeightedRateLimiter(
        RateLimitScope(
            region="india",
            environment="testnet",
            kind=ScopeKind.IP,
            external_id="respx-contract-egress",
        )
    )
    async with DeltaPublicClient(
        endpoints=get_delta_endpoints("india", "testnet"),
        limiter=limiter,
        user_agent="hmt-crypto-respx-test/1.0",
    ) as client:
        ticker = await client.get_ticker(ProductRef(symbol="XBTUSD_TEST"))

    assert route.called
    assert ticker.product_id == 101
    assert ticker.symbol == "XBTUSD_TEST"
