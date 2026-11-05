"""Fail-closed actor regressions for real transport and hostile cleanup behavior."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from public_stream_failure_support import (
    CATALOGUE_ID,
    GENERATION,
    ManualTime,
    ScriptedControl,
    ScriptedReceiver,
    ack,
    generation,
    harness,
    settle,
    ticker_frame,
)

from hmt_crypto.errors import ExchangeDownError
from hmt_crypto.exchange.base import MarketComponent
from hmt_crypto.exchange.delta.websocket_contracts import WsChannel
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    SubscriptionLease,
    SubscriptionRegistry,
    WsSubscription,
)
from hmt_crypto.market_data.public_stream import PublicGenerationResult, PublicGenerationStatus
from hmt_crypto.market_data.readiness import (
    FreshnessRequirement,
    MarketReadinessEvidence,
    MarketRequirements,
)
from hmt_crypto.market_data.state import MarketStateStore


def _generation_closed(state: MarketStateStore) -> bool:
    view = state.snapshot(
        MarketRequirements(
            product_ids=(1101,),
            components=(FreshnessRequirement(component=MarketComponent.TICKER),),
        ),
        evidence=MarketReadinessEvidence(
            connection_generation=GENERATION,
            catalogue_snapshot_id=CATALOGUE_ID,
            catalogue_fresh=True,
            supervisor_barrier_current=True,
        ),
    )
    return not view.usable and "generation_invalidated" in view.reasons


async def _start_live(
    time: ManualTime,
    receiver: ScriptedReceiver,
    control: ScriptedControl,
) -> tuple[
    asyncio.Task[PublicGenerationResult],
    SubscriptionRegistry,
    SubscriptionLease,
    MarketStateStore,
]:
    actor, registry, state = harness(time)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    task = asyncio.create_task(
        actor.run_generation(generation(), receiver=receiver, control=control)
    )
    await settle()
    assert len(control.receipts) == 1
    return task, registry, lease, state


@pytest.mark.asyncio
async def test_production_receive_exchange_down_returns_typed_fenced_result() -> None:
    time = ManualTime()
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    receiver = ScriptedReceiver(
        ack(ticker),
        ticker_frame(),
        ExchangeDownError(context={"operation": "websocket_receive"}),
    )
    control = ScriptedControl()
    task, _, lease, state = await _start_live(time, receiver, control)
    result = await task
    await lease.aclose()

    assert result.status is PublicGenerationStatus.RECEIVER_ENDED
    assert control.invalidations
    assert _generation_closed(state)


@pytest.mark.asyncio
async def test_production_send_exchange_down_returns_typed_fenced_result() -> None:
    time = ManualTime()
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    receiver = ScriptedReceiver(ack(ticker), ticker_frame())
    control = ScriptedControl(fail_send_at=2)
    task, registry, lease, state = await _start_live(time, receiver, control)
    second = WsSubscription.for_channel(WsChannel.TICKER, symbols=("ETHUSD_CAT",))
    second_lease = await registry.acquire(owner_id=UUID(int=2), entries=(second,))
    time.advance(generation().registry_poll_ns)
    await settle()
    result = await task
    await lease.aclose()
    await second_lease.aclose()

    assert result.status is PublicGenerationStatus.RECEIVER_ENDED
    assert control.invalidations
    assert _generation_closed(state)


@pytest.mark.asyncio
async def test_cancellation_resistant_send_cannot_extend_control_deadline() -> None:
    time = ManualTime()
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    receiver = ScriptedReceiver(ack(ticker), ticker_frame())
    control = ScriptedControl(resist_send_at=2)
    task, registry, lease, state = await _start_live(time, receiver, control)
    second = WsSubscription.for_channel(WsChannel.TICKER, symbols=("ETHUSD_CAT",))
    second_lease = await registry.acquire(owner_id=UUID(int=2), entries=(second,))
    time.advance(generation().registry_poll_ns)
    await settle()
    deadline = time.now + generation().control_timeout_ns
    time.advance(generation().control_timeout_ns)
    await settle()
    result = task.result() if task.done() else None
    control.release_send.set()
    await settle()
    if not task.done():
        await task
    await lease.aclose()
    await second_lease.aclose()

    assert result is not None and result.status is PublicGenerationStatus.CONTROL_TIMEOUT
    assert time.now == deadline
    assert control.invalidations
    assert control.active_sends == 0
    assert _generation_closed(state)


@pytest.mark.asyncio
async def test_resistant_receive_cleanup_stops_at_exact_finalization_deadline() -> None:
    time = ManualTime()
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    receiver = ScriptedReceiver(ack(ticker), ticker_frame(), resist_after=3)
    control = ScriptedControl(fail_send_at=2)
    task, registry, lease, state = await _start_live(time, receiver, control)
    second = WsSubscription.for_channel(WsChannel.TICKER, symbols=("ETHUSD_CAT",))
    second_lease = await registry.acquire(owner_id=UUID(int=2), entries=(second,))
    time.advance(generation().registry_poll_ns)
    await settle()
    cleanup_deadline = time.now + generation().finalization_timeout_ns
    time.advance(generation().finalization_timeout_ns - 1)
    await settle()
    done_before_deadline = task.done()
    time.advance(1)
    await settle()
    done_at_deadline = task.done()
    receiver.release.set()
    await settle()
    failure: BaseException | None = None
    result: PublicGenerationResult | None = None
    try:
        result = await task
    except BaseException as error:
        failure = error
    await lease.aclose()
    await second_lease.aclose()

    assert not done_before_deadline
    assert done_at_deadline
    assert failure is None
    assert result is not None and result.status is PublicGenerationStatus.RECEIVER_ENDED
    assert time.now == cleanup_deadline
    assert receiver.cancellations >= 1
    assert receiver.active == 0
    assert control.invalidations
    assert _generation_closed(state)
