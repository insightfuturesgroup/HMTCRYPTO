"""Contract tests for inert Delta private mutation response parsing."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from delta_private_parser_test_support import (
    CANCEL_ALL_FIXTURE,
    CREATE_BRACKET_FIXTURE,
    EDIT_BRACKET_FIXTURE,
    LEVERAGE_FIXTURE,
    MARGIN_MODE_FIXTURE,
    ORDER_FIXTURE,
    POSITION_FIXTURE,
    fixture_bytes,
)

from hmt_crypto.domain import private_orders, private_positions
from hmt_crypto.errors import (
    AmbiguousExchangeResult,
    AuthError,
    OrderRejected,
    SignatureExpiredError,
    UnauthorizedApiAccessError,
)
from hmt_crypto.exchange.delta import private_mutation_parsers as subject
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId, get_private_route_spec
from hmt_crypto.exchange.private_requests import (
    BracketStopLossOrder,
    CancelAllRequest,
    CancelOrderRequest,
    ChangePositionMarginRequest,
    CreateBracketRequest,
    EditBracketRequest,
    ModifyOrderRequest,
    PlaceOrderRequest,
    RequestMarginMode,
    SetLeverageRequest,
    SetMarginModeRequest,
)


def _place() -> PlaceOrderRequest:
    return PlaceOrderRequest(
        product_id=27,
        size=2,
        side=private_orders.OrderSide.BUY,
        order_type=private_orders.OrderType.LIMIT_ORDER,
        limit_price=Decimal("123.45"),
        reduce_only=False,
        client_order_id="HMT_FIXTURE_1",
    )


def _modify() -> ModifyOrderRequest:
    return ModifyOrderRequest(
        id=7001,
        product_id=27,
        size=2,
        limit_price=Decimal("123.45"),
        mmp=private_orders.MmpLevel.DISABLED,
    )


def _order_body(**changes: object) -> bytes:
    envelope = json.loads(fixture_bytes("orders", ORDER_FIXTURE))
    assert type(envelope) is dict and type(envelope["result"]) is dict
    envelope["result"].update(changes)
    return json.dumps(envelope, separators=(",", ":")).encode()


def test_all_nine_success_wrappers_return_exact_a2_models() -> None:
    order_body = fixture_bytes("orders", ORDER_FIXTURE)
    place = subject.parse_place_order_response(
        order_body, status_code=200, request=_place(), external_user_id="12345"
    )
    modify = subject.parse_modify_order_response(
        order_body, status_code=200, request=_modify(), external_user_id="12345"
    )
    cancel = subject.parse_cancel_order_response(
        order_body,
        status_code=200,
        request=CancelOrderRequest(id=7001, product_id=27),
        external_user_id="12345",
    )
    cancel_all = subject.parse_cancel_all_response(
        fixture_bytes("cancel_all", CANCEL_ALL_FIXTURE),
        status_code=200,
        request=CancelAllRequest(
            product_id=27,
            cancel_limit_orders=True,
            cancel_stop_orders=False,
            cancel_reduce_only_orders=False,
        ),
    )
    leverage = subject.parse_set_leverage_response(
        fixture_bytes("leverage", LEVERAGE_FIXTURE),
        status_code=200,
        request=SetLeverageRequest(product_id=27, leverage=Decimal("5")),
    )
    margin_mode = subject.parse_set_margin_mode_response(
        fixture_bytes("margin_mode", MARGIN_MODE_FIXTURE),
        status_code=200,
        request=SetMarginModeRequest(
            margin_mode=RequestMarginMode.PORTFOLIO, subaccount_user_id="12345"
        ),
    )
    position = subject.parse_change_position_margin_response(
        fixture_bytes("position_margin", POSITION_FIXTURE),
        status_code=200,
        request=ChangePositionMarginRequest(product_id=27, delta_margin=Decimal("10")),
        external_user_id="12345",
    )
    create = subject.parse_create_bracket_response(
        fixture_bytes("brackets", CREATE_BRACKET_FIXTURE),
        status_code=200,
        request=CreateBracketRequest(
            product_id=27,
            stop_loss_order=BracketStopLossOrder(
                order_type=private_orders.OrderType.MARKET_ORDER,
                stop_price=Decimal("110"),
            ),
            bracket_stop_trigger_method=private_orders.StopTriggerMethod.MARK_PRICE,
        ),
    )
    edit = subject.parse_edit_bracket_response(
        fixture_bytes("brackets", EDIT_BRACKET_FIXTURE),
        status_code=200,
        request=EditBracketRequest(id=7001, product_id=27, bracket_stop_loss_price=Decimal("111")),
    )
    assert all(type(value) is private_orders.Order for value in (place, modify, cancel))
    assert type(leverage) is private_orders.Leverage
    assert type(margin_mode) is private_orders.MarginModeResult
    assert type(position) is private_positions.MarginedPosition
    assert type(cancel_all) is type(create) is type(edit) is private_orders.MutationAcknowledgement
    assert cancel_all.success is create.success is edit.success is True


@pytest.mark.parametrize(
    ("code", "expected"),
    (
        ("SignatureExpired", SignatureExpiredError),
        ("UnauthorizedApiAccess", UnauthorizedApiAccessError),
        ("Forbidden", AuthError),
    ),
)
@pytest.mark.parametrize("status", (200, 400, 401, 403))
def test_exact_top_level_auth_shapes_map_only_on_admissible_statuses(
    code: str, expected: type[Exception], status: int
) -> None:
    body = ('{"error":"' + code + '","message":"discarded"}').encode()
    with pytest.raises(expected) as captured:
        subject.parse_place_order_response(
            body, status_code=status, request=_place(), external_user_id="12345"
        )
    assert captured.value.context == {"operation": "place_order", "http_status": status}


@pytest.mark.parametrize(
    "code", ("invalid_api_key", "ip_not_whitelisted_for_api_key", "Signature Mismatch")
)
def test_exact_nested_auth_shapes_map_without_normalization(code: str) -> None:
    body = ('{"success":false,"error":{"code":"' + code + '"}}').encode()
    with pytest.raises(AuthError):
        subject.parse_set_leverage_response(
            body,
            status_code=401,
            request=SetLeverageRequest(product_id=27, leverage=Decimal("5")),
        )
    for changed in (code.swapcase(), "InvalidApiKey", code + " "):
        changed_body = ('{"success":false,"error":{"code":"' + changed + '"}}').encode()
        with pytest.raises(AmbiguousExchangeResult):
            subject.parse_set_leverage_response(
                changed_body,
                status_code=401,
                request=SetLeverageRequest(product_id=27, leverage=Decimal("5")),
            )


_COMMON_REJECTIONS = tuple(
    "insufficient_margin,close_position_insufficient_margin,insufficient_commission,"
    "order_size_exceed_available,order_size_not_available_in_orderbook,"
    "close_order_position_unavailable,risk_limits_breached,invalid_contract,"
    "immediate_liquidation,out_of_bankruptcy,self_matching_disrupted_post_only,"
    "overlapping_buy_sell_orders,immediate_execution_post_only,naked_short_restricted,"
    "portfolio_risk_limits_breached,liquidation_risk_limits_breached,"
    "greeks_limits_breached,no_position_for_reduce_only,no_position_left_for_reduce_only,"
    "max_leverage_exceeded,min_leverage_exceeded,leverage_limit_exceeded,order_already_filled,"
    "less_than_order_min_size,order_max_size_exceeded,reduce_only_orders_allowed_sell_side,"
    "reduce_only_orders_allowed,trading_blocked,kyc_pending,trading_credits_breached,"
    "derivatives_trading_blocked,spot_trading_blocked,spot_blocked_country,"
    "account_risk_limits_breached,position_under_liquidation,"
    "position_bracket_exist_or_invalid_data,no_open_position,stale_order,offset_by_reduce_only,"
    "unsupported,no_orders,no_liquidity_for_market_order,family_position_limit_exceeded,"
    "trading_not_allowed_on_current_margin_mode,max_orders_count_exceeded".split(",")
)


def test_common_rejection_inventory_is_exactly_the_reviewed_set() -> None:
    assert subject._COMMON_REJECTIONS == frozenset(_COMMON_REJECTIONS)


@pytest.mark.parametrize("code", _COMMON_REJECTIONS)
def test_complete_common_order_rejection_inventory(code: str) -> None:
    body = ('{"success":false,"error":{"code":"' + code + '"}}').encode()
    for parser, request in (
        (subject.parse_place_order_response, _place()),
        (subject.parse_modify_order_response, _modify()),
    ):
        with pytest.raises(OrderRejected) as captured:
            parser(body, status_code=400, request=request, external_user_id="12345")
        assert captured.value.context["exchange_code"] == code


@pytest.mark.parametrize(
    "code",
    (
        "limit_order_not_allowed_for_binary_options",
        "stop_orders_not_allowed_for_binary_options",
        "not_a_binary_options_product",
        "invalid_index_symbol",
    ),
)
def test_place_only_rejections_do_not_leak_into_modify(code: str) -> None:
    body = ('{"success":false,"error":{"code":"' + code + '"}}').encode()
    with pytest.raises(OrderRejected):
        subject.parse_place_order_response(
            body, status_code=400, request=_place(), external_user_id="12345"
        )
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_modify_order_response(
            body, status_code=400, request=_modify(), external_user_id="12345"
        )


def test_modify_only_and_common_rejections_do_not_leak_to_other_routes() -> None:
    modify_only = b'{"success":false,"error":{"code":"open_order_not_found"}}'
    common = b'{"success":false,"error":{"code":"insufficient_margin"}}'
    with pytest.raises(OrderRejected):
        subject.parse_modify_order_response(
            modify_only, status_code=400, request=_modify(), external_user_id="12345"
        )
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_place_order_response(
            modify_only, status_code=400, request=_place(), external_user_id="12345"
        )
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_cancel_order_response(
            common,
            status_code=400,
            request=CancelOrderRequest(id=7001, product_id=27),
            external_user_id="12345",
        )


@pytest.mark.parametrize("status", (100, 199, 201, 202, 204, 300, 399, 429, 500, 599))
def test_status_precedence_keeps_every_non_200_or_400_outcome_ambiguous(status: int) -> None:
    bodies = (
        fixture_bytes("orders", ORDER_FIXTURE),
        b'{"error":"SignatureExpired","message":"discarded"}',
        b'{"success":false,"error":{"code":"insufficient_margin"}}',
    )
    for body in bodies:
        with pytest.raises(AmbiguousExchangeResult):
            subject.parse_place_order_response(
                body, status_code=status, request=_place(), external_user_id="12345"
            )


@pytest.mark.parametrize(
    "body",
    (
        b"",
        b"{",
        b"[]",
        b'{"success":true,"success":true,"result":{}}',
        b'{"success":true}',
        b'{"success":"true","result":{}}',
        b'{"success":true,"result":{},"extra":1}',
        b'{"success":false,"error":{"code":"unknown"}}',
    ),
)
def test_malformed_unknown_and_duplicate_order_results_are_ambiguous(body: bytes) -> None:
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_place_order_response(
            body, status_code=200, request=_place(), external_user_id="12345"
        )


def test_selector_conflicts_are_ambiguous_after_possible_send() -> None:
    body = fixture_bytes("orders", ORDER_FIXTURE)
    conflicts = (
        PlaceOrderRequest(
            product_id=28,
            size=2,
            side=private_orders.OrderSide.BUY,
            order_type=private_orders.OrderType.LIMIT_ORDER,
            limit_price=Decimal("123.45"),
            client_order_id="HMT_FIXTURE_1",
        ),
        PlaceOrderRequest(
            product_id=27,
            size=3,
            side=private_orders.OrderSide.BUY,
            order_type=private_orders.OrderType.LIMIT_ORDER,
            limit_price=Decimal("123.45"),
            client_order_id="HMT_FIXTURE_1",
        ),
        PlaceOrderRequest(
            product_id=27,
            size=2,
            side=private_orders.OrderSide.SELL,
            order_type=private_orders.OrderType.LIMIT_ORDER,
            limit_price=Decimal("123.45"),
            client_order_id="HMT_FIXTURE_1",
        ),
    )
    for request in conflicts:
        with pytest.raises(AmbiguousExchangeResult):
            subject.parse_place_order_response(
                body, status_code=200, request=request, external_user_id="12345"
            )
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_place_order_response(
            body, status_code=200, request=_place(), external_user_id="54321"
        )


def test_every_representable_mutation_selector_is_checked() -> None:
    order_body = fixture_bytes("orders", ORDER_FIXTURE)
    order_conflicts = (
        (
            subject.parse_cancel_order_response,
            CancelOrderRequest(id=7002, product_id=27),
        ),
        (
            subject.parse_modify_order_response,
            ModifyOrderRequest(id=7001, product_id=27, size=2, stop_price=Decimal("110")),
        ),
        (
            subject.parse_modify_order_response,
            ModifyOrderRequest(id=7001, product_id=27, size=2, trail_amount=Decimal("1")),
        ),
    )
    for parser, request in order_conflicts:
        with pytest.raises(AmbiguousExchangeResult):
            parser(order_body, status_code=200, request=request, external_user_id="12345")
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_set_leverage_response(
            fixture_bytes("leverage", LEVERAGE_FIXTURE),
            status_code=200,
            request=SetLeverageRequest(product_id=27, leverage=Decimal("6")),
        )
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_set_margin_mode_response(
            fixture_bytes("margin_mode", MARGIN_MODE_FIXTURE),
            status_code=200,
            request=SetMarginModeRequest(
                margin_mode=RequestMarginMode.ISOLATED, subaccount_user_id="12345"
            ),
        )
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_change_position_margin_response(
            fixture_bytes("position_margin", POSITION_FIXTURE),
            status_code=200,
            request=ChangePositionMarginRequest(product_id=28, delta_margin=Decimal("10")),
            external_user_id="12345",
        )


def test_remaining_order_and_account_selector_conflicts_are_ambiguous() -> None:
    order_body = fixture_bytes("orders", ORDER_FIXTURE)
    place_conflicts = (
        PlaceOrderRequest(
            product_id=27,
            size=2,
            side=private_orders.OrderSide.BUY,
            order_type=private_orders.OrderType.LIMIT_ORDER,
            limit_price=Decimal("124"),
            reduce_only=False,
            client_order_id="HMT_FIXTURE_1",
        ),
        PlaceOrderRequest(
            product_id=27,
            size=2,
            side=private_orders.OrderSide.BUY,
            order_type=private_orders.OrderType.LIMIT_ORDER,
            limit_price=Decimal("123.45"),
            reduce_only=True,
            client_order_id="HMT_FIXTURE_1",
        ),
        PlaceOrderRequest(
            product_id=27,
            size=2,
            side=private_orders.OrderSide.BUY,
            order_type=private_orders.OrderType.LIMIT_ORDER,
            limit_price=Decimal("123.45"),
            reduce_only=False,
            client_order_id="HMT_DIFFERENT",
        ),
    )
    for request in place_conflicts:
        with pytest.raises(AmbiguousExchangeResult):
            subject.parse_place_order_response(
                order_body, status_code=200, request=request, external_user_id="12345"
            )

    modify_conflicts = (
        ModifyOrderRequest(id=7001, product_id=28, size=2),
        ModifyOrderRequest(id=7001, product_id=27, size=3),
        ModifyOrderRequest(id=7001, product_id=27, size=2, limit_price=Decimal("124")),
        ModifyOrderRequest(id=7001, product_id=27, size=2, mmp=private_orders.MmpLevel.MMP2),
    )
    for request in modify_conflicts:
        with pytest.raises(AmbiguousExchangeResult):
            subject.parse_modify_order_response(
                order_body, status_code=200, request=request, external_user_id="12345"
            )

    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_cancel_order_response(
            order_body,
            status_code=200,
            request=CancelOrderRequest(id=7001, product_id=28),
            external_user_id="12345",
        )
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_set_margin_mode_response(
            fixture_bytes("margin_mode", MARGIN_MODE_FIXTURE),
            status_code=200,
            request=SetMarginModeRequest(
                margin_mode=RequestMarginMode.PORTFOLIO, subaccount_user_id="54321"
            ),
        )
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_change_position_margin_response(
            fixture_bytes("position_margin", POSITION_FIXTURE),
            status_code=200,
            request=ChangePositionMarginRequest(product_id=27, delta_margin=Decimal("10")),
            external_user_id="54321",
        )


def test_place_client_id_absence_requires_missing_or_null_response_presence() -> None:
    request = PlaceOrderRequest(
        product_id=27,
        size=2,
        side=private_orders.OrderSide.BUY,
        order_type=private_orders.OrderType.LIMIT_ORDER,
        limit_price=Decimal("123.45"),
        reduce_only=False,
    )
    envelope = json.loads(fixture_bytes("orders", ORDER_FIXTURE))
    result = envelope["result"]
    assert type(result) is dict
    result.pop("client_order_id")
    missing = json.dumps(envelope, separators=(",", ":")).encode()
    assert (
        type(
            subject.parse_place_order_response(
                missing, status_code=200, request=request, external_user_id="12345"
            )
        )
        is private_orders.Order
    )
    result["client_order_id"] = None
    null = json.dumps(envelope, separators=(",", ":")).encode()
    assert (
        type(
            subject.parse_place_order_response(
                null, status_code=200, request=request, external_user_id="12345"
            )
        )
        is private_orders.Order
    )
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_place_order_response(
            fixture_bytes("orders", ORDER_FIXTURE),
            status_code=200,
            request=request,
            external_user_id="12345",
        )


def test_every_mutation_route_enforces_its_exact_response_ceiling() -> None:
    cases = (
        (
            1_048_576,
            lambda body: subject.parse_place_order_response(
                body, status_code=200, request=_place(), external_user_id="12345"
            ),
        ),
        (
            1_048_576,
            lambda body: subject.parse_modify_order_response(
                body, status_code=200, request=_modify(), external_user_id="12345"
            ),
        ),
        (
            1_048_576,
            lambda body: subject.parse_cancel_order_response(
                body,
                status_code=200,
                request=CancelOrderRequest(id=7001, product_id=27),
                external_user_id="12345",
            ),
        ),
        (
            262_144,
            lambda body: subject.parse_cancel_all_response(
                body,
                status_code=200,
                request=CancelAllRequest(
                    product_id=27,
                    cancel_limit_orders=True,
                    cancel_stop_orders=False,
                    cancel_reduce_only_orders=False,
                ),
            ),
        ),
        (
            262_144,
            lambda body: subject.parse_set_leverage_response(
                body,
                status_code=200,
                request=SetLeverageRequest(product_id=27, leverage=Decimal("5")),
            ),
        ),
        (
            262_144,
            lambda body: subject.parse_set_margin_mode_response(
                body,
                status_code=200,
                request=SetMarginModeRequest(
                    margin_mode=RequestMarginMode.PORTFOLIO,
                    subaccount_user_id="12345",
                ),
            ),
        ),
        (
            1_048_576,
            lambda body: subject.parse_change_position_margin_response(
                body,
                status_code=200,
                request=ChangePositionMarginRequest(product_id=27, delta_margin=Decimal("10")),
                external_user_id="12345",
            ),
        ),
        (
            262_144,
            lambda body: subject.parse_create_bracket_response(
                body,
                status_code=200,
                request=CreateBracketRequest(
                    product_id=27,
                    stop_loss_order=BracketStopLossOrder(
                        order_type=private_orders.OrderType.MARKET_ORDER,
                        stop_price=Decimal("110"),
                    ),
                    bracket_stop_trigger_method=private_orders.StopTriggerMethod.MARK_PRICE,
                ),
            ),
        ),
        (
            262_144,
            lambda body: subject.parse_edit_bracket_response(
                body,
                status_code=200,
                request=EditBracketRequest(
                    id=7001,
                    product_id=27,
                    bracket_stop_loss_price=Decimal("111"),
                ),
            ),
        ),
    )
    for ceiling, invoke in cases:
        with pytest.raises(AmbiguousExchangeResult):
            invoke(b"0" * (ceiling + 1))


@pytest.mark.parametrize(
    "body",
    (
        b'{"success":true,"result":{}}',
        b'{"success":true,"extra":1}',
        b'{"success":false}',
        b'{"success":null}',
        b'{"success":"true"}',
    ),
)
def test_acknowledgements_never_infer_cancelled_or_protected_state(body: bytes) -> None:
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_cancel_all_response(
            body,
            status_code=200,
            request=CancelAllRequest(
                product_id=27,
                cancel_limit_orders=True,
                cancel_stop_orders=False,
                cancel_reduce_only_orders=False,
            ),
        )


def test_bounds_exact_input_types_and_safe_traceback_retention() -> None:
    canary = "PRIVATE_MUTATION_SECRET_CANARY"
    body = ('{"error":"SignatureExpired","message":"' + canary + '","extra":1}').encode()
    for invalid_body in (body, bytes(1_048_577), bytearray(b"{}")):
        with pytest.raises(AmbiguousExchangeResult) as captured:
            subject.parse_place_order_response(
                invalid_body,  # type: ignore[arg-type]
                status_code=200,
                request=_place(),
                external_user_id="12345",
            )
        error = captured.value
        assert canary not in repr((error.args, error.context, str(error), repr(error)))
        traceback = error.__traceback__
        while traceback is not None:
            if Path(traceback.tb_frame.f_code.co_filename).name == "private_mutation_parsers.py":
                assert canary not in repr(traceback.tb_frame.f_locals)
            traceback = traceback.tb_next
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_place_order_response(
            b"{}",
            status_code=True,
            request=_place(),
            external_user_id="12345",  # type: ignore[arg-type]
        )


def test_mapped_auth_discards_message_canary_from_every_owned_surface() -> None:
    canary = "PRIVATE_MAPPED_MESSAGE_CANARY"
    body = ('{"error":"SignatureExpired","message":"' + canary + '"}').encode()
    with pytest.raises(SignatureExpiredError) as captured:
        subject.parse_place_order_response(
            body, status_code=401, request=_place(), external_user_id="12345"
        )
    error = captured.value
    assert canary not in repr((error.args, error.context, str(error), repr(error)))
    traceback = error.__traceback__
    while traceback is not None:
        if Path(traceback.tb_frame.f_code.co_filename).name == "private_mutation_parsers.py":
            assert canary not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


def test_mutation_module_imports_shared_row_grammar_and_defines_no_duplicate_parser() -> None:
    source_path = Path(subject.__file__)
    tree = ast.parse(source_path.read_text())
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "hmt_crypto.exchange.delta.private_payload_parsers"
        for alias in node.names
    }
    assert imports == {
        "_parse_order_payload",
        "_parse_margined_position_payload",
        "_parse_leverage_payload",
        "_parse_margin_mode_payload",
    }
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not any(name.endswith("_payload") for name in functions)


def test_place_response_rejects_coordinated_route_profile_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = get_private_route_spec

    def drifted(route_id: PrivateRouteId) -> object:
        spec = original(route_id)
        if route_id is PrivateRouteId.PLACE_ORDER:
            return replace(
                spec,
                method="PUT",
                path_template="/v2/orders/drifted",
                body_fields=("product_id", "size"),
                max_response_bytes=2_000_000,
            )
        return spec

    profile_owner = getattr(subject, "_mutation_profile", subject)
    monkeypatch.setattr(profile_owner, "get_private_route_spec", drifted)
    with pytest.raises(AmbiguousExchangeResult):
        subject.parse_place_order_response(
            fixture_bytes("orders", ORDER_FIXTURE),
            status_code=200,
            request=_place(),
            external_user_id="12345",
        )
