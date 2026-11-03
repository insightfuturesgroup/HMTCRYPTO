"""Sanitized support for Delta public REST contract tests."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hmt_crypto.exchange.delta.endpoints import get_delta_endpoints
from hmt_crypto.exchange.delta.rate_limiter import (
    RateLimitScope,
    ScopeKind,
    WeightedRateLimiter,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "delta"


def public_api() -> Any:
    """Load the Task 1.9 surface after tests have been collected cleanly."""
    try:
        base = import_module("hmt_crypto.exchange.base")
        client = import_module("hmt_crypto.exchange.delta.client")
        routes = import_module("hmt_crypto.exchange.delta.routes")
    except ModuleNotFoundError:
        pytest.fail("Delta public REST client API is missing", pytrace=False)
    return SimpleNamespace(base=base, client=client, routes=routes)


def fixture_bytes(name: str) -> bytes:
    """Return one synthetic, sanitized official-shape response fixture."""
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> dict[str, Any]:
    """Load one sanitized response fixture for controlled mutation."""
    value = json.loads(fixture_bytes(name))
    assert isinstance(value, dict)
    return value


class RecordingSleeper:
    """Retry sleeper that records exact delays without blocking."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


Handler = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


def new_client(
    handler: Handler,
    *,
    region: str = "india",
    environment: str = "testnet",
    sleeper: RecordingSleeper | None = None,
    l2_microseconds: bool = False,
) -> Any:
    """Build an owned client with an exact public-IP limiter scope."""
    api = public_api()
    endpoints = get_delta_endpoints(region, environment)
    scope = RateLimitScope(
        region=region,
        environment=environment,
        kind=ScopeKind.IP,
        external_id="contract-test-egress",
    )
    limiter = WeightedRateLimiter(scope)
    return api.client.DeltaPublicClient(
        endpoints=endpoints,
        limiter=limiter,
        user_agent="hmt-crypto-contract-tests/1.0",
        transport=httpx.MockTransport(handler),
        retry_policy=api.client.RetryPolicy(
            max_attempts=3,
            base_delay_seconds=Decimal("0.001"),
            max_delay_seconds=Decimal("0.1"),
            jitter_ratio=Decimal("0"),
        ),
        sleeper=sleeper or RecordingSleeper(),
        jitter_source=lambda: Decimal("0"),
        l2_timestamp_policy=(
            api.client.L2TimestampPolicy.INFER_MICROSECONDS_FROM_OFFICIAL_EXAMPLE
            if l2_microseconds
            else api.client.L2TimestampPolicy.DISABLED
        ),
    )


def json_response(
    request: httpx.Request,
    body: bytes,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Create one explicit JSON response bound to its request."""
    selected = {"Content-Type": "application/json", **(headers or {})}
    return httpx.Response(status_code, content=body, headers=selected, request=request)


__all__ = [
    "FIXTURES",
    "RecordingSleeper",
    "fixture_bytes",
    "fixture_json",
    "json_response",
    "new_client",
    "public_api",
]
