"""Registry reconciliation remains live under continuous valid traffic."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from public_stream_failure_support import (
    ManualTime,
    ScriptedControl,
    ack,
    generation,
    harness,
    run_scripted_generation,
    settle,
)

from hmt_crypto.exchange.delta.websocket_contracts import WsChannel
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    StreamInvalidationKind,
    WsSubscription,
)
from hmt_crypto.market_data.public_stream import PublicGenerationStatus


class QueueReceiver:
    """Single-reader queue used to interleave traffic and registry mutations."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str | BaseException] = asyncio.Queue()
        self.active = 0

    async def recv(self) -> str:
        self.active += 1
        try:
            value = await self.queue.get()
        finally:
            self.active -= 1
        if isinstance(value, BaseException):
            raise value
        return value

    async def feed(self, *values: str | BaseException) -> None:
        for value in values:
            await self.queue.put(value)


@pytest.mark.parametrize("change", ("add", "remove"))
@pytest.mark.asyncio
async def test_absolute_registry_poll_survives_continuous_heartbeats(change: str) -> None:
    time = ManualTime()
    actor, registry, _state = harness(time)
    xbt = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    eth = WsSubscription.for_channel(WsChannel.TICKER, symbols=("ETHUSD_CAT",))
    combined = WsSubscription.for_channel(
        WsChannel.TICKER,
        symbols=("ETHUSD_CAT", "XBTUSD_CAT"),
    )
    xbt_lease = await registry.acquire(owner_id=UUID(int=1), entries=(xbt,))
    eth_lease = None
    initial = xbt
    if change == "remove":
        eth_lease = await registry.acquire(owner_id=UUID(int=2), entries=(eth,))
        initial = combined

    receiver = QueueReceiver()
    control = ScriptedControl()
    await receiver.feed(ack(initial))
    task = asyncio.create_task(
        run_scripted_generation(
            actor,
            generation(),
            receiver=receiver,
            control=control,
        )
    )
    await settle()
    assert control.receipts[-1].desired_revision == (1 if change == "add" else 2)

    if change == "add":
        eth_lease = await registry.acquire(owner_id=UUID(int=2), entries=(eth,))
        expected = combined
        target_revision = 2
    else:
        await xbt_lease.aclose()
        xbt_lease = None
        expected = eth
        target_revision = 3

    ack_sent = False
    for _ in range(20):
        time.advance(50_000_000)
        await receiver.feed('{"type":"heartbeat"}')
        await settle()
        if control.send_calls >= 2 and not ack_sent:
            await receiver.feed(ack(expected))
            ack_sent = True
        await settle()

    assert ack_sent
    assert control.receipts[-1].desired_revision == target_revision
    assert control.events[-3:] == ["invalidate", "send", "receipt"]
    assert any(
        item.kind is StreamInvalidationKind.SUBSCRIPTION_CHANGE for item in control.invalidations
    )

    await receiver.feed(EOFError())
    result = await task
    assert result.status is PublicGenerationStatus.RECEIVER_ENDED
    assert receiver.active == 0
    if xbt_lease is not None:
        await xbt_lease.aclose()
    assert eth_lease is not None
    await eth_lease.aclose()
