"""Package-private ownership primitives for identified quota accounting."""

from __future__ import annotations

import asyncio
import math
import os
import threading
from asyncio import AbstractEventLoop
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.rate_limit_policy import (
    RateLimitPolicy,
    RateLimitRoute,
    RateLimitScope,
    RatePermit,
    RatePermitAccounting,
    ScopeKind,
)

_ROUTES = tuple(RateLimitRoute)
_SCOPE_KINDS = tuple(ScopeKind)


def _canonical_route(value: object) -> RateLimitRoute | None:
    if type(value) is RateLimitRoute:
        for route in _ROUTES:
            if value is route:
                return route
    return None


def _positive_int(value: object, *, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be an exact positive integer")
    return value


def _nonnegative_int(value: object, *, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be an exact nonnegative integer")
    return value


def _event_id(value: object, *, name: str) -> UUID:
    if type(value) is not UUID or value.int == 0:
        raise ValueError(f"{name} must be an exact nonzero UUID")
    return value


@dataclass(frozen=True, slots=True)
class _QuotaAccountingEvent:
    event_id: UUID
    occurred_at_monotonic_ns: int
    expires_at_monotonic_ns: int
    amount: int

    def __post_init__(self) -> None:
        _event_id(self.event_id, name="event_id")
        occurred = _nonnegative_int(self.occurred_at_monotonic_ns, name="occurred_at_monotonic_ns")
        if (
            type(self.expires_at_monotonic_ns) is not int
            or self.expires_at_monotonic_ns <= occurred
        ):
            raise ValueError("expires_at_monotonic_ns must follow occurrence")
        _positive_int(self.amount, name="amount")


@dataclass(frozen=True, slots=True)
class _IndexedQuotaAccountingEvent:
    event: _QuotaAccountingEvent
    product_id: int | None

    def __post_init__(self) -> None:
        if type(self.event) is not _QuotaAccountingEvent:
            raise ValueError("event must be exact quota accounting event")
        if self.product_id is not None:
            _positive_int(self.product_id, name="product_id")


@dataclass(frozen=True, slots=True)
class _LimiterChargePlan:
    route: RateLimitRoute
    rest_weight: int
    product_operations: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        canonical = _canonical_route(self.route)
        if canonical is None:
            raise ValueError("route must be a canonical RateLimitRoute")
        _positive_int(self.rest_weight, name="rest_weight")
        if type(self.product_operations) is not tuple:
            raise ValueError("product_operations must be an exact tuple")
        previous = 0
        for item in self.product_operations:
            if type(item) is not tuple or len(item) != 2:
                raise ValueError("product operation entries must be exact pairs")
            product_id, count = item
            _positive_int(product_id, name="product_id")
            _positive_int(count, name="product operation count")
            if product_id <= previous:
                raise ValueError("product operations must be sorted and unique")
            previous = product_id
        object.__setattr__(self, "route", canonical)


@dataclass(frozen=True, slots=True)
class _PreallocatedQuotaEventIds:
    rest_event_id: UUID
    product_event_ids: tuple[tuple[int, UUID], ...]

    def __post_init__(self) -> None:
        _event_id(self.rest_event_id, name="rest_event_id")
        if type(self.product_event_ids) is not tuple:
            raise ValueError("product_event_ids must be an exact tuple")
        previous = 0
        identities = [self.rest_event_id]
        for item in self.product_event_ids:
            if type(item) is not tuple or len(item) != 2:
                raise ValueError("product event entries must be exact pairs")
            product_id, identity = item
            _positive_int(product_id, name="product_id")
            _event_id(identity, name="product event ID")
            if product_id <= previous:
                raise ValueError("product event IDs must be sorted and unique")
            previous = product_id
            identities.append(identity)
        if len(set(identities)) != len(identities):
            raise ValueError("quota event IDs must be unique")


@dataclass(frozen=True, slots=True)
class _OwnedLimiterConfiguration:
    """Callback-free constructor snapshot for one limiter instance."""

    region: str
    environment: str
    kind: ScopeKind
    external_id: str
    policy_version: str
    india_capacity: int
    global_capacity: int
    rest_window_seconds: int
    product_capacity: int
    route_weights: tuple[tuple[RateLimitRoute, int], ...]

    @classmethod
    def from_inputs(cls, scope: object, policy: object) -> _OwnedLimiterConfiguration:
        if type(scope) is not RateLimitScope:
            raise ValueError("scope must be an exact RateLimitScope")
        if type(scope.region) is not str or scope.region not in {"india", "global"}:
            raise ValueError("scope region is invalid")
        if type(scope.environment) is not str or scope.environment not in {"testnet", "prod"}:
            raise ValueError("scope environment is invalid")
        if type(scope.kind) is not ScopeKind or not any(
            scope.kind is member for member in _SCOPE_KINDS
        ):
            raise ValueError("scope kind is invalid")
        if (
            type(scope.external_id) is not str
            or not scope.external_id
            or len(scope.external_id) > 128
            or not scope.external_id.isascii()
            or any(
                ord(character) < 0x21 or ord(character) > 0x7E for character in scope.external_id
            )
        ):
            raise ValueError("scope external ID is invalid")
        if type(policy) is not RateLimitPolicy:
            raise ValueError("policy must be an exact RateLimitPolicy")
        scalars = (
            policy.india_capacity,
            policy.global_capacity,
            policy.rest_window_seconds,
            policy.product_operations_per_second,
        )
        if type(policy.version) is not str or not policy.version.strip():
            raise ValueError("policy version is invalid")
        if any(type(value) is not int or value <= 0 for value in scalars):
            raise ValueError("policy quota values are invalid")
        weights = policy._attested_route_weights()
        return cls(
            region=scope.region,
            environment=scope.environment,
            kind=scope.kind,
            external_id=scope.external_id,
            policy_version=policy.version,
            india_capacity=policy.india_capacity,
            global_capacity=policy.global_capacity,
            rest_window_seconds=policy.rest_window_seconds,
            product_capacity=policy.product_operations_per_second,
            route_weights=weights,
        )

    @property
    def capacity(self) -> int:
        return self.india_capacity if self.region == "india" else self.global_capacity

    def scope_snapshot(self) -> RateLimitScope:
        return RateLimitScope(self.region, self.environment, self.kind, self.external_id)

    def policy_snapshot(self) -> RateLimitPolicy:
        return RateLimitPolicy(
            version=self.policy_version,
            india_capacity=self.india_capacity,
            global_capacity=self.global_capacity,
            rest_window_seconds=self.rest_window_seconds,
            product_operations_per_second=self.product_capacity,
            route_weights=dict(self.route_weights),
        )


def _validate_event_sequence(
    events: deque[_QuotaAccountingEvent],
    *,
    product_id: int | None,
    expected: dict[UUID, tuple[_QuotaAccountingEvent, int | None]],
    rest_window_ns: int,
    capacity: int,
    product_capacity: int,
) -> None:
    previous_occurrence = -1
    previous_expiry = -1
    expiry_delta = rest_window_ns if product_id is None else 1_000_000_000
    category_capacity = capacity if product_id is None else product_capacity
    for event in events:
        if type(event) is not _QuotaAccountingEvent:
            raise ValueError("event type is invalid")
        if type(event.event_id) is not UUID or event.event_id.int == 0:
            raise ValueError("event identity is invalid")
        if type(event.occurred_at_monotonic_ns) is not int or event.occurred_at_monotonic_ns < 0:
            raise ValueError("event occurrence is invalid")
        if (
            type(event.expires_at_monotonic_ns) is not int
            or event.expires_at_monotonic_ns <= event.occurred_at_monotonic_ns
        ):
            raise ValueError("event expiry is invalid")
        if type(event.amount) is not int or event.amount <= 0:
            raise ValueError("event amount is invalid")
        if (
            event.amount > category_capacity
            or event.expires_at_monotonic_ns - event.occurred_at_monotonic_ns != expiry_delta
        ):
            raise ValueError("event bounds are invalid")
        if (
            event.occurred_at_monotonic_ns < previous_occurrence
            or event.expires_at_monotonic_ns < previous_expiry
        ):
            raise ValueError("event order is invalid")
        if event.event_id in expected:
            raise ValueError("event identity is duplicated")
        expected[event.event_id] = (event, product_id)
        previous_occurrence = event.occurred_at_monotonic_ns
        previous_expiry = event.expires_at_monotonic_ns


def _assert_event_index_integrity(
    *,
    rest_events: deque[_QuotaAccountingEvent],
    product_events: dict[int, deque[_QuotaAccountingEvent]],
    event_index: dict[UUID, _IndexedQuotaAccountingEvent],
    limiter_instance_id: UUID,
    rest_window_ns: int,
    capacity: int,
    product_capacity: int,
    clock_epoch: int,
    accounting_generation: int,
    cooldown_epoch: int,
    blocked_until_ns: int,
    last_now: float | None,
    closed: bool,
    clock_poisoned: bool,
    accounting_poisoned: bool,
) -> None:
    if type(rest_events) is not deque or type(product_events) is not dict:
        raise ValueError("quota event containers are invalid")
    if type(event_index) is not dict:
        raise ValueError("quota event index is invalid")
    if type(limiter_instance_id) is not UUID or limiter_instance_id.int == 0:
        raise ValueError("limiter instance identity is invalid")
    if any(
        type(value) is not int or value <= 0
        for value in (rest_window_ns, capacity, product_capacity)
    ):
        raise ValueError("captured quota bounds are invalid")
    if any(type(value) is not int or value <= 0 for value in (clock_epoch, accounting_generation)):
        raise ValueError("accounting epochs are invalid")
    if type(cooldown_epoch) is not int or cooldown_epoch < 0:
        raise ValueError("cooldown epoch is invalid")
    if type(blocked_until_ns) is not int or blocked_until_ns < 0:
        raise ValueError("cooldown deadline is invalid")
    if last_now is not None and (
        type(last_now) is not float or not math.isfinite(last_now) or last_now < 0
    ):
        raise ValueError("last clock sample is invalid")
    if any(type(value) is not bool for value in (closed, clock_poisoned, accounting_poisoned)):
        raise ValueError("accounting state flags are invalid")
    expected: dict[UUID, tuple[_QuotaAccountingEvent, int | None]] = {}
    _validate_event_sequence(
        rest_events,
        product_id=None,
        expected=expected,
        rest_window_ns=rest_window_ns,
        capacity=capacity,
        product_capacity=product_capacity,
    )
    for product_id, events in product_events.items():
        if (
            type(product_id) is not int
            or product_id <= 0
            or type(events) is not deque
            or not events
        ):
            raise ValueError("product accounting category is invalid")
        _validate_event_sequence(
            events,
            product_id=product_id,
            expected=expected,
            rest_window_ns=rest_window_ns,
            capacity=capacity,
            product_capacity=product_capacity,
        )
    if len(expected) != len(event_index):
        raise ValueError("event index cardinality is invalid")
    for event_id, indexed in event_index.items():
        if type(event_id) is not UUID or event_id.int == 0:
            raise ValueError("indexed event ID is invalid")
        if type(indexed) is not _IndexedQuotaAccountingEvent:
            raise ValueError("indexed event type is invalid")
        if indexed.product_id is not None and (
            type(indexed.product_id) is not int or indexed.product_id <= 0
        ):
            raise ValueError("indexed product identity is invalid")
        match = expected.get(event_id)
        if match is None or indexed.event is not match[0] or indexed.product_id != match[1]:
            raise ValueError("indexed event identity is invalid")


def _prune_accounting_events(
    *,
    rest_events: deque[_QuotaAccountingEvent],
    product_events: dict[int, deque[_QuotaAccountingEvent]],
    event_index: dict[UUID, _IndexedQuotaAccountingEvent],
    now_ns: int,
) -> None:
    while rest_events and rest_events[0].expires_at_monotonic_ns <= now_ns:
        event = rest_events.popleft()
        del event_index[event.event_id]
    for product_id, events in tuple(product_events.items()):
        while events and events[0].expires_at_monotonic_ns <= now_ns:
            event = events.popleft()
            del event_index[event.event_id]
        if not events:
            del product_events[product_id]


def _event_delay_ns(
    events: deque[_QuotaAccountingEvent],
    *,
    used: int,
    requested: int,
    capacity: int,
    now_ns: int,
) -> int:
    required = used + requested - capacity
    if required <= 0:
        return 0
    freed = 0
    for event in events:
        freed += event.amount
        if freed >= required:
            return max(0, event.expires_at_monotonic_ns - now_ns)
    raise ValueError("quota accounting cannot satisfy requested capacity")


def _accounting_delay_seconds(
    *,
    plan: _LimiterChargePlan,
    rest_events: deque[_QuotaAccountingEvent],
    product_events: dict[int, deque[_QuotaAccountingEvent]],
    capacity: int,
    product_capacity: int,
    blocked_until_ns: int,
    now_ns: int,
) -> float:
    rest_delay = _event_delay_ns(
        rest_events,
        used=sum(event.amount for event in rest_events),
        requested=plan.rest_weight,
        capacity=capacity,
        now_ns=now_ns,
    )
    product_delay = 0
    for product_id, requested in plan.product_operations:
        events = product_events.get(product_id, deque())
        product_delay = max(
            product_delay,
            _event_delay_ns(
                events,
                used=sum(event.amount for event in events),
                requested=requested,
                capacity=product_capacity,
                now_ns=now_ns,
            ),
        )
    blocked = max(0, blocked_until_ns - now_ns)
    return max(rest_delay, product_delay, blocked) / 1_000_000_000


def _consume_accounting_plan(
    *,
    plan: _LimiterChargePlan,
    event_ids: _PreallocatedQuotaEventIds,
    rest_events: deque[_QuotaAccountingEvent],
    product_events: dict[int, deque[_QuotaAccountingEvent]],
    event_index: dict[UUID, _IndexedQuotaAccountingEvent],
    acquired_at: float,
    acquired_ns: int,
    rest_window_ns: int,
    limiter_instance_id: UUID,
    clock_epoch: int,
    accounting_generation: int,
    cooldown_epoch: int,
) -> RatePermit:
    expected_products = tuple(product_id for product_id, _ in plan.product_operations)
    if tuple(product_id for product_id, _ in event_ids.product_event_ids) != expected_products:
        raise ValueError("preallocated product identities do not match charge plan")
    identities = [
        event_ids.rest_event_id,
        *(identity for _, identity in event_ids.product_event_ids),
    ]
    if any(identity in event_index for identity in identities):
        raise ValueError("active quota event identity is duplicated")
    rest = _QuotaAccountingEvent(
        event_id=event_ids.rest_event_id,
        occurred_at_monotonic_ns=acquired_ns,
        expires_at_monotonic_ns=acquired_ns + rest_window_ns,
        amount=plan.rest_weight,
    )
    product_identities = dict(event_ids.product_event_ids)
    product_rows = tuple(
        (
            product_id,
            _QuotaAccountingEvent(
                event_id=product_identities[product_id],
                occurred_at_monotonic_ns=acquired_ns,
                expires_at_monotonic_ns=acquired_ns + 1_000_000_000,
                amount=count,
            ),
        )
        for product_id, count in plan.product_operations
    )
    rest_events.append(rest)
    event_index[rest.event_id] = _IndexedQuotaAccountingEvent(rest, None)
    for product_id, event in product_rows:
        product_events.setdefault(product_id, deque()).append(event)
        event_index[event.event_id] = _IndexedQuotaAccountingEvent(event, product_id)
    evidence = RatePermitAccounting(
        limiter_instance_id=limiter_instance_id,
        limiter_clock_epoch=clock_epoch,
        limiter_accounting_generation=accounting_generation,
        acquired_monotonic_ns=acquired_ns,
        rest_event_id=rest.event_id,
        rest_window_end_monotonic_ns=rest.expires_at_monotonic_ns,
        product_event_ids={product_id: event.event_id for product_id, event in product_rows},
        product_window_end_monotonic_ns={
            product_id: event.expires_at_monotonic_ns for product_id, event in product_rows
        },
        cooldown_epoch=cooldown_epoch,
    )
    return RatePermit(
        acquired_at=acquired_at,
        rest_weight=plan.rest_weight,
        product_operations=dict(plan.product_operations),
        accounting=evidence,
    )


class _LimiterAccountingGate:
    """Non-reentrant synchronous gate confined to one process/thread/loop."""

    __slots__ = (
        "_active_holder",
        "_bound_loop",
        "_lock",
        "_owner_pid",
        "_owner_thread_id",
    )

    def __init__(self) -> None:
        self._owner_pid = os.getpid()
        self._owner_thread_id = threading.get_ident()
        self._bound_loop: AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._active_holder: int | None = None

    @property
    def owner_pid(self) -> int:
        return self._owner_pid

    @property
    def owner_thread_id(self) -> int:
        return self._owner_thread_id

    def assert_process_thread(self) -> None:
        if os.getpid() != self._owner_pid or threading.get_ident() != self._owner_thread_id:
            raise ConfigurationError(context={"operation": "rate_limit_context"})

    def bind_or_assert_running_loop(self, loop: AbstractEventLoop) -> None:
        self.assert_process_thread()
        if not isinstance(loop, AbstractEventLoop):
            raise ConfigurationError(context={"operation": "rate_limit_context"})
        if self._bound_loop is None:
            self._bound_loop = loop
        elif loop is not self._bound_loop:
            raise ConfigurationError(context={"operation": "rate_limit_context"})

    def assert_bound_sync_context(self) -> None:
        self.assert_process_thread()
        if self._bound_loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            raise ConfigurationError(context={"operation": "rate_limit_context"}) from None
        if running is not self._bound_loop:
            raise ConfigurationError(context={"operation": "rate_limit_context"})

    @contextmanager
    def hold(self) -> Iterator[None]:
        self.assert_bound_sync_context()
        holder = threading.get_ident()
        if self._active_holder is not None or not self._lock.acquire(blocking=False):
            raise ConfigurationError(context={"operation": "rate_limit_accounting_gate"})
        self._active_holder = holder
        try:
            yield
        finally:
            self._active_holder = None
            self._lock.release()

    def assert_held(self) -> None:
        self.assert_process_thread()
        if self._active_holder != threading.get_ident():
            raise ConfigurationError(context={"operation": "rate_limit_accounting_gate"})

    def held_by_current_thread(self) -> bool:
        return self._active_holder == threading.get_ident()


__all__: list[str] = []
