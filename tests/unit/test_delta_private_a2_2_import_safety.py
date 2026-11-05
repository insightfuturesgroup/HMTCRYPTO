"""Adversarial staged-import safety checks for A2.2 owner facades."""

from __future__ import annotations

import pytest
from delta_private_model_test_support import isolated_python

pytestmark = pytest.mark.unit

_IMPORT_ERROR = "private domain import unavailable"
_PRIMITIVES = "hmt_crypto.domain.private_primitives"
_WALLET_LEAF = "hmt_crypto.domain._private_wallet_contracts"
_WALLET_PUBLIC = "hmt_crypto.domain.private_wallet"
_WALLET_IDENTITIES = (
    "WalletTransactionType",
    "WalletTransactionMetadataProfile",
    "WalletTransactionMetadataValue",
    "Balance",
    "BalanceSnapshot",
    "WalletTransaction",
)
_POSITIONS_LEAF = "hmt_crypto.domain._private_positions_contracts"
_POSITIONS_PUBLIC = "hmt_crypto.domain.private_positions"
_POSITIONS_IDENTITIES = (
    "MarginMode",
    "PositionProductIdentity",
    "LatestPosition",
    "MarginedPosition",
    "MarginedPositionBatch",
)


def _assert_isolated_success(source: str) -> None:
    result = isolated_python(source)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def _substitution_probe(
    *,
    leaf_name: str,
    public_name: str,
    identities: tuple[str, ...],
    table_name: str,
    table_size: int,
    target_name: str,
    binding: str,
) -> str:
    return f"""
import gc, importlib, sys, traceback, weakref
leaf = importlib.import_module({leaf_name!r})
target = leaf if {target_name!r} == {leaf_name!r} else importlib.import_module({target_name!r})
callbacks = []
class Hostile:
    def __getattribute__(self, name):
        callbacks.append('getattribute')
        raise AssertionError('hostile attribute callback')
    def __bool__(self):
        callbacks.append('bool')
        raise AssertionError('hostile bool callback')
    def __call__(self, *args, **kwargs):
        callbacks.append('call')
        raise AssertionError('hostile call callback')
    def __eq__(self, other):
        callbacks.append('eq')
        raise AssertionError('hostile equality callback')
    def __hash__(self):
        callbacks.append('hash')
        raise AssertionError('hostile hash callback')
    def __iter__(self):
        callbacks.append('iter')
        raise AssertionError('hostile iteration callback')
    def __repr__(self):
        callbacks.append('repr')
        return 'import-safety-canary'
    def __str__(self):
        callbacks.append('str')
        return 'import-safety-canary'

def attempt():
    original = target.__dict__[{binding!r}]
    canary = Hostile()
    reference = weakref.ref(canary)
    target.__dict__[{binding!r}] = canary
    try:
        try:
            importlib.import_module({public_name!r})
        except RuntimeError as error:
            retained = error
        else:
            raise AssertionError('hostile import dependency accepted')
    finally:
        target.__dict__[{binding!r}] = original
    del canary
    return retained, reference

error, reference = attempt()
assert type(error) is RuntimeError
assert error.args == ({_IMPORT_ERROR!r},)
assert error.__cause__ is None and error.__context__ is None
assert callbacks == []
assert {public_name!r} not in sys.modules
assert 'import-safety-canary' not in ''.join(traceback.format_exception(error))
gc.collect()
assert reference() is None
public = importlib.import_module({public_name!r})
for name in {identities!r}:
    assert public.__dict__[name] is leaf.__dict__[name]
assert len(public.__dict__[{table_name!r}]) == {table_size}
"""


_PRIMITIVE_CASES = tuple(
    (
        _WALLET_LEAF,
        _WALLET_PUBLIC,
        _WALLET_IDENTITIES,
        "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
        4,
        target,
        binding,
    )
    for binding in (
        "AssetSymbol",
        "ExactAwareUtcDatetime",
        "ExactStrEnum",
        "ExternalUserText",
        "PrivateBoundaryModel",
        "PrivateDecimal",
        "PrivateId",
    )
    for target in (_WALLET_LEAF, _PRIMITIVES)
) + tuple(
    (
        _POSITIONS_LEAF,
        _POSITIONS_PUBLIC,
        _POSITIONS_IDENTITIES,
        "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
        2,
        target,
        binding,
    )
    for binding in (
        "ExactAwareUtcDatetime",
        "ExactStrEnum",
        "ExternalUserText",
        "NonNegativePrivateDecimal",
        "PrivateBoundaryModel",
        "PrivateDecimal",
        "PrivateId",
        "ProductSymbol",
        "SignedContracts",
        "StrictNonNegativeInt",
    )
    for target in (_POSITIONS_LEAF, _PRIMITIVES)
)


@pytest.mark.parametrize(
    (
        "leaf_name",
        "public_name",
        "identities",
        "table_name",
        "table_size",
        "target_name",
        "binding",
    ),
    _PRIMITIVE_CASES,
)
def test_leaf_and_supplier_primitive_substitutions_fail_before_rebuild(
    leaf_name: str,
    public_name: str,
    identities: tuple[str, ...],
    table_name: str,
    table_size: int,
    target_name: str,
    binding: str,
) -> None:
    """Catch a local or supplier primitive rewrite reaching Pydantic callbacks."""
    _assert_isolated_success(
        _substitution_probe(
            leaf_name=leaf_name,
            public_name=public_name,
            identities=identities,
            table_name=table_name,
            table_size=table_size,
            target_name=target_name,
            binding=binding,
        )
    )


_HELPER_CASES = tuple(
    (_WALLET_LEAF, _WALLET_PUBLIC, _WALLET_IDENTITIES, target, binding)
    for target, binding in (
        (_WALLET_LEAF, "FunctionType"),
        ("types", "FunctionType"),
        (_WALLET_LEAF, "GenericAlias"),
        ("types", "GenericAlias"),
        (_WALLET_LEAF, "ModuleType"),
        ("types", "ModuleType"),
        (_WALLET_LEAF, "Annotated"),
        ("typing", "Annotated"),
        (_WALLET_LEAF, "BeforeValidator"),
        ("pydantic", "BeforeValidator"),
        (_WALLET_LEAF, "_exact_balance_tuple"),
        (_WALLET_LEAF, "_ExactBalances"),
        (_WALLET_LEAF, "_rebuild_wallet_independent"),
        (_WALLET_LEAF, "_rebuild_wallet_dependent"),
    )
) + tuple(
    (_POSITIONS_LEAF, _POSITIONS_PUBLIC, _POSITIONS_IDENTITIES, target, binding)
    for target, binding in (
        (_POSITIONS_LEAF, "FunctionType"),
        ("types", "FunctionType"),
        (_POSITIONS_LEAF, "GenericAlias"),
        ("types", "GenericAlias"),
        (_POSITIONS_LEAF, "ModuleType"),
        ("types", "ModuleType"),
        (_POSITIONS_LEAF, "Annotated"),
        ("typing", "Annotated"),
        (_POSITIONS_LEAF, "MarginedPositionBatch"),
        (_POSITIONS_LEAF, "_rebuild_positions_independent"),
        (_POSITIONS_LEAF, "_rebuild_positions_dependent"),
    )
)


@pytest.mark.parametrize(
    ("leaf_name", "public_name", "identities", "target_name", "binding"),
    _HELPER_CASES,
)
def test_leaf_schema_and_rebuild_helper_substitutions_fail_before_use(
    leaf_name: str,
    public_name: str,
    identities: tuple[str, ...],
    target_name: str,
    binding: str,
) -> None:
    """Catch mutable schema/rebuild helpers being called before source proof."""
    wallet = public_name == _WALLET_PUBLIC
    _assert_isolated_success(
        _substitution_probe(
            leaf_name=leaf_name,
            public_name=public_name,
            identities=identities,
            table_name=(
                "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS"
                if wallet
                else "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS"
            ),
            table_size=4 if wallet else 2,
            target_name=target_name,
            binding=binding,
        )
    )


@pytest.mark.parametrize(
    "replacement",
    (
        "Annotated[list[leaf.Balance], BeforeValidator(leaf._exact_balance_tuple)]",
        "Annotated[tuple[leaf.WalletTransaction, ...], BeforeValidator(leaf._exact_balance_tuple)]",
        "Annotated[tuple[leaf.Balance], BeforeValidator(leaf._exact_balance_tuple)]",
        "Annotated[tuple[leaf.Balance, ...], 'wrong metadata']",
        "Annotated[tuple[leaf.Balance, ...], BeforeValidator(wrong_func)]",
        "Annotated[tuple[leaf.Balance, ...], "
        "BeforeValidator(leaf._exact_balance_tuple, json_schema_input_type=str)]",
    ),
)
def test_wallet_balance_alias_wrong_origin_item_arity_or_metadata_fails(
    replacement: str,
) -> None:
    """Catch a structurally different local balance alias reaching model rebuild."""
    _assert_isolated_success(
        f"""
import importlib, sys
from pydantic import BeforeValidator
from typing import Annotated
leaf = importlib.import_module({_WALLET_LEAF!r})
original = leaf._ExactBalances
callbacks = []
def wrong_func(value):
    callbacks.append('validator')
    raise AssertionError('wrong metadata callback')
leaf._ExactBalances = {replacement}
try:
    try:
        importlib.import_module({_WALLET_PUBLIC!r})
    except RuntimeError as error:
        assert error.args == ({_IMPORT_ERROR!r},)
        assert error.__cause__ is None and error.__context__ is None
    else:
        raise AssertionError('invalid balance alias accepted')
finally:
    leaf._ExactBalances = original
assert callbacks == []
assert {_WALLET_PUBLIC!r} not in sys.modules
wallet = importlib.import_module({_WALLET_PUBLIC!r})
assert wallet.BalanceSnapshot.__module__ == {_WALLET_PUBLIC!r}
assert len(wallet._WALLET_REVIEWED_GENERIC_SPECIALIZATIONS) == 4
"""
    )


@pytest.mark.parametrize(
    "replacement",
    (
        "list[leaf.MarginedPosition]",
        "tuple[leaf.LatestPosition, ...]",
        "tuple[leaf.MarginedPosition]",
    ),
)
def test_positions_batch_wrong_origin_item_or_arity_fails_without_callbacks(
    replacement: str,
) -> None:
    """Catch a reconstructed wrong batch alias crossing the leaf preflight."""
    _assert_isolated_success(
        f"""
import importlib, sys
leaf = importlib.import_module({_POSITIONS_LEAF!r})
original = leaf.MarginedPositionBatch
leaf.MarginedPositionBatch = {replacement}
try:
    try:
        importlib.import_module({_POSITIONS_PUBLIC!r})
    except RuntimeError as error:
        assert error.args == ({_IMPORT_ERROR!r},)
        assert error.__cause__ is None and error.__context__ is None
    else:
        raise AssertionError('invalid positions batch accepted')
finally:
    leaf.MarginedPositionBatch = original
assert {_POSITIONS_PUBLIC!r} not in sys.modules
positions = importlib.import_module({_POSITIONS_PUBLIC!r})
assert positions.MarginedPositionBatch is original
assert len(positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS) == 2
"""
    )


_OWNER_HELPERS = (
    (
        _WALLET_LEAF,
        _WALLET_PUBLIC,
        "_wallet_contract_snapshot",
        "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
        4,
    ),
    (
        _POSITIONS_LEAF,
        _POSITIONS_PUBLIC,
        "_positions_contract_snapshot",
        "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
        2,
    ),
)


@pytest.mark.parametrize(
    ("leaf_name", "public_name", "helper_name", "table_name", "table_size"),
    _OWNER_HELPERS,
)
def test_noncanonical_snapshot_helper_body_is_never_called(
    leaf_name: str,
    public_name: str,
    helper_name: str,
    table_name: str,
    table_size: int,
) -> None:
    """Catch an arbitrary exact function gaining preflight execution authority."""
    _assert_isolated_success(
        f"""
import gc, importlib, sys, weakref
leaf = importlib.import_module({leaf_name!r})
original = leaf.__dict__[{helper_name!r}]
calls = []
def impostor():
    calls.append('body')
    raise AssertionError('impostor helper executed')
reference = weakref.ref(impostor)
leaf.__dict__[{helper_name!r}] = impostor
try:
    try:
        importlib.import_module({public_name!r})
    except RuntimeError as caught:
        error = caught
    else:
        raise AssertionError('noncanonical helper accepted')
finally:
    leaf.__dict__[{helper_name!r}] = original
del impostor
assert error.args == ({_IMPORT_ERROR!r},)
assert error.__cause__ is None and error.__context__ is None
assert calls == []
assert {public_name!r} not in sys.modules
gc.collect()
assert reference() is None
public = importlib.import_module({public_name!r})
assert len(public.__dict__[{table_name!r}]) == {table_size}
"""
    )


@pytest.mark.parametrize(
    ("attribute", "hostile"),
    (
        ("__module__", False),
        ("__module__", True),
        ("__name__", False),
        ("__qualname__", False),
    ),
)
@pytest.mark.parametrize(
    ("leaf_name", "public_name", "helper_name", "table_name", "table_size"),
    _OWNER_HELPERS,
)
def test_snapshot_helper_type_state_rejects_wrong_and_hostile_strings(
    leaf_name: str,
    public_name: str,
    helper_name: str,
    table_name: str,
    table_size: int,
    attribute: str,
    hostile: bool,
) -> None:
    """Catch helper type-state checks comparing a hostile non-string value."""
    _assert_isolated_success(
        f"""
import gc, importlib, sys, weakref
leaf = importlib.import_module({leaf_name!r})
helper = leaf.__dict__[{helper_name!r}]
original = object.__getattribute__(helper, {attribute!r})
callbacks = []
class Hostile:
    def __eq__(self, other):
        callbacks.append('eq')
        raise AssertionError('hostile helper string compared')
    def __repr__(self):
        callbacks.append('repr')
        return 'helper-string-canary'
value = Hostile() if {hostile!r} else 'wrong helper string'
reference = weakref.ref(value) if {hostile!r} else None
object.__setattr__(helper, {attribute!r}, value)
try:
    try:
        importlib.import_module({public_name!r})
    except RuntimeError as caught:
        error = caught
    else:
        raise AssertionError('invalid helper type state accepted')
finally:
    object.__setattr__(helper, {attribute!r}, original)
del value
assert error.args == ({_IMPORT_ERROR!r},)
assert error.__cause__ is None and error.__context__ is None
assert callbacks == []
assert {public_name!r} not in sys.modules
if reference is not None:
    gc.collect()
    assert reference() is None
public = importlib.import_module({public_name!r})
assert len(public.__dict__[{table_name!r}]) == {table_size}
"""
    )


@pytest.mark.parametrize("hostile", (False, True))
@pytest.mark.parametrize(
    ("leaf_name", "public_name", "helper_name", "table_name", "table_size"),
    _OWNER_HELPERS,
)
def test_construction_module_name_is_checked_before_snapshot_helper_call(
    leaf_name: str,
    public_name: str,
    helper_name: str,
    table_name: str,
    table_size: int,
    hostile: bool,
) -> None:
    """Catch a malformed module identity reaching the trusted snapshot helper."""
    _assert_isolated_success(
        f"""
import gc, importlib, sys, weakref
leaf = importlib.import_module({leaf_name!r})
original_name = leaf.__dict__['__name__']
original_helper = leaf.__dict__[{helper_name!r}]
calls = []
class Hostile:
    def __eq__(self, other):
        calls.append('eq')
        raise AssertionError('hostile module name compared')
    def __repr__(self):
        calls.append('repr')
        return 'module-name-canary'
value = Hostile() if {hostile!r} else 'wrong.module.name'
reference = weakref.ref(value) if {hostile!r} else None
def wrapper():
    calls.append('body')
    return original_helper()
wrapper.__module__ = {leaf_name!r}
wrapper.__name__ = {helper_name!r}
wrapper.__qualname__ = {helper_name!r}
leaf.__dict__['__name__'] = value
leaf.__dict__[{helper_name!r}] = wrapper
try:
    try:
        importlib.import_module({public_name!r})
    except RuntimeError as caught:
        error = caught
    else:
        raise AssertionError('invalid construction module accepted')
finally:
    leaf.__dict__['__name__'] = original_name
    leaf.__dict__[{helper_name!r}] = original_helper
del wrapper, value
assert error.args == ({_IMPORT_ERROR!r},)
assert error.__cause__ is None and error.__context__ is None
assert calls == []
assert {public_name!r} not in sys.modules
if reference is not None:
    gc.collect()
    assert reference() is None
public = importlib.import_module({public_name!r})
assert len(public.__dict__[{table_name!r}]) == {table_size}
"""
    )
