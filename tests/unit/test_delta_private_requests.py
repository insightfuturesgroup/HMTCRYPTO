"""Exact exchange-neutral private query, request, and read-protocol contracts."""

from __future__ import annotations

import ast
import gc
import inspect
import os
import subprocess
import sys
import textwrap
import weakref
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import get_args, get_overloads, get_type_hints

import pytest
from pydantic import ValidationError

import hmt_crypto.exchange.private as private_exchange
import hmt_crypto.exchange.private_requests as private_requests
from hmt_crypto.domain.account import CollectionMode
from hmt_crypto.domain.private_orders import (
    DiagnosticFillPaginationResult,
    DiagnosticOrderPaginationResult,
    DiagnosticWalletTransactionPaginationResult,
    MmpLevel,
    ObservedLatestPositionLookupResult,
    ObservedLeverage,
    ObservedOrderLookupResult,
    OrderSide,
    OrderType,
    StopOrderType,
    StopTriggerMethod,
    StrictFillPaginationResult,
    StrictOrderPaginationResult,
    StrictWalletTransactionPaginationResult,
    TimeInForce,
)
from hmt_crypto.domain.private_positions import MarginMode, ObservedMarginedPositions
from hmt_crypto.domain.private_wallet import ObservedBalanceSnapshot, WalletTransactionType
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
    PrivateContractTypeFilter,
    WalletTransactionQuery,
)

pytestmark = pytest.mark.unit


def _enum_manifest(enum_type: type[StrEnum]) -> tuple[tuple[str, str], ...]:
    return tuple((member.name, member.value) for member in enum_type)


def _fields(model: type[object]) -> tuple[tuple[str, object, object], ...]:
    fields = model.model_fields  # type: ignore[attr-defined]
    return tuple(
        (name, field.annotation, ... if field.is_required() else field.default)
        for name, field in fields.items()
    )


def _annotation_snapshot(model: type[object]) -> tuple[str, ...]:
    return tuple(model.__annotations__.values())  # type: ignore[attr-defined]


def _forge_member(enum_type: type[StrEnum], member: StrEnum) -> StrEnum:
    forged = str.__new__(enum_type, member.value)
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", member.value)
    return forged


def test_private_filter_enum_manifests_are_exact_and_fail_closed() -> None:
    expected = {
        PrivateContractTypeFilter: (
            ("FUTURES", "futures"),
            ("PERPETUAL_FUTURES", "perpetual_futures"),
            ("CALL_OPTIONS", "call_options"),
            ("PUT_OPTIONS", "put_options"),
        ),
        MarginedPositionContractTypeFilter: (
            ("PERPETUAL_FUTURES", "perpetual_futures"),
            ("CALL_OPTIONS", "call_options"),
            ("PUT_OPTIONS", "put_options"),
        ),
        OpenOrderStateFilter: (("OPEN", "open"), ("PENDING", "pending")),
        OrderTypeFilter: (
            ("MARKET", "market"),
            ("LIMIT", "limit"),
            ("STOP_MARKET", "stop_market"),
            ("STOP_LIMIT", "stop_limit"),
            ("ALL_STOP", "all_stop"),
        ),
    }
    for enum_type, manifest in expected.items():
        assert _enum_manifest(enum_type) == manifest
        canonical = tuple(enum_type)
        query = OpenOrderQuery(
            states=(OpenOrderStateFilter.OPEN,),
            contract_types=(PrivateContractTypeFilter.FUTURES,),
            order_types=(OrderTypeFilter.LIMIT,),
        )
        assert query.model_dump(mode="json") == {
            "product_ids": [],
            "states": ["open"],
            "contract_types": ["futures"],
            "order_types": ["limit"],
            "start_time": None,
            "end_time": None,
            "after": None,
            "before": None,
            "page_size": 10,
        }
        for member in canonical:
            assert type(member) is enum_type

    with pytest.raises(ValidationError):
        OpenOrderQuery(states=("open",))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        OpenOrderQuery(states=(OpenOrderStateFilter.OPEN, PrivateContractTypeFilter.FUTURES))  # type: ignore[arg-type]


def test_private_query_field_snapshots_and_defaults_are_exact() -> None:
    expected_names = {
        WalletTransactionQuery: (
            "asset_id",
            "transaction_types",
            "start_time",
            "end_time",
            "after",
            "before",
            "page_size",
        ),
        MarginedPositionQuery: ("product_ids", "contract_types"),
        LatestPositionQuery: ("product_id",),
        OpenOrderQuery: (
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
        OrderHistoryQuery: (
            "product_ids",
            "contract_types",
            "order_types",
            "start_time",
            "end_time",
            "after",
            "before",
            "page_size",
        ),
        FillQuery: (
            "product_ids",
            "contract_types",
            "start_time",
            "end_time",
            "after",
            "before",
            "page_size",
        ),
        OrderByIdQuery: ("order_id",),
        OrderByClientIdQuery: ("client_order_id",),
        LeverageQuery: ("product_id",),
    }
    for model, names in expected_names.items():
        assert tuple(model.model_fields) == names
        assert all(type(name) is str for name in model.model_fields)

    assert WalletTransactionQuery().model_dump() == {
        "asset_id": None,
        "transaction_types": (),
        "start_time": None,
        "end_time": None,
        "after": None,
        "before": None,
        "page_size": 10,
    }
    assert MarginedPositionQuery().model_dump() == {
        "product_ids": (),
        "contract_types": (),
    }
    assert OpenOrderQuery().page_size == 10
    assert OrderHistoryQuery().page_size == 10
    assert FillQuery().page_size == 10
    for model in (LatestPositionQuery, OrderByIdQuery, OrderByClientIdQuery, LeverageQuery):
        assert _fields(model)[0][2] is ...
        with pytest.raises(ValidationError):
            model()  # type: ignore[call-arg]


def test_private_query_annotation_snapshots_are_exact() -> None:
    expected = {
        WalletTransactionQuery: (
            "PrivateId | None",
            "_WalletTransactionTypes",
            "ExactAwareUtcDatetime | None",
            "ExactAwareUtcDatetime | None",
            "OpaqueCursor | None",
            "OpaqueCursor | None",
            "PrivatePageSize",
        ),
        MarginedPositionQuery: ("_ProductIdTuple", "_MarginedContractTypes"),
        LatestPositionQuery: ("PrivateId",),
        OpenOrderQuery: (
            "_ProductIdTuple",
            "_OpenOrderStates",
            "_PrivateContractTypes",
            "_OrderTypes",
            "ExactAwareUtcDatetime | None",
            "ExactAwareUtcDatetime | None",
            "OpaqueCursor | None",
            "OpaqueCursor | None",
            "PrivatePageSize",
        ),
        OrderHistoryQuery: (
            "_ProductIdTuple",
            "_PrivateContractTypes",
            "_OrderTypes",
            "ExactAwareUtcDatetime | None",
            "ExactAwareUtcDatetime | None",
            "OpaqueCursor | None",
            "OpaqueCursor | None",
            "PrivatePageSize",
        ),
        FillQuery: (
            "_ProductIdTuple",
            "_PrivateContractTypes",
            "ExactAwareUtcDatetime | None",
            "ExactAwareUtcDatetime | None",
            "OpaqueCursor | None",
            "OpaqueCursor | None",
            "PrivatePageSize",
        ),
        OrderByIdQuery: ("PrivateId",),
        OrderByClientIdQuery: ("ObservedDeltaClientOrderId",),
        LeverageQuery: ("PrivateId",),
    }
    for model, expected_annotations in expected.items():
        assert _annotation_snapshot(model) == expected_annotations


@pytest.mark.parametrize(
    "model", (MarginedPositionQuery, OpenOrderQuery, OrderHistoryQuery, FillQuery)
)
def test_private_product_filter_tuples_are_exact_bounded_unique_ordered(
    model: type[object],
) -> None:
    original = (9, 2, 7)
    query = model(product_ids=original)  # type: ignore[call-arg]
    assert query.product_ids == original
    assert query.product_ids is not original
    assert model(product_ids=()).product_ids == ()  # type: ignore[call-arg]
    assert len(model(product_ids=tuple(range(1, 11))).product_ids) == 10  # type: ignore[call-arg]
    for invalid in (
        tuple(range(1, 12)),
        (1, 1),
        [1],
        {1},
        {"x": 1},
        (item for item in (1,)),
    ):
        with pytest.raises(ValidationError):
            model(product_ids=invalid)  # type: ignore[call-arg]

    class TupleSubclass(tuple):
        pass

    with pytest.raises(ValidationError):
        model(product_ids=TupleSubclass((1,)))  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        model(product_ids=(True,))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("model", "field", "members"),
    (
        (MarginedPositionQuery, "contract_types", tuple(MarginedPositionContractTypeFilter)),
        (OpenOrderQuery, "states", tuple(OpenOrderStateFilter)),
        (OpenOrderQuery, "contract_types", tuple(PrivateContractTypeFilter)),
        (OpenOrderQuery, "order_types", tuple(OrderTypeFilter)),
        (OrderHistoryQuery, "contract_types", tuple(PrivateContractTypeFilter)),
        (OrderHistoryQuery, "order_types", tuple(OrderTypeFilter)),
        (FillQuery, "contract_types", tuple(PrivateContractTypeFilter)),
    ),
)
def test_private_enum_filter_tuples_are_exact_unique_and_ordered(
    model: type[object], field: str, members: tuple[StrEnum, ...]
) -> None:
    supplied = members[:2]
    value = model(**{field: supplied})  # type: ignore[call-arg]
    assert getattr(value, field) == supplied
    assert getattr(value, field) is not supplied
    with pytest.raises(ValidationError):
        model(**{field: (members[0], members[0])})  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        model(**{field: (members[0].value,)})  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        model(**{field: list(supplied)})  # type: ignore[call-arg]


def test_every_filter_enum_path_rejects_raw_foreign_forged_and_corrupt_values() -> None:
    cases: tuple[tuple[Callable[[object], object], str, type[StrEnum], StrEnum], ...] = (
        (
            lambda value: MarginedPositionQuery(contract_types=value),
            "contract_types",
            MarginedPositionContractTypeFilter,
            MarginedPositionContractTypeFilter.PERPETUAL_FUTURES,
        ),  # type: ignore[arg-type]
        (
            lambda value: OpenOrderQuery(states=value),
            "states",
            OpenOrderStateFilter,
            OpenOrderStateFilter.OPEN,
        ),  # type: ignore[arg-type]
        (
            lambda value: OpenOrderQuery(contract_types=value),
            "contract_types",
            PrivateContractTypeFilter,
            PrivateContractTypeFilter.FUTURES,
        ),  # type: ignore[arg-type]
        (
            lambda value: OpenOrderQuery(order_types=value),
            "order_types",
            OrderTypeFilter,
            OrderTypeFilter.LIMIT,
        ),  # type: ignore[arg-type]
        (
            lambda value: OrderHistoryQuery(contract_types=value),
            "contract_types",
            PrivateContractTypeFilter,
            PrivateContractTypeFilter.FUTURES,
        ),  # type: ignore[arg-type]
        (
            lambda value: OrderHistoryQuery(order_types=value),
            "order_types",
            OrderTypeFilter,
            OrderTypeFilter.LIMIT,
        ),  # type: ignore[arg-type]
        (
            lambda value: FillQuery(contract_types=value),
            "contract_types",
            PrivateContractTypeFilter,
            PrivateContractTypeFilter.FUTURES,
        ),  # type: ignore[arg-type]
    )
    callbacks: list[str] = []

    class HostileText(str):
        def __eq__(self, other: object) -> bool:
            callbacks.append("eq")
            raise AssertionError("hostile enum text equality")

        def __hash__(self) -> int:
            callbacks.append("hash")
            raise AssertionError("hostile enum text hash")

        def __repr__(self) -> str:
            callbacks.append("repr")
            raise AssertionError("hostile enum text repr")

    for factory, field, enum_type, member in cases:
        accepted = factory((member,))
        assert accepted.model_dump(mode="json")[field] == [member.value]  # type: ignore[attr-defined]
        foreign_type = StrEnum("Foreign", {"VALUE": member.value})
        invalid = (
            member.value,
            foreign_type.VALUE,
            HostileText(member.value),
            _forge_member(enum_type, member),
        )
        for value in invalid:
            with pytest.raises(ValidationError):
                factory((value,))
        original_name, original_value = member._name_, member._value_
        try:
            object.__setattr__(member, "_name_", "CORRUPTED")
            object.__setattr__(member, "_value_", "fixture-secret-corrupted")
            frozen = factory((member,))
            assert frozen.model_dump(mode="json")[field] == [original_value]  # type: ignore[attr-defined]
        finally:
            object.__setattr__(member, "_name_", original_name)
            object.__setattr__(member, "_value_", original_value)
    assert callbacks == []


@pytest.mark.parametrize(
    "model", (WalletTransactionQuery, OpenOrderQuery, OrderHistoryQuery, FillQuery)
)
def test_private_query_cursor_time_and_page_windows_are_fail_closed(model: type[object]) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)
    assert model(start_time=start, end_time=end, after="A", page_size=1).page_size == 1  # type: ignore[call-arg]
    assert model(start_time=start, end_time=start, before="B", page_size=50).page_size == 50  # type: ignore[call-arg]
    for values in (
        {"after": "A", "before": "B"},
        {"start_time": end, "end_time": start},
        {"page_size": 0},
        {"page_size": 51},
        {"page_size": True},
    ):
        with pytest.raises(ValidationError):
            model(**values)  # type: ignore[call-arg]


def test_query_tuples_scalars_and_datetimes_reject_subclasses_before_callbacks() -> None:
    callbacks: list[str] = []

    class HostileTuple(tuple[object, ...]):
        def __len__(self) -> int:
            callbacks.append("len")
            raise AssertionError("hostile tuple length")

        def __iter__(self) -> Iterator[object]:
            callbacks.append("iter")
            raise AssertionError("hostile tuple iteration")

        def __getitem__(self, index: object) -> object:
            callbacks.append("getitem")
            raise AssertionError("hostile tuple indexing")

        def __repr__(self) -> str:
            callbacks.append("repr")
            raise AssertionError("hostile tuple repr")

    class HostileInt(int):
        def __eq__(self, other: object) -> bool:
            callbacks.append("eq")
            raise AssertionError("hostile int equality")

        def __hash__(self) -> int:
            callbacks.append("hash")
            raise AssertionError("hostile int hash")

    class HostileDatetime(datetime):
        def utcoffset(self) -> timedelta | None:
            callbacks.append("utcoffset")
            raise AssertionError("hostile datetime timezone")

        def __repr__(self) -> str:
            callbacks.append("repr")
            raise AssertionError("hostile datetime repr")

    invalid_calls = (
        lambda: OpenOrderQuery(product_ids=HostileTuple((1,))),
        lambda: OpenOrderQuery(product_ids=(HostileInt(1),)),
        lambda: OpenOrderQuery(page_size=HostileInt(10)),
        lambda: OpenOrderQuery(start_time=datetime(2026, 1, 1)),
        lambda: OpenOrderQuery(
            start_time=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))
        ),
        lambda: OpenOrderQuery(start_time=HostileDatetime(2026, 1, 1)),
    )
    for call in invalid_calls:
        with pytest.raises(ValidationError) as captured:
            call()
        assert all(detail.get("input") is None for detail in captured.value.errors())
    assert callbacks == []


def test_rejected_tuple_elements_are_not_retained() -> None:
    callbacks: list[str] = []

    class HostileElement:
        def __eq__(self, other: object) -> bool:
            callbacks.append("eq")
            raise AssertionError("hostile element equality")

        def __hash__(self) -> int:
            callbacks.append("hash")
            raise AssertionError("hostile element hash")

        def __repr__(self) -> str:
            callbacks.append("repr")
            raise AssertionError("hostile element repr")

    element = HostileElement()
    reference = weakref.ref(element)
    caught: ValidationError | None = None
    try:
        OpenOrderQuery(product_ids=(element,))  # type: ignore[arg-type]
    except ValidationError as error:
        caught = error
    else:
        raise AssertionError("hostile element unexpectedly accepted")
    del element, caught
    gc.collect()
    assert reference() is None
    assert callbacks == []


def test_wallet_query_keeps_one_asset_and_empty_transaction_type_gate() -> None:
    assert WalletTransactionQuery(asset_id=None).asset_id is None
    assert WalletTransactionQuery(asset_id=17).asset_id == 17
    assert WalletTransactionQuery(transaction_types=()).transaction_types == ()
    with pytest.raises(ValidationError):
        WalletTransactionQuery(transaction_types=(WalletTransactionType.FUNDING,))
    with pytest.raises(ValidationError):
        WalletTransactionQuery(asset_id=(17,))  # type: ignore[arg-type]


def test_wallet_enum_admission_precedes_the_empty_filter_gate() -> None:
    with pytest.raises(ValidationError) as raw:
        WalletTransactionQuery(transaction_types=("funding",))  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as canonical:
        WalletTransactionQuery(transaction_types=(WalletTransactionType.FUNDING,))
    assert raw.value.errors(include_url=False)[0]["type"] == "private_model_value"
    assert canonical.value.errors(include_url=False)[0]["type"] == "private_wallet_filter"


def test_read_order_lookup_accepts_broader_observed_client_id() -> None:
    assert OrderByClientIdQuery(client_order_id="desk.alpha:7").client_order_id == "desk.alpha:7"
    assert OrderByIdQuery(order_id=7).order_id == 7
    with pytest.raises(ValidationError):
        OrderByClientIdQuery(client_order_id="bad space")


def test_private_queries_own_copy_dump_and_corruption_boundaries() -> None:
    query = OpenOrderQuery(
        product_ids=(3, 1),
        states=(OpenOrderStateFilter.OPEN,),
        page_size=50,
    )
    assert query.model_copy() == query
    assert query.model_dump() == {
        "product_ids": (3, 1),
        "states": (OpenOrderStateFilter.OPEN,),
        "contract_types": (),
        "order_types": (),
        "start_time": None,
        "end_time": None,
        "after": None,
        "before": None,
        "page_size": 50,
    }
    with pytest.raises(ValidationError):
        OpenOrderQuery.model_validate(query.model_dump() | {"product_ids": [3, 1]})
    with pytest.raises(ValidationError):
        OpenOrderQuery(extra="forbidden")  # type: ignore[call-arg]


def test_private_query_surface_has_no_wire_or_runtime_authority() -> None:
    path = Path(__file__).resolve().parents[2] / "src/hmt_crypto/exchange/private_requests.py"
    tree = ast.parse(path.read_text())
    forbidden_imports = {"httpx", "socket", "asyncio", "sqlalchemy", "os", "logging"}
    imported = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node.names
    }
    assert imported.isdisjoint(forbidden_imports)
    forbidden = {"send", "sign", "acquire", "dispatch", "execute", "commit", "encode"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in forbidden
    assert Decimal("1") == Decimal("1")
    assert inspect.isclass(OrderSide) and inspect.isclass(OrderType)
    assert inspect.isclass(MmpLevel) and inspect.isclass(StopTriggerMethod)
    assert inspect.isclass(TimeInForce)
    assert get_args(WalletTransactionQuery.model_fields["asset_id"].annotation)


def _valid_place(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "product_id": 11,
        "size": 2,
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT_ORDER,
        "limit_price": Decimal("100.5"),
    }
    values.update(changes)
    return values


def test_request_only_enum_manifests_are_exact_and_fail_closed() -> None:
    assert _enum_manifest(private_requests.RequestStopOrderType) == (
        ("STOP_LOSS_ORDER", "stop_loss_order"),
        ("TAKE_PROFIT_ORDER", "take_profit_order"),
    )
    assert _enum_manifest(private_requests.RequestMarginMode) == (
        ("ISOLATED", "isolated"),
        ("PORTFOLIO", "portfolio"),
    )
    cases: tuple[tuple[Callable[[object], object], str, type[StrEnum], StrEnum, StrEnum], ...] = (
        (
            lambda value: private_requests.PlaceOrderRequest(
                **_valid_place(
                    stop_order_type=value,
                    stop_price=Decimal("90"),
                    stop_trigger_method=StopTriggerMethod.MARK_PRICE,
                )
            ),
            "stop_order_type",
            private_requests.RequestStopOrderType,
            private_requests.RequestStopOrderType.STOP_LOSS_ORDER,
            StopOrderType.LIQUIDATION_ORDER,
        ),
        (
            lambda value: private_requests.SetMarginModeRequest(
                margin_mode=value, subaccount_user_id="7"
            ),
            "margin_mode",
            private_requests.RequestMarginMode,
            private_requests.RequestMarginMode.ISOLATED,
            MarginMode.CROSS,
        ),
    )
    for factory, field, enum_type, member, response_only in cases:
        assert factory(member).model_dump(mode="json")[field] == member.value  # type: ignore[attr-defined]
        foreign_type = StrEnum("ForeignRequest", {"VALUE": member.value})
        for invalid in (
            member.value,
            response_only,
            foreign_type.VALUE,
            type("TextSubclass", (str,), {})(member.value),
            _forge_member(enum_type, member),
        ):
            with pytest.raises(ValidationError):
                factory(invalid)
        original_name, original_value = member._name_, member._value_
        try:
            object.__setattr__(member, "_name_", "CORRUPTED")
            object.__setattr__(member, "_value_", "fixture-secret-corrupted")
            assert factory(member).model_dump(mode="json")[field] == original_value  # type: ignore[attr-defined]
        finally:
            object.__setattr__(member, "_name_", original_name)
            object.__setattr__(member, "_value_", original_value)


def test_mutation_request_field_type_default_snapshots_are_exact() -> None:
    expected: dict[type[object], tuple[tuple[str, object], ...]] = {
        private_requests.PlaceOrderRequest: (
            ("product_id", ...),
            ("size", ...),
            ("side", ...),
            ("order_type", ...),
            ("limit_price", None),
            ("stop_order_type", None),
            ("stop_price", None),
            ("trail_amount", None),
            ("stop_trigger_method", None),
            ("bracket_stop_trigger_method", None),
            ("bracket_stop_loss_limit_price", None),
            ("bracket_stop_loss_price", None),
            ("bracket_trail_amount", None),
            ("bracket_take_profit_limit_price", None),
            ("bracket_take_profit_price", None),
            ("time_in_force", None),
            ("mmp", None),
            ("post_only", None),
            ("reduce_only", None),
            ("client_order_id", None),
        ),
        private_requests.ModifyOrderRequest: (
            ("id", ...),
            ("product_id", ...),
            ("size", ...),
            ("limit_price", None),
            ("mmp", None),
            ("post_only", None),
            ("stop_price", None),
            ("trail_amount", None),
        ),
        private_requests.CancelOrderRequest: (("id", ...), ("product_id", ...)),
        private_requests.CancelAllRequest: (
            ("product_id", ...),
            ("cancel_limit_orders", ...),
            ("cancel_stop_orders", ...),
            ("cancel_reduce_only_orders", ...),
        ),
        private_requests.SetLeverageRequest: (("product_id", ...), ("leverage", ...)),
        private_requests.SetMarginModeRequest: (
            ("margin_mode", ...),
            ("subaccount_user_id", ...),
        ),
        private_requests.ChangePositionMarginRequest: (
            ("product_id", ...),
            ("delta_margin", ...),
        ),
        private_requests.BracketStopLossOrder: (
            ("order_type", ...),
            ("stop_price", None),
            ("trail_amount", None),
            ("limit_price", None),
        ),
        private_requests.BracketTakeProfitOrder: (
            ("order_type", ...),
            ("stop_price", ...),
            ("limit_price", None),
        ),
        private_requests.CreateBracketRequest: (
            ("product_id", ...),
            ("stop_loss_order", None),
            ("take_profit_order", None),
            ("bracket_stop_trigger_method", ...),
        ),
        private_requests.EditBracketRequest: (
            ("id", ...),
            ("product_id", ...),
            ("bracket_stop_loss_limit_price", None),
            ("bracket_stop_loss_price", None),
            ("bracket_take_profit_limit_price", None),
            ("bracket_take_profit_price", None),
            ("bracket_trail_amount", None),
            ("bracket_stop_trigger_method", None),
        ),
    }
    for model, snapshot in expected.items():
        assert (
            tuple(
                (name, ... if field.is_required() else field.default)
                for name, field in model.model_fields.items()  # type: ignore[attr-defined]
            )
            == snapshot
        )
        assert "product_symbol" not in model.model_fields  # type: ignore[attr-defined]
        assert "cancel_orders_accepted" not in model.model_fields  # type: ignore[attr-defined]


def test_mutation_request_annotation_snapshots_are_exact() -> None:
    expected = {
        private_requests.PlaceOrderRequest: (
            "PrivateId",
            "Contracts",
            "OrderSide",
            "OrderType",
            "PositivePrivateDecimal | None",
            "RequestStopOrderType | None",
            "PositivePrivateDecimal | None",
            "PositivePrivateDecimal | None",
            "StopTriggerMethod | None",
            "StopTriggerMethod | None",
            *("PositivePrivateDecimal | None",) * 5,
            "TimeInForce | None",
            "MmpLevel | None",
            "ExactBool | None",
            "ExactBool | None",
            "OmsClientOrderId | None",
        ),
        private_requests.ModifyOrderRequest: (
            "PrivateId",
            "PrivateId",
            "Contracts",
            "PositivePrivateDecimal | None",
            "MmpLevel | None",
            "ExactBool | None",
            "PositivePrivateDecimal | None",
            "PositivePrivateDecimal | None",
        ),
        private_requests.CancelOrderRequest: ("PrivateId", "PrivateId"),
        private_requests.CancelAllRequest: (
            "PrivateId",
            "ExactBool",
            "ExactBool",
            "ExactBool",
        ),
        private_requests.SetLeverageRequest: ("PrivateId", "PositivePrivateDecimal"),
        private_requests.SetMarginModeRequest: ("RequestMarginMode", "ExternalUserText"),
        private_requests.ChangePositionMarginRequest: (
            "PrivateId",
            "NonZeroPrivateDecimal",
        ),
        private_requests.BracketStopLossOrder: (
            "OrderType",
            "PositivePrivateDecimal | None",
            "PositivePrivateDecimal | None",
            "PositivePrivateDecimal | None",
        ),
        private_requests.BracketTakeProfitOrder: (
            "OrderType",
            "PositivePrivateDecimal",
            "PositivePrivateDecimal | None",
        ),
        private_requests.CreateBracketRequest: (
            "PrivateId",
            "BracketStopLossOrder | None",
            "BracketTakeProfitOrder | None",
            "StopTriggerMethod",
        ),
        private_requests.EditBracketRequest: (
            "PrivateId",
            "PrivateId",
            *("PositivePrivateDecimal | None",) * 5,
            "StopTriggerMethod | None",
        ),
    }
    for model, expected_annotations in expected.items():
        assert _annotation_snapshot(model) == expected_annotations


def test_mutation_requests_revalidate_every_consumed_a2_enum_field() -> None:
    cases: tuple[tuple[Callable[[object], object], str, type[StrEnum], StrEnum], ...] = (
        (
            lambda value: private_requests.PlaceOrderRequest(**_valid_place(side=value)),
            "side",
            OrderSide,
            OrderSide.BUY,
        ),
        (
            lambda value: private_requests.PlaceOrderRequest(**_valid_place(order_type=value)),
            "order_type",
            OrderType,
            OrderType.LIMIT_ORDER,
        ),
        (
            lambda value: private_requests.BracketStopLossOrder(
                order_type=value, stop_price=Decimal("1")
            ),
            "order_type",
            OrderType,
            OrderType.MARKET_ORDER,
        ),
        (
            lambda value: private_requests.BracketTakeProfitOrder(
                order_type=value, stop_price=Decimal("1")
            ),
            "order_type",
            OrderType,
            OrderType.MARKET_ORDER,
        ),
        (
            lambda value: private_requests.PlaceOrderRequest(
                **_valid_place(time_in_force=TimeInForce.GTC, mmp=value)
            ),
            "mmp",
            MmpLevel,
            MmpLevel.DISABLED,
        ),
        (
            lambda value: private_requests.ModifyOrderRequest(
                id=1, product_id=2, size=3, mmp=value
            ),
            "mmp",
            MmpLevel,
            MmpLevel.DISABLED,
        ),
        (
            lambda value: private_requests.PlaceOrderRequest(
                **_valid_place(
                    stop_order_type=private_requests.RequestStopOrderType.STOP_LOSS_ORDER,
                    stop_price=Decimal("1"),
                    stop_trigger_method=value,
                )
            ),
            "stop_trigger_method",
            StopTriggerMethod,
            StopTriggerMethod.MARK_PRICE,
        ),
        (
            lambda value: private_requests.PlaceOrderRequest(
                **_valid_place(
                    bracket_take_profit_price=Decimal("2"), bracket_stop_trigger_method=value
                )
            ),
            "bracket_stop_trigger_method",
            StopTriggerMethod,
            StopTriggerMethod.MARK_PRICE,
        ),
        (
            lambda value: private_requests.CreateBracketRequest(
                product_id=1,
                stop_loss_order={"order_type": OrderType.MARKET_ORDER, "stop_price": Decimal("1")},
                bracket_stop_trigger_method=value,
            ),
            "bracket_stop_trigger_method",
            StopTriggerMethod,
            StopTriggerMethod.MARK_PRICE,
        ),
        (
            lambda value: private_requests.EditBracketRequest(
                id=1,
                product_id=2,
                bracket_take_profit_price=Decimal("2"),
                bracket_stop_trigger_method=value,
            ),
            "bracket_stop_trigger_method",
            StopTriggerMethod,
            StopTriggerMethod.MARK_PRICE,
        ),
        (
            lambda value: private_requests.PlaceOrderRequest(**_valid_place(time_in_force=value)),
            "time_in_force",
            TimeInForce,
            TimeInForce.GTC,
        ),
    )
    callbacks: list[str] = []

    class HostileText(str):
        def __eq__(self, other: object) -> bool:
            callbacks.append("eq")
            raise AssertionError("hostile A2 enum text equality")

        def __hash__(self) -> int:
            callbacks.append("hash")
            raise AssertionError("hostile A2 enum text hash")

        def __repr__(self) -> str:
            callbacks.append("repr")
            raise AssertionError("hostile A2 enum text repr")

    for factory, field, enum_type, member in cases:
        assert factory(member).model_dump(mode="json")[field] == member.value  # type: ignore[attr-defined]
        foreign_type = StrEnum("ForeignA2", {"VALUE": member.value})
        for invalid in (
            member.value,
            foreign_type.VALUE,
            private_requests.RequestMarginMode.ISOLATED,
            HostileText(member.value),
            _forge_member(enum_type, member),
        ):
            with pytest.raises(ValidationError):
                factory(invalid)
        original_name, original_value = member._name_, member._value_
        try:
            object.__setattr__(member, "_name_", "CORRUPTED")
            object.__setattr__(member, "_value_", "fixture-secret-corrupted")
            assert factory(member).model_dump(mode="json")[field] == original_value  # type: ignore[attr-defined]
        finally:
            object.__setattr__(member, "_name_", original_name)
            object.__setattr__(member, "_value_", original_value)
    assert callbacks == []


def test_place_order_conditional_matrix_is_fail_closed() -> None:
    place = private_requests.PlaceOrderRequest
    assert place(**_valid_place()).limit_price == Decimal("100.5")
    assert (
        place(**_valid_place(order_type=OrderType.MARKET_ORDER, limit_price=None)).limit_price
        is None
    )
    assert place(
        **_valid_place(
            stop_order_type=private_requests.RequestStopOrderType.STOP_LOSS_ORDER,
            stop_price=Decimal("90"),
            stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        )
    ).stop_price == Decimal("90")
    assert place(
        **_valid_place(
            stop_order_type=private_requests.RequestStopOrderType.STOP_LOSS_ORDER,
            trail_amount=Decimal("2"),
            stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        )
    ).trail_amount == Decimal("2")
    assert place(
        **_valid_place(
            bracket_stop_loss_price=Decimal("90"),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        )
    ).bracket_stop_loss_price == Decimal("90")
    assert place(
        **_valid_place(
            bracket_trail_amount=Decimal("2"),
            bracket_stop_loss_limit_price=Decimal("89"),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        )
    ).bracket_trail_amount == Decimal("2")
    assert place(
        **_valid_place(
            bracket_take_profit_price=Decimal("110"),
            bracket_take_profit_limit_price=Decimal("111"),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        )
    ).bracket_take_profit_limit_price == Decimal("111")
    assert (
        place(
            **_valid_place(
                time_in_force=TimeInForce.GTC,
                post_only=False,
                mmp=MmpLevel.DISABLED,
            )
        ).post_only
        is False
    )
    for time_in_force in (None, TimeInForce.GTC, TimeInForce.IOC):
        assert place(**_valid_place(time_in_force=time_in_force)).time_in_force is time_in_force

    invalid = (
        _valid_place(limit_price=None),
        _valid_place(order_type=OrderType.MARKET_ORDER),
        _valid_place(stop_price=Decimal("90")),
        _valid_place(
            stop_order_type=private_requests.RequestStopOrderType.STOP_LOSS_ORDER,
            stop_price=Decimal("90"),
            trail_amount=Decimal("2"),
            stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
        _valid_place(bracket_stop_loss_limit_price=Decimal("89")),
        _valid_place(bracket_take_profit_limit_price=Decimal("111")),
        _valid_place(bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE),
        _valid_place(
            stop_order_type=private_requests.RequestStopOrderType.STOP_LOSS_ORDER,
            stop_price=Decimal("90"),
            stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            bracket_take_profit_price=Decimal("110"),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
        _valid_place(post_only=False),
        _valid_place(time_in_force=TimeInForce.IOC, mmp=MmpLevel.MMP1),
        _valid_place(
            reduce_only=True,
            bracket_take_profit_price=Decimal("110"),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
        _valid_place(
            stop_order_type=private_requests.RequestStopOrderType.STOP_LOSS_ORDER,
            stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
        _valid_place(
            stop_order_type=private_requests.RequestStopOrderType.STOP_LOSS_ORDER,
            stop_price=Decimal("90"),
        ),
        _valid_place(
            bracket_stop_loss_price=Decimal("90"),
            bracket_trail_amount=Decimal("2"),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
        _valid_place(bracket_take_profit_price=Decimal("110")),
    )
    for values in invalid:
        with pytest.raises(ValidationError):
            place(**values)
    restricted_contexts = (
        _valid_place(),
        _valid_place(
            order_type=OrderType.MARKET_ORDER,
            limit_price=None,
            time_in_force=TimeInForce.GTC,
        ),
        _valid_place(
            stop_order_type=private_requests.RequestStopOrderType.STOP_LOSS_ORDER,
            stop_price=Decimal("90"),
            stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            time_in_force=TimeInForce.GTC,
        ),
        _valid_place(
            bracket_take_profit_price=Decimal("110"),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            time_in_force=TimeInForce.GTC,
        ),
        _valid_place(time_in_force=TimeInForce.IOC),
    )
    for field, value in (("post_only", False), ("post_only", True), ("mmp", MmpLevel.MMP1)):
        assert (
            getattr(
                place(**_valid_place(time_in_force=TimeInForce.GTC, **{field: value})),
                field,
            )
            is value
        )
        for values in restricted_contexts:
            with pytest.raises(ValidationError):
                place(**(values | {field: value}))


def test_modify_order_owns_total_size_and_stop_trail_xor() -> None:
    model = private_requests.ModifyOrderRequest
    assert model(id=1, product_id=2, size=3).size == 3
    assert model(id=1, product_id=2, size=3, stop_price=Decimal("1")).stop_price == 1
    assert model(id=1, product_id=2, size=3, trail_amount=Decimal("1")).trail_amount == 1
    with pytest.raises(ValidationError):
        model(
            id=1,
            product_id=2,
            size=3,
            stop_price=Decimal("1"),
            trail_amount=Decimal("1"),
        )
    with pytest.raises(ValidationError):
        model(id=1, product_id=2)  # type: ignore[call-arg]


def test_cancel_and_account_setting_requests_are_exact_and_scoped() -> None:
    assert private_requests.CancelOrderRequest(id=1, product_id=2).id == 1
    for limit in (False, True):
        for stop in (False, True):
            for reduce_only in (False, True):
                values = {
                    "product_id": 2,
                    "cancel_limit_orders": limit,
                    "cancel_stop_orders": stop,
                    "cancel_reduce_only_orders": reduce_only,
                }
                if limit or stop or reduce_only:
                    assert private_requests.CancelAllRequest(**values).product_id == 2
                else:
                    with pytest.raises(ValidationError):
                        private_requests.CancelAllRequest(**values)
    assert private_requests.SetLeverageRequest(product_id=2, leverage=Decimal("3")).leverage == 3
    assert (
        private_requests.SetMarginModeRequest(
            margin_mode=private_requests.RequestMarginMode.ISOLATED,
            subaccount_user_id="7",
        ).subaccount_user_id
        == "7"
    )
    assert (
        private_requests.ChangePositionMarginRequest(
            product_id=2, delta_margin=Decimal("-1")
        ).delta_margin
        == -1
    )


def test_nested_bracket_create_and_edit_truth_tables_are_complete() -> None:
    stop_market = private_requests.BracketStopLossOrder(
        order_type=OrderType.MARKET_ORDER, stop_price=Decimal("90")
    )
    stop_limit = private_requests.BracketStopLossOrder(
        order_type=OrderType.LIMIT_ORDER,
        trail_amount=Decimal("2"),
        limit_price=Decimal("89"),
    )
    take_market = private_requests.BracketTakeProfitOrder(
        order_type=OrderType.MARKET_ORDER, stop_price=Decimal("110")
    )
    request = private_requests.CreateBracketRequest(
        product_id=2,
        stop_loss_order=stop_market,
        take_profit_order={
            "order_type": OrderType.LIMIT_ORDER,
            "stop_price": Decimal("110"),
            "limit_price": Decimal("111"),
        },
        bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
    )
    assert request.stop_loss_order == stop_market
    assert request.stop_loss_order is not stop_market
    assert stop_limit.trail_amount == 2 and take_market.stop_price == 110
    with pytest.raises(ValidationError):
        private_requests.BracketStopLossOrder(
            order_type=OrderType.MARKET_ORDER,
            stop_price=Decimal("90"),
            trail_amount=Decimal("2"),
        )
    with pytest.raises(ValidationError):
        private_requests.BracketTakeProfitOrder(
            order_type=OrderType.LIMIT_ORDER, stop_price=Decimal("110")
        )
    with pytest.raises(ValidationError):
        private_requests.CreateBracketRequest(
            product_id=2, bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE
        )
    assert (
        private_requests.EditBracketRequest(
            id=1, product_id=2, bracket_take_profit_price=Decimal("110")
        ).bracket_take_profit_price
        == 110
    )
    with pytest.raises(ValidationError):
        private_requests.EditBracketRequest(id=1, product_id=2)
    with pytest.raises(ValidationError):
        private_requests.EditBracketRequest(
            id=1,
            product_id=2,
            bracket_stop_loss_price=Decimal("90"),
            bracket_trail_amount=Decimal("2"),
        )

    stop_legs = (
        {"order_type": OrderType.MARKET_ORDER, "stop_price": Decimal("1")},
        {"order_type": OrderType.MARKET_ORDER, "trail_amount": Decimal("1")},
        {
            "order_type": OrderType.LIMIT_ORDER,
            "stop_price": Decimal("1"),
            "limit_price": Decimal("1"),
        },
        {
            "order_type": OrderType.LIMIT_ORDER,
            "trail_amount": Decimal("1"),
            "limit_price": Decimal("1"),
        },
    )
    for values in stop_legs:
        leg = private_requests.BracketStopLossOrder(**values)
        assert all(getattr(leg, field) == value for field, value in values.items())
    invalid_stop_legs = (
        {"order_type": OrderType.MARKET_ORDER},
        {
            "order_type": OrderType.MARKET_ORDER,
            "stop_price": Decimal("1"),
            "trail_amount": Decimal("1"),
        },
        {
            "order_type": OrderType.MARKET_ORDER,
            "stop_price": Decimal("1"),
            "limit_price": Decimal("1"),
        },
        {"order_type": OrderType.LIMIT_ORDER, "stop_price": Decimal("1")},
    )
    for values in invalid_stop_legs:
        with pytest.raises(ValidationError):
            private_requests.BracketStopLossOrder(**values)
    assert (
        private_requests.BracketTakeProfitOrder(
            order_type=OrderType.MARKET_ORDER, stop_price=Decimal("1")
        ).limit_price
        is None
    )
    assert (
        private_requests.BracketTakeProfitOrder(
            order_type=OrderType.LIMIT_ORDER,
            stop_price=Decimal("1"),
            limit_price=Decimal("1"),
        ).limit_price
        == 1
    )
    for values in (
        {
            "order_type": OrderType.MARKET_ORDER,
            "stop_price": Decimal("1"),
            "limit_price": Decimal("1"),
        },
        {"order_type": OrderType.LIMIT_ORDER, "stop_price": Decimal("1")},
    ):
        with pytest.raises(ValidationError):
            private_requests.BracketTakeProfitOrder(**values)
    for stop_leg, take_leg in (
        (stop_market, None),
        (None, take_market),
        (stop_market, take_market),
        ({"order_type": OrderType.MARKET_ORDER, "stop_price": Decimal("1")}, None),
        (None, {"order_type": OrderType.MARKET_ORDER, "stop_price": Decimal("2")}),
    ):
        created = private_requests.CreateBracketRequest(
            product_id=2,
            stop_loss_order=stop_leg,
            take_profit_order=take_leg,
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        )
        assert created.stop_loss_order is not None or created.take_profit_order is not None
    edit_fields = (
        "bracket_stop_loss_limit_price",
        "bracket_stop_loss_price",
        "bracket_take_profit_limit_price",
        "bracket_take_profit_price",
        "bracket_trail_amount",
    )
    for field in edit_fields:
        assert (
            getattr(
                private_requests.EditBracketRequest(id=1, product_id=2, **{field: Decimal("1")}),
                field,
            )
            == 1
        )
    assert (
        private_requests.EditBracketRequest(
            id=1,
            product_id=2,
            bracket_stop_loss_limit_price=Decimal("1"),
            bracket_stop_loss_price=Decimal("2"),
            bracket_take_profit_limit_price=Decimal("3"),
            bracket_take_profit_price=Decimal("4"),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ).bracket_take_profit_price
        == 4
    )


def test_request_decimal_families_reject_coercion_and_nonfinite_values() -> None:
    stop_type = private_requests.RequestStopOrderType.STOP_LOSS_ORDER
    trigger = StopTriggerMethod.MARK_PRICE
    positive_cases: tuple[tuple[type[object], str, dict[str, object]], ...] = (
        (private_requests.PlaceOrderRequest, "limit_price", _valid_place()),
        (
            private_requests.PlaceOrderRequest,
            "stop_price",
            _valid_place(stop_order_type=stop_type, stop_trigger_method=trigger),
        ),
        (
            private_requests.PlaceOrderRequest,
            "trail_amount",
            _valid_place(stop_order_type=stop_type, stop_trigger_method=trigger),
        ),
        (
            private_requests.PlaceOrderRequest,
            "bracket_stop_loss_limit_price",
            _valid_place(bracket_stop_loss_price=Decimal("1"), bracket_stop_trigger_method=trigger),
        ),
        (
            private_requests.PlaceOrderRequest,
            "bracket_stop_loss_price",
            _valid_place(bracket_stop_trigger_method=trigger),
        ),
        (
            private_requests.PlaceOrderRequest,
            "bracket_trail_amount",
            _valid_place(bracket_stop_trigger_method=trigger),
        ),
        (
            private_requests.PlaceOrderRequest,
            "bracket_take_profit_limit_price",
            _valid_place(
                bracket_take_profit_price=Decimal("1"), bracket_stop_trigger_method=trigger
            ),
        ),
        (
            private_requests.PlaceOrderRequest,
            "bracket_take_profit_price",
            _valid_place(bracket_stop_trigger_method=trigger),
        ),
        (private_requests.ModifyOrderRequest, "limit_price", {"id": 1, "product_id": 2, "size": 3}),
        (private_requests.ModifyOrderRequest, "stop_price", {"id": 1, "product_id": 2, "size": 3}),
        (
            private_requests.ModifyOrderRequest,
            "trail_amount",
            {"id": 1, "product_id": 2, "size": 3},
        ),
        (
            private_requests.BracketStopLossOrder,
            "stop_price",
            {"order_type": OrderType.MARKET_ORDER},
        ),
        (
            private_requests.BracketStopLossOrder,
            "trail_amount",
            {"order_type": OrderType.MARKET_ORDER},
        ),
        (
            private_requests.BracketStopLossOrder,
            "limit_price",
            {"order_type": OrderType.LIMIT_ORDER, "stop_price": Decimal("1")},
        ),
        (
            private_requests.BracketTakeProfitOrder,
            "stop_price",
            {"order_type": OrderType.MARKET_ORDER},
        ),
        (
            private_requests.BracketTakeProfitOrder,
            "limit_price",
            {"order_type": OrderType.LIMIT_ORDER, "stop_price": Decimal("1")},
        ),
        *(
            (private_requests.EditBracketRequest, field, {"id": 1, "product_id": 2})
            for field in (
                "bracket_stop_loss_limit_price",
                "bracket_stop_loss_price",
                "bracket_take_profit_limit_price",
                "bracket_take_profit_price",
                "bracket_trail_amount",
            )
        ),
        (private_requests.SetLeverageRequest, "leverage", {"product_id": 2}),
    )
    invalid_positive: tuple[object, ...] = (
        0,
        1,
        1.0,
        "1",
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    )
    for model, field, base in positive_cases:
        accepted = model(**(base | {field: Decimal("1")}))  # type: ignore[call-arg]
        assert getattr(accepted, field) == Decimal("1")
        for invalid in invalid_positive:
            with pytest.raises(ValidationError) as captured:
                model(**(base | {field: invalid}))  # type: ignore[call-arg]
            assert all(detail.get("input") is None for detail in captured.value.errors())
    for value in (Decimal("-1"), Decimal("1")):
        assert (
            private_requests.ChangePositionMarginRequest(
                product_id=2, delta_margin=value
            ).delta_margin
            == value
        )
    for invalid in (
        0,
        1,
        1.0,
        "1",
        Decimal("0"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ):
        with pytest.raises(ValidationError):
            private_requests.ChangePositionMarginRequest(
                product_id=2,
                delta_margin=invalid,  # type: ignore[arg-type]
            )


def test_request_boolean_presence_semantics_are_exact() -> None:
    for field, base in (
        ("post_only", _valid_place(time_in_force=TimeInForce.GTC)),
        ("reduce_only", _valid_place()),
    ):
        omitted = private_requests.PlaceOrderRequest(**base)
        explicit_none = private_requests.PlaceOrderRequest(**(base | {field: None}))
        assert omitted == explicit_none
        for value in (False, True):
            request = private_requests.PlaceOrderRequest(**(base | {field: value}))
            assert getattr(request, field) is value
            assert request.model_dump()[field] is value
        for invalid in (0, 1, "false"):
            with pytest.raises(ValidationError):
                private_requests.PlaceOrderRequest(**(base | {field: invalid}))
    omitted_modify = private_requests.ModifyOrderRequest(id=1, product_id=2, size=3)
    none_modify = private_requests.ModifyOrderRequest(id=1, product_id=2, size=3, post_only=None)
    assert omitted_modify == none_modify
    for value in (False, True):
        assert (
            private_requests.ModifyOrderRequest(
                id=1, product_id=2, size=3, post_only=value
            ).post_only
            is value
        )
    for field in ("cancel_limit_orders", "cancel_stop_orders", "cancel_reduce_only_orders"):
        base = {
            "product_id": 2,
            "cancel_limit_orders": True,
            "cancel_stop_orders": True,
            "cancel_reduce_only_orders": True,
        }
        for value in (False, True):
            assert (
                getattr(private_requests.CancelAllRequest(**(base | {field: value})), field)
                is value
            )
        for invalid in (None, 0, 1, "true"):
            with pytest.raises(ValidationError):
                private_requests.CancelAllRequest(**(base | {field: invalid}))
        with pytest.raises(ValidationError):
            private_requests.CancelAllRequest(
                **{key: value for key, value in base.items() if key != field}
            )


def test_mutation_client_ids_are_oms_only() -> None:
    assert (
        private_requests.PlaceOrderRequest(**_valid_place(client_order_id="OMS_7")).client_order_id
        == "OMS_7"
    )
    for observed_only in ("desk.alpha", "desk:7"):
        with pytest.raises(ValidationError):
            private_requests.PlaceOrderRequest(**_valid_place(client_order_id=observed_only))
    assert "client_order_id" not in private_requests.CancelOrderRequest.model_fields


def test_mutation_requests_own_nested_values_copy_and_dumps() -> None:
    supplied = {
        "order_type": OrderType.MARKET_ORDER,
        "stop_price": Decimal("90"),
    }
    request = private_requests.CreateBracketRequest(
        product_id=2,
        stop_loss_order=supplied,
        bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
    )
    supplied["stop_price"] = Decimal("1")
    assert request.stop_loss_order is not supplied
    assert request.stop_loss_order.stop_price == Decimal("90")
    assert request.model_copy() == request
    assert request.model_dump(mode="json")["stop_loss_order"]["stop_price"] == "90"
    false_request = private_requests.PlaceOrderRequest(**_valid_place(reduce_only=False))
    absent_request = private_requests.PlaceOrderRequest(**_valid_place())
    assert false_request.reduce_only is False and absent_request.reduce_only is None
    assert private_requests.CreateBracketRequest(
        product_id=2,
        stop_loss_order=supplied,
        take_profit_order=None,
        bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
    ) == private_requests.CreateBracketRequest(
        product_id=2,
        stop_loss_order=supplied,
        bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
    )

    callbacks: list[str] = []

    class HostileDict(dict[object, object]):
        def __iter__(self) -> Iterator[object]:
            callbacks.append("iter")
            raise AssertionError("hostile nested mapping iteration")

        def __getitem__(self, key: object) -> object:
            callbacks.append("getitem")
            raise AssertionError("hostile nested mapping lookup")

        def __repr__(self) -> str:
            callbacks.append("repr")
            raise AssertionError("hostile nested mapping repr")

    with pytest.raises(ValidationError) as captured:
        private_requests.CreateBracketRequest(
            product_id=2,
            stop_loss_order=HostileDict(supplied),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        )
    assert all(detail.get("input") is None for detail in captured.value.errors())
    assert callbacks == []

    nested = request.stop_loss_order
    assert nested is not None
    dict.__setitem__(object.__getattribute__(nested, "__dict__"), "stop_price", "canary")
    actions = (
        request.model_copy,
        request.model_dump,
        lambda: request.model_dump(mode="json"),
        request.model_dump_json,
        lambda: private_requests.CreateBracketRequest.model_validate(request),
    )
    for action in actions:
        with pytest.raises(ValidationError) as corrupted:
            action()
        assert "canary" not in repr(corrupted.value.errors())
        assert all(detail.get("input") is None for detail in corrupted.value.errors())


def test_mutation_request_models_are_structurally_inert() -> None:
    names = (
        "PlaceOrderRequest",
        "ModifyOrderRequest",
        "CancelOrderRequest",
        "CancelAllRequest",
        "SetLeverageRequest",
        "SetMarginModeRequest",
        "ChangePositionMarginRequest",
        "BracketStopLossOrder",
        "BracketTakeProfitOrder",
        "CreateBracketRequest",
        "EditBracketRequest",
    )
    forbidden = {
        "send",
        "sign",
        "acquire",
        "dispatch",
        "execute",
        "commit",
        "request",
        "client",
        "credential",
        "limiter",
        "transport",
        "audit",
    }
    for name in names:
        model = getattr(private_requests, name)
        assert forbidden.isdisjoint(vars(model))
        for field in model.model_fields.values():
            annotation = repr(field.annotation).lower()
            assert all(
                token not in annotation
                for token in (
                    "credential",
                    "limiter",
                    "transport",
                    "audit",
                    "signer",
                    "privateexecutionclient",
                    "httpx",
                )
            )
    assert forbidden.isdisjoint(private_requests.__dict__)
    path = Path(private_requests.__file__ or "")
    tree = ast.parse(path.read_text())
    denied_modules = {
        "asyncio",
        "httpx",
        "logging",
        "os",
        "socket",
        "sqlalchemy",
        "hmt_crypto.exchange.delta",
        "hmt_crypto.persistence",
    }
    imported_modules = {
        node.module or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names[:1]
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert all(
        not any(module == denied or module.startswith(denied + ".") for denied in denied_modules)
        for module in imported_modules
    )
    import hmt_crypto.domain as domain_package
    import hmt_crypto.exchange as exchange_package

    assert all(name not in domain_package.__all__ for name in names)
    assert all(name not in exchange_package.__all__ for name in names)


def _registered_protocol_overloads(name: str) -> list[object]:
    """Read overload registry metadata without adding a runtime implementation."""

    def probe() -> None:
        pass

    probe.__module__ = private_exchange.__name__
    probe.__qualname__ = f"PrivateReadClient.{name}"
    return get_overloads(probe)


def test_private_read_protocol_method_surface_is_exact() -> None:
    expected = (
        "get_balances",
        "get_wallet_transactions",
        "get_latest_position",
        "get_margined_positions",
        "get_open_orders",
        "get_order_history",
        "get_fills",
        "get_order",
        "get_order_by_client_id",
        "get_leverage",
        "aclose",
    )
    namespace = vars(private_exchange.PrivateReadClient)
    assert tuple(name for name in namespace if not name.startswith("_")) == expected
    assert private_exchange.PrivateReadClient._is_protocol is True
    overloaded = {"get_wallet_transactions", "get_open_orders", "get_order_history", "get_fills"}
    for name in expected:
        value = namespace[name]
        if name in overloaded:
            assert len(_registered_protocol_overloads(name)) == 2
        else:
            assert inspect.iscoroutinefunction(value)
    query_types = {
        "get_wallet_transactions": WalletTransactionQuery,
        "get_latest_position": LatestPositionQuery,
        "get_margined_positions": MarginedPositionQuery,
        "get_open_orders": OpenOrderQuery,
        "get_order_history": OrderHistoryQuery,
        "get_fills": FillQuery,
        "get_order": OrderByIdQuery,
        "get_order_by_client_id": OrderByClientIdQuery,
        "get_leverage": LeverageQuery,
    }
    for name, query_type in query_types.items():
        declarations = (
            _registered_protocol_overloads(name) if name in overloaded else [namespace[name]]
        )
        for declaration in declarations:
            signature = inspect.signature(declaration)
            assert tuple(signature.parameters) == (
                ("self", "query", "mode") if name in overloaded else ("self", "query")
            )
            assert get_type_hints(declaration)["query"] is query_type
            if name in overloaded:
                assert signature.parameters["mode"].kind is inspect.Parameter.KEYWORD_ONLY


def test_private_read_protocol_overloads_resolve_exact_canonical_results() -> None:
    protocol = private_exchange.PrivateReadClient
    paginated = {
        "get_wallet_transactions": (
            StrictWalletTransactionPaginationResult,
            DiagnosticWalletTransactionPaginationResult,
        ),
        "get_open_orders": (StrictOrderPaginationResult, DiagnosticOrderPaginationResult),
        "get_order_history": (StrictOrderPaginationResult, DiagnosticOrderPaginationResult),
        "get_fills": (StrictFillPaginationResult, DiagnosticFillPaginationResult),
    }
    observed: set[object] = set()
    for name, expected in paginated.items():
        overloads = _registered_protocol_overloads(name)
        assert len(overloads) == 2
        for overload, result_type in zip(overloads, expected, strict=True):
            assert get_type_hints(overload)["return"] is result_type
            observed.add(result_type)
        first_mode = inspect.signature(overloads[0]).parameters["mode"].default
        second_mode = inspect.signature(overloads[1]).parameters["mode"].default
        assert first_mode is CollectionMode.STRICT
        assert second_mode is inspect.Parameter.empty

    direct = {
        "get_balances": ObservedBalanceSnapshot,
        "get_latest_position": ObservedLatestPositionLookupResult,
        "get_margined_positions": ObservedMarginedPositions,
        "get_order": ObservedOrderLookupResult,
        "get_order_by_client_id": ObservedOrderLookupResult,
        "get_leverage": ObservedLeverage,
        "aclose": type(None),
    }
    for name, result_type in direct.items():
        assert get_type_hints(getattr(protocol, name))["return"] is result_type
        observed.add(result_type)
    assert len(observed - {type(None)}) == 11


def test_private_read_protocol_never_subscripts_guarded_origins() -> None:
    path = Path(__file__).resolve().parents[2] / "src/hmt_crypto/exchange/private.py"
    tree = ast.parse(path.read_text())
    guarded = {
        "ObservedEnum",
        "ValueField",
        "FieldPresence",
        "NullableField",
        "Observed",
        "LookupFound",
        "LookupResult",
        "PrivateCursorPage",
        "StrictPrivatePaginationResult",
        "DiagnosticPrivatePaginationResult",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            assert node.value.id not in guarded
        if isinstance(node, ast.Name):
            assert node.id != "Any"
    protocol = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PrivateReadClient"
    )
    declarations = [node for node in protocol.body if isinstance(node, ast.AsyncFunctionDef)]
    overloaded = {"get_wallet_transactions", "get_open_orders", "get_order_history", "get_fills"}
    for name in overloaded:
        methods = [node for node in declarations if node.name == name]
        assert len(methods) == 2
        assert all(
            any(
                isinstance(decorator, ast.Name) and decorator.id == "overload"
                for decorator in method.decorator_list
            )
            for method in methods
        )
    assert not any(
        isinstance(node, ast.Constant) and isinstance(node.value, str)
        for method in declarations
        for node in ast.walk(method.args)
    )
    assert not any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
        for node in ast.walk(protocol)
    )


def test_private_read_protocol_has_no_execution_or_runtime_authority() -> None:
    namespace = vars(private_exchange.PrivateReadClient)
    forbidden = {
        "place_order",
        "modify_order",
        "cancel_order",
        "cancel_all",
        "request",
        "sign",
        "acquire",
        "dispatch",
        "execute",
        "__aenter__",
        "__aexit__",
        "credentials",
        "switch_account",
    }
    assert forbidden.isdisjoint(namespace)
    assert getattr(private_exchange.PrivateReadClient, "_is_runtime_protocol", False) is False
    tree = ast.parse(Path(private_exchange.__file__ or "").read_text())
    denied_modules = {"asyncio", "httpx", "logging", "socket", "sqlalchemy"}
    imported = {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported.isdisjoint(denied_modules)


def _run_mypy(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "protocol_probe.py"
    path.write_text(textwrap.dedent(source))
    environment = os.environ.copy()
    environment["MYPYPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    return subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--show-error-codes", str(path)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_private_read_protocol_strict_mypy_positive(tmp_path: Path) -> None:
    result = _run_mypy(
        tmp_path,
        """
        from hmt_crypto.domain.account import CollectionMode
        from hmt_crypto.domain.private_orders import (
            DiagnosticFillPaginationResult, DiagnosticOrderPaginationResult,
            DiagnosticWalletTransactionPaginationResult, ObservedLatestPositionLookupResult,
            ObservedLeverage, ObservedOrderLookupResult, StrictFillPaginationResult,
            StrictOrderPaginationResult, StrictWalletTransactionPaginationResult,
        )
        from hmt_crypto.domain.private_positions import ObservedMarginedPositions
        from hmt_crypto.domain.private_wallet import ObservedBalanceSnapshot
        from hmt_crypto.exchange.private import PrivateReadClient
        from hmt_crypto.exchange.private_requests import (
            FillQuery, LatestPositionQuery, LeverageQuery, MarginedPositionQuery,
            OpenOrderQuery, OrderByClientIdQuery, OrderByIdQuery,
            OrderHistoryQuery, WalletTransactionQuery,
        )

        async def prove(client: PrivateReadClient) -> None:
            balances: ObservedBalanceSnapshot = await client.get_balances()
            wallet: StrictWalletTransactionPaginationResult = (
                await client.get_wallet_transactions(WalletTransactionQuery())
            )
            wallet_explicit: StrictWalletTransactionPaginationResult = (
                await client.get_wallet_transactions(
                    WalletTransactionQuery(), mode=CollectionMode.STRICT
                )
            )
            wallet_diag: DiagnosticWalletTransactionPaginationResult = (
                await client.get_wallet_transactions(
                    WalletTransactionQuery(), mode=CollectionMode.DIAGNOSTIC
                )
            )
            position: ObservedLatestPositionLookupResult = (
                await client.get_latest_position(LatestPositionQuery(product_id=1))
            )
            positions: ObservedMarginedPositions = (
                await client.get_margined_positions(MarginedPositionQuery())
            )
            orders: StrictOrderPaginationResult = await client.get_open_orders(OpenOrderQuery())
            order_diag: DiagnosticOrderPaginationResult = (
                await client.get_order_history(
                    OrderHistoryQuery(), mode=CollectionMode.DIAGNOSTIC
                )
            )
            fills: StrictFillPaginationResult = await client.get_fills(FillQuery())
            fill_diag: DiagnosticFillPaginationResult = await client.get_fills(
                FillQuery(), mode=CollectionMode.DIAGNOSTIC
            )
            order: ObservedOrderLookupResult = await client.get_order(OrderByIdQuery(order_id=1))
            order_client: ObservedOrderLookupResult = (
                await client.get_order_by_client_id(
                    OrderByClientIdQuery(client_order_id='OMS_1')
                )
            )
            leverage: ObservedLeverage = await client.get_leverage(LeverageQuery(product_id=1))
            await client.aclose()
        """,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_private_read_protocol_accepts_a_structural_implementation(tmp_path: Path) -> None:
    result = _run_mypy(
        tmp_path,
        """
        from typing import Literal, overload
        from hmt_crypto.domain.account import CollectionMode
        from hmt_crypto.domain.private_orders import (
            DiagnosticFillPaginationResult as DFills,
            DiagnosticOrderPaginationResult as DOrders,
            DiagnosticWalletTransactionPaginationResult as DWallet,
            ObservedLatestPositionLookupResult as LatestResult,
            ObservedLeverage as LeverageResult,
            ObservedOrderLookupResult as OrderResult,
            StrictFillPaginationResult as SFills,
            StrictOrderPaginationResult as SOrders,
            StrictWalletTransactionPaginationResult as SWallet,
        )
        from hmt_crypto.domain.private_positions import ObservedMarginedPositions as Positions
        from hmt_crypto.domain.private_wallet import ObservedBalanceSnapshot as Balances
        from hmt_crypto.exchange.private import PrivateReadClient
        from hmt_crypto.exchange.private_requests import (
            FillQuery, LatestPositionQuery, LeverageQuery, MarginedPositionQuery,
            OpenOrderQuery, OrderByClientIdQuery, OrderByIdQuery,
            OrderHistoryQuery, WalletTransactionQuery,
        )

        class Implementation:
            async def get_balances(self) -> Balances:
                raise NotImplementedError
            @overload
            async def get_wallet_transactions(
                self, query: WalletTransactionQuery, *,
                mode: Literal[CollectionMode.STRICT] = CollectionMode.STRICT,
            ) -> SWallet: ...
            @overload
            async def get_wallet_transactions(
                self, query: WalletTransactionQuery, *,
                mode: Literal[CollectionMode.DIAGNOSTIC],
            ) -> DWallet: ...
            async def get_wallet_transactions(
                self, query: WalletTransactionQuery, *,
                mode: CollectionMode = CollectionMode.STRICT,
            ) -> SWallet | DWallet:
                raise NotImplementedError
            async def get_latest_position(
                self, query: LatestPositionQuery,
            ) -> LatestResult:
                raise NotImplementedError
            async def get_margined_positions(
                self, query: MarginedPositionQuery,
            ) -> Positions:
                raise NotImplementedError
            @overload
            async def get_open_orders(
                self, query: OpenOrderQuery, *,
                mode: Literal[CollectionMode.STRICT] = CollectionMode.STRICT,
            ) -> SOrders: ...
            @overload
            async def get_open_orders(
                self, query: OpenOrderQuery, *,
                mode: Literal[CollectionMode.DIAGNOSTIC],
            ) -> DOrders: ...
            async def get_open_orders(
                self, query: OpenOrderQuery, *,
                mode: CollectionMode = CollectionMode.STRICT,
            ) -> SOrders | DOrders:
                raise NotImplementedError
            @overload
            async def get_order_history(
                self, query: OrderHistoryQuery, *,
                mode: Literal[CollectionMode.STRICT] = CollectionMode.STRICT,
            ) -> SOrders: ...
            @overload
            async def get_order_history(
                self, query: OrderHistoryQuery, *,
                mode: Literal[CollectionMode.DIAGNOSTIC],
            ) -> DOrders: ...
            async def get_order_history(
                self, query: OrderHistoryQuery, *,
                mode: CollectionMode = CollectionMode.STRICT,
            ) -> SOrders | DOrders:
                raise NotImplementedError
            @overload
            async def get_fills(
                self, query: FillQuery, *,
                mode: Literal[CollectionMode.STRICT] = CollectionMode.STRICT,
            ) -> SFills: ...
            @overload
            async def get_fills(
                self, query: FillQuery, *,
                mode: Literal[CollectionMode.DIAGNOSTIC],
            ) -> DFills: ...
            async def get_fills(
                self, query: FillQuery, *,
                mode: CollectionMode = CollectionMode.STRICT,
            ) -> SFills | DFills:
                raise NotImplementedError
            async def get_order(self, query: OrderByIdQuery) -> OrderResult:
                raise NotImplementedError
            async def get_order_by_client_id(
                self, query: OrderByClientIdQuery,
            ) -> OrderResult:
                raise NotImplementedError
            async def get_leverage(self, query: LeverageQuery) -> LeverageResult:
                raise NotImplementedError
            async def aclose(self) -> None:
                pass

        candidate: PrivateReadClient = Implementation()
        """,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_private_read_protocol_strict_mypy_negative(tmp_path: Path) -> None:
    result = _run_mypy(
        tmp_path,
        """
        from hmt_crypto.domain.account import CollectionMode
        from hmt_crypto.domain.private_orders import (
            StrictFillPaginationResult, StrictOrderPaginationResult,
        )
        from hmt_crypto.exchange.private import PrivateReadClient
        from hmt_crypto.exchange.private_requests import FillQuery, OpenOrderQuery

        def consume(value: StrictFillPaginationResult) -> None: ...
        async def bad(client: PrivateReadClient) -> None:
            strict: StrictOrderPaginationResult = await client.get_open_orders(
                OpenOrderQuery(), mode=CollectionMode.DIAGNOSTIC
            )
            consume(await client.get_fills(FillQuery(), mode=CollectionMode.DIAGNOSTIC))
            await client.get_fills(FillQuery(), mode='diagnostic')
        """,
    )
    assert result.returncode != 0
    assert "[assignment]" in result.stdout
    assert "[arg-type]" in result.stdout
