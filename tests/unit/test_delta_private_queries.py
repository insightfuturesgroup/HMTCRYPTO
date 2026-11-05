"""Byte-exact, route-inferred Delta private-read target codecs."""

from __future__ import annotations

import ast
import copy
import inspect
import pickle
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path

import pytest

from hmt_crypto.domain.private_orders import OrderSide, OrderType
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta import private_query as codec
from hmt_crypto.exchange.delta.private_routes import (
    PRIVATE_ROUTE_SPECS,
    PrivateAuthentication,
    PrivateDispatchCapability,
    PrivateEngineAccounting,
    PrivateIdempotencyPolicy,
    PrivatePaginationPolicy,
    PrivateProfileState,
    PrivateRegionSupport,
    PrivateResponseSchema,
    PrivateRouteAccess,
    PrivateRouteId,
    get_private_route_spec,
)
from hmt_crypto.exchange.private_requests import (
    FillQuery,
    LatestPositionQuery,
    LeverageQuery,
    MarginedPositionContractTypeFilter,
    MarginedPositionQuery,
    OpenOrderQuery,
    OpenOrderStateFilter,
    OrderByClientIdQuery,
    OrderByIdQuery,
    OrderHistoryQuery,
    OrderTypeFilter,
    PlaceOrderRequest,
    PrivateContractTypeFilter,
    WalletTransactionQuery,
)

pytestmark = pytest.mark.unit

_STAMP = datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=UTC)
_END_STAMP = datetime(2026, 3, 1, 12, 34, 56, 789012, tzinfo=UTC)
_LEAP_STAMP = datetime(2024, 2, 29, 12, 34, 56, 789012, tzinfo=UTC)
_PROFILE_FIELDS = (
    "route_id",
    "region_support",
    "method",
    "path_template",
    "path_fields",
    "query_fields",
    "body_fields",
    "nested_body_fields",
    "response_schema",
    "max_response_bytes",
    "pagination",
    "default_page_size",
    "max_page_size",
    "max_pages",
    "max_items",
    "access",
    "authentication",
    "retry_operation_name",
    "idempotency",
    "rate_route_name",
    "rest_weight",
    "engine_accounting",
    "dispatch_capability",
    "profile_state",
)


def _assert_safe_failure(call: object, *, canary: str = "fixture-secret-query") -> None:
    with pytest.raises(ConfigurationError) as captured:
        call()  # type: ignore[operator]
    error = captured.value
    assert error.context == {"operation": "canonical_private_read_target"}
    assert error.args == ("configuration_error: Configuration is invalid",)
    rendered = f"{error!s} {error!r} {error.context!r} {getattr(error, '__notes__', ())!r}"
    assert canary not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def _target(query: object) -> object:
    return codec._canonical_private_read_target(query)  # type: ignore[attr-defined, arg-type, no-any-return]


def _parts(target: object) -> tuple[PrivateRouteId, str, bytes]:
    return target.route_id, target.path, target.query_string  # type: ignore[attr-defined, no-any-return]


def test_all_ten_read_routes_have_literal_canonical_targets() -> None:
    """Catch wrong route inference, field order, paths, enum text, or omission."""
    assert hasattr(codec, "_canonical_balances_target")
    cases: tuple[tuple[object, tuple[PrivateRouteId, str, bytes]], ...] = (
        (
            codec._canonical_balances_target(),  # type: ignore[attr-defined]
            (PrivateRouteId.BALANCES, "/v2/wallet/balances", b""),
        ),
        (
            WalletTransactionQuery(
                asset_id=17,
                start_time=_STAMP,
                end_time=_END_STAMP,
                after="A&B=C",
                page_size=50,
            ),
            (
                PrivateRouteId.TRANSACTIONS,
                "/v2/wallet/transactions",
                b"?asset_ids=17&start_time=1767323045000006&end_time=1772368496789012"
                b"&after=A%26B%3DC&page_size=50",
            ),
        ),
        (
            MarginedPositionQuery(
                product_ids=(9, 7),
                contract_types=(
                    MarginedPositionContractTypeFilter.PERPETUAL_FUTURES,
                    MarginedPositionContractTypeFilter.CALL_OPTIONS,
                ),
            ),
            (
                PrivateRouteId.MARGINED_POSITIONS,
                "/v2/positions/margined",
                b"?product_ids=9,7&contract_types=perpetual_futures,call_options",
            ),
        ),
        (
            LatestPositionQuery(product_id=27),
            (PrivateRouteId.LATEST_POSITION, "/v2/positions", b"?product_id=27"),
        ),
        (
            OpenOrderQuery(
                product_ids=(27, 99),
                states=(OpenOrderStateFilter.OPEN, OpenOrderStateFilter.PENDING),
                contract_types=(
                    PrivateContractTypeFilter.FUTURES,
                    PrivateContractTypeFilter.PUT_OPTIONS,
                ),
                order_types=(OrderTypeFilter.MARKET, OrderTypeFilter.ALL_STOP),
                start_time=_STAMP,
                end_time=_END_STAMP,
                after="A&B=C",
                page_size=50,
            ),
            (
                PrivateRouteId.OPEN_ORDERS,
                "/v2/orders",
                b"?product_ids=27,99&states=open,pending&contract_types=futures,put_options"
                b"&order_types=market,all_stop&start_time=1767323045000006"
                b"&end_time=1772368496789012&after=A%26B%3DC&page_size=50",
            ),
        ),
        (
            OrderHistoryQuery(
                product_ids=(3, 2),
                contract_types=(PrivateContractTypeFilter.CALL_OPTIONS,),
                order_types=(OrderTypeFilter.STOP_LIMIT,),
                start_time=_STAMP,
                end_time=_END_STAMP,
                before="history:before",
                page_size=11,
            ),
            (
                PrivateRouteId.ORDER_HISTORY,
                "/v2/orders/history",
                b"?product_ids=3,2&contract_types=call_options&order_types=stop_limit"
                b"&start_time=1767323045000006&end_time=1772368496789012"
                b"&before=history%3Abefore&page_size=11",
            ),
        ),
        (
            FillQuery(
                product_ids=(44,),
                contract_types=(PrivateContractTypeFilter.PERPETUAL_FUTURES,),
                start_time=_STAMP,
                end_time=_END_STAMP,
                after="fill%cursor",
                page_size=7,
            ),
            (
                PrivateRouteId.FILLS,
                "/v2/fills",
                b"?product_ids=44&contract_types=perpetual_futures"
                b"&start_time=1767323045000006&end_time=1772368496789012"
                b"&after=fill%25cursor&page_size=7",
            ),
        ),
        (
            OrderByIdQuery(order_id=9223372036854775807),
            (
                PrivateRouteId.ORDER_BY_ID,
                "/v2/orders/9223372036854775807",
                b"",
            ),
        ),
        (
            OrderByClientIdQuery(client_order_id="client:alpha.beta_1-2"),
            (
                PrivateRouteId.ORDER_BY_CLIENT_ID,
                "/v2/orders/client_order_id/client%3Aalpha.beta_1-2",
                b"",
            ),
        ),
        (
            LeverageQuery(product_id=81),
            (
                PrivateRouteId.GET_LEVERAGE,
                "/v2/products/81/orders/leverage",
                b"",
            ),
        ),
    )

    for source, expected in cases:
        target = source if expected[0] is PrivateRouteId.BALANCES else _target(source)
        assert type(target) is codec._CanonicalPrivateReadTarget  # type: ignore[attr-defined]
        assert _parts(target) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    (
        (WalletTransactionQuery(), b"?page_size=10"),
        (WalletTransactionQuery(asset_id=5), b"?asset_ids=5&page_size=10"),
        (WalletTransactionQuery(start_time=_STAMP), b"?start_time=1767323045000006&page_size=10"),
        (WalletTransactionQuery(end_time=_STAMP), b"?end_time=1767323045000006&page_size=10"),
        (WalletTransactionQuery(after="a?b"), b"?after=a%3Fb&page_size=10"),
        (WalletTransactionQuery(before="a#b"), b"?before=a%23b&page_size=10"),
        (WalletTransactionQuery(page_size=1), b"?page_size=1"),
        (MarginedPositionQuery(product_ids=(1, 2)), b"?product_ids=1,2"),
        (
            MarginedPositionQuery(contract_types=(MarginedPositionContractTypeFilter.PUT_OPTIONS,)),
            b"?contract_types=put_options",
        ),
        (OpenOrderQuery(product_ids=(2, 1)), b"?product_ids=2,1&page_size=10"),
        (OpenOrderQuery(states=(OpenOrderStateFilter.PENDING,)), b"?states=pending&page_size=10"),
        (
            OpenOrderQuery(contract_types=(PrivateContractTypeFilter.FUTURES,)),
            b"?contract_types=futures&page_size=10",
        ),
        (
            OpenOrderQuery(order_types=(OrderTypeFilter.STOP_MARKET,)),
            b"?order_types=stop_market&page_size=10",
        ),
        (OpenOrderQuery(start_time=_STAMP), b"?start_time=1767323045000006&page_size=10"),
        (OpenOrderQuery(end_time=_STAMP), b"?end_time=1767323045000006&page_size=10"),
        (OpenOrderQuery(after="after=1"), b"?after=after%3D1&page_size=10"),
        (OpenOrderQuery(before="before&1"), b"?before=before%261&page_size=10"),
        (OpenOrderQuery(page_size=50), b"?page_size=50"),
        (OrderHistoryQuery(product_ids=(4,)), b"?product_ids=4&page_size=10"),
        (
            OrderHistoryQuery(contract_types=(PrivateContractTypeFilter.CALL_OPTIONS,)),
            b"?contract_types=call_options&page_size=10",
        ),
        (
            OrderHistoryQuery(order_types=(OrderTypeFilter.LIMIT,)),
            b"?order_types=limit&page_size=10",
        ),
        (OrderHistoryQuery(start_time=_STAMP), b"?start_time=1767323045000006&page_size=10"),
        (OrderHistoryQuery(end_time=_STAMP), b"?end_time=1767323045000006&page_size=10"),
        (OrderHistoryQuery(after="x/y"), b"?after=x%2Fy&page_size=10"),
        (OrderHistoryQuery(before="x:y"), b"?before=x%3Ay&page_size=10"),
        (OrderHistoryQuery(page_size=9), b"?page_size=9"),
        (FillQuery(product_ids=(6,)), b"?product_ids=6&page_size=10"),
        (
            FillQuery(contract_types=(PrivateContractTypeFilter.PUT_OPTIONS,)),
            b"?contract_types=put_options&page_size=10",
        ),
        (FillQuery(start_time=_STAMP), b"?start_time=1767323045000006&page_size=10"),
        (FillQuery(end_time=_STAMP), b"?end_time=1767323045000006&page_size=10"),
        (FillQuery(after="x+y"), b"?after=x%2By&page_size=10"),
        (FillQuery(before="x%y"), b"?before=x%25y&page_size=10"),
        (FillQuery(page_size=12), b"?page_size=12"),
    ),
)
def test_each_optional_query_field_has_one_literal_wire_case(
    query: object, expected: bytes
) -> None:
    """Catch accidental field omission, sorting, renaming, or default removal."""
    assert _parts(_target(query))[2] == expected


def test_cursor_percent_encoding_covers_every_visible_ascii_byte() -> None:
    """Catch URL quoting drift, lowercase hex, `+` spaces, or delimiter leaks."""
    visible = "".join(chr(value) for value in range(0x21, 0x7F))
    expected = (
        b"%21%22%23%24%25%26%27%28%29%2A%2B%2C-.%2F0123456789"
        b"%3A%3B%3C%3D%3E%3F%40ABCDEFGHIJKLMNOPQRSTUVWXYZ%5B%5C%5D%5E_"
        b"%60abcdefghijklmnopqrstuvwxyz%7B%7C%7D~"
    )
    target = _target(OpenOrderQuery(after=visible))
    assert _parts(target)[2] == b"?after=" + expected + b"&page_size=10"

    maximum = "A" * 1024
    assert _parts(_target(FillQuery(before=maximum)))[2] == (
        b"?before=" + b"A" * 1024 + b"&page_size=10"
    )


@pytest.mark.parametrize(
    ("client_id", "expected"),
    (
        (".", None),
        ("..", None),
        ("a.b", "/v2/orders/client_order_id/a.b"),
        ("a:b", "/v2/orders/client_order_id/a%3Ab"),
        ("a_b-c", "/v2/orders/client_order_id/a_b-c"),
        ("A" * 32, "/v2/orders/client_order_id/" + "A" * 32),
    ),
)
def test_client_order_id_uses_a_distinct_segment_encoder(
    client_id: str, expected: str | None
) -> None:
    """Catch path traversal or query-style encoding in client-ID paths."""
    query = OrderByClientIdQuery(client_order_id=client_id)
    if expected is None:
        _assert_safe_failure(lambda: _target(query))
    else:
        assert _parts(_target(query))[1:] == (expected, b"")


def test_epoch_microseconds_use_integer_utc_arithmetic_only() -> None:
    """Catch float timestamp conversion, leap-day loss, or timezone widening."""
    local_floor_plus_one = datetime(2000, 1, 1, 0, 0, 0, 1, tzinfo=UTC)
    assert _parts(_target(OpenOrderQuery(start_time=local_floor_plus_one)))[2] == (
        b"?start_time=946684800000001&page_size=10"
    )
    assert _parts(_target(FillQuery(end_time=_LEAP_STAMP)))[2] == (
        b"?end_time=1709210096789012&page_size=10"
    )

    class TimestampTrap(datetime):
        def timestamp(self) -> float:
            raise AssertionError("timestamp callback ran")

    for corrupted in (
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
        datetime.max.replace(tzinfo=UTC),
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))),
        TimestampTrap(2026, 1, 1, tzinfo=UTC),
    ):
        valid = OpenOrderQuery(start_time=_STAMP)
        object.__setattr__(valid, "start_time", corrupted)
        _assert_safe_failure(lambda valid=valid: _target(valid))


def test_exact_query_class_and_corrupted_fields_fail_before_encoding() -> None:
    """Catch structural query admission or trust in frozen-but-corrupted slots."""
    valid_and_corruption: tuple[tuple[object, str, object], ...] = (
        (WalletTransactionQuery(), "page_size", 0),
        (MarginedPositionQuery(), "product_ids", (0,)),
        (LatestPositionQuery(product_id=1), "product_id", 0),
        (OpenOrderQuery(), "states", ("open",)),
        (OrderHistoryQuery(), "order_types", ("limit",)),
        (FillQuery(), "contract_types", (PrivateContractTypeFilter.FUTURES,) * 2),
        (OrderByIdQuery(order_id=1), "order_id", True),
        (OrderByClientIdQuery(client_order_id="safe"), "client_order_id", "a/b"),
        (LeverageQuery(product_id=1), "product_id", -1),
    )
    for query, field_name, replacement in valid_and_corruption:
        object.__setattr__(query, field_name, replacement)
        _assert_safe_failure(lambda query=query: _target(query))

    class Derived(OpenOrderQuery):
        pass

    _assert_safe_failure(lambda: _target(Derived()))
    _assert_safe_failure(lambda: _target({"page_size": 10}))
    _assert_safe_failure(
        lambda: _target(
            PlaceOrderRequest(
                product_id=1,
                size=1,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET_ORDER,
            )
        )
    )


def test_hostile_corrupted_values_do_not_run_caller_callbacks() -> None:
    """Catch validation paths that iterate, compare, hash, or format hostile values."""
    calls: list[str] = []

    class HostileTuple(tuple[object, ...]):
        def __iter__(self):
            calls.append("iter")
            raise AssertionError

    class HostileText(str):
        def __str__(self) -> str:
            calls.append("str")
            return "fixture-secret-query"

        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError

        def __hash__(self) -> int:
            calls.append("hash")
            raise AssertionError

    class HostileInt(int):
        def __int__(self) -> int:
            calls.append("int")
            raise AssertionError

        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError

    cases = (
        (OpenOrderQuery(), "product_ids", HostileTuple((1,))),
        (OpenOrderQuery(), "after", HostileText("fixture-secret-query")),
        (LatestPositionQuery(product_id=1), "product_id", HostileInt(1)),
    )
    for query, field_name, hostile in cases:
        object.__setattr__(query, field_name, hostile)
        _assert_safe_failure(lambda query=query: _target(query))
    assert calls == []


def test_wallet_transaction_type_filter_remains_disabled_after_corruption() -> None:
    """Catch A5 emitting the unresolved `transaction_types` wire profile."""

    class ForeignType(StrEnum):
        FUNDING = "funding"

    for replacement in ((ForeignType.FUNDING,), ("funding",)):
        query = WalletTransactionQuery()
        object.__setattr__(query, "transaction_types", replacement)
        _assert_safe_failure(lambda query=query: _target(query))


def test_targets_attest_exact_a4_read_profiles_and_declared_fields() -> None:
    """Catch codec/profile drift or an upgrade of held read profiles."""
    targets = (
        codec._canonical_balances_target(),  # type: ignore[attr-defined]
        _target(WalletTransactionQuery()),
        _target(MarginedPositionQuery()),
        _target(LatestPositionQuery(product_id=1)),
        _target(OpenOrderQuery()),
        _target(OrderHistoryQuery()),
        _target(FillQuery()),
        _target(OrderByIdQuery(order_id=1)),
        _target(OrderByClientIdQuery(client_order_id="safe")),
        _target(LeverageQuery(product_id=1)),
    )
    assert tuple(target.route_id for target in targets) == tuple(PrivateRouteId)[:10]
    for target in targets:
        spec = get_private_route_spec(target.route_id)
        assert spec.method == "GET"
        assert spec.region_support is PrivateRegionSupport.INDIA_ONLY
        assert spec.access is PrivateRouteAccess.READ
        assert spec.authentication is PrivateAuthentication.HMAC_SIGNED_EXTERNAL_USER
        assert spec.body_fields == () and spec.nested_body_fields == ()
        if target.route_id in (PrivateRouteId.TRANSACTIONS, PrivateRouteId.FILLS):
            assert spec.dispatch_capability is PrivateDispatchCapability.NONE
            assert spec.profile_state is PrivateProfileState.DISABLED_PENDING_TESTNET
        else:
            assert spec.dispatch_capability is PrivateDispatchCapability.READ_ONLY
            assert spec.profile_state is PrivateProfileState.REVIEWED

        keys = ()
        if target.query_string:
            keys = tuple(
                pair.split(b"=", 1)[0].decode("ascii")
                for pair in target.query_string[1:].split(b"&")
            )
        assert keys == tuple(field for field in spec.query_fields if field in keys)


def test_malformed_a4_spec_rejects_before_query_encoding() -> None:
    """Catch trust in mutable route metadata or raw route/method/path inputs."""
    spec = get_private_route_spec(PrivateRouteId.OPEN_ORDERS)
    original = spec.method
    object.__setattr__(spec, "method", "POST")
    try:
        _assert_safe_failure(lambda: _target(OpenOrderQuery(after="fixture-secret-query")))
    finally:
        object.__setattr__(spec, "method", original)

    signature = inspect.signature(codec._canonical_private_read_target)  # type: ignore[attr-defined]
    assert tuple(signature.parameters) == ("query",)
    assert not hasattr(codec, "request")
    assert not hasattr(codec, "resolve")
    assert len(PRIVATE_ROUTE_SPECS) == 19


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("route_id", PrivateRouteId.BALANCES),
        ("region_support", "india_only"),
        ("method", "POST"),
        ("path_template", "/v2/fixture-secret-query"),
        ("path_fields", ("fixture-secret-query",)),
        ("query_fields", ("product_ids",)),
        ("body_fields", ("fixture-secret-query",)),
        ("nested_body_fields", ("fixture-secret-query.value",)),
        ("response_schema", PrivateResponseSchema.FILL_PAGE),
        ("max_response_bytes", 1),
        ("pagination", PrivatePaginationPolicy.NONE),
        ("default_page_size", None),
        ("max_page_size", None),
        ("max_pages", None),
        ("max_items", 1),
        ("access", PrivateRouteAccess.WRITE),
        ("authentication", "hmac_signed_external_user"),
        ("retry_operation_name", "fixture-secret-query"),
        ("idempotency", PrivateIdempotencyPolicy.NO_EXCHANGE_GUARANTEE),
        ("rate_route_name", "fixture-secret-query"),
        ("rest_weight", 99),
        ("engine_accounting", PrivateEngineAccounting.ONE_PRODUCT_OPERATION),
        ("dispatch_capability", PrivateDispatchCapability.NONE),
        ("profile_state", PrivateProfileState.DISABLED_PENDING_TESTNET),
    ),
)
def test_every_a4_profile_field_is_bound_to_immutable_evidence(
    field: str,
    replacement: object,
) -> None:
    """Catch drift in any of the 24 accepted A4 route-profile fields."""
    spec = get_private_route_spec(PrivateRouteId.OPEN_ORDERS)
    original = object.__getattribute__(spec, field)
    object.__setattr__(spec, field, replacement)
    try:
        _assert_safe_failure(lambda: _target(OpenOrderQuery(after="fixture-secret-query")))
    finally:
        object.__setattr__(spec, field, original)


def test_every_missing_a4_profile_slot_has_one_stable_safe_error() -> None:
    """Catch raw AttributeError from any deleted accepted profile slot."""
    spec = get_private_route_spec(PrivateRouteId.OPEN_ORDERS)
    for field in _PROFILE_FIELDS:
        original = object.__getattribute__(spec, field)
        object.__delattr__(spec, field)
        try:
            _assert_safe_failure(lambda: _target(OpenOrderQuery(after="fixture-secret-query")))
        finally:
            object.__setattr__(spec, field, original)


def test_hostile_a4_scalar_and_tuple_elements_never_execute_callbacks() -> None:
    """Reject subclasses before equality, hashing, iteration, or rendering."""

    class HostileText(str):
        calls = 0

        def __eq__(self, other: object) -> bool:
            del other
            type(self).calls += 1
            raise AssertionError("fixture-secret-query")

        def __hash__(self) -> int:
            type(self).calls += 1
            raise AssertionError("fixture-secret-query")

        def startswith(self, prefix: object, *args: object) -> bool:
            del prefix, args
            type(self).calls += 1
            raise AssertionError("fixture-secret-query")

    spec = get_private_route_spec(PrivateRouteId.OPEN_ORDERS)
    cases = (
        ("method", HostileText("GET")),
        ("query_fields", (HostileText("product_ids"),)),
        ("retry_operation_name", HostileText("safe_read")),
    )
    for field, replacement in cases:
        original = object.__getattribute__(spec, field)
        object.__setattr__(spec, field, replacement)
        try:
            _assert_safe_failure(lambda: _target(OpenOrderQuery(after="fixture-secret-query")))
        finally:
            object.__setattr__(spec, field, original)
    assert HostileText.calls == 0


def test_target_values_are_sealed_nonserializable_owned_scalars() -> None:
    """Catch target mutation or leakage of selectors through repr/serialization."""
    target = _target(OpenOrderQuery(after="fixture-secret-query"))
    assert not hasattr(target, "__dict__")
    rendered = repr(target)
    assert "OPEN_ORDERS" in rendered
    assert "fixture-secret-query" not in rendered
    assert "after" not in rendered
    with pytest.raises(TypeError):
        codec._CanonicalPrivateReadTarget(  # type: ignore[attr-defined, call-arg]
            route_id=PrivateRouteId.OPEN_ORDERS,
            path="/v2/orders",
            query_string=b"?page_size=10",
        )
    for operation in (
        lambda: copy.copy(target),
        lambda: copy.deepcopy(target),
        lambda: pickle.dumps(target),
        lambda: target.__reduce__(),
        lambda: target.__reduce_ex__(4),
        lambda: target.__getstate__(),
    ):
        with pytest.raises(TypeError):
            operation()
    with pytest.raises((AttributeError, TypeError)):
        target.path = "/fixture-secret-query"  # type: ignore[misc]


def test_query_module_contains_no_origin_hash_signer_http_or_io_authority() -> None:
    """Catch a pure codec growing request, signing, runtime, or I/O authority."""
    tree = ast.parse(Path(codec.__file__ or "").read_text())
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    denied = {
        "asyncio",
        "hashlib",
        "hmac",
        "httpx",
        "logging",
        "os",
        "socket",
        "threading",
        "hmt_crypto.exchange.delta.private_origin",
        "hmt_crypto.exchange.delta.signer",
        "hmt_crypto.exchange.delta.transport",
        "hmt_crypto.persistence",
    }
    assert imports.isdisjoint(denied)
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))
