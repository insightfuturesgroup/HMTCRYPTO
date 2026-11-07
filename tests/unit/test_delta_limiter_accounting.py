"""Identified quota-accounting contracts on the real Delta limiter."""

from __future__ import annotations

import ast
import asyncio
import math
import threading
from collections import deque
from collections.abc import Iterator
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from types import MappingProxyType
from uuid import UUID

import pytest

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta import rate_limit_policy as policy_api

pytestmark = pytest.mark.unit


def _uuid(value: int) -> UUID:
    return UUID(int=value)


def _accounting(**changes: object) -> object:
    values: dict[str, object] = {
        "limiter_instance_id": _uuid(1),
        "limiter_clock_epoch": 1,
        "limiter_accounting_generation": 1,
        "acquired_monotonic_ns": 10,
        "rest_event_id": _uuid(2),
        "rest_window_end_monotonic_ns": 20,
        "product_event_ids": {7: _uuid(3)},
        "product_window_end_monotonic_ns": {7: 15},
        "cooldown_epoch": 0,
    }
    values.update(changes)
    accounting_type = policy_api.RatePermitAccounting
    return accounting_type(**values)


def test_rate_permit_accounting_owns_exact_fields_and_product_maps() -> None:
    event_ids = {7: _uuid(3)}
    deadlines = {7: 15}
    accounting = _accounting(
        product_event_ids=event_ids,
        product_window_end_monotonic_ns=deadlines,
    )
    event_ids[7] = _uuid(99)
    deadlines[7] = 99

    assert accounting.limiter_instance_id == _uuid(1)
    assert accounting.limiter_clock_epoch == 1
    assert accounting.limiter_accounting_generation == 1
    assert accounting.acquired_monotonic_ns == 10
    assert accounting.rest_event_id == _uuid(2)
    assert accounting.rest_window_end_monotonic_ns == 20
    assert accounting.product_event_ids == {7: _uuid(3)}
    assert accounting.product_window_end_monotonic_ns == {7: 15}
    assert accounting.cooldown_epoch == 0
    assert type(accounting.product_event_ids) is MappingProxyType
    assert type(accounting.product_window_end_monotonic_ns) is MappingProxyType
    with pytest.raises(TypeError):
        accounting.product_event_ids[7] = _uuid(4)


@pytest.mark.parametrize(
    "changes",
    [
        {"limiter_instance_id": "1"},
        {"limiter_clock_epoch": True},
        {"limiter_clock_epoch": 0},
        {"limiter_accounting_generation": 0},
        {"acquired_monotonic_ns": -1},
        {"rest_event_id": UUID(int=0)},
        {"rest_window_end_monotonic_ns": 10},
        {"cooldown_epoch": -1},
        {"product_event_ids": {0: _uuid(3)}},
        {"product_event_ids": {7: UUID(int=0)}},
        {"product_window_end_monotonic_ns": {7: 10}},
        {"product_event_ids": {7: _uuid(2)}},
        {"product_event_ids": {7: _uuid(3)}, "product_window_end_monotonic_ns": {}},
    ],
)
def test_accounting_rejects_invalid_bounds_and_identity(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _accounting(**changes)


def test_rate_permit_accounting_rejects_nonexact_containers_before_callbacks() -> None:
    calls: list[str] = []

    class HostileDict(dict[object, object]):
        def items(self) -> object:
            calls.append("items")
            raise AssertionError("fixture-secret")

        def __iter__(self) -> Iterator[object]:
            calls.append("iter")
            raise AssertionError("fixture-secret")

    class Integer(int):
        __hash__ = int.__hash__

        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("fixture-secret")

    for changes in (
        {"product_event_ids": HostileDict({7: _uuid(3)})},
        {"product_window_end_monotonic_ns": HostileDict({7: 15})},
        {"product_event_ids": {Integer(7): _uuid(3)}},
        {"product_window_end_monotonic_ns": {Integer(7): 15}},
    ):
        with pytest.raises(ValueError):
            _accounting(**changes)
    assert calls == []


def test_rate_permit_keeps_legacy_positional_shape_and_rebuilds_accounting() -> None:
    legacy = policy_api.RatePermit(1.25, 3, {7: 2})
    assert legacy.accounting is None
    accounting = _accounting()
    permit = policy_api.RatePermit(1.25, 3, {7: 2}, accounting)

    assert permit.accounting == accounting
    assert permit.accounting is not accounting
    assert permit.accounting.product_event_ids is not accounting.product_event_ids
    assert "RatePermitAccounting" in policy_api.__all__
    with pytest.raises(ValueError):
        policy_api.RatePermit(1.25, 3, {7: 2}, object())


class _Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _Ids:
    def __init__(self, values: list[int] | None = None) -> None:
        self.values = iter(values) if values is not None else None
        self.next_value = 1
        self.signal: tuple[int, asyncio.Event] | None = None

    def __call__(self) -> UUID:
        if self.values is not None:
            return _uuid(next(self.values))
        value = _uuid(self.next_value)
        self.next_value += 1
        if self.signal is not None and value.int == self.signal[0]:
            self.signal[1].set()
        return value


def _scope() -> policy_api.RateLimitScope:
    return policy_api.RateLimitScope(
        region="india",
        environment="testnet",
        kind=policy_api.ScopeKind.EXTERNAL_USER,
        external_id="accounting-user",
    )


def _policy(*, capacity: int = 100, product_capacity: int = 500) -> policy_api.RateLimitPolicy:
    return policy_api.RateLimitPolicy(
        version="accounting-test-v1",
        india_capacity=capacity,
        global_capacity=capacity,
        rest_window_seconds=300,
        product_operations_per_second=product_capacity,
        route_weights=dict(policy_api.ROUTE_WEIGHTS),
    )


def _request(*, products: dict[int, int] | None = None) -> policy_api.RateRequest:
    route = (
        policy_api.RateLimitRoute.PLACE_ORDER
        if products
        else policy_api.RateLimitRoute.RATE_LIMIT_QUOTA
    )
    return policy_api.RateRequest(route=route, product_operations=products or {})


def _limiter(
    clock: _Clock,
    *,
    ids: _Ids | None = None,
    capacity: int = 100,
    product_capacity: int = 500,
) -> object:
    limiter_type = import_module("hmt_crypto.exchange.delta.weighted_limiter").WeightedRateLimiter
    return limiter_type(
        _scope(),
        policy=_policy(capacity=capacity, product_capacity=product_capacity),
        monotonic=clock,
        event_id_factory=ids or _Ids(),
    )


def test_float_monotonic_conversion_uses_exact_binary_ratio_flooring() -> None:
    limiter_type = import_module("hmt_crypto.exchange.delta.weighted_limiter").WeightedRateLimiter
    value = math.nextafter(1_000_000_000.0, math.inf)
    numerator, denominator = value.as_integer_ratio()
    expected = numerator * 1_000_000_000 // denominator

    assert limiter_type._float_monotonic_to_ns(value) == expected
    assert limiter_type._float_monotonic_to_ns(value) != int(value * 1_000_000_000)


@pytest.mark.asyncio
async def test_real_limiter_emits_distinct_equal_time_events_and_exact_deadlines() -> None:
    clock = _Clock(12.5)
    limiter = _limiter(clock)
    first = await limiter.acquire(_request(products={7: 2}))
    second = await limiter.acquire(_request(products={8: 3}))

    assert first.accounting is not None and second.accounting is not None
    assert first.accounting.limiter_instance_id == second.accounting.limiter_instance_id
    assert first.accounting.rest_event_id != second.accounting.rest_event_id
    assert first.accounting.acquired_monotonic_ns == 12_500_000_000
    assert first.accounting.rest_window_end_monotonic_ns == 312_500_000_000
    assert first.accounting.product_window_end_monotonic_ns == {7: 13_500_000_000}
    assert second.accounting.product_window_end_monotonic_ns == {8: 13_500_000_000}
    assert len(limiter._event_index) == 4
    assert {event.event_id for event in limiter._rest_events} == {
        first.accounting.rest_event_id,
        second.accounting.rest_event_id,
    }


@pytest.mark.asyncio
async def test_duplicate_preallocated_event_ids_fail_before_queue_or_charge() -> None:
    limiter = _limiter(_Clock(), ids=_Ids([1, 2, 2]))
    with pytest.raises(ConfigurationError) as captured:
        await limiter.acquire(_request(products={7: 1}))

    assert captured.value.context == {"operation": "rate_limit_accounting"}
    assert not limiter._waiters
    assert not limiter._rest_events
    assert not limiter._event_index


@pytest.mark.asyncio
async def test_exact_expiry_prunes_event_and_index_without_generation_change() -> None:
    clock = _Clock()
    limiter = _limiter(clock, capacity=1)
    permit = await limiter.acquire(_request())
    assert permit.accounting is not None
    assert len(limiter._event_index) == 1

    clock.value = 300.0
    snapshot = await limiter.snapshot()
    assert snapshot.rest_used == 0
    assert limiter._event_index == {}
    assert limiter._limiter_accounting_generation == 1


def test_accounting_gate_is_nonreentrant_and_permanently_context_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounting = import_module("hmt_crypto.exchange.delta.limiter_accounting")
    gate = accounting._LimiterAccountingGate()
    assert gate.owner_thread_id == threading.get_ident()
    assert gate.held_by_current_thread() is False
    with gate.hold():
        assert gate.held_by_current_thread() is True
        with pytest.raises(ConfigurationError):
            with gate.hold():
                pass
    assert gate.held_by_current_thread() is False

    assert gate._lock.acquire(blocking=False) is True
    try:
        with pytest.raises(ConfigurationError):
            with gate.hold():
                pass
    finally:
        gate._lock.release()

    monkeypatch.setattr(accounting.os, "getpid", lambda: gate.owner_pid + 1)
    with pytest.raises(ConfigurationError):
        gate.assert_process_thread()


def test_accounting_gate_rejects_foreign_thread_and_second_owner_loop() -> None:
    accounting = import_module("hmt_crypto.exchange.delta.limiter_accounting")
    gate = accounting._LimiterAccountingGate()
    errors: list[BaseException] = []

    def foreign() -> None:
        try:
            gate.assert_process_thread()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=foreign)
    thread.start()
    thread.join(timeout=1)
    assert len(errors) == 1 and isinstance(errors[0], ConfigurationError)

    async def bind() -> None:
        gate.bind_or_assert_running_loop(asyncio.get_running_loop())

    asyncio.run(bind())
    with pytest.raises(ConfigurationError):
        asyncio.run(bind())


def test_owned_configuration_snapshots_exact_inputs_without_hostile_callbacks() -> None:
    accounting = import_module("hmt_crypto.exchange.delta.limiter_accounting")
    owned_type = accounting._OwnedLimiterConfiguration
    selected_scope = _scope()
    selected_policy = _policy(capacity=17)
    owned = owned_type.from_inputs(selected_scope, selected_policy)
    object.__setattr__(selected_scope, "region", "global")
    object.__setattr__(selected_policy, "india_capacity", 999)

    assert owned.scope_snapshot().region == "india"
    assert owned.policy_snapshot().india_capacity == 17
    assert owned.capacity == 17

    calls: list[str] = []

    class HostileMapping(dict[object, object]):
        def items(self) -> object:
            calls.append("items")
            raise AssertionError("fixture-secret")

        def __iter__(self) -> Iterator[object]:
            calls.append("iter")
            raise AssertionError("fixture-secret")

    corrupted = _policy()
    object.__setattr__(corrupted, "route_weights", HostileMapping())
    with pytest.raises(ValueError):
        owned_type.from_inputs(_scope(), corrupted)
    assert calls == []

    corrupted_scope = _scope()
    object.__setattr__(corrupted_scope, "external_id", "")
    with pytest.raises(ValueError):
        owned_type.from_inputs(corrupted_scope, _policy())


@pytest.mark.asyncio
async def test_clock_and_uuid_callbacks_execute_only_outside_accounting_gate() -> None:
    calls: list[str] = []
    holder: list[object] = []
    next_id = 1

    def event_id() -> UUID:
        nonlocal next_id
        if holder:
            assert not holder[0]._accounting_gate.held_by_current_thread()
        calls.append("uuid")
        result = _uuid(next_id)
        next_id += 1
        return result

    def monotonic() -> float:
        if holder:
            assert not holder[0]._accounting_gate.held_by_current_thread()
        calls.append("clock")
        return 0.0

    limiter_type = import_module("hmt_crypto.exchange.delta.weighted_limiter").WeightedRateLimiter
    limiter = limiter_type(
        _scope(), policy=_policy(), monotonic=monotonic, event_id_factory=event_id
    )
    holder.append(limiter)
    await limiter.acquire(_request(products={7: 1, 8: 1}))

    assert calls.count("uuid") == 4  # limiter, REST, then two sorted products
    assert calls.count("clock") >= 2


@pytest.mark.asyncio
async def test_clock_regression_permanently_poisoned_once_without_accounting_poison() -> None:
    clock = _Clock(2.0)
    limiter = _limiter(clock)
    await limiter.acquire(_request())
    clock.value = 1.0

    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert limiter._clock_poisoned is True
    assert limiter._limiter_clock_epoch == 2
    assert limiter._limiter_accounting_generation == 1
    clock.value = 3.0
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert limiter._limiter_clock_epoch == 2


@pytest.mark.asyncio
async def test_index_corruption_permanently_poisoned_once_without_clock_poison() -> None:
    limiter = _limiter(_Clock())
    await limiter.acquire(_request())
    limiter._event_index.clear()

    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert limiter._accounting_poisoned is True
    assert limiter._limiter_accounting_generation == 2
    assert limiter._limiter_clock_epoch == 1
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert limiter._limiter_accounting_generation == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("event", "amount", True),
        ("event", "occurred_at_monotonic_ns", True),
        ("event", "expires_at_monotonic_ns", True),
        ("limiter", "_limiter_instance_id", UUID(int=0)),
        ("limiter", "_cooldown_epoch", True),
        ("limiter", "_blocked_until_monotonic_ns", True),
        ("limiter", "_capacity", True),
        ("limiter", "_product_capacity", True),
        ("limiter", "_rest_window_ns", True),
        ("limiter", "_last_now", True),
    ],
)
async def test_exact_scalar_corruption_is_detected_before_prune_or_snapshot(
    target: str,
    field: str,
    value: object,
) -> None:
    limiter = _limiter(_Clock())
    await limiter.acquire(_request())
    selected = limiter._rest_events[0] if target == "event" else limiter
    object.__setattr__(selected, field, value)

    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert limiter._accounting_poisoned is True
    assert limiter._limiter_accounting_generation == 2


@pytest.mark.asyncio
async def test_quota_observation_accepts_exact_keys_independent_of_dict_order() -> None:
    limiter = _limiter(_Clock())
    observation = limiter.observe_quota(
        {
            "remaining_time_in_milliseconds": 1,
            "current_quota": 2,
        }
    )
    assert observation.current_quota == 2
    assert observation.remaining_time_in_milliseconds == 1


@pytest.mark.asyncio
async def test_clock_source_exception_is_safely_and_permanently_poisoned() -> None:
    calls = 0

    def broken_clock() -> float:
        nonlocal calls
        calls += 1
        raise RuntimeError("fixture-secret-clock")

    limiter_type = import_module("hmt_crypto.exchange.delta.weighted_limiter").WeightedRateLimiter
    limiter = limiter_type(
        _scope(), policy=_policy(), monotonic=broken_clock, event_id_factory=_Ids()
    )
    with pytest.raises(ConfigurationError) as captured:
        await limiter.acquire(_request())
    assert "fixture-secret" not in str(captured.value)
    assert limiter._clock_poisoned is True
    assert limiter._limiter_clock_epoch == 2
    with pytest.raises(ConfigurationError):
        await limiter.acquire(_request())
    assert calls == 1


def test_foreign_thread_rejects_limiter_before_clock_request_or_uuid_callbacks() -> None:
    calls: list[str] = []

    def clock() -> float:
        calls.append("clock")
        return 0.0

    def identity() -> UUID:
        calls.append("uuid")
        return _uuid(len(calls))

    limiter_type = import_module("hmt_crypto.exchange.delta.weighted_limiter").WeightedRateLimiter
    limiter = limiter_type(_scope(), policy=_policy(), monotonic=clock, event_id_factory=identity)
    calls.clear()
    errors: list[BaseException] = []
    selected_request = _request()

    def foreign() -> None:
        operations = (
            lambda: limiter.scope,
            lambda: limiter.policy,
            lambda: limiter.coordination_mode,
            lambda: limiter._build_owned_charge_plan(selected_request),
            limiter._new_event_id,
            limiter._sample_monotonic,
            limiter._notify_head,
        )
        for operation in operations:
            try:
                operation()
            except BaseException as error:
                errors.append(error)

    thread = threading.Thread(target=foreign)
    thread.start()
    thread.join(timeout=1)
    assert len(errors) == 7 and all(isinstance(error, ConfigurationError) for error in errors)
    assert calls == []


def test_second_event_loop_rejects_before_clock_request_or_uuid_callbacks() -> None:
    clock = _Clock()
    ids = _Ids()
    limiter = _limiter(clock, ids=ids)
    asyncio.run(limiter.snapshot())
    next_identity = ids.next_value

    with pytest.raises(ConfigurationError):
        asyncio.run(limiter.acquire(_request()))
    assert ids.next_value == next_identity


@pytest.mark.asyncio
async def test_fork_mismatch_rejects_before_input_or_callback_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounting = import_module("hmt_crypto.exchange.delta.limiter_accounting")
    calls: list[str] = []

    def clock() -> float:
        calls.append("clock")
        return 0.0

    limiter_type = import_module("hmt_crypto.exchange.delta.weighted_limiter").WeightedRateLimiter
    limiter = limiter_type(_scope(), policy=_policy(), monotonic=clock, event_id_factory=_Ids())
    monkeypatch.setattr(accounting.os, "getpid", lambda: limiter._accounting_gate.owner_pid + 1)
    hostile = _request()
    object.__setattr__(hostile, "product_operations", object())

    with pytest.raises(ConfigurationError):
        await limiter.acquire(hostile)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reading", [True, -1.0, float("nan"), float("inf"), "0"])
async def test_invalid_clock_readings_poison_once_with_safe_error(reading: object) -> None:
    calls = 0

    def clock() -> object:
        nonlocal calls
        calls += 1
        return reading

    limiter_type = import_module("hmt_crypto.exchange.delta.weighted_limiter").WeightedRateLimiter
    limiter = limiter_type(  # type: ignore[arg-type]
        _scope(), policy=_policy(), monotonic=clock, event_id_factory=_Ids()
    )
    with pytest.raises(ConfigurationError) as captured:
        await limiter.acquire(_request())
    assert captured.value.context == {"operation": "monotonic_clock"}
    assert limiter._clock_poisoned is True
    assert limiter._limiter_clock_epoch == 2
    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert calls == 1


@pytest.mark.asyncio
async def test_hostile_corrupted_request_rejected_before_callbacks() -> None:
    calls: list[str] = []

    class HostileMapping(dict[object, object]):
        def items(self) -> object:
            calls.append("items")
            raise AssertionError("fixture-secret")

        def __iter__(self) -> Iterator[object]:
            calls.append("iter")
            raise AssertionError("fixture-secret")

    limiter = _limiter(_Clock())
    corrupted = _request()
    object.__setattr__(corrupted, "product_operations", HostileMapping())
    with pytest.raises(ConfigurationError) as captured:
        await limiter.acquire(corrupted)
    assert calls == []
    assert "fixture-secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_every_valid_429_advances_epoch_without_shortening_deadline() -> None:
    clock = _Clock(1.0)
    limiter = _limiter(clock)
    await limiter.observe_429("1000")
    original = limiter._blocked_until_monotonic_ns
    await limiter.observe_429("0")
    await limiter.observe_429("500")

    assert limiter._cooldown_epoch == 3
    assert limiter._blocked_until_monotonic_ns == original


class _BarrierLimiter:
    @staticmethod
    def create(clock: _Clock, ids: _Ids) -> object:
        base = import_module("hmt_crypto.exchange.delta.weighted_limiter").WeightedRateLimiter

        class BarrierLimiter(base):
            def __init__(self) -> None:
                super().__init__(
                    _scope(), policy=_policy(capacity=1), monotonic=clock, event_id_factory=ids
                )
                self.before_entered = asyncio.Event()
                self.before_release = asyncio.Event()
                self.after_entered = asyncio.Event()
                self.after_release = asyncio.Event()
                self.block_before = False
                self.block_after = False

            async def _before_grant_entry_barrier(self) -> None:
                if self.block_before:
                    self.before_entered.set()
                    await self.before_release.wait()

            async def _after_grant_release_before_delivery_barrier(self) -> None:
                if self.block_after:
                    self.after_entered.set()
                    await self.after_release.wait()

        return BarrierLimiter()


@pytest.mark.asyncio
async def test_cancellation_before_grant_discards_ids_and_charges_nothing() -> None:
    successor_preallocated = asyncio.Event()
    ids = _Ids()
    ids.signal = (3, successor_preallocated)
    limiter = _BarrierLimiter.create(_Clock(), ids)
    limiter.block_before = True
    task = asyncio.create_task(limiter.acquire(_request()))
    await asyncio.wait_for(limiter.before_entered.wait(), timeout=1)
    limiter.before_entered.clear()
    successor = asyncio.create_task(limiter.acquire(_request()))
    await asyncio.wait_for(successor_preallocated.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    await asyncio.wait_for(limiter.before_entered.wait(), timeout=1)

    assert not limiter._rest_events
    assert not limiter._event_index
    assert len(limiter._waiters) == 1
    limiter.before_release.set()
    permit = await asyncio.wait_for(successor, timeout=1)
    assert permit.accounting is not None
    assert permit.accounting.rest_event_id == _uuid(3)
    assert ids.next_value == 4


@pytest.mark.asyncio
async def test_cancellation_after_charge_never_refunds_or_reacquires() -> None:
    clock = _Clock()
    successor_preallocated = asyncio.Event()
    ids = _Ids()
    ids.signal = (3, successor_preallocated)
    limiter = _BarrierLimiter.create(clock, ids)
    limiter.block_before = True
    limiter.block_after = True
    task = asyncio.create_task(limiter.acquire(_request()))
    await asyncio.wait_for(limiter.before_entered.wait(), timeout=1)
    successor = asyncio.create_task(limiter.acquire(_request()))
    await asyncio.wait_for(successor_preallocated.wait(), timeout=1)
    limiter.before_release.set()
    await asyncio.wait_for(limiter.after_entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    limiter.block_after = False
    assert len(limiter._rest_events) == 1
    assert len(limiter._event_index) == 1
    assert len(limiter._waiters) == 1
    assert (await limiter.snapshot()).rest_used == 1
    assert limiter._rest_events[0].event_id == _uuid(2)
    clock.value = 300.0
    await limiter.observe_429("0")
    permit = await asyncio.wait_for(successor, timeout=1)
    assert permit.accounting is not None
    assert permit.accounting.rest_event_id == _uuid(3)
    assert ids.next_value == 4


@pytest.mark.asyncio
async def test_request_and_policy_corruption_after_admission_cannot_change_charge() -> None:
    clock = _Clock()
    limiter = _BarrierLimiter.create(clock, _Ids())
    limiter.block_before = True
    rate_request = _request()
    returned_policy = limiter.policy
    task = asyncio.create_task(limiter.acquire(rate_request))
    await asyncio.wait_for(limiter.before_entered.wait(), timeout=1)
    object.__setattr__(rate_request, "route", policy_api.RateLimitRoute.ORDER_HISTORY)
    object.__setattr__(rate_request, "product_operations", {7: 500})
    object.__setattr__(returned_policy, "india_capacity", 1)
    limiter.before_release.set()

    permit = await asyncio.wait_for(task, timeout=1)
    assert permit.rest_weight == 1
    assert dict(permit.product_operations) == {}
    assert limiter.policy.india_capacity == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "rest_container",
        "empty_product_category",
        "index_event_identity",
        "index_category",
        "expiry_delta",
        "event_order",
    ],
)
async def test_event_index_corruption_matrix_poisoned_without_pruning(
    corruption: str,
) -> None:
    clock = _Clock()
    limiter = _limiter(clock)
    await limiter.acquire(_request(products={7: 1}))
    first = limiter._rest_events[0]
    if corruption == "rest_container":
        limiter._rest_events = list(limiter._rest_events)
    elif corruption == "empty_product_category":
        limiter._product_events[7] = deque()
    elif corruption == "index_event_identity":
        indexed = limiter._event_index[first.event_id]
        limiter._event_index[first.event_id] = replace(indexed, event=replace(first))
    elif corruption == "index_category":
        indexed = limiter._event_index[first.event_id]
        limiter._event_index[first.event_id] = replace(indexed, product_id=7)
    elif corruption == "expiry_delta":
        object.__setattr__(first, "expires_at_monotonic_ns", 300_000_000_001)
    else:
        clock.value = 1.0
        await limiter.acquire(_request(products={8: 1}))
        limiter._rest_events.reverse()
    before = len(limiter._event_index)

    with pytest.raises(ConfigurationError):
        await limiter.snapshot()
    assert limiter._accounting_poisoned is True
    assert len(limiter._event_index) == before


def test_accounting_helper_import_graph_has_no_authority_or_second_owner() -> None:
    accounting = import_module("hmt_crypto.exchange.delta.limiter_accounting")
    source_path = Path(accounting.__file__ or "")
    tree = ast.parse(source_path.read_text())
    imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    denied = {
        "httpx",
        "logging",
        "socket",
        "sqlalchemy",
        "private_limiter_registry",
        "private_limiter_completion",
        "private_dispatch",
    }
    assert not any(
        imported == target or imported.endswith(f".{target}")
        for imported in imports
        for target in denied
    )
    assert accounting.__all__ == []
    assert not hasattr(accounting, "acquire")
    assert not hasattr(accounting, "refund")
