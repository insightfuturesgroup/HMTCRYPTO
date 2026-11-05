"""Pure subscription transitions and task cleanup for the public actor."""

from __future__ import annotations

import asyncio
from typing import Any

from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    DesiredSubscriptions,
    WsSubscription,
    desired_subscriptions,
)
from hmt_crypto.market_data.public_stream_models import (
    PublicCatalogueBinding,
    PublicCatalogueProvider,
    PublicConnectionGeneration,
    PublicGenerationResult,
    PublicGenerationStatus,
)


class ControlTimeout(Exception):
    """A local control deadline expired."""


class CatalogueMismatch(Exception):
    """A sanitized parser signal requires catalogue refresh and reconnect."""


class ReceiverEnded(Exception):
    """The receive capability ended without another frame."""


_DETACHED_TASKS: set[asyncio.Future[Any]] = set()


def _consume_task(task: asyncio.Future[Any]) -> None:
    _DETACHED_TASKS.discard(task)
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


def observe_task(task: asyncio.Future[Any] | None) -> None:
    """Consume a finished task or retain a detached task until its outcome exists."""
    if task is None:
        return
    if task.done():
        _consume_task(task)
        return
    _DETACHED_TASKS.add(task)
    task.add_done_callback(_consume_task)


def abandon_task(task: asyncio.Future[Any] | None) -> None:
    """Request cancellation without ever awaiting cancellation cooperation."""
    if task is not None and not task.done():
        task.cancel()
    observe_task(task)


def empty_desired(revision: int) -> DesiredSubscriptions:
    """Create an empty server-set model for the initial transition."""
    return desired_subscriptions(revision=revision, entries=())


def _entry_map(entries: tuple[WsSubscription, ...]) -> dict[str, WsSubscription]:
    return {entry.channel: entry for entry in entries}


def transition_changes(
    current: DesiredSubscriptions,
    target: DesiredSubscriptions,
) -> tuple[tuple[WsSubscription, ...], DesiredSubscriptions, tuple[WsSubscription, ...]]:
    """Return removals, exact intermediate set, and additions."""
    current_map = _entry_map(current.entries)
    target_map = _entry_map(target.entries)
    removed: list[WsSubscription] = []
    intermediate: list[WsSubscription] = []
    added: list[WsSubscription] = []
    for channel, old in current_map.items():
        new = target_map.get(channel)
        if new is None:
            removed.append(old)
            continue
        removed_symbols = tuple(sorted(set(old.symbols) - set(new.symbols)))
        if removed_symbols:
            removed.append(WsSubscription(channel=channel, symbols=removed_symbols))
        kept = tuple(sorted(set(old.symbols) - set(removed_symbols)))
        if kept or not old.symbols:
            intermediate.append(WsSubscription(channel=channel, symbols=kept))
    for channel, target_entry in target_map.items():
        current_entry = current_map.get(channel)
        if current_entry is None:
            added.append(target_entry)
            continue
        added_symbols = tuple(sorted(set(target_entry.symbols) - set(current_entry.symbols)))
        if added_symbols:
            added.append(WsSubscription(channel=channel, symbols=added_symbols))
    interim = desired_subscriptions(revision=target.revision, entries=tuple(intermediate))
    return tuple(sorted(removed)), interim, tuple(sorted(added))


def generation_result(
    *,
    status: PublicGenerationStatus,
    generation: PublicConnectionGeneration,
    observations_applied: int,
    desired_revision: int,
) -> PublicGenerationResult:
    """Build bounded terminal evidence from one runner's counters."""
    return PublicGenerationResult(
        status=status,
        connection_generation=generation.connection_generation,
        observations_applied=observations_applied,
        desired_revision=desired_revision,
    )


def require_binding(
    provider: PublicCatalogueProvider,
    generation: PublicConnectionGeneration,
) -> PublicCatalogueBinding:
    """Return a catalogue binding scoped to the exact generation source."""
    binding = provider.current()
    if type(binding) is not PublicCatalogueBinding or (
        binding.catalogue.source.region.value != generation.region
        or binding.catalogue.source.environment.value != generation.environment
    ):
        raise ExchangeProtocolError(
            context={"operation": "public_catalogue_binding", "scope": "public"}
        )
    return binding


def is_subscribed(message: dict[str, object], desired: DesiredSubscriptions) -> bool:
    """Require an exact accepted channel and its raw subscription symbol."""
    message_type = message.get("type")
    if type(message_type) is not str:
        return False
    entry = next(
        (candidate for candidate in desired.entries if candidate.channel == message_type),
        None,
    )
    if entry is None:
        return False
    if not entry.symbols:
        return message_type in {"product_updates", "system_status"}
    symbol_key = (
        "symbol" if message_type == "ob_updates" and message.get("action") == "error" else "sy"
    )
    symbol = message.get(symbol_key)
    return type(symbol) is str and symbol in entry.symbols
