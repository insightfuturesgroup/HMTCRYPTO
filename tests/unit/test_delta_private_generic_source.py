"""Construction-source identity proof regressions for reviewed generics."""

from __future__ import annotations

import gc
import importlib
import sys
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Generic, TypeVar, cast, get_args

import pytest
from delta_private_model_test_support import account_api, isolated_python

pytestmark = pytest.mark.unit

_ACCOUNT = "hmt_crypto.domain.account"
_CORE = "hmt_crypto.domain._private_generic_core"
_ORDERS = "hmt_crypto.domain.private_orders"
_SNAPSHOT_MODULE_ROLES = ("wallet_leaf", "wallet", "orders")


class _HostileModuleName(str):
    callbacks = 0

    def __eq__(self, other: object) -> bool:
        del other
        type(self).callbacks += 1
        raise AssertionError("hostile module-name equality ran")

    def __ne__(self, other: object) -> bool:
        del other
        type(self).callbacks += 1
        raise AssertionError("hostile module-name inequality ran")


def _source() -> ModuleType:
    return importlib.import_module("hmt_crypto.domain._private_generic_source")


def _owners_and_rows() -> tuple[tuple[object, ...], tuple[object, ...]]:
    manifest = importlib.import_module("hmt_crypto.domain._private_generic_manifest")
    owners = manifest._reviewed_vocabulary_for_verification()
    return owners, tuple(row for owner in owners for row in owner.rows)


@contextmanager
def _restore_bindings(module: ModuleType, names: tuple[str, ...]) -> Iterator[None]:
    saved = {
        name: (dict.__contains__(module.__dict__, name), dict.get(module.__dict__, name))
        for name in names
    }
    try:
        yield
    finally:
        for name, (present, value) in saved.items():
            if present:
                dict.__setitem__(module.__dict__, name, value)
            else:
                dict.pop(module.__dict__, name, None)


@contextmanager
def _mutate_after_snapshot(
    helper: Callable[[object], object],
    namespace: dict[str, object],
    mutation: Callable[[], None],
) -> Iterator[None]:
    previous = sys.gettrace()
    armed = True

    def trace(frame: object, event: str, arg: object) -> object:
        del arg
        nonlocal armed
        code = object.__getattribute__(frame, "f_code")
        locals_ = object.__getattribute__(frame, "f_locals")
        if (
            armed
            and event == "return"
            and code is helper.__code__
            and locals_["value"] is namespace
        ):
            armed = False
            mutation()
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)


def test_source_verifier_import_is_dependency_light_and_does_not_import_a2_owners() -> None:
    """The verifier imports no public A2 owner or construction leaf dynamically."""
    result = isolated_python(
        """
import sys
for name in tuple(sys.modules):
    if name.startswith('hmt_crypto.domain'):
        sys.modules.pop(name)
import hmt_crypto.domain._private_generic_source
assert 'hmt_crypto.domain.account' not in sys.modules
assert 'hmt_crypto.domain._private_generic_core' not in sys.modules
for name in (
    'hmt_crypto.domain.private_wallet',
    'hmt_crypto.domain.private_positions',
    'hmt_crypto.domain.private_orders',
    'hmt_crypto.domain._private_wallet_contracts',
    'hmt_crypto.domain._private_positions_contracts',
    'hmt_crypto.domain._private_order_contracts',
    'hmt_crypto.domain._private_fill_contracts',
    'hmt_crypto.domain._private_result_contracts',
):
    assert name not in sys.modules
"""
    )
    assert result.returncode == 0, result.stderr


def test_account_pre_table_post_row_and_alias_dependencies_use_exact_sources() -> None:
    """Account model, alias, payload, and branches prove through construction sources."""
    api = account_api()
    source = _source()
    owners, rows = _owners_and_rows()
    model_row, alias_row = rows[:2]
    assert source._verify_pre_table_source(model_row, api.ValueField, api.OpaqueCursor, _ACCOUNT)
    assert source._verify_pre_table_source(alias_row, api.NullableField, api.OpaqueCursor, _ACCOUNT)
    assert source._verify_loaded_row_source(owners[0], model_row, api.ValueField, api.OpaqueCursor)
    assert source._verify_loaded_row_source(
        owners[0], alias_row, api.NullableField, api.OpaqueCursor
    )
    assert source._source_alias_dependencies(alias_row, api.OpaqueCursor, _ACCOUNT) == (
        ("NULL", api.NullField),
        ("VALUE", api.OpaqueCursorValueField),
    )


@pytest.mark.parametrize("invalid", [None, False, 0, "", object()])
def test_present_malformed_source_public_and_consumer_bindings_fail_without_cache(
    invalid: object,
) -> None:
    """Every present falsy, sentinel, or foreign binding is malformed state."""
    api = account_api()
    source = _source()
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    core = importlib.import_module(_CORE)
    primitives = importlib.import_module("hmt_crypto.domain.private_primitives")
    contracts = importlib.import_module("hmt_crypto.domain._account_contracts")
    _owners, rows = _owners_and_rows()
    model_row, alias_row = rows[:2]
    for bound in (invalid, registry._ABSENT):
        for module, name in (
            (core, "ValueField"),
            (api.module, "ValueField"),
            (primitives, "OpaqueCursor"),
            (api.module, "OpaqueCursor"),
        ):
            with _restore_bindings(module, (name,)):
                dict.__setitem__(module.__dict__, name, bound)
                assert not source._verify_pre_table_source(
                    model_row, api.ValueField, api.OpaqueCursor, _ACCOUNT
                )
            assert source._verify_pre_table_source(
                model_row, api.ValueField, api.OpaqueCursor, _ACCOUNT
            )
        for module in (contracts, api.module):
            with _restore_bindings(module, ("NullField",)):
                dict.__setitem__(module.__dict__, "NullField", bound)
                assert (
                    source._source_alias_dependencies(alias_row, api.OpaqueCursor, _ACCOUNT) is None
                )
            assert (
                source._source_alias_dependencies(alias_row, api.OpaqueCursor, _ACCOUNT) is not None
            )


def test_hostile_bound_values_never_receive_equality_or_hash_callbacks() -> None:
    """Identity proof rejects caller-controlled values before any rich operation."""
    api = account_api()
    source = _source()
    core = importlib.import_module(_CORE)
    _owners, rows = _owners_and_rows()
    calls: list[str] = []

    class Hostile:
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("hostile equality ran")

        def __hash__(self) -> int:
            calls.append("hash")
            raise AssertionError("hostile hash ran")

    hostile = Hostile()
    with _restore_bindings(core, ("ValueField",)):
        core.ValueField = hostile
        assert not source._verify_pre_table_source(
            rows[0], api.ValueField, api.OpaqueCursor, _ACCOUNT
        )
    assert calls == []


def test_source_mismatch_rejects_declaration_before_every_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-table proof runs before model/alias construction and before reuse."""
    api = account_api()
    core = importlib.import_module(_CORE)
    calls: list[str] = []

    def forbid(*args: object, **kwargs: object) -> object:
        del args, kwargs
        calls.append("builder")
        raise AssertionError("builder ran")

    monkeypatch.setattr(core, "prepare_class", forbid)
    monkeypatch.setattr(core, "_source_alias_dependencies", forbid, raising=False)
    cases = (
        (
            "ValueField",
            api.module._declare_reviewed_model_candidate,
            "OpaqueCursorValueField",
            "model",
        ),
        (
            "NullableField",
            api.module._declare_reviewed_alias_candidate,
            "NullableOpaqueCursor",
            "alias",
        ),
    )
    for family_name, declaration, candidate_name, kind in cases:
        family = api.module.__dict__[family_name]
        with _restore_bindings(core, (family_name,)):
            dict.__setitem__(core.__dict__, family_name, object())
            with pytest.raises(TypeError) as captured:
                declaration(
                    family,
                    api.OpaqueCursor,
                    canonical_name=candidate_name,
                    owner_module=_ACCOUNT,
                )
            assert str(captured.value) == f"invalid reviewed generic {kind} declaration"
    assert calls == []


def test_coordinated_public_branch_and_alias_tag_replacement_still_rejects() -> None:
    """Public branch and tag changes cannot replace the construction-source anchor."""
    api = account_api()
    source = _source()
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    _owners, rows = _owners_and_rows()
    original_tags = api.NullableOpaqueCursor.__private_tags__

    class ForgedNull(api.NullField):
        extra: str

    with _restore_bindings(api.module, ("NullField",)):
        api.module.NullField = ForgedNull
        api.NullableOpaqueCursor.__private_tags__ = (("NULL", ForgedNull), original_tags[1])
        try:
            assert source._source_alias_dependencies(rows[1], api.OpaqueCursor, _ACCOUNT) is None
            assert registry._load_reviewed_rows() is None
        finally:
            api.NullableOpaqueCursor.__private_tags__ = original_tags
    assert len(registry._load_reviewed_rows() or ()) == 2


@contextmanager
def _future_world() -> Iterator[SimpleNamespace]:
    """Install inert, already-loaded construction/public/consumer modules."""
    from hmt_crypto.domain.private_primitives import ExactStrEnum, PrivateBoundaryModel

    api = account_api()
    parameter = TypeVar("parameter")
    names = (
        "hmt_crypto.domain._private_wallet_contracts",
        "hmt_crypto.domain.private_wallet",
        "hmt_crypto.domain._private_positions_contracts",
        "hmt_crypto.domain.private_positions",
        "hmt_crypto.domain._private_order_contracts",
        "hmt_crypto.domain._private_fill_contracts",
        "hmt_crypto.domain._private_result_contracts",
        _ORDERS,
    )
    saved = {
        name: (dict.__contains__(sys.modules, name), dict.get(sys.modules, name)) for name in names
    }
    modules = {name: ModuleType(name) for name in names}

    class WalletTransactionType(ExactStrEnum):
        __module__ = "hmt_crypto.domain.private_wallet"
        DEPOSIT = "DEPOSIT"

    class WalletTransactionMetadataValue(PrivateBoundaryModel):
        __module__ = "hmt_crypto.domain.private_wallet"
        value: int

    class BalanceSnapshot(PrivateBoundaryModel):
        __module__ = "hmt_crypto.domain.private_wallet"
        value: int

    class WalletTransaction(PrivateBoundaryModel):
        __module__ = "hmt_crypto.domain.private_wallet"
        value: int

    class MarginMode(ExactStrEnum):
        __module__ = "hmt_crypto.domain.private_positions"
        CROSS = "CROSS"

    class MarginedPosition(PrivateBoundaryModel):
        __module__ = "hmt_crypto.domain.private_positions"
        value: int

    margined_batch = tuple[MarginedPosition, ...]

    class LatestPosition(PrivateBoundaryModel):
        __module__ = "hmt_crypto.domain.private_positions"
        value: int

    class MmpLevel(ExactStrEnum):
        __module__ = _ORDERS
        LOW = "LOW"

    class Order(PrivateBoundaryModel):
        __module__ = _ORDERS
        value: int

    class LookupNotFound(PrivateBoundaryModel):
        __module__ = _ORDERS
        kind: str

    class Leverage(PrivateBoundaryModel):
        __module__ = _ORDERS
        value: int

    class LookupFound(api.module._ClosedPrivateGenericModel, Generic[parameter]):
        __module__ = _ORDERS

    class LookupResult(api.module._RequireClosedGenericAlias, Generic[parameter]):
        __module__ = _ORDERS

    class PrivateCursorPage(api.module._ClosedPrivateGenericModel, Generic[parameter]):
        __module__ = _ORDERS

    class StrictPrivatePaginationResult(api.module._ClosedPrivateGenericModel, Generic[parameter]):
        __module__ = _ORDERS

    class DiagnosticPrivatePaginationResult(
        api.module._ClosedPrivateGenericModel, Generic[parameter]
    ):
        __module__ = _ORDERS

    values = (
        WalletTransactionType,
        WalletTransactionMetadataValue,
        BalanceSnapshot,
        WalletTransaction,
        MarginMode,
        MarginedPosition,
        LatestPosition,
        MmpLevel,
        Order,
        LookupNotFound,
        Leverage,
        LookupFound,
        LookupResult,
        PrivateCursorPage,
        StrictPrivatePaginationResult,
        DiagnosticPrivatePaginationResult,
    )
    for value in values:
        value.__qualname__ = value.__name__
    wallet_leaf, wallet = modules[names[0]], modules[names[1]]
    positions_leaf, positions = modules[names[2]], modules[names[3]]
    order_leaf, result_leaf, orders = modules[names[4]], modules[names[6]], modules[names[7]]
    for value in (
        WalletTransactionType,
        WalletTransactionMetadataValue,
        BalanceSnapshot,
        WalletTransaction,
    ):
        setattr(wallet_leaf, value.__name__, value)
        setattr(wallet, value.__name__, value)
    for value in (MarginMode, LatestPosition):
        setattr(positions_leaf, value.__name__, value)
        setattr(positions, value.__name__, value)
    positions_leaf.MarginedPositionBatch = margined_batch
    positions.MarginedPositionBatch = margined_batch
    for value in (MmpLevel, Order):
        setattr(order_leaf, value.__name__, value)
        setattr(orders, value.__name__, value)
    for value in (
        LookupNotFound,
        Leverage,
        LookupFound,
        LookupResult,
        PrivateCursorPage,
        StrictPrivatePaginationResult,
        DiagnosticPrivatePaginationResult,
    ):
        setattr(result_leaf, value.__name__, value)
        setattr(orders, value.__name__, value)
    for owner in (wallet, positions, orders):
        for family_name in (
            "ObservedEnum",
            "ValueField",
            "Observed",
            "FieldPresence",
            "NullableField",
        ):
            setattr(owner, family_name, api.module.__dict__[family_name])
    orders.LatestPosition = LatestPosition
    orders.WalletTransaction = WalletTransaction
    try:
        for name, module in modules.items():
            sys.modules[name] = module
        yield SimpleNamespace(
            wallet=wallet,
            wallet_leaf=wallet_leaf,
            positions=positions,
            positions_leaf=positions_leaf,
            orders=orders,
            result_leaf=result_leaf,
            WalletTransactionType=WalletTransactionType,
            WalletTransactionMetadataValue=WalletTransactionMetadataValue,
            WalletTransaction=WalletTransaction,
            MarginedPosition=MarginedPosition,
            MarginedPositionBatch=margined_batch,
            LatestPosition=LatestPosition,
            MmpLevel=MmpLevel,
            LookupFound=LookupFound,
            LookupResult=LookupResult,
            PrivateCursorPage=PrivateCursorPage,
            StrictPrivatePaginationResult=StrictPrivatePaginationResult,
            DiagnosticPrivatePaginationResult=DiagnosticPrivatePaginationResult,
        )
    finally:
        for name, (present, module) in saved.items():
            if present:
                sys.modules[name] = cast(ModuleType, module)
            else:
                sys.modules.pop(name, None)


def test_future_seam_proves_all_payload_shapes_and_both_family_kinds() -> None:
    """The supplier supports ENUM/MODEL/aliases/prior rows without registration."""
    api = account_api()
    source = _source()
    _owners, rows = _owners_and_rows()
    with _future_world() as world:
        cases = (
            (
                rows[2],
                api.ObservedEnum,
                world.WalletTransactionType,
                "hmt_crypto.domain.private_wallet",
            ),
            (
                rows[3],
                api.ValueField,
                world.WalletTransactionMetadataValue,
                "hmt_crypto.domain.private_wallet",
            ),
            (
                rows[7],
                api.Observed,
                world.MarginedPositionBatch,
                "hmt_crypto.domain.private_positions",
            ),
            (rows[35], world.LookupFound, world.LatestPosition, _ORDERS),
            (rows[37], world.LookupResult, world.LatestPosition, _ORDERS),
            (rows[39], world.PrivateCursorPage, world.WalletTransaction, _ORDERS),
            (rows[42], world.StrictPrivatePaginationResult, world.WalletTransaction, _ORDERS),
            (rows[45], world.DiagnosticPrivatePaginationResult, world.WalletTransaction, _ORDERS),
        )
        assert all(source._verify_pre_table_source(*case) for case in cases)
        found = api.module._declare_reviewed_model_candidate(
            world.LookupFound,
            world.LatestPosition,
            canonical_name="LatestPositionLookupFound",
            owner_module=_ORDERS,
        )
        world.orders.LatestPositionLookupFound = found
        result = api.module._declare_reviewed_alias_candidate(
            world.LookupResult,
            world.LatestPosition,
            canonical_name="LatestPositionLookupResult",
            owner_module=_ORDERS,
        )
        world.orders.LatestPositionLookupResult = result
        observed = api.module._declare_reviewed_model_candidate(
            api.ObservedEnum,
            world.MmpLevel,
            canonical_name="ObservedMmpLevel",
            owner_module=_ORDERS,
        )
        world.orders.ObservedMmpLevel = observed
        assert source._verify_pre_table_source(rows[21], api.ValueField, observed, _ORDERS)
        assert source._verify_pre_table_source(rows[48], api.Observed, result, _ORDERS)


@pytest.mark.parametrize("attribute", ["__module__", "__name__", "__qualname__"])
def test_payload_literal_metadata_rejects_before_hostile_comparison(
    attribute: str,
) -> None:
    """Payload metadata must be exact built-in strings before comparison."""
    api = account_api()
    source = _source()
    _owners, rows = _owners_and_rows()
    calls: list[str] = []

    class HostileString(str):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("payload metadata equality ran")

        def __ne__(self, other: object) -> bool:
            del other
            calls.append("ne")
            raise AssertionError("payload metadata inequality ran")

    with _future_world() as world:
        payload = world.WalletTransactionMetadataValue
        original = type.__getattribute__(payload, attribute)
        type.__setattr__(payload, attribute, HostileString(original))
        try:
            assert not source._verify_pre_table_source(
                rows[3],
                api.ValueField,
                payload,
                "hmt_crypto.domain.private_wallet",
            )
        finally:
            type.__setattr__(payload, attribute, original)
    assert calls == []


@pytest.mark.parametrize("attribute", ["__module__", "__name__", "__qualname__"])
def test_prior_candidate_literal_metadata_rejects_before_hostile_comparison(
    attribute: str,
) -> None:
    """Prior-candidate metadata must be exact built-in strings before comparison."""
    api = account_api()
    source = _source()
    _owners, rows = _owners_and_rows()
    calls: list[str] = []

    class HostileString(str):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("candidate metadata equality ran")

        def __ne__(self, other: object) -> bool:
            del other
            calls.append("ne")
            raise AssertionError("candidate metadata inequality ran")

    with _future_world() as world:
        candidate = api.module._declare_reviewed_model_candidate(
            api.ObservedEnum,
            world.MmpLevel,
            canonical_name="ObservedMmpLevel",
            owner_module=_ORDERS,
        )
        world.orders.ObservedMmpLevel = candidate
        original = type.__getattribute__(candidate, attribute)
        type.__setattr__(candidate, attribute, HostileString(original))
        try:
            assert not source._verify_pre_table_source(rows[21], api.ValueField, candidate, _ORDERS)
        finally:
            type.__setattr__(candidate, attribute, original)
    assert calls == []


def test_missing_branch_and_candidate_tag_replacement_cannot_replace_source() -> None:
    """The MissingField construction leaf remains authority after a coordinated edit."""
    api = account_api()
    source = _source()
    _owners, rows = _owners_and_rows()
    with _future_world() as world:
        value = api.module._declare_reviewed_model_candidate(
            api.ValueField,
            world.WalletTransactionMetadataValue,
            canonical_name="WalletTransactionMetadataValueField",
            owner_module="hmt_crypto.domain.private_wallet",
        )
        world.wallet.WalletTransactionMetadataValueField = value
        presence = api.module._declare_reviewed_alias_candidate(
            api.FieldPresence,
            world.WalletTransactionMetadataValue,
            canonical_name="WalletTransactionMetadataPresence",
            owner_module="hmt_crypto.domain.private_wallet",
        )
        world.wallet.WalletTransactionMetadataPresence = presence
        original_tags = presence.__private_tags__

        class ForgedMissing(api.MissingField):
            extra: str

        with _restore_bindings(api.module, ("MissingField",)):
            api.module.MissingField = ForgedMissing
            presence.__private_tags__ = (("MISSING", ForgedMissing), *original_tags[1:])
            try:
                assert (
                    source._source_alias_dependencies(
                        rows[4],
                        world.WalletTransactionMetadataValue,
                        "hmt_crypto.domain.private_wallet",
                    )
                    is None
                )
            finally:
                presence.__private_tags__ = original_tags


def test_cross_owner_payload_requires_source_public_and_distinct_consumer_identity() -> None:
    """Source-plus-public replacement cannot bypass an unchanged orders consumer."""
    from hmt_crypto.domain.private_primitives import PrivateBoundaryModel

    source = _source()
    _owners, rows = _owners_and_rows()
    with _future_world() as world:
        assert source._verify_pre_table_source(
            rows[39], world.PrivateCursorPage, world.WalletTransaction, _ORDERS
        )
        with _restore_bindings(world.orders, ("WalletTransaction",)):
            world.orders.WalletTransaction = object()
            assert not source._verify_pre_table_source(
                rows[39], world.PrivateCursorPage, world.WalletTransaction, _ORDERS
            )

        class WalletTransaction(PrivateBoundaryModel):
            __module__ = "hmt_crypto.domain.private_wallet"
            value: int

        WalletTransaction.__qualname__ = "WalletTransaction"
        with (
            _restore_bindings(world.wallet_leaf, ("WalletTransaction",)),
            _restore_bindings(world.wallet, ("WalletTransaction",)),
        ):
            world.wallet_leaf.WalletTransaction = WalletTransaction
            world.wallet.WalletTransaction = WalletTransaction
            assert not source._verify_pre_table_source(
                rows[39], world.PrivateCursorPage, WalletTransaction, _ORDERS
            )


def test_reconstructed_annotated_and_generic_aliases_reject_by_identity() -> None:
    """Outward alias equality never substitutes for the accepted source object."""
    api = account_api()
    source = _source()
    _owners, rows = _owners_and_rows()
    origin, *metadata = get_args(api.OpaqueCursor)
    reconstructed_annotated = type(api.OpaqueCursor)(origin, tuple(metadata))
    assert reconstructed_annotated == api.OpaqueCursor
    assert reconstructed_annotated is not api.OpaqueCursor
    assert not source._verify_pre_table_source(
        rows[0], api.ValueField, reconstructed_annotated, _ACCOUNT
    )
    with _future_world() as world:
        reconstructed_tuple = tuple[world.MarginedPosition, ...]
        assert reconstructed_tuple == world.MarginedPositionBatch
        assert reconstructed_tuple is not world.MarginedPositionBatch
        assert not source._verify_pre_table_source(
            rows[7], api.Observed, reconstructed_tuple, "hmt_crypto.domain.private_positions"
        )


def test_wrong_future_family_shape_rejects_even_when_all_three_names_match() -> None:
    """A same-name Pydantic generic without the guarded base is not a family source."""
    from hmt_crypto.domain.private_primitives import PrivateBoundaryModel

    source = _source()
    _owners, rows = _owners_and_rows()
    parameter = TypeVar("parameter")
    with _future_world() as world:

        class LookupFound(PrivateBoundaryModel, Generic[parameter]):
            __module__ = _ORDERS

        LookupFound.__qualname__ = "LookupFound"
        for module in (world.result_leaf, world.orders):
            module.LookupFound = LookupFound
        assert not source._verify_pre_table_source(
            rows[35], LookupFound, world.LatestPosition, _ORDERS
        )


@pytest.mark.parametrize("module_role", _SNAPSHOT_MODULE_ROLES)
def test_wrong_snapshotted_module_name_rejects_even_if_live_name_recovers(
    module_role: str,
) -> None:
    """Module identity is decided by the owned namespace, not later live state."""
    source = _source()
    _HostileModuleName.callbacks = 0
    with _future_world() as world:
        module = getattr(world, module_role)
        namespace = module.__dict__
        expected_name = dict.__getitem__(namespace, "__name__")
        wrong_name = _HostileModuleName("hmt_crypto.domain.wrong_snapshot")
        dict.__setitem__(namespace, "__name__", wrong_name)
        try:
            with _mutate_after_snapshot(
                source._exact_string_dict,
                namespace,
                lambda: dict.__setitem__(namespace, "__name__", expected_name),
            ):
                assert source._snapshots((expected_name,)) is None
        finally:
            dict.__setitem__(namespace, "__name__", expected_name)
    assert _HostileModuleName.callbacks == 0


@pytest.mark.parametrize("module_role", _SNAPSHOT_MODULE_ROLES)
def test_good_snapshotted_module_name_survives_later_live_corruption(
    module_role: str,
) -> None:
    """A post-copy live-name mutation cannot invalidate the stable namespace."""
    source = _source()
    _HostileModuleName.callbacks = 0
    with _future_world() as world:
        module = getattr(world, module_role)
        namespace = module.__dict__
        expected_name = dict.__getitem__(namespace, "__name__")
        corrupt_name = _HostileModuleName("hmt_crypto.domain.corrupt_live")
        try:
            with _mutate_after_snapshot(
                source._exact_string_dict,
                namespace,
                lambda: dict.__setitem__(namespace, "__name__", corrupt_name),
            ):
                snapshots = source._snapshots((expected_name,))
            assert snapshots is not None
            assert source._snapshots((expected_name,)) is None
            assert dict.__getitem__(namespace, "__name__") is corrupt_name
            owned = dict.__getitem__(snapshots, expected_name)
            assert type(owned) is dict
            assert dict.__getitem__(owned, "__name__") == expected_name
        finally:
            dict.__setitem__(namespace, "__name__", expected_name)
    assert _HostileModuleName.callbacks == 0


def test_each_distinct_module_snapshot_is_owned_for_the_complete_proof() -> None:
    """Live source, public, or consumer mutation cannot change an in-flight proof."""
    source = _source()
    _owners, rows = _owners_and_rows()
    with _future_world() as world:
        for module in (world.wallet_leaf, world.wallet, world.orders):
            original = module.WalletTransaction
            try:
                with _mutate_after_snapshot(
                    source._exact_string_dict,
                    module.__dict__,
                    lambda module=module: dict.__setitem__(
                        module.__dict__, "WalletTransaction", None
                    ),
                ):
                    assert source._verify_pre_table_source(
                        rows[39], world.PrivateCursorPage, world.WalletTransaction, _ORDERS
                    )
            finally:
                module.WalletTransaction = original


def test_verifier_and_declaration_failures_retain_no_raw_inputs() -> None:
    """A rejected proof or declaration leaves no raw object in persistent state."""
    api = account_api()
    source = _source()
    _owners, rows = _owners_and_rows()

    class Probe:
        pass

    family = Probe()
    payload = Probe()
    family_ref = weakref.ref(family)
    payload_ref = weakref.ref(payload)
    assert not source._verify_pre_table_source(rows[0], family, payload, _ACCOUNT)
    with pytest.raises(TypeError, match="invalid reviewed generic model declaration"):
        api.module._declare_reviewed_model_candidate(
            family,
            payload,
            canonical_name="OpaqueCursorValueField",
            owner_module=_ACCOUNT,
        )
    del family, payload
    gc.collect()
    assert family_ref() is None
    assert payload_ref() is None
