"""Immutable Delta quota policy, scope, request, and evidence models."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

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
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def strict_nonnegative_int(value: object, *, name: str) -> int:
    """Parse an actual nonnegative integer, rejecting booleans and coercion."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
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
        object.__setattr__(self, "route_weights", MappingProxyType(copied))

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
        return self.route_weights[canonical_route]


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
        object.__setattr__(self, "route", route)
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
    "RateRequest",
    "ScopeKind",
    "parse_rate_limit_reset_ms",
]
