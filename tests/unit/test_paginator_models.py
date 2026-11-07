"""Immutable cursor page and pagination result evidence models."""

from __future__ import annotations

import pytest
from paginator_test_support import paginator_api

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "after",
    ["", " ", "bad\nvalue", "bad\x00value", True, 1, [], {}],
)
def test_cursor_page_rejects_invalid_cursor_types_or_contents(after: object) -> None:
    api = paginator_api()
    with pytest.raises((TypeError, ValueError)):
        api.CursorPage(items=[1], after=after)


def test_cursor_page_rejects_oversized_cursor_without_echoing_it() -> None:
    api = paginator_api()
    raw = "x" * 4097
    with pytest.raises(ValueError) as raised:
        api.CursorPage(items=[1], after=raw)
    assert raw not in str(raised.value)


def test_page_and_result_copy_mutable_items_and_are_frozen() -> None:
    api = paginator_api()
    source = [1, 2]
    page = api.CursorPage(items=source, after=None)
    source.append(3)
    assert page.items == (1, 2)
    with pytest.raises((AttributeError, TypeError)):
        page.items = (9,)

    result = api.PaginationResult(
        items=source,
        page_count=1,
        complete=True,
        truncated=False,
        terminal_after=None,
    )
    source.append(4)
    assert result.items == (1, 2, 3)
    with pytest.raises((AttributeError, TypeError)):
        result.complete = False


def test_page_and_result_representations_do_not_expose_items_or_cursors() -> None:
    api = paginator_api()
    secret_item = "fixture-secret-item"
    secret_cursor = "fixture-secret-cursor"
    page = api.CursorPage(items=[secret_item], after=secret_cursor)
    result = api.PaginationResult(
        items=[secret_item],
        page_count=1,
        complete=False,
        truncated=True,
        terminal_after=secret_cursor,
    )

    for rendered in (str(page), repr(page), str(result), repr(result)):
        assert secret_item not in rendered
        assert secret_cursor not in rendered


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_count": 0, "complete": True, "truncated": False, "terminal_after": None},
        {"page_count": 1, "complete": True, "truncated": True, "terminal_after": None},
        {"page_count": 1, "complete": False, "truncated": False, "terminal_after": "x"},
        {"page_count": 1, "complete": True, "truncated": False, "terminal_after": "x"},
    ],
)
def test_result_rejects_contradictory_direct_construction(kwargs: dict[str, object]) -> None:
    api = paginator_api()
    with pytest.raises((TypeError, ValueError)):
        api.PaginationResult(items=[], **kwargs)
