"""Outbound compression negotiation matches the strict response decoder allowlist."""

from __future__ import annotations

import gzip
from collections.abc import Awaitable, Callable
from decimal import Decimal

import httpx
import pytest
from delta_public_test_support import fixture_bytes, public_api

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.endpoints import DeltaEndpointSet, get_delta_endpoints
from hmt_crypto.exchange.delta.rate_limiter import (
    RateLimitScope,
    ScopeKind,
    WeightedRateLimiter,
)

pytestmark = pytest.mark.contract


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


def _client(endpoints: DeltaEndpointSet, handler: Handler) -> object:
    api = public_api().client
    limiter = WeightedRateLimiter(
        RateLimitScope(
            region=endpoints.region,
            environment=endpoints.environment,
            kind=ScopeKind.IP,
            external_id="encoding-contract-egress",
        )
    )
    return api.DeltaPublicClient(
        endpoints=endpoints,
        limiter=limiter,
        user_agent="hmt-crypto-encoding-tests/1.0",
        transport=httpx.MockTransport(handler),
        retry_policy=api.RetryPolicy(
            max_attempts=1,
            base_delay_seconds=Decimal("0.001"),
            max_delay_seconds=Decimal("0.001"),
            jitter_ratio=Decimal("0"),
        ),
        jitter_source=lambda: Decimal("0"),
    )


async def _ticker_call(client: object) -> object:
    return await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("region", "environment", "override"),
    [
        ("india", "prod", None),
        ("india", "testnet", None),
        ("global", "prod", None),
        ("global", "testnet", None),
        ("india", "testnet", "http://127.0.0.1:9010"),
    ],
)
async def test_every_endpoint_set_advertises_only_supported_gzip_encoding(
    region: str,
    environment: str,
    override: str | None,
) -> None:
    endpoints = get_delta_endpoints(
        region,
        environment,
        development_rest_base_url=override,
        allow_development_override=override is not None,
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=fixture_bytes("public_ticker.json"),
            headers={"Content-Type": "application/json"},
            request=request,
        )

    client = _client(endpoints, handler)
    try:
        ticker = await _ticker_call(client)
    finally:
        await client.aclose()
    assert ticker.product_id == 101
    assert seen[0].headers.get_list("Accept-Encoding") == ["gzip"]


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "identity"])
async def test_advertised_gzip_and_identity_responses_are_accepted(encoding: str) -> None:
    body = fixture_bytes("public_ticker.json")
    encoded = gzip.compress(body) if encoding == "gzip" else body
    stream = _Chunks(encoded[:17], encoded[17:])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(encoded)),
        }
        if encoding == "gzip":
            headers["Content-Encoding"] = "gzip"
        return httpx.Response(200, headers=headers, stream=stream, request=request)

    client = _client(get_delta_endpoints("india", "testnet"), handler)
    try:
        ticker = await _ticker_call(client)
    finally:
        await client.aclose()
    assert ticker.product_id == 101
    assert seen[0].headers.get_list("Accept-Encoding") == ["gzip"]
    assert stream.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["deflate", "br"])
async def test_unadvertised_or_unknown_content_encoding_is_rejected(encoding: str) -> None:
    stream = _Chunks(fixture_bytes("public_ticker.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Encoding": encoding},
            stream=stream,
            request=request,
        )

    client = _client(get_delta_endpoints("india", "testnet"), handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await _ticker_call(client)
    finally:
        await client.aclose()
    assert stream.closed is True
