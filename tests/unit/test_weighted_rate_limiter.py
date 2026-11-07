"""Asynchronous weighted quota scheduling and registry ownership contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import MappingProxyType
from uuid import UUID

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
async def test_public_grant_carries_identity_evidence_without_a_held_token() -> None:
    limiter = new_limiter(FakeTime(), capacity=5)
    permit = await limiter.acquire(request())

    assert permit.accounting is not None
    assert permit.accounting.rest_event_id.int > 0
    assert permit.accounting.rest_window_end_monotonic_ns == 300_000_000_000
    assert not hasattr(permit.accounting, "release")
    assert not hasattr(permit.accounting, "refund")


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
async def test_429_extension_wakes_sleeping_fifo_head_and_recomputes_deadline() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=100)
    await limiter.observe_429("1000")
    head = asyncio.create_task(limiter.acquire(request()))
    await settle()
    assert not head.done()

    fake.advance(0.5)
    await limiter.observe_429("2500")
    await settle()
    fake.advance(0.5)
    await settle()
    assert not head.done()

    fake.advance(1.999)
    await settle()
    assert not head.done()
    fake.advance(0.001)
    await settle()
    permit = await head
    assert permit.acquired_at == pytest.approx(3.0)


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


def test_quota_observation_rejects_integer_subclasses_without_callbacks() -> None:
    calls: list[str] = []

    class HostileInt(int):
        def __lt__(self, other: object) -> bool:
            calls.append("lt")
            raise AssertionError("fixture-secret")

    limiter = new_limiter(FakeTime())
    with pytest.raises(ConfigurationError):
        limiter.observe_quota(
            {
                "current_quota": HostileInt(1),
                "remaining_time_in_milliseconds": HostileInt(1),
            }
        )
    assert calls == []


@pytest.mark.asyncio
async def test_request_mapping_provenance_and_route_semantics_fail_before_callbacks() -> None:
    api = limiter_api()
    calls: list[str] = []

    class HostileDict(dict[object, object]):
        def items(self) -> object:
            calls.append("items")
            raise AssertionError("fixture-secret")

        def __iter__(self) -> Iterator[object]:
            calls.append("iter")
            raise AssertionError("fixture-secret")

    def clock() -> float:
        calls.append("clock")
        return 0.0

    def identity() -> UUID:
        calls.append("uuid")
        return UUID(int=len(calls))

    limiter = api.WeightedRateLimiter(
        scope(), policy=tiny_policy(), monotonic=clock, event_id_factory=identity
    )
    calls.clear()
    replacements = (
        MappingProxyType({}),
        MappingProxyType(HostileDict()),
    )
    for replacement in replacements:
        corrupted = api.RateRequest(route=api.RateLimitRoute.RATE_LIMIT_QUOTA)
        object.__setattr__(corrupted, "product_operations", replacement)
        with pytest.raises(ConfigurationError):
            await limiter.acquire(corrupted)
    for route in (
        api.RateLimitRoute.PLACE_ORDER,
        api.RateLimitRoute.MODIFY_ORDER,
        api.RateLimitRoute.BATCH_ORDERS,
    ):
        mismatched = api.RateRequest(route=api.RateLimitRoute.CANCEL_ORDER)
        object.__setattr__(mismatched, "route", route)
        object.__setattr__(mismatched, "_route_owner", route)
        with pytest.raises(ConfigurationError):
            await limiter.acquire(mismatched)
    for route in (api.RateLimitRoute.CANCEL_ORDER, api.RateLimitRoute.CANCEL_ALL):
        mismatched = api.RateRequest(
            route=api.RateLimitRoute.PLACE_ORDER, product_operations={7: 1}
        )
        object.__setattr__(mismatched, "route", route)
        object.__setattr__(mismatched, "_route_owner", route)
        with pytest.raises(ConfigurationError):
            await limiter.acquire(mismatched)
    assert calls == []


def test_policy_mapping_provenance_fails_before_uuid_or_hostile_callbacks() -> None:
    api = limiter_api()
    calls: list[str] = []

    class HostileDict(dict[object, object]):
        def items(self) -> object:
            calls.append("items")
            raise AssertionError("fixture-secret")

    for replacement in (
        MappingProxyType(dict(tiny_policy().route_weights)),
        MappingProxyType(HostileDict(tiny_policy().route_weights)),
    ):
        policy = tiny_policy()
        object.__setattr__(policy, "route_weights", replacement)
        with pytest.raises(ValueError):
            api.WeightedRateLimiter(
                scope(), policy=policy, event_id_factory=lambda: calls.append("uuid")
            )
    policy = tiny_policy()
    object.__setattr__(policy, "route_weights", MappingProxyType(HostileDict()))
    with pytest.raises(ConfigurationError):
        policy.weight_for(api.RateLimitRoute.PRODUCTS)
    assert calls == []


@pytest.mark.parametrize("attribute", ["scope", "policy", "coordination_mode"])
def test_bound_limiter_properties_reject_a_second_loop_without_callbacks(
    attribute: str,
) -> None:
    api = limiter_api()
    calls: list[str] = []

    def clock() -> float:
        calls.append("clock")
        return 0.0

    limiter = api.WeightedRateLimiter(scope(), policy=tiny_policy(), monotonic=clock)
    asyncio.run(limiter.snapshot())
    calls.clear()

    async def read_property() -> object:
        return getattr(limiter, attribute)

    with pytest.raises(ConfigurationError):
        asyncio.run(read_property())
    assert calls == []


@pytest.mark.asyncio
async def test_hostile_poison_flags_transition_once_without_truth_callbacks() -> None:
    calls: list[str] = []

    class HostileFlag:
        def __bool__(self) -> bool:
            calls.append("bool")
            raise AssertionError("fixture-secret")

    limiter = new_limiter(FakeTime())
    object.__setattr__(limiter, "_accounting_poisoned", HostileFlag())

    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert calls == []
    assert limiter._accounting_poisoned is True
    assert limiter._limiter_accounting_generation == 2
    assert limiter._limiter_clock_epoch == 1
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert limiter._limiter_accounting_generation == 2


@pytest.mark.asyncio
async def test_clock_poison_handles_callback_corrupted_flag_without_truth_callback() -> None:
    calls: list[str] = []
    holder: list[object] = []

    class HostileFlag:
        def __bool__(self) -> bool:
            calls.append("bool")
            raise AssertionError("fixture-secret")

    def clock() -> float:
        calls.append("clock")
        object.__setattr__(holder[0], "_clock_poisoned", HostileFlag())
        raise RuntimeError("fixture-secret")

    limiter = limiter_api().WeightedRateLimiter(scope(), policy=tiny_policy(), monotonic=clock)
    holder.append(limiter)

    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert calls == ["clock"]
    assert limiter._clock_poisoned is True
    assert limiter._limiter_clock_epoch == 2
    assert limiter._limiter_accounting_generation == 1
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert calls == ["clock"]
    assert limiter._limiter_clock_epoch == 2


@pytest.mark.asyncio
async def test_accounting_poison_never_adds_a_corrupted_generation() -> None:
    calls: list[str] = []

    class HostileInt(int):
        def __add__(self, other: object) -> int:
            calls.append("add")
            raise AssertionError("fixture-secret")

    limiter = new_limiter(FakeTime())
    object.__setattr__(limiter, "_limiter_accounting_generation", HostileInt(1))

    with pytest.raises(ConfigurationError) as captured:
        await limiter.snapshot()
    assert calls == []
    assert "fixture-secret" not in str(captured.value)
    assert limiter._accounting_poisoned is True
    assert type(limiter._limiter_accounting_generation) is int
    assert limiter._limiter_accounting_generation == 2
    assert limiter._limiter_clock_epoch == 1
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert calls == []
    assert limiter._limiter_accounting_generation == 2


@pytest.mark.asyncio
async def test_clock_poison_never_adds_callback_corrupted_epoch() -> None:
    canary: list[str] = []
    clock_calls = 0
    holder: list[object] = []

    class HostileInt(int):
        def __add__(self, other: object) -> int:
            canary.append("add")
            raise AssertionError("fixture-secret")

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        object.__setattr__(holder[0], "_limiter_clock_epoch", HostileInt(1))
        raise RuntimeError("fixture-secret")

    limiter = limiter_api().WeightedRateLimiter(scope(), policy=tiny_policy(), monotonic=clock)
    holder.append(limiter)

    with pytest.raises(ConfigurationError) as captured:
        await limiter.snapshot()
    assert canary == []
    assert clock_calls == 1
    assert "fixture-secret" not in str(captured.value)
    assert limiter._clock_poisoned is True
    assert type(limiter._limiter_clock_epoch) is int
    assert limiter._limiter_clock_epoch == 2
    assert limiter._limiter_accounting_generation == 1
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert canary == []
    assert clock_calls == 1
    assert limiter._limiter_clock_epoch == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [0, -1])
async def test_accounting_poison_canonicalizes_nonpositive_generation(generation: int) -> None:
    limiter = new_limiter(FakeTime())
    object.__setattr__(limiter, "_limiter_accounting_generation", generation)

    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert limiter._accounting_poisoned is True
    assert type(limiter._limiter_accounting_generation) is int
    assert limiter._limiter_accounting_generation == 2
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert limiter._limiter_accounting_generation == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("epoch", [0, -1])
async def test_clock_poison_canonicalizes_callback_nonpositive_epoch(epoch: int) -> None:
    clock_calls = 0
    holder: list[object] = []

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        object.__setattr__(holder[0], "_limiter_clock_epoch", epoch)
        raise RuntimeError("fixture-secret")

    limiter = limiter_api().WeightedRateLimiter(scope(), policy=tiny_policy(), monotonic=clock)
    holder.append(limiter)

    with pytest.raises(ConfigurationError) as captured:
        await limiter.snapshot()
    assert "fixture-secret" not in str(captured.value)
    assert limiter._clock_poisoned is True
    assert type(limiter._limiter_clock_epoch) is int
    assert limiter._limiter_clock_epoch == 2
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert clock_calls == 1
    assert limiter._limiter_clock_epoch == 2


@pytest.mark.asyncio
async def test_clock_callback_corrupting_last_sample_poisoned_before_comparison() -> None:
    calls: list[str] = []
    holder: list[object] = []

    class HostileFloat(float):
        def __gt__(self, other: object) -> bool:
            calls.append("compare")
            raise AssertionError("fixture-secret")

    def clock() -> float:
        calls.append("clock")
        object.__setattr__(holder[0], "_last_now", HostileFloat(0.0))
        return 1.0

    limiter = limiter_api().WeightedRateLimiter(scope(), policy=tiny_policy(), monotonic=clock)
    holder.append(limiter)

    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert calls == ["clock"]
    assert limiter._accounting_poisoned is True
    assert limiter._limiter_accounting_generation == 2
    assert limiter._limiter_clock_epoch == 1
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert calls == ["clock"]
    assert limiter._limiter_accounting_generation == 2


@pytest.mark.asyncio
async def test_live_leaf_subclasses_poison_accounting_once_without_callbacks() -> None:
    calls: list[str] = []

    class HostileFloat(float):
        def __lt__(self, other: object) -> bool:
            calls.append("float-lt")
            raise AssertionError("fixture-secret")

        def __gt__(self, other: object) -> bool:
            calls.append("float-gt")
            raise AssertionError("fixture-secret")

    class HostileInt(int):
        def __le__(self, other: object) -> bool:
            calls.append("int-le")
            raise AssertionError("fixture-secret")

        def __ne__(self, other: object) -> bool:
            calls.append("int-ne")
            raise AssertionError("fixture-secret")

    first = new_limiter(FakeTime())
    await first.acquire(request())
    object.__setattr__(first, "_last_now", HostileFloat(0.0))
    second = new_limiter(FakeTime(), capacity=5)
    await second.acquire(request("place_order", {7: 1}))
    indexed = next(row for row in second._event_index.values() if row.product_id == 7)
    object.__setattr__(indexed, "product_id", HostileInt(7))

    for limiter in (first, second):
        with pytest.raises(ConfigurationError):
            await limiter.snapshot()
        assert limiter._accounting_poisoned is True
        assert limiter._limiter_accounting_generation == 2
        assert limiter._limiter_clock_epoch == 1
        with pytest.raises(ConfigurationError):
            await limiter.snapshot()
        assert limiter._limiter_accounting_generation == 2
    assert calls == []


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


def test_registry_preserves_existing_weighted_limiter_subclass_compatibility() -> None:
    api = limiter_api()

    class InstrumentedLimiter(api.WeightedRateLimiter):
        pass

    selected_scope = scope(external_id="subclass-compatible")
    limiter = InstrumentedLimiter(selected_scope, policy=tiny_policy())
    registry = api.LimiterRegistry()

    assert registry.register_existing(selected_scope, limiter) is limiter


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
