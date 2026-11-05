"""A2.3 owner identity, source, and import-order closure checks."""
# ruff: noqa: E501 -- embedded isolated probes preserve exact executable matrices.

from __future__ import annotations

from itertools import permutations

import pytest
from delta_private_model_test_support import isolated_python

pytestmark = pytest.mark.unit


def _orders() -> object:
    from hmt_crypto.domain import private_orders

    return private_orders


def _assert_orders_owner_binds_exact_43_rows_and_complete_51_row_manifest() -> None:
    """Catch missing, reordered, or duplicate canonical specialization identities."""
    from hmt_crypto.domain import account, private_positions, private_wallet

    orders = _orders()
    expected = (
        (account.ObservedEnum, orders.OrderSide, orders.ObservedOrderSide),
        (account.ObservedEnum, orders.OrderType, orders.ObservedOrderType),
        (account.ObservedEnum, orders.StopOrderType, orders.ObservedStopOrderType),
        (account.ObservedEnum, orders.MmpLevel, orders.ObservedMmpLevel),
        (
            account.ObservedEnum,
            orders.StopTriggerMethod,
            orders.ObservedStopTriggerMethod,
        ),
        (account.ObservedEnum, orders.TimeInForce, orders.ObservedTimeInForce),
        (account.ObservedEnum, orders.OrderState, orders.ObservedOrderState),
        (
            account.ObservedEnum,
            orders.FillContractType,
            orders.ObservedFillContractType,
        ),
        (
            account.ObservedEnum,
            orders.FillNotionalType,
            orders.ObservedFillNotionalType,
        ),
        (account.ObservedEnum, orders.FillType, orders.ObservedFillType),
        (account.ObservedEnum, orders.FillRole, orders.ObservedFillRole),
        (account.ValueField, account.ExactBool, orders.ExactBoolValueField),
        (
            account.ValueField,
            orders.PositivePrivateDecimal,
            orders.PositivePrivateDecimalValueField,
        ),
        (account.ValueField, orders.ObservedMmpLevel, orders.ObservedMmpLevelValueField),
        (
            account.ValueField,
            orders.ObservedStopTriggerMethod,
            orders.ObservedStopTriggerMethodValueField,
        ),
        (
            account.ValueField,
            orders.ObservedTimeInForce,
            orders.ObservedTimeInForceValueField,
        ),
        (account.ValueField, orders.PrivateDecimal, orders.PrivateDecimalValueField),
        (
            account.ValueField,
            orders.ObservedDeltaClientOrderId,
            orders.ObservedDeltaClientOrderIdValueField,
        ),
        (
            account.ValueField,
            orders.FillMetadataValue,
            orders.FillMetadataValueField,
        ),
        (account.FieldPresence, account.ExactBool, orders.ExactBoolPresence),
        (
            account.FieldPresence,
            orders.PositivePrivateDecimal,
            orders.PositivePrivateDecimalPresence,
        ),
        (
            account.FieldPresence,
            orders.ObservedMmpLevel,
            orders.ObservedMmpLevelPresence,
        ),
        (
            account.FieldPresence,
            orders.ObservedStopTriggerMethod,
            orders.ObservedStopTriggerMethodPresence,
        ),
        (
            account.FieldPresence,
            orders.ObservedTimeInForce,
            orders.ObservedTimeInForcePresence,
        ),
        (account.FieldPresence, orders.PrivateDecimal, orders.PrivateDecimalPresence),
        (
            account.FieldPresence,
            orders.ObservedDeltaClientOrderId,
            orders.ObservedDeltaClientOrderIdPresence,
        ),
        (
            account.FieldPresence,
            orders.FillMetadataValue,
            orders.FillMetadataPresence,
        ),
        (
            orders.LookupFound,
            private_positions.LatestPosition,
            orders.LatestPositionLookupFound,
        ),
        (orders.LookupFound, orders.Order, orders.OrderLookupFound),
        (
            orders.LookupResult,
            private_positions.LatestPosition,
            orders.LatestPositionLookupResult,
        ),
        (orders.LookupResult, orders.Order, orders.OrderLookupResult),
        (
            orders.PrivateCursorPage,
            private_wallet.WalletTransaction,
            orders.WalletTransactionCursorPage,
        ),
        (orders.PrivateCursorPage, orders.Order, orders.OrderCursorPage),
        (orders.PrivateCursorPage, orders.Fill, orders.FillCursorPage),
        (
            orders.StrictPrivatePaginationResult,
            private_wallet.WalletTransaction,
            orders.StrictWalletTransactionPaginationResult,
        ),
        (
            orders.StrictPrivatePaginationResult,
            orders.Order,
            orders.StrictOrderPaginationResult,
        ),
        (
            orders.StrictPrivatePaginationResult,
            orders.Fill,
            orders.StrictFillPaginationResult,
        ),
        (
            orders.DiagnosticPrivatePaginationResult,
            private_wallet.WalletTransaction,
            orders.DiagnosticWalletTransactionPaginationResult,
        ),
        (
            orders.DiagnosticPrivatePaginationResult,
            orders.Order,
            orders.DiagnosticOrderPaginationResult,
        ),
        (
            orders.DiagnosticPrivatePaginationResult,
            orders.Fill,
            orders.DiagnosticFillPaginationResult,
        ),
        (
            account.Observed,
            orders.LatestPositionLookupResult,
            orders.ObservedLatestPositionLookupResult,
        ),
        (
            account.Observed,
            orders.OrderLookupResult,
            orders.ObservedOrderLookupResult,
        ),
        (account.Observed, orders.Leverage, orders.ObservedLeverage),
    )
    rows = orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS
    assert len(rows) == 43
    assert tuple((row.family, row.payload, row.specialization) for row in rows) == expected
    complete = account._reviewed_generic_manifest_for_verification()
    assert len(complete) == 51
    assert complete[:2] == account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS
    assert complete[2:6] == private_wallet._WALLET_REVIEWED_GENERIC_SPECIALIZATIONS
    assert complete[6:8] == private_positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS
    assert complete[8:] == rows
    for family, payload, specialization in expected:
        assert family[payload] is specialization


def test_orders_owner_binds_exact_43_rows_and_complete_51_row_manifest() -> None:
    """Prove owner closure without retaining later owners in the pytest process."""
    result = isolated_python(
        """
import runpy, sys
sys.path.insert(0, 'tests/unit')
namespace = runpy.run_path('tests/unit/test_delta_private_a2_3_identity.py')
namespace['_assert_orders_owner_binds_exact_43_rows_and_complete_51_row_manifest']()
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("leaf_name", "names"),
    (
        (
            "hmt_crypto.domain._private_order_contracts",
            ("OrderSide", "OrderType", "OrderState", "Order"),
        ),
        (
            "hmt_crypto.domain._private_fill_contracts",
            ("FillType", "FillProductSummary", "Fill", "FillOrderRef"),
        ),
        (
            "hmt_crypto.domain._private_result_contracts",
            (
                "Leverage",
                "LookupFound",
                "LookupResult",
                "PrivateCursorPage",
                "StrictPrivatePaginationResult",
                "DiagnosticPrivatePaginationResult",
            ),
        ),
    ),
)
@pytest.mark.parametrize("leaf_first", (True, False))
def test_leaf_and_facade_import_orders_preserve_exact_public_identity(
    leaf_name: str, names: tuple[str, ...], leaf_first: bool
) -> None:
    """Catch either import direction reconstructing a source-owned contract."""
    imports = (
        f"leaf = importlib.import_module({leaf_name!r})\n"
        "public = importlib.import_module('hmt_crypto.domain.private_orders')"
        if leaf_first
        else "public = importlib.import_module('hmt_crypto.domain.private_orders')\n"
        f"leaf = importlib.import_module({leaf_name!r})"
    )
    result = isolated_python(
        f"""
import importlib
{imports}
for name in {names!r}:
    assert public.__dict__[name] is leaf.__dict__[name]
    value = public.__dict__[name]
    if isinstance(value, type):
        assert value.__module__ == 'hmt_crypto.domain.private_orders'
        assert value.__name__ == name
        assert value.__qualname__ == name
assert len(public._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "owner_order",
    tuple(
        permutations(
            (
                "hmt_crypto.domain.account",
                "hmt_crypto.domain.private_wallet",
                "hmt_crypto.domain.private_positions",
                "hmt_crypto.domain.private_orders",
            )
        )
    ),
)
def test_all_owner_import_permutations_finish_with_exact_counts(
    owner_order: tuple[str, ...],
) -> None:
    """Catch global registration or import order changing canonical identities."""
    result = isolated_python(
        f"""
import importlib
for module_name in {owner_order!r}:
    importlib.import_module(module_name)
account = importlib.import_module('hmt_crypto.domain.account')
wallet = importlib.import_module('hmt_crypto.domain.private_wallet')
positions = importlib.import_module('hmt_crypto.domain.private_positions')
orders = importlib.import_module('hmt_crypto.domain.private_orders')
rows = account._reviewed_generic_manifest_for_verification()
assert len(rows) == 51
assert [len(account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS),
        len(wallet._WALLET_REVIEWED_GENERIC_SPECIALIZATIONS),
        len(positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS),
        len(orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS)] == [2, 4, 2, 43]
for row in rows:
    assert row.family[row.payload] is row.specialization
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_fill_order_alias_rebinding_cannot_widen_the_public_schema() -> None:
    """Catch a rebound Annotated alias replacing exact fill-reference dispatch."""
    result = isolated_python(
        """
import importlib, sys
from typing import Annotated
leaf = importlib.import_module('hmt_crypto.domain._private_fill_contracts')
original = leaf.FillOrderRef
leaf.FillOrderRef = Annotated[int, 'widened']
try:
    try:
        importlib.import_module('hmt_crypto.domain.private_orders')
    except RuntimeError as error:
        assert error.args == ('private domain import unavailable',)
        assert error.__cause__ is None and error.__context__ is None
    else:
        raise AssertionError('widened fill alias accepted')
finally:
    leaf.FillOrderRef = original
assert 'hmt_crypto.domain.private_orders' not in sys.modules
orders = importlib.import_module('hmt_crypto.domain.private_orders')
assert orders.FillOrderRef is original
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_exact_items_alias_rebinding_cannot_normalize_mutable_items() -> None:
    """Catch a rebound BeforeValidator converting caller lists into trusted tuples."""
    result = isolated_python(
        """
import importlib, sys
from typing import Annotated
from pydantic import BeforeValidator
leaf = importlib.import_module('hmt_crypto.domain._private_result_contracts')
original = leaf._ExactItems
leaf._ExactItems = Annotated[tuple[leaf.T, ...], BeforeValidator(tuple)]
try:
    try:
        importlib.import_module('hmt_crypto.domain.private_orders')
    except RuntimeError as error:
        assert error.args == ('private domain import unavailable',)
        assert error.__cause__ is None and error.__context__ is None
    else:
        raise AssertionError('widened items alias accepted')
finally:
    leaf._ExactItems = original
assert 'hmt_crypto.domain.private_orders' not in sys.modules
orders = importlib.import_module('hmt_crypto.domain.private_orders')
assert len(orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_result_snapshot_rejects_hostile_alias_before_attribute_access() -> None:
    """Catch alias preflight invoking a hostile rebound object's descriptors."""
    result = isolated_python(
        """
import importlib, sys
leaf = importlib.import_module('hmt_crypto.domain._private_result_contracts')
original = leaf._ExactItems
calls = []
class Hostile:
    def __getattribute__(self, name):
        calls.append(name)
        raise AssertionError('alias callback executed')
hostile = Hostile()
leaf._ExactItems = hostile
try:
    try:
        importlib.import_module('hmt_crypto.domain.private_orders')
    except RuntimeError as error:
        assert error.args == ('private domain import unavailable',)
    else:
        raise AssertionError('hostile alias accepted')
finally:
    leaf._ExactItems = original
assert calls == []
assert 'hmt_crypto.domain.private_orders' not in sys.modules
importlib.import_module('hmt_crypto.domain.private_orders')
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("leaf_name", "snapshot_name"),
    (
        ("hmt_crypto.domain._private_fill_contracts", "_fill_contract_snapshot"),
        ("hmt_crypto.domain._private_result_contracts", "_result_contract_snapshot"),
    ),
)
def test_leaf_snapshot_type_gates_modules_before_namespace_access(
    leaf_name: str, snapshot_name: str
) -> None:
    """Catch source preflight reading a hostile module replacement's descriptors."""
    result = isolated_python(
        f"""
import importlib, sys
leaf = importlib.import_module({leaf_name!r})
original = sys.modules['types']
calls = []
class Trap:
    @property
    def __dict__(self):
        calls.append('namespace')
        raise AssertionError('module callback executed')
sys.modules['types'] = Trap()
try:
    assert leaf.__dict__[{snapshot_name!r}]() is None
finally:
    sys.modules['types'] = original
assert calls == []
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("owner_name", "binding"),
    (
        ("hmt_crypto.domain.account", "NullableOpaqueCursor"),
        ("hmt_crypto.domain.private_positions", "ObservedMarginMode"),
    ),
)
def test_prior_owner_alias_is_verified_before_any_result_rebuild(
    owner_name: str, binding: str
) -> None:
    """Catch a corrupted accepted-owner alias reaching Pydantic schema callbacks."""
    result = isolated_python(
        f"""
import importlib, sys
from typing import Annotated
account = importlib.import_module('hmt_crypto.domain.account')
importlib.import_module('hmt_crypto.domain.private_wallet')
importlib.import_module('hmt_crypto.domain.private_positions')
owner = importlib.import_module({owner_name!r})
original = owner.__dict__[{binding!r}]
calls = []
class Spy:
    def __get_pydantic_core_schema__(self, source_type, handler):
        calls.append(source_type)
        return handler(int)
owner.__dict__[{binding!r}] = Annotated[int, Spy()]
leaf_name = 'hmt_crypto.domain._private_result_contracts'
importlib.import_module(leaf_name)
try:
    try:
        importlib.import_module('hmt_crypto.domain.private_orders')
    except RuntimeError as error:
        assert error.args == ('private domain import unavailable',)
        assert error.__cause__ is None and error.__context__ is None
    else:
        raise AssertionError('corrupted prior owner accepted')
finally:
    owner.__dict__[{binding!r}] = original
assert calls == []
assert 'hmt_crypto.domain.private_orders' not in sys.modules
sys.modules.pop(leaf_name)
orders = importlib.import_module('hmt_crypto.domain.private_orders')
assert len(orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_result_snapshot_rejects_custom_metaclass_with_matching_names() -> None:
    """Catch a same-base direct model forged under a custom Pydantic metaclass."""
    result = isolated_python(
        """
import importlib, sys
from pydantic import ConfigDict
leaf = importlib.import_module('hmt_crypto.domain._private_result_contracts')
original = leaf.MarginModeResult
class CustomMeta(type(original)):
    pass
forged = CustomMeta('MarginModeResult', (leaf.PrivateBoundaryModel,), {
    '__module__': 'hmt_crypto.domain.private_orders',
    '__qualname__': 'MarginModeResult',
    '__annotations__': {},
    'model_config': ConfigDict(defer_build=True),
})
leaf.MarginModeResult = forged
try:
    try:
        importlib.import_module('hmt_crypto.domain.private_orders')
    except RuntimeError as error:
        assert error.args == ('private domain import unavailable',)
    else:
        raise AssertionError('custom-metaclass model accepted')
finally:
    leaf.MarginModeResult = original
assert 'hmt_crypto.domain.private_orders' not in sys.modules
orders = importlib.import_module('hmt_crypto.domain.private_orders')
assert orders.MarginModeResult is original
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_orders_import_is_inert_and_contains_no_credentials_or_transport() -> None:
    """Catch importing response contracts acquiring I/O or secret-bearing authority."""
    result = isolated_python(
        """
import os, socket
os.environ['DELTA_API_KEY'] = 'a2-3-secret-canary'
os.environ['DELTA_API_SECRET'] = 'a2-3-secret-canary'
def forbidden(*args, **kwargs):
    raise AssertionError('network acquired during private model import')
socket.create_connection = forbidden
import hmt_crypto.domain.private_orders as orders
for name, value in orders.__dict__.items():
    lowered = name.lower()
    assert 'secret' not in lowered and 'api_key' not in lowered and 'signer' not in lowered
assert len(orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert "a2-3-secret-canary" not in result.stdout + result.stderr


def test_all_a2_owners_construct_and_reject_without_acquiring_runtime_authority() -> None:
    """Block every runtime acquisition class while exercising accepted and rejected DTOs."""
    result = isolated_python(
        """
import asyncio, builtins, httpx, importlib.abc, logging, os, pathlib, pydantic, socket, sys, threading, zoneinfo
from decimal import Decimal
import sqlalchemy
import sqlalchemy.ext.asyncio as sa_async
class WarmPydantic(pydantic.BaseModel):
    value: int
calls = []
def blocked(*args, **kwargs):
    calls.append('authority')
    raise AssertionError('runtime authority acquired')
class Env(dict):
    __getitem__ = get = items = keys = values = __iter__ = blocked
dependency_env = []
def guarded_getenv(key, default=None):
    if key == 'PYDANTIC_DISABLE_PLUGINS': dependency_env.append(key); return default
    return blocked(key, default)
os.getenv = guarded_getenv
os.environ = Env()
builtins.open = blocked
pathlib.Path.open = pathlib.Path.read_bytes = pathlib.Path.read_text = blocked
logging.basicConfig = blocked
logging.Logger.addHandler = blocked
httpx.Client = httpx.AsyncClient = blocked
socket.socket = socket.create_connection = socket.getaddrinfo = blocked
asyncio.create_task = blocked
threading.Thread.start = blocked
sqlalchemy.create_engine = blocked
sa_async.create_async_engine = blocked
blocked_imports = {'supabase', 'hmt_crypto.exchange.delta.signer',
                   'hmt_crypto.exchange.delta.weighted_limiter',
                   'hmt_crypto.exchange.delta.catalog', 'hmt_crypto.exchange.delta.sizing',
                   'hmt_crypto.persistence.db', 'hmt_crypto.persistence.audit_repository',
                   'hmt_crypto.persistence.command_repository'}
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in blocked_imports: blocked()
        return None
sys.meta_path.insert(0, Blocker())
baseline_threads = threading.active_count()
baseline_handlers = tuple(logging.getLogger().handlers)
from hmt_crypto.domain import account, private_wallet, private_positions, private_orders
value = private_orders.Leverage(leverage=Decimal('10'), leverage_encoding=private_orders.LeverageEncoding.DECIMAL_STRING,
                               order_margin=Decimal('0'), product_id=27)
assert value.model_copy().model_dump()['product_id'] == 27
assert private_orders.parse_numeric_fill_order_ref('1').order_id == 1
try:
    account.Observed[object](context=None, value=None)
except Exception:
    pass
else:
    raise AssertionError('rejected specialization accepted')
assert [len(account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS),
        len(private_wallet._WALLET_REVIEWED_GENERIC_SPECIALIZATIONS),
        len(private_positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS),
        len(private_orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS)] == [2, 4, 2, 43]
assert calls == []
assert set(dependency_env) <= {'PYDANTIC_DISABLE_PLUGINS'}
assert threading.active_count() == baseline_threads
assert tuple(logging.getLogger().handlers) == baseline_handlers
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_every_orders_family_rejection_before_owner_bind_is_permanent_and_uncached() -> None:
    """Prove partial subscriptions create nothing and fresh post-bind lookups are canonical."""
    result = isolated_python(
        """
import importlib
from pydantic import ValidationError
account = importlib.import_module('hmt_crypto.domain.account')
wallet = importlib.import_module('hmt_crypto.domain.private_wallet')
positions = importlib.import_module('hmt_crypto.domain.private_positions')
order_leaf = importlib.import_module('hmt_crypto.domain._private_order_contracts')
result_leaf = importlib.import_module('hmt_crypto.domain._private_result_contracts')
cases = (
    (account.ObservedEnum, order_leaf.OrderSide, 'ObservedOrderSide'),
    (account.ValueField, account.ExactBool, 'ExactBoolValueField'),
    (account.FieldPresence, account.ExactBool, 'ExactBoolPresence'),
    (result_leaf.LookupFound, positions.LatestPosition, 'LatestPositionLookupFound'),
    (result_leaf.LookupResult, positions.LatestPosition, 'LatestPositionLookupResult'),
    (result_leaf.PrivateCursorPage, wallet.WalletTransaction, 'WalletTransactionCursorPage'),
    (result_leaf.StrictPrivatePaginationResult, wallet.WalletTransaction, 'StrictWalletTransactionPaginationResult'),
    (result_leaf.DiagnosticPrivatePaginationResult, wallet.WalletTransaction, 'DiagnosticWalletTransactionPaginationResult'),
    (account.Observed, result_leaf.Leverage, 'ObservedLeverage'),
)
marker = account._RejectedPrivateGenericSpecialization
tables = (account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS,
          wallet._WALLET_REVIEWED_GENERIC_SPECIALIZATIONS,
          positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS)
for family, payload, _name in cases:
    assert family[payload] is marker and family[payload] is marker
orders = importlib.import_module('hmt_crypto.domain.private_orders')
assert tables == (account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS,
                  wallet._WALLET_REVIEWED_GENERIC_SPECIALIZATIONS,
                  positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS)
for family, payload, name in cases:
    assert family[payload] is orders.__dict__[name]
try:
    marker()
except ValidationError:
    pass
else:
    raise AssertionError('old rejection marker became usable')
assert len(account._reviewed_generic_manifest_for_verification()) == 51
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("leaf_name", "binding"),
    (
        ("hmt_crypto.domain._private_order_contracts", "OrderSide"),
        ("hmt_crypto.domain._private_order_contracts", "OrderType"),
        ("hmt_crypto.domain._private_order_contracts", "OrderState"),
        ("hmt_crypto.domain._private_order_contracts", "StopOrderType"),
        ("hmt_crypto.domain._private_order_contracts", "MmpLevel"),
        ("hmt_crypto.domain._private_order_contracts", "StopTriggerMethod"),
        ("hmt_crypto.domain._private_order_contracts", "TimeInForce"),
        ("hmt_crypto.domain._private_order_contracts", "Order"),
        ("hmt_crypto.domain._private_fill_contracts", "FillType"),
        ("hmt_crypto.domain._private_fill_contracts", "FillRole"),
        ("hmt_crypto.domain._private_fill_contracts", "FillContractType"),
        ("hmt_crypto.domain._private_fill_contracts", "FillNotionalType"),
        ("hmt_crypto.domain._private_fill_contracts", "FillMetadataValue"),
        ("hmt_crypto.domain._private_fill_contracts", "Fill"),
        ("hmt_crypto.domain._private_result_contracts", "Leverage"),
        ("hmt_crypto.domain._private_result_contracts", "LookupFound"),
        ("hmt_crypto.domain._private_result_contracts", "LookupNotFound"),
        ("hmt_crypto.domain._private_result_contracts", "LookupResult"),
        ("hmt_crypto.domain._private_result_contracts", "PrivateCursorPage"),
        ("hmt_crypto.domain._private_result_contracts", "StrictPrivatePaginationResult"),
        ("hmt_crypto.domain._private_result_contracts", "DiagnosticPrivatePaginationResult"),
    ),
)
def test_rebound_leaf_identity_fails_cleanly_and_retry_is_unpoisoned(
    leaf_name: str, binding: str
) -> None:
    """Catch facade construction consulting a rebound source identity."""
    result = isolated_python(
        f"""
import importlib, sys
leaf = importlib.import_module({leaf_name!r})
original = leaf.__dict__[{binding!r}]
leaf.__dict__[{binding!r}] = object()
try:
    try:
        importlib.import_module('hmt_crypto.domain.private_orders')
    except RuntimeError as error:
        assert error.args == ('private domain import unavailable',)
        assert error.__cause__ is None and error.__context__ is None
    else:
        raise AssertionError('rebound source identity accepted')
finally:
    leaf.__dict__[{binding!r}] = original
assert 'hmt_crypto.domain.private_orders' not in sys.modules
orders = importlib.import_module('hmt_crypto.domain.private_orders')
assert orders.__dict__[{binding!r}] is original
assert len(orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "binding",
    (
        "ObservedMmpLevel",
        "ObservedStopTriggerMethod",
        "ObservedTimeInForce",
        "LatestPositionLookupResult",
        "OrderLookupResult",
    ),
)
def test_each_orders_prior_row_payload_binding_is_identity_checked(binding: str) -> None:
    """Catch a mutable scalar-directory dependency bypassing the exact 43-row owner."""
    result = isolated_python(
        f"""
import importlib
orders = importlib.import_module('hmt_crypto.domain.private_orders')
registry = importlib.import_module('hmt_crypto.domain._private_generic_registry')
original = orders.__dict__[{binding!r}]
calls = []
class Hostile:
    def __getattribute__(self, name):
        calls.append(name)
        raise AssertionError('prior-row callback')
hostile = Hostile()
orders.__dict__[{binding!r}] = hostile
try:
    assert registry._load_reviewed_rows() is None
finally:
    orders.__dict__[{binding!r}] = original
assert calls == []
assert len(registry._load_reviewed_rows()) == 51
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("binding", ("WalletTransaction", "LatestPosition"))
@pytest.mark.parametrize("level", ("source", "public", "consumer"))
def test_cross_owner_payload_source_public_and_consumer_bindings_fail_closed(
    binding: str, level: str
) -> None:
    """Catch a cross-owner payload substitution at every mutable binding layer."""
    result = isolated_python(
        f"""
import importlib, sys
public_name = ('hmt_crypto.domain.private_wallet' if {binding!r} == 'WalletTransaction'
               else 'hmt_crypto.domain.private_positions')
leaf_name = ('hmt_crypto.domain._private_wallet_contracts' if {binding!r} == 'WalletTransaction'
             else 'hmt_crypto.domain._private_positions_contracts')
public = importlib.import_module(public_name)
leaf = importlib.import_module(leaf_name)
if {level!r} == 'consumer':
    target = importlib.import_module('hmt_crypto.domain.private_orders')
    registry = importlib.import_module('hmt_crypto.domain._private_generic_registry')
    original = target.__dict__[{binding!r}]
    target.__dict__[{binding!r}] = object()
    try: assert registry._load_reviewed_rows() is None
    finally: target.__dict__[{binding!r}] = original
    assert len(registry._load_reviewed_rows()) == 51
else:
    target = leaf if {level!r} == 'source' else public
    original = target.__dict__[{binding!r}]
    target.__dict__[{binding!r}] = object()
    try:
        try: importlib.import_module('hmt_crypto.domain.private_orders')
        except RuntimeError as error:
            assert error.args == ('private domain import unavailable',)
            assert error.__cause__ is None and error.__context__ is None
        else: raise AssertionError('cross-owner substitution accepted')
    finally: target.__dict__[{binding!r}] = original
    assert 'hmt_crypto.domain.private_orders' not in sys.modules
    assert len(importlib.import_module('hmt_crypto.domain.private_orders')._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_leaf_facade_and_all_43_candidate_identities_are_pickle_and_schema_stable() -> None:
    """Catch duplicate leaf identities or candidates unavailable to schema consumers."""
    result = isolated_python(
        """
import importlib, pickle
from pydantic import TypeAdapter
orders = importlib.import_module('hmt_crypto.domain.private_orders')
leaves = [importlib.import_module(name) for name in (
    'hmt_crypto.domain._private_order_contracts',
    'hmt_crypto.domain._private_fill_contracts',
    'hmt_crypto.domain._private_result_contracts')]
for leaf in leaves:
    for name in leaf.__all__:
        if name in orders.__dict__:
            value = leaf.__dict__[name]
            assert orders.__dict__[name] is value
            if name != 'FillOrderRef' and (isinstance(value, type) or callable(value)):
                assert pickle.loads(pickle.dumps(value)) is value
            elif name == 'FillOrderRef':
                assert isinstance(TypeAdapter(value).json_schema(), dict)
for row in orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS:
    candidate = row.specialization
    assert pickle.loads(pickle.dumps(candidate)) is candidate
    assert isinstance(TypeAdapter(candidate).json_schema(), dict)
assert len(orders._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS) == 43
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
