"""Candle results form a bounded, unique, normalized interval series."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from delta_public_test_support import json_response, new_client, public_api

from hmt_crypto.errors import ExchangeProtocolError

pytestmark = pytest.mark.contract

_START = 1_700_000_000


def _row(timestamp: int) -> dict[str, object]:
    return {
        "time": timestamp,
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "volume": "1",
    }


def _request(*, resolution: str = "1m", end: int = _START + 180) -> object:
    api = public_api().base
    return api.CandleRequest(
        product=api.ProductRef(symbol="XBTUSD_TEST"),
        resolution=resolution,
        start=datetime.fromtimestamp(_START, UTC),
        end=datetime.fromtimestamp(end, UTC),
    )


def _client_for_rows(rows: list[dict[str, object]]) -> object:
    body = json.dumps({"success": True, "result": rows}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(request, body)

    return new_client(handler)


@pytest.mark.asyncio
async def test_duplicate_candle_timestamps_are_rejected() -> None:
    client = _client_for_rows([_row(_START), _row(_START)])
    try:
        with pytest.raises(ExchangeProtocolError):
            await client.get_candles(_request())
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_shuffled_unique_candles_are_normalized_ascending_without_requiring_gaps() -> None:
    client = _client_for_rows([_row(_START + 180), _row(_START), _row(_START + 120)])
    try:
        result = await client.get_candles(_request(end=_START + 180))
    finally:
        await client.aclose()
    assert [candle.timestamp for candle in result.items] == [
        datetime.fromtimestamp(_START, UTC),
        datetime.fromtimestamp(_START + 120, UTC),
        datetime.fromtimestamp(_START + 180, UTC),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [_START - 1, _START + 181])
async def test_candle_timestamp_outside_inclusive_request_interval_is_rejected(
    timestamp: int,
) -> None:
    client = _client_for_rows([_row(timestamp)])
    try:
        with pytest.raises(ExchangeProtocolError):
            await client.get_candles(_request())
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolution", "seconds"),
    [
        ("1m", 60),
        ("3m", 180),
        ("5m", 300),
        ("15m", 900),
        ("30m", 1_800),
        ("1h", 3_600),
        ("2h", 7_200),
        ("4h", 14_400),
        ("6h", 21_600),
        ("1d", 86_400),
        ("1w", 604_800),
    ],
)
async def test_each_resolution_rejects_count_impossible_for_inclusive_interval(
    resolution: str,
    seconds: int,
) -> None:
    client = _client_for_rows([_row(_START), _row(_START + 1), _row(_START + seconds)])
    try:
        with pytest.raises(ExchangeProtocolError):
            await client.get_candles(_request(resolution=resolution, end=_START + seconds))
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolution", "seconds"),
    [
        ("1m", 60),
        ("3m", 180),
        ("5m", 300),
        ("15m", 900),
        ("30m", 1800),
        ("1h", 3600),
        ("2h", 7200),
        ("4h", 14400),
        ("6h", 21600),
        ("1d", 86400),
        ("1w", 604800),
    ],
)
@pytest.mark.parametrize("spacing", ["one_second", "just_short", "exact", "gap"])
async def test_candle_spacing_respects_resolution_without_grid_guessing(
    resolution: str,
    seconds: int,
    spacing: str,
) -> None:
    distance = {
        "one_second": 1,
        "just_short": seconds - 1,
        "exact": seconds,
        "gap": 2 * seconds + 1,
    }[spacing]
    rows = [_row(_START + distance), _row(_START)]

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            request,
            json.dumps({"success": True, "result": rows}).encode(),
            headers={"X-Request-ID": "spacing-request"},
        )

    async with new_client(handler) as client:
        request = _request(resolution=resolution, end=_START + max(distance, seconds))
        if spacing in {"one_second", "just_short"}:
            with pytest.raises(ExchangeProtocolError) as raised:
                await client.get_candles(request)
            assert raised.value.context["operation"] == "candles"
            assert raised.value.context["request_id"] == "spacing-request"
            assert raised.value.context["http_status"] == 200
            assert raised.value.context["correlation_id"]
        else:
            result = await client.get_candles(request)
            assert [int(row.timestamp.timestamp()) for row in result.items] == [
                _START,
                _START + distance,
            ]
