"""Credential-free Delta India public ticker WebSocket ingestion with REST fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from websockets.asyncio.client import connect as websocket_connect

from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.config import ExecutionMode
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints
from hmt_crypto.exchange.delta.websocket_codec import (
    decode_ws_object,
    encode_enable_heartbeat,
    encode_subscribe,
)
from hmt_crypto.exchange.delta.websocket_contracts import (
    WsCandleResolution,
    WsChannel,
    WsPlane,
    require_delta_ws_endpoint,
)
from hmt_crypto.exchange.delta.websocket_public_parser_support import PublicParseContext
from hmt_crypto.exchange.delta.websocket_public_parsers import DeltaPublicFrameParser
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    WsSubscription,
    desired_subscriptions,
    parse_subscriptions_ack,
)
from hmt_crypto.logging import get_logger
from hmt_crypto.market_data.contracts import CandleObservation, TickerObservation
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import MarketCandle, MarketQuote

_MAX_FRAME_BYTES = 16 * 1024 * 1024
_SYMBOLS_PER_CONNECTION = 100
_QUEUE_LIMIT = 10_000
_BATCH_LIMIT = 500
_ConnectFactory = Callable[..., Any]


async def _persist_batch(
    database: Database,
    runtime: ControlRuntimeSettings,
    rows: list[TickerObservation | CandleObservation],
) -> None:
    latest = {row.product_id: row for row in rows if type(row) is TickerObservation}
    candles = {
        (row.product_id, row.resolution, row.timestamp): row
        for row in rows
        if type(row) is CandleObservation
    }
    updated = datetime.now(UTC)
    async with database.transaction() as session:
        insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
        for ticker in latest.values():
            values = {
                "id": uuid4(),
                "region": "india",
                "environment": runtime.delta_env.value,
                "product_id": ticker.product_id,
                "symbol": ticker.symbol,
                "mark_price": None if ticker.mark_price is None else str(ticker.mark_price),
                "spot_price": None if ticker.spot_price is None else str(ticker.spot_price),
                "best_bid": None if ticker.best_bid is None else str(ticker.best_bid),
                "best_ask": None if ticker.best_ask is None else str(ticker.best_ask),
                "observed_at": ticker.source.observed_at,
                "updated_at": updated,
            }
            statement = insert(MarketQuote).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=["region", "environment", "product_id"],
                set_={key: value for key, value in values.items() if key != "id"},
            )
            await session.execute(statement)
        for candle in candles.values():
            values = {
                "id": uuid4(),
                "region": "india",
                "environment": runtime.delta_env.value,
                "product_id": candle.product_id,
                "symbol": candle.symbol,
                "resolution": candle.resolution,
                "timestamp": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "observed_at": candle.source.observed_at,
            }
            statement = insert(MarketCandle).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[
                    "region",
                    "environment",
                    "product_id",
                    "resolution",
                    "timestamp",
                ],
                set_={key: value for key, value in values.items() if key != "id"},
            )
            await session.execute(statement)
        if candles:
            await session.execute(
                delete(MarketCandle).where(
                    MarketCandle.region == "india",
                    MarketCandle.environment == runtime.delta_env.value,
                    MarketCandle.timestamp < updated - timedelta(hours=24),
                )
            )


async def _consume(
    database: Database,
    runtime: ControlRuntimeSettings,
    queue: asyncio.Queue[TickerObservation | CandleObservation],
    stop: asyncio.Event,
) -> None:
    logger = get_logger("worker.market.websocket")
    while not stop.is_set():
        try:
            first = await asyncio.wait_for(queue.get(), timeout=1)
        except TimeoutError:
            continue
        batch = [first]
        while len(batch) < _BATCH_LIMIT:
            try:
                batch.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        try:
            await _persist_batch(database, runtime, batch)
            logger.info(
                "public_market_batch_published",
                observation_count=len(batch),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("public_ticker_batch_failed")


async def _connection(
    *,
    endpoint_url: str,
    snapshot: CatalogSnapshot,
    snapshot_id: UUID,
    symbols: tuple[str, ...],
    queue: asyncio.Queue[TickerObservation | CandleObservation],
    stop: asyncio.Event,
    connect_factory: _ConnectFactory,
) -> None:
    logger = get_logger("worker.market.websocket")
    subscription = WsSubscription.for_channel(WsChannel.TICKER, symbols=symbols)
    candles = WsSubscription.for_candles(WsCandleResolution.ONE_MINUTE, symbols=symbols)
    expected = desired_subscriptions(revision=1, entries=(subscription, candles))
    parser = DeltaPublicFrameParser()
    backoff = 1
    rejected_frames = 0
    while not stop.is_set():
        generation = uuid4()
        sequence = 1
        try:
            async with connect_factory(
                endpoint_url,
                max_size=_MAX_FRAME_BYTES,
                open_timeout=10,
                close_timeout=5,
                ping_interval=None,
            ) as socket:
                await socket.send(encode_subscribe(expected.entries))
                await socket.send(encode_enable_heartbeat())
                accepted = False
                backoff = 1
                while not stop.is_set():
                    frame = await asyncio.wait_for(socket.recv(), timeout=30)
                    message = decode_ws_object(
                        frame,
                        max_bytes=_MAX_FRAME_BYTES,
                        stream_label="public",
                    )
                    message_type = message.get("type")
                    if message_type in {"heartbeat", "pong"}:
                        continue
                    if message_type == "subscriptions":
                        parse_subscriptions_ack(message, expected=expected)
                        accepted = True
                        logger.info("public_ticker_subscribed", symbol_count=len(symbols))
                        continue
                    if not accepted:
                        raise RuntimeError(
                            "public data arrived before subscription acknowledgement"
                        )
                    context = PublicParseContext(
                        catalogue=snapshot,
                        catalogue_snapshot_id=snapshot_id,
                        connection_generation=generation,
                        local_sequence_start=sequence,
                        received_at=datetime.now(UTC),
                        spot_asset_ids={},
                    )
                    try:
                        outcomes = parser.parse(message, context=context)
                    except ExchangeProtocolError:
                        rejected_frames += 1
                        if rejected_frames == 1 or rejected_frames % 100 == 0:
                            logger.warning(
                                "public_ticker_frame_rejected",
                                channel=message_type
                                if isinstance(message_type, str)
                                else "unknown",
                                rejected_count=rejected_frames,
                            )
                        continue
                    sequence += len(outcomes)
                    for outcome in outcomes:
                        if isinstance(outcome, (TickerObservation, CandleObservation)):
                            await asyncio.wait_for(queue.put(outcome), timeout=2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("public_ticker_reconnect", backoff_seconds=backoff)
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        except TimeoutError:
            backoff = min(backoff * 2, 30)


async def run_public_ticker_stream(
    runtime: ControlRuntimeSettings,
    snapshot: CatalogSnapshot,
    database: Database,
    *,
    stop: asyncio.Event,
    connect_factory: _ConnectFactory = websocket_connect,
) -> None:
    """Supervise chunked ticker subscriptions and durable quote publication."""
    endpoints = get_delta_endpoints(runtime.delta_region, runtime.delta_env)
    endpoint = require_delta_ws_endpoint(
        endpoints,
        plane=WsPlane.PUBLIC,
        mode=ExecutionMode.LIVE,
    )
    symbols = tuple(
        sorted(
            product.symbol
            for product in snapshot.products
            if product.symbol and product.state.value == "live"
        )
    )
    if not symbols:
        raise ValueError("public ticker stream requires a nonempty live catalogue")
    chunks = tuple(
        symbols[index : index + _SYMBOLS_PER_CONNECTION]
        for index in range(0, len(symbols), _SYMBOLS_PER_CONNECTION)
    )
    queue: asyncio.Queue[TickerObservation | CandleObservation] = asyncio.Queue(
        maxsize=_QUEUE_LIMIT
    )
    snapshot_id = uuid4()
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_consume(database, runtime, queue, stop))
        for chunk in chunks:
            tasks.create_task(
                _connection(
                    endpoint_url=endpoint.url,
                    snapshot=snapshot,
                    snapshot_id=snapshot_id,
                    symbols=chunk,
                    queue=queue,
                    stop=stop,
                    connect_factory=connect_factory,
                )
            )


__all__ = ["run_public_ticker_stream"]
