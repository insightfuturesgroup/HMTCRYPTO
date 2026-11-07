"""Import-order and construction-source identity checks for A2.2 owners."""

from __future__ import annotations

import pytest
from delta_private_model_test_support import isolated_python

pytestmark = pytest.mark.unit

_IMPORT_ERROR = "private domain import unavailable"


def _assert_isolated_success(source: str) -> None:
    result = isolated_python(source)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def _hostile_import_probe(
    *,
    leaf_name: str,
    public_name: str,
    target_module_name: str,
    binding: str,
    leaf_identities: tuple[str, ...],
    table_name: str,
    table_size: int,
) -> str:
    return f"""
import gc, importlib, sys, traceback, weakref
account = importlib.import_module('hmt_crypto.domain.account')
leaf = importlib.import_module({leaf_name!r})
target_name = {target_module_name!r}
target = leaf if target_name == {leaf_name!r} else (
    account if target_name == 'hmt_crypto.domain.account' else importlib.import_module(target_name)
)
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
        return 'private-import-canary'
    def __str__(self):
        callbacks.append('str')
        return 'private-import-canary'

def attempt():
    original = target.__dict__[{binding!r}]
    canary = Hostile()
    reference = weakref.ref(canary)
    target.__dict__[{binding!r}] = canary
    try:
        try:
            importlib.import_module({public_name!r})
        except RuntimeError as error:
            retained_error = error
        else:
            raise AssertionError('invalid import source accepted')
    finally:
        target.__dict__[{binding!r}] = original
    del canary
    return retained_error, reference

error, reference = attempt()
assert type(error) is RuntimeError
assert error.args == ({_IMPORT_ERROR!r},)
assert error.__cause__ is None
assert error.__context__ is None
assert {public_name!r} not in sys.modules
assert callbacks == []
rendered = ''.join(traceback.format_exception(error))
assert 'private-import-canary' not in rendered
for frame, _line in traceback.walk_tb(error.__traceback__):
    if frame.f_globals.get('__name__') == {public_name!r}:
        for name in {leaf_identities!r} + ({table_name!r},) + (
            'ExactBool', 'ExactTrue', 'FieldPresence', 'MissingField', 'NullField',
            'Observed', 'ObservedEnum', 'ValueField', '__all__', '_account', '_accounts',
            '_construction', '_construction_namespace', '_contracts', '_facade_copy',
            '_snapshot_function',
        ):
            assert name not in frame.f_globals
gc.collect()
assert reference() is None
public = importlib.import_module({public_name!r})
for name in {leaf_identities!r}:
    assert public.__dict__[name] is leaf.__dict__[name]
assert len(public.__dict__[{table_name!r}]) == {table_size}
"""


@pytest.mark.parametrize(
    ("first", "second"),
    (
        ("hmt_crypto.domain.private_wallet", "hmt_crypto.domain.private_positions"),
        ("hmt_crypto.domain.private_positions", "hmt_crypto.domain.private_wallet"),
    ),
)
def test_wallet_and_positions_import_in_either_order_without_orders(
    first: str, second: str
) -> None:
    """Catch an owner-order cycle or an eager import of the orders owner."""
    result = isolated_python(
        f"""
import importlib, sys
account = importlib.import_module('hmt_crypto.domain.account')
first = importlib.import_module({first!r})
assert 'hmt_crypto.domain.private_orders' not in sys.modules
second = importlib.import_module({second!r})
rows = account._load_reviewed_rows()
assert rows is not None and len(rows) == 8
suffix = '_REVIEWED_GENERIC_SPECIALIZATIONS'
first_table = first.__dict__[next(k for k in first.__dict__ if k.endswith(suffix))]
second_table = second.__dict__[next(k for k in second.__dict__ if k.endswith(suffix))]
assert len(first_table) in (2, 4)
assert len(second_table) in (2, 4)
try:
    account._reviewed_generic_manifest_for_verification()
except RuntimeError as error:
    assert str(error) == 'private generic manifest unavailable'
else:
    raise AssertionError('partial manifest unexpectedly complete')
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("leaf_name", "public_name", "names"),
    (
        (
            "hmt_crypto.domain._private_wallet_contracts",
            "hmt_crypto.domain.private_wallet",
            (
                "WalletTransactionType",
                "WalletTransactionMetadataValue",
                "BalanceSnapshot",
                "WalletTransaction",
            ),
        ),
        (
            "hmt_crypto.domain._private_positions_contracts",
            "hmt_crypto.domain.private_positions",
            (
                "MarginMode",
                "LatestPosition",
                "MarginedPosition",
                "MarginedPositionBatch",
            ),
        ),
    ),
)
@pytest.mark.parametrize("leaf_first", [True, False])
def test_construction_leaf_and_public_facade_share_exact_identities(
    leaf_name: str,
    public_name: str,
    names: tuple[str, ...],
    leaf_first: bool,
) -> None:
    """Catch either import direction reconstructing source-owned identities."""
    imports = (
        f"leaf = importlib.import_module({leaf_name!r})\n"
        f"public = importlib.import_module({public_name!r})"
        if leaf_first
        else f"public = importlib.import_module({public_name!r})\n"
        f"leaf = importlib.import_module({leaf_name!r})"
    )
    result = isolated_python(
        f"""
import importlib
{imports}
for name in {names!r}:
    assert getattr(public, name) is getattr(leaf, name)
    value = getattr(public, name)
    if isinstance(value, type):
        assert value.__module__ == {public_name!r}
        assert value.__name__ == name
        assert value.__qualname__ == name
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("leaf_name", "public_name", "payload_name"),
    (
        (
            "hmt_crypto.domain._private_wallet_contracts",
            "hmt_crypto.domain.private_wallet",
            "WalletTransactionType",
        ),
        (
            "hmt_crypto.domain._private_positions_contracts",
            "hmt_crypto.domain.private_positions",
            "MarginMode",
        ),
    ),
)
@pytest.mark.parametrize("mutated_binding", ["source", "public"])
def test_source_or_public_payload_rebinding_invalidates_loaded_rows(
    leaf_name: str,
    public_name: str,
    payload_name: str,
    mutated_binding: str,
) -> None:
    """Catch one source/public payload binding being replaced after owner load."""
    result = isolated_python(
        f"""
import importlib
account = importlib.import_module('hmt_crypto.domain.account')
leaf = importlib.import_module({leaf_name!r})
public = importlib.import_module({public_name!r})
target = leaf if {mutated_binding!r} == 'source' else public
original = target.__dict__[{payload_name!r}]
target.__dict__[{payload_name!r}] = object()
try:
    assert account._load_reviewed_rows() is None
finally:
    target.__dict__[{payload_name!r}] = original
assert account._load_reviewed_rows() is not None
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_owner_imports_do_not_call_network_or_read_environment_secrets() -> None:
    """Catch model import acquiring an exchange or credential capability."""
    result = isolated_python(
        """
import os, socket
os.environ['DELTA_API_KEY'] = 'import-secret-canary'
os.environ['DELTA_API_SECRET'] = 'import-secret-canary'
def forbidden(*args, **kwargs):
    raise AssertionError('network called during domain import')
socket.create_connection = forbidden
import hmt_crypto.domain.private_wallet
import hmt_crypto.domain.private_positions
assert 'private_orders' not in globals()
"""
    )
    assert result.returncode == 0, result.stderr
    assert "import-secret-canary" not in result.stdout + result.stderr


def test_partial_owner_rows_are_identity_verified_and_orders_remain_absent() -> None:
    """Catch partial owner tables bypassing source identity verification."""
    result = isolated_python(
        """
import importlib, sys
account = importlib.import_module('hmt_crypto.domain.account')
wallet = importlib.import_module('hmt_crypto.domain.private_wallet')
positions = importlib.import_module('hmt_crypto.domain.private_positions')
rows = account._load_reviewed_rows()
assert rows is not None
assert len(rows) == 8
assert tuple(rows[:2]) == account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS
assert tuple(rows[2:6]) == wallet._WALLET_REVIEWED_GENERIC_SPECIALIZATIONS
assert tuple(rows[6:]) == positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS
assert 'hmt_crypto.domain.private_orders' not in sys.modules
try:
    account._reviewed_generic_manifest_for_verification()
except RuntimeError as error:
    assert str(error) == 'private generic manifest unavailable'
else:
    raise AssertionError('partial manifest unexpectedly complete')
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("leaf_name", "public_name", "binding", "leaf_identities", "table_name", "table_size"),
    (
        *(
            (
                "hmt_crypto.domain._private_wallet_contracts",
                "hmt_crypto.domain.private_wallet",
                binding,
                (
                    "WalletTransactionType",
                    "WalletTransactionMetadataProfile",
                    "WalletTransactionMetadataValue",
                    "Balance",
                    "BalanceSnapshot",
                    "WalletTransaction",
                ),
                "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
                4,
            )
            for binding in (
                "WalletTransactionType",
                "WalletTransactionMetadataProfile",
                "WalletTransactionMetadataValue",
                "Balance",
                "BalanceSnapshot",
                "WalletTransaction",
            )
        ),
        *(
            (
                "hmt_crypto.domain._private_positions_contracts",
                "hmt_crypto.domain.private_positions",
                binding,
                (
                    "MarginMode",
                    "PositionProductIdentity",
                    "LatestPosition",
                    "MarginedPosition",
                    "MarginedPositionBatch",
                ),
                "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
                2,
            )
            for binding in (
                "MarginMode",
                "PositionProductIdentity",
                "LatestPosition",
                "MarginedPosition",
                "MarginedPositionBatch",
            )
        ),
    ),
)
def test_facade_rejects_every_rebound_construction_identity_without_retention(
    leaf_name: str,
    public_name: str,
    binding: str,
    leaf_identities: tuple[str, ...],
    table_name: str,
    table_size: int,
) -> None:
    """Catch a rebound leaf identity reaching rebuild or surviving failed import."""
    _assert_isolated_success(
        _hostile_import_probe(
            leaf_name=leaf_name,
            public_name=public_name,
            target_module_name=leaf_name,
            binding=binding,
            leaf_identities=leaf_identities,
            table_name=table_name,
            table_size=table_size,
        )
    )


@pytest.mark.parametrize(
    ("leaf_name", "public_name", "binding", "leaf_identities", "table_name", "table_size"),
    (
        *(
            (
                "hmt_crypto.domain._private_wallet_contracts",
                "hmt_crypto.domain.private_wallet",
                binding,
                (
                    "WalletTransactionType",
                    "WalletTransactionMetadataProfile",
                    "WalletTransactionMetadataValue",
                    "Balance",
                    "BalanceSnapshot",
                    "WalletTransaction",
                ),
                "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
                4,
            )
            for binding in (
                "ObservedEnum",
                "ValueField",
                "FieldPresence",
                "Observed",
                "MissingField",
                "NullField",
            )
        ),
        *(
            (
                "hmt_crypto.domain._private_positions_contracts",
                "hmt_crypto.domain.private_positions",
                binding,
                (
                    "MarginMode",
                    "PositionProductIdentity",
                    "LatestPosition",
                    "MarginedPosition",
                    "MarginedPositionBatch",
                ),
                "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
                2,
            )
            for binding in ("ObservedEnum", "Observed", "ExactBool", "ExactTrue")
        ),
    ),
)
def test_facade_rejects_rebound_account_dependency_without_callbacks(
    leaf_name: str,
    public_name: str,
    binding: str,
    leaf_identities: tuple[str, ...],
    table_name: str,
    table_size: int,
) -> None:
    """Catch a public account family, branch, or boolean alias becoming authority."""
    _assert_isolated_success(
        _hostile_import_probe(
            leaf_name=leaf_name,
            public_name=public_name,
            target_module_name="hmt_crypto.domain.account",
            binding=binding,
            leaf_identities=leaf_identities,
            table_name=table_name,
            table_size=table_size,
        )
    )


@pytest.mark.parametrize(
    (
        "leaf_name",
        "public_name",
        "source_name",
        "binding",
        "leaf_identities",
        "table_name",
        "table_size",
    ),
    (
        *(
            (
                "hmt_crypto.domain._private_wallet_contracts",
                "hmt_crypto.domain.private_wallet",
                source_name,
                binding,
                (
                    "WalletTransactionType",
                    "WalletTransactionMetadataProfile",
                    "WalletTransactionMetadataValue",
                    "Balance",
                    "BalanceSnapshot",
                    "WalletTransaction",
                ),
                "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
                4,
            )
            for source_name, binding in (
                ("hmt_crypto.domain._private_generic_core", "ObservedEnum"),
                ("hmt_crypto.domain._private_generic_core", "ValueField"),
                ("hmt_crypto.domain._private_generic_core", "FieldPresence"),
                ("hmt_crypto.domain._private_generic_core", "Observed"),
                ("hmt_crypto.domain._account_contracts", "MissingField"),
                ("hmt_crypto.domain._account_contracts", "NullField"),
            )
        ),
        *(
            (
                "hmt_crypto.domain._private_positions_contracts",
                "hmt_crypto.domain.private_positions",
                source_name,
                binding,
                (
                    "MarginMode",
                    "PositionProductIdentity",
                    "LatestPosition",
                    "MarginedPosition",
                    "MarginedPositionBatch",
                ),
                "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
                2,
            )
            for source_name, binding in (
                ("hmt_crypto.domain._private_generic_core", "ObservedEnum"),
                ("hmt_crypto.domain._private_generic_core", "Observed"),
                ("hmt_crypto.domain._account_contracts", "ExactBool"),
                ("hmt_crypto.domain._account_contracts", "ExactTrue"),
            )
        ),
    ),
)
def test_facade_rejects_rebound_account_source_without_retention(
    leaf_name: str,
    public_name: str,
    source_name: str,
    binding: str,
    leaf_identities: tuple[str, ...],
    table_name: str,
    table_size: int,
) -> None:
    """Catch a source-only account rewrite bypassing the staged public snapshot."""
    _assert_isolated_success(
        _hostile_import_probe(
            leaf_name=leaf_name,
            public_name=public_name,
            target_module_name=source_name,
            binding=binding,
            leaf_identities=leaf_identities,
            table_name=table_name,
            table_size=table_size,
        )
    )


@pytest.mark.parametrize(
    ("leaf_name", "public_name", "binding", "table_name", "table_size"),
    (
        (
            "hmt_crypto.domain._private_wallet_contracts",
            "hmt_crypto.domain.private_wallet",
            "Balance",
            "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
            4,
        ),
        (
            "hmt_crypto.domain._private_positions_contracts",
            "hmt_crypto.domain.private_positions",
            "LatestPosition",
            "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
            2,
        ),
    ),
)
def test_facade_rejects_same_metaclass_model_with_wrong_direct_base(
    leaf_name: str,
    public_name: str,
    binding: str,
    table_name: str,
    table_size: int,
) -> None:
    """Catch a same-metaclass facade forgery whose direct base and MRO differ."""
    _assert_isolated_success(
        f"""
import gc, importlib, sys, weakref
leaf = importlib.import_module({leaf_name!r})
original = leaf.__dict__[{binding!r}]
class Forged(original):
    pass
Forged.__module__ = {public_name!r}
Forged.__name__ = {binding!r}
Forged.__qualname__ = {binding!r}
reference = weakref.ref(Forged)
leaf.__dict__[{binding!r}] = Forged
try:
    try:
        importlib.import_module({public_name!r})
    except RuntimeError as caught:
        error = caught
    else:
        raise AssertionError('same-metaclass forgery accepted')
finally:
    leaf.__dict__[{binding!r}] = original
del Forged
assert error.args == ({_IMPORT_ERROR!r},)
assert error.__cause__ is None and error.__context__ is None
assert {public_name!r} not in sys.modules
gc.collect()
assert reference() is None
public = importlib.import_module({public_name!r})
assert public.__dict__[{binding!r}] is original
assert len(public.__dict__[{table_name!r}]) == {table_size}
"""
    )


@pytest.mark.parametrize(
    ("leaf_name", "public_name", "binding", "table_name", "table_size"),
    (
        (
            "hmt_crypto.domain._private_wallet_contracts",
            "hmt_crypto.domain.private_wallet",
            "WalletTransactionType",
            "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
            4,
        ),
        (
            "hmt_crypto.domain._private_positions_contracts",
            "hmt_crypto.domain.private_positions",
            "MarginMode",
            "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
            2,
        ),
    ),
)
def test_facade_rejects_mutated_literal_enum_qualname_and_retries(
    leaf_name: str,
    public_name: str,
    binding: str,
    table_name: str,
    table_size: int,
) -> None:
    """Catch a live enum identity whose literal public type state was changed."""
    _assert_isolated_success(
        f"""
import importlib, sys
leaf = importlib.import_module({leaf_name!r})
enum = leaf.__dict__[{binding!r}]
original = enum.__qualname__
enum.__qualname__ = 'private_import_canary'
try:
    try:
        importlib.import_module({public_name!r})
    except RuntimeError as error:
        assert error.args == ({_IMPORT_ERROR!r},)
        assert error.__cause__ is None and error.__context__ is None
    else:
        raise AssertionError('mutated enum type state accepted')
finally:
    enum.__qualname__ = original
assert {public_name!r} not in sys.modules
public = importlib.import_module({public_name!r})
assert public.__dict__[{binding!r}] is enum
assert len(public.__dict__[{table_name!r}]) == {table_size}
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
def test_positions_facade_rejects_wrong_batch_shapes_and_retries(replacement: str) -> None:
    """Catch wrong GenericAlias origin, item, or arity crossing staged import."""
    _assert_isolated_success(
        f"""
import importlib, sys
leaf = importlib.import_module('hmt_crypto.domain._private_positions_contracts')
original = leaf.MarginedPositionBatch
leaf.MarginedPositionBatch = {replacement}
try:
    try:
        importlib.import_module('hmt_crypto.domain.private_positions')
    except RuntimeError as error:
        assert type(error) is RuntimeError
        assert error.args == ({_IMPORT_ERROR!r},)
        assert error.__cause__ is None and error.__context__ is None
    else:
        raise AssertionError('invalid batch alias accepted')
finally:
    leaf.MarginedPositionBatch = original
assert 'hmt_crypto.domain.private_positions' not in sys.modules
positions = importlib.import_module('hmt_crypto.domain.private_positions')
assert positions.MarginedPositionBatch is original
assert len(positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS) == 2
"""
    )


@pytest.mark.parametrize(
    ("leaf_name", "public_name", "binding", "helper_name", "table_name", "table_size"),
    (
        (
            "hmt_crypto.domain._private_wallet_contracts",
            "hmt_crypto.domain.private_wallet",
            "BalanceSnapshot",
            "_wallet_contract_snapshot",
            "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
            4,
        ),
        (
            "hmt_crypto.domain._private_positions_contracts",
            "hmt_crypto.domain.private_positions",
            "MarginMode",
            "_positions_contract_snapshot",
            "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
            2,
        ),
    ),
)
def test_mutation_after_snapshot_fails_cleanly_and_does_not_poison_retry(
    leaf_name: str,
    public_name: str,
    binding: str,
    helper_name: str,
    table_name: str,
    table_size: int,
) -> None:
    """Catch facade rebuild consulting live leaf globals after its source snapshot."""
    _assert_isolated_success(
        f"""
import gc, importlib, sys, weakref
leaf = importlib.import_module({leaf_name!r})
original_binding = leaf.__dict__[{binding!r}]
original_helper = leaf.__dict__[{helper_name!r}]
callbacks = []
class Hostile:
    def __getattribute__(self, name):
        callbacks.append('getattribute')
        raise AssertionError('live leaf identity consulted')
    def __repr__(self):
        callbacks.append('repr')
        return 'after-snapshot-canary'
canary = Hostile()
reference = weakref.ref(canary)
def wrapper():
    snapshot = original_helper()
    leaf.__dict__[{binding!r}] = canary
    return snapshot
wrapper.__module__ = {leaf_name!r}
wrapper.__name__ = {helper_name!r}
wrapper.__qualname__ = {helper_name!r}
leaf.__dict__[{helper_name!r}] = wrapper
try:
    try:
        importlib.import_module({public_name!r})
    except RuntimeError as caught:
        error = caught
    else:
        raise AssertionError('post-snapshot mutation accepted')
finally:
    leaf.__dict__[{helper_name!r}] = original_helper
    leaf.__dict__[{binding!r}] = original_binding
del wrapper, canary
assert error.args == ({_IMPORT_ERROR!r},)
assert error.__cause__ is None and error.__context__ is None
assert callbacks == []
assert {public_name!r} not in sys.modules
gc.collect()
assert reference() is None
public = importlib.import_module({public_name!r})
assert public.__dict__[{binding!r}] is original_binding
assert len(public.__dict__[{table_name!r}]) == {table_size}
"""
    )
