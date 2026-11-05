"""Regression tests for the A2.1 exact-boundary review HOLD."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from copy import copy, deepcopy
from typing import cast

import pytest
from delta_private_model_test_support import account_api, assert_safe_generic_error
from pydantic import TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError

pytestmark = pytest.mark.unit


def test_open_alias_origins_reject_construction_before_subclass_new() -> None:
    """An alias origin cannot be instantiated, even through a hostile subclass."""
    api = account_api()
    calls: list[str] = []
    canary = "fixture-secret-open-alias"

    class Bypass(api.FieldPresence):
        def __new__(cls, *args: object, **kwargs: object) -> Bypass:
            del args, kwargs
            calls.append("new")
            return object.__new__(cls)

        def __init__(self, *args: object, **kwargs: object) -> None:
            del self, args, kwargs
            calls.append("init")

    for origin in (
        api.module._RequireClosedGenericAlias,
        api.FieldPresence,
        api.NullableField,
        Bypass,
    ):
        with pytest.raises(ValidationError) as captured:
            origin(canary)
        assert_safe_generic_error(captured.value, canary)
    assert calls == []


def test_declaration_helpers_enforce_owner_lifecycle_before_builders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only absent-name/table construction or exact-table reuse is admitted."""
    api = account_api()
    account = api.module
    core = importlib.import_module("hmt_crypto.domain._private_generic_core")
    builder_calls: list[str] = []

    def forbid_builder(*args: object, **kwargs: object) -> object:
        del args, kwargs
        builder_calls.append("builder")
        raise AssertionError("candidate builder ran")

    monkeypatch.setattr(core, "prepare_class", forbid_builder)
    assert (
        account._declare_reviewed_model_candidate(
            api.ValueField,
            api.OpaqueCursor,
            canonical_name="OpaqueCursorValueField",
            owner_module="hmt_crypto.domain.account",
        )
        is api.OpaqueCursorValueField
    )
    monkeypatch.setattr(core, "_source_alias_dependencies", forbid_builder)
    assert (
        account._declare_reviewed_alias_candidate(
            api.NullableField,
            api.OpaqueCursor,
            canonical_name="NullableOpaqueCursor",
            owner_module="hmt_crypto.domain.account",
        )
        is api.NullableOpaqueCursor
    )
    assert builder_calls == []

    table_name = "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS"
    original_table = account.__dict__[table_name]
    original_model = account.__dict__["OpaqueCursorValueField"]
    variants = (
        (object(), original_table),
        (original_model, None),
        (original_model, [*original_table]),
        (original_model, original_table[:1]),
        (original_model, tuple(reversed(original_table))),
    )
    for bound_name, bound_table in variants:
        account.OpaqueCursorValueField = bound_name
        if bound_table is None:
            account.__dict__.pop(table_name, None)
        else:
            account.__dict__[table_name] = bound_table
        try:
            with pytest.raises(TypeError, match="invalid reviewed generic model declaration"):
                account._declare_reviewed_model_candidate(
                    api.ValueField,
                    api.OpaqueCursor,
                    canonical_name="OpaqueCursorValueField",
                    owner_module="hmt_crypto.domain.account",
                )
        finally:
            account.OpaqueCursorValueField = original_model
            account.__dict__[table_name] = original_table

    account.__dict__.pop("OpaqueCursorValueField")
    try:
        with pytest.raises(TypeError, match="invalid reviewed generic model declaration"):
            account._declare_reviewed_model_candidate(
                api.ValueField,
                api.OpaqueCursor,
                canonical_name="OpaqueCursorValueField",
                owner_module="hmt_crypto.domain.account",
            )
    finally:
        account.OpaqueCursorValueField = original_model

    original_alias = account.__dict__["NullableOpaqueCursor"]
    alias_variants = (
        (object(), original_table),
        (original_alias, None),
        (original_alias, [*original_table]),
        (original_alias, original_table[:1]),
        (original_alias, tuple(reversed(original_table))),
    )
    for bound_name, bound_table in alias_variants:
        account.NullableOpaqueCursor = bound_name
        if bound_table is None:
            account.__dict__.pop(table_name, None)
        else:
            account.__dict__[table_name] = bound_table
        try:
            with pytest.raises(TypeError, match="invalid reviewed generic alias declaration"):
                account._declare_reviewed_alias_candidate(
                    api.NullableField,
                    api.OpaqueCursor,
                    canonical_name="NullableOpaqueCursor",
                    owner_module="hmt_crypto.domain.account",
                )
        finally:
            account.NullableOpaqueCursor = original_alias
            account.__dict__[table_name] = original_table

    account.__dict__.pop("NullableOpaqueCursor")
    try:
        with pytest.raises(TypeError, match="invalid reviewed generic alias declaration"):
            account._declare_reviewed_alias_candidate(
                api.NullableField,
                api.OpaqueCursor,
                canonical_name="NullableOpaqueCursor",
                owner_module="hmt_crypto.domain.account",
            )
    finally:
        account.NullableOpaqueCursor = original_alias
    assert builder_calls == []


def test_exact_tagged_union_rejects_hostile_branch_state_before_lookup() -> None:
    """Validation, dump, and copy reject a hostile exact-dict key callback-free."""
    api = account_api()
    adapter = TypeAdapter(api.NullableOpaqueCursor)
    calls: list[str] = []
    armed = False

    class HostileKey(str):
        state: dict[object, object]

        def __hash__(self) -> int:
            return hash("kind")

        def __eq__(self, other: object) -> bool:
            del other
            if armed:
                calls.append("eq")
                dict.__setitem__(self.state, "mutated", True)
                raise AssertionError("hostile branch-state equality ran")
            return False

        def __ne__(self, other: object) -> bool:
            del other
            if armed:
                calls.append("ne")
                raise AssertionError("hostile branch-state inequality ran")
            return True

    def corrupt() -> object:
        value = api.NullField(kind="NULL")
        key = HostileKey("hostile")
        state: dict[object, object] = {key: None, "kind": "NULL"}
        key.state = state
        object.__setattr__(value, "__dict__", state)
        return value

    values = tuple(corrupt() for _ in range(4))
    armed = True
    with pytest.raises(ValidationError):
        adapter.validate_python(values[0])
    with pytest.raises(PydanticSerializationError):
        adapter.dump_python(values[1])
    for operation, value in zip((copy, deepcopy), values[2:], strict=True):
        with pytest.raises(ValidationError):
            operation(value)
    assert calls == []


def test_hostile_exact_dict_keys_fail_closed_without_callbacks_or_poison() -> None:
    """sys.modules, owner namespaces, and model metadata are scanned before lookup."""
    api = account_api()
    account = api.module
    core = importlib.import_module("hmt_crypto.domain._private_generic_core")
    marker = api.ValueField[int]
    calls: list[str] = []
    armed = False

    class HostileKey(str):
        target: str

        def __new__(cls, value: str, target: str) -> HostileKey:
            instance = super().__new__(cls, value)
            instance.target = target
            return instance

        def __hash__(self) -> int:
            return hash(self.target)

        def __eq__(self, other: object) -> bool:
            del other
            if armed:
                calls.append("eq")
                raise AssertionError("hostile dictionary-key equality ran")
            return False

        def __ne__(self, other: object) -> bool:
            del other
            if armed:
                calls.append("ne")
                raise AssertionError("hostile dictionary-key inequality ran")
            return True

    @contextmanager
    def hostile_collision(mapping: dict[object, object], target: str) -> Iterator[None]:
        original = dict.__getitem__(mapping, target)
        dict.__delitem__(mapping, target)
        key = HostileKey(f"hostile-{target}", target)
        dict.__setitem__(mapping, key, None)
        dict.__setitem__(mapping, target, original)
        try:
            yield
        finally:
            nonlocal armed
            armed = False
            dict.__delitem__(mapping, target)
            dict.__delitem__(mapping, key)
            dict.__setitem__(mapping, target, original)

    module_name = "hmt_crypto.domain.account"
    with hostile_collision(cast(dict[object, object], sys.modules), module_name):
        armed = True
        result = api.ValueField[api.OpaqueCursor]
    assert result is marker
    assert calls == []
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField

    table_name = "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS"
    armed = False
    with hostile_collision(cast(dict[object, object], account.__dict__), table_name):
        armed = True
        result = api.ValueField[api.OpaqueCursor]
        assert core._owner_namespace(module_name) is None
    assert result is marker
    assert calls == []
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField

    metadata = api.OpaqueCursorValueField.__pydantic_generic_metadata__
    armed = False
    with hostile_collision(cast(dict[object, object], metadata), "origin"):
        armed = True
        result = api.ValueField[api.OpaqueCursor]
    assert result is marker
    assert calls == []
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField
