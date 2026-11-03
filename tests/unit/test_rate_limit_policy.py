"""Immutable Delta quota policy, scope, request, and evidence contracts."""

from __future__ import annotations

from decimal import Decimal

import pytest
from rate_limit_test_support import limiter_api, request, scope

from hmt_crypto.errors import ConfigurationError

pytestmark = pytest.mark.unit


def test_official_policy_has_exact_version_quotas_and_weights() -> None:
    api = limiter_api()
    policy = api.DELTA_RATE_LIMIT_POLICY

    assert policy.version == "delta-docs-2026-11-03"
    assert policy.capacity_for("india") == 20_000
    assert policy.capacity_for("global") == 10_000
    assert policy.rest_window_seconds == 300
    assert policy.product_operations_per_second == 500

    weight_three = {
        "products",
        "orderbook",
        "tickers",
        "open_orders",
        "open_positions",
        "balances",
        "candles",
    }
    weight_five = {"place_order", "modify_order", "cancel_order", "add_position_margin"}
    weight_ten = {"order_history", "fills", "transactions"}
    for name in weight_three:
        assert policy.weight_for(api.RateLimitRoute(name)) == 3
    for name in weight_five:
        assert policy.weight_for(api.RateLimitRoute(name)) == 5
    for name in weight_ten:
        assert policy.weight_for(api.RateLimitRoute(name)) == 10
    assert policy.weight_for(api.RateLimitRoute.BATCH_ORDERS) == 25
    assert policy.weight_for(api.RateLimitRoute.RATE_LIMIT_QUOTA) == 1


def test_unknown_route_is_not_silently_assigned_default_weight() -> None:
    api = limiter_api()
    with pytest.raises((ValueError, ConfigurationError)):
        api.DELTA_RATE_LIMIT_POLICY.weight_for("/v2/withdraw")
    with pytest.raises(ValueError):
        api.RateLimitRoute("withdraw")


def test_policy_snapshots_caller_owned_route_weight_mapping() -> None:
    api = limiter_api()
    weights = dict(api.ROUTE_WEIGHTS)
    policy = api.RateLimitPolicy(
        version="snapshot-test",
        india_capacity=100,
        global_capacity=100,
        rest_window_seconds=300,
        product_operations_per_second=500,
        route_weights=weights,
    )
    weights[api.RateLimitRoute.PRODUCTS] = 99
    assert policy.weight_for(api.RateLimitRoute.PRODUCTS) == 3


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"region": "INDIA"}, "region"),
        ({"environment": "production"}, "environment"),
        ({"external_id": ""}, "external"),
        ({"external_id": " "}, "external"),
        ({"external_id": "x" * 129}, "external"),
    ],
)
def test_scope_is_strict_and_environment_specific(kwargs: dict[str, object], message: str) -> None:
    api = limiter_api()
    values: dict[str, object] = {
        "region": "india",
        "environment": "testnet",
        "kind": api.ScopeKind.EXTERNAL_USER,
        "external_id": "user-42",
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError), match=message):
        api.RateLimitScope(**values)

    assert scope(region="india") != scope(region="global")
    assert scope(environment="testnet") != scope(environment="prod")


def test_scope_representation_does_not_expose_external_identifier() -> None:
    external_id = "fixture-api-key-must-never-be-logged"
    selected = scope(external_id=external_id)
    assert external_id not in repr(selected)
    assert external_id not in str(selected)


@pytest.mark.parametrize(
    "external_id",
    [
        " user-1",
        "user-1 ",
        "user\n1",
        "user\x001",
        "usеr-1",
    ],
)
def test_scope_rejects_noncanonical_or_confusable_external_identifiers(
    external_id: str,
) -> None:
    with pytest.raises(ValueError, match="external"):
        scope(external_id=external_id)


@pytest.mark.parametrize(
    "products",
    [
        {True: 1},
        {0: 1},
        {-1: 1},
        {1: True},
        {1: 0},
        {1: -1},
        {"1": 1},
    ],
)
def test_product_operations_require_positive_integer_ids_and_counts(
    products: dict[object, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        request(products=products)  # type: ignore[arg-type]


def test_rate_request_snapshots_caller_mapping_and_is_immutable() -> None:
    products = {7: 2}
    rate_request = request(products=products)
    products[7] = 400

    assert dict(rate_request.product_operations) == {7: 2}
    with pytest.raises(TypeError):
        rate_request.product_operations[7] = 3


@pytest.mark.parametrize("route", ["place_order", "modify_order", "batch_orders"])
def test_matching_engine_writes_cannot_bypass_product_operation_budget(route: str) -> None:
    api = limiter_api()
    with pytest.raises(ValueError, match="product"):
        api.RateRequest(route=api.RateLimitRoute(route))


def test_cancel_route_rejects_product_operations_because_cancels_are_exempt() -> None:
    api = limiter_api()
    with pytest.raises(ValueError, match="cancel"):
        api.RateRequest(route=api.RateLimitRoute.CANCEL_ORDER, product_operations={1: 1})


def test_request_and_policy_reject_non_mapping_inputs() -> None:
    api = limiter_api()
    with pytest.raises(ValueError, match="mapping"):
        api.RateRequest(  # type: ignore[arg-type]
            route=api.RateLimitRoute.PRODUCTS,
            product_operations=[(1, 1)],
        )
    with pytest.raises(ValueError, match="mapping"):
        api.RateLimitPolicy(  # type: ignore[arg-type]
            version="bad",
            india_capacity=1,
            global_capacity=1,
            rest_window_seconds=300,
            product_operations_per_second=500,
            route_weights=[],
        )


def test_batch_counts_items_and_cancel_is_product_exempt() -> None:
    api = limiter_api()
    batch = api.RateRequest(
        route=api.RateLimitRoute.BATCH_ORDERS,
        product_operations={1: 20, 2: 5},
    )
    cancel = api.RateRequest(route=api.RateLimitRoute.CANCEL_ORDER)
    assert api.DELTA_RATE_LIMIT_POLICY.weight_for(batch.route) == 25
    assert sum(batch.product_operations.values()) == 25
    assert api.DELTA_RATE_LIMIT_POLICY.weight_for(cancel.route) == 5
    assert dict(cancel.product_operations) == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", 0), ("1", 1), ("1500", 1500), ("300000", 300_000)],
)
def test_reset_header_is_strict_milliseconds(raw: str, expected: int) -> None:
    assert limiter_api().parse_rate_limit_reset_ms(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "-1", "+1", "1.0", " 1", "1 ", "٣", "300001", 1, True],
)
def test_invalid_reset_header_fails_safely(raw: object) -> None:
    with pytest.raises((TypeError, ValueError, ConfigurationError)):
        limiter_api().parse_rate_limit_reset_ms(raw)  # type: ignore[arg-type]


def test_exported_quota_and_limit_evidence_rejects_contradictory_direct_construction() -> None:
    api = limiter_api()
    with pytest.raises(ValueError):
        api.QuotaObservation(
            current_quota=-1,
            remaining_time_in_milliseconds=1,
            observed_at=0.0,
        )
    with pytest.raises(ValueError):
        api.RateLimitSnapshot(
            policy_version="test",
            capacity=1,
            rest_used=1,
            rest_remaining=1,
            blocked_until=None,
            waiter_count=0,
            product_operations_used={},
            closed=False,
        )
    with pytest.raises((ValueError, ConfigurationError)):
        api.RatePermit(acquired_at=-1, rest_weight=1, product_operations={})


def test_compatibility_facade_exports_retry_and_limiter_contracts() -> None:
    api = limiter_api()
    assert api.WeightedRateLimiter.__module__.endswith("weighted_limiter")
    assert api.RetryPolicy.__module__.endswith("retry_policy")
    assert api.RateLimitPolicy.__module__.endswith("rate_limit_policy")
    assert Decimal(api.DEFAULT_RETRY_POLICY.base_delay_seconds) > 0
