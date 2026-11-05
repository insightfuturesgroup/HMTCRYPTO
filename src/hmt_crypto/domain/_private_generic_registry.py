"""Callback-free identity registry for already loaded private generic owners."""

import sys
from dataclasses import dataclass
from types import GenericAlias, MappingProxyType

from hmt_crypto.domain._private_generic_manifest import (
    _reviewed_vocabulary_for_verification,
    _ReviewedGenericOwnerSpec,
    _ReviewedGenericRowSpec,
)
from hmt_crypto.domain._private_generic_source import (
    _exact_string_dict,
    _module_namespace,
    _owned_lookup,
    _source_alias_dependencies,
    _verify_loaded_row_source,
)

_ACCOUNT_MODULE = "hmt_crypto.domain.account"
_ABSENT = object()


@dataclass(frozen=True, slots=True, eq=False)
class _ReviewedGenericSpecialization:
    __module__ = _ACCOUNT_MODULE

    family: object
    payload: object
    specialization: object


@dataclass(frozen=True, slots=True, eq=False)
class _DeclarationResolution:
    row: _ReviewedGenericRowSpec
    reuse: bool
    candidate: object


_RegistryEntry = tuple[
    _ReviewedGenericOwnerSpec,
    _ReviewedGenericRowSpec,
    object,
    object,
    object,
    dict[str, object],
]


def _normalize_single_parameter(parameters: object) -> object | None:
    if type(parameters) is not tuple:
        return parameters
    return tuple.__getitem__(parameters, 0) if tuple.__len__(parameters) == 1 else None


def _record_values(row: object) -> tuple[object, object, object] | None:
    if type(row) is not _ReviewedGenericSpecialization:
        return None
    try:
        return (
            object.__getattribute__(row, "family"),
            object.__getattribute__(row, "payload"),
            object.__getattribute__(row, "specialization"),
        )
    except Exception:
        return None


def _candidate_namespace(
    candidate: object,
    expected_type: object,
    owner: _ReviewedGenericOwnerSpec,
    row: _ReviewedGenericRowSpec,
    owner_namespace: dict[str, object],
) -> dict[str, object] | None:
    if type(candidate) is not expected_type:
        return None
    try:
        raw_namespace = type.__getattribute__(candidate, "__dict__")
        namespace = _exact_string_dict(MappingProxyType.copy(raw_namespace))
        module_name = type.__getattribute__(candidate, "__module__")
        name = type.__getattribute__(candidate, "__name__")
        qualname = type.__getattribute__(candidate, "__qualname__")
    except Exception:
        return None
    expected_name = row.specialization_name
    present, bound = _owned_lookup(owner_namespace, expected_name)
    if (
        namespace is None
        or type(module_name) is not str
        or type(name) is not str
        or type(qualname) is not str
        or module_name != owner.module_name
        or name != expected_name
        or qualname != expected_name
        or not present
        or bound is not candidate
    ):
        return None
    return namespace


def _contains_identity(values: object, expected: object) -> bool:
    if type(values) is not tuple:
        return False
    for index in range(tuple.__len__(values)):
        if tuple.__getitem__(values, index) is expected:
            return True
    return False


def _model_matches(entry: _RegistryEntry) -> bool:
    owner, row, family, payload, candidate, owner_namespace = entry
    namespace = _candidate_namespace(candidate, type(family), owner, row, owner_namespace)
    account_namespace = _module_namespace(_ACCOUNT_MODULE)
    if namespace is None or account_namespace is None:
        return False
    boundary_present, boundary = _owned_lookup(account_namespace, "_ClosedPrivateGenericModel")
    try:
        model_mro = type.__getattribute__(candidate, "__mro__")
    except Exception:
        return False
    if not boundary_present or not _contains_identity(model_mro, boundary):
        return False
    metadata_present, metadata_value = _owned_lookup(namespace, "__pydantic_generic_metadata__")
    metadata = _exact_string_dict(metadata_value) if metadata_present else None
    if metadata is None:
        return False
    origin_present, origin = _owned_lookup(metadata, "origin")
    arguments_present, arguments = _owned_lookup(metadata, "args")
    parameters_present, parameters = _owned_lookup(metadata, "parameters")
    return (
        origin_present
        and origin is family
        and arguments_present
        and type(arguments) is tuple
        and tuple.__len__(arguments) == 1
        and tuple.__getitem__(arguments, 0) is payload
        and parameters_present
        and type(parameters) is tuple
        and tuple.__len__(parameters) == 0
    )


def _alias_matches(
    entry: _RegistryEntry,
    entries: tuple[_RegistryEntry, ...],
) -> bool:
    owner, row, family, payload, candidate, owner_namespace = entry
    namespace = _candidate_namespace(candidate, type, owner, row, owner_namespace)
    account_namespace = _module_namespace(_ACCOUNT_MODULE)
    if namespace is None or account_namespace is None:
        return False
    alias_base_present, alias_base = _owned_lookup(account_namespace, "_ClosedPrivateAlias")
    family_present, actual_family = _owned_lookup(namespace, "__private_family__")
    payload_present, actual_payload = _owned_lookup(namespace, "__private_payload__")
    tags_present, tags = _owned_lookup(namespace, "__private_tags__")
    try:
        bases = type.__getattribute__(candidate, "__bases__")
    except Exception:
        return False
    if (
        not alias_base_present
        or type(bases) is not tuple
        or tuple.__len__(bases) != 1
        or tuple.__getitem__(bases, 0) is not alias_base
        or not family_present
        or actual_family is not family
        or not payload_present
        or actual_payload is not payload
        or not tags_present
    ):
        return False
    expected = _source_alias_dependencies(row, payload, owner.module_name)
    if type(tags) is not tuple or expected is None or tuple.__len__(tags) != len(expected):
        return False
    for index in range(len(expected)):
        tag = tuple.__getitem__(tags, index)
        literal, branch = expected[index]
        if type(tag) is not tuple or tuple.__len__(tag) != 2:
            return False
        actual_literal = tuple.__getitem__(tag, 0)
        if type(actual_literal) is not str or actual_literal != literal:
            return False
        if tuple.__getitem__(tag, 1) is not branch:
            return False
    return True


def _load_reviewed_rows() -> tuple[_ReviewedGenericSpecialization, ...] | None:
    entries: list[_RegistryEntry] = []
    rows: list[_ReviewedGenericSpecialization] = []
    for owner in _reviewed_vocabulary_for_verification():
        owner_namespace = _module_namespace(owner.module_name)
        if owner_namespace is None:
            modules = _exact_string_dict(sys.modules)
            if modules is not None and not _owned_lookup(modules, owner.module_name)[0]:
                continue
            return None
        table_present, table = _owned_lookup(owner_namespace, owner.table_name)
        if not table_present:
            continue
        if type(table) is not tuple or tuple.__len__(table) != len(owner.rows):
            return None
        for index, expected in enumerate(owner.rows):
            actual = tuple.__getitem__(table, index)
            values = _record_values(actual)
            family_namespace = _module_namespace(expected.family_module)
            if values is None or family_namespace is None:
                return None
            family, payload, specialization = values
            family_present, bound_family = _owned_lookup(family_namespace, expected.family_name)
            payload_present, bound_payload = _owned_lookup(owner_namespace, expected.payload_name)
            candidate_present, bound_candidate = _owned_lookup(
                owner_namespace, expected.specialization_name
            )
            if (
                not family_present
                or family is _ABSENT
                or family is not bound_family
                or not payload_present
                or payload is _ABSENT
                or payload is not bound_payload
                or not candidate_present
                or specialization is _ABSENT
                or specialization is not bound_candidate
                or not _verify_loaded_row_source(owner, expected, family, payload)
            ):
                return None
            rows.append(actual)
            entries.append((owner, expected, family, payload, specialization, owner_namespace))
    frozen_entries = tuple(entries)
    for index, entry in enumerate(frozen_entries):
        _owner, row, family, payload, specialization, _namespace = entry
        if not (
            _model_matches(entry)
            if row.candidate_kind == "model"
            else _alias_matches(entry, frozen_entries)
        ):
            return None
        for prior in frozen_entries[:index]:
            if specialization is prior[4] or family is prior[2] and payload is prior[3]:
                return None
    return tuple(rows)


def _find_reviewed_specialization(family: object, payload: object) -> object | None:
    rows = _load_reviewed_rows()
    if rows is None:
        return None
    for row in rows:
        values = _record_values(row)
        if values is None:
            return None
        if values[0] is family and values[1] is payload:
            return values[2]
    return None


def _is_reviewed_specialization(value: object) -> bool:
    rows = _load_reviewed_rows()
    return rows is not None and any(_record_values(row)[2] is value for row in rows)  # type: ignore[index]


def _reviewed_payload(value: object, family: object) -> object | None:
    rows = _load_reviewed_rows()
    if rows is None:
        return None
    for row in rows:
        values = _record_values(row)
        if values is None:
            return None
        if values[2] is value and values[0] is family:
            return values[1]
    return None


def _reviewed_payload_requires_exact_tuple(value: object, family: object) -> bool | None:
    payload = _reviewed_payload(value, family)
    if payload is None:
        return None
    if type(payload) is not GenericAlias:
        return False
    try:
        origin = object.__getattribute__(payload, "__origin__")
    except Exception:
        return False
    return origin is tuple


def _match_declaration(
    kind: object,
    family: object,
    payload: object,
    canonical_name: object,
    owner_module: object,
) -> _ReviewedGenericRowSpec | None:
    if type(kind) is not str or type(canonical_name) is not str or type(owner_module) is not str:
        return None
    if family is _ABSENT or payload is _ABSENT:
        return None
    for owner in _reviewed_vocabulary_for_verification():
        if owner.module_name != owner_module:
            continue
        owner_namespace = _module_namespace(owner.module_name)
        if owner_namespace is None:
            return None
        for row in owner.rows:
            if row.candidate_kind != kind or row.specialization_name != canonical_name:
                continue
            family_namespace = _module_namespace(row.family_module)
            if family_namespace is None:
                return None
            family_present, bound_family = _owned_lookup(family_namespace, row.family_name)
            payload_present, bound_payload = _owned_lookup(owner_namespace, row.payload_name)
            if (
                family_present
                and bound_family is family
                and payload_present
                and bound_payload is payload
            ):
                return row
        return None
    return None


def _resolve_declaration(
    kind: object,
    family: object,
    payload: object,
    canonical_name: object,
    owner_module: object,
) -> _DeclarationResolution | None:
    row = _match_declaration(kind, family, payload, canonical_name, owner_module)
    if row is None:
        return None
    for owner in _reviewed_vocabulary_for_verification():
        if owner.module_name != owner_module:
            continue
        namespace = _module_namespace(owner.module_name)
        if namespace is None:
            return None
        existing_present, existing = _owned_lookup(namespace, row.specialization_name)
        table_present, _table = _owned_lookup(namespace, owner.table_name)
        if not existing_present and not table_present:
            return _DeclarationResolution(row, False, None)
        if not existing_present or not table_present:
            return None
        resolved = _find_reviewed_specialization(family, payload)
        return _DeclarationResolution(row, True, existing) if resolved is existing else None
    return None


def _verify_complete_manifest() -> tuple[_ReviewedGenericSpecialization, ...]:
    rows = _load_reviewed_rows()
    if rows is None or tuple.__len__(rows) != 51:
        raise RuntimeError("private generic manifest unavailable")
    return rows
