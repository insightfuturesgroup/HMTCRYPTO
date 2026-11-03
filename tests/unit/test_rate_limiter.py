"""Deterministic weighted quota, product-operation, and retry safety contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from decimal import Decimal
from importlib import import_module
from typing import Any

import httpx
import pytest

from hmt_crypto.errors import (
    AmbiguousExchangeResult,
    ConfigurationError,
    RateLimitError,
)

pytestmark = pytest.mark.unit


def limiter_api() -> Any:
    """Load the wished-for limiter API without making RED a collection failure."""
    try:
        return import_module("hmt_crypto.exchange.delta.rate_limiter")
    except ModuleNotFoundError:
        pytest.fail("Delta rate-limiter API is missing", pytrace=False)


class FakeTime:
    """Monotonic clock whose async sleepers complete only when tests advance it."""

    def __init__(self) -> None:
        self.now = 0.0
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        if delay <= 0:
            await asyncio.sleep(0)
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._sleepers.append((self.now + delay, future))
        self._wake_due()
        try:
            await future
        finally:
            self._sleepers = [(at, item) for at, item in self._sleepers if item is not future]

    def advance(self, seconds: float) -> None:
        assert seconds >= 0
        self.now += seconds
        self._wake_due()

    def _wake_due(self) -> None:
        for deadline, future in tuple(self._sleepers):
            if deadline <= self.now and not future.done():
                future.set_result(None)


async def settle() -> None:
    """Allow limiter tasks and fake-sleeper cancellation callbacks to run."""
    for _ in range(12):
        await asyncio.sleep(0)


def scope(
    *, region: str = "india", environment: str = "testnet", external_id: str = "user-42"
) -> Any:
    api = limiter_api()
    return api.RateLimitScope(
        region=region,
        environment=environment,
        kind=api.ScopeKind.EXTERNAL_USER,
        external_id=external_id,
    )


def tiny_policy(*, capacity: int = 3, product_limit: int = 500) -> Any:
    api = limiter_api()
    return api.RateLimitPolicy(
        version="unit-test-policy",
        india_capacity=capacity,
        global_capacity=capacity,
        rest_window_seconds=300,
        product_operations_per_second=product_limit,
        route_weights={route: api.ROUTE_WEIGHTS[route] for route in api.RateLimitRoute},
    )


def request(route_name: str = "rate_limit_quota", products: dict[int, int] | None = None) -> Any:
    api = limiter_api()
    return api.RateRequest(
        route=api.RateLimitRoute(route_name),
        product_operations=products or {},
    )


def new_limiter(
    fake: FakeTime,
    *,
    capacity: int = 3,
    product_limit: int = 500,
    selected_scope: Any | None = None,
) -> Any:
    api = limiter_api()
    return api.WeightedRateLimiter(
        selected_scope or scope(),
        policy=tiny_policy(capacity=capacity, product_limit=product_limit),
        monotonic=fake.monotonic,
        sleeper=fake.sleep,
    )


def test_official_policy_has_exact_version_quotas_and_weights() -> None:
    api = limiter_api()
    policy = api.DELTA_RATE_LIMIT_POLICY

    assert policy.version == "delta-docs-2026-11-03"
    assert policy.capacity_for("india") == 20_000
    assert policy.capacity_for("global") == 10_000
    assert policy.rest_window_seconds == 300
    assert policy.product_operations_per_second == 500

    weight_three = {
        "products",
        "orderbook",
        "tickers",
        "open_orders",
        "open_positions",
        "balances",
        "candles",
    }
    weight_five = {"place_order", "modify_order", "cancel_order", "add_position_margin"}
    weight_ten = {"order_history", "fills", "transactions"}
    for name in weight_three:
        assert policy.weight_for(api.RateLimitRoute(name)) == 3
    for name in weight_five:
        assert policy.weight_for(api.RateLimitRoute(name)) == 5
    for name in weight_ten:
        assert policy.weight_for(api.RateLimitRoute(name)) == 10
    assert policy.weight_for(api.RateLimitRoute.BATCH_ORDERS) == 25
    assert policy.weight_for(api.RateLimitRoute.RATE_LIMIT_QUOTA) == 1


def test_unknown_route_is_not_silently_assigned_default_weight() -> None:
    api = limiter_api()
    with pytest.raises((ValueError, ConfigurationError)):
        api.DELTA_RATE_LIMIT_POLICY.weight_for("/v2/withdraw")
    with pytest.raises(ValueError):
        api.RateLimitRoute("withdraw")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"region": "INDIA"}, "region"),
        ({"environment": "production"}, "environment"),
        ({"external_id": ""}, "external"),
        ({"external_id": " "}, "external"),
        ({"external_id": "x" * 129}, "external"),
    ],
)
def test_scope_is_strict_and_environment_specific(kwargs: dict[str, object], message: str) -> None:
    api = limiter_api()
    values: dict[str, object] = {
        "region": "india",
        "environment": "testnet",
        "kind": api.ScopeKind.EXTERNAL_USER,
        "external_id": "user-42",
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError), match=message):
        api.RateLimitScope(**values)

    assert scope(region="india") != scope(region="global")
    assert scope(environment="testnet") != scope(environment="prod")


def test_scope_representation_does_not_expose_external_identifier() -> None:
    external_id = "fixture-api-key-must-never-be-logged"
    selected = scope(external_id=external_id)
    assert external_id not in repr(selected)
    assert external_id not in str(selected)


@pytest.mark.parametrize(
    "products",
    [
        {True: 1},
        {0: 1},
        {-1: 1},
        {1: True},
        {1: 0},
        {1: -1},
        {"1": 1},
    ],
)
def test_product_operations_require_positive_integer_ids_and_counts(
    products: dict[object, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        request(products=products)  # type: ignore[arg-type]


def test_rate_request_snapshots_caller_mapping_and_is_immutable() -> None:
    products = {7: 2}
    rate_request = request(products=products)
    products[7] = 400

    assert dict(rate_request.product_operations) == {7: 2}
    with pytest.raises(TypeError):
        rate_request.product_operations[7] = 3


@pytest.mark.parametrize("route", ["place_order", "modify_order", "batch_orders"])
def test_matching_engine_writes_cannot_bypass_product_operation_budget(route: str) -> None:
    api = limiter_api()
    with pytest.raises(ValueError, match="product"):
        api.RateRequest(route=api.RateLimitRoute(route))


def test_cancel_route_rejects_product_operations_because_cancels_are_exempt() -> None:
    api = limiter_api()
    with pytest.raises(ValueError, match="cancel"):
        api.RateRequest(route=api.RateLimitRoute.CANCEL_ORDER, product_operations={1: 1})


def test_request_and_policy_reject_non_mapping_inputs() -> None:
    api = limiter_api()
    with pytest.raises(ValueError, match="mapping"):
        api.RateRequest(  # type: ignore[arg-type]
            route=api.RateLimitRoute.PRODUCTS,
            product_operations=[(1, 1)],
        )
    with pytest.raises(ValueError, match="mapping"):
        api.RateLimitPolicy(  # type: ignore[arg-type]
            version="bad",
            india_capacity=1,
            global_capacity=1,
            rest_window_seconds=300,
            product_operations_per_second=500,
            route_weights=[],
        )


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
    # If the cancelled waiter was the head, let its successor register its
    # fake-clock sleep before advancing the clock.
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

    # Strict FIFO intentionally creates head-of-line blocking. Remove that
    # waiter before proving a different product has an independent budget.
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


def test_batch_counts_items_and_cancel_is_product_exempt() -> None:
    api = limiter_api()
    batch = api.RateRequest(
        route=api.RateLimitRoute.BATCH_ORDERS,
        product_operations={1: 20, 2: 5},
    )
    cancel = api.RateRequest(route=api.RateLimitRoute.CANCEL_ORDER)
    assert api.DELTA_RATE_LIMIT_POLICY.weight_for(batch.route) == 25
    assert sum(batch.product_operations.values()) == 25
    assert api.DELTA_RATE_LIMIT_POLICY.weight_for(cancel.route) == 5
    assert dict(cancel.product_operations) == {}


@pytest.mark.asyncio
async def test_over_capacity_request_fails_without_queuing_or_partial_consumption() -> None:
    fake = FakeTime()
    limiter = new_limiter(fake, capacity=3, product_limit=500)
    api = limiter_api()
    too_heavy = api.RateRequest(route=api.RateLimitRoute.PRODUCTS)
    # Products weight is exactly the capacity and is allowed.
    await limiter.acquire(too_heavy)

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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", 0), ("1", 1), ("1500", 1500), ("300000", 300_000)],
)
def test_reset_header_is_strict_milliseconds(raw: str, expected: int) -> None:
    assert limiter_api().parse_rate_limit_reset_ms(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "-1", "+1", "1.0", " 1", "1 ", "٣", "300001", 1, True],
)
def test_invalid_reset_header_fails_safely(raw: object) -> None:
    with pytest.raises((TypeError, ValueError, ConfigurationError)):
        limiter_api().parse_rate_limit_reset_ms(raw)  # type: ignore[arg-type]


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


def test_exported_quota_and_retry_evidence_rejects_contradictory_direct_construction() -> None:
    api = limiter_api()
    with pytest.raises(ValueError):
        api.QuotaObservation(
            current_quota=-1,
            remaining_time_in_milliseconds=1,
            observed_at=0.0,
        )
    with pytest.raises(ValueError):
        api.RetryDecision(
            action=api.RetryAction.RETRY,
            delay_seconds=None,
            reason="contradictory",
            operation=api.OperationClass.SAFE_READ,
        )
    with pytest.raises(ValueError):
        api.RateLimitSnapshot(
            policy_version="test",
            capacity=1,
            rest_used=1,
            rest_remaining=1,
            blocked_until=None,
            waiter_count=0,
            product_operations_used={},
            closed=False,
        )
    with pytest.raises((ValueError, ConfigurationError)):
        api.RatePermit(acquired_at=-1, rest_weight=1, product_operations={})
    with pytest.raises(ValueError):
        api.RetryDecision(
            action=api.RetryAction.RETRY,
            delay_seconds=Decimal("1"),
            reason="write-retry",
            operation=api.OperationClass.CREATE_ORDER,
        )
    with pytest.raises(ValueError):
        api.RetryDecision(
            action=api.RetryAction.AMBIGUOUS,
            delay_seconds=None,
            reason="read-ambiguity",
            operation=api.OperationClass.SAFE_READ,
        )


def test_registry_shares_one_limiter_per_external_user_scope() -> None:
    api = limiter_api()
    registry = api.LimiterRegistry()
    fake = FakeTime()
    selected_scope = scope(external_id="delta-user-123")
    calls = 0

    def build() -> Any:
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


def retry_policy(**overrides: object) -> Any:
    api = limiter_api()
    values: dict[str, object] = {
        "max_attempts": 3,
        "base_delay_seconds": "1",
        "max_delay_seconds": "10",
        "jitter_ratio": "0.2",
    }
    values.update(overrides)
    return api.RetryPolicy(**values)


def decision(
    *,
    operation: str = "safe_read",
    attempt: int = 1,
    status_code: int | None = None,
    reset_header: str | None = None,
    transport_error: BaseException | None = None,
    bytes_may_have_been_sent: bool = False,
    jitter_unit: object = "0.5",
) -> Any:
    api = limiter_api()
    return api.classify_retry(
        operation=api.OperationClass(operation),
        attempt=attempt,
        status_code=status_code,
        reset_header=reset_header,
        transport_error=transport_error,
        bytes_may_have_been_sent=bytes_may_have_been_sent,
        jitter_unit=jitter_unit,
        policy=retry_policy(),
    )


@pytest.mark.parametrize("status", [429, 500, 502, 503, 599])
def test_safe_reads_retry_429_and_every_5xx(status: int) -> None:
    api = limiter_api()
    result = decision(status_code=status)
    assert result.action is api.RetryAction.RETRY
    assert result.delay_seconds == Decimal("1.1")


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda: httpx.ConnectError("connect"),
        lambda: httpx.ConnectTimeout("connect-timeout"),
        lambda: httpx.ReadTimeout("read-timeout"),
        lambda: httpx.ReadError("read"),
    ],
)
def test_safe_reads_retry_only_narrow_transient_transport_errors(
    error_factory: Callable[[], BaseException],
) -> None:
    assert decision(transport_error=error_factory()).action is limiter_api().RetryAction.RETRY


def test_tls_connect_failure_is_not_classified_as_transient() -> None:
    import ssl

    try:
        raise ssl.SSLError("certificate failure")
    except ssl.SSLError as cause:
        error = httpx.ConnectError("TLS failed")
        error.__cause__ = cause
    assert decision(transport_error=error).action is limiter_api().RetryAction.FAIL


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 406])
def test_non_429_4xx_are_never_retried(status: int) -> None:
    result = decision(status_code=status)
    assert result.action is limiter_api().RetryAction.FAIL
    assert result.delay_seconds is None


@pytest.mark.parametrize(
    "error",
    [
        ValueError("programming"),
        httpx.RemoteProtocolError("protocol"),
        asyncio.CancelledError(),
    ],
)
def test_programming_protocol_and_cancellation_errors_are_not_retried(error: BaseException) -> None:
    assert decision(transport_error=error).action is limiter_api().RetryAction.FAIL


def test_definitive_http_response_and_cancellation_take_precedence_over_other_signals() -> None:
    api = limiter_api()
    assert (
        decision(status_code=401, transport_error=httpx.ConnectError("also-present")).action
        is api.RetryAction.FAIL
    )
    assert (
        decision(status_code=503, transport_error=asyncio.CancelledError()).action
        is api.RetryAction.FAIL
    )


def test_backoff_is_exponential_bounded_and_stops_at_max_attempts() -> None:
    api = limiter_api()
    assert decision(status_code=503, attempt=1).delay_seconds == Decimal("1.1")
    assert decision(status_code=503, attempt=2).delay_seconds == Decimal("2.2")
    exhausted = decision(status_code=503, attempt=3)
    assert exhausted.action is api.RetryAction.FAIL
    assert exhausted.reason == "attempts_exhausted"

    capped = api.classify_retry(
        operation=api.OperationClass.SAFE_READ,
        attempt=3,
        status_code=503,
        reset_header=None,
        transport_error=None,
        bytes_may_have_been_sent=False,
        jitter_unit="1",
        policy=retry_policy(max_attempts=5, base_delay_seconds="4", max_delay_seconds="5"),
    )
    assert capped.delay_seconds == Decimal("5")


def test_429_reset_is_a_minimum_delay_and_never_interpreted_as_seconds() -> None:
    result = decision(status_code=429, reset_header="2500", jitter_unit="0")
    assert result.delay_seconds == Decimal("2.5")


@pytest.mark.parametrize("jitter", [True, -1, "-0.1", "1.1", float("nan")])
def test_jitter_input_is_strict_and_bounded(jitter: object) -> None:
    with pytest.raises((TypeError, ValueError, ConfigurationError)):
        decision(status_code=500, jitter_unit=jitter)


@pytest.mark.parametrize(
    "operation",
    ["create_order", "modify_order", "cancel_order", "set_leverage", "add_margin", "batch_write"],
)
def test_every_write_class_is_categorically_never_auto_retried(operation: str) -> None:
    assert decision(operation=operation, status_code=429).action is limiter_api().RetryAction.FAIL
    assert decision(operation=operation, status_code=503).action is limiter_api().RetryAction.FAIL
    assert (
        decision(operation=operation, transport_error=httpx.ConnectError("x")).action
        is limiter_api().RetryAction.FAIL
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status_code": 503},
        {"transport_error": httpx.ReadTimeout("uncertain")},
        {"transport_error": httpx.WriteError("uncertain")},
    ],
)
def test_write_failures_after_bytes_may_have_been_sent_are_ambiguous(
    kwargs: dict[str, object],
) -> None:
    api = limiter_api()
    result = decision(operation="create_order", bytes_may_have_been_sent=True, **kwargs)  # type: ignore[arg-type]
    assert result.action is api.RetryAction.AMBIGUOUS
    assert result.delay_seconds is None
    with pytest.raises(AmbiguousExchangeResult):
        result.raise_if_ambiguous()


def test_definitive_write_rejection_and_no_send_fail_without_ambiguity() -> None:
    api = limiter_api()
    assert (
        decision(operation="create_order", status_code=400, bytes_may_have_been_sent=True).action
        is api.RetryAction.FAIL
    )
    assert (
        decision(
            operation="create_order",
            status_code=503,
            bytes_may_have_been_sent=False,
        ).action
        is api.RetryAction.FAIL
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": True},
        {"max_attempts": 0},
        {"base_delay_seconds": 1.0},
        {"base_delay_seconds": "0"},
        {"max_delay_seconds": "0"},
        {"jitter_ratio": True},
        {"jitter_ratio": "-0.1"},
        {"jitter_ratio": "1.1"},
    ],
)
def test_retry_policy_rejects_ambiguous_numeric_inputs(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        retry_policy(**kwargs)


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
