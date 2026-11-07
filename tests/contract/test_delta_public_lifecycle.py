"""Owned transport close coordinates admitted calls and concurrent closers."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest
from delta_public_test_support import fixture_bytes, public_api

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints
from hmt_crypto.exchange.delta.rate_limiter import (
    RateLimitScope,
    RateRequest,
    ScopeKind,
    WeightedRateLimiter,
)

pytestmark = pytest.mark.contract


def _scope() -> RateLimitScope:
    return RateLimitScope(
        region="india",
        environment="testnet",
        kind=ScopeKind.IP,
        external_id="lifecycle-test-egress",
    )


def _client(
    transport: httpx.AsyncBaseTransport,
    *,
    limiter: WeightedRateLimiter | None = None,
) -> object:
    api = public_api().client
    return api.DeltaPublicClient(
        endpoints=get_delta_endpoints("india", "testnet"),
        limiter=limiter or WeightedRateLimiter(_scope()),
        user_agent="hmt-crypto-lifecycle-tests/1.0",
        transport=transport,
        retry_policy=api.RetryPolicy(
            max_attempts=1,
            base_delay_seconds=Decimal("0.001"),
            max_delay_seconds=Decimal("0.001"),
            jitter_ratio=Decimal("0"),
        ),
        jitter_source=lambda: Decimal("0"),
    )


async def _ticker_call(client: object) -> object:
    return await client.get_ticker(public_api().base.ProductRef(symbol="XBTUSD_TEST"))


class _GateLimiter(WeightedRateLimiter):
    def __init__(self) -> None:
        super().__init__(_scope())
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def acquire(self, request: RateRequest):
        self.entered.set()
        await self.release.wait()
        return await super().acquire(request)


class _GateStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        split = len(self.body) // 2
        yield self.body[:split]
        self.entered.set()
        await self.release.wait()
        yield self.body[split:]

    async def aclose(self) -> None:
        self.closed = True


class _CloseGateTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.close_calls = 0
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=fixture_bytes("public_ticker.json"),
            headers={"Content-Type": "application/json"},
            request=request,
        )

    async def aclose(self) -> None:
        self.close_calls += 1
        self.close_entered.set()
        await self.close_release.wait()
        self.closed = True


class _BlockingCountingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.close_calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.entered.set()
        await asyncio.Future()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_close_waits_for_call_admitted_before_blocked_limiter_dispatch() -> None:
    limiter = _GateLimiter()
    client = _client(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=fixture_bytes("public_ticker.json"),
                headers={"Content-Type": "application/json"},
                request=request,
            )
        ),
        limiter=limiter,
    )
    call = asyncio.create_task(_ticker_call(client))
    await asyncio.wait_for(limiter.entered.wait(), timeout=1)
    closing = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    close_waited = not closing.done()
    try:
        with pytest.raises(ConfigurationError):
            await asyncio.wait_for(_ticker_call(client), timeout=0.1)
    finally:
        limiter.release.set()
    outcome = (await asyncio.gather(call, return_exceptions=True))[0]
    await asyncio.wait_for(closing, timeout=1)
    assert close_waited is True
    assert not isinstance(outcome, BaseException)
    assert outcome.product_id == 101


@pytest.mark.asyncio
async def test_close_waits_for_call_blocked_inside_handler() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(
            200,
            content=fixture_bytes("public_ticker.json"),
            headers={"Content-Type": "application/json"},
            request=request,
        )

    client = _client(httpx.MockTransport(handler))
    call = asyncio.create_task(_ticker_call(client))
    await asyncio.wait_for(entered.wait(), timeout=1)
    closing = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    close_waited = not closing.done()
    try:
        with pytest.raises(ConfigurationError):
            await asyncio.wait_for(_ticker_call(client), timeout=0.1)
    finally:
        release.set()
    outcome = (await asyncio.gather(call, return_exceptions=True))[0]
    await asyncio.wait_for(closing, timeout=1)
    assert close_waited is True
    assert not isinstance(outcome, BaseException)
    assert outcome.product_id == 101


@pytest.mark.asyncio
async def test_close_waits_for_call_blocked_in_response_stream() -> None:
    stream = _GateStream(fixture_bytes("public_ticker.json"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=stream,
            request=request,
        )

    client = _client(httpx.MockTransport(handler))
    call = asyncio.create_task(_ticker_call(client))
    await asyncio.wait_for(stream.entered.wait(), timeout=1)
    closing = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    close_waited = not closing.done()
    try:
        with pytest.raises(ConfigurationError):
            await asyncio.wait_for(_ticker_call(client), timeout=0.1)
    finally:
        stream.release.set()
    outcome = (await asyncio.gather(call, return_exceptions=True))[0]
    await asyncio.wait_for(closing, timeout=1)
    assert close_waited is True
    assert not isinstance(outcome, BaseException)
    assert outcome.product_id == 101
    assert stream.closed is True


@pytest.mark.asyncio
async def test_concurrent_and_cancelled_closers_share_one_unstranded_close() -> None:
    transport = _CloseGateTransport()
    client = _client(transport)
    first = asyncio.create_task(client.aclose())
    await asyncio.wait_for(transport.close_entered.wait(), timeout=1)
    second = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    second_waited = not second.done()

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    third = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    third_waited = not third.done()

    transport.close_release.set()
    await asyncio.wait_for(asyncio.gather(second, third), timeout=1)
    await client.aclose()
    assert second_waited is True
    assert third_waited is True
    assert transport.close_calls == 1
    assert transport.closed is True
    with pytest.raises(ConfigurationError):
        await _ticker_call(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancelled_call_cannot_strand_its_lifecycle_lease(cancel_count: int) -> None:
    transport = _BlockingCountingTransport()
    client = _client(transport)
    lifecycle = client._transport._lifecycle
    call = asyncio.create_task(_ticker_call(client))
    await asyncio.wait_for(transport.entered.wait(), timeout=1)

    # Round-1's condition-based release can be suspended here. A synchronous release has no
    # suspension point, so this compatibility gate deliberately disappears after the fix.
    release_condition = getattr(lifecycle, "_condition", None)
    if release_condition is not None:
        await release_condition.acquire()
    call.cancel()
    await asyncio.sleep(0)
    if cancel_count == 2:
        call.cancel()
    if release_condition is not None and release_condition.locked():
        release_condition.release()
    with pytest.raises(asyncio.CancelledError):
        await call

    close_timed_out = False
    try:
        await asyncio.wait_for(client.aclose(), timeout=0.05)
    except TimeoutError:
        close_timed_out = True
        # RED cleanup for the old stranded counter so pytest does not retain its close task.
        assert release_condition is not None
        async with release_condition:
            lifecycle._active_calls = 0
            release_condition.notify_all()
        await asyncio.wait_for(client.aclose(), timeout=1)

    close_task = lifecycle._close_task
    assert close_timed_out is False
    assert call.done() is True
    assert close_task is not None and close_task.done()
    assert transport.close_calls == 1
