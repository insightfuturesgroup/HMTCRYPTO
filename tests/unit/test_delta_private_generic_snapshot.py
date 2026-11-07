"""Stable-snapshot and bound-sentinel regressions for the A2.1 registry."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Generic, TypeVar, cast

import pytest
from delta_private_model_test_support import account_api
from pydantic import TypeAdapter

pytestmark = pytest.mark.unit


@contextmanager
def _mutate_when_exact_dict_returns(
    helper: Callable[[object], object],
    source: object,
    mutation: Callable[[], None],
) -> Iterator[None]:
    """Mutate the live source after the helper has selected its return value."""
    previous = sys.gettrace()
    armed = True

    def trace(frame: object, event: str, arg: object) -> object:
        del arg
        nonlocal armed
        code = object.__getattribute__(frame, "f_code")
        locals_ = object.__getattribute__(frame, "f_locals")
        if armed and event == "return" and code is helper.__code__ and locals_["value"] is source:
            armed = False
            mutation()
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)


def test_exact_tagged_union_uses_snapshot_when_live_state_mutates_after_scan() -> None:
    """A post-scan hostile mutation cannot affect validation's owned branch state."""
    api = account_api()
    core = importlib.import_module("hmt_crypto.domain._private_generic_core")
    adapter = TypeAdapter(api.NullableOpaqueCursor)
    source: dict[object, object] = {"kind": "NULL"}
    calls: list[str] = []
    armed = False

    class HostileKey(str):
        def __hash__(self) -> int:
            return hash("kind")

        def __eq__(self, other: object) -> bool:
            del other
            if armed:
                calls.append("eq")
                raise AssertionError("live branch-state key reached lookup")
            return False

    hostile = HostileKey("hostile-kind")

    def mutate() -> None:
        nonlocal armed
        dict.clear(source)
        dict.__setitem__(source, hostile, "NULL")
        armed = True

    try:
        with _mutate_when_exact_dict_returns(core._exact_string_dict, source, mutate):
            result = adapter.validate_python(source)
        assert type(result) is api.NullField
        assert calls == []
    finally:
        armed = False
        dict.clear(source)
        dict.__setitem__(source, "kind", "NULL")


def test_registry_readers_use_only_owned_snapshots_after_live_mutation() -> None:
    """sys.modules, module namespaces, and metadata are stable after snapshot."""
    api = account_api()
    account = api.module
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    source_verifier = importlib.import_module("hmt_crypto.domain._private_generic_source")
    module_name = "hmt_crypto.domain.account"

    original_module = sys.modules[module_name]
    try:
        with _mutate_when_exact_dict_returns(
            source_verifier._exact_string_dict,
            sys.modules,
            lambda: dict.__setitem__(sys.modules, module_name, None),
        ):
            namespace = source_verifier._module_namespace(module_name)
    finally:
        dict.__setitem__(sys.modules, module_name, original_module)
    assert namespace is not None
    assert namespace["ValueField"] is api.ValueField

    table_name = "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS"
    original_table = account.__dict__[table_name]
    try:
        with _mutate_when_exact_dict_returns(
            source_verifier._exact_string_dict,
            account.__dict__,
            lambda: dict.__delitem__(account.__dict__, table_name),
        ):
            namespace = source_verifier._module_namespace(module_name)
        assert namespace is not None
        assert namespace[table_name] is original_table
    finally:
        account.__dict__[table_name] = original_table

    metadata = api.OpaqueCursorValueField.__pydantic_generic_metadata__
    original_origin = metadata["origin"]
    owner = importlib.import_module(
        "hmt_crypto.domain._private_generic_manifest"
    )._reviewed_vocabulary_for_verification()[0]
    owner_namespace = registry._module_namespace(module_name)
    assert owner_namespace is not None
    entry = (
        owner,
        owner.rows[0],
        api.ValueField,
        api.OpaqueCursor,
        api.OpaqueCursorValueField,
        owner_namespace,
    )
    try:
        with _mutate_when_exact_dict_returns(
            registry._exact_string_dict,
            metadata,
            lambda: dict.__setitem__(metadata, "origin", object()),
        ):
            assert registry._model_matches(entry)
    finally:
        metadata["origin"] = original_origin
    assert (
        registry._find_reviewed_specialization(api.ValueField, api.OpaqueCursor)
        is api.OpaqueCursorValueField
    )
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField


def test_exact_dict_reader_cannot_leak_size_change_runtime_error() -> None:
    """A deterministic concurrent-style mutation never escapes as RuntimeError."""
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    source: dict[str, object] = {str(index): index for index in range(32)}
    previous = sys.gettrace()
    mutated = False

    def trace(frame: object, event: str, arg: object) -> object:
        del arg
        nonlocal mutated
        code = object.__getattribute__(frame, "f_code")
        back = object.__getattribute__(frame, "f_back")
        if (
            not mutated
            and event == "line"
            and code.co_name == "<genexpr>"
            and back is not None
            and object.__getattribute__(back, "f_code") is registry._exact_string_dict.__code__
        ):
            mutated = True
            dict.__setitem__(source, "concurrent", True)
        return trace

    sys.settrace(trace)
    try:
        owned = registry._exact_string_dict(source)
    finally:
        sys.settrace(previous)
    assert mutated is True
    assert owned is not None
    assert "concurrent" not in owned


@contextmanager
def _restore_bindings(module: ModuleType, names: tuple[str, ...]) -> Iterator[None]:
    saved = {
        name: (dict.__contains__(module.__dict__, name), module.__dict__.get(name))
        for name in names
    }
    try:
        yield
    finally:
        for name, (present, value) in saved.items():
            if present:
                module.__dict__[name] = value
            else:
                module.__dict__.pop(name, None)


@pytest.mark.parametrize("kind", ["model", "alias"])
@pytest.mark.parametrize("slot", ["family", "payload", "name", "table"])
def test_bound_registry_sentinel_is_never_declaration_absence(
    kind: str,
    slot: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every sentinel-bound declaration slot is present and malformed."""
    api = account_api()
    account = api.module
    core = importlib.import_module("hmt_crypto.domain._private_generic_core")
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    sentinel = registry._ABSENT
    table_name = "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS"
    if kind == "model":
        family_name, payload_name = "ValueField", "OpaqueCursor"
        candidate_name = "OpaqueCursorValueField"
        declaration = account._declare_reviewed_model_candidate
        message = "invalid reviewed generic model declaration"
    else:
        family_name, payload_name = "NullableField", "OpaqueCursor"
        candidate_name = "NullableOpaqueCursor"
        declaration = account._declare_reviewed_alias_candidate
        message = "invalid reviewed generic alias declaration"
    original_family = account.__dict__[family_name]
    original_payload = account.__dict__[payload_name]
    original_candidate = account.__dict__[candidate_name]
    original_table = account.__dict__[table_name]
    calls: list[str] = []

    def forbid_builder(*args: object, **kwargs: object) -> object:
        del args, kwargs
        calls.append("builder")
        raise AssertionError("declaration builder ran")

    monkeypatch.setattr(core, "prepare_class", forbid_builder)
    monkeypatch.setattr(core, "_source_alias_dependencies", forbid_builder)
    names = (family_name, payload_name, candidate_name, table_name)
    with _restore_bindings(account, names):
        bound_name = {
            "family": family_name,
            "payload": payload_name,
            "name": candidate_name,
            "table": table_name,
        }[slot]
        account.__dict__[bound_name] = sentinel
        assert registry._load_reviewed_rows() is None
        account.__dict__[bound_name] = {
            "family": original_family,
            "payload": original_payload,
            "name": original_candidate,
            "table": original_table,
        }[slot]
        account.__dict__.pop(candidate_name, None)
        account.__dict__.pop(table_name, None)
        family: object = original_family
        payload: object = original_payload
        if slot == "family":
            account.__dict__[family_name] = sentinel
            family = sentinel
            assert (
                registry._match_declaration(kind, family, payload, candidate_name, account.__name__)
                is None
            )
        elif slot == "payload":
            account.__dict__[payload_name] = sentinel
            payload = sentinel
        elif slot == "name":
            account.__dict__[candidate_name] = sentinel
        else:
            account.__dict__[table_name] = sentinel
        with pytest.raises(TypeError, match=message):
            declaration(
                family,
                payload,
                canonical_name=candidate_name,
                owner_module=account.__name__,
            )
    assert calls == []
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField
    assert api.NullableField[api.OpaqueCursor] is api.NullableOpaqueCursor


def test_later_owner_sentinel_table_rejects_before_every_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present sentinel table cannot open the future-owner declaration seam."""
    from hmt_crypto.domain.private_primitives import PrivateBoundaryModel

    api = account_api()
    account = api.module
    core = importlib.import_module("hmt_crypto.domain._private_generic_core")
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    module_name = "hmt_crypto.domain.private_orders"
    parameter = TypeVar("parameter")
    fake = ModuleType(module_name)
    previous_present = dict.__contains__(sys.modules, module_name)
    previous = dict.get(sys.modules, module_name)
    calls: list[str] = []

    class LookupFound(account._ClosedPrivateGenericModel, Generic[parameter]):
        __module__ = module_name

    class LookupResult(account._RequireClosedGenericAlias, Generic[parameter]):
        __module__ = module_name

    class LookupNotFound(PrivateBoundaryModel):
        __module__ = module_name
        kind: str

    class LatestPosition(PrivateBoundaryModel):
        __module__ = module_name
        product_id: int

    for value in (LookupFound, LookupResult, LookupNotFound, LatestPosition):
        setattr(fake, value.__name__, value)
    fake._ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS = registry._ABSENT

    def forbid_builder(*args: object, **kwargs: object) -> object:
        del args, kwargs
        calls.append("builder")
        raise AssertionError("future declaration builder ran")

    monkeypatch.setattr(core, "prepare_class", forbid_builder)
    monkeypatch.setattr(core, "_source_alias_dependencies", forbid_builder)
    try:
        sys.modules[module_name] = fake
        with pytest.raises(TypeError, match="invalid reviewed generic model declaration"):
            account._declare_reviewed_model_candidate(
                LookupFound,
                LatestPosition,
                canonical_name="LatestPositionLookupFound",
                owner_module=module_name,
            )
        with pytest.raises(TypeError, match="invalid reviewed generic alias declaration"):
            account._declare_reviewed_alias_candidate(
                LookupResult,
                LatestPosition,
                canonical_name="LatestPositionLookupResult",
                owner_module=module_name,
            )
    finally:
        if previous_present:
            sys.modules[module_name] = cast(ModuleType, previous)
        else:
            sys.modules.pop(module_name, None)
    assert calls == []
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField


def test_bound_sentinel_branch_dependency_is_malformed_not_authority() -> None:
    """A self-consistent alias cannot turn a sentinel branch into authority."""
    api = account_api()
    account = api.module
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    sentinel = registry._ABSENT
    marker = api.ValueField[int]
    original_null = account.NullField
    original_tags = api.NullableOpaqueCursor.__private_tags__
    try:
        account.NullField = sentinel
        api.NullableOpaqueCursor.__private_tags__ = (
            ("NULL", sentinel),
            original_tags[1],
        )
        assert registry._load_reviewed_rows() is None
        assert api.NullableField[api.OpaqueCursor] is marker
    finally:
        account.NullField = original_null
        api.NullableOpaqueCursor.__private_tags__ = original_tags
    assert api.NullableField[api.OpaqueCursor] is api.NullableOpaqueCursor
