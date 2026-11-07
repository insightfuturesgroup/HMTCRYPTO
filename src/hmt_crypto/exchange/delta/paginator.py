"""Fail-closed, transport-neutral cursor pagination for Delta REST results."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar, cast

from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError, PaginationLimitError

T = TypeVar("T")
QueryScalar = str | int

MAX_CURSOR_LENGTH = 4096
_MAX_FILTER_NAME_LENGTH = 64
_MAX_FILTER_VALUE_LENGTH = 4096
_MAX_FILTER_INTEGER = (1 << 63) - 1
_RESERVED_FILTERS = frozenset({"after", "before", "page_size"})


class MissingAfter(Enum):
    """Sentinel distinguishing an absent ``meta.after`` from JSON null."""

    MISSING = "missing"


MISSING_AFTER = MissingAfter.MISSING
CursorAfter = str | None | MissingAfter


def _config_error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "cursor_pagination"})


def _protocol_error() -> ExchangeProtocolError:
    return ExchangeProtocolError(context={"operation": "cursor_pagination"})


def _limit_error() -> PaginationLimitError:
    return PaginationLimitError(context={"operation": "cursor_pagination"})


def _is_safe_text(value: str, *, maximum_length: int) -> bool:
    return 0 < len(value) <= maximum_length and all(
        character.isprintable() and not character.isspace() for character in value
    )


def _validate_cursor(value: object, *, allow_missing: bool) -> CursorAfter:
    if value is MISSING_AFTER:
        if allow_missing:
            return MISSING_AFTER
        raise ValueError("initial cursor cannot be missing")
    if value is None:
        return None
    if not isinstance(value, str) or not _is_safe_text(value, maximum_length=MAX_CURSOR_LENGTH):
        raise ValueError("cursor must be a bounded nonempty printable string")
    return value


def _copy_items(items: object) -> tuple[object, ...]:
    if isinstance(items, (str, bytes, bytearray, Mapping)) or not isinstance(items, Sequence):
        raise TypeError("page items must be a sequence")
    return tuple(items)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CursorPage(Generic[T]):
    """One normalized page with explicit cursor-presence evidence."""

    items: tuple[T, ...]
    after: CursorAfter

    def __init__(
        self,
        *,
        items: Sequence[T],
        after: object = MISSING_AFTER,
    ) -> None:
        copied_items = cast(tuple[T, ...], _copy_items(items))
        cursor = _validate_cursor(after, allow_missing=True)
        object.__setattr__(self, "items", copied_items)
        object.__setattr__(self, "after", cursor)

    def __repr__(self) -> str:
        after_state = "missing" if self.after is MISSING_AFTER else "null"
        if isinstance(self.after, str):
            after_state = "present"
        return f"CursorPage(item_count={len(self.items)}, after={after_state!r})"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PaginationResult(Generic[T]):
    """Immutable traversal result that cannot hide partial completion."""

    items: tuple[T, ...]
    page_count: int
    complete: bool
    truncated: bool
    terminal_after: CursorAfter

    def __init__(
        self,
        *,
        items: Sequence[T],
        page_count: int,
        complete: bool,
        truncated: bool,
        terminal_after: object,
    ) -> None:
        copied_items = cast(tuple[T, ...], _copy_items(items))
        if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count <= 0:
            raise ValueError("page_count must be a positive integer")
        if not isinstance(complete, bool) or not isinstance(truncated, bool):
            raise TypeError("completion flags must be booleans")
        if complete == truncated:
            raise ValueError("exactly one completion state must be true")
        cursor = _validate_cursor(terminal_after, allow_missing=True)
        if complete and isinstance(cursor, str):
            raise ValueError("a complete result cannot retain a forward cursor")
        object.__setattr__(self, "items", copied_items)
        object.__setattr__(self, "page_count", page_count)
        object.__setattr__(self, "complete", complete)
        object.__setattr__(self, "truncated", truncated)
        object.__setattr__(self, "terminal_after", cursor)

    def __repr__(self) -> str:
        cursor_state = "missing" if self.terminal_after is MISSING_AFTER else "null"
        if isinstance(self.terminal_after, str):
            cursor_state = "present"
        return (
            "PaginationResult("
            f"item_count={len(self.items)}, page_count={self.page_count}, "
            f"complete={self.complete}, truncated={self.truncated}, "
            f"terminal_after={cursor_state!r})"
        )

    __str__ = __repr__


FetchPage = Callable[[dict[str, QueryScalar]], Awaitable[CursorPage[T]]]


def _positive_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _config_error()
    return value


def _snapshot_filters(filters: Mapping[str, object]) -> dict[str, QueryScalar]:
    if not isinstance(filters, Mapping):
        raise _config_error()
    snapshot: dict[str, QueryScalar] = {}
    for name, value in filters.items():
        if (
            not isinstance(name, str)
            or not name.isascii()
            or not name.replace("_", "a").isalnum()
            or not name[0].isalpha()
            or len(name) > _MAX_FILTER_NAME_LENGTH
            or name.casefold() in _RESERVED_FILTERS
        ):
            raise _config_error()
        if isinstance(value, bool):
            raise _config_error()
        if isinstance(value, int):
            if abs(value) > _MAX_FILTER_INTEGER:
                raise _config_error()
            snapshot[name] = value
            continue
        if isinstance(value, str) and _is_safe_text(value, maximum_length=_MAX_FILTER_VALUE_LENGTH):
            snapshot[name] = value
            continue
        raise _config_error()
    return snapshot


def _checked_page(page: CursorPage[T], *, page_size: int) -> tuple[tuple[T, ...], CursorAfter]:
    if not isinstance(page, CursorPage):
        raise _protocol_error()
    try:
        items = cast(tuple[T, ...], _copy_items(page.items))
        cursor = _validate_cursor(page.after, allow_missing=True)
    except (TypeError, ValueError):
        raise _protocol_error() from None
    if len(items) > page_size:
        raise _protocol_error()
    if cursor is MISSING_AFTER and items:
        raise _protocol_error()
    if isinstance(cursor, str) and not items:
        raise _protocol_error()
    return items, cursor


async def collect_cursor_pages(
    fetch_page: FetchPage[T],
    *,
    filters: Mapping[str, object],
    page_size: int,
    page_size_cap: int,
    max_pages: int,
    max_items: int,
    initial_after: str | None = None,
    truncate_at_max_items: bool = False,
) -> PaginationResult[T]:
    """Collect a bounded cursor traversal without ever returning a partial full load.

    The callback is invoked serially with a fresh parameter dictionary. Exchange errors
    and task cancellation propagate unchanged. ``truncate_at_max_items`` is an explicit
    diagnostic mode whose result always carries ``complete=False``.
    """
    if not callable(fetch_page):
        raise _config_error()
    selected_page_size = _positive_integer(page_size)
    selected_cap = _positive_integer(page_size_cap)
    selected_max_pages = _positive_integer(max_pages)
    selected_max_items = _positive_integer(max_items)
    if selected_page_size > selected_cap:
        raise _config_error()
    if not isinstance(truncate_at_max_items, bool):
        raise _config_error()
    try:
        current_after = _validate_cursor(initial_after, allow_missing=False)
    except ValueError:
        raise _config_error() from None

    base_filters = _snapshot_filters(filters)
    seen_cursors: set[str] = set()
    if isinstance(current_after, str):
        seen_cursors.add(current_after)

    accepted_items: list[T] = []
    page_count = 0
    while True:
        params = dict(base_filters)
        params["page_size"] = selected_page_size
        if isinstance(current_after, str):
            params["after"] = current_after

        page = await fetch_page(params)
        page_count += 1
        page_items, next_after = _checked_page(page, page_size=selected_page_size)

        if isinstance(next_after, str):
            if next_after in seen_cursors:
                raise _protocol_error()
            seen_cursors.add(next_after)

        remaining = selected_max_items - len(accepted_items)
        if len(page_items) > remaining:
            if not truncate_at_max_items:
                raise _limit_error()
            accepted_items.extend(page_items[:remaining])
            return PaginationResult(
                items=accepted_items,
                page_count=page_count,
                complete=False,
                truncated=True,
                terminal_after=next_after,
            )

        accepted_items.extend(page_items)
        if next_after is None or next_after is MISSING_AFTER:
            return PaginationResult(
                items=accepted_items,
                page_count=page_count,
                complete=True,
                truncated=False,
                terminal_after=next_after,
            )

        if len(accepted_items) == selected_max_items:
            if truncate_at_max_items:
                return PaginationResult(
                    items=accepted_items,
                    page_count=page_count,
                    complete=False,
                    truncated=True,
                    terminal_after=next_after,
                )
            raise _limit_error()
        if page_count == selected_max_pages:
            raise _limit_error()
        current_after = next_after


__all__ = [
    "CursorPage",
    "MISSING_AFTER",
    "MAX_CURSOR_LENGTH",
    "MissingAfter",
    "PaginationResult",
    "collect_cursor_pages",
]
