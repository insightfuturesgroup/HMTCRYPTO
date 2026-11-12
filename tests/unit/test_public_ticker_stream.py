"""Production public ticker stream reconnect and subscription boundary tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot, CatalogSource
from hmt_crypto.exchange.delta.wire_schemas import parse_product
from hmt_crypto.market_data.contracts import TickerObservation
from hmt_crypto.worker.public_ticker_stream import _connection

FIXTURES = Path(__file__).parents[1] / "fixtures" / "delta"


def _catalogue() -> CatalogSnapshot:
    rows = json.loads((FIXTURES / "products_catalog_valid.json").read_text(encoding="utf-8"))
    products = tuple(parse_product(row, endpoint_id="public_stream_test") for row in rows)
    now = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
    return CatalogSnapshot(
        source=CatalogSource(region=DeltaRegion.INDIA, environment=DeltaEnvironment.PROD),
        revision=1,
        fetched_at=now,
        validated_at=now,
        ttl=timedelta(minutes=30),
        products=products,
    )


class _Socket:
    def __init__(self, stop: asyncio.Event) -> None:
        self.stop = stop
        self.sent: list[str] = []
        self.frames = [
            '{"type":"subscriptions","channels":['
            '{"name":"ticker","symbols":["XBTUSD_CAT"]}]}',
            '{"type":"ticker","ts":1793872800000000,"sy":"XBTUSD_CAT",'
            '"sp":"100.10","d":[{"i":1101,"s":"XBTUSD_CAT","m":"100.00",'
            '"m24hc":"1.5","ohlc":["90","110","80","100"],"oi":["3","1"],'
            '"pb":["70","130"],"q":["72101","2","72100","3","72100.5"],'
            '"qiv":["0.4","0.5","0.45"],"to":["500","20"],'
            '"g":["0.1","0.2","0.3","0.4","0.5"]}]}',
        ]

    async def send(self, value: str) -> None:
        self.sent.append(value)

    async def recv(self) -> str:
        value = self.frames.pop(0)
        if not self.frames:
            self.stop.set()
        return value


class _ConnectionContext:
    def __init__(self, socket: _Socket) -> None:
        self.socket = socket

    async def __aenter__(self) -> _Socket:
        return self.socket

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_connection_requires_ack_then_publishes_catalogue_bound_ticker() -> None:
    stop = asyncio.Event()
    socket = _Socket(stop)
    queue: asyncio.Queue[TickerObservation] = asyncio.Queue(maxsize=10)

    def connect(_url: str, **_options: object) -> _ConnectionContext:
        return _ConnectionContext(socket)

    await _connection(
        endpoint_url="wss://public-socket.india.delta.exchange",
        snapshot=_catalogue(),
        snapshot_id=UUID("10000000-0000-4000-8000-000000000001"),
        symbols=("XBTUSD_CAT",),
        queue=queue,
        stop=stop,
        connect_factory=connect,
    )

    ticker = queue.get_nowait()
    assert type(ticker) is TickerObservation
    assert ticker.product_id == 1101
    assert ticker.symbol == "XBTUSD_CAT"
    assert ticker.mark_price == 100
    assert ticker.best_bid == 72100
    assert ticker.best_ask == 72101
    assert socket.sent == [
        '{"type":"subscribe","payload":{"channels":['
        '{"name":"ticker","symbols":["XBTUSD_CAT"]}]}}',
        '{"type":"enable_heartbeat"}',
    ]
