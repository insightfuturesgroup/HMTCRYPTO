"""Shared bounded cursor walker for reviewed Delta private reads."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from hmt_crypto.domain.account import (
    AccountRef,
    CollectionMode,
    PrivatePaginationProvenance,
    TruncationReason,
)
from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError, PaginationLimitError
from hmt_crypto.exchange.delta.private_read_runtime import _PrivateEnvelopeResult
from hmt_crypto.exchange.delta.private_read_wire import _FrozenPrivateReadRequestV1
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId, get_private_route_spec
from hmt_crypto.exchange.delta.rate_limit_policy import POLICY_VERSION

Q = TypeVar("Q")
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _CollectedPrivatePages(Generic[T]):
    items: tuple[T, ...]
    complete: bool
    truncation_reason: TruncationReason | None
    page_count: int
    provenance: PrivatePaginationProvenance


async def _collect_private_pages(
    initial_query: Q,
    *,
    account: AccountRef,
    route_id: PrivateRouteId,
    mode: CollectionMode,
    initial_after: str | None,
    initial_before: str | None,
    freeze: Callable[[Q], _FrozenPrivateReadRequestV1],
    get_json: Callable[[_FrozenPrivateReadRequestV1], Awaitable[_PrivateEnvelopeResult]],
    parse_page: Callable[[_PrivateEnvelopeResult, Q], tuple[tuple[T, ...], str | None, str | None]],
    next_query: Callable[[Q, str, bool], Q],
) -> _CollectedPrivatePages[T]:
    """Collect a cursor chain with exact page/item caps and cycle detection."""
    if type(mode) is not CollectionMode:
        raise ConfigurationError(context={"operation": route_id.value})
    spec = get_private_route_spec(route_id)
    if spec.max_pages is None or spec.max_page_size is None:
        raise ConfigurationError(context={"operation": route_id.value})
    current = initial_query
    backward = initial_before is not None
    initial_cursor = initial_before if backward else initial_after
    seen = {initial_cursor} if initial_cursor is not None else set()
    items: list[T] = []
    envelopes: list[_PrivateEnvelopeResult] = []
    reason: TruncationReason | None = None
    complete = False

    while True:
        envelope = await get_json(freeze(current))
        envelopes.append(envelope)
        page_items, after, before = parse_page(envelope, current)
        next_cursor = before if backward else after
        remaining = spec.max_items - len(items)
        if len(page_items) > remaining:
            if mode is CollectionMode.STRICT:
                raise PaginationLimitError(context={"operation": route_id.value})
            items.extend(page_items[:remaining])
            reason = TruncationReason.ITEM_LIMIT
            break
        items.extend(page_items)
        if next_cursor is None:
            complete = True
            break
        if next_cursor in seen:
            raise ExchangeProtocolError(context={"operation": route_id.value})
        seen.add(next_cursor)
        if len(items) == spec.max_items:
            if mode is CollectionMode.STRICT:
                raise PaginationLimitError(context={"operation": route_id.value})
            reason = TruncationReason.ITEM_LIMIT
            break
        if len(envelopes) == spec.max_pages:
            if mode is CollectionMode.STRICT:
                raise PaginationLimitError(context={"operation": route_id.value})
            reason = TruncationReason.PAGE_LIMIT
            break
        current = next_query(current, next_cursor, backward)

    first, last = envelopes[0], envelopes[-1]
    provenance = PrivatePaginationProvenance(
        account=account,
        endpoint_id=route_id,
        first_requested_at=first.requested_at,
        last_received_at=last.received_at,
        page_correlation_ids=tuple(item.correlation_id for item in envelopes),
        credential_version=account.credential_version,
        policy_version=POLICY_VERSION,
        mode=mode,
    )
    return _CollectedPrivatePages(
        items=tuple(items),
        complete=complete,
        truncation_reason=reason,
        page_count=len(envelopes),
        provenance=provenance,
    )


__all__: list[str] = []
