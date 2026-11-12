"""Credential-free Delta India public ticker WebSocket ingestion with REST fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

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
from hmt_crypto.market_data.contracts import TickerObservation
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import MarketQuote

_MAX_FRAME_BYTES = 16 * 1024 * 1024
_SYMBOLS_PER_CONNECTION = 100
_QUEUE_LIMIT = 10_000
_BATCH_LIMIT = 500
_ConnectFactory = Callable[..., Any]


async def _persist_batch(
    database: Database,
    runtime: ControlRuntimeSettings,
    rows: list[TickerObservation],
    persistence_lock: asyncio.Lock,
) -> None:
    latest = {row.product_id: row for row in rows}
    updated = datetime.now(UTC)
    async with persistence_lock:
        async with database.transaction() as session:
            insert = (
                pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
            )
            quote_values = [
                {
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
                for ticker in sorted(latest.values(), key=lambda item: item.product_id)
            ]
            if quote_values:
                statement = insert(MarketQuote).values(quote_values)
                statement = statement.on_conflict_do_update(
                    index_elements=["region", "environment", "product_id"],
                    set_={
                        "symbol": statement.excluded.symbol,
                        "mark_price": statement.excluded.mark_price,
                        "spot_price": statement.excluded.spot_price,
                        "best_bid": statement.excluded.best_bid,
                        "best_ask": statement.excluded.best_ask,
                        "observed_at": statement.excluded.observed_at,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
                await session.execute(statement)


async def _consume(
    database: Database,
    runtime: ControlRuntimeSettings,
    queue: asyncio.Queue[TickerObservation],
    persistence_lock: asyncio.Lock,
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
            await _persist_batch(database, runtime, batch, persistence_lock)
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
    queue: asyncio.Queue[TickerObservation],
    stop: asyncio.Event,
    connect_factory: _ConnectFactory,
) -> None:
    logger = get_logger("worker.market.websocket")
    subscription = WsSubscription.for_channel(WsChannel.TICKER, symbols=symbols)
    expected = desired_subscriptions(revision=1, entries=(subscription,))
    parser = DeltaPublicFrameParser()
    backoff = 1
    rejected_frames = 0
    while not stop.is_set():
        generation = uuid4()
        sequence = 1
        stage = "connect"
        try:
            async with connect_factory(
                endpoint_url,
                max_size=_MAX_FRAME_BYTES,
                open_timeout=10,
                close_timeout=5,
                ping_interval=None,
            ) as socket:
                stage = "subscribe"
                await socket.send(encode_subscribe(expected.entries))
                await socket.send(encode_enable_heartbeat())
                accepted = False
                while not stop.is_set():
                    stage = "receive"
                    frame = await asyncio.wait_for(socket.recv(), timeout=30)
                    stage = "decode"
                    message = decode_ws_object(
                        frame,
                        max_bytes=_MAX_FRAME_BYTES,
                        stream_label="public",
                    )
                    message_type = message.get("type")
                    if message_type in {"heartbeat", "pong"}:
                        continue
                    if message_type == "subscriptions":
                        stage = "subscription_ack"
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
                        stage = "parse"
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
                        if isinstance(outcome, TickerObservation):
                            stage = "enqueue"
                            await asyncio.wait_for(queue.put(outcome), timeout=2)
                    if outcomes:
                        backoff = 1
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "public_ticker_reconnect",
                backoff_seconds=backoff,
                failure_stage=stage,
                error_type=type(error).__name__,
            )
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
    persistence_lock: asyncio.Lock,
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
    queue: asyncio.Queue[TickerObservation] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
    snapshot_id = uuid4()
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_consume(database, runtime, queue, persistence_lock, stop))
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
