"""Adversarial admission tests for Delta private generic specializations."""

from __future__ import annotations

import importlib
import sys
from copy import copy, deepcopy
from types import ModuleType

import pytest
from delta_private_model_test_support import account_api, assert_safe_generic_error
from pydantic import TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError

pytestmark = pytest.mark.unit


def test_arbitrary_exact_count_later_owner_table_cannot_widen_grammar() -> None:
    """A correctly sized fake later-owner table is not vocabulary authority."""
    from pydantic import BaseModel

    api = account_api()
    account = api.module
    module_name = "hmt_crypto.domain.private_wallet"
    sentinel = object()
    previous = sys.modules.get(module_name, sentinel)
    fake = ModuleType(module_name)
    payloads = (int, str, float, bytes)
    candidates: list[type[object]] = []
    rows: list[object] = []
    try:
        sys.modules[module_name] = fake
        builder = BaseModel.__class_getitem__
        for index, payload in enumerate(payloads):
            candidate = builder.__func__(api.ValueField, payload)
            candidate.__name__ = f"InjectedValue{index}"
            candidate.__qualname__ = candidate.__name__
            candidate.__module__ = module_name
            setattr(fake, candidate.__name__, candidate)
            candidates.append(candidate)
            rows.append(account._ReviewedGenericSpecialization(api.ValueField, payload, candidate))
        fake._WALLET_REVIEWED_GENERIC_SPECIALIZATIONS = tuple(rows)

        marker = api.ValueField[list[int]]
        for payload, candidate in zip(payloads, candidates, strict=True):
            assert api.ValueField[payload] is marker
            assert api.ValueField[payload] is not candidate
            with pytest.raises(ValidationError, match="private generic specialization unavailable"):
                api.ValueField[payload](kind="VALUE", value=payload())
    finally:
        if previous is sentinel:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous


def test_declaration_helpers_accept_only_one_frozen_row_identity() -> None:
    """Declaration is limited by the frozen row, not an owner/name shape."""
    api = account_api()
    account = api.module
    assert (
        account._declare_reviewed_model_candidate(
            api.ValueField,
            api.OpaqueCursor,
            canonical_name="OpaqueCursorValueField",
            owner_module="hmt_crypto.domain.account",
        )
        is not None
    )
    for change in (
        {"payload": int},
        {"canonical_name": "OtherCursorValueField"},
        {"owner_module": "hmt_crypto.domain.private_wallet"},
        {"family": api.Observed},
    ):
        arguments = {
            "family": api.ValueField,
            "payload": api.OpaqueCursor,
            "canonical_name": "OpaqueCursorValueField",
            "owner_module": "hmt_crypto.domain.account",
        }
        arguments.update(change)
        with pytest.raises(TypeError, match="invalid reviewed generic model declaration"):
            account._declare_reviewed_model_candidate(**arguments)

    vocabulary = importlib.import_module(
        "hmt_crypto.domain._private_generic_manifest"
    )._reviewed_vocabulary_for_verification()
    family_locations = {
        (row.family_module, row.family_name) for owner in vocabulary for row in owner.rows
    }
    assert family_locations == {
        ("hmt_crypto.domain.account", "ObservedEnum"),
        ("hmt_crypto.domain.account", "ValueField"),
        ("hmt_crypto.domain.account", "FieldPresence"),
        ("hmt_crypto.domain.account", "NullableField"),
        ("hmt_crypto.domain.account", "Observed"),
        ("hmt_crypto.domain.private_orders", "LookupFound"),
        ("hmt_crypto.domain.private_orders", "LookupResult"),
        ("hmt_crypto.domain.private_orders", "PrivateCursorPage"),
        ("hmt_crypto.domain.private_orders", "StrictPrivatePaginationResult"),
        ("hmt_crypto.domain.private_orders", "DiagnosticPrivatePaginationResult"),
    }


def test_exact_tag_ownership_precedes_every_comparison() -> None:
    """Corrupt alias tags reject before hostile string equality can run."""
    api = account_api()
    calls: list[str] = []

    class HostileTag(str):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("hostile equality ran")

        def __ne__(self, other: object) -> bool:
            del other
            calls.append("ne")
            raise AssertionError("hostile inequality ran")

    original = api.NullableOpaqueCursor.__private_tags__
    api.NullableOpaqueCursor.__private_tags__ = (
        (HostileTag("NULL"), api.NullField),
        original[1],
    )
    try:
        assert api.NullableField[api.OpaqueCursor] is not api.NullableOpaqueCursor
        assert calls == []
    finally:
        api.NullableOpaqueCursor.__private_tags__ = original

    from pydantic import BaseModel

    class ForeignModel(BaseModel):
        kind: str

    corrupt = api.module._ExactTaggedUnion((("NULL", ForeignModel),))
    with pytest.raises(ValidationError, match="private generic specialization unavailable"):
        corrupt.own(ForeignModel(kind="NULL"))
    assert calls == []


def test_specialization_type_checks_use_identity_not_membership() -> None:
    """A hostile metaclass cannot run equality during table validation."""
    api = account_api()
    account = api.module
    calls: list[str] = []

    class MetaMeta(type):
        def __eq__(cls, other: object) -> bool:
            del cls, other
            calls.append("eq")
            raise AssertionError("hostile metaclass equality ran")

    class HostileMeta(type, metaclass=MetaMeta):
        pass

    class Hostile(metaclass=HostileMeta):
        pass

    original = account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS
    account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS = (
        account._ReviewedGenericSpecialization(api.ValueField, api.OpaqueCursor, Hostile),
        original[1],
    )
    try:
        assert api.ValueField[api.OpaqueCursor] is not api.OpaqueCursorValueField
        assert calls == []
    finally:
        account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS = original


def test_malformed_owner_tables_fail_closed_without_poisoning_restoration() -> None:
    """Every malformed bound-table shape rejects, and no negative result is cached."""
    api = account_api()
    account = api.module
    marker = api.ValueField[int]
    original = account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS

    class TupleSubclass(tuple):
        pass

    class RecordSubclass(account._ReviewedGenericSpecialization):
        pass

    variants = (
        [*original],
        TupleSubclass(original),
        original[:1],
        (*original, original[0]),
        (object(), original[1]),
        (
            RecordSubclass(
                original[0].family,
                original[0].payload,
                original[0].specialization,
            ),
            original[1],
        ),
        (original[1], original[0]),
        (original[0], original[0]),
        (
            account._ReviewedGenericSpecialization(
                api.Observed, original[0].payload, original[0].specialization
            ),
            original[1],
        ),
        (
            account._ReviewedGenericSpecialization(
                original[0].family, int, original[0].specialization
            ),
            original[1],
        ),
        (
            account._ReviewedGenericSpecialization(
                original[0].family, original[0].payload, api.NullField
            ),
            original[1],
        ),
    )
    try:
        del account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS
        assert api.ValueField[api.OpaqueCursor] is marker
        for malformed in variants:
            account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS = malformed
            assert api.ValueField[api.OpaqueCursor] is marker
            assert api.NullableField[api.OpaqueCursor] is marker
    finally:
        account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS = original

    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField
    assert api.NullableField[api.OpaqueCursor] is api.NullableOpaqueCursor
    with pytest.raises(ValidationError, match="private generic specialization unavailable"):
        marker.model_validate({"kind": "VALUE", "value": "next"})


def test_bound_nonmodule_owner_slots_fail_closed_including_none() -> None:
    """A present sys.modules slot is never mistaken for an absent later owner."""
    api = account_api()
    module_name = "hmt_crypto.domain.private_wallet"
    sentinel = object()
    previous = sys.modules.get(module_name, sentinel)
    marker = api.ValueField[int]

    class ModuleSubclass(ModuleType):
        pass

    try:
        for malformed in (None, object(), ModuleSubclass(module_name)):
            sys.modules[module_name] = malformed
            assert api.ValueField[api.OpaqueCursor] is marker
            assert api.NullableField[api.OpaqueCursor] is marker
    finally:
        if previous is sentinel:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField


def test_rejected_specialization_blocks_every_public_entry_path() -> None:
    """The permanent marker never validates, constructs, copies, dumps, or schemas."""
    api = account_api()
    marker = api.ValueField[tuple[int, ...]]
    adapter = TypeAdapter(marker)
    canary = "fixture-secret-rejected-generic"
    payload = {"kind": "VALUE", "value": canary}
    validation_calls = (
        lambda: marker(**payload),
        lambda: marker.model_validate(payload),
        lambda: marker.model_validate_json(
            '{"kind":"VALUE","value":"fixture-secret-rejected-generic"}'
        ),
        lambda: marker.model_validate_strings(payload),
        lambda: marker.model_construct(kind="VALUE", value=canary),
        lambda: adapter.validate_python(payload),
        lambda: adapter.validate_json('{"kind":"VALUE","value":"fixture-secret-rejected-generic"}'),
        lambda: adapter.validate_strings(payload),
    )
    for call in validation_calls:
        with pytest.raises(ValidationError) as captured:
            call()
        assert_safe_generic_error(captured.value, canary)

    for call in (marker.model_json_schema, adapter.json_schema):
        with pytest.raises(TypeError, match="^private generic specialization unavailable$"):
            call()

    forged = object.__new__(marker)
    for call in (
        lambda: copy(forged),
        lambda: deepcopy(forged),
        lambda: forged.model_copy(),
        lambda: forged.model_dump(),
        lambda: forged.model_dump_json(),
    ):
        with pytest.raises(ValidationError) as captured:
            call()
        assert_safe_generic_error(captured.value, canary)

    for call in (lambda: adapter.dump_python(forged), lambda: adapter.dump_json(forged)):
        with pytest.raises(PydanticSerializationError) as captured:
            call()
        assert "private generic specialization unavailable" in str(captured.value)
        assert_safe_generic_error(captured.value, canary)


def test_bare_subclass_forged_and_corrupted_generic_candidates_stay_rejected() -> None:
    """Structural resemblance never grants a model candidate table authority."""
    from pydantic import BaseModel

    api = account_api()

    class ForgedFamily(api.ValueField):
        pass

    builder = BaseModel.__class_getitem__
    forged_candidate = builder.__func__(api.ValueField, api.OpaqueCursor)
    rejected = (
        api.ValueField,
        api.Observed,
        api.ObservedEnum,
        api.ValueField[int],
        api.Observed[int],
        ForgedFamily,
        forged_candidate,
    )
    for candidate in rejected:
        adapter = TypeAdapter(candidate)
        with pytest.raises(ValidationError) as captured:
            adapter.validate_python({"kind": "VALUE", "value": "next"})
        assert_safe_generic_error(captured.value, "next")
        with pytest.raises(TypeError, match="^private generic specialization unavailable$"):
            adapter.json_schema()

    metadata = api.OpaqueCursorValueField.__pydantic_generic_metadata__
    original = dict(metadata)
    try:
        metadata["args"] = (int,)
        assert api.ValueField[api.OpaqueCursor] is api.ValueField[int]
        value = object.__new__(api.OpaqueCursorValueField)
        with pytest.raises(ValidationError, match="private generic specialization unavailable"):
            value.model_dump()
    finally:
        metadata.clear()
        metadata.update(original)
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField
