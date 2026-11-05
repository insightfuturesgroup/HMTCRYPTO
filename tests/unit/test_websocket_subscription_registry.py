"""Concurrent owner-refcount behavior for the in-memory desired subscription set."""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Any
from uuid import UUID

import pytest

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.websocket_contracts import WsPlane


def api() -> Any:
    try:
        return import_module("hmt_crypto.exchange.delta.websocket_subscriptions")
    except ModuleNotFoundError:
        pytest.fail("WebSocket subscription registry is missing", pytrace=False)


def owner(value: int) -> UUID:
    return UUID(int=value)


@pytest.mark.asyncio
async def test_two_owner_refcounts_change_revision_only_when_desired_set_changes() -> None:
    module = api()
    registry = module.SubscriptionRegistry(plane=WsPlane.PUBLIC)
    entry = module.WsSubscription("ticker", ("BTCUSD",))
    initial = await registry.snapshot()
    first = await registry.acquire(owner_id=owner(1), entries=(entry,))
    after_first = await registry.snapshot()
    second = await registry.acquire(owner_id=owner(2), entries=(entry,))
    after_second = await registry.snapshot()

    assert initial.revision == 0 and initial.entries == ()
    assert after_first.revision == 1 and after_first.entries == (entry,)
    assert after_second == after_first
    await first.aclose()
    assert await registry.snapshot() == after_second
    await first.aclose()
    await second.aclose()
    final = await registry.snapshot()
    assert final.revision == 2 and final.entries == ()


@pytest.mark.asyncio
async def test_parallel_acquire_release_has_deterministic_union_and_empty_end_state() -> None:
    module = api()
    registry = module.SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = module.WsSubscription("ticker", ("BTCUSD",))
    funding = module.WsSubscription("funding_rate", ("ETHUSD",))

    first, second = await asyncio.gather(
        registry.acquire(owner_id=owner(10), entries=(ticker,)),
        registry.acquire(owner_id=owner(11), entries=(funding,)),
    )
    snapshot = await registry.snapshot()
    assert snapshot.entries == tuple(sorted((funding, ticker)))
    assert snapshot.revision == 2
    assert len(snapshot.evidence_sha256) == 64

    await asyncio.gather(first.aclose(), second.aclose())
    final = await registry.snapshot()
    assert final.entries == ()
    assert final.revision == 4


@pytest.mark.asyncio
async def test_snapshot_is_stable_for_reconnect_until_desired_state_changes() -> None:
    module = api()
    registry = module.SubscriptionRegistry(plane=WsPlane.PRIVATE)
    lease = await registry.acquire(
        owner_id=owner(20),
        entries=(module.WsSubscription("margins", ()),),
    )
    first = await registry.snapshot()
    second = await registry.snapshot()
    assert first == second
    assert first is not second
    await lease.aclose()


@pytest.mark.asyncio
async def test_owner_reuse_duplicate_channels_and_wrong_plane_fail_without_mutation() -> None:
    module = api()
    registry = module.SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = module.WsSubscription("ticker", ("BTCUSD",))
    lease = await registry.acquire(owner_id=owner(30), entries=(ticker,))
    before = await registry.snapshot()

    with pytest.raises(ConfigurationError):
        await registry.acquire(owner_id=owner(30), entries=(ticker,))
    with pytest.raises(ConfigurationError):
        await registry.acquire(
            owner_id=owner(31),
            entries=(
                module.WsSubscription("ticker", ("BTCUSD",)),
                module.WsSubscription("ticker", ("ETHUSD",)),
            ),
        )
    with pytest.raises(ConfigurationError):
        await registry.acquire(
            owner_id=owner(32),
            entries=(module.WsSubscription("orders", ("BTCUSD",)),),
        )
    assert await registry.snapshot() == before
    await lease.aclose()


@pytest.mark.asyncio
async def test_cross_owner_symbol_union_cap_is_enforced_atomically() -> None:
    module = api()
    registry = module.SubscriptionRegistry(plane=WsPlane.PUBLIC)
    first_symbols = tuple(f"A{index:03d}" for index in range(60))
    second_symbols = tuple(f"B{index:03d}" for index in range(41))
    first = await registry.acquire(
        owner_id=owner(40),
        entries=(module.WsSubscription("ob_l1", first_symbols),),
    )
    before = await registry.snapshot()
    with pytest.raises(ConfigurationError):
        await registry.acquire(
            owner_id=owner(41),
            entries=(module.WsSubscription("ob_l1", second_symbols),),
        )
    assert await registry.snapshot() == before
    await first.aclose()


@pytest.mark.asyncio
async def test_product_updates_stays_off_without_explicit_testnet_evidence_gate() -> None:
    module = api()
    entry = module.WsSubscription("product_updates", ())
    with pytest.raises(ConfigurationError):
        await module.SubscriptionRegistry(plane=WsPlane.PUBLIC).acquire(
            owner_id=owner(50),
            entries=(entry,),
        )
    registry = module.SubscriptionRegistry(
        plane=WsPlane.PUBLIC,
        allow_testnet_assumption=True,
    )
    lease = await registry.acquire(owner_id=owner(51), entries=(entry,))
    assert (await registry.snapshot()).entries == (entry,)
    await lease.aclose()
