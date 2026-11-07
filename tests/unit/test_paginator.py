"""Transport-neutral cursor pagination and completeness behavior."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from paginator_test_support import collect, paginator_api

from hmt_crypto.errors import AuthError, ConfigurationError, ExchangeProtocolError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_collects_normal_chain_and_replaces_after_cursor() -> None:
    api = paginator_api()
    calls: list[dict[str, object]] = []
    pages = [
        api.CursorPage(items=[1, 2], after="cursor-a"),
        api.CursorPage(items=[3, 4], after="cursor-b"),
        api.CursorPage(items=[5], after=None),
    ]

    async def fetch(params: dict[str, object]) -> Any:
        calls.append(dict(params))
        return pages[len(calls) - 1]

    result = await collect(fetch, filters={"states": "live"})

    assert result.items == (1, 2, 3, 4, 5)
    assert result.page_count == 3
    assert result.complete is True
    assert result.truncated is False
    assert result.terminal_after is None
    assert calls == [
        {"states": "live", "page_size": 2},
        {"states": "live", "page_size": 2, "after": "cursor-a"},
        {"states": "live", "page_size": 2, "after": "cursor-b"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_empty_first_page_is_complete_for_null_or_missing_after(missing: bool) -> None:
    api = paginator_api()
    page = api.CursorPage(items=[]) if missing else api.CursorPage(items=[], after=None)

    async def fetch(params: dict[str, object]) -> Any:
        return page

    result = await collect(fetch)
    assert result.items == ()
    assert result.page_count == 1
    assert result.complete is True
    assert result.truncated is False
    if missing:
        assert result.terminal_after is api.MISSING_AFTER
    else:
        assert result.terminal_after is None


@pytest.mark.asyncio
async def test_filters_and_callback_mutation_are_isolated_between_pages() -> None:
    api = paginator_api()
    original = {"states": "live", "expiry": 1_800_000_000}
    seen: list[dict[str, object]] = []

    async def fetch(params: dict[str, object]) -> Any:
        seen.append(dict(params))
        params.clear()
        if len(seen) == 1:
            original["states"] = "settled"
            original["new"] = "caller-mutation"
            return api.CursorPage(items=[1], after="next")
        return api.CursorPage(items=[2], after=None)

    result = await collect(fetch, filters=original, page_size=1)
    assert result.items == (1, 2)
    assert seen == [
        {"states": "live", "expiry": 1_800_000_000, "page_size": 1},
        {
            "states": "live",
            "expiry": 1_800_000_000,
            "page_size": 1,
            "after": "next",
        },
    ]
    assert original == {
        "states": "settled",
        "expiry": 1_800_000_000,
        "new": "caller-mutation",
    }


@pytest.mark.parametrize("name", ["after", "before", "page_size", "AFTER"])
def test_reserved_filter_keys_are_rejected(name: str) -> None:
    async def fetch(params: dict[str, object]) -> Any:
        raise AssertionError("transport must not run")

    with pytest.raises(ConfigurationError) as raised:
        asyncio.run(collect(fetch, filters={name: "fixture-secret-cursor"}))
    assert "fixture-secret-cursor" not in str(raised.value)
    assert "fixture-secret-cursor" not in repr(raised.value)


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("page_size", 0),
        ("page_size", -1),
        ("page_size", True),
        ("page_size", 101),
        ("page_size_cap", 0),
        ("page_size_cap", False),
        ("max_pages", 0),
        ("max_pages", True),
        ("max_items", 0),
        ("max_items", False),
    ],
)
@pytest.mark.asyncio
async def test_strict_positive_limits_fail_before_fetch(argument: str, value: object) -> None:
    called = False

    async def fetch(params: dict[str, object]) -> Any:
        nonlocal called
        called = True
        raise AssertionError("transport must not run")

    kwargs = {argument: value}
    with pytest.raises(ConfigurationError):
        await collect(fetch, **kwargs)
    assert called is False


@pytest.mark.asyncio
async def test_page_size_equal_to_endpoint_cap_is_allowed() -> None:
    api = paginator_api()
    calls: list[dict[str, object]] = []

    async def fetch(params: dict[str, object]) -> Any:
        calls.append(dict(params))
        return api.CursorPage(items=range(3), after=None)

    result = await collect(fetch, page_size=3, page_size_cap=3)
    assert result.items == (0, 1, 2)
    assert calls == [{"page_size": 3}]


@pytest.mark.asyncio
async def test_full_load_fails_at_max_pages_without_overfetch() -> None:
    api = paginator_api()
    calls = 0

    async def fetch(params: dict[str, object]) -> Any:
        nonlocal calls
        calls += 1
        return api.CursorPage(items=[calls], after=f"cursor-{calls}")

    with pytest.raises(api.PaginationLimitError) as raised:
        await collect(fetch, page_size=1, max_pages=2)
    assert calls == 2
    assert "cursor-2" not in str(raised.value)
    assert "cursor-2" not in json.dumps(raised.value.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_full_load_fails_when_page_exceeds_remaining_item_budget(terminal: bool) -> None:
    api = paginator_api()
    calls = 0

    async def fetch(params: dict[str, object]) -> Any:
        nonlocal calls
        calls += 1
        return api.CursorPage(items=[1, 2], after=None if terminal else "more")

    with pytest.raises(api.PaginationLimitError):
        await collect(fetch, page_size=2, max_items=1)
    assert calls == 1


@pytest.mark.asyncio
async def test_exact_item_limit_with_more_cursor_fails_without_second_fetch() -> None:
    api = paginator_api()
    calls = 0

    async def fetch(params: dict[str, object]) -> Any:
        nonlocal calls
        calls += 1
        return api.CursorPage(items=[1, 2], after="more")

    with pytest.raises(api.PaginationLimitError):
        await collect(fetch, page_size=2, max_items=2)
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("items", "max_items"), [([1, 2], 1), ([1, 2], 2)])
async def test_explicit_item_truncation_is_visible_and_never_overfetches(
    items: list[int], max_items: int
) -> None:
    api = paginator_api()
    calls = 0

    async def fetch(params: dict[str, object]) -> Any:
        nonlocal calls
        calls += 1
        return api.CursorPage(items=items, after="remaining-cursor")

    result = await collect(
        fetch,
        page_size=2,
        max_items=max_items,
        truncate=True,
    )
    assert result.items == tuple(items[:max_items])
    assert result.page_count == 1
    assert result.complete is False
    assert result.truncated is True
    assert result.terminal_after == "remaining-cursor"
    assert calls == 1


@pytest.mark.asyncio
async def test_terminal_page_overflow_can_be_explicitly_truncated() -> None:
    api = paginator_api()

    async def fetch(params: dict[str, object]) -> Any:
        return api.CursorPage(items=[1, 2], after=None)

    result = await collect(fetch, page_size=2, max_items=1, truncate=True)
    assert result.items == (1,)
    assert result.complete is False
    assert result.truncated is True
    assert result.terminal_after is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pages",
    [
        [([1], "same"), ([2], "same")],
        [([1], "a"), ([2], "b"), ([3], "a")],
    ],
)
async def test_repeated_and_multi_cursor_cycles_fail(pages: list[tuple[list[int], str]]) -> None:
    api = paginator_api()
    calls = 0

    async def fetch(params: dict[str, object]) -> Any:
        nonlocal calls
        items, after = pages[calls]
        calls += 1
        return api.CursorPage(items=items, after=after)

    with pytest.raises(ExchangeProtocolError):
        await collect(fetch, page_size=1)
    assert calls == len(pages)


@pytest.mark.asyncio
async def test_resume_cursor_is_sent_and_participates_in_cycle_detection() -> None:
    api = paginator_api()
    calls: list[dict[str, object]] = []

    async def fetch(params: dict[str, object]) -> Any:
        calls.append(dict(params))
        return api.CursorPage(items=[1], after="resume-point")

    with pytest.raises(ExchangeProtocolError):
        await collect(fetch, page_size=1, initial_after="resume-point")
    assert calls == [{"page_size": 1, "after": "resume-point"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page_factory",
    [
        lambda api: api.CursorPage(items=[1]),
        lambda api: api.CursorPage(items=[], after="next"),
        lambda api: api.CursorPage(items=[1, 2, 3], after=None),
        lambda api: object(),
    ],
)
async def test_malformed_or_contradictory_pages_fail_closed(page_factory: Any) -> None:
    api = paginator_api()

    async def fetch(params: dict[str, object]) -> Any:
        return page_factory(api)

    with pytest.raises(ExchangeProtocolError):
        await collect(fetch, page_size=2)


@pytest.mark.asyncio
async def test_tampered_page_structure_is_revalidated_at_callback_boundary() -> None:
    api = paginator_api()
    page = api.CursorPage(items=[1], after=None)
    object.__setattr__(page, "items", "raw-payload")

    async def fetch(params: dict[str, object]) -> Any:
        return page

    with pytest.raises(ExchangeProtocolError):
        await collect(fetch, page_size=20)


@pytest.mark.asyncio
async def test_typed_exchange_error_propagates_by_identity() -> None:
    error = AuthError(context={"operation": "products"})

    async def fetch(params: dict[str, object]) -> Any:
        raise error

    with pytest.raises(AuthError) as raised:
        await collect(fetch)
    assert raised.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_on_page", [1, 2])
async def test_cancellation_propagates_and_prevents_later_fetches(cancel_on_page: int) -> None:
    api = paginator_api()
    entered = asyncio.Event()
    calls = 0

    async def fetch(params: dict[str, object]) -> Any:
        nonlocal calls
        calls += 1
        if calls == cancel_on_page:
            entered.set()
            await asyncio.Future()
        return api.CursorPage(items=[calls], after="next")

    task = asyncio.create_task(collect(fetch, page_size=1))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == cancel_on_page


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [1, 2, 5, 10])
async def test_generated_acyclic_chains_terminate_in_order(length: int) -> None:
    api = paginator_api()
    calls = 0

    async def fetch(params: dict[str, object]) -> Any:
        nonlocal calls
        calls += 1
        after = None if calls == length else f"cursor-{calls}"
        return api.CursorPage(items=[calls], after=after)

    result = await collect(fetch, page_size=1, max_pages=length, max_items=length)
    assert result.items == tuple(range(1, length + 1))
    assert result.page_count == length
    assert result.complete is True
    assert calls == length


@pytest.mark.asyncio
async def test_errors_and_serialization_do_not_include_cursor_or_filter_values() -> None:
    api = paginator_api()
    secret_cursor = "fixture-secret-cursor"
    secret_filter = "fixture-secret-filter"

    async def fetch(params: dict[str, object]) -> Any:
        return api.CursorPage(items=[1], after=secret_cursor)

    with pytest.raises(api.PaginationLimitError) as raised:
        await collect(
            fetch,
            filters={"states": secret_filter},
            page_size=1,
            max_pages=1,
        )
    surfaces = (str(raised.value), repr(raised.value), json.dumps(raised.value.to_dict()))
    assert all(secret_cursor not in surface for surface in surfaces)
    assert all(secret_filter not in surface for surface in surfaces)
