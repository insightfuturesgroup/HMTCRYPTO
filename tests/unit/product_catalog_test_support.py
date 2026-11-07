"""Shared deterministic product-catalogue fixtures."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.domain import Product
from hmt_crypto.exchange.delta.catalog import CatalogProductLoad, CatalogSource
from hmt_crypto.exchange.delta.paginator import MISSING_AFTER, PaginationResult
from hmt_crypto.exchange.delta.wire_schemas import parse_product

FIXTURES = Path(__file__).parents[1] / "fixtures" / "delta"
BASE_TIME = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


def catalog_api() -> SimpleNamespace:
    """Load the wished-for API while keeping initial RED behavior explicit."""
    try:
        module = importlib.import_module("hmt_crypto.exchange.delta.catalog")
        errors = importlib.import_module("hmt_crypto.errors")
    except (AttributeError, ImportError, ModuleNotFoundError):
        pytest.fail("Delta product-catalogue API is missing", pytrace=False)
    names = (
        "CatalogProductLoad",
        "CatalogRefreshResult",
        "CatalogRefreshStatus",
        "CatalogSnapshot",
        "CatalogSource",
        "ProductCatalog",
        "venue_allows_new_orders",
    )
    if any(not hasattr(module, name) for name in names):
        pytest.fail("Delta product-catalogue API is missing", pytrace=False)
    return SimpleNamespace(
        **{name: getattr(module, name) for name in names},
        CatalogUnavailableError=getattr(errors, "CatalogUnavailableError", None),
        ProductNotFoundError=getattr(errors, "ProductNotFoundError", None),
        StaleCatalogError=getattr(errors, "StaleCatalogError", None),
    )


def load_products(name: str = "products_catalog_valid.json") -> tuple[Product, ...]:
    """Load sanitized official-shape product fixtures through the real wire parser."""
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    return tuple(parse_product(item, endpoint_id="catalog_fixture") for item in raw)


def complete_result(
    products: Sequence[Product],
    *,
    page_count: int = 1,
    terminal_after: object = None,
) -> PaginationResult[Product]:
    """Build explicit complete pagination evidence."""
    return PaginationResult(
        items=products,
        page_count=page_count,
        complete=True,
        truncated=False,
        terminal_after=terminal_after,
    )


def truncated_result(products: Sequence[Product]) -> PaginationResult[Product]:
    """Build explicit incomplete pagination evidence."""
    return PaginationResult(
        items=products,
        page_count=1,
        complete=False,
        truncated=True,
        terminal_after="more-products",
    )


def catalog_source(
    region: str = "india",
    environment: str = "testnet",
) -> CatalogSource:
    """Build one exact typed catalogue source."""
    return CatalogSource(
        region=DeltaRegion(region),
        environment=DeltaEnvironment(environment),
    )


def product_load(
    pagination: PaginationResult[Product],
    *,
    region: str = "india",
    environment: str = "testnet",
) -> CatalogProductLoad:
    """Bind paginator evidence to exact source provenance."""
    return CatalogProductLoad(
        source=catalog_source(region, environment),
        pagination=pagination,
    )


def complete_load(
    products: Sequence[Product],
    *,
    page_count: int = 1,
    terminal_after: object = None,
    region: str = "india",
    environment: str = "testnet",
) -> CatalogProductLoad:
    """Build a complete source-bearing catalogue load."""
    return product_load(
        complete_result(
            products,
            page_count=page_count,
            terminal_after=terminal_after,
        ),
        region=region,
        environment=environment,
    )


def truncated_load(products: Sequence[Product]) -> CatalogProductLoad:
    """Build an explicitly truncated source-bearing catalogue load."""
    return product_load(truncated_result(products))


class MutableClock:
    """UTC clock controlled by a test."""

    def __init__(self, now: datetime = BASE_TIME) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class SequenceClock:
    """Clock returning a predetermined sequence for temporal-integrity tests."""

    def __init__(self, *values: datetime) -> None:
        self._values = list(values)

    def __call__(self) -> datetime:
        if not self._values:
            raise AssertionError("clock sequence exhausted")
        return self._values.pop(0)


class FetchQueue:
    """Async fetch callback returning queued results or raising queued failures."""

    def __init__(self, *values: object) -> None:
        self.values = list(values)
        self.calls = 0

    async def __call__(self) -> Any:
        self.calls += 1
        if not self.values:
            raise AssertionError("fetch queue exhausted")
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            returned = value()
            if isinstance(returned, Awaitable):
                return await returned
            return returned
        return value


class GatedFetch:
    """One fetch operation whose completion is released explicitly."""

    def __init__(self, result: CatalogProductLoad) -> None:
        self.result = result
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def __call__(self) -> CatalogProductLoad:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.result


async def settle() -> None:
    """Let catalogue tasks reach their next await boundary."""
    for _ in range(12):
        await asyncio.sleep(0)


def new_catalog(
    fetcher: Callable[[], Awaitable[CatalogProductLoad]],
    *,
    clock: Callable[[], datetime] | None = None,
    region: str = "india",
    environment: str = "testnet",
    ttl_seconds: int = 300,
) -> Any:
    """Construct the wished-for catalogue with common safe defaults."""
    api = catalog_api()
    from datetime import timedelta

    source = catalog_source(region, environment)
    return api.ProductCatalog(
        source=source,
        fetch_products=fetcher,
        ttl=timedelta(seconds=ttl_seconds),
        clock=clock or MutableClock(),
    )


__all__ = [
    "BASE_TIME",
    "FetchQueue",
    "GatedFetch",
    "MISSING_AFTER",
    "MutableClock",
    "SequenceClock",
    "catalog_source",
    "catalog_api",
    "complete_load",
    "complete_result",
    "load_products",
    "new_catalog",
    "product_load",
    "settle",
    "truncated_result",
    "truncated_load",
]
