"""Durable public Delta India catalogue and quote polling worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.config import DeltaRegion
from hmt_crypto.domain.enums import ContractType
from hmt_crypto.exchange.base import ProductQuery
from hmt_crypto.exchange.delta.catalog import CatalogProductLoad, CatalogSource, ProductCatalog
from hmt_crypto.exchange.delta.client import DeltaPublicClient
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints
from hmt_crypto.exchange.delta.rate_limiter import RateLimitScope, ScopeKind, WeightedRateLimiter
from hmt_crypto.errors import HmtCryptoError
from hmt_crypto.logging import get_logger
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import CatalogueProduct, CatalogueSnapshot, MarketQuote
from hmt_crypto.worker.public_ticker_stream import run_public_ticker_stream


def _safe_failure_code(error: Exception) -> str:
    """Return an operator-safe category without retaining exception diagnostics."""
    if isinstance(error, HmtCryptoError):
        return error.code
    return "unexpected_error"


class MarketWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, case_sensitive=True, hide_input_in_errors=True
    )

    refresh_seconds: int = Field(default=15, ge=5, le=300, alias="HMT_MARKET_REFRESH_SECONDS")
    catalogue_seconds: int = Field(
        default=900, ge=60, le=86_400, alias="HMT_MARKET_CATALOGUE_SECONDS"
    )


def _client(settings: ControlRuntimeSettings) -> DeltaPublicClient:
    endpoints = get_delta_endpoints(settings.delta_region, settings.delta_env)
    limiter = WeightedRateLimiter(
        RateLimitScope(
            region=settings.delta_region.value,
            environment=settings.delta_env.value,
            kind=ScopeKind.IP,
            external_id="hmt-crypto-market-worker",
        )
    )
    return DeltaPublicClient(
        endpoints=endpoints,
        limiter=limiter,
        user_agent="hmt-crypto-market-worker/1.0",
    )


async def run_market_worker(
    runtime: ControlRuntimeSettings,
    process: MarketWorkerSettings,
    *,
    stop: asyncio.Event,
) -> None:
    if runtime.delta_region is not DeltaRegion.INDIA:
        raise ValueError("market worker supports Delta India only")
    database = Database(runtime.database_runtime_config())
    logger = get_logger("worker.market")
    source = CatalogSource(region=runtime.delta_region, environment=runtime.delta_env)
    client = _client(runtime)

    async def fetch_products() -> CatalogProductLoad:
        page = await client.get_products(
            query=ProductQuery(
                contract_types=("perpetual_futures", "futures"),
                states=("live",),
            )
        )
        return CatalogProductLoad(source=source, pagination=page)

    catalog = ProductCatalog(
        source=source,
        fetch_products=fetch_products,
        ttl=timedelta(seconds=process.catalogue_seconds * 2),
        clock=lambda: datetime.now(UTC),
    )
    last_catalogue = datetime.min.replace(tzinfo=UTC)
    stream_task: asyncio.Task[None] | None = None
    logger.info("market_worker_started", region="india", environment=runtime.delta_env.value)
    try:
        while not stop.is_set():
            now = datetime.now(UTC)
            stage = "catalogue_due_check"
            try:
                if now - last_catalogue >= timedelta(seconds=process.catalogue_seconds):
                    stage = "catalogue_fetch"
                    result = await catalog.refresh()
                    stage = "catalogue_validate"
                    snapshot = catalog.require_snapshot(at=datetime.now(UTC))
                    products = tuple(
                        product
                        for product in snapshot.products
                        if product.contract_type
                        in {ContractType.PERPETUAL_FUTURES, ContractType.FUTURES}
                    )
                    stage = "catalogue_persist"
                    async with database.transaction() as session:
                        latest = await session.scalar(
                            select(func.max(CatalogueSnapshot.revision)).where(
                                CatalogueSnapshot.region == "india",
                                CatalogueSnapshot.environment == runtime.delta_env.value,
                            )
                        )
                        revision = int(latest or 0) + 1
                        snapshot_id = uuid4()
                        session.add(
                            CatalogueSnapshot(
                                id=snapshot_id,
                                region="india",
                                environment=runtime.delta_env.value,
                                revision=revision,
                                source="delta_rest_v2_products",
                                content_sha256=snapshot.content_sha256,
                                fetched_at=snapshot.fetched_at,
                                validated_at=snapshot.validated_at,
                                fresh_until=snapshot.fresh_until,
                                product_count=len(products),
                            )
                        )
                        # These mappers intentionally have no ORM relationship. Flush the
                        # parent explicitly so SQLAlchemy cannot schedule child inserts ahead
                        # of the database foreign-key target. The surrounding transaction
                        # still commits or rolls back the complete catalogue atomically.
                        stage = "catalogue_snapshot_flush"
                        await session.flush()
                        session.add_all(
                            CatalogueProduct.from_product(snapshot_id=snapshot_id, product=product)
                            for product in products
                        )
                        stage = "catalogue_products_flush"
                        await session.flush()
                        stage = "catalogue_commit"
                    last_catalogue = now
                    if stream_task is not None:
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                    stream_task = asyncio.create_task(
                        run_public_ticker_stream(runtime, snapshot, database, stop=stop)
                    )
                    logger.info(
                        "market_catalogue_published",
                        revision=revision,
                        product_count=len(products),
                        refresh_status=result.status.value,
                    )
                stage = "catalogue_read"
                snapshot = catalog.require_snapshot(at=datetime.now(UTC))
                if stream_task is not None and stream_task.done():
                    try:
                        stream_task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.error("market_websocket_supervisor_failed")
                    stream_task = asyncio.create_task(
                        run_public_ticker_stream(runtime, snapshot, database, stop=stop)
                    )
                allowed = {
                    product.id
                    for product in snapshot.products
                    if product.contract_type
                    in {ContractType.PERPETUAL_FUTURES, ContractType.FUTURES}
                }
                stage = "ticker_fetch"
                tickers = await client.get_tickers(
                    contract_types=("perpetual_futures", "futures")
                )
                observed = datetime.now(UTC)
                stage = "quote_persist"
                async with database.transaction() as session:
                    insert = (
                        pg_insert
                        if session.get_bind().dialect.name == "postgresql"
                        else sqlite_insert
                    )
                    for ticker in tickers:
                        if ticker.product_id not in allowed:
                            continue
                        values = {
                            "id": uuid4(),
                            "region": "india",
                            "environment": runtime.delta_env.value,
                            "product_id": ticker.product_id,
                            "symbol": ticker.symbol,
                            "mark_price": None
                            if ticker.mark_price is None
                            else str(ticker.mark_price),
                            "spot_price": None
                            if ticker.spot_price is None
                            else str(ticker.spot_price),
                            "best_bid": None if ticker.best_bid is None else str(ticker.best_bid),
                            "best_ask": None if ticker.best_ask is None else str(ticker.best_ask),
                            "observed_at": ticker.timestamp,
                            "updated_at": observed,
                        }
                        statement = insert(MarketQuote).values(**values)
                        statement = statement.on_conflict_do_update(
                            index_elements=["region", "environment", "product_id"],
                            set_={key: value for key, value in values.items() if key != "id"},
                        )
                        await session.execute(statement)
                logger.info("market_quotes_updated", quote_count=len(tickers))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error(
                    "market_refresh_failed",
                    stage=stage,
                    error_code=_safe_failure_code(error),
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=process.refresh_seconds)
            except TimeoutError:
                pass
    finally:
        if stream_task is not None:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
        await client.aclose()
        await database.aclose()
        logger.info("market_worker_stopped")


__all__ = ["MarketWorkerSettings", "run_market_worker"]
