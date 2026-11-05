"""FIFO Delta quota scheduler with identified nanosecond accounting."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

from hmt_crypto.errors import ConfigurationError, RateLimitError
from hmt_crypto.exchange.delta.limiter_accounting import (
    _accounting_delay_seconds,
    _assert_event_index_integrity,
    _canonical_route,
    _consume_accounting_plan,
    _IndexedQuotaAccountingEvent,
    _LimiterAccountingGate,
    _LimiterChargePlan,
    _OwnedLimiterConfiguration,
    _PreallocatedQuotaEventIds,
    _prune_accounting_events,
    _QuotaAccountingEvent,
)
from hmt_crypto.exchange.delta.rate_limit_policy import (
    _CANCELLATION_ROUTES,
    _PRODUCT_OPERATION_ROUTES,
    DELTA_RATE_LIMIT_POLICY,
    MAX_RATE_LIMIT_RESET_MS,
    CoordinationMode,
    QuotaObservation,
    RateLimitPolicy,
    RateLimitScope,
    RateLimitSnapshot,
    RatePermit,
    RateRequest,
    parse_rate_limit_reset_ms,
    strict_nonnegative_int,
)

_NS_PER_SECOND = 1_000_000_000


@dataclass(slots=True, eq=False)
class _Waiter:
    plan: _LimiterChargePlan
    event_ids: _PreallocatedQuotaEventIds
    event: asyncio.Event


class WeightedRateLimiter:
    """FIFO, cancellation-safe quota owner for one exact Delta scope."""

    def __init__(
        self,
        scope: RateLimitScope,
        *,
        policy: RateLimitPolicy = DELTA_RATE_LIMIT_POLICY,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        event_id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._accounting_gate = _LimiterAccountingGate()
        owned = _OwnedLimiterConfiguration.from_inputs(scope, policy)
        if monotonic is not None and not callable(monotonic):
            raise ValueError("monotonic must be callable")
        if sleeper is not None and not callable(sleeper):
            raise ValueError("sleeper must be callable")
        if event_id_factory is not None and not callable(event_id_factory):
            raise ValueError("event_id_factory must be callable")

        self._owned_configuration = owned
        self._policy_version = owned.policy_version
        self._capacity = owned.capacity
        self._rest_window_ns = owned.rest_window_seconds * _NS_PER_SECOND
        self._product_capacity = owned.product_capacity
        self._route_weights = dict(owned.route_weights)
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or asyncio.sleep
        self._event_id_factory = event_id_factory or uuid4
        self._limiter_instance_id = self._new_event_id()

        self._lock = asyncio.Lock()
        self._rest_events: deque[_QuotaAccountingEvent] = deque()
        self._product_events: dict[int, deque[_QuotaAccountingEvent]] = {}
        self._event_index: dict[UUID, _IndexedQuotaAccountingEvent] = {}
        self._waiters: deque[_Waiter] = deque()
        self._limiter_clock_epoch = 1
        self._limiter_accounting_generation = 1
        self._cooldown_epoch = 0
        self._blocked_until_monotonic_ns = 0
        self._last_now: float | None = None
        self._clock_poisoned = False
        self._accounting_poisoned = False
        self._closed = False

    @property
    def scope(self) -> RateLimitScope:
        """Return a fresh defensive copy of the captured scope."""
        self._assert_context()
        return self._owned_configuration.scope_snapshot()

    @property
    def policy(self) -> RateLimitPolicy:
        """Return a fresh defensive copy of the captured quota policy."""
        self._assert_context()
        return self._owned_configuration.policy_snapshot()

    @property
    def coordination_mode(self) -> CoordinationMode:
        """Return the deliberately in-process-only coordination mode."""
        self._assert_context()
        return CoordinationMode.IN_PROCESS_ONLY

    def _bind_async_entry(self) -> None:
        self._accounting_gate.bind_or_assert_running_loop(asyncio.get_running_loop())

    def _assert_context(self) -> None:
        self._accounting_gate.assert_bound_sync_context()

    def _preflight_integrity(self, *, allow_closed: bool) -> None:
        self._assert_context()
        with self._accounting_gate.hold():
            self._assert_accounting_integrity_locked()
            if self._clock_poisoned is True:
                raise ConfigurationError(context={"operation": "monotonic_clock"})
            if self._closed is True and not allow_closed:
                raise RateLimitError(context={"operation": "rate_limit_acquire", "state": "closed"})

    def _new_event_id(self) -> UUID:
        self._assert_context()
        try:
            identity = self._event_id_factory()
        except Exception:
            raise ConfigurationError(context={"operation": "rate_limit_accounting"}) from None
        if type(identity) is not UUID or identity.int == 0:
            raise ConfigurationError(context={"operation": "rate_limit_accounting"})
        return identity

    def _preallocate_event_ids(self, plan: _LimiterChargePlan) -> _PreallocatedQuotaEventIds:
        self._assert_context()
        try:
            return _PreallocatedQuotaEventIds(
                rest_event_id=self._new_event_id(),
                product_event_ids=tuple(
                    (product_id, self._new_event_id()) for product_id, _ in plan.product_operations
                ),
            )
        except (ValueError, ConfigurationError):
            raise ConfigurationError(context={"operation": "rate_limit_accounting"}) from None

    def _build_owned_charge_plan(self, request: RateRequest) -> _LimiterChargePlan:
        self._assert_context()
        if type(request) is not RateRequest:
            raise ConfigurationError(context={"operation": "rate_limit_acquire"})
        route = request.route
        products = request._product_operation_items
        if _canonical_route(route) is None or route is not request._route_owner:
            raise ConfigurationError(context={"operation": "rate_limit_acquire"})
        if (
            request.product_operations is not request._product_operations_owner
            or type(products) is not tuple
        ):
            raise ConfigurationError(context={"operation": "rate_limit_acquire"})
        previous = 0
        for item in products:
            if type(item) is not tuple or len(item) != 2:
                raise ConfigurationError(context={"operation": "rate_limit_acquire"})
            product_id, count = item
            if (
                type(product_id) is not int
                or product_id <= previous
                or type(count) is not int
                or count <= 0
                or count > self._product_capacity
            ):
                raise ConfigurationError(context={"operation": "rate_limit_acquire"})
            previous = product_id
        if (route in _PRODUCT_OPERATION_ROUTES and not products) or (
            route in _CANCELLATION_ROUTES and products
        ):
            raise ConfigurationError(context={"operation": "rate_limit_acquire"})
        weight = self._route_weights[route]
        if weight > self._capacity:
            raise ConfigurationError(context={"operation": "rate_limit_acquire"})
        return _LimiterChargePlan(route=route, rest_weight=weight, product_operations=products)

    @staticmethod
    def _float_monotonic_to_ns(value: float) -> int:
        if type(value) is not float or not math.isfinite(value) or value < 0:
            raise ConfigurationError(context={"operation": "monotonic_clock"})
        numerator, denominator = value.as_integer_ratio()
        return numerator * _NS_PER_SECOND // denominator

    def _sample_monotonic(self) -> tuple[float, int]:
        self._assert_context()
        try:
            raw = self._monotonic()
            if type(raw) not in (int, float):
                raise ValueError
            now = float(raw)
            now_ns = self._float_monotonic_to_ns(now)
        except Exception:
            with self._accounting_gate.hold():
                self._poison_clock_locked()
            raise ConfigurationError(context={"operation": "monotonic_clock"}) from None
        return now, now_ns

    def _accept_clock_sample_locked(self, now: float, now_ns: int) -> None:
        self._accounting_gate.assert_held()
        clock_poisoned: object = self._clock_poisoned
        if type(clock_poisoned) is not bool:
            self._poison_accounting_locked()
            raise ConfigurationError(context={"operation": "rate_limit_accounting"})
        if clock_poisoned is True:
            raise ConfigurationError(context={"operation": "monotonic_clock"})
        if type(now) is not float or type(now_ns) is not int or now_ns < 0:
            self._poison_clock_locked()
            raise ConfigurationError(context={"operation": "monotonic_clock"})
        last_now = self._last_now
        if last_now is not None and (
            type(last_now) is not float or not math.isfinite(last_now) or last_now < 0
        ):
            self._poison_accounting_locked()
            raise ConfigurationError(context={"operation": "rate_limit_accounting"})
        if last_now is not None and now < last_now:
            self._poison_clock_locked()
            raise ConfigurationError(context={"operation": "monotonic_clock"})
        self._last_now = now

    def _poison_clock_locked(self) -> None:
        self._accounting_gate.assert_held()
        epoch: object = self._limiter_clock_epoch
        if type(self._clock_poisoned) is not bool or self._clock_poisoned is False:
            next_epoch = epoch + 1 if type(epoch) is int and epoch > 0 else 2
            self._clock_poisoned, self._limiter_clock_epoch = True, next_epoch

    def _poison_accounting_locked(self) -> None:
        self._accounting_gate.assert_held()
        generation: object = self._limiter_accounting_generation
        if type(self._accounting_poisoned) is not bool or self._accounting_poisoned is False:
            next_generation = generation + 1 if type(generation) is int and generation > 0 else 2
            self._accounting_poisoned, self._limiter_accounting_generation = True, next_generation

    def _assert_accounting_integrity_locked(self) -> None:
        self._accounting_gate.assert_held()
        accounting_poisoned: object = self._accounting_poisoned
        if type(accounting_poisoned) is not bool:
            self._poison_accounting_locked()
            raise ConfigurationError(context={"operation": "rate_limit_accounting"})
        if accounting_poisoned is True:
            raise ConfigurationError(context={"operation": "rate_limit_accounting"})
        try:
            self._check_accounting_integrity()
        except Exception:
            self._poison_accounting_locked()
            raise ConfigurationError(context={"operation": "rate_limit_accounting"}) from None

    def _check_accounting_integrity(self) -> None:
        self._accounting_gate.assert_held()
        _assert_event_index_integrity(
            rest_events=self._rest_events,
            product_events=self._product_events,
            event_index=self._event_index,
            limiter_instance_id=self._limiter_instance_id,
            rest_window_ns=self._rest_window_ns,
            capacity=self._capacity,
            product_capacity=self._product_capacity,
            clock_epoch=self._limiter_clock_epoch,
            accounting_generation=self._limiter_accounting_generation,
            cooldown_epoch=self._cooldown_epoch,
            blocked_until_ns=self._blocked_until_monotonic_ns,
            last_now=self._last_now,
            closed=self._closed,
            clock_poisoned=self._clock_poisoned,
            accounting_poisoned=self._accounting_poisoned,
        )

    def _prune_locked(self, now_ns: int) -> None:
        self._accounting_gate.assert_held()
        self._assert_accounting_integrity_locked()
        _prune_accounting_events(
            rest_events=self._rest_events,
            product_events=self._product_events,
            event_index=self._event_index,
            now_ns=now_ns,
        )
        if self._blocked_until_monotonic_ns <= now_ns:
            self._blocked_until_monotonic_ns = 0
        self._assert_accounting_integrity_locked()

    def _delay_for_locked(self, plan: _LimiterChargePlan, now_ns: int) -> float:
        self._accounting_gate.assert_held()
        return _accounting_delay_seconds(
            plan=plan,
            rest_events=self._rest_events,
            product_events=self._product_events,
            capacity=self._capacity,
            product_capacity=self._product_capacity,
            blocked_until_ns=self._blocked_until_monotonic_ns,
            now_ns=now_ns,
        )

    def _consume_locked(
        self,
        plan: _LimiterChargePlan,
        *,
        acquired_at: float,
        acquired_monotonic_ns: int,
        event_ids: _PreallocatedQuotaEventIds,
    ) -> RatePermit:
        self._accounting_gate.assert_held()
        try:
            permit = _consume_accounting_plan(
                plan=plan,
                event_ids=event_ids,
                rest_events=self._rest_events,
                product_events=self._product_events,
                event_index=self._event_index,
                acquired_at=acquired_at,
                acquired_ns=acquired_monotonic_ns,
                rest_window_ns=self._rest_window_ns,
                limiter_instance_id=self._limiter_instance_id,
                clock_epoch=self._limiter_clock_epoch,
                accounting_generation=self._limiter_accounting_generation,
                cooldown_epoch=self._cooldown_epoch,
            )
        except (TypeError, ValueError):
            self._poison_accounting_locked()
            raise ConfigurationError(context={"operation": "rate_limit_accounting"}) from None
        self._assert_accounting_integrity_locked()
        return permit

    def _notify_head(self) -> None:
        self._assert_context()
        if self._waiters:
            self._waiters[0].event.set()

    async def _before_grant_entry_barrier(self) -> None:
        self._assert_context()
        return None

    async def _after_grant_release_before_delivery_barrier(self) -> None:
        self._assert_context()
        return None

    async def _sleep_or_notification(self, waiter: _Waiter, delay: float) -> None:
        self._assert_context()
        sleep_task = asyncio.ensure_future(self._sleeper(delay))
        notification_task = asyncio.create_task(waiter.event.wait())
        tasks = {sleep_task, notification_task}
        try:
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if sleep_task.done() and not sleep_task.cancelled():
                exception = sleep_task.exception()
                if exception is not None:
                    raise exception
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def acquire(self, request: RateRequest) -> RatePermit:
        """Acquire route and product quota atomically in strict FIFO order."""
        self._bind_async_entry()
        self._preflight_integrity(allow_closed=False)
        plan = self._build_owned_charge_plan(request)
        event_ids = self._preallocate_event_ids(plan)
        initial_sample = self._sample_monotonic()
        waiter = _Waiter(plan=plan, event_ids=event_ids, event=asyncio.Event())
        async with self._lock:
            with self._accounting_gate.hold():
                self._accept_clock_sample_locked(*initial_sample)
                self._assert_accounting_integrity_locked()
                if self._closed is True:
                    raise RateLimitError(
                        context={"operation": "rate_limit_acquire", "state": "closed"}
                    )
                self._waiters.append(waiter)
        self._notify_head()
        try:
            while True:
                await waiter.event.wait()
                waiter.event.clear()
                sample = self._sample_monotonic()
                async with self._lock:
                    with self._accounting_gate.hold():
                        self._accept_clock_sample_locked(*sample)
                        self._assert_accounting_integrity_locked()
                        if self._closed is True:
                            raise RateLimitError(
                                context={"operation": "rate_limit_acquire", "state": "closed"}
                            )
                        is_head = bool(self._waiters and self._waiters[0] is waiter)
                        if is_head:
                            self._prune_locked(sample[1])
                            delay = self._delay_for_locked(plan, sample[1])
                        else:
                            delay = 0.0
                if not is_head:
                    continue
                if delay > 0:
                    await self._sleep_or_notification(waiter, delay)
                    waiter.event.set()
                    continue

                self._assert_context()
                await self._before_grant_entry_barrier()
                sample = self._sample_monotonic()
                permit: RatePermit | None = None
                async with self._lock:
                    with self._accounting_gate.hold():
                        self._accept_clock_sample_locked(*sample)
                        self._assert_accounting_integrity_locked()
                        if self._closed is True:
                            raise RateLimitError(
                                context={"operation": "rate_limit_acquire", "state": "closed"}
                            )
                        if self._waiters and self._waiters[0] is waiter:
                            self._prune_locked(sample[1])
                            delay = self._delay_for_locked(plan, sample[1])
                            if delay <= 0:
                                permit = self._consume_locked(
                                    plan,
                                    acquired_at=sample[0],
                                    acquired_monotonic_ns=sample[1],
                                    event_ids=event_ids,
                                )
                                self._waiters.popleft()
                if permit is not None:
                    self._notify_head()
                    self._assert_context()
                    await self._after_grant_release_before_delivery_barrier()
                    return permit
                if delay > 0:
                    await self._sleep_or_notification(waiter, delay)
                    waiter.event.set()
        except BaseException:
            removed_head = False
            async with self._lock:
                with self._accounting_gate.hold():
                    if waiter in self._waiters:
                        removed_head = self._waiters[0] is waiter
                        self._waiters.remove(waiter)
            if removed_head:
                self._notify_head()
            raise

    async def observe_429(self, reset_header: str) -> None:
        """Extend the local cooldown without crediting quota."""
        self._bind_async_entry()
        self._preflight_integrity(allow_closed=False)
        reset_ms = parse_rate_limit_reset_ms(reset_header)
        sample = self._sample_monotonic()
        async with self._lock:
            with self._accounting_gate.hold():
                self._accept_clock_sample_locked(*sample)
                self._assert_accounting_integrity_locked()
                self._cooldown_epoch += 1
                deadline = sample[1] + reset_ms * 1_000_000
                self._blocked_until_monotonic_ns = max(self._blocked_until_monotonic_ns, deadline)
        self._notify_head()

    def observe_quota(self, payload: Mapping[str, object]) -> QuotaObservation:
        """Validate telemetry without changing any local capacity."""
        self._accounting_gate.assert_bound_sync_context()
        self._preflight_integrity(allow_closed=True)
        if type(payload) is not dict:
            raise ConfigurationError(context={"operation": "rate_limit_quota_observation"})
        keys = tuple(payload)
        if (
            len(keys) != 2
            or any(type(key) is not str for key in keys)
            or frozenset(keys) != frozenset({"current_quota", "remaining_time_in_milliseconds"})
        ):
            raise ConfigurationError(context={"operation": "rate_limit_quota_observation"})
        current_value = payload["current_quota"]
        remaining_value = payload["remaining_time_in_milliseconds"]
        if type(current_value) is not int or type(remaining_value) is not int:
            raise ConfigurationError(context={"operation": "rate_limit_quota_observation"})
        current = strict_nonnegative_int(current_value, name="current_quota")
        remaining = strict_nonnegative_int(
            remaining_value,
            name="remaining_time_in_milliseconds",
        )
        if remaining > MAX_RATE_LIMIT_RESET_MS:
            raise ConfigurationError(context={"operation": "rate_limit_quota_observation"})
        sample = self._sample_monotonic()
        with self._accounting_gate.hold():
            self._accept_clock_sample_locked(*sample)
            self._assert_accounting_integrity_locked()
        return QuotaObservation(current, remaining, sample[0])

    async def snapshot(self) -> RateLimitSnapshot:
        """Return current counters without changing quota capacity."""
        self._bind_async_entry()
        self._preflight_integrity(allow_closed=True)
        sample = self._sample_monotonic()
        async with self._lock:
            with self._accounting_gate.hold():
                self._accept_clock_sample_locked(*sample)
                self._prune_locked(sample[1])
                values = (
                    sum(event.amount for event in self._rest_events),
                    {
                        product_id: sum(event.amount for event in events)
                        for product_id, events in self._product_events.items()
                    },
                    self._blocked_until_monotonic_ns,
                    len(self._waiters),
                    self._closed,
                )
        used, products, blocked_ns, waiters, closed = values
        return RateLimitSnapshot(
            policy_version=self._policy_version,
            capacity=self._capacity,
            rest_used=used,
            rest_remaining=self._capacity - used,
            blocked_until=blocked_ns / _NS_PER_SECOND if blocked_ns else None,
            waiter_count=waiters,
            product_operations_used=products,
            closed=closed,
        )

    async def close(self) -> None:
        """Close the limiter and release every queued waiter."""
        self._bind_async_entry()
        async with self._lock:
            with self._accounting_gate.hold():
                self._closed = True
                waiters = tuple(self._waiters)
        for waiter in waiters:
            waiter.event.set()


class LimiterRegistry:
    """In-process owner of exactly one limiter instance per external scope."""

    def __init__(self) -> None:
        self._limiters: dict[RateLimitScope, WeightedRateLimiter] = {}

    @property
    def coordination_mode(self) -> CoordinationMode:
        """Return the registry's deliberately process-local coordination mode."""
        return CoordinationMode.IN_PROCESS_ONLY

    def get_or_create(
        self, scope: RateLimitScope, factory: Callable[[], WeightedRateLimiter]
    ) -> WeightedRateLimiter:
        """Return the existing limiter for ``scope`` or register a factory result."""
        if type(scope) is not RateLimitScope or not callable(factory):
            raise ConfigurationError(context={"operation": "rate_limit_registry"})
        existing = self._limiters.get(scope)
        if existing is not None:
            return existing
        created = factory()
        if not isinstance(created, WeightedRateLimiter) or created.scope != scope:
            raise ConfigurationError(context={"operation": "rate_limit_registry"})
        self._limiters[scope] = created
        return created

    def register_existing(
        self, scope: RateLimitScope, limiter: WeightedRateLimiter
    ) -> WeightedRateLimiter:
        """Register one existing limiter while rejecting conflicting ownership."""
        if type(scope) is not RateLimitScope or not isinstance(limiter, WeightedRateLimiter):
            raise ConfigurationError(context={"operation": "rate_limit_registry"})
        if limiter.scope != scope:
            raise ConfigurationError(context={"operation": "rate_limit_registry"})
        existing = self._limiters.get(scope)
        if existing is not None and existing is not limiter:
            raise ConfigurationError(
                context={"operation": "rate_limit_registry", "state": "duplicate"}
            )
        self._limiters[scope] = limiter
        return limiter

    def assert_durable_live_coordination(self, scope: RateLimitScope) -> None:
        """Fail closed because this registry cannot coordinate across processes."""
        if type(scope) is not RateLimitScope:
            raise ConfigurationError(context={"operation": "durable_rate_limit_coordination"})
        raise ConfigurationError(
            context={
                "operation": "durable_rate_limit_coordination",
                "scope": scope.kind.value,
                "region": scope.region,
                "environment": scope.environment,
            }
        )


__all__ = ["LimiterRegistry", "WeightedRateLimiter"]
