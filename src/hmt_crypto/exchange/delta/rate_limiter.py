"""Stable facade for Delta quota scheduling and retry policy primitives.

The implementation is split by responsibility: immutable policy/models live in
``rate_limit_policy``, asynchronous scheduling in ``weighted_limiter``, ordinary
scope registry ownership here, and pure retry decisions in ``retry_policy``.
"""

from collections.abc import Callable

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.rate_limit_policy import (
    DELTA_RATE_LIMIT_POLICY,
    POLICY_VERSION,
    ROUTE_WEIGHTS,
    CoordinationMode,
    QuotaObservation,
    RateLimitPolicy,
    RateLimitRoute,
    RateLimitScope,
    RateLimitSnapshot,
    RatePermit,
    RateRequest,
    ScopeKind,
    parse_rate_limit_reset_ms,
)
from hmt_crypto.exchange.delta.retry_policy import (
    DEFAULT_RETRY_POLICY,
    OperationClass,
    RetryAction,
    RetryDecision,
    RetryPolicy,
    classify_retry,
)
from hmt_crypto.exchange.delta.weighted_limiter import WeightedRateLimiter


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
