"""Central Delta quota accounting and operation-safe retry classification.

Delta documents fixed five-minute quotas but not their server-side alignment.  The
local limiter therefore uses a conservative rolling 300-second window: it may wait
longer than the exchange, but it cannot knowingly grant more work than the documented
quota.  Phase 1 coordination is deliberately in-process only.
"""

from __future__ import annotations

import asyncio
import math
import ssl
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast

import httpx

from hmt_crypto.errors import (
    AmbiguousExchangeResult,
    ConfigurationError,
    RateLimitError,
)

POLICY_VERSION: Final = "delta-docs-2026-11-03"
_RESET_MAX_MS: Final = 300_000
_REGIONS: Final = frozenset({"india", "global"})
_ENVIRONMENTS: Final = frozenset({"testnet", "prod"})


class RateLimitRoute(StrEnum):
    """Allowlisted Delta route identifiers with documented quota weights."""

    PRODUCTS = "products"
    ORDERBOOK = "orderbook"
    TICKERS = "tickers"
    OPEN_ORDERS = "open_orders"
    OPEN_POSITIONS = "open_positions"
    BALANCES = "balances"
    CANDLES = "candles"
    PLACE_ORDER = "place_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    ADD_POSITION_MARGIN = "add_position_margin"
    ORDER_HISTORY = "order_history"
    FILLS = "fills"
    TRANSACTIONS = "transactions"
    BATCH_ORDERS = "batch_orders"
    RATE_LIMIT_QUOTA = "rate_limit_quota"


_PRODUCT_OPERATION_ROUTES: Final = frozenset(
    {
        RateLimitRoute.PLACE_ORDER,
        RateLimitRoute.MODIFY_ORDER,
        RateLimitRoute.BATCH_ORDERS,
    }
)


ROUTE_WEIGHTS: Mapping[RateLimitRoute, int] = MappingProxyType(
    {
        RateLimitRoute.PRODUCTS: 3,
        RateLimitRoute.ORDERBOOK: 3,
        RateLimitRoute.TICKERS: 3,
        RateLimitRoute.OPEN_ORDERS: 3,
        RateLimitRoute.OPEN_POSITIONS: 3,
        RateLimitRoute.BALANCES: 3,
        RateLimitRoute.CANDLES: 3,
        RateLimitRoute.PLACE_ORDER: 5,
        RateLimitRoute.MODIFY_ORDER: 5,
        RateLimitRoute.CANCEL_ORDER: 5,
        RateLimitRoute.ADD_POSITION_MARGIN: 5,
        RateLimitRoute.ORDER_HISTORY: 10,
        RateLimitRoute.FILLS: 10,
        RateLimitRoute.TRANSACTIONS: 10,
        RateLimitRoute.BATCH_ORDERS: 25,
        RateLimitRoute.RATE_LIMIT_QUOTA: 1,
    }
)


class ScopeKind(StrEnum):
    """Exchange quota ownership scope."""

    IP = "ip"
    EXTERNAL_USER = "external_user"


class CoordinationMode(StrEnum):
    """Available quota coordination durability."""

    IN_PROCESS_ONLY = "in_process_only"


class OperationClass(StrEnum):
    """Retry-safety class independent of HTTP method names."""

    SAFE_READ = "safe_read"
    CREATE_ORDER = "create_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    SET_LEVERAGE = "set_leverage"
    ADD_MARGIN = "add_margin"
    BATCH_WRITE = "batch_write"


class RetryAction(StrEnum):
    """Pure retry-classifier outcome."""

    RETRY = "retry"
    FAIL = "fail"
    AMBIGUOUS = "ambiguous"


def _strict_positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _strict_nonnegative_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _strict_decimal(value: object, *, name: str, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be an exact decimal")
    if not isinstance(value, (Decimal, int, str)):
        raise ValueError(f"{name} must be an exact decimal")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be an exact decimal") from error
    if not parsed.is_finite() or (positive and parsed <= 0):
        qualifier = "positive finite" if positive else "finite"
        raise ValueError(f"{name} must be {qualifier}")
    return parsed


def _strict_monotonic_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(context={"operation": "monotonic_clock"})
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ConfigurationError(context={"operation": "monotonic_clock"})
    return parsed


@dataclass(frozen=True, slots=True)
class RateLimitScope:
    """Region/environment budget key using an egress label or Delta user ID."""

    region: str
    environment: str
    kind: ScopeKind
    external_id: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.region) is not str or self.region not in _REGIONS:
            raise ValueError("region must be india or global")
        if type(self.environment) is not str or self.environment not in _ENVIRONMENTS:
            raise ValueError("environment must be testnet or prod")
        if not isinstance(self.kind, ScopeKind):
            raise ValueError("kind must be a ScopeKind")
        if (
            type(self.external_id) is not str
            or not self.external_id.strip()
            or len(self.external_id) > 128
        ):
            raise ValueError("external_id must be a nonempty identifier of at most 128 characters")


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Immutable documented quota policy and allowlisted route weights."""

    version: str
    india_capacity: int
    global_capacity: int
    rest_window_seconds: int
    product_operations_per_second: int
    route_weights: Mapping[RateLimitRoute, int]

    def __post_init__(self) -> None:
        if type(self.version) is not str or not self.version.strip():
            raise ValueError("version must be a nonempty string")
        _strict_positive_int(self.india_capacity, name="india_capacity")
        _strict_positive_int(self.global_capacity, name="global_capacity")
        _strict_positive_int(self.rest_window_seconds, name="rest_window_seconds")
        _strict_positive_int(
            self.product_operations_per_second,
            name="product_operations_per_second",
        )
        if not isinstance(self.route_weights, Mapping):
            raise ValueError("route_weights must be a mapping")
        copied: dict[RateLimitRoute, int] = {}
        for route, weight in self.route_weights.items():
            if not isinstance(route, RateLimitRoute):
                raise ValueError("route_weights keys must be RateLimitRoute values")
            copied[route] = _strict_positive_int(weight, name="route weight")
        if set(copied) != set(RateLimitRoute):
            raise ValueError("route_weights must cover the complete allowlisted route registry")
        object.__setattr__(self, "route_weights", MappingProxyType(copied))

    def capacity_for(self, region: str) -> int:
        """Return the documented weighted quota for one region."""
        if type(region) is not str or region not in _REGIONS:
            raise ConfigurationError(context={"operation": "rate_limit_capacity"})
        return self.india_capacity if region == "india" else self.global_capacity

    def weight_for(self, route: RateLimitRoute) -> int:
        """Return a route weight; arbitrary paths and strings fail closed."""
        if not isinstance(route, RateLimitRoute):
            raise ConfigurationError(context={"operation": "rate_limit_weight"})
        return self.route_weights[route]


DELTA_RATE_LIMIT_POLICY = RateLimitPolicy(
    version=POLICY_VERSION,
    india_capacity=20_000,
    global_capacity=10_000,
    rest_window_seconds=300,
    product_operations_per_second=500,
    route_weights=ROUTE_WEIGHTS,
)


@dataclass(frozen=True, slots=True)
class RateRequest:
    """One atomic REST-weight and per-product operation acquisition."""

    route: RateLimitRoute
    product_operations: Mapping[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.route, RateLimitRoute):
            raise ValueError("route must be a RateLimitRoute")
        if not isinstance(self.product_operations, Mapping):
            raise ValueError("product_operations must be a mapping")
        copied: dict[int, int] = {}
        for product_id, count in self.product_operations.items():
            copied[_strict_positive_int(product_id, name="product_id")] = _strict_positive_int(
                count, name="product operation count"
            )
        if self.route in _PRODUCT_OPERATION_ROUTES and not copied:
            raise ValueError("matching-engine write routes require product operations")
        if self.route is RateLimitRoute.CANCEL_ORDER and copied:
            raise ValueError("cancel routes are exempt from product operations")
        object.__setattr__(self, "product_operations", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class RatePermit:
    """Immutable evidence that both local budgets were consumed atomically."""

    acquired_at: float
    rest_weight: int
    product_operations: Mapping[int, int]

    def __post_init__(self) -> None:
        if (
            isinstance(self.acquired_at, bool)
            or not isinstance(self.acquired_at, (int, float))
            or not math.isfinite(float(self.acquired_at))
            or self.acquired_at < 0
        ):
            raise ValueError("acquired_at must be a nonnegative monotonic value")
        _strict_positive_int(self.rest_weight, name="rest_weight")
        if not isinstance(self.product_operations, Mapping):
            raise ValueError("product_operations must be a mapping")
        copied = {
            _strict_positive_int(product_id, name="product_id"): _strict_positive_int(
                count, name="product operation count"
            )
            for product_id, count in self.product_operations.items()
        }
        object.__setattr__(
            self,
            "product_operations",
            MappingProxyType(copied),
        )


@dataclass(frozen=True, slots=True)
class RateLimitSnapshot:
    """Read-only current local accounting state."""

    policy_version: str
    capacity: int
    rest_used: int
    rest_remaining: int
    blocked_until: float | None
    waiter_count: int
    product_operations_used: Mapping[int, int]
    closed: bool
    coordination_mode: CoordinationMode = CoordinationMode.IN_PROCESS_ONLY

    def __post_init__(self) -> None:
        if type(self.policy_version) is not str or not self.policy_version:
            raise ValueError("policy_version must be a nonempty string")
        capacity = _strict_positive_int(self.capacity, name="capacity")
        used = _strict_nonnegative_int(self.rest_used, name="rest_used")
        remaining = _strict_nonnegative_int(self.rest_remaining, name="rest_remaining")
        if used + remaining != capacity:
            raise ValueError("used and remaining REST quota must equal capacity")
        _strict_nonnegative_int(self.waiter_count, name="waiter_count")
        if type(self.closed) is not bool:
            raise ValueError("closed must be boolean")
        if not isinstance(self.coordination_mode, CoordinationMode):
            raise ValueError("coordination_mode must be CoordinationMode")
        if self.blocked_until is not None and (
            isinstance(self.blocked_until, bool)
            or not isinstance(self.blocked_until, (int, float))
            or not math.isfinite(float(self.blocked_until))
            or self.blocked_until < 0
        ):
            raise ValueError("blocked_until must be a nonnegative monotonic value")
        if not isinstance(self.product_operations_used, Mapping):
            raise ValueError("product_operations_used must be a mapping")
        products = {
            _strict_positive_int(product_id, name="product_id"): _strict_positive_int(
                count, name="product operations used"
            )
            for product_id, count in self.product_operations_used.items()
        }
        object.__setattr__(
            self,
            "product_operations_used",
            MappingProxyType(products),
        )


@dataclass(frozen=True, slots=True)
class QuotaObservation:
    """Validated but non-authoritative `/v2/rate_limits/quota` telemetry."""

    current_quota: int
    remaining_time_in_milliseconds: int
    observed_at: float

    def __post_init__(self) -> None:
        _strict_nonnegative_int(self.current_quota, name="current_quota")
        remaining = _strict_nonnegative_int(
            self.remaining_time_in_milliseconds,
            name="remaining_time_in_milliseconds",
        )
        if remaining > _RESET_MAX_MS:
            raise ValueError("remaining_time_in_milliseconds exceeds one quota window")
        _strict_monotonic_value(self.observed_at)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff policy for explicitly safe reads."""

    max_attempts: int = 3
    base_delay_seconds: Decimal | int | str = Decimal("0.25")
    max_delay_seconds: Decimal | int | str = Decimal("10")
    jitter_ratio: Decimal | int | str = Decimal("0.2")

    def __post_init__(self) -> None:
        max_attempts = _strict_positive_int(self.max_attempts, name="max_attempts")
        base = _strict_decimal(self.base_delay_seconds, name="base_delay_seconds", positive=True)
        maximum = _strict_decimal(self.max_delay_seconds, name="max_delay_seconds", positive=True)
        jitter = _strict_decimal(self.jitter_ratio, name="jitter_ratio")
        if maximum < base:
            raise ValueError("max_delay_seconds must be at least base_delay_seconds")
        if jitter < 0 or jitter > 1:
            raise ValueError("jitter_ratio must be between zero and one")
        object.__setattr__(self, "max_attempts", max_attempts)
        object.__setattr__(self, "base_delay_seconds", base)
        object.__setattr__(self, "max_delay_seconds", maximum)
        object.__setattr__(self, "jitter_ratio", jitter)


DEFAULT_RETRY_POLICY = RetryPolicy()


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """Pure retry outcome; ambiguous writes can raise their typed terminal error."""

    action: RetryAction
    delay_seconds: Decimal | None
    reason: str
    operation: OperationClass

    def __post_init__(self) -> None:
        if not isinstance(self.action, RetryAction):
            raise ValueError("action must be a RetryAction")
        if not isinstance(self.operation, OperationClass):
            raise ValueError("operation must be an OperationClass")
        if type(self.reason) is not str or not self.reason:
            raise ValueError("reason must be a nonempty string")
        if self.action is RetryAction.RETRY:
            if not isinstance(self.delay_seconds, Decimal) or self.delay_seconds <= 0:
                raise ValueError("retry decisions require a positive Decimal delay")
            if self.operation is not OperationClass.SAFE_READ:
                raise ValueError("write operations cannot carry retry decisions")
        elif self.delay_seconds is not None:
            raise ValueError("terminal decisions cannot carry a retry delay")
        if self.action is RetryAction.AMBIGUOUS and self.operation is OperationClass.SAFE_READ:
            raise ValueError("safe reads cannot have ambiguous write outcomes")

    def raise_if_ambiguous(self) -> None:
        """Raise the normalized ambiguity error when reconciliation is required."""
        if self.action is RetryAction.AMBIGUOUS:
            raise AmbiguousExchangeResult(
                context={"operation": self.operation.value, "state": "unknown"}
            )


@dataclass(slots=True, eq=False)
class _Waiter:
    request: RateRequest
    event: asyncio.Event


class WeightedRateLimiter:
    """FIFO, cancellation-safe local limiter for one Delta quota scope."""

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
        self.scope = scope
        self.policy = policy
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
    def coordination_mode(self) -> CoordinationMode:
        """Return the deliberately non-durable Phase 1 coordination mode."""
        return CoordinationMode.IN_PROCESS_ONLY

    def _now(self) -> float:
        now = _strict_monotonic_value(self._monotonic())
        if self._last_now is not None and now < self._last_now:
            raise ConfigurationError(context={"operation": "monotonic_clock"})
        self._last_now = now
        return now

    def _capacity(self) -> int:
        return self.policy.capacity_for(self.scope.region)

    def _prune(self, now: float) -> None:
        rest_cutoff = now - self.policy.rest_window_seconds
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
            requested=self.policy.weight_for(request.route),
            capacity=self._capacity(),
            window_seconds=float(self.policy.rest_window_seconds),
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
                    capacity=self.policy.product_operations_per_second,
                    window_seconds=1.0,
                    now=now,
                ),
            )
        block_delay = max(0.0, self._blocked_until - now)
        return max(rest_delay, product_delay, block_delay)

    def _consume(self, request: RateRequest, now: float) -> RatePermit:
        weight = self.policy.weight_for(request.route)
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
        if self.policy.weight_for(request.route) > self._capacity():
            raise ConfigurationError(context={"operation": "rate_limit_acquire"})
        if any(
            count > self.policy.product_operations_per_second
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
            for task in (sleep_task, notification_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sleep_task, notification_task, return_exceptions=True)

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
        current = _strict_nonnegative_int(payload["current_quota"], name="current_quota")
        remaining = _strict_nonnegative_int(
            payload["remaining_time_in_milliseconds"],
            name="remaining_time_in_milliseconds",
        )
        if remaining > _RESET_MAX_MS:
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
                policy_version=self.policy.version,
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


def parse_rate_limit_reset_ms(value: str) -> int:
    """Parse Delta's reset header strictly as milliseconds within one window."""
    if type(value) is not str or not value or not value.isascii() or not value.isdecimal():
        raise ConfigurationError(context={"operation": "rate_limit_reset"})
    parsed = int(value, 10)
    if parsed > _RESET_MAX_MS:
        raise ConfigurationError(context={"operation": "rate_limit_reset"})
    return parsed


def _contains_ssl_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _safe_read_transient(error: BaseException | None) -> bool:
    return bool(
        error is not None
        and not _contains_ssl_error(error)
        and isinstance(
            error,
            (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ReadError),
        )
    )


def _fail(operation: OperationClass, reason: str) -> RetryDecision:
    return RetryDecision(
        action=RetryAction.FAIL,
        delay_seconds=None,
        reason=reason,
        operation=operation,
    )


def classify_retry(
    *,
    operation: OperationClass,
    attempt: int,
    status_code: int | None,
    reset_header: str | None,
    transport_error: BaseException | None,
    bytes_may_have_been_sent: bool,
    jitter_unit: Decimal | int | str,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> RetryDecision:
    """Classify retry safety without performing I/O or sleeping.

    ``attempt`` is one-based and counts the failed attempt.  A policy with three
    attempts may retry failures from attempts one and two only.
    """
    if not isinstance(operation, OperationClass):
        raise ConfigurationError(context={"operation": "retry_classification"})
    attempt_number = _strict_positive_int(attempt, name="attempt")
    if status_code is not None and (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or status_code < 100
        or status_code > 599
    ):
        raise ValueError("status_code must be a valid HTTP status")
    if transport_error is not None and not isinstance(transport_error, BaseException):
        raise ValueError("transport_error must be an exception")
    if type(bytes_may_have_been_sent) is not bool:
        raise ValueError("bytes_may_have_been_sent must be boolean")
    if not isinstance(policy, RetryPolicy):
        raise ValueError("policy must be RetryPolicy")
    jitter = _strict_decimal(jitter_unit, name="jitter_unit")
    if jitter < 0 or jitter > 1:
        raise ValueError("jitter_unit must be between zero and one")

    if operation is not OperationClass.SAFE_READ:
        definitive = status_code is not None and (status_code < 500 or status_code == 429)
        uncertain_status = status_code is not None and 500 <= status_code <= 599
        uncertain_transport = isinstance(transport_error, httpx.TransportError)
        if (
            bytes_may_have_been_sent
            and not definitive
            and (uncertain_status or uncertain_transport)
        ):
            return RetryDecision(
                action=RetryAction.AMBIGUOUS,
                delay_seconds=None,
                reason="write_outcome_unknown",
                operation=operation,
            )
        return _fail(operation, "write_not_retryable")

    if isinstance(transport_error, asyncio.CancelledError):
        return _fail(operation, "cancelled")
    if status_code is not None and status_code < 500 and status_code != 429:
        return _fail(operation, "definitive_http_response")
    retryable_status = status_code == 429 or (status_code is not None and 500 <= status_code <= 599)
    retryable_transport = _safe_read_transient(transport_error)
    if not retryable_status and not retryable_transport:
        return _fail(operation, "not_retryable")
    if attempt_number >= policy.max_attempts:
        return _fail(operation, "attempts_exhausted")

    base_delay = cast(Decimal, policy.base_delay_seconds)
    jitter_ratio = cast(Decimal, policy.jitter_ratio)
    maximum_delay = cast(Decimal, policy.max_delay_seconds)
    exponential = base_delay * (Decimal(2) ** (attempt_number - 1))
    jittered = exponential * (Decimal(1) + jitter_ratio * jitter)
    delay = min(maximum_delay, jittered)
    if status_code == 429 and reset_header is not None:
        reset_seconds = Decimal(parse_rate_limit_reset_ms(reset_header)) / Decimal(1000)
        delay = max(delay, reset_seconds)
    return RetryDecision(
        action=RetryAction.RETRY,
        delay_seconds=delay,
        reason="safe_read_transient",
        operation=operation,
    )


__all__ = [
    "CoordinationMode",
    "DEFAULT_RETRY_POLICY",
    "DELTA_RATE_LIMIT_POLICY",
    "LimiterRegistry",
    "OperationClass",
    "POLICY_VERSION",
    "QuotaObservation",
    "ROUTE_WEIGHTS",
    "RateLimitPolicy",
    "RateLimitRoute",
    "RateLimitScope",
    "RateLimitSnapshot",
    "RatePermit",
    "RateRequest",
    "RetryAction",
    "RetryDecision",
    "RetryPolicy",
    "ScopeKind",
    "WeightedRateLimiter",
    "classify_retry",
    "parse_rate_limit_reset_ms",
]
