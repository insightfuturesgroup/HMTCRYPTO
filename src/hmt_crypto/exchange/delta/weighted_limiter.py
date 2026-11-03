"""FIFO asynchronous Delta quota scheduler and in-process scope registry."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from hmt_crypto.errors import ConfigurationError, RateLimitError
from hmt_crypto.exchange.delta.rate_limit_policy import (
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
    strict_monotonic_value,
    strict_nonnegative_int,
)


@dataclass(slots=True, eq=False)
class _Waiter:
    request: RateRequest
    event: asyncio.Event


class WeightedRateLimiter:
    """FIFO, cancellation-safe local limiter for one immutable Delta quota scope."""

    def __init__(
        self,
        scope: RateLimitScope,
        *,
        policy: RateLimitPolicy = DELTA_RATE_LIMIT_POLICY,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not isinstance(scope, RateLimitScope):
            raise ValueError("scope must be a RateLimitScope")
        if not isinstance(policy, RateLimitPolicy):
            raise ValueError("policy must be a RateLimitPolicy")
        self._scope = scope
        self._policy = policy
        self._monotonic = monotonic or __import__("time").monotonic
        self._sleeper = sleeper or asyncio.sleep
        self._lock = asyncio.Lock()
        self._rest_events: deque[tuple[float, int]] = deque()
        self._product_events: dict[int, deque[tuple[float, int]]] = {}
        self._waiters: deque[_Waiter] = deque()
        self._blocked_until = 0.0
        self._closed = False
        self._last_now: float | None = None

    @property
    def scope(self) -> RateLimitScope:
        """Return the immutable registry key captured at construction."""
        return self._scope

    @property
    def policy(self) -> RateLimitPolicy:
        """Return the immutable policy snapshot captured at construction."""
        return self._policy

    @property
    def coordination_mode(self) -> CoordinationMode:
        """Return the deliberately non-durable Phase 1 coordination mode."""
        return CoordinationMode.IN_PROCESS_ONLY

    def _now(self) -> float:
        now = strict_monotonic_value(self._monotonic())
        if self._last_now is not None and now < self._last_now:
            raise ConfigurationError(context={"operation": "monotonic_clock"})
        self._last_now = now
        return now

    def _capacity(self) -> int:
        return self._policy.capacity_for(self._scope.region)

    def _prune(self, now: float) -> None:
        rest_cutoff = now - self._policy.rest_window_seconds
        while self._rest_events and self._rest_events[0][0] <= rest_cutoff:
            self._rest_events.popleft()
        product_cutoff = now - 1.0
        for product_id, events in tuple(self._product_events.items()):
            while events and events[0][0] <= product_cutoff:
                events.popleft()
            if not events:
                del self._product_events[product_id]
        if self._blocked_until <= now:
            self._blocked_until = 0.0

    @staticmethod
    def _event_delay(
        events: deque[tuple[float, int]],
        *,
        used: int,
        requested: int,
        capacity: int,
        window_seconds: float,
        now: float,
    ) -> float:
        required = used + requested - capacity
        if required <= 0:
            return 0.0
        freed = 0
        for occurred_at, amount in events:
            freed += amount
            if freed >= required:
                return max(0.0, occurred_at + window_seconds - now)
        raise ConfigurationError(context={"operation": "rate_limit_accounting"})

    def _delay_for(self, request: RateRequest, now: float) -> float:
        rest_used = sum(weight for _, weight in self._rest_events)
        rest_delay = self._event_delay(
            self._rest_events,
            used=rest_used,
            requested=self._policy.weight_for(request.route),
            capacity=self._capacity(),
            window_seconds=float(self._policy.rest_window_seconds),
            now=now,
        )
        product_delay = 0.0
        for product_id, requested in request.product_operations.items():
            events = self._product_events.get(product_id, deque())
            used = sum(count for _, count in events)
            product_delay = max(
                product_delay,
                self._event_delay(
                    events,
                    used=used,
                    requested=requested,
                    capacity=self._policy.product_operations_per_second,
                    window_seconds=1.0,
                    now=now,
                ),
            )
        return max(rest_delay, product_delay, max(0.0, self._blocked_until - now))

    def _consume(self, request: RateRequest, now: float) -> RatePermit:
        weight = self._policy.weight_for(request.route)
        self._rest_events.append((now, weight))
        for product_id, count in request.product_operations.items():
            self._product_events.setdefault(product_id, deque()).append((now, count))
        return RatePermit(
            acquired_at=now,
            rest_weight=weight,
            product_operations=request.product_operations,
        )

    def _validate_request_capacity(self, request: RateRequest) -> None:
        if not isinstance(request, RateRequest):
            raise ConfigurationError(context={"operation": "rate_limit_acquire"})
        if self._policy.weight_for(request.route) > self._capacity():
            raise ConfigurationError(context={"operation": "rate_limit_acquire"})
        if any(
            count > self._policy.product_operations_per_second
            for count in request.product_operations.values()
        ):
            raise ConfigurationError(context={"operation": "rate_limit_acquire"})

    def _notify_head(self) -> None:
        if self._waiters:
            self._waiters[0].event.set()

    async def _sleep_or_notification(self, waiter: _Waiter, delay: float) -> None:
        async def wait_for_notification() -> None:
            await waiter.event.wait()

        sleep_task: asyncio.Future[None] = asyncio.ensure_future(self._sleeper(delay))
        notification_task: asyncio.Future[None] = asyncio.ensure_future(wait_for_notification())
        tasks: set[asyncio.Future[None]] = {sleep_task, notification_task}
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
        """Acquire route and product capacity atomically in strict FIFO order."""
        self._validate_request_capacity(request)
        waiter = _Waiter(request=request, event=asyncio.Event())
        async with self._lock:
            self._now()
            if self._closed:
                raise RateLimitError(context={"operation": "rate_limit_acquire", "state": "closed"})
            self._waiters.append(waiter)
            self._notify_head()
        try:
            while True:
                await waiter.event.wait()
                waiter.event.clear()
                async with self._lock:
                    now = self._now()
                    if self._closed:
                        raise RateLimitError(
                            context={"operation": "rate_limit_acquire", "state": "closed"}
                        )
                    if not self._waiters or self._waiters[0] is not waiter:
                        continue
                    self._prune(now)
                    delay = self._delay_for(request, now)
                    if delay <= 0:
                        permit = self._consume(request, now)
                        self._waiters.popleft()
                        self._notify_head()
                        return permit
                await self._sleep_or_notification(waiter, delay)
                waiter.event.set()
        except BaseException:
            async with self._lock:
                if waiter in self._waiters:
                    was_head = self._waiters[0] is waiter
                    self._waiters.remove(waiter)
                    if was_head:
                        self._notify_head()
            raise

    async def observe_429(self, reset_header: str) -> None:
        """Extend the local block from Delta's millisecond reset header."""
        reset_ms = parse_rate_limit_reset_ms(reset_header)
        async with self._lock:
            now = self._now()
            self._blocked_until = max(self._blocked_until, now + reset_ms / 1000)
            self._notify_head()

    def observe_quota(self, payload: Mapping[str, object]) -> QuotaObservation:
        """Validate quota telemetry without crediting or mutating local capacity."""
        if not isinstance(payload, Mapping) or set(payload) != {
            "current_quota",
            "remaining_time_in_milliseconds",
        }:
            raise ConfigurationError(context={"operation": "rate_limit_quota_observation"})
        current = strict_nonnegative_int(payload["current_quota"], name="current_quota")
        remaining = strict_nonnegative_int(
            payload["remaining_time_in_milliseconds"],
            name="remaining_time_in_milliseconds",
        )
        if remaining > MAX_RATE_LIMIT_RESET_MS:
            raise ConfigurationError(context={"operation": "rate_limit_quota_observation"})
        return QuotaObservation(
            current_quota=current,
            remaining_time_in_milliseconds=remaining,
            observed_at=self._now(),
        )

    async def snapshot(self) -> RateLimitSnapshot:
        """Return current nonnegative counters without changing quota capacity."""
        async with self._lock:
            now = self._now()
            self._prune(now)
            capacity = self._capacity()
            used = sum(weight for _, weight in self._rest_events)
            products = {
                product_id: sum(count for _, count in events)
                for product_id, events in self._product_events.items()
            }
            return RateLimitSnapshot(
                policy_version=self._policy.version,
                capacity=capacity,
                rest_used=used,
                rest_remaining=capacity - used,
                blocked_until=self._blocked_until or None,
                waiter_count=len(self._waiters),
                product_operations_used=products,
                closed=self._closed,
            )

    async def close(self) -> None:
        """Close the limiter and deterministically release every queued waiter."""
        async with self._lock:
            self._closed = True
            for waiter in self._waiters:
                waiter.event.set()


class LimiterRegistry:
    """In-process owner of exactly one limiter instance per external scope."""

    def __init__(self) -> None:
        self._limiters: dict[RateLimitScope, WeightedRateLimiter] = {}

    @property
    def coordination_mode(self) -> CoordinationMode:
        """Return the explicit Phase 1 coordination limitation."""
        return CoordinationMode.IN_PROCESS_ONLY

    def get_or_create(
        self,
        scope: RateLimitScope,
        factory: Callable[[], WeightedRateLimiter],
    ) -> WeightedRateLimiter:
        """Return the shared limiter, creating it exactly once for this registry."""
        if not isinstance(scope, RateLimitScope) or not callable(factory):
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
        """Register an existing shared limiter or reject an independent duplicate."""
        if (
            not isinstance(scope, RateLimitScope)
            or not isinstance(limiter, WeightedRateLimiter)
            or limiter.scope != scope
        ):
            raise ConfigurationError(context={"operation": "rate_limit_registry"})
        existing = self._limiters.get(scope)
        if existing is not None and existing is not limiter:
            raise ConfigurationError(
                context={"operation": "rate_limit_registry", "state": "duplicate"}
            )
        self._limiters[scope] = limiter
        return limiter

    def assert_durable_live_coordination(self, scope: RateLimitScope) -> None:
        """Fail closed because durable cross-process leadership starts in Phase 2."""
        if not isinstance(scope, RateLimitScope):
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
