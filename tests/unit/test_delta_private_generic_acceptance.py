"""Final acceptance regressions for the closed A2.1 generic boundary."""

from __future__ import annotations

import gc
import importlib
import sys
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Generic, TypeVar, cast

import pytest
from delta_private_model_test_support import account_api, assert_safe_generic_error
from pydantic import TypeAdapter, ValidationError

pytestmark = pytest.mark.unit


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


def test_declaration_resolution_exposes_explicit_fresh_and_reuse_states() -> None:
    """Fresh construction is never encoded as a possibly-bound ``None`` value."""
    api = account_api()
    account = api.module
    registry = importlib.import_module("hmt_crypto.domain._private_generic_registry")
    table_name = "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS"
    for kind, family, candidate_name in (
        ("model", api.ValueField, "OpaqueCursorValueField"),
        ("alias", api.NullableField, "NullableOpaqueCursor"),
    ):
        with _restore_bindings(account, (candidate_name, table_name)):
            account.__dict__.pop(candidate_name)
            account.__dict__.pop(table_name)
            fresh = registry._resolve_declaration(
                kind, family, api.OpaqueCursor, candidate_name, account.__name__
            )
            assert fresh is not None
            assert fresh.reuse is False
            assert fresh.candidate is None
        reuse = registry._resolve_declaration(
            kind, family, api.OpaqueCursor, candidate_name, account.__name__
        )
        assert reuse is not None
        assert reuse.reuse is True
        assert reuse.candidate is account.__dict__[candidate_name]


@pytest.mark.parametrize("bound", [None, False, 0, "", object()])
@pytest.mark.parametrize("kind", ["model", "alias"])
def test_present_falsy_canonical_name_rejects_before_builder_without_poison(
    bound: object,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = account_api()
    account = api.module
    core = importlib.import_module("hmt_crypto.domain._private_generic_core")
    table_name = "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS"
    if kind == "model":
        family = api.ValueField
        candidate_name = "OpaqueCursorValueField"
        declaration = account._declare_reviewed_model_candidate
    else:
        family = api.NullableField
        candidate_name = "NullableOpaqueCursor"
        declaration = account._declare_reviewed_alias_candidate
    calls: list[str] = []

    def forbid(*args: object, **kwargs: object) -> object:
        del args, kwargs
        calls.append("builder")
        raise AssertionError("builder ran")

    monkeypatch.setattr(core, "prepare_class", forbid)
    monkeypatch.setattr(core, "_source_alias_dependencies", forbid)
    with _restore_bindings(account, (candidate_name, table_name)):
        account.__dict__[candidate_name] = bound
        account.__dict__.pop(table_name)
        with pytest.raises(TypeError, match=f"invalid reviewed generic {kind} declaration"):
            declaration(
                family,
                api.OpaqueCursor,
                canonical_name=candidate_name,
                owner_module=account.__name__,
            )
    assert calls == []
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField
    assert api.NullableField[api.OpaqueCursor] is api.NullableOpaqueCursor


@pytest.mark.parametrize("slot", ["family", "payload"])
@pytest.mark.parametrize("bound", [None, False, 0, "", "canary-private-input", object()])
@pytest.mark.parametrize("kind", ["model", "alias"])
def test_arbitrary_bound_declaration_inputs_never_reach_builders(
    slot: str,
    bound: object,
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = account_api()
    account = api.module
    core = importlib.import_module("hmt_crypto.domain._private_generic_core")
    table_name = "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS"
    if kind == "model":
        family_name, candidate_name = "ValueField", "OpaqueCursorValueField"
        declaration = account._declare_reviewed_model_candidate
    else:
        family_name, candidate_name = "NullableField", "NullableOpaqueCursor"
        declaration = account._declare_reviewed_alias_candidate
    family: object = account.__dict__[family_name]
    payload: object = api.OpaqueCursor
    calls: list[str] = []

    def forbid(*args: object, **kwargs: object) -> object:
        del args, kwargs
        calls.append("builder")
        raise AssertionError("builder ran")

    monkeypatch.setattr(core, "prepare_class", forbid)
    monkeypatch.setattr(core, "_source_alias_dependencies", forbid)
    names = (family_name, "OpaqueCursor", candidate_name, table_name)
    with _restore_bindings(account, names):
        account.__dict__.pop(candidate_name)
        account.__dict__.pop(table_name)
        if slot == "family":
            account.__dict__[family_name] = bound
            family = bound
        else:
            account.__dict__["OpaqueCursor"] = bound
            payload = bound
        with pytest.raises(TypeError) as captured:
            declaration(
                family,
                payload,
                canonical_name=candidate_name,
                owner_module=account.__name__,
            )
        assert str(captured.value) == f"invalid reviewed generic {kind} declaration"
        assert "canary-private-input" not in repr(captured.value.args)
    assert calls == []
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField


def test_forged_account_families_reject_before_model_or_alias_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = account_api()
    account = api.module
    core = importlib.import_module("hmt_crypto.domain._private_generic_core")
    table_name = "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS"
    calls: list[str] = []

    class ValueField(api.ValueField):
        pass

    class NullableField(api.NullableField):
        pass

    for forged, name in ((ValueField, "ValueField"), (NullableField, "NullableField")):
        forged.__module__ = account.__name__
        forged.__qualname__ = name

    def forbid(*args: object, **kwargs: object) -> object:
        del args, kwargs
        calls.append("builder")
        raise AssertionError("builder ran")

    monkeypatch.setattr(core, "prepare_class", forbid)
    monkeypatch.setattr(core, "_source_alias_dependencies", forbid)
    cases = (
        (ValueField, "ValueField", "OpaqueCursorValueField", "model"),
        (NullableField, "NullableField", "NullableOpaqueCursor", "alias"),
    )
    for forged, family_name, candidate_name, kind in cases:
        declaration = (
            account._declare_reviewed_model_candidate
            if kind == "model"
            else account._declare_reviewed_alias_candidate
        )
        with _restore_bindings(account, (family_name, candidate_name, table_name)):
            account.__dict__[family_name] = forged
            account.__dict__.pop(candidate_name)
            account.__dict__.pop(table_name)
            with pytest.raises(TypeError, match=f"invalid reviewed generic {kind} declaration"):
                declaration(
                    forged,
                    api.OpaqueCursor,
                    canonical_name=candidate_name,
                    owner_module=account.__name__,
                )
    assert calls == []


def test_future_owner_family_requires_guarded_boundary_mro_before_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hmt_crypto.domain.private_primitives import PrivateBoundaryModel

    api = account_api()
    account = api.module
    core = importlib.import_module("hmt_crypto.domain._private_generic_core")
    module_name = "hmt_crypto.domain.private_orders"
    previous = sys.modules.get(module_name)
    previous_present = module_name in sys.modules
    fake = ModuleType(module_name)
    parameter = TypeVar("parameter")
    calls: list[str] = []

    class LookupFound(PrivateBoundaryModel, Generic[parameter]):
        __module__ = module_name

    class LookupResult(metaclass=type(api.FieldPresence)):
        __module__ = module_name

    class LatestPosition(PrivateBoundaryModel):
        __module__ = module_name
        product_id: int

    for value in (LookupFound, LookupResult, LatestPosition):
        value.__qualname__ = value.__name__
        setattr(fake, value.__name__, value)

    def forbid(*args: object, **kwargs: object) -> object:
        del args, kwargs
        calls.append("builder")
        raise AssertionError("builder ran")

    monkeypatch.setattr(core, "prepare_class", forbid)
    monkeypatch.setattr(core, "_source_alias_dependencies", forbid)
    try:
        sys.modules[module_name] = fake
        for family, candidate_name, kind in (
            (LookupFound, "LatestPositionLookupFound", "model"),
            (LookupResult, "LatestPositionLookupResult", "alias"),
        ):
            declaration = (
                account._declare_reviewed_model_candidate
                if kind == "model"
                else account._declare_reviewed_alias_candidate
            )
            with pytest.raises(TypeError, match=f"invalid reviewed generic {kind} declaration"):
                declaration(
                    family,
                    LatestPosition,
                    canonical_name=candidate_name,
                    owner_module=module_name,
                )
    finally:
        if previous_present:
            sys.modules[module_name] = cast(ModuleType, previous)
        else:
            sys.modules.pop(module_name, None)
    assert calls == []


def test_coordinated_null_branch_and_alias_tag_replacement_cannot_widen_schema() -> None:
    api = account_api()
    account = api.module
    marker = api.ValueField[int]
    original_null = account.NullField
    original_tags = api.NullableOpaqueCursor.__private_tags__

    class ForgedNull(api.NullField):
        extra: str

    try:
        account.NullField = ForgedNull
        api.NullableOpaqueCursor.__private_tags__ = (
            ("NULL", ForgedNull),
            original_tags[1],
        )
        assert api.NullableField[api.OpaqueCursor] is marker
        with pytest.raises(ValidationError, match="private generic specialization unavailable"):
            TypeAdapter(api.NullableOpaqueCursor).validate_python(
                {"kind": "NULL", "extra": "widened"}
            )
    finally:
        account.NullField = original_null
        api.NullableOpaqueCursor.__private_tags__ = original_tags
    assert api.NullableField[api.OpaqueCursor] is api.NullableOpaqueCursor
    with pytest.raises(ValidationError):
        TypeAdapter(api.NullableOpaqueCursor).validate_python(
            {"kind": "NULL", "extra": "still-forbidden"}
        )


def _core_boundary_locals(
    error: BaseException, function_name: str
) -> tuple[dict[str, object], ...]:
    found: list[dict[str, object]] = []
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_name == function_name and frame.f_code.co_filename.endswith(
            "/domain/_private_generic_core.py"
        ):
            found.append(dict(frame.f_locals))
        traceback = traceback.tb_next
    return tuple(found)


def test_alias_call_boundary_discards_class_args_and_kwargs_before_error() -> None:
    api = account_api()
    canary = "canary-alias-boundary"

    class Probe:
        pass

    class EphemeralAlias(api.FieldPresence):
        pass

    ephemeral_ref = weakref.ref(EphemeralAlias)
    origins = (
        api.module._RequireClosedGenericAlias,
        api.FieldPresence,
        api.NullableField,
        EphemeralAlias,
    )
    for origin in origins:
        probe = Probe()
        probe_ref = weakref.ref(probe)
        caught: ValidationError | None = None
        try:
            origin(probe, canary=probe)
        except ValidationError as error:
            assert_safe_generic_error(error, canary)
            caught = error
        else:
            raise AssertionError("open alias unexpectedly constructed")
        assert caught is not None
        frames = _core_boundary_locals(caught, "__call__")
        assert frames and all(canary not in repr(locals_) for locals_ in frames)
        assert all(not {"_cls", "_args", "_kwargs"} & locals_.keys() for locals_ in frames)
        del probe, caught, frames
        gc.collect()
        assert probe_ref() is None
    del origin, origins, EphemeralAlias
    gc.collect()
    assert ephemeral_ref() is None


def test_invalid_declaration_boundaries_discard_untrusted_inputs_before_type_error() -> None:
    api = account_api()
    canary = "canary-declaration-boundary"

    class Probe:
        pass

    def exercise(declaration: Callable[..., object], kind: str) -> None:
        family = Probe()
        payload = Probe()
        family_ref = weakref.ref(family)
        payload_ref = weakref.ref(payload)
        caught: TypeError | None = None
        try:
            declaration(
                family,
                payload,
                canonical_name=canary,
                owner_module=canary,
            )
        except TypeError as error:
            assert str(error) == f"invalid reviewed generic {kind} declaration"
            caught = error
        else:
            raise AssertionError("invalid declaration unexpectedly accepted")
        assert caught is not None
        frames = _core_boundary_locals(caught, f"_declare_reviewed_{kind}_candidate")
        assert frames and all(canary not in repr(locals_) for locals_ in frames)
        for locals_ in frames:
            assert not {"family", "payload", "canonical_name", "owner_module"} & locals_.keys()
        del family, payload, caught, frames
        gc.collect()
        assert family_ref() is None
        assert payload_ref() is None

    exercise(api.module._declare_reviewed_model_candidate, "model")
    exercise(api.module._declare_reviewed_alias_candidate, "alias")
