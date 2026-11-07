"""Delta historical candle request, schema, and completeness contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from delta_public_test_support import (
    fixture_bytes,
    fixture_json,
    json_response,
    new_client,
    public_api,
)
from pydantic import ValidationError

from hmt_crypto.errors import ExchangeProtocolError

pytestmark = pytest.mark.contract


def _candle_rows(*, count: int, start: int, step: int = 60) -> list[dict[str, object]]:
    return [
        {
            "time": start + index * step,
            "open": "100",
            "high": "102",
            "low": "99",
            "close": "101",
            "volume": "1",
        }
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_india_candles_use_documented_resolutions_and_epoch_seconds() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(request, fixture_bytes("public_candles.json"))

    api = public_api().base
    request = api.CandleRequest(
        product=api.ProductRef(symbol="XBTUSD_TEST"),
        resolution=api.CandleResolution.ONE_MINUTE,
        start=datetime.fromtimestamp(1_700_000_000, UTC),
        end=datetime.fromtimestamp(1_700_000_120, UTC),
    )
    client = new_client(handler)
    try:
        candles = await client.get_candles(request)
    finally:
        await client.aclose()
    assert len(candles) == 2
    assert candles.possibly_truncated is False
    assert candles[1].close == Decimal("103.5")
    assert candles[1].timestamp == datetime.fromtimestamp(1_700_000_060, UTC)
    assert list(seen[0].url.params.multi_items()) == [
        ("resolution", "1m"),
        ("symbol", "XBTUSD_TEST"),
        ("start", "1700000000"),
        ("end", "1700000120"),
    ]


def test_deprecated_long_candle_resolutions_fail_closed_for_every_region() -> None:
    api = public_api().base
    with pytest.raises(ValidationError):
        api.CandleRequest(
            product=api.ProductRef(symbol="XBTUSD_TEST"),
            resolution="7d",
            start=datetime.fromtimestamp(1_700_000_000, UTC),
            end=datetime.fromtimestamp(1_701_000_000, UTC),
        )


@pytest.mark.asyncio
async def test_more_than_two_thousand_candles_fails_closed() -> None:
    payload = fixture_json("public_candles.json")
    payload["result"] = _candle_rows(count=2001, start=1_700_000_000)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    api = public_api().base
    request = api.CandleRequest(
        product=api.ProductRef(symbol="XBTUSD_TEST"),
        resolution=api.CandleResolution.ONE_MINUTE,
        start=datetime.fromtimestamp(1_700_000_000, UTC),
        end=datetime.fromtimestamp(1_700_120_000, UTC),
    )
    client = new_client(handler)
    try:
        with pytest.raises(ExchangeProtocolError):
            await client.get_candles(request)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_exactly_two_thousand_candles_exposes_possible_incompleteness() -> None:
    payload = fixture_json("public_candles.json")
    payload["result"] = _candle_rows(count=2000, start=1_700_000_000)

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, json.dumps(payload).encode())

    api = public_api().base
    request = api.CandleRequest(
        product=api.ProductRef(symbol="XBTUSD_TEST"),
        resolution=api.CandleResolution.ONE_MINUTE,
        start=datetime.fromtimestamp(1_700_000_000, UTC),
        end=datetime.fromtimestamp(1_700_119_940, UTC),
    )
    client = new_client(handler)
    try:
        result = await client.get_candles(request)
    finally:
        await client.aclose()
    assert len(result) == 2000
    assert result.possibly_truncated is True
