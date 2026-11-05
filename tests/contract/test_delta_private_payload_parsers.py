"""Contract tests for the shared, authority-free Delta private row grammar."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from delta_private_parser_test_support import (
    LEVERAGE_FIXTURE,
    MARGIN_MODE_FIXTURE,
    ORDER_FIXTURE,
    POSITION_FIXTURE,
    fixture_result,
)

from hmt_crypto.domain import private_orders, private_positions
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta import private_payload_parsers as subject
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId


def _order(**changes: object) -> dict[str, object]:
    value = fixture_result("orders", ORDER_FIXTURE)
    value.update(changes)
    return value


def _position(**changes: object) -> dict[str, object]:
    value = fixture_result("position_margin", POSITION_FIXTURE)
    value.update(changes)
    return value


def _parse_order(value: object, **changes: object) -> private_orders.Order:
    arguments = {
        "endpoint_id": PrivateRouteId.PLACE_ORDER,
        "expected_user_id": "12345",
        "allowed_product_ids": (27,),
    }
    arguments.update(changes)
    return subject._parse_order_payload(value, **arguments)


def _parse_position(value: object, **changes: object) -> private_positions.MarginedPosition:
    arguments = {
        "endpoint_id": PrivateRouteId.CHANGE_POSITION_MARGIN,
        "expected_user_id": "12345",
        "allowed_product_ids": (27,),
    }
    arguments.update(changes)
    return subject._parse_margined_position_payload(value, **arguments)


def test_order_fixture_preserves_presence_encodings_and_identity_bridge() -> None:
    value = _parse_order(_order())
    assert value.user_id == "12345"
    assert value.product_id == 27
    assert value.side.known is private_orders.OrderSide.BUY
    assert value.reduce_only.value is False
    assert value.reduce_only.encoding is private_orders.ReduceOnlyEncoding.JSON_BOOLEAN
    assert value.bracket_order.kind == "VALUE"
    assert value.bracket_order.value is False
    assert value.bracket_stop_loss_limit_price.kind == "NULL"
    assert value.bracket_trail_amount.kind == "NULL"
    assert value.created_at.microsecond == 0


def test_order_missing_null_value_and_unknown_enum_are_distinct() -> None:
    missing = _order()
    missing.pop("mmp")
    assert _parse_order(missing).mmp.kind == "MISSING"
    assert _parse_order(_order(mmp=None)).mmp.kind == "NULL"
    unknown = _parse_order(_order(mmp="mmp_future", state="future_state"))
    assert unknown.mmp.value.known is None
    assert unknown.state.known is None
    string_boolean = _parse_order(_order(reduce_only="true"))
    assert string_boolean.reduce_only.value is True
    assert string_boolean.reduce_only.encoding is private_orders.ReduceOnlyEncoding.STRING_BOOLEAN


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("id", "7001"),
        ("user_id", "12345"),
        ("size", True),
        ("unfilled_size", -1),
        ("side", 1),
        ("order_type", None),
        ("limit_price", 123),
        ("bracket_order", 0),
        ("reduce_only", "TRUE"),
        ("created_at", "2026-11-05T12:00:00Z"),
        ("updated_at", "1793880001000000.0"),
        ("product_id", 27.0),
        ("product_symbol", ""),
    ),
)
def test_order_rejects_wrong_wire_types(field: str, invalid: object) -> None:
    with pytest.raises(ExchangeProtocolError):
        _parse_order(_order(**{field: invalid}))


@pytest.mark.parametrize(
    "field",
    (
        "id",
        "user_id",
        "size",
        "unfilled_size",
        "side",
        "order_type",
        "updated_at",
        "reduce_only",
        "state",
        "created_at",
        "product_id",
        "product_symbol",
    ),
)
def test_order_nonnullable_core_rejects_explicit_null(field: str) -> None:
    with pytest.raises(ExchangeProtocolError):
        _parse_order(_order(**{field: None}))


def test_order_nullable_and_presence_fields_preserve_explicit_null() -> None:
    nullable = (
        "limit_price",
        "stop_order_type",
        "stop_price",
        "average_fill_price",
        "trail_amount",
        "cancellation_reason",
    )
    parsed = _parse_order(_order(**dict.fromkeys(nullable)))
    assert all(object.__getattribute__(parsed, field) is None for field in nullable)
    for field in (
        "bracket_order",
        "bracket_stop_loss_price",
        "bracket_stop_loss_limit_price",
        "bracket_take_profit_price",
        "bracket_take_profit_limit_price",
        "bracket_trail_amount",
        "mmp",
        "stop_trigger_method",
        "time_in_force",
        "paid_commission",
        "commission",
        "client_order_id",
    ):
        value = _parse_order(_order(**{field: None}))
        assert object.__getattribute__(value, field).kind == "NULL"


def test_order_rejects_missing_required_extra_and_model_invariants() -> None:
    for field in (
        "id",
        "user_id",
        "size",
        "unfilled_size",
        "side",
        "order_type",
        "limit_price",
        "stop_order_type",
        "stop_price",
        "average_fill_price",
        "trail_amount",
        "cancellation_reason",
        "state",
        "reduce_only",
        "created_at",
        "updated_at",
        "product_id",
        "product_symbol",
    ):
        value = _order()
        value.pop(field)
        with pytest.raises(ExchangeProtocolError):
            _parse_order(value)
    for value in (
        _order(extra="discard-me"),
        _order(unfilled_size=3),
        _order(updated_at="1793879999000000"),
    ):
        with pytest.raises(ExchangeProtocolError):
            _parse_order(value)


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("bracket_order", "false"),
        ("bracket_stop_loss_price", 110),
        ("bracket_stop_loss_limit_price", False),
        ("bracket_take_profit_price", 140),
        ("bracket_take_profit_limit_price", []),
        ("bracket_trail_amount", {}),
        ("mmp", 1),
        ("stop_trigger_method", True),
        ("time_in_force", 0),
        ("paid_commission", -1),
        ("commission", 1),
        ("client_order_id", 123),
    ),
)
def test_order_presence_fields_reject_wrong_value_tokens(field: str, invalid: object) -> None:
    with pytest.raises(ExchangeProtocolError):
        _parse_order(_order(**{field: invalid}))


@pytest.mark.parametrize(
    "changes",
    (
        {"expected_user_id": "54321"},
        {"allowed_product_ids": (28,)},
        {"allowed_product_ids": ()},
        {"allowed_product_ids": (27, 27)},
        {"allowed_product_ids": tuple(range(1, 12))},
        {"endpoint_id": PrivateRouteId.GET_LEVERAGE},
    ),
)
def test_order_rejects_selector_and_schema_mismatch(changes: dict[str, object]) -> None:
    with pytest.raises(ExchangeProtocolError):
        _parse_order(_order(), **changes)
    assert _parse_order(_order(), allowed_product_ids=None).product_id == 27


def test_rich_position_is_validated_then_product_extras_are_discarded() -> None:
    value = _parse_position(_position())
    assert value.user_id == "12345"
    assert value.size == -2
    assert value.product.id == value.product_id == 27
    assert value.product.symbol == value.product_symbol == "BTCUSD"
    assert value.margin_mode.known is private_positions.MarginMode.ISOLATED
    assert value.potentially_delayed is True
    dumped = value.model_dump()
    assert dumped["product"] == {"id": 27, "symbol": "BTCUSD"}
    assert "contract_type" not in repr(value)
    assert "candidate" not in repr(value)


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("user_id", "12345"),
        ("size", "-2"),
        ("entry_price", 123),
        ("margin", "-1"),
        ("liquidation_price", False),
        ("adl_level", 2.0),
        ("product_id", True),
        ("product_symbol", 27),
        ("margin_mode", None),
        ("auto_topup", 0),
        ("created_at", "2026-11-05 12:00:00"),
        ("updated_at", "1793880001000000"),
    ),
)
def test_position_rejects_wrong_wire_types_and_ranges(field: str, invalid: object) -> None:
    with pytest.raises(ExchangeProtocolError):
        _parse_position(_position(**{field: invalid}))


def test_position_rejects_top_level_extras_and_nested_identity_conflicts() -> None:
    bad_product_id = deepcopy(_position())
    assert type(bad_product_id["product"]) is dict
    bad_product_id["product"]["id"] = 28  # type: ignore[index]
    bad_symbol = deepcopy(_position())
    assert type(bad_symbol["product"]) is dict
    bad_symbol["product"]["symbol"] = "ETHUSD"  # type: ignore[index]
    for value in (bad_product_id, bad_symbol, _position(extra="not-allowed")):
        with pytest.raises(ExchangeProtocolError):
            _parse_position(value)


def test_position_requires_every_documented_top_level_field() -> None:
    fields = (
        "user_id",
        "size",
        "entry_price",
        "margin",
        "liquidation_price",
        "bankruptcy_price",
        "adl_level",
        "product_id",
        "product_symbol",
        "product",
        "commission",
        "realized_pnl",
        "realized_funding",
        "realized_cashflow",
        "realized_holding_cost",
        "unrealized_pnl",
        "unrealized_cashflow",
        "mark_price",
        "margin_mode",
        "auto_topup",
        "created_at",
        "updated_at",
    )
    for field in fields:
        value = _position()
        value.pop(field)
        with pytest.raises(ExchangeProtocolError):
            _parse_position(value)


def test_position_nullability_is_exactly_the_three_documented_fields() -> None:
    permitted = ("liquidation_price", "bankruptcy_price", "adl_level")
    value = _parse_position(_position(**dict.fromkeys(permitted)))
    assert all(object.__getattribute__(value, field) is None for field in permitted)
    for field in (
        "user_id",
        "size",
        "entry_price",
        "margin",
        "product_id",
        "product_symbol",
        "product",
        "commission",
        "realized_pnl",
        "realized_funding",
        "realized_cashflow",
        "realized_holding_cost",
        "unrealized_pnl",
        "unrealized_cashflow",
        "mark_price",
        "margin_mode",
        "auto_topup",
        "created_at",
        "updated_at",
    ):
        with pytest.raises(ExchangeProtocolError):
            _parse_position(_position(**{field: None}))


def test_position_rejects_invalid_full_product_tree_bounds() -> None:
    raw_float = _position()
    assert type(raw_float["product"]) is dict
    raw_float["product"]["unsafe"] = 1.25  # type: ignore[index]
    non_string_key = _position()
    assert type(non_string_key["product"]) is dict
    non_string_key["product"][1] = "bad"  # type: ignore[index]
    too_deep = _position()
    assert type(too_deep["product"]) is dict
    node: dict[str, object] = {}
    too_deep["product"]["deep"] = node  # type: ignore[index]
    for _ in range(65):
        child: dict[str, object] = {}
        node["next"] = child
        node = child
    too_many = _position()
    assert type(too_many["product"]) is dict
    too_many["product"]["many"] = [None] * 4097  # type: ignore[index]
    for value in (raw_float, non_string_key, too_deep, too_many):
        with pytest.raises(ExchangeProtocolError):
            _parse_position(value)


def test_payload_scalar_boundaries_are_explicit() -> None:
    order = _order(
        cancellation_reason="D" * 256,
        state="S" * 64,
        product_symbol="P" * 64,
    )
    parsed = _parse_order(order)
    assert len(parsed.cancellation_reason or "") == 256
    assert parsed.state.known is None
    for changes in (
        {"cancellation_reason": "D" * 257},
        {"state": "S" * 65},
        {"product_symbol": "P" * 65},
    ):
        with pytest.raises(ExchangeProtocolError):
            _parse_order(_order(**changes))

    margin = fixture_result("margin_mode", MARGIN_MODE_FIXTURE)
    unknown = subject._parse_margin_mode_payload(
        margin | {"margin_mode": "M" * 64},
        endpoint_id=PrivateRouteId.SET_MARGIN_MODE,
        expected_user_id="12345",
    )
    assert unknown.margin_mode.known is None
    with pytest.raises(ExchangeProtocolError):
        subject._parse_margin_mode_payload(
            margin | {"margin_mode": "M" * 65},
            endpoint_id=PrivateRouteId.SET_MARGIN_MODE,
            expected_user_id="12345",
        )

    oversized = _position()
    product = oversized["product"]
    assert type(product) is dict
    product["discarded"] = "X" * 1_048_577
    with pytest.raises(ExchangeProtocolError):
        _parse_position(oversized)


def test_numeric_scalar_boundaries_are_exact_without_coercion() -> None:
    maximum_id = 2**63 - 1
    maximum_decimal = "9" * 20 + "." + "9" * 18
    order = _parse_order(
        _order(
            id=maximum_id,
            user_id=maximum_id,
            size=maximum_id,
            unfilled_size=maximum_id,
            limit_price=maximum_decimal,
            average_fill_price=maximum_decimal,
            product_id=maximum_id,
        ),
        expected_user_id=str(maximum_id),
        allowed_product_ids=(maximum_id,),
    )
    assert order.id == order.size == order.product_id == maximum_id
    for invalid_decimal in ("9" * 21, "1." + "9" * 19):
        with pytest.raises(ExchangeProtocolError):
            _parse_order(_order(limit_price=invalid_decimal))
    with pytest.raises(ExchangeProtocolError):
        _parse_order(_order(id=maximum_id + 1))

    position_payload = _position(
        user_id=maximum_id,
        size=-maximum_id,
        entry_price=maximum_decimal,
        margin=maximum_decimal,
        adl_level=maximum_id,
        product_id=maximum_id,
        commission="-" + maximum_decimal,
        realized_pnl=maximum_decimal,
        mark_price=maximum_decimal,
    )
    product = position_payload["product"]
    assert type(product) is dict
    product["id"] = maximum_id
    position = _parse_position(
        position_payload,
        expected_user_id=str(maximum_id),
        allowed_product_ids=(maximum_id,),
    )
    assert position.size == -maximum_id
    assert position.product_id == maximum_id
    for invalid_decimal in ("9" * 21, "1." + "9" * 19):
        with pytest.raises(ExchangeProtocolError):
            _parse_position(_position(margin=invalid_decimal))


def test_hostile_container_and_selector_subclasses_reject_without_callbacks() -> None:
    calls: list[str] = []

    class HostileDict(dict[object, object]):
        def __getattribute__(self, name: str) -> object:
            calls.append(name)
            raise AssertionError("hostile dict callback")

    class HostileTuple(tuple[int, ...]):
        def __iter__(self) -> object:
            calls.append("iter")
            raise AssertionError("hostile tuple callback")

    with pytest.raises(ExchangeProtocolError):
        _parse_order(HostileDict())
    with pytest.raises(ExchangeProtocolError):
        _parse_order(_order(), allowed_product_ids=HostileTuple((27,)))
    assert calls == []


def test_leverage_accepts_only_canonical_string_or_positive_integer() -> None:
    payload = fixture_result("leverage", LEVERAGE_FIXTURE)
    value = subject._parse_leverage_payload(
        payload, endpoint_id=PrivateRouteId.SET_LEVERAGE, expected_product_id=27
    )
    assert value.leverage == Decimal("5")
    assert value.leverage_encoding is private_orders.LeverageEncoding.DECIMAL_STRING
    integer = subject._parse_leverage_payload(
        payload | {"leverage": 5},
        endpoint_id=PrivateRouteId.GET_LEVERAGE,
        expected_product_id=27,
    )
    assert integer.leverage_encoding is private_orders.LeverageEncoding.JSON_INTEGER
    for invalid in (0, -1, True, 5.0, Decimal("5"), "5.0", "05", "1e1"):
        with pytest.raises(ExchangeProtocolError):
            subject._parse_leverage_payload(
                payload | {"leverage": invalid},
                endpoint_id=PrivateRouteId.SET_LEVERAGE,
                expected_product_id=27,
            )


def test_leverage_requires_exact_shape_schema_and_product_identity() -> None:
    payload = fixture_result("leverage", LEVERAGE_FIXTURE)
    cases = (
        ({"leverage": "5", "order_margin": "12.5"}, PrivateRouteId.SET_LEVERAGE, 27),
        (payload | {"extra": 1}, PrivateRouteId.SET_LEVERAGE, 27),
        (payload | {"order_margin": 1}, PrivateRouteId.SET_LEVERAGE, 27),
        (payload, PrivateRouteId.PLACE_ORDER, 27),
        (payload, PrivateRouteId.SET_LEVERAGE, 28),
    )
    for value, endpoint, expected in cases:
        with pytest.raises(ExchangeProtocolError):
            subject._parse_leverage_payload(
                value, endpoint_id=endpoint, expected_product_id=expected
            )


def test_margin_mode_uses_exact_string_id_and_observes_unknowns() -> None:
    payload = fixture_result("margin_mode", MARGIN_MODE_FIXTURE)
    value = subject._parse_margin_mode_payload(
        payload,
        endpoint_id=PrivateRouteId.SET_MARGIN_MODE,
        expected_user_id="12345",
    )
    assert value.margin_mode.known is private_positions.MarginMode.PORTFOLIO
    unknown = subject._parse_margin_mode_payload(
        payload | {"margin_mode": "future_mode"},
        endpoint_id=PrivateRouteId.SET_MARGIN_MODE,
        expected_user_id="12345",
    )
    assert unknown.margin_mode.known is None
    for invalid in (
        payload | {"id": 12345},
        payload | {"id": "54321"},
        payload | {"margin_mode": 1},
        payload | {"extra": "x"},
    ):
        with pytest.raises(ExchangeProtocolError):
            subject._parse_margin_mode_payload(
                invalid,
                endpoint_id=PrivateRouteId.SET_MARGIN_MODE,
                expected_user_id="12345",
            )


def test_payload_failures_expose_only_safe_route_context() -> None:
    canary = "PRIVATE_SECRET_CANARY_DO_NOT_RETAIN"
    value = _order(cancellation_reason=canary, extra=canary)
    with pytest.raises(ExchangeProtocolError) as captured:
        _parse_order(value, expected_user_id=canary)
    error = captured.value
    assert error.context == {"operation": "place_order"}
    surfaces = (str(error), repr(error), repr(error.args), repr(error.context))
    assert all(canary not in surface for surface in surfaces)
    traceback = error.__traceback__
    while traceback is not None:
        if Path(traceback.tb_frame.f_code.co_filename).name == "private_payload_parsers.py":
            assert canary not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


def test_all_four_payload_parser_kinds_clear_canaries_from_owned_frames() -> None:
    canary = "PRIVATE_ALL_PARSER_KINDS_CANARY"
    leverage = fixture_result("leverage", LEVERAGE_FIXTURE) | {"extra": canary}
    margin = fixture_result("margin_mode", MARGIN_MODE_FIXTURE) | {"extra": canary}
    cases = (
        lambda: _parse_order(_order(extra=canary)),
        lambda: _parse_position(_position(extra=canary)),
        lambda: subject._parse_leverage_payload(
            leverage,
            endpoint_id=PrivateRouteId.SET_LEVERAGE,
            expected_product_id=27,
        ),
        lambda: subject._parse_margin_mode_payload(
            margin,
            endpoint_id=PrivateRouteId.SET_MARGIN_MODE,
            expected_user_id="12345",
        ),
    )
    for invoke in cases:
        with pytest.raises(ExchangeProtocolError) as captured:
            invoke()
        error = captured.value
        assert canary not in repr((error.args, error.context, str(error), repr(error)))
        traceback = error.__traceback__
        while traceback is not None:
            if Path(traceback.tb_frame.f_code.co_filename).name == "private_payload_parsers.py":
                assert canary not in repr(traceback.tb_frame.f_locals)
            traceback = traceback.tb_next


def test_neutral_payload_leaf_has_no_execution_or_authority_surface() -> None:
    forbidden = (
        "send",
        "sign",
        "execute",
        "transport",
        "credential",
        "dispatch",
        "catalog",
        "terminal",
        "absence",
        "flat",
    )
    names = tuple(vars(subject))
    assert not any(any(token in name.casefold() for token in forbidden) for name in names)
