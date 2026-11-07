"""Shared helpers for cursor paginator unit tests."""

from __future__ import annotations

from importlib import import_module
from typing import Any

import pytest


def paginator_api() -> Any:
    """Load the wished-for API without converting RED into collection failure."""
    try:
        return import_module("hmt_crypto.exchange.delta.paginator")
    except ModuleNotFoundError:
        pytest.fail("cursor paginator API is missing", pytrace=False)


async def collect(
    fetch: Any,
    *,
    filters: dict[str, object] | None = None,
    page_size: object = 2,
    page_size_cap: object = 100,
    max_pages: object = 10,
    max_items: object = 100,
    initial_after: object = None,
    truncate: object = False,
) -> Any:
    """Call the paginator with safe defaults that individual tests may override."""
    api = paginator_api()
    return await api.collect_cursor_pages(
        fetch,
        filters={} if filters is None else filters,
        page_size=page_size,
        page_size_cap=page_size_cap,
        max_pages=max_pages,
        max_items=max_items,
        initial_after=initial_after,
        truncate_at_max_items=truncate,
    )
