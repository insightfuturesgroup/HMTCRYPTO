"""Inert, fail-closed Delta India private-route profile contracts."""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
import textwrap
from dataclasses import FrozenInstanceError, replace
from enum import StrEnum
from pathlib import Path

import pytest

from hmt_crypto.errors import ConfigurationError
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
    PrivateRouteSpec,
    get_private_route_spec,
    resolve_private_route_template,
)

pytestmark = pytest.mark.unit


def _manifest(enum_type: type[StrEnum]) -> tuple[tuple[str, str], ...]:
    return tuple((member.name, member.value) for member in enum_type)


ENUMS = {
    PrivateRegionSupport: (("INDIA_ONLY", "india_only"),),
    PrivateRouteAccess: (("READ", "read"), ("WRITE", "write")),
    PrivateAuthentication: (("HMAC_SIGNED_EXTERNAL_USER", "hmac_signed_external_user"),),
    PrivatePaginationPolicy: (("NONE", "none"), ("FORWARD_CURSOR", "forward_cursor")),
    PrivateIdempotencyPolicy: (
        ("SAFE_READ", "safe_read"),
        ("NO_EXCHANGE_GUARANTEE", "no_exchange_guarantee"),
    ),
    PrivateEngineAccounting: (
        ("NONE", "none"),
        ("ONE_PRODUCT_OPERATION", "one_product_operation"),
        ("CANCELLATION_EXEMPT", "cancellation_exempt"),
        ("UNVERIFIED_DISABLED", "unverified_disabled"),
    ),
    PrivateDispatchCapability: (("READ_ONLY", "read_only"), ("NONE", "none")),
    PrivateProfileState: (
        ("REVIEWED", "reviewed"),
        ("DISABLED_PENDING_TESTNET", "disabled_pending_testnet"),
        ("OFFLINE_CODEC_ONLY", "offline_codec_only"),
    ),
    PrivateResponseSchema: tuple(
        (name.upper(), name)
        for name in (
            "balance_snapshot",
            "transaction_page",
            "margined_position_list",
            "latest_position",
            "order_page",
            "fill_page",
            "order",
            "leverage",
            "margin_mode",
            "margined_position",
            "acknowledgement",
        )
    ),
}


# route, verb, template, path, query, body, nested, response, bytes
ROWS: tuple[tuple[object, ...], ...] = (
    ("balances", "GET", "/v2/wallet/balances", (), (), (), (), "balance_snapshot", 524288),
    (
        "transactions",
        "GET",
        "/v2/wallet/transactions",
        (),
        (
            "asset_ids",
            "transaction_types",
            "start_time",
            "end_time",
            "after",
            "before",
            "page_size",
        ),
        (),
        (),
        "transaction_page",
        4194304,
    ),
    (
        "margined_positions",
        "GET",
        "/v2/positions/margined",
        (),
        ("product_ids", "contract_types"),
        (),
        (),
        "margined_position_list",
        4194304,
    ),
    (
        "latest_position",
        "GET",
        "/v2/positions",
        (),
        ("product_id",),
        (),
        (),
        "latest_position",
        524288,
    ),
    (
        "open_orders",
        "GET",
        "/v2/orders",
        (),
        (
            "product_ids",
            "states",
            "contract_types",
            "order_types",
            "start_time",
            "end_time",
            "after",
            "before",
            "page_size",
        ),
        (),
        (),
        "order_page",
        4194304,
    ),
    (
        "order_history",
        "GET",
        "/v2/orders/history",
        (),
        (
            "product_ids",
            "contract_types",
            "order_types",
            "start_time",
            "end_time",
            "after",
            "before",
            "page_size",
        ),
        (),
        (),
        "order_page",
        4194304,
    ),
    (
        "fills",
        "GET",
        "/v2/fills",
        (),
        ("product_ids", "contract_types", "start_time", "end_time", "after", "before", "page_size"),
        (),
        (),
        "fill_page",
        8388608,
    ),
    ("order_by_id", "GET", "/v2/orders/{order_id}", ("order_id",), (), (), (), "order", 524288),
    (
        "order_by_client_id",
        "GET",
        "/v2/orders/client_order_id/{client_oid}",
        ("client_oid",),
        (),
        (),
        (),
        "order",
        524288,
    ),
    (
        "get_leverage",
        "GET",
        "/v2/products/{product_id}/orders/leverage",
        ("product_id",),
        (),
        (),
        (),
        "leverage",
        524288,
    ),
    (
        "place_order",
        "POST",
        "/v2/orders",
        (),
        (),
        (
            "product_id",
            "size",
            "side",
            "order_type",
            "limit_price",
            "stop_order_type",
            "stop_price",
            "trail_amount",
            "stop_trigger_method",
            "bracket_stop_trigger_method",
            "bracket_stop_loss_limit_price",
            "bracket_stop_loss_price",
            "bracket_trail_amount",
            "bracket_take_profit_limit_price",
            "bracket_take_profit_price",
            "time_in_force",
            "mmp",
            "post_only",
            "reduce_only",
            "client_order_id",
        ),
        (),
        "order",
        1048576,
    ),
    (
        "modify_order",
        "PUT",
        "/v2/orders",
        (),
        (),
        (
            "id",
            "product_id",
            "size",
            "limit_price",
            "mmp",
            "post_only",
            "stop_price",
            "trail_amount",
        ),
        (),
        "order",
        1048576,
    ),
    ("cancel_order", "DELETE", "/v2/orders", (), (), ("id", "product_id"), (), "order", 1048576),
    (
        "cancel_all",
        "DELETE",
        "/v2/orders/all",
        (),
        (),
        ("product_id", "cancel_limit_orders", "cancel_stop_orders", "cancel_reduce_only_orders"),
        (),
        "acknowledgement",
        262144,
    ),
    (
        "set_leverage",
        "POST",
        "/v2/products/{product_id}/orders/leverage",
        ("product_id",),
        (),
        ("leverage",),
        (),
        "leverage",
        262144,
    ),
    (
        "set_margin_mode",
        "PUT",
        "/v2/users/margin_mode",
        (),
        (),
        ("margin_mode", "subaccount_user_id"),
        (),
        "margin_mode",
        262144,
    ),
    (
        "change_position_margin",
        "POST",
        "/v2/positions/change_margin",
        (),
        (),
        ("product_id", "delta_margin"),
        (),
        "margined_position",
        1048576,
    ),
    (
        "create_bracket",
        "POST",
        "/v2/orders/bracket",
        (),
        (),
        ("product_id", "stop_loss_order", "take_profit_order", "bracket_stop_trigger_method"),
        (
            "stop_loss_order.order_type",
            "stop_loss_order.stop_price",
            "stop_loss_order.trail_amount",
            "stop_loss_order.limit_price",
            "take_profit_order.order_type",
            "take_profit_order.stop_price",
            "take_profit_order.limit_price",
        ),
        "acknowledgement",
        262144,
    ),
    (
        "edit_bracket",
        "PUT",
        "/v2/orders/bracket",
        (),
        (),
        (
            "id",
            "product_id",
            "bracket_stop_loss_limit_price",
            "bracket_stop_loss_price",
            "bracket_take_profit_limit_price",
            "bracket_take_profit_price",
            "bracket_trail_amount",
            "bracket_stop_trigger_method",
        ),
        (),
        "acknowledgement",
        262144,
    ),
)


def test_private_route_profile_enum_manifests_are_exact() -> None:
    names = tuple(row[0] for row in ROWS)
    assert _manifest(PrivateRouteId) == tuple((str(name).upper(), str(name)) for name in names)
    for enum_type, expected in ENUMS.items():
        assert _manifest(enum_type) == expected
        assert all(type(member) is enum_type for member in enum_type)


def test_private_route_profile_snapshot_has_exact_19_rows() -> None:
    actual = tuple(
        (
            spec.route_id.value,
            spec.method,
            spec.path_template,
            spec.path_fields,
            spec.query_fields,
            spec.body_fields,
            spec.nested_body_fields,
            spec.response_schema.value,
            spec.max_response_bytes,
        )
        for spec in PRIVATE_ROUTE_SPECS
    )
    assert actual == ROWS
    assert tuple(spec.route_id for spec in PRIVATE_ROUTE_SPECS) == tuple(PrivateRouteId)


def test_private_route_request_field_allowlists_match_a3() -> None:
    from hmt_crypto.exchange import private_requests as request

    pairs = (
        (PrivateRouteId.MARGINED_POSITIONS, request.MarginedPositionQuery, "query_fields"),
        (PrivateRouteId.LATEST_POSITION, request.LatestPositionQuery, "query_fields"),
        (PrivateRouteId.OPEN_ORDERS, request.OpenOrderQuery, "query_fields"),
        (PrivateRouteId.ORDER_HISTORY, request.OrderHistoryQuery, "query_fields"),
        (PrivateRouteId.FILLS, request.FillQuery, "query_fields"),
        (PrivateRouteId.PLACE_ORDER, request.PlaceOrderRequest, "body_fields"),
        (PrivateRouteId.MODIFY_ORDER, request.ModifyOrderRequest, "body_fields"),
        (PrivateRouteId.CANCEL_ORDER, request.CancelOrderRequest, "body_fields"),
        (PrivateRouteId.CANCEL_ALL, request.CancelAllRequest, "body_fields"),
        (PrivateRouteId.SET_MARGIN_MODE, request.SetMarginModeRequest, "body_fields"),
        (PrivateRouteId.CHANGE_POSITION_MARGIN, request.ChangePositionMarginRequest, "body_fields"),
        (PrivateRouteId.CREATE_BRACKET, request.CreateBracketRequest, "body_fields"),
        (PrivateRouteId.EDIT_BRACKET, request.EditBracketRequest, "body_fields"),
    )
    for route_id, model, field_name in pairs:
        assert tuple(model.model_fields) == getattr(get_private_route_spec(route_id), field_name)
    assert tuple(request.WalletTransactionQuery.model_fields) == (
        "asset_id",
        "transaction_types",
        "start_time",
        "end_time",
        "after",
        "before",
        "page_size",
    )
    assert get_private_route_spec(PrivateRouteId.TRANSACTIONS).query_fields == (
        "asset_ids",
        "transaction_types",
        "start_time",
        "end_time",
        "after",
        "before",
        "page_size",
    )
    assert (
        tuple(request.OrderByIdQuery.model_fields)
        == get_private_route_spec(PrivateRouteId.ORDER_BY_ID).path_fields
    )
    assert tuple(request.OrderByClientIdQuery.model_fields) == ("client_order_id",)
    assert get_private_route_spec(PrivateRouteId.ORDER_BY_CLIENT_ID).path_fields == ("client_oid",)
    assert (
        tuple(request.LeverageQuery.model_fields)
        == get_private_route_spec(PrivateRouteId.GET_LEVERAGE).path_fields
    )
    set_leverage = get_private_route_spec(PrivateRouteId.SET_LEVERAGE)
    assert tuple(request.SetLeverageRequest.model_fields) == (
        *set_leverage.path_fields,
        *set_leverage.body_fields,
    )
    create_bracket = get_private_route_spec(PrivateRouteId.CREATE_BRACKET)
    assert create_bracket.nested_body_fields == tuple(
        ("stop_loss_order." + name) for name in request.BracketStopLossOrder.model_fields
    ) + tuple(("take_profit_order." + name) for name in request.BracketTakeProfitOrder.model_fields)


def test_private_route_response_byte_and_collection_bounds_are_exact() -> None:
    cursors = {
        "transactions": (10, 50, 200, 10000),
        "open_orders": (10, 50, 100, 5000),
        "order_history": (10, 50, 200, 10000),
        "fills": (10, 50, 200, 10000),
    }
    collections = {"balances": 256, "margined_positions": 4096}
    for spec in PRIVATE_ROUTE_SPECS:
        if spec.route_id.value in cursors:
            assert spec.pagination is PrivatePaginationPolicy.FORWARD_CURSOR
            assert (
                spec.default_page_size,
                spec.max_page_size,
                spec.max_pages,
                spec.max_items,
            ) == cursors[spec.route_id.value]
        else:
            assert spec.pagination is PrivatePaginationPolicy.NONE
            assert (spec.default_page_size, spec.max_page_size, spec.max_pages) == (
                None,
                None,
                None,
            )
            assert spec.max_items == collections.get(spec.route_id.value, 1)


def test_private_route_access_auth_retry_idempotency_and_dispatch_are_exact() -> None:
    disabled: set[PrivateRouteId] = set()
    operations = {
        "place_order": ("create_order", 5, PrivateEngineAccounting.ONE_PRODUCT_OPERATION),
        "modify_order": ("modify_order", 5, PrivateEngineAccounting.ONE_PRODUCT_OPERATION),
        "cancel_order": ("cancel_order", 5, PrivateEngineAccounting.CANCELLATION_EXEMPT),
        "cancel_all": ("cancel_all", 1, PrivateEngineAccounting.CANCELLATION_EXEMPT),
        "set_leverage": ("set_leverage", 1, PrivateEngineAccounting.NONE),
        "set_margin_mode": ("set_margin_mode", 1, PrivateEngineAccounting.NONE),
        "change_position_margin": ("add_margin", 5, PrivateEngineAccounting.NONE),
        "create_bracket": ("create_bracket", 1, PrivateEngineAccounting.UNVERIFIED_DISABLED),
        "edit_bracket": ("edit_bracket", 1, PrivateEngineAccounting.UNVERIFIED_DISABLED),
    }
    read_weights = {
        "balances": 3,
        "transactions": 10,
        "margined_positions": 3,
        "latest_position": 3,
        "open_orders": 3,
        "order_history": 10,
        "fills": 10,
        "order_by_id": 1,
        "order_by_client_id": 1,
        "get_leverage": 1,
    }
    for spec in PRIVATE_ROUTE_SPECS:
        assert spec.region_support is PrivateRegionSupport.INDIA_ONLY
        assert spec.authentication is PrivateAuthentication.HMAC_SIGNED_EXTERNAL_USER
        assert spec.auth_required is True and spec.rate_route_name == spec.route_id.value
        if spec.method == "GET":
            assert (
                spec.access,
                spec.retry_operation_name,
                spec.idempotency,
                spec.rest_weight,
                spec.engine_accounting,
            ) == (
                PrivateRouteAccess.READ,
                "safe_read",
                PrivateIdempotencyPolicy.SAFE_READ,
                read_weights[spec.route_id.value],
                PrivateEngineAccounting.NONE,
            )
            expected = (
                (PrivateDispatchCapability.NONE, PrivateProfileState.DISABLED_PENDING_TESTNET)
                if spec.route_id in disabled
                else (PrivateDispatchCapability.READ_ONLY, PrivateProfileState.REVIEWED)
            )
            assert (spec.dispatch_capability, spec.profile_state) == expected
            assert spec.is_read and spec.retry_safe and not spec.is_write
        else:
            operation, weight, engine = operations[spec.route_id.value]
            assert (
                spec.access,
                spec.retry_operation_name,
                spec.idempotency,
                spec.rest_weight,
                spec.engine_accounting,
            ) == (
                PrivateRouteAccess.WRITE,
                operation,
                PrivateIdempotencyPolicy.NO_EXCHANGE_GUARANTEE,
                weight,
                engine,
            )
            assert (spec.dispatch_capability, spec.profile_state) == (
                PrivateDispatchCapability.NONE,
                PrivateProfileState.OFFLINE_CODEC_ONLY,
            )
            assert spec.is_write and not spec.is_read and not spec.retry_safe


def _forge_member(enum_type: type[StrEnum], member: StrEnum) -> StrEnum:
    forged = str.__new__(enum_type, member.value)
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", member.value)
    return forged


def test_private_route_accessors_require_exact_positive_identity() -> None:
    class Wrong(StrEnum):
        BALANCES = "balances"

    class Text(str):
        pass

    for spec in PRIVATE_ROUTE_SPECS:
        assert get_private_route_spec(spec.route_id) is spec
        assert resolve_private_route_template(spec.method, spec.path_template) is spec
    for invalid in (
        "balances",
        Text("balances"),
        {},
        Wrong.BALANCES,
        _forge_member(PrivateRouteId, PrivateRouteId.BALANCES),
        object(),
    ):
        with pytest.raises(ConfigurationError) as raised:
            get_private_route_spec(invalid)  # type: ignore[arg-type]
        assert raised.value.context == {"operation": "resolve_private_route"}
    for method, path in (
        (Text("GET"), "/v2/wallet/balances"),
        ("GET", Text("/v2/wallet/balances")),
        (object(), object()),
    ):
        with pytest.raises(ConfigurationError) as raised:
            resolve_private_route_template(method, path)
        assert raised.value.context == {"operation": "resolve_private_route"}


def test_private_route_specs_are_frozen_owned_and_inert() -> None:
    assert PrivateRouteSpec.__dataclass_params__.eq is False
    assert all(
        value.default is inspect.Parameter.empty
        for value in inspect.signature(PrivateRouteSpec).parameters.values()
    )
    spec = PRIVATE_ROUTE_SPECS[0]
    with pytest.raises(FrozenInstanceError):
        spec.method = "POST"  # type: ignore[misc]
    assert spec != replace(spec)
    assert not any(
        hasattr(spec, name)
        for name in ("send", "sign", "dispatch", "execute", "request", "acquire")
    )
    caller_fields = ("asset_ids",)
    assert replace(spec, query_fields=caller_fields).query_fields is not caller_fields
    with pytest.raises((TypeError, ValueError)):
        replace(spec, query_fields=["unsafe"])  # type: ignore[arg-type]
    with pytest.raises((TypeError, ValueError)):
        replace(spec, max_items=True)


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("POST", "/v2/wallets/sub_account_balance_transfer"),
        ("GET", "/v2/wallets/sub_accounts_transfer_history"),
        ("GET", "/v2/wallet/transactions/download"),
        ("GET", "/v2/fills/history/download/csv"),
        ("POST", "/v2/withdrawals"),
        ("POST", "/v2/withdraw"),
        ("GET", "/v2/deposit"),
        ("GET", "/v2/deposit-address"),
        ("POST", "/v2/funds/move"),
        ("POST", "/v2/orders/batch"),
        ("POST", "/v2/positions/close_all"),
        ("PUT", "/v2/positions/auto_topup"),
        ("POST", "/v2/orders/mmp"),
        ("GET", "/v2/subaccounts/profile"),
        ("POST", "/v2/admin/users"),
        ("POST", "/v2/heartbeat"),
        ("POST", "/v2/deadman"),
        ("GET", "/v2/rate_limits/quota"),
    ),
)
def test_unknown_fund_admin_download_batch_and_close_routes_are_structurally_denied(
    method: str, path: str
) -> None:
    with pytest.raises(ConfigurationError) as raised:
        resolve_private_route_template(method, path)
    assert raised.value.context == {"operation": "resolve_private_route"}
    assert path not in str(raised.value) and path not in repr(raised.value)


def test_alternate_verbs_urls_queries_fragments_and_smuggling_are_denied() -> None:
    base = "/v2/wallet/balances"
    known = {(spec.method, spec.path_template) for spec in PRIVATE_ROUTE_SPECS}
    verbs = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "get")
    candidates = [
        (verb, spec.path_template)
        for spec in PRIVATE_ROUTE_SPECS
        for verb in verbs
        if (verb, spec.path_template) not in known
    ]
    candidates += [
        ("GET", value)
        for value in (
            "https://api.india.delta.exchange" + base,
            "//api.india.delta.exchange" + base,
            base + "?secret=x",
            base + "#x",
            "/v2//wallet/balances",
            "/v2/wallet/../wallet/balances",
            "/v2/wallet/%62alances",
            "/v2/wallet\\balances",
            base + " ",
            base + "\n",
            "/v2/wallet/bаlances",
        )
    ]
    for method, path in candidates:
        with pytest.raises(ConfigurationError):
            resolve_private_route_template(method, path)


def test_route_rejection_is_callback_free_and_secret_safe() -> None:
    calls: list[str] = []

    class Hostile:
        def __hash__(self) -> int:
            calls.append("hash")
            raise AssertionError

        def __eq__(self, _other: object) -> bool:
            calls.append("eq")
            raise AssertionError

        def __str__(self) -> str:
            calls.append("str")
            return "fixture-secret"

        def __iter__(self) -> object:
            calls.append("iter")
            raise AssertionError

    for call in (
        lambda: get_private_route_spec(Hostile()),
        lambda: resolve_private_route_template(Hostile(), Hostile()),
    ):  # type: ignore[arg-type]
        with pytest.raises(ConfigurationError) as raised:
            call()
        assert raised.value.context == {"operation": "resolve_private_route"}
        assert "fixture-secret" not in str(raised.value) and "fixture-secret" not in repr(
            raised.value
        )
    assert calls == []


def _subprocess(source: str) -> str:
    result = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(source)],
        cwd=Path(__file__).parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_route_row_leaf_first_and_facade_first_are_acyclic_and_identical() -> None:
    body = """
        import importlib
        import sys
        sys.path.insert(0, 'src')
        importlib.import_module('{first}')
        importlib.import_module('{second}')
        from hmt_crypto.exchange.delta import _private_route_rows as rows
        from hmt_crypto.exchange.delta import private_routes as routes
        print(
            len(routes.PRIVATE_ROUTE_SPECS),
            routes.PRIVATE_ROUTE_SPECS[0].route_id.value,
            type(rows.PRIVATE_ROUTE_ROWS) is tuple,
        )
    """
    first = _subprocess(
        body.format(
            first="hmt_crypto.exchange.delta._private_route_rows",
            second="hmt_crypto.exchange.delta.private_routes",
        )
    )
    second = _subprocess(
        body.format(
            first="hmt_crypto.exchange.delta.private_routes",
            second="hmt_crypto.exchange.delta._private_route_rows",
        )
    )
    assert first == second == "19 balances True"


def test_route_first_import_still_blocks_runtime_leaves() -> None:
    output = _subprocess("""
        import sys
        sys.path.insert(0, 'src')
        import hmt_crypto.exchange.delta as package
        import hmt_crypto.exchange.delta.private_routes as routes
        expected = (
            'PrivateRouteId', 'PrivateRegionSupport', 'PrivateRouteAccess',
            'PrivateAuthentication', 'PrivatePaginationPolicy',
            'PrivateIdempotencyPolicy', 'PrivateEngineAccounting',
            'PrivateDispatchCapability', 'PrivateProfileState',
            'PrivateResponseSchema', 'PrivateRouteSpec', 'PRIVATE_ROUTE_SPECS',
            'get_private_route_spec', 'resolve_private_route_template',
        )
        forbidden = (
            'httpx', 'rate_limit_policy', 'retry_policy', 'rate_limiter',
            'weighted_limiter', 'signer', 'client', 'transport', 'wire',
            'paginator', 'persistence', 'runtime',
        )
        root_absent = all(
            name not in package.__all__
            and name not in vars(package)
            and not hasattr(package, name)
            for name in expected
        )
        runtime_loaded = any(
            any(token in name for token in forbidden) for name in sys.modules
        )
        print(runtime_loaded, tuple(routes.__all__) == expected, root_absent)
    """)
    assert output == "False True True"


def test_private_route_module_has_no_io_or_execution_authority() -> None:
    root = Path(__file__).parents[2] / "src/hmt_crypto/exchange/delta"
    for name in ("private_routes.py", "_private_route_rows.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not any(
            token in item
            for item in imports
            for token in (
                "httpx",
                "asyncio",
                "socket",
                "rate_limit",
                "retry_policy",
                "signer",
                "client",
                "transport",
                "persistence",
            )
        )
        definitions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert definitions.isdisjoint(
            {"send", "sign", "acquire", "dispatch", "execute", "request", "encode", "parse"}
        )
