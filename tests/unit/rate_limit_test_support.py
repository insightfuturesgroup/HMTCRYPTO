"""Shared deterministic fixtures for Delta rate-limit unit tests."""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Any

import pytest


def limiter_api() -> Any:
    """Load the stable public rate-limiter facade."""
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
