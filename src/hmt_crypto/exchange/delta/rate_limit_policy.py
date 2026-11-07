"""Immutable Delta quota policy, scope, request, and evidence models."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from hmt_crypto.errors import ConfigurationError

POLICY_VERSION: Final = "delta-docs-2026-11-04-private-v1"
MAX_RATE_LIMIT_RESET_MS: Final = 300_000
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
    MARGINED_POSITIONS = "margined_positions"
    LATEST_POSITION = "latest_position"
    ORDER_BY_ID = "order_by_id"
    ORDER_BY_CLIENT_ID = "order_by_client_id"
    GET_LEVERAGE = "get_leverage"
    CANCEL_ALL = "cancel_all"
    SET_LEVERAGE = "set_leverage"
    SET_MARGIN_MODE = "set_margin_mode"
    CHANGE_POSITION_MARGIN = "change_position_margin"
    CREATE_BRACKET = "create_bracket"
    EDIT_BRACKET = "edit_bracket"


_RATE_LIMIT_ROUTES: Final = tuple(RateLimitRoute)


def _canonical_rate_route(value: object) -> RateLimitRoute | None:
    if type(value) is RateLimitRoute:
        for member in _RATE_LIMIT_ROUTES:
            if value is member:
                return member
    return None


_PRODUCT_OPERATION_ROUTES: Final = frozenset(
    {
        RateLimitRoute.PLACE_ORDER,
        RateLimitRoute.MODIFY_ORDER,
        RateLimitRoute.BATCH_ORDERS,
    }
)
_CANCELLATION_ROUTES: Final = frozenset({RateLimitRoute.CANCEL_ORDER, RateLimitRoute.CANCEL_ALL})

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
        RateLimitRoute.MARGINED_POSITIONS: 3,
        RateLimitRoute.LATEST_POSITION: 3,
        RateLimitRoute.ORDER_BY_ID: 1,
        RateLimitRoute.ORDER_BY_CLIENT_ID: 1,
        RateLimitRoute.GET_LEVERAGE: 1,
        RateLimitRoute.CANCEL_ALL: 1,
        RateLimitRoute.SET_LEVERAGE: 1,
        RateLimitRoute.SET_MARGIN_MODE: 1,
        RateLimitRoute.CHANGE_POSITION_MARGIN: 5,
        RateLimitRoute.CREATE_BRACKET: 1,
        RateLimitRoute.EDIT_BRACKET: 1,
    }
)


class ScopeKind(StrEnum):
    """Exchange quota ownership scope."""

    IP = "ip"
    EXTERNAL_USER = "external_user"


class CoordinationMode(StrEnum):
    """Available quota coordination durability."""

    IN_PROCESS_ONLY = "in_process_only"


def strict_positive_int(value: object, *, name: str) -> int:
    """Parse an actual positive integer, rejecting booleans and coercion."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def strict_nonnegative_int(value: object, *, name: str) -> int:
    """Parse an actual nonnegative integer, rejecting booleans and coercion."""
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def strict_monotonic_value(value: object) -> float:
    """Validate an injected monotonic-clock reading."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(context={"operation": "monotonic_clock"})
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ConfigurationError(context={"operation": "monotonic_clock"})
    return parsed


@dataclass(frozen=True, slots=True)
class RateLimitScope:
    """Canonical region/environment key using an egress label or Delta user ID."""

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
            or not self.external_id
            or len(self.external_id) > 128
            or not self.external_id.isascii()
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in self.external_id)
        ):
            raise ValueError(
                "external_id must be a canonical printable ASCII identifier "
                "of at most 128 characters"
            )


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """Immutable documented quota policy and allowlisted route weights."""

    version: str
    india_capacity: int
    global_capacity: int
    rest_window_seconds: int
    product_operations_per_second: int
    route_weights: Mapping[RateLimitRoute, int]
    _route_weight_items: tuple[tuple[RateLimitRoute, int], ...] = field(
        init=False, repr=False, compare=False
    )
    _route_weights_owner: Mapping[RateLimitRoute, int] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if type(self.version) is not str or not self.version.strip():
            raise ValueError("version must be a nonempty string")
        strict_positive_int(self.india_capacity, name="india_capacity")
        strict_positive_int(self.global_capacity, name="global_capacity")
        strict_positive_int(self.rest_window_seconds, name="rest_window_seconds")
        strict_positive_int(
            self.product_operations_per_second,
            name="product_operations_per_second",
        )
        if not isinstance(self.route_weights, Mapping):
            raise ValueError("route_weights must be a mapping")
        copied: dict[RateLimitRoute, int] = {}
        for route, weight in self.route_weights.items():
            canonical_route = _canonical_rate_route(route)
            if canonical_route is None:
                raise ValueError("route_weights keys must be RateLimitRoute values")
            if type(weight) is not int or weight <= 0:
                raise ValueError("route_weights values must be exact positive integers")
            if canonical_route in copied:
                raise ValueError("route_weights keys must be unique canonical routes")
            copied[canonical_route] = weight
        if len(copied) != len(_RATE_LIMIT_ROUTES) or any(
            route not in copied for route in _RATE_LIMIT_ROUTES
        ):
            raise ValueError("route_weights must cover the complete allowlisted route registry")
        items = tuple((route, copied[route]) for route in _RATE_LIMIT_ROUTES)
        owner = MappingProxyType(dict(items))
        object.__setattr__(self, "route_weights", owner)
        object.__setattr__(self, "_route_weight_items", items)
        object.__setattr__(self, "_route_weights_owner", owner)

    def _attested_route_weights(self) -> tuple[tuple[RateLimitRoute, int], ...]:
        items = self._route_weight_items
        if self.route_weights is not self._route_weights_owner or type(items) is not tuple:
            raise ValueError("route weight provenance is invalid")
        if len(items) != len(_RATE_LIMIT_ROUTES):
            raise ValueError("route weight provenance is incomplete")
        for expected, item in zip(_RATE_LIMIT_ROUTES, items, strict=True):
            if type(item) is not tuple or len(item) != 2:
                raise ValueError("route weight provenance is invalid")
            route, weight = item
            if route is not expected or type(weight) is not int or weight <= 0:
                raise ValueError("route weight provenance is invalid")
        return items

    def capacity_for(self, region: str) -> int:
        """Return the documented weighted quota for one region."""
        if type(region) is not str or region not in _REGIONS:
            raise ConfigurationError(context={"operation": "rate_limit_capacity"})
        return self.india_capacity if region == "india" else self.global_capacity

    def weight_for(self, route: RateLimitRoute) -> int:
        """Return a route weight; arbitrary paths and strings fail closed."""
        canonical_route = _canonical_rate_route(route)
        if canonical_route is None:
            raise ConfigurationError(context={"operation": "rate_limit_weight"})
        try:
            return next(
                weight for member, weight in self._attested_route_weights() if member is route
            )
        except (StopIteration, ValueError):
            raise ConfigurationError(context={"operation": "rate_limit_weight"}) from None


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
    _route_owner: RateLimitRoute = field(init=False, repr=False, compare=False)
    _product_operation_items: tuple[tuple[int, int], ...] = field(
        init=False, repr=False, compare=False
    )
    _product_operations_owner: Mapping[int, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        route = _canonical_rate_route(self.route)
        if route is None:
            raise ValueError("route must be a RateLimitRoute")
        if not isinstance(self.product_operations, Mapping):
            raise ValueError("product_operations must be a mapping")
        copied: dict[int, int] = {}
        for product_id, count in self.product_operations.items():
            copied[strict_positive_int(product_id, name="product_id")] = strict_positive_int(
                count, name="product operation count"
            )
        if route in _PRODUCT_OPERATION_ROUTES and not copied:
            raise ValueError("matching-engine write routes require product operations")
        if route in _CANCELLATION_ROUTES and copied:
            raise ValueError("cancel routes are exempt from product operations")
        items = tuple(sorted(copied.items()))
        owner = MappingProxyType(dict(items))
        object.__setattr__(self, "route", route)
        object.__setattr__(self, "product_operations", owner)
        object.__setattr__(self, "_route_owner", route)
        object.__setattr__(self, "_product_operation_items", items)
        object.__setattr__(self, "_product_operations_owner", owner)


@dataclass(frozen=True, slots=True)
class RatePermitAccounting:
    """Immutable identity-bearing evidence for one limiter grant."""

    limiter_instance_id: UUID
    limiter_clock_epoch: int
    limiter_accounting_generation: int
    acquired_monotonic_ns: int
    rest_event_id: UUID
    rest_window_end_monotonic_ns: int
    product_event_ids: Mapping[int, UUID]
    product_window_end_monotonic_ns: Mapping[int, int]
    cooldown_epoch: int
    _attestation: tuple[
        UUID,
        int,
        int,
        int,
        UUID,
        int,
        int,
        tuple[tuple[int, UUID], ...],
        tuple[tuple[int, int], ...],
    ] = field(init=False, repr=False, compare=False)
    _map_owners: tuple[Mapping[int, UUID], Mapping[int, int]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if type(self.limiter_instance_id) is not UUID or self.limiter_instance_id.int == 0:
            raise ValueError("limiter_instance_id must be an exact nonzero UUID")
        if type(self.rest_event_id) is not UUID or self.rest_event_id.int == 0:
            raise ValueError("rest_event_id must be an exact nonzero UUID")
        for name, value in (
            ("limiter_clock_epoch", self.limiter_clock_epoch),
            ("limiter_accounting_generation", self.limiter_accounting_generation),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be an exact positive integer")
        for name, value in (
            ("acquired_monotonic_ns", self.acquired_monotonic_ns),
            ("cooldown_epoch", self.cooldown_epoch),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be an exact nonnegative integer")
        if (
            type(self.rest_window_end_monotonic_ns) is not int
            or self.rest_window_end_monotonic_ns <= self.acquired_monotonic_ns
        ):
            raise ValueError("REST window deadline must follow acquisition")
        if type(self.product_event_ids) is not dict:
            raise ValueError("product_event_ids must be an exact dictionary")
        if type(self.product_window_end_monotonic_ns) is not dict:
            raise ValueError("product deadlines must be an exact dictionary")

        event_ids: dict[int, UUID] = {}
        for product_id, event_id in self.product_event_ids.items():
            if type(product_id) is not int or product_id <= 0:
                raise ValueError("product IDs must be exact positive integers")
            if type(event_id) is not UUID or event_id.int == 0:
                raise ValueError("product event IDs must be exact nonzero UUIDs")
            event_ids[product_id] = event_id
        deadlines: dict[int, int] = {}
        for product_id, deadline in self.product_window_end_monotonic_ns.items():
            if type(product_id) is not int or product_id <= 0:
                raise ValueError("product IDs must be exact positive integers")
            if type(deadline) is not int or deadline <= self.acquired_monotonic_ns:
                raise ValueError("product deadlines must follow acquisition")
            deadlines[product_id] = deadline
        if set(event_ids) != set(deadlines):
            raise ValueError("product event and deadline keys must match")
        all_event_ids = [self.rest_event_id, *event_ids.values()]
        if len(set(all_event_ids)) != len(all_event_ids):
            raise ValueError("quota event IDs must be unique")
        event_items = tuple(sorted(event_ids.items()))
        deadline_items = tuple(sorted(deadlines.items()))
        event_owner = MappingProxyType(dict(event_items))
        deadline_owner = MappingProxyType(dict(deadline_items))
        attestation = (
            self.limiter_instance_id,
            self.limiter_clock_epoch,
            self.limiter_accounting_generation,
            self.acquired_monotonic_ns,
            self.rest_event_id,
            self.rest_window_end_monotonic_ns,
            self.cooldown_epoch,
            event_items,
            deadline_items,
        )
        object.__setattr__(self, "product_event_ids", event_owner)
        object.__setattr__(self, "product_window_end_monotonic_ns", deadline_owner)
        object.__setattr__(self, "_attestation", attestation)
        object.__setattr__(self, "_map_owners", (event_owner, deadline_owner))

    def _defensive_copy(self) -> RatePermitAccounting:
        attestation = self._attestation
        owners = self._map_owners
        if type(attestation) is not tuple or len(attestation) != 9:
            raise ValueError("accounting attestation is invalid")
        if type(owners) is not tuple or len(owners) != 2:
            raise ValueError("accounting map provenance is invalid")
        if (
            self.product_event_ids is not owners[0]
            or self.product_window_end_monotonic_ns is not owners[1]
        ):
            raise ValueError("accounting map provenance is invalid")
        event_items, deadline_items = attestation[7], attestation[8]
        for index, items in enumerate((event_items, deadline_items)):
            if type(items) is not tuple:
                raise ValueError("accounting map attestation is invalid")
            previous = 0
            for item in items:
                if type(item) is not tuple or len(item) != 2:
                    raise ValueError("accounting map attestation is invalid")
                product_id, value = item
                if type(product_id) is not int or product_id <= previous:
                    raise ValueError("accounting map attestation is invalid")
                if index == 0:
                    if type(value) is not UUID or value.int == 0:
                        raise ValueError("accounting map attestation is invalid")
                elif type(value) is not int:
                    raise ValueError("accounting map attestation is invalid")
                previous = product_id
        values = (
            self.limiter_instance_id,
            self.limiter_clock_epoch,
            self.limiter_accounting_generation,
            self.acquired_monotonic_ns,
            self.rest_event_id,
            self.rest_window_end_monotonic_ns,
            self.cooldown_epoch,
        )
        RatePermitAccounting(
            limiter_instance_id=attestation[0],
            limiter_clock_epoch=attestation[1],
            limiter_accounting_generation=attestation[2],
            acquired_monotonic_ns=attestation[3],
            rest_event_id=attestation[4],
            rest_window_end_monotonic_ns=attestation[5],
            product_event_ids=dict(event_items),
            product_window_end_monotonic_ns=dict(deadline_items),
            cooldown_epoch=attestation[6],
        )
        copied = RatePermitAccounting(
            limiter_instance_id=values[0],
            limiter_clock_epoch=values[1],
            limiter_accounting_generation=values[2],
            acquired_monotonic_ns=values[3],
            rest_event_id=values[4],
            rest_window_end_monotonic_ns=values[5],
            product_event_ids=dict(event_items),
            product_window_end_monotonic_ns=dict(deadline_items),
            cooldown_epoch=values[6],
        )
        if values != attestation[:7]:
            raise ValueError("accounting scalar provenance is invalid")
        return copied


@dataclass(frozen=True, slots=True)
class RatePermit:
    """Immutable evidence that both local budgets were consumed atomically."""

    acquired_at: float
    rest_weight: int
    product_operations: Mapping[int, int]
    accounting: RatePermitAccounting | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.acquired_at, bool)
            or not isinstance(self.acquired_at, (int, float))
            or not math.isfinite(float(self.acquired_at))
            or self.acquired_at < 0
        ):
            raise ValueError("acquired_at must be a nonnegative monotonic value")
        strict_positive_int(self.rest_weight, name="rest_weight")
        if not isinstance(self.product_operations, Mapping):
            raise ValueError("product_operations must be a mapping")
        copied = {
            strict_positive_int(product_id, name="product_id"): strict_positive_int(
                count, name="product operation count"
            )
            for product_id, count in self.product_operations.items()
        }
        object.__setattr__(self, "product_operations", MappingProxyType(copied))
        if self.accounting is not None:
            if type(self.accounting) is not RatePermitAccounting:
                raise ValueError("accounting must be exact RatePermitAccounting evidence")
            object.__setattr__(self, "accounting", self.accounting._defensive_copy())


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
        capacity = strict_positive_int(self.capacity, name="capacity")
        used = strict_nonnegative_int(self.rest_used, name="rest_used")
        remaining = strict_nonnegative_int(self.rest_remaining, name="rest_remaining")
        if used + remaining != capacity:
            raise ValueError("used and remaining REST quota must equal capacity")
        strict_nonnegative_int(self.waiter_count, name="waiter_count")
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
            strict_positive_int(product_id, name="product_id"): strict_positive_int(
                count, name="product operations used"
            )
            for product_id, count in self.product_operations_used.items()
        }
        object.__setattr__(self, "product_operations_used", MappingProxyType(products))


@dataclass(frozen=True, slots=True)
class QuotaObservation:
    """Validated but non-authoritative `/v2/rate_limits/quota` telemetry."""

    current_quota: int
    remaining_time_in_milliseconds: int
    observed_at: float

    def __post_init__(self) -> None:
        strict_nonnegative_int(self.current_quota, name="current_quota")
        remaining = strict_nonnegative_int(
            self.remaining_time_in_milliseconds,
            name="remaining_time_in_milliseconds",
        )
        if remaining > MAX_RATE_LIMIT_RESET_MS:
            raise ValueError("remaining_time_in_milliseconds exceeds one quota window")
        strict_monotonic_value(self.observed_at)


def parse_rate_limit_reset_ms(value: str) -> int:
    """Parse Delta's reset header strictly as milliseconds within one window."""
    if (
        type(value) is not str
        or not value
        or len(value) > len(str(MAX_RATE_LIMIT_RESET_MS))
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise ConfigurationError(context={"operation": "rate_limit_reset"})
    try:
        parsed = int(value, 10)
    except (ValueError, OverflowError):
        raise ConfigurationError(context={"operation": "rate_limit_reset"}) from None
    if parsed > MAX_RATE_LIMIT_RESET_MS:
        raise ConfigurationError(context={"operation": "rate_limit_reset"})
    return parsed


__all__ = [
    "CoordinationMode",
    "DELTA_RATE_LIMIT_POLICY",
    "MAX_RATE_LIMIT_RESET_MS",
    "POLICY_VERSION",
    "QuotaObservation",
    "ROUTE_WEIGHTS",
    "RateLimitPolicy",
    "RateLimitRoute",
    "RateLimitScope",
    "RateLimitSnapshot",
    "RatePermit",
    "RatePermitAccounting",
    "RateRequest",
    "ScopeKind",
    "parse_rate_limit_reset_ms",
]
