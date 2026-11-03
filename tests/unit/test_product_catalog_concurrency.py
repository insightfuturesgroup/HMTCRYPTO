"""Concurrent product-catalogue refresh and publication behavior."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from product_catalog_test_support import (
    GatedFetch,
    MutableClock,
    catalog_api,
    complete_load,
    load_products,
    new_catalog,
    settle,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_concurrent_refreshes_coalesce_and_share_one_result_object() -> None:
    products = load_products()
    fetcher = GatedFetch(complete_load(products))
    catalog = new_catalog(fetcher)

    callers = [asyncio.create_task(catalog.refresh()) for _ in range(5)]
    await fetcher.started.wait()
    await settle()
    assert fetcher.calls == 1
    assert catalog.peek() is None

    fetcher.release.set()
    results = await asyncio.gather(*callers)

    assert fetcher.calls == 1
    assert all(result is results[0] for result in results)
    assert catalog.peek() is not None
    assert results[0].active_revision == 1


@pytest.mark.asyncio
async def test_cancelling_one_waiter_does_not_cancel_shared_publication() -> None:
    products = load_products()
    fetcher = GatedFetch(complete_load(products))
    catalog = new_catalog(fetcher)

    cancelled_waiter = asyncio.create_task(catalog.refresh())
    surviving_waiter = asyncio.create_task(catalog.refresh())
    await fetcher.started.wait()
    await settle()
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    assert not surviving_waiter.done()
    assert catalog.peek() is None

    fetcher.release.set()
    result = await surviving_waiter
    assert result.active_revision == 1
    assert fetcher.calls == 1
    assert catalog.peek() is not None


class TwoStageFetch:
    """Initial immediate result followed by a gated replacement result."""

    def __init__(self, first: Any, second: Any) -> None:
        self.first = first
        self.second = second
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self) -> Any:
        self.calls += 1
        if self.calls == 1:
            return self.first
        self.started.set()
        await self.release.wait()
        return self.second


@pytest.mark.asyncio
async def test_readers_observe_only_complete_old_or_new_snapshots() -> None:
    products = load_products()
    changed = products[0].model_copy(update={"tick_size": products[0].tick_size * 2})
    fetcher = TwoStageFetch(complete_load(products), complete_load((changed, products[1])))
    catalog = new_catalog(fetcher)
    await catalog.refresh()
    old = catalog.peek()
    assert old is not None

    refresh_task = asyncio.create_task(catalog.refresh())
    await fetcher.started.wait()
    observations = [catalog.peek() for _ in range(100)]
    assert all(item is old for item in observations)
    assert old.by_id[1101].tick_size == products[0].tick_size

    fetcher.release.set()
    result = await refresh_task
    new = catalog.peek()
    assert new is not None and new is not old
    assert result.active_revision == 2
    assert new.by_id[1101].tick_size == changed.tick_size
    assert old.by_id[1101].tick_size == products[0].tick_size


@pytest.mark.asyncio
async def test_finished_refresh_does_not_block_a_later_refresh() -> None:
    products = load_products()
    fetcher = TwoStageFetch(complete_load(products), complete_load(products))
    clock = MutableClock()
    catalog = new_catalog(fetcher, clock=clock)
    first = await catalog.refresh()
    clock.now += timedelta(seconds=30)

    second_task = asyncio.create_task(catalog.refresh())
    await fetcher.started.wait()
    assert fetcher.calls == 2
    fetcher.release.set()
    second = await second_task

    assert first is not second
    assert second.active_revision == 2
    assert fetcher.calls == 2


@pytest.mark.asyncio
async def test_concurrent_failure_returns_one_safe_result_and_keeps_last_good() -> None:
    api = catalog_api()
    products = load_products()

    class FailingSecondFetch(TwoStageFetch):
        async def __call__(self) -> Any:
            value = await super().__call__()
            if self.calls == 2:
                raise RuntimeError("FIXTURE-SECRET-CONCURRENT")
            return value

    fetcher = FailingSecondFetch(complete_load(products), complete_load(products))
    catalog = new_catalog(fetcher)
    await catalog.refresh()
    old = catalog.peek()

    callers = [asyncio.create_task(catalog.refresh()) for _ in range(3)]
    await fetcher.started.wait()
    fetcher.release.set()
    results = await asyncio.gather(*callers)

    assert all(result is results[0] for result in results)
    assert results[0].status is api.CatalogRefreshStatus.RETAINED_LAST_GOOD
    assert results[0].safe_error_code == "catalog_refresh_failed"
    assert catalog.peek() is old


@pytest.mark.asyncio
async def test_shared_refresh_self_cancellation_propagates_without_partial_state() -> None:
    started = asyncio.Event()

    async def self_cancel() -> Any:
        started.set()
        raise asyncio.CancelledError

    catalog = new_catalog(self_cancel)
    callers = [asyncio.create_task(catalog.refresh()) for _ in range(2)]
    await started.wait()
    results = await asyncio.gather(*callers, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert catalog.peek() is None
