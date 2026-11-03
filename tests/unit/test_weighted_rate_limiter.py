"""Asynchronous weighted quota scheduling and registry ownership contracts."""

from __future__ import annotations

import asyncio

import pytest
from rate_limit_test_support import (
    FakeTime,
    limiter_api,
    new_limiter,
    request,
    scope,
    settle,
    tiny_policy,
)

from hmt_crypto.errors import ConfigurationError, RateLimitError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_rolling_rest_quota_waits_until_exact_300_second_boundary() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=1)

    first = await limiter.acquire(request())
    blocked = asyncio.create_task(limiter.acquire(request()))
    await settle()
    assert not blocked.done()

    fake.advance(299.999)
    await settle()
    assert not blocked.done()

    fake.advance(0.001)
    await settle()
    second = await blocked
    assert first.rest_weight == second.rest_weight == 1
    assert second.acquired_at == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_fifo_waiters_do_not_bypass_the_head() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=1)
    await limiter.acquire(request())
    order: list[str] = []

    async def acquire_named(name: str) -> None:
        await limiter.acquire(request())
        order.append(name)

    first = asyncio.create_task(acquire_named("first"))
    second = asyncio.create_task(acquire_named("second"))
    await settle()

    fake.advance(300)
    await settle()
    assert order == ["first"]
    assert not second.done()

    fake.advance(300)
    await settle()
    await asyncio.gather(first, second)
    assert order == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_index", [0, 1])
async def test_cancelling_queued_waiter_does_not_consume_or_block_successors(
    cancel_index: int,
) -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=1)
    await limiter.acquire(request())
    waiters = [asyncio.create_task(limiter.acquire(request())) for _ in range(2)]
    await settle()
    waiters[cancel_index].cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiters[cancel_index]
    await settle()

    fake.advance(300)
    await settle()
    survivor = waiters[1 - cancel_index]
    assert survivor.done()
    await survivor
    snapshot = await limiter.snapshot()
    assert snapshot.rest_used == 1
    assert snapshot.waiter_count == 0


@pytest.mark.asyncio
async def test_granted_permit_is_never_refunded_by_later_task_cancellation() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=1)

    granted = asyncio.create_task(limiter.acquire(request()))
    await granted
    granted.cancel()
    blocked = asyncio.create_task(limiter.acquire(request()))
    await settle()

    assert not blocked.done()
    assert (await limiter.snapshot()).rest_used == 1
    blocked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked


@pytest.mark.asyncio
async def test_registered_limiter_scope_and_policy_cannot_change_after_construction() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=1)
    registry = limiter_api().LimiterRegistry()
    registry.register_existing(limiter.scope, limiter)
    await limiter.acquire(request())

    with pytest.raises((AttributeError, TypeError)):
        limiter.scope = scope(region="global", external_id="other-user")
    with pytest.raises((AttributeError, TypeError)):
        limiter.policy = tiny_policy(capacity=2)

    blocked = asyncio.create_task(limiter.acquire(request()))
    await settle()
    assert not blocked.done()
    blocked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked


@pytest.mark.asyncio
async def test_product_and_rest_budgets_are_acquired_atomically() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=20, product_limit=500)
    await limiter.acquire(request(products={7: 500}))

    blocked_same_product = asyncio.create_task(limiter.acquire(request(products={7: 1})))
    await settle()
    snapshot = await limiter.snapshot()
    assert not blocked_same_product.done()
    assert snapshot.rest_used == 1
    assert snapshot.product_operations_used[7] == 500

    blocked_same_product.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked_same_product
    independent = await limiter.acquire(request(products={8: 1}))
    assert dict(independent.product_operations) == {8: 1}
    assert (await limiter.snapshot()).rest_used == 2

    blocked_same_product = asyncio.create_task(limiter.acquire(request(products={7: 1})))
    await settle()
    fake.advance(1)
    await settle()
    await blocked_same_product
    assert (await limiter.snapshot()).rest_used == 3


@pytest.mark.asyncio
async def test_500_product_operations_allowed_and_501st_waits() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=100, product_limit=500)
    await limiter.acquire(request(products={99: 500}))
    extra = asyncio.create_task(limiter.acquire(request(products={99: 1})))
    await settle()
    assert not extra.done()
    fake.advance(0.999)
    await settle()
    assert not extra.done()
    fake.advance(0.001)
    await settle()
    await extra


@pytest.mark.asyncio
async def test_over_capacity_request_fails_without_queuing_or_partial_consumption() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=3, product_limit=500)
    api = limiter_api()
    await limiter.acquire(api.RateRequest(route=api.RateLimitRoute.PRODUCTS))

    impossible = api.RateRequest(
        route=api.RateLimitRoute.RATE_LIMIT_QUOTA,
        product_operations={1: 501},
    )
    with pytest.raises(ConfigurationError):
        await limiter.acquire(impossible)
    assert (await limiter.snapshot()).waiter_count == 0


@pytest.mark.asyncio
async def test_close_fails_all_waiters_and_rejects_new_acquisitions() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=1)
    await limiter.acquire(request())
    blocked = asyncio.create_task(limiter.acquire(request()))
    await settle()

    await limiter.close()
    with pytest.raises(RateLimitError):
        await blocked
    with pytest.raises(RateLimitError):
        await limiter.acquire(request())
    assert (await limiter.snapshot()).closed is True


@pytest.mark.asyncio
async def test_429_block_uses_milliseconds_and_only_extends_existing_block() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=100)
    await limiter.observe_429("1500")
    await limiter.observe_429("500")
    blocked = asyncio.create_task(limiter.acquire(request()))
    await settle()
    assert not blocked.done()

    fake.advance(1.499)
    await settle()
    assert not blocked.done()
    fake.advance(0.001)
    await settle()
    await blocked


@pytest.mark.asyncio
async def test_quota_endpoint_is_validated_telemetry_only() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake)
    before = await limiter.snapshot()
    observation = limiter.observe_quota(
        {"current_quota": 12_345, "remaining_time_in_milliseconds": 2_500}
    )
    after = await limiter.snapshot()

    assert observation.current_quota == 12_345
    assert observation.remaining_time_in_milliseconds == 2_500
    assert observation.observed_at == 0.0
    assert before.rest_used == after.rest_used
    assert before.rest_remaining == after.rest_remaining
    assert before.blocked_until == after.blocked_until


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"current_quota": True, "remaining_time_in_milliseconds": 1},
        {"current_quota": -1, "remaining_time_in_milliseconds": 1},
        {"current_quota": 1, "remaining_time_in_milliseconds": -1},
        {"current_quota": 1, "remaining_time_in_milliseconds": 300_001},
        {"current_quota": 1, "remaining_time_in_milliseconds": "1"},
        {"current_quota": 1, "remaining_time_in_milliseconds": 1, "extra": 2},
    ],
)
def test_quota_telemetry_rejects_ambiguous_values(payload: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError, ConfigurationError)):
        new_limiter(FakeTime()).observe_quota(payload)


def test_registry_shares_one_limiter_per_external_user_scope() -> None:
    api = limiter_api()
    registry = api.LimiterRegistry()
    fake = FakeTime()
    selected_scope = scope(external_id="delta-user-123")
    calls = 0

    def build() -> object:
        nonlocal calls
        calls += 1
        return new_limiter(fake, selected_scope=selected_scope)

    first = registry.get_or_create(selected_scope, build)
    second = registry.get_or_create(
        scope(external_id="delta-user-123"),
        lambda: pytest.fail("same-user scope must reuse central limiter"),
    )
    assert first is second
    assert calls == 1
    assert registry.coordination_mode is api.CoordinationMode.IN_PROCESS_ONLY


def test_registry_rejects_second_independent_limiter_for_same_scope() -> None:
    api = limiter_api()
    registry = api.LimiterRegistry()
    fake = FakeTime()
    selected_scope = scope(external_id="fixture-api-key-must-not-appear")
    first = new_limiter(fake, selected_scope=selected_scope)
    second = new_limiter(fake, selected_scope=selected_scope)
    registry.register_existing(selected_scope, first)

    with pytest.raises(ConfigurationError) as caught:
        registry.register_existing(selected_scope, second)
    assert "fixture-api-key-must-not-appear" not in str(caught.value)
    assert "fixture-api-key-must-not-appear" not in repr(caught.value.to_dict())


def test_phase_one_durable_coordination_always_fails_closed() -> None:
    registry = limiter_api().LimiterRegistry()
    with pytest.raises(ConfigurationError):
        registry.assert_durable_live_coordination(scope())


@pytest.mark.asyncio
async def test_monotonic_regression_fails_closed_and_snapshots_never_go_negative() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=5)
    await limiter.acquire(request())
    snapshot = await limiter.snapshot()
    assert snapshot.rest_used >= 0
    assert snapshot.rest_remaining >= 0
    assert all(value >= 0 for value in snapshot.product_operations_used.values())

    fake.now = -1
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
