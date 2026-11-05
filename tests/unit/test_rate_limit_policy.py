"""Immutable Delta quota policy, scope, request, and evidence contracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import fields
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

import pytest
from rate_limit_test_support import limiter_api, request, scope

from hmt_crypto.errors import ConfigurationError

pytestmark = pytest.mark.unit


def test_official_policy_has_exact_version_quotas_and_weights() -> None:
    api = limiter_api()
    policy = api.DELTA_RATE_LIMIT_POLICY

    assert policy.version == "delta-docs-2026-11-04-private-v1"
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


def test_private_v1_policy_snapshot_is_atomic_and_complete() -> None:
    from hmt_crypto.exchange.delta.private_routes import (
        PRIVATE_ROUTE_SPECS,
        PrivateAuthentication,
        PrivateEngineAccounting,
        PrivateRouteId,
        PrivateRouteSpec,
    )
    from hmt_crypto.exchange.delta.routes import resolve_public_request

    api = limiter_api()
    policy = api.DELTA_RATE_LIMIT_POLICY
    manifest = (
        "products",
        "orderbook",
        "tickers",
        "open_orders",
        "open_positions",
        "balances",
        "candles",
        "place_order",
        "modify_order",
        "cancel_order",
        "add_position_margin",
        "order_history",
        "fills",
        "transactions",
        "batch_orders",
        "rate_limit_quota",
        "margined_positions",
        "latest_position",
        "order_by_id",
        "order_by_client_id",
        "get_leverage",
        "cancel_all",
        "set_leverage",
        "set_margin_mode",
        "change_position_margin",
        "create_bracket",
        "edit_bracket",
    )
    expected_weights = {
        "products": 3,
        "orderbook": 3,
        "tickers": 3,
        "open_orders": 3,
        "open_positions": 3,
        "balances": 3,
        "candles": 3,
        "place_order": 5,
        "modify_order": 5,
        "cancel_order": 5,
        "add_position_margin": 5,
        "order_history": 10,
        "fills": 10,
        "transactions": 10,
        "batch_orders": 25,
        "rate_limit_quota": 1,
        "margined_positions": 3,
        "latest_position": 3,
        "order_by_id": 1,
        "order_by_client_id": 1,
        "get_leverage": 1,
        "cancel_all": 1,
        "set_leverage": 1,
        "set_margin_mode": 1,
        "change_position_margin": 5,
        "create_bracket": 1,
        "edit_bracket": 1,
    }
    assert api.POLICY_VERSION == policy.version == "delta-docs-2026-11-04-private-v1"
    assert (policy.india_capacity, policy.global_capacity) == (20_000, 10_000)
    assert (policy.rest_window_seconds, policy.product_operations_per_second) == (300, 500)
    assert tuple(member.value for member in api.RateLimitRoute) == manifest
    assert {route.value: weight for route, weight in api.ROUTE_WEIGHTS.items()} == expected_weights
    assert tuple(spec.route_id for spec in PRIVATE_ROUTE_SPECS) == tuple(PrivateRouteId)
    assert len(PRIVATE_ROUTE_SPECS) == 19
    profile_snapshot = tuple(
        tuple(
            value.value if isinstance(value, StrEnum) else value
            for value in (getattr(spec, item.name) for item in fields(PrivateRouteSpec))
        )
        for spec in PRIVATE_ROUTE_SPECS
    )
    assert sha256(repr(profile_snapshot).encode("ascii")).hexdigest() == (
        "52440f6435794ffe2462b1f03ea6d51569b294f939b137b355e7f3e36a01e51b"
    )
    assert all(
        spec.authentication is PrivateAuthentication.HMAC_SIGNED_EXTERNAL_USER
        for spec in PRIVATE_ROUTE_SPECS
    )
    assert api.ScopeKind.EXTERNAL_USER.value == "external_user"
    engine = {spec.route_id.value: spec.engine_accounting for spec in PRIVATE_ROUTE_SPECS}
    assert engine["place_order"] is PrivateEngineAccounting.ONE_PRODUCT_OPERATION
    assert engine["modify_order"] is PrivateEngineAccounting.ONE_PRODUCT_OPERATION
    assert engine["cancel_order"] is PrivateEngineAccounting.CANCELLATION_EXEMPT
    assert engine["cancel_all"] is PrivateEngineAccounting.CANCELLATION_EXEMPT
    for spec in PRIVATE_ROUTE_SPECS:
        rate_route = api.RateLimitRoute(spec.rate_route_name)
        assert policy.weight_for(rate_route) == spec.rest_weight
    for route in (
        api.RateLimitRoute.PLACE_ORDER,
        api.RateLimitRoute.MODIFY_ORDER,
        api.RateLimitRoute.BATCH_ORDERS,
    ):
        with pytest.raises(ValueError, match="product"):
            api.RateRequest(route=route)
    for route in (api.RateLimitRoute.CANCEL_ORDER, api.RateLimitRoute.CANCEL_ALL):
        assert not api.RateRequest(route=route).product_operations
        with pytest.raises(ValueError, match="cancel"):
            api.RateRequest(route=route, product_operations={1: 1})
    public_routes = (
        ("/v2/products", api.RateLimitRoute.PRODUCTS),
        ("/v2/products/XBTUSD_TEST", api.RateLimitRoute.PRODUCTS),
        ("/v2/tickers", api.RateLimitRoute.TICKERS),
        ("/v2/tickers/XBTUSD_TEST", api.RateLimitRoute.TICKERS),
        ("/v2/l2orderbook/XBTUSD_TEST", api.RateLimitRoute.ORDERBOOK),
        ("/v2/history/candles", api.RateLimitRoute.CANDLES),
    )
    for path, expected_route in public_routes:
        public_spec = resolve_public_request("GET", path)
        assert public_spec.rate_route is expected_route
        assert policy.weight_for(expected_route) == 3


def test_every_private_profile_resolves_one_canonical_rate_route_and_weight() -> None:
    from hmt_crypto.exchange.delta.private_routes import PRIVATE_ROUTE_SPECS

    api = limiter_api()
    for spec in PRIVATE_ROUTE_SPECS:
        route = api.RateLimitRoute(spec.rate_route_name)
        assert route.value == spec.rate_route_name
        assert api.DELTA_RATE_LIMIT_POLICY.weight_for(route) == spec.rest_weight
        assert api.ROUTE_WEIGHTS[route] == spec.rest_weight


def _forge_route(api: object, member: StrEnum) -> object:
    route_type = api.RateLimitRoute  # type: ignore[attr-defined]
    forged = str.__new__(route_type, member.value)
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", member.value)
    return forged


def test_rate_route_admission_is_identity_exact_and_unknown_has_no_default() -> None:
    api = limiter_api()

    class Wrong(StrEnum):
        PRODUCTS = "products"

    class Text(str):
        pass

    forged = _forge_route(api, api.RateLimitRoute.PRODUCTS)
    for value in ("products", Text("products"), Wrong.PRODUCTS, forged, object()):
        with pytest.raises(ConfigurationError):
            api.DELTA_RATE_LIMIT_POLICY.weight_for(value)
        with pytest.raises(ValueError, match="route"):
            api.RateRequest(route=value)


def test_policy_constructor_rejects_forged_equal_route_keys_before_hash_or_equality() -> None:
    api = limiter_api()
    calls: list[str] = []

    class Hostile:
        def __hash__(self) -> int:
            calls.append("hash")
            raise AssertionError

        def __eq__(self, _other: object) -> bool:
            calls.append("eq")
            raise AssertionError

    class Integer(int):
        pass

    class EmittedMapping(Mapping[object, object]):
        def __init__(self, entries: tuple[tuple[object, object], ...]) -> None:
            self.entries = entries

        def __getitem__(self, key: object) -> object:
            raise KeyError(key)

        def __iter__(self) -> Iterator[object]:
            return iter(())

        def __len__(self) -> int:
            return len(self.entries)

        def items(self) -> object:
            return iter(self.entries)

    base = tuple(api.ROUTE_WEIGHTS.items())
    candidates = (
        EmittedMapping((*base, (_forge_route(api, api.RateLimitRoute.PRODUCTS), 99))),
        EmittedMapping((*base, (Hostile(), 99))),
        EmittedMapping(
            tuple(
                (route, Integer(weight) if route is api.RateLimitRoute.PRODUCTS else weight)
                for route, weight in base
            )
        ),
    )
    for weights in candidates:
        with pytest.raises(ValueError, match="route_weights"):
            api.RateLimitPolicy(
                version="hostile",
                india_capacity=1,
                global_capacity=1,
                rest_window_seconds=1,
                product_operations_per_second=1,
                route_weights=weights,
            )
    assert calls == []


def test_place_modify_require_and_both_cancel_routes_reject_product_operations() -> None:
    api = limiter_api()
    for route in (api.RateLimitRoute.PLACE_ORDER, api.RateLimitRoute.MODIFY_ORDER):
        with pytest.raises(ValueError, match="product"):
            api.RateRequest(route=route)
    for route in (api.RateLimitRoute.CANCEL_ORDER, api.RateLimitRoute.CANCEL_ALL):
        with pytest.raises(ValueError, match="cancel"):
            api.RateRequest(route=route, product_operations={1: 1})


def test_public_capacity_window_and_route_weights_are_preserved() -> None:
    from hmt_crypto.exchange.delta.routes import resolve_public_request

    api = limiter_api()
    requests = (
        ("/v2/products", "products", 3),
        ("/v2/products/XBTUSD_TEST", "products", 3),
        ("/v2/tickers", "tickers", 3),
        ("/v2/tickers/XBTUSD_TEST", "tickers", 3),
        ("/v2/l2orderbook/XBTUSD_TEST", "orderbook", 3),
        ("/v2/history/candles", "candles", 3),
    )
    assert api.DELTA_RATE_LIMIT_POLICY.capacity_for("india") == 20_000
    assert api.DELTA_RATE_LIMIT_POLICY.capacity_for("global") == 10_000
    assert api.DELTA_RATE_LIMIT_POLICY.rest_window_seconds == 300
    for path, route_name, weight in requests:
        spec = resolve_public_request("GET", path)
        assert spec.rate_route is api.RateLimitRoute(route_name)
        assert api.DELTA_RATE_LIMIT_POLICY.weight_for(spec.rate_route) == weight


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
