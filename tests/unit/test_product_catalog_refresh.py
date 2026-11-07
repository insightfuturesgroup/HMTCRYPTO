"""Atomic product-catalogue refresh, last-good, and temporal policy."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from product_catalog_test_support import (
    BASE_TIME,
    MISSING_AFTER,
    FetchQueue,
    MutableClock,
    SequenceClock,
    catalog_api,
    complete_load,
    load_products,
    new_catalog,
    truncated_load,
)

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ExchangeDownError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_result",
    [
        (),
        [],
        None,
        "products",
    ],
)
async def test_bare_or_wrong_fetch_results_fail_closed(bad_result: object) -> None:
    api = catalog_api()
    catalog = new_catalog(FetchQueue(bad_result))  # type: ignore[arg-type]
    result = await catalog.refresh()
    assert result.status is api.CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "truncated", "nonterminal", "tampered"])
async def test_incomplete_or_empty_pagination_never_publishes(kind: str) -> None:
    api = catalog_api()
    products = load_products()
    if kind == "empty":
        result = complete_load(())
    elif kind == "truncated":
        result = truncated_load(products)
    elif kind == "nonterminal":
        result = complete_load(products)
        object.__setattr__(result.pagination, "terminal_after", "still-more")
    else:
        result = complete_load(products)
        object.__setattr__(result.pagination, "items", (products[0], "raw-payload"))

    catalog = new_catalog(FetchQueue(result))
    refresh = await catalog.refresh()
    assert refresh.status is api.CatalogRefreshStatus.UNAVAILABLE
    assert refresh.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is None


@pytest.mark.asyncio
async def test_terminal_cursor_requires_exact_null_or_missing_evidence() -> None:
    class NullLookalike:
        def __eq__(self, other: object) -> bool:
            return other is None

    products = load_products()
    result = complete_load(products)
    object.__setattr__(result.pagination, "terminal_after", NullLookalike())
    catalog = new_catalog(FetchQueue(result))

    refresh = await catalog.refresh()

    assert refresh.status is catalog_api().CatalogRefreshStatus.UNAVAILABLE
    assert refresh.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [None, MISSING_AFTER])
async def test_complete_null_or_missing_terminal_cursor_is_accepted(terminal: object) -> None:
    api = catalog_api()
    catalog = new_catalog(FetchQueue(complete_load(load_products(), terminal_after=terminal)))
    result = await catalog.refresh()
    assert result.status is api.CatalogRefreshStatus.PUBLISHED_CHANGED
    assert catalog.peek() is not None


@pytest.mark.asyncio
async def test_bad_refresh_preserves_exact_last_good_and_expiry() -> None:
    api = catalog_api()
    clock = MutableClock()
    products = load_products()
    fetcher = FetchQueue(
        complete_load(products),
        truncated_load(products),
        ExchangeDownError(context={"operation": "products"}),
    )
    catalog = new_catalog(fetcher, clock=clock, ttl_seconds=60)
    first_result = await catalog.refresh()
    first = catalog.peek()
    assert first is not None

    clock.now += timedelta(seconds=30)
    second_result = await catalog.refresh()
    assert second_result.status is api.CatalogRefreshStatus.RETAINED_LAST_GOOD
    assert second_result.safe_error_code == "catalog_validation_failed"
    assert catalog.peek() is first
    assert catalog.peek().fresh_until == first.fresh_until

    clock.now += timedelta(seconds=10)
    third_result = await catalog.refresh()
    assert third_result.status is api.CatalogRefreshStatus.RETAINED_LAST_GOOD
    assert third_result.safe_error_code == "exchange_down"
    assert catalog.peek() is first
    assert first_result.active_revision == third_result.active_revision == 1


@pytest.mark.asyncio
async def test_successful_unchanged_refresh_renews_revision_and_freshness() -> None:
    api = catalog_api()
    clock = MutableClock()
    products = load_products()
    catalog = new_catalog(
        FetchQueue(complete_load(products), complete_load(tuple(reversed(products)))),
        clock=clock,
        ttl_seconds=60,
    )
    first_result = await catalog.refresh()
    first = catalog.peek()
    assert first is not None

    clock.now += timedelta(seconds=30)
    second_result = await catalog.refresh()
    second = catalog.peek()

    assert second is not None and second is not first
    assert second_result.status is api.CatalogRefreshStatus.PUBLISHED_UNCHANGED
    assert second.revision == 2
    assert second.content_sha256 == first.content_sha256
    assert second.fresh_until == clock.now + timedelta(seconds=60)
    assert first_result.status is api.CatalogRefreshStatus.PUBLISHED_CHANGED


@pytest.mark.asyncio
async def test_changed_refresh_increments_revision_and_changes_hash() -> None:
    api = catalog_api()
    products = load_products()
    changed = products[0].model_copy(update={"tick_size": products[0].tick_size * 2})
    catalog = new_catalog(
        FetchQueue(complete_load(products), complete_load((changed, products[1])))
    )
    await catalog.refresh()
    first = catalog.peek()
    second_result = await catalog.refresh()
    second = catalog.peek()
    assert first is not None and second is not None
    assert second_result.status is api.CatalogRefreshStatus.PUBLISHED_CHANGED
    assert second.revision == 2
    assert second.content_sha256 != first.content_sha256


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ExchangeDownError(context={"operation": "products", "raw_body": "FIXTURE-SECRET"}),
        RuntimeError("FIXTURE-SECRET-EXCEPTION"),
        MemoryError("FIXTURE-SECRET-MEMORY"),
    ],
)
async def test_initial_failures_normalize_without_leaking(failure: BaseException) -> None:
    api = catalog_api()
    catalog = new_catalog(FetchQueue(failure))
    result = await catalog.refresh()
    assert result.status is api.CatalogRefreshStatus.UNAVAILABLE
    rendered = json.dumps({"repr": repr(result), "code": result.safe_error_code}, default=str)
    assert "FIXTURE-SECRET" not in rendered
    if isinstance(failure, ExchangeDownError):
        assert result.safe_error_code == "exchange_down"
    else:
        assert result.safe_error_code == "catalog_refresh_failed"


@pytest.mark.asyncio
async def test_fetcher_cancelled_error_propagates_and_never_publishes() -> None:
    async def cancelled() -> object:
        raise asyncio.CancelledError

    catalog = new_catalog(cancelled)  # type: ignore[arg-type]
    with pytest.raises(asyncio.CancelledError):
        await catalog.refresh()
    assert catalog.peek() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "times",
    [
        # attempted later than fetched
        (
            BASE_TIME + timedelta(seconds=1),
            BASE_TIME,
            BASE_TIME + timedelta(seconds=2),
        ),
        # fetched later than validated
        (
            BASE_TIME,
            BASE_TIME + timedelta(seconds=2),
            BASE_TIME + timedelta(seconds=1),
        ),
        # naive evidence
        (
            datetime(2026, 11, 3, 10, 0),
            BASE_TIME,
            BASE_TIME,
        ),
    ],
)
async def test_clock_evidence_must_be_aware_and_monotonic(
    times: tuple[datetime, datetime, datetime],
) -> None:
    api = catalog_api()
    catalog = new_catalog(FetchQueue(complete_load(load_products())), clock=SequenceClock(*times))
    result = await catalog.refresh()
    assert result.status is api.CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_clock_invalid"
    assert catalog.peek() is None


@pytest.mark.asyncio
async def test_unexpected_clock_failure_is_normalized_safely() -> None:
    def broken_clock() -> datetime:
        raise RuntimeError("FIXTURE-SECRET-CLOCK")

    catalog = new_catalog(FetchQueue(complete_load(load_products())), clock=broken_clock)

    result = await catalog.refresh()

    assert result.status is catalog_api().CatalogRefreshStatus.UNAVAILABLE
    assert result.safe_error_code == "catalog_clock_invalid"
    assert "FIXTURE-SECRET" not in repr(result)
    assert catalog.peek() is None


@pytest.mark.parametrize(
    "ttl",
    [timedelta(0), timedelta(seconds=-1), True, 300],
)
def test_ttl_is_a_strictly_positive_timedelta(ttl: object) -> None:
    api = catalog_api()
    source = api.CatalogSource(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )

    async def fetch() -> object:
        return complete_load(())

    with pytest.raises((TypeError, ValueError)):
        api.ProductCatalog(source=source, fetch_products=fetch, ttl=ttl, clock=MutableClock())


def test_refresh_result_is_immutable_and_safe() -> None:
    api = catalog_api()
    result = api.CatalogRefreshResult(
        status=api.CatalogRefreshStatus.UNAVAILABLE,
        attempted_at=BASE_TIME,
        active_revision=None,
        active_content_sha256=None,
        product_count=0,
        safe_error_code="catalog_refresh_failed",
    )
    with pytest.raises((TypeError, AttributeError)):
        result.product_count = 9
    assert "catalog_refresh_failed" in repr(result)
    assert "content_sha256" not in repr(result)


@pytest.mark.parametrize(
    ("status_name", "revision", "content_hash", "count", "error_code"),
    [
        ("UNAVAILABLE", None, None, 0, None),
        ("RETAINED_LAST_GOOD", None, None, 0, "catalog_refresh_failed"),
        ("RETAINED_LAST_GOOD", 1, "0" * 64, 1, None),
        ("RETAINED_LAST_GOOD", 1, "0" * 64, 0, "catalog_refresh_failed"),
    ],
)
def test_refresh_result_rejects_contradictory_status_evidence(
    status_name: str,
    revision: int | None,
    content_hash: str | None,
    count: int,
    error_code: str | None,
) -> None:
    api = catalog_api()
    with pytest.raises(ValueError):
        api.CatalogRefreshResult(
            status=getattr(api.CatalogRefreshStatus, status_name),
            attempted_at=BASE_TIME,
            active_revision=revision,
            active_content_sha256=content_hash,
            product_count=count,
            safe_error_code=error_code,
        )
