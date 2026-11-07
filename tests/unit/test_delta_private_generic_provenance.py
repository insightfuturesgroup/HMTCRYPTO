"""Scalar construction-source directory regressions for reviewed generics."""

# ruff: noqa: E501 -- literal one-record-per-line snapshots are the reviewed contract.

from __future__ import annotations

import dataclasses
import gc
import importlib
import weakref
from collections.abc import Callable, Iterator
from types import ModuleType

import pytest
from delta_private_model_test_support import isolated_python

pytestmark = pytest.mark.unit


def _modules() -> tuple[ModuleType, ModuleType]:
    provenance = importlib.import_module("hmt_crypto.domain._private_generic_provenance")
    manifest = importlib.import_module("hmt_crypto.domain._private_generic_manifest")
    return provenance, manifest


def _projection(manifest: ModuleType) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            owner.module_name,
            row.candidate_kind,
            row.family_module,
            row.family_name,
            row.payload_name,
            row.specialization_name,
        )
        for owner in manifest._reviewed_vocabulary_for_verification()
        for row in owner.rows
    )


def _record_text(records: tuple[object, ...]) -> str:
    return "\n".join(
        "|".join(
            "-" if value is None else str(value)
            for value in (
                object.__getattribute__(record, field.name) for field in dataclasses.fields(record)
            )
        )
        for record in records
    )


def test_scalar_directory_has_exact_ordered_family_records() -> None:
    """A reordered or self-consistent family replacement cannot redefine provenance."""
    provenance, _manifest = _modules()
    assert (
        _record_text(provenance._GENERIC_FAMILY_SOURCES)
        == """
hmt_crypto.domain.account|ValueField|hmt_crypto.domain._private_generic_core|ValueField|MODEL
hmt_crypto.domain.account|NullableField|hmt_crypto.domain._private_generic_core|NullableField|ALIAS
hmt_crypto.domain.account|ObservedEnum|hmt_crypto.domain._private_generic_core|ObservedEnum|MODEL
hmt_crypto.domain.account|FieldPresence|hmt_crypto.domain._private_generic_core|FieldPresence|ALIAS
hmt_crypto.domain.account|Observed|hmt_crypto.domain._private_generic_core|Observed|MODEL
hmt_crypto.domain.private_orders|LookupFound|hmt_crypto.domain._private_result_contracts|LookupFound|MODEL
hmt_crypto.domain.private_orders|LookupResult|hmt_crypto.domain._private_result_contracts|LookupResult|ALIAS
hmt_crypto.domain.private_orders|PrivateCursorPage|hmt_crypto.domain._private_result_contracts|PrivateCursorPage|MODEL
hmt_crypto.domain.private_orders|StrictPrivatePaginationResult|hmt_crypto.domain._private_result_contracts|StrictPrivatePaginationResult|MODEL
hmt_crypto.domain.private_orders|DiagnosticPrivatePaginationResult|hmt_crypto.domain._private_result_contracts|DiagnosticPrivatePaginationResult|MODEL
""".strip()
    )


def test_scalar_directory_has_exact_ordered_payload_records() -> None:
    """Every distinct payload has exactly one construction-source or prior-row rule."""
    provenance, _manifest = _modules()
    assert (
        _record_text(provenance._GENERIC_PAYLOAD_SOURCES)
        == """
hmt_crypto.domain.private_primitives|OpaqueCursor|SOURCE|ANNOTATED_ALIAS|hmt_crypto.domain.private_primitives|OpaqueCursor|-|-
hmt_crypto.domain.private_wallet|WalletTransactionType|SOURCE|ENUM|hmt_crypto.domain._private_wallet_contracts|WalletTransactionType|-|-
hmt_crypto.domain.private_wallet|WalletTransactionMetadataValue|SOURCE|MODEL|hmt_crypto.domain._private_wallet_contracts|WalletTransactionMetadataValue|-|-
hmt_crypto.domain.private_wallet|BalanceSnapshot|SOURCE|MODEL|hmt_crypto.domain._private_wallet_contracts|BalanceSnapshot|-|-
hmt_crypto.domain.private_positions|MarginMode|SOURCE|ENUM|hmt_crypto.domain._private_positions_contracts|MarginMode|-|-
hmt_crypto.domain.private_positions|MarginedPositionBatch|SOURCE|TUPLE_ALIAS|hmt_crypto.domain._private_positions_contracts|MarginedPositionBatch|-|-
hmt_crypto.domain.private_orders|OrderSide|SOURCE|ENUM|hmt_crypto.domain._private_order_contracts|OrderSide|-|-
hmt_crypto.domain.private_orders|OrderType|SOURCE|ENUM|hmt_crypto.domain._private_order_contracts|OrderType|-|-
hmt_crypto.domain.private_orders|StopOrderType|SOURCE|ENUM|hmt_crypto.domain._private_order_contracts|StopOrderType|-|-
hmt_crypto.domain.private_orders|MmpLevel|SOURCE|ENUM|hmt_crypto.domain._private_order_contracts|MmpLevel|-|-
hmt_crypto.domain.private_orders|StopTriggerMethod|SOURCE|ENUM|hmt_crypto.domain._private_order_contracts|StopTriggerMethod|-|-
hmt_crypto.domain.private_orders|TimeInForce|SOURCE|ENUM|hmt_crypto.domain._private_order_contracts|TimeInForce|-|-
hmt_crypto.domain.private_orders|OrderState|SOURCE|ENUM|hmt_crypto.domain._private_order_contracts|OrderState|-|-
hmt_crypto.domain.private_orders|FillContractType|SOURCE|ENUM|hmt_crypto.domain._private_fill_contracts|FillContractType|-|-
hmt_crypto.domain.private_orders|FillNotionalType|SOURCE|ENUM|hmt_crypto.domain._private_fill_contracts|FillNotionalType|-|-
hmt_crypto.domain.private_orders|FillType|SOURCE|ENUM|hmt_crypto.domain._private_fill_contracts|FillType|-|-
hmt_crypto.domain.private_orders|FillRole|SOURCE|ENUM|hmt_crypto.domain._private_fill_contracts|FillRole|-|-
hmt_crypto.domain.account|ExactBool|SOURCE|ANNOTATED_ALIAS|hmt_crypto.domain._account_contracts|ExactBool|-|-
hmt_crypto.domain.private_primitives|PositivePrivateDecimal|SOURCE|ANNOTATED_ALIAS|hmt_crypto.domain.private_primitives|PositivePrivateDecimal|-|-
hmt_crypto.domain.private_orders|ObservedMmpLevel|PRIOR_ROW|PRIOR_MODEL|-|-|11|ObservedMmpLevel
hmt_crypto.domain.private_orders|ObservedStopTriggerMethod|PRIOR_ROW|PRIOR_MODEL|-|-|12|ObservedStopTriggerMethod
hmt_crypto.domain.private_orders|ObservedTimeInForce|PRIOR_ROW|PRIOR_MODEL|-|-|13|ObservedTimeInForce
hmt_crypto.domain.private_primitives|PrivateDecimal|SOURCE|ANNOTATED_ALIAS|hmt_crypto.domain.private_primitives|PrivateDecimal|-|-
hmt_crypto.domain.private_primitives|ObservedDeltaClientOrderId|SOURCE|ANNOTATED_ALIAS|hmt_crypto.domain.private_primitives|ObservedDeltaClientOrderId|-|-
hmt_crypto.domain.private_orders|FillMetadataValue|SOURCE|MODEL|hmt_crypto.domain._private_fill_contracts|FillMetadataValue|-|-
hmt_crypto.domain.private_positions|LatestPosition|SOURCE|MODEL|hmt_crypto.domain._private_positions_contracts|LatestPosition|-|-
hmt_crypto.domain.private_orders|Order|SOURCE|MODEL|hmt_crypto.domain._private_order_contracts|Order|-|-
hmt_crypto.domain.private_wallet|WalletTransaction|SOURCE|MODEL|hmt_crypto.domain._private_wallet_contracts|WalletTransaction|-|-
hmt_crypto.domain.private_orders|Fill|SOURCE|MODEL|hmt_crypto.domain._private_fill_contracts|Fill|-|-
hmt_crypto.domain.private_orders|LatestPositionLookupResult|PRIOR_ROW|PRIOR_ALIAS|-|-|37|LatestPositionLookupResult
hmt_crypto.domain.private_orders|OrderLookupResult|PRIOR_ROW|PRIOR_ALIAS|-|-|38|OrderLookupResult
hmt_crypto.domain.private_orders|Leverage|SOURCE|MODEL|hmt_crypto.domain._private_result_contracts|Leverage|-|-
""".strip()
    )


def test_scalar_directory_has_exact_ordered_dependency_records() -> None:
    """Alias branches are fixed to source identities or matching earlier families."""
    provenance, _manifest = _modules()
    assert (
        _record_text(provenance._GENERIC_ALIAS_DEPENDENCIES)
        == """
hmt_crypto.domain.account|FieldPresence|MISSING|SOURCE|hmt_crypto.domain.account|MissingField|hmt_crypto.domain._account_contracts|MissingField|-|-
hmt_crypto.domain.account|FieldPresence|NULL|SOURCE|hmt_crypto.domain.account|NullField|hmt_crypto.domain._account_contracts|NullField|-|-
hmt_crypto.domain.account|FieldPresence|VALUE|MATCHING_PRIOR_FAMILY|-|-|-|-|hmt_crypto.domain.account|ValueField
hmt_crypto.domain.account|NullableField|NULL|SOURCE|hmt_crypto.domain.account|NullField|hmt_crypto.domain._account_contracts|NullField|-|-
hmt_crypto.domain.account|NullableField|VALUE|MATCHING_PRIOR_FAMILY|-|-|-|-|hmt_crypto.domain.account|ValueField
hmt_crypto.domain.private_orders|LookupResult|FOUND|MATCHING_PRIOR_FAMILY|-|-|-|-|hmt_crypto.domain.private_orders|LookupFound
hmt_crypto.domain.private_orders|LookupResult|NOT_FOUND|SOURCE|hmt_crypto.domain.private_orders|LookupNotFound|hmt_crypto.domain._private_result_contracts|LookupNotFound|-|-
""".strip()
    )


def test_source_records_are_frozen_slotted_and_identity_compared() -> None:
    """A record cannot become mutable or value-equal authority."""
    provenance, _manifest = _modules()
    for record in (
        provenance._GENERIC_FAMILY_SOURCES[0],
        provenance._GENERIC_PAYLOAD_SOURCES[0],
        provenance._GENERIC_ALIAS_DEPENDENCIES[0],
    ):
        assert dataclasses.is_dataclass(record)
        assert type(record).__dataclass_params__.frozen is True
        assert not hasattr(record, "__dict__")
        values = tuple(
            object.__getattribute__(record, field.name) for field in dataclasses.fields(record)
        )
        clone = type(record)(*values)
        assert clone is not record and clone != record
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, dataclasses.fields(record)[0].name, "changed")


def test_manifest_projection_and_prior_row_indices_are_exact() -> None:
    """Prior payload links cannot drift to another same-looking global row."""
    provenance, manifest = _modules()
    projection = _projection(manifest)
    assert len(projection) == 51
    assert tuple(projection[index][5] for index in (11, 12, 13, 37, 38)) == (
        "ObservedMmpLevel",
        "ObservedStopTriggerMethod",
        "ObservedTimeInForce",
        "LatestPositionLookupResult",
        "OrderLookupResult",
    )
    assert provenance._validate_source_directory(projection) is None


@pytest.mark.parametrize(
    "attribute,index,changes",
    (
        (
            "_GENERIC_FAMILY_SOURCES",
            0,
            {"source_module": "hmt_crypto.domain._account_contracts"},
        ),
        ("_GENERIC_PAYLOAD_SOURCES", 1, {"shape": "MODEL"}),
        (
            "_GENERIC_PAYLOAD_SOURCES",
            1,
            {"source_module": "hmt_crypto.domain._private_positions_contracts"},
        ),
        (
            "_GENERIC_ALIAS_DEPENDENCIES",
            0,
            {"public_name": "NullField", "source_name": "NullField"},
        ),
        (
            "_GENERIC_ALIAS_DEPENDENCIES",
            2,
            {"prior_family_name": "ObservedEnum"},
        ),
    ),
    ids=(
        "value-field-source-module",
        "wallet-transaction-type-shape",
        "wallet-transaction-type-source-module",
        "field-presence-missing-target",
        "field-presence-value-prior-family",
    ),
)
def test_every_canonical_source_scalar_is_directory_authority(
    attribute: str,
    index: int,
    changes: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A semantically plausible scalar edit cannot redefine a reviewed source."""
    provenance, manifest = _modules()
    records = getattr(provenance, attribute)
    changed = dataclasses.replace(records[index], **changes)
    monkeypatch.setattr(provenance, attribute, (*records[:index], changed, *records[index + 1 :]))
    with pytest.raises(RuntimeError, match="^private generic source directory invalid$"):
        provenance._validate_source_directory(_projection(manifest))


def test_source_record_containers_are_exact_before_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tuple subclass cannot run iteration while supplier containers are rejected."""
    provenance, manifest = _modules()
    calls: list[str] = []

    class HostileTuple(tuple[object, ...]):
        def __iter__(self) -> Iterator[object]:
            calls.append("iter")
            raise AssertionError("hostile source container iterated")

    monkeypatch.setattr(
        provenance,
        "_GENERIC_FAMILY_SOURCES",
        HostileTuple(provenance._GENERIC_FAMILY_SOURCES),
    )
    with pytest.raises(RuntimeError, match="^private generic source directory invalid$"):
        provenance._validate_source_directory(_projection(manifest))
    assert calls == []


@pytest.mark.parametrize("level", ["outer", "inner"])
def test_projection_containers_are_exact_before_iteration(level: str) -> None:
    """Neither an outer nor inner tuple subclass gains an iteration callback."""
    provenance, manifest = _modules()
    projection = _projection(manifest)
    calls: list[str] = []

    class HostileTuple(tuple[object, ...]):
        def __iter__(self) -> Iterator[object]:
            calls.append("iter")
            raise AssertionError("hostile projection container iterated")

    malformed = (
        HostileTuple(projection)
        if level == "outer"
        else (HostileTuple(projection[0]), *projection[1:])
    )
    with pytest.raises(RuntimeError, match="^private generic source directory invalid$"):
        provenance._validate_source_directory(malformed)
    assert calls == []


@pytest.mark.parametrize(
    "attribute,mutation",
    (
        ("_GENERIC_FAMILY_SOURCES", lambda values, _module: values[:-1]),
        ("_GENERIC_FAMILY_SOURCES", lambda values, _module: (*values[:-1], values[0])),
        ("_GENERIC_FAMILY_SOURCES", lambda values, _module: (values[1], values[0], *values[2:])),
        (
            "_GENERIC_FAMILY_SOURCES",
            lambda values, _module: (
                *values,
                dataclasses.replace(values[0], public_name="Unused", source_name="Unused"),
            ),
        ),
        (
            "_GENERIC_FAMILY_SOURCES",
            lambda values, _module: (dataclasses.replace(values[0], kind="ALIAS"), *values[1:]),
        ),
        (
            "_GENERIC_PAYLOAD_SOURCES",
            lambda values, _module: (dataclasses.replace(values[0], shape="MODEL"), *values[1:]),
        ),
        (
            "_GENERIC_PAYLOAD_SOURCES",
            lambda values, _module: (
                *values[:19],
                dataclasses.replace(values[19], prior_global_row=10),
                *values[20:],
            ),
        ),
        (
            "_GENERIC_PAYLOAD_SOURCES",
            lambda values, _module: (
                *values[:19],
                dataclasses.replace(values[19], prior_name="ObservedOrderType"),
                *values[20:],
            ),
        ),
        (
            "_GENERIC_PAYLOAD_SOURCES",
            lambda values, _module: (
                *values[:19],
                dataclasses.replace(values[19], shape="PRIOR_ALIAS"),
                *values[20:],
            ),
        ),
        (
            "_GENERIC_ALIAS_DEPENDENCIES",
            lambda values, _module: (
                dataclasses.replace(values[0], kind="MATCHING_PRIOR_FAMILY"),
                *values[1:],
            ),
        ),
    ),
)
def test_directory_mutations_fail_with_one_fixed_internal_error(
    attribute: str,
    mutation: Callable[[tuple[object, ...], ModuleType], tuple[object, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing, duplicate, reordered, unused, and contradictory records fail closed."""
    provenance, manifest = _modules()
    original = getattr(provenance, attribute)
    monkeypatch.setattr(provenance, attribute, mutation(original, provenance))
    with pytest.raises(RuntimeError) as captured:
        provenance._validate_source_directory(_projection(manifest))
    assert str(captured.value) == "private generic source directory invalid"
    assert captured.value.__cause__ is None


def test_directory_rejection_does_not_retain_hostile_projection_value() -> None:
    """The fixed internal failure traceback owns no caller-supplied raw value."""
    provenance, manifest = _modules()

    class Probe:
        pass

    probe = Probe()
    probe_ref = weakref.ref(probe)
    projection = _projection(manifest)
    malformed = ((probe, *projection[0][1:]), *projection[1:])
    caught: RuntimeError | None = None
    try:
        provenance._validate_source_directory(malformed)
    except RuntimeError as error:
        caught = error
    else:
        raise AssertionError("malformed source directory unexpectedly accepted")
    assert caught is not None
    assert str(caught) == "private generic source directory invalid"
    del probe, projection, malformed
    gc.collect()
    assert probe_ref() is None


def test_lookup_scans_require_exact_strings_without_hostile_callbacks() -> None:
    """Lookup inputs never gain equality or hashing callbacks."""
    provenance, _manifest = _modules()
    calls: list[str] = []

    class HostileString(str):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("hostile equality ran")

        def __hash__(self) -> int:
            calls.append("hash")
            raise AssertionError("hostile hash ran")

    hostile = HostileString("ValueField")
    assert provenance._family_source_for("hmt_crypto.domain.account", hostile) is None
    assert provenance._payload_source_for(hostile) is None
    assert provenance._alias_dependencies_for("hmt_crypto.domain.account", hostile) is None
    assert calls == []


def test_manifest_import_executes_source_directory_validation() -> None:
    """A malformed scalar supplier aborts manifest import before use."""
    result = isolated_python(
        """
import sys
import hmt_crypto.domain._private_generic_provenance as source

def reject(_rows):
    raise RuntimeError('validation-probe')

source._validate_source_directory = reject
sys.modules.pop('hmt_crypto.domain._private_generic_manifest', None)
try:
    import hmt_crypto.domain._private_generic_manifest
except RuntimeError as error:
    assert str(error) == 'validation-probe'
else:
    raise AssertionError('manifest skipped scalar source validation')
"""
    )
    assert result.returncode == 0, result.stderr
