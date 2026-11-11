"""Rollback-only operator probe for public catalogue persistence failures."""

from __future__ import annotations

import asyncio
import json
import ssl
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.domain.enums import ContractType
from hmt_crypto.exchange.base import ProductQuery
from hmt_crypto.exchange.delta.catalog import CatalogProductLoad, CatalogSource, ProductCatalog
from hmt_crypto.persistence.configuration import database_url
from hmt_crypto.persistence.models import CatalogueProduct, CatalogueSnapshot
from hmt_crypto.worker.market_runner import _client


def _safe_database_evidence(error: BaseException) -> dict[str, str]:
    """Extract bounded structural diagnostics without retaining server messages."""
    evidence: dict[str, str] = {"error_type": type(error).__name__[:64]}
    current: BaseException | None = error
    depth = 0
    while current is not None and depth < 6:
        sqlstate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if isinstance(sqlstate, str) and len(sqlstate) == 5 and sqlstate.isalnum():
            evidence["sqlstate"] = sqlstate
        constraint = getattr(current, "constraint_name", None)
        if (
            isinstance(constraint, str)
            and 1 <= len(constraint) <= 128
            and constraint.replace("_", "a").isalnum()
            and constraint.isascii()
        ):
            evidence["constraint"] = constraint
        next_error = current.__cause__ or current.__context__
        current = next_error if isinstance(next_error, BaseException) else None
        depth += 1
    return evidence


async def _run() -> dict[str, object]:
    runtime = ControlRuntimeSettings()
    config = runtime.database_runtime_config()
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
        ttl=timedelta(minutes=30),
        clock=lambda: datetime.now(UTC),
    )
    engine = None
    connection = None
    transaction = None
    session = None
    stage = "catalogue_fetch"
    try:
        await catalog.refresh()
        snapshot = catalog.require_snapshot(at=datetime.now(UTC))
        products = tuple(
            product
            for product in snapshot.products
            if product.contract_type
            in {ContractType.PERPETUAL_FUTURES, ContractType.FUTURES}
        )
        connect_args: dict[str, object] = {"server_settings": {"timezone": "UTC"}}
        if config.database_ca_file is not None:
            connect_args["ssl"] = ssl.create_default_context(cafile=config.database_ca_file)
        engine = create_async_engine(
            database_url(config.database_url),
            hide_parameters=True,
            pool_size=1,
            max_overflow=0,
            connect_args=connect_args,
        )
        connection = await engine.connect()
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        latest = await session.scalar(
            select(func.max(CatalogueSnapshot.revision)).where(
                CatalogueSnapshot.region == "india",
                CatalogueSnapshot.environment == runtime.delta_env.value,
            )
        )
        snapshot_id = uuid4()
        stage = "snapshot_flush"
        session.add(
            CatalogueSnapshot(
                id=snapshot_id,
                region="india",
                environment=runtime.delta_env.value,
                revision=int(latest or 0) + 1,
                source="delta_rest_v2_products",
                content_sha256=snapshot.content_sha256,
                fetched_at=snapshot.fetched_at,
                validated_at=snapshot.validated_at,
                fresh_until=snapshot.fresh_until,
                product_count=len(products),
            )
        )
        await session.flush()
        stage = "products_flush"
        session.add_all(
            CatalogueProduct.from_product(snapshot_id=snapshot_id, product=product)
            for product in products
        )
        await session.flush()
        return {"status": "PASS", "rolled_back": True, "product_count": len(products)}
    except BaseException as error:
        return {
            "status": "FAIL",
            "rolled_back": True,
            "stage": stage,
            **_safe_database_evidence(error),
        }
    finally:
        if session is not None:
            await session.close()
        if transaction is not None and transaction.is_active:
            await transaction.rollback()
        if connection is not None:
            await connection.close()
        if engine is not None:
            await engine.dispose()
        await client.aclose()


def main() -> None:
    """Run the probe and emit one JSON result; no transaction is committed."""
    print(json.dumps(asyncio.run(_run()), sort_keys=True))


if __name__ == "__main__":
    main()

