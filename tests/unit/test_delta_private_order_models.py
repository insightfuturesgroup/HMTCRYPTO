"""Order response contracts for reviewed Delta private observations."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from delta_private_model_test_support import isolated_python
from pydantic import TypeAdapter, ValidationError

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module", autouse=True)
def _release_later_owner_modules() -> Iterator[None]:
    """Keep this later-owner contract module neutral to earlier-owner suites."""
    yield
    import sys

    domain = sys.modules.get("hmt_crypto.domain")
    for name in (
        "hmt_crypto.domain.private_orders",
        "hmt_crypto.domain._private_order_contracts",
        "hmt_crypto.domain._private_fill_contracts",
        "hmt_crypto.domain._private_result_contracts",
        "hmt_crypto.domain.private_wallet",
        "hmt_crypto.domain._private_wallet_contracts",
        "hmt_crypto.domain.private_positions",
        "hmt_crypto.domain._private_positions_contracts",
    ):
        sys.modules.pop(name, None)
        if domain is not None:
            domain.__dict__.pop(name.rsplit(".", 1)[1], None)


def _orders() -> object:
    from hmt_crypto.domain import private_orders

    return private_orders


def _observed(model: object, raw: str, member: object) -> object:
    return model(raw=raw, known=member)


def _payload(**changes: object) -> dict[str, object]:
    orders = _orders()
    created = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    values: dict[str, object] = {
        "id": 101,
        "user_id": "12345",
        "size": 3,
        "unfilled_size": 1,
        "side": _observed(orders.ObservedOrderSide, "buy", orders.OrderSide.BUY),
        "order_type": _observed(
            orders.ObservedOrderType, "limit_order", orders.OrderType.LIMIT_ORDER
        ),
        "limit_price": Decimal("100.25"),
        "stop_order_type": None,
        "stop_price": None,
        "average_fill_price": Decimal("100"),
        "bracket_order": {"kind": "VALUE", "value": False},
        "bracket_stop_loss_price": {"kind": "MISSING"},
        "bracket_stop_loss_limit_price": {"kind": "NULL"},
        "bracket_take_profit_price": {"kind": "VALUE", "value": Decimal("110")},
        "bracket_take_profit_limit_price": {"kind": "MISSING"},
        "bracket_trail_amount": {"kind": "NULL"},
        "trail_amount": Decimal("1"),
        "cancellation_reason": None,
        "mmp": {"kind": "MISSING"},
        "stop_trigger_method": {"kind": "NULL"},
        "time_in_force": {
            "kind": "VALUE",
            "value": _observed(orders.ObservedTimeInForce, "gtc", orders.TimeInForce.GTC),
        },
        "updated_at": created + timedelta(seconds=1),
        "paid_commission": {"kind": "VALUE", "value": Decimal("-0.1")},
        "commission": {"kind": "MISSING"},
        "reduce_only": {
            "value": True,
            "encoding": orders.ReduceOnlyEncoding.JSON_BOOLEAN,
        },
        "client_order_id": {"kind": "NULL"},
        "state": _observed(orders.ObservedOrderState, "open", orders.OrderState.OPEN),
        "created_at": created,
        "product_id": 27,
        "product_symbol": "BTCUSD",
    }
    values.update(changes)
    return values


def test_order_enum_vocabulary_is_exact_and_unknowns_remain_observations() -> None:
    """Catch missing official tokens or a future token gaining known semantics."""
    orders = _orders()
    cases = (
        (orders.OrderSide, ("buy", "sell")),
        (orders.OrderType, ("limit_order", "market_order")),
        (orders.OrderState, ("open", "pending", "closed", "cancelled")),
        (
            orders.StopOrderType,
            ("stop_loss_order", "take_profit_order", "liquidation_order"),
        ),
        (orders.MmpLevel, ("disabled", "mmp1", "mmp2", "mmp3", "mmp4", "mmp5")),
        (
            orders.StopTriggerMethod,
            ("mark_price", "last_traded_price", "spot_price"),
        ),
        (orders.TimeInForce, ("gtc", "ioc")),
        (orders.ReduceOnlyEncoding, ("JSON_BOOLEAN", "STRING_BOOLEAN")),
    )
    for enum, expected in cases:
        assert tuple(member.value for member in enum) == expected
    unknown = orders.ObservedOrderState(raw="future_state", known=None)
    assert unknown.known is None
    with pytest.raises(ValidationError):
        orders.ObservedOrderState(raw="open", known=None)
    with pytest.raises(ValidationError):
        TypeAdapter(orders.OrderSide).validate_python("buy")


def test_order_retains_all_fields_and_enforces_size_and_time() -> None:
    """Catch a response field becoming optional or contradictory size/time being accepted."""
    orders = _orders()
    order = orders.Order(**_payload())
    assert order.unfilled_size == 1
    assert order.side.known is orders.OrderSide.BUY
    assert order.updated_at > order.created_at
    assert order.model_copy() == order
    dumped = order.model_dump()
    assert tuple(dumped) == tuple(_payload())
    for field in tuple(dumped):
        missing = dict(dumped)
        missing.pop(field)
        with pytest.raises(ValidationError):
            orders.Order.model_validate(missing)
    for change in (
        {"size": 0},
        {"unfilled_size": 4},
        {"updated_at": order.created_at - timedelta(microseconds=1)},
    ):
        with pytest.raises(ValidationError):
            orders.Order(**_payload(**change))


@pytest.mark.parametrize(
    "field",
    (
        "limit_price",
        "stop_price",
        "average_fill_price",
        "trail_amount",
    ),
)
@pytest.mark.parametrize("invalid", (Decimal("0"), Decimal("-0.01")))
def test_nullable_order_prices_require_positive_values(field: str, invalid: Decimal) -> None:
    """Catch zero or negative executable prices being treated as response sentinels."""
    orders = _orders()
    with pytest.raises(ValidationError):
        orders.Order(**_payload(**{field: invalid}))
    assert getattr(orders.Order(**_payload(**{field: None})), field) is None


@pytest.mark.parametrize(
    "field",
    (
        "bracket_stop_loss_price",
        "bracket_stop_loss_limit_price",
        "bracket_take_profit_price",
        "bracket_take_profit_limit_price",
        "bracket_trail_amount",
    ),
)
def test_bracket_prices_preserve_presence_and_reject_nonpositive_values(field: str) -> None:
    """Catch three-way presence collapse or a nonpositive VALUE branch."""
    orders = _orders()
    adapter = TypeAdapter(orders.PositivePrivateDecimalPresence)
    for kind in ("MISSING", "NULL"):
        assert adapter.validate_python({"kind": kind}).kind == kind
    value = adapter.validate_python({"kind": "VALUE", "value": Decimal("0.01")})
    assert value.value == Decimal("0.01")
    for invalid in (Decimal("0"), Decimal("-0.01")):
        with pytest.raises(ValidationError):
            orders.Order(**_payload(**{field: {"kind": "VALUE", "value": invalid}}))


def test_all_twelve_order_presence_fields_keep_missing_null_and_typed_value() -> None:
    """Catch an optional response field losing MISSING/NULL/VALUE distinctions."""
    orders = _orders()
    cases = {
        "bracket_order": (orders.ExactBoolPresence, True),
        "bracket_stop_loss_price": (
            orders.PositivePrivateDecimalPresence,
            Decimal("1"),
        ),
        "bracket_stop_loss_limit_price": (
            orders.PositivePrivateDecimalPresence,
            Decimal("1"),
        ),
        "bracket_take_profit_price": (
            orders.PositivePrivateDecimalPresence,
            Decimal("1"),
        ),
        "bracket_take_profit_limit_price": (
            orders.PositivePrivateDecimalPresence,
            Decimal("1"),
        ),
        "bracket_trail_amount": (
            orders.PositivePrivateDecimalPresence,
            Decimal("1"),
        ),
        "mmp": (
            orders.ObservedMmpLevelPresence,
            orders.ObservedMmpLevel(raw="mmp1", known=orders.MmpLevel.MMP1),
        ),
        "stop_trigger_method": (
            orders.ObservedStopTriggerMethodPresence,
            orders.ObservedStopTriggerMethod(
                raw="mark_price", known=orders.StopTriggerMethod.MARK_PRICE
            ),
        ),
        "time_in_force": (
            orders.ObservedTimeInForcePresence,
            orders.ObservedTimeInForce(raw="ioc", known=orders.TimeInForce.IOC),
        ),
        "paid_commission": (orders.PrivateDecimalPresence, Decimal("-1")),
        "commission": (orders.PrivateDecimalPresence, Decimal("1")),
        "client_order_id": (
            orders.ObservedDeltaClientOrderIdPresence,
            "observed.id:1",
        ),
    }
    for field, (alias, value) in cases.items():
        adapter = TypeAdapter(alias)
        for branch in ({"kind": "MISSING"}, {"kind": "NULL"}):
            assert adapter.validate_python(branch).kind == branch["kind"]
        validated = adapter.validate_python({"kind": "VALUE", "value": value})
        assert validated.kind == "VALUE"
        assert getattr(orders.Order(**_payload(**{field: validated})), field).kind == "VALUE"


def test_reduce_only_records_exact_observation_encoding_without_coercion() -> None:
    """Catch bool/integer coercion or loss of the conflicting response encoding."""
    orders = _orders()
    for value in (True, False):
        for encoding in orders.ReduceOnlyEncoding:
            observed = orders.ObservedReduceOnly(value=value, encoding=encoding)
            assert observed.value is value
            assert observed.encoding is encoding
    for value in (0, 1, "true", "false"):
        with pytest.raises(ValidationError):
            orders.ObservedReduceOnly(value=value, encoding=orders.ReduceOnlyEncoding.JSON_BOOLEAN)


def test_order_forbids_request_only_and_unknown_response_fields() -> None:
    """Catch response DTO widening into request-write authority."""
    orders = _orders()
    for extra in ("post_only", "bracket_stop_trigger_method"):
        with pytest.raises(ValidationError):
            orders.Order(**(_payload() | {extra: True}))


@pytest.mark.parametrize(
    ("model_name", "kwargs", "construct_fails"),
    (
        ("Order", {}, False),
        ("ObservedReduceOnly", {"value": True, "encoding": "JSON_BOOLEAN"}, False),
        ("ObservedOrderState", {"raw": "open", "known": "open"}, True),
        ("ExactBoolValueField", {"kind": "VALUE", "value": True}, True),
    ),
)
def test_order_models_inherit_the_closed_boundary_policy(
    model_name: str, kwargs: dict[str, object], construct_fails: bool
) -> None:
    """Catch a concrete order model weakening A1d or enabling construct bypass."""
    model = getattr(_orders(), model_name)
    for key, expected in {
        "extra": "forbid",
        "frozen": True,
        "hide_input_in_errors": True,
        "revalidate_instances": "always",
        "strict": True,
        "validate_default": True,
    }.items():
        assert model.model_config[key] == expected
    if construct_fails:
        with pytest.raises(ValidationError):
            model.model_construct(**kwargs)
    else:
        forged = model.model_construct(**kwargs)
        with pytest.raises(ValidationError):
            forged.model_dump()


@pytest.mark.parametrize(
    ("model_name", "enum_name", "token"),
    (
        ("ObservedOrderSide", "OrderSide", "buy"),
        ("ObservedOrderType", "OrderType", "limit_order"),
        ("ObservedOrderState", "OrderState", "open"),
        ("ObservedStopOrderType", "StopOrderType", "stop_loss_order"),
        ("ObservedMmpLevel", "MmpLevel", "mmp1"),
        ("ObservedStopTriggerMethod", "StopTriggerMethod", "mark_price"),
        ("ObservedTimeInForce", "TimeInForce", "gtc"),
    ),
)
def test_every_order_observed_enum_keeps_known_and_unknown_tokens_disjoint(
    model_name: str, enum_name: str, token: str
) -> None:
    """Catch any order enum silently inferring or discarding known semantics."""
    orders = _orders()
    model, member = getattr(orders, model_name), getattr(orders, enum_name)(token)
    assert model(raw=token, known=member).known is member
    assert model(raw=f"future_{token}", known=None).known is None
    for raw, known in ((token, None), (f"future_{token}", member)):
        with pytest.raises(ValidationError) as captured:
            model(raw=raw, known=known)
        assert all(detail.get("input") is None for detail in captured.value.errors())


def test_order_boundary_rejects_subclasses_callbacks_corruption_and_non_utc_time() -> None:
    """Catch caller-owned roots, duck scalars, nested subclasses, or corrupt state."""
    orders = _orders()
    calls: list[str] = []

    class Root(dict[str, object]):
        pass

    class SideSubclass(orders.ObservedOrderSide):
        pass

    class HostileBool:
        def __bool__(self) -> bool:
            calls.append("bool")
            raise AssertionError("bool callback")

    payload = _payload()
    with pytest.raises(ValidationError):
        orders.Order.model_validate(Root(payload))
    with pytest.raises(ValidationError):
        orders.Order(**_payload(side=SideSubclass(raw="buy", known=orders.OrderSide.BUY)))
    with pytest.raises(ValidationError):
        orders.ObservedReduceOnly(
            value=HostileBool(), encoding=orders.ReduceOnlyEncoding.JSON_BOOLEAN
        )
    assert calls == []
    for instant in (
        datetime(2026, 11, 5, 12, 0),
        datetime(2026, 11, 5, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    ):
        with pytest.raises(ValidationError):
            orders.Order(**_payload(created_at=instant))
    order = orders.Order(**payload)
    object.__getattribute__(order, "__dict__")["side"] = {"raw": "buy", "known": "buy"}
    for action in (order.model_copy, order.model_dump, order.model_dump_json):
        with pytest.raises(ValidationError):
            action()


def test_forced_order_rebuild_failure_cleans_import_tracebacks_and_retry_state() -> None:
    """Catch a failed staged builder retaining callbacks or a partial owner module."""
    result = isolated_python(
        """
import gc, importlib, sys, traceback, weakref
leaf = importlib.import_module('hmt_crypto.domain._private_order_contracts')
model = leaf.ObservedReduceOnly
present = 'model_rebuild' in model.__dict__
original = model.__dict__.get('model_rebuild')
def fail(*args, **kwargs):
    raise RuntimeError('rebuild-retention-canary')
reference = weakref.ref(fail)
model.model_rebuild = classmethod(fail)
try:
    try: importlib.import_module('hmt_crypto.domain.private_orders')
    except RuntimeError as caught: error = caught
    else: raise AssertionError('forced rebuild failure accepted')
finally:
    if present: model.model_rebuild = original
    else: del model.model_rebuild
del fail
assert error.args == ('private domain import unavailable',)
assert error.__cause__ is None and error.__context__ is None
assert 'rebuild-retention-canary' not in ''.join(traceback.format_exception(error))
assert 'hmt_crypto.domain.private_orders' not in sys.modules
gc.collect()
assert reference() is None
orders = importlib.import_module('hmt_crypto.domain.private_orders')
assert len(orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("leaf_name", "binding", "kind"),
    (
        ("hmt_crypto.domain._private_order_contracts", "OrderSide", "enum"),
        ("hmt_crypto.domain._private_order_contracts", "Order", "model"),
        ("hmt_crypto.domain._private_order_contracts", "_rebuild_order_independent", "function"),
        ("hmt_crypto.domain._private_order_contracts", "_order_contract_snapshot", "function"),
        ("hmt_crypto.domain._private_fill_contracts", "Fill", "model"),
        ("hmt_crypto.domain._private_fill_contracts", "parse_numeric_fill_order_ref", "function"),
        ("hmt_crypto.domain._private_fill_contracts", "_rebuild_fill_independent", "function"),
        ("hmt_crypto.domain._private_fill_contracts", "_fill_contract_snapshot", "function"),
        ("hmt_crypto.domain._private_result_contracts", "Leverage", "model"),
        ("hmt_crypto.domain._private_result_contracts", "LookupFound", "model"),
        ("hmt_crypto.domain._private_result_contracts", "_rebuild_result_independent", "function"),
        ("hmt_crypto.domain._private_result_contracts", "_result_contract_snapshot", "function"),
    ),
)
def test_exact_metaclass_and_function_metadata_forgeries_fail_before_execution(
    leaf_name: str, binding: str, kind: str
) -> None:
    """Catch same-shape source replacement that metadata-only preflight cannot distinguish."""
    result = isolated_python(
        f"""
import importlib, sys, types
from pydantic import ConfigDict
leaf = importlib.import_module({leaf_name!r})
original = leaf.__dict__[{binding!r}]
calls = []
if {kind!r} == 'enum':
    forged = leaf.ExactStrEnum({binding!r}, {{'HACK': 'hack'}},
                               module='hmt_crypto.domain.private_orders', qualname={binding!r})
elif {kind!r} == 'model':
    namespace = {{
        '__module__': 'hmt_crypto.domain.private_orders', '__qualname__': {binding!r},
        '__annotations__': {{'widened': object}}, 'model_config': ConfigDict(defer_build=True),
    }}
    bases = ((original.__bases__[0], leaf.Generic[leaf.T])
             if {binding!r} == 'LookupFound' else original.__bases__)
    forged = types.new_class({binding!r}, bases, {{'metaclass': type(original)}}, namespace.update)
else:
    def forged(*args, **kwargs):
        calls.append('body')
        return ('FORGED', 'bad')
    forged.__module__ = {leaf_name!r}
    forged.__name__ = forged.__qualname__ = {binding!r}
leaf.__dict__[{binding!r}] = forged
try:
    try: importlib.import_module('hmt_crypto.domain.private_orders')
    except RuntimeError as error:
        assert error.args == ('private domain import unavailable',)
        assert error.__cause__ is None and error.__context__ is None
    else: raise AssertionError('same-shape source forgery accepted')
finally:
    leaf.__dict__[{binding!r}] = original
assert calls == []
assert 'hmt_crypto.domain.private_orders' not in sys.modules
orders = importlib.import_module('hmt_crypto.domain.private_orders')
assert len(orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("leaf_name", "binding"),
    (
        ("hmt_crypto.domain._private_order_contracts", "_order_contract_snapshot"),
        ("hmt_crypto.domain._private_fill_contracts", "_fill_contract_snapshot"),
        ("hmt_crypto.domain._private_result_contracts", "_result_contract_snapshot"),
    ),
)
@pytest.mark.parametrize("capture_mode", ("copied", "self"))
def test_snapshot_function_capture_must_match_the_leaf_origin_record(
    leaf_name: str, binding: str, capture_mode: str
) -> None:
    """Reject a lookalike snapshot function before its body can run."""
    result = isolated_python(
        f"""
import importlib, sys
leaf = importlib.import_module({leaf_name!r})
original = leaf.__dict__[{binding!r}]
snapshot = original()
calls = []
def replacement(_source=None):
    calls.append('body')
    return snapshot
replacement.__module__ = {leaf_name!r}
replacement.__name__ = replacement.__qualname__ = {binding!r}
if {capture_mode!r} == 'copied':
    replacement.__defaults__ = original.__defaults__
else:
    replacement.__defaults__ = ((({binding!r},), (replacement,)),)
leaf.__dict__[{binding!r}] = replacement
try:
    try: importlib.import_module('hmt_crypto.domain.private_orders')
    except RuntimeError as error:
        assert error.args == ('private domain import unavailable',)
        assert error.__cause__ is None and error.__context__ is None
    else: raise AssertionError('unowned snapshot function accepted')
finally:
    leaf.__dict__[{binding!r}] = original
assert calls == []
assert 'hmt_crypto.domain.private_orders' not in sys.modules
orders = importlib.import_module('hmt_crypto.domain.private_orders')
assert len(orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a2_3_ast_has_no_serializer_export_registrar_or_unguarded_subscription() -> None:
    """Freeze A2.3 as inert domain grammar rather than package or mutation surface."""
    root = Path(__file__).resolve().parents[2]
    paths = tuple(
        root / "src/hmt_crypto/domain" / name
        for name in (
            "_private_order_contracts.py",
            "_private_fill_contracts.py",
            "_private_result_contracts.py",
            "private_orders.py",
        )
    )
    family_names = {
        "ObservedEnum",
        "ValueField",
        "FieldPresence",
        "LookupFound",
        "LookupResult",
        "PrivateCursorPage",
        "StrictPrivatePaginationResult",
        "DiagnosticPrivatePaginationResult",
        "Observed",
    }
    forbidden_exports = ("_ORDERS_REVIEWED", "_ReviewedGeneric", "_declare_reviewed")
    for path in paths:
        source = path.read_text()
        tree = ast.parse(source)
        assert "field_serializer" not in source and "model_serializer" not in source
        assert not any(token in source for token in ("registrar", "bootstrap", "seal_owner"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                if node.value.id in family_names:
                    allowed_definition = (
                        path.name == "_private_result_contracts.py"
                        and node.value.id
                        in {
                            "StrictPrivatePaginationResult",
                            "DiagnosticPrivatePaginationResult",
                        }
                    )
                    type_only = path.name == "private_orders.py" and node.lineno <= 152
                    assert allowed_definition or type_only
    package_source = (root / "src/hmt_crypto/__init__.py").read_text()
    assert not any(token in package_source for token in forbidden_exports)
    assert "private_orders" not in package_source

    domain_source = (root / "src/hmt_crypto/domain/__init__.py").read_text()
    assert not any(token in domain_source for token in forbidden_exports)
    domain_tree = ast.parse(domain_source)
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "hmt_crypto.domain.private_orders"
        for node in domain_tree.body
    )
    type_checking = next(
        node
        for node in domain_tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
    )
    assert any(
        isinstance(node, ast.ImportFrom) and node.module == "hmt_crypto.domain.private_orders"
        for node in type_checking.body
    )
