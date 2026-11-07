"""Stable construction-source identity proof for reviewed private generics."""

from __future__ import annotations

import sys
from enum import EnumType
from types import GenericAlias, MappingProxyType, ModuleType
from typing import Annotated, Generic, get_origin

from pydantic import BaseModel

from hmt_crypto.domain._private_generic_manifest import (
    _reviewed_vocabulary_for_verification,
)
from hmt_crypto.domain._private_generic_manifest import (
    _ReviewedGenericOwnerSpec as _Owner,
)
from hmt_crypto.domain._private_generic_manifest import (
    _ReviewedGenericRowSpec as _Row,
)
from hmt_crypto.domain._private_generic_provenance import (
    _GENERIC_SOURCE_MODULES,
    _alias_dependencies_for,
    _family_source_for,
    _payload_source_for,
)
from hmt_crypto.domain._private_generic_provenance import (
    _FamilySource as _Family,
)
from hmt_crypto.domain._private_generic_provenance import (
    _PayloadSource as _Payload,
)
from hmt_crypto.domain.private_primitives import ExactStrEnum, PrivateBoundaryModel

_CORE = "hmt_crypto.domain._private_generic_core"
_MODEL_BOUNDARY = "_ClosedPrivateGenericModel"
_ALIAS_BOUNDARY = "_RequireClosedGenericAlias"
_ALIAS_METACLASS = "_ClosedAliasMeta"
_ALIAS_CANDIDATE = "_ClosedPrivateAlias"
_TYPE_FIELDS = ("__bases__", "__mro__", "__module__", "__name__", "__qualname__")
_ANNOTATED_ALIAS_TYPE = type(Annotated[object, "private-source-marker"])
_MODEL_METACLASS = type(PrivateBoundaryModel)
_ROWS = tuple(
    (owner, row) for owner in _reviewed_vocabulary_for_verification() for row in owner.rows
)
_Snapshots = dict[str, object]
_Values = tuple[object, object]


def _exact_string_dict(value: object) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    try:
        owned = dict.copy(value)
    except RuntimeError:
        return None
    return None if any(type(key) is not str for key in owned) else owned


def _owned_lookup(mapping: dict[str, object], key: str) -> tuple[bool, object]:
    if dict.__contains__(mapping, key):
        return True, dict.__getitem__(mapping, key)
    return False, None


def _module_namespace(module_name: str) -> dict[str, object] | None:
    snapshots = _snapshots((module_name,)) if type(module_name) is str else None
    if snapshots is None:
        return None
    present, namespace = _owned_lookup(snapshots, module_name)
    return namespace if present and type(namespace) is dict else None


def _snapshots(names: tuple[str, ...] = _GENERIC_SOURCE_MODULES) -> _Snapshots | None:
    modules = _exact_string_dict(sys.modules)
    if modules is None:
        return None
    snapshots: _Snapshots = {}
    try:
        for name in names:
            present, module = _owned_lookup(modules, name)
            if not present:
                continue
            if type(module) is not ModuleType:
                return None
            namespace = _exact_string_dict(object.__getattribute__(module, "__dict__"))
            if namespace is None:
                return None
            name_present, actual_name = _owned_lookup(namespace, "__name__")
            if not name_present or type(actual_name) is not str or actual_name != name:
                return None
            dict.__setitem__(snapshots, name, namespace)
    except Exception:
        return None
    return snapshots


def _tuple_is(value: object, expected: tuple[object, ...]) -> bool:
    return (
        type(value) is tuple
        and tuple.__len__(value) == len(expected)
        and all(tuple.__getitem__(value, index) is item for index, item in enumerate(expected))
    )


def _type_state(
    value: object, expected_module: str, expected_name: str
) -> tuple[object, object] | None:
    try:
        bases, mro, module, name, qualname = tuple(
            type.__getattribute__(value, field) for field in _TYPE_FIELDS
        )
    except Exception:
        return None
    if type(module) is not str or type(name) is not str or type(qualname) is not str:
        return None
    if module != expected_module or name != expected_name or qualname != expected_name:
        return None
    return bases, mro


def _fields(mapping: dict[str, object], *names: str) -> tuple[object, ...] | None:
    states = tuple(_owned_lookup(mapping, name) for name in names)
    return tuple(state[1] for state in states) if all(state[0] for state in states) else None


def _class_shape(
    value: object,
    metaclass: object,
    bases: object,
    mro: object,
    direct: tuple[object, ...],
    suffix: tuple[object, ...],
) -> bool:
    return (
        type(value) is metaclass and _tuple_is(bases, direct) and _tuple_is(mro, (value, *suffix))
    )


class _SourceProof:
    __slots__ = ("snapshots",)

    def __init__(self, snapshots: _Snapshots) -> None:
        self.snapshots = snapshots

    def bound(self, module: str, name: str) -> tuple[bool, object]:
        present, namespace = _owned_lookup(self.snapshots, module)
        if not present or type(namespace) is not dict:
            return False, None
        return _owned_lookup(namespace, name)

    def same(self, value: object, *locations: tuple[str, str]) -> bool:
        for location in locations:
            present, bound = self.bound(*location)
            if not present or bound is not value:
                return False
        return True

    def family_shape(self, value: object, source: _Family) -> bool:
        state = _type_state(value, source.public_module, source.public_name)
        boundary_name = _MODEL_BOUNDARY if source.kind == "MODEL" else _ALIAS_BOUNDARY
        boundary = self.bound(_CORE, boundary_name)
        if state is None or not boundary[0]:
            return False
        bases, mro = state
        metaclass: object
        suffix: tuple[object, ...]
        if source.kind == "MODEL":
            metaclass = _MODEL_METACLASS
            suffix = (boundary[1], PrivateBoundaryModel, BaseModel, Generic, object)
        else:
            present, metaclass = self.bound(_CORE, _ALIAS_METACLASS)
            if not present:
                return False
            suffix = (boundary[1], Generic, object)
        return _class_shape(value, metaclass, bases, mro, (boundary[1], Generic), suffix)

    def plain_shape(self, value: object, module: str, name: str, shape: str) -> bool:
        if shape == "ANNOTATED_ALIAS":
            return type(value) is _ANNOTATED_ALIAS_TYPE and get_origin(value) is Annotated
        if shape == "TUPLE_ALIAS":
            return type(value) is GenericAlias
        state = _type_state(value, module, name)
        if state is None:
            return False
        bases, mro = state
        metaclass: object
        base: type[object]
        if shape == "ENUM":
            metaclass, base = EnumType, ExactStrEnum
        elif shape == "MODEL":
            metaclass, base = _MODEL_METACLASS, PrivateBoundaryModel
        else:
            return False
        return _class_shape(value, metaclass, bases, mro, (base,), base.__mro__)

    def values(self, owner: _Owner, row: _Row, supplied: _Values | None = None) -> _Values | None:
        family_source = _family_source_for(row.family_module, row.family_name)
        payload_source = _payload_source_for(row.payload_name)
        if family_source is None or payload_source is None:
            return None
        if supplied is None:
            family_state = self.bound(family_source.source_module, family_source.source_name)
            payload_module = (
                payload_source.source_module
                if payload_source.kind == "SOURCE"
                else payload_source.public_module
            )
            if type(payload_module) is not str:
                return None
            payload_state = self.bound(payload_module, payload_source.public_name)
            if not family_state[0] or not payload_state[0]:
                return None
            family, payload = family_state[1], payload_state[1]
        else:
            family, payload = supplied
        if not self.same(
            family,
            (family_source.source_module, family_source.source_name),
            (family_source.public_module, family_source.public_name),
            (owner.module_name, family_source.public_name),
        ) or not self.family_shape(family, family_source):
            return None
        return (family, payload) if self.payload(owner, payload, payload_source) else None

    def payload(self, owner: _Owner, value: object, source: _Payload) -> bool:
        if source.kind == "SOURCE":
            return (
                type(source.source_module) is str
                and type(source.source_name) is str
                and self.same(
                    value,
                    (source.source_module, source.source_name),
                    (source.public_module, source.public_name),
                    (owner.module_name, source.public_name),
                )
                and self.plain_shape(value, source.public_module, source.public_name, source.shape)
            )
        index = source.prior_global_row
        linked = _ROWS[index] if type(index) is int and 0 <= index < len(_ROWS) else None
        if (
            linked is None
            or type(source.prior_name) is not str
            or source.prior_name != linked[1].specialization_name
            or source.shape
            != ("PRIOR_MODEL" if linked[1].candidate_kind == "model" else "PRIOR_ALIAS")
        ):
            return False
        prior_values = self.values(*linked)
        return (
            self.same(
                value,
                (source.public_module, source.prior_name),
                (owner.module_name, source.public_name),
            )
            and prior_values is not None
            and self.candidate(*linked, value, prior_values)
        )

    def candidate(self, owner: _Owner, row: _Row, value: object, identities: _Values) -> bool:
        family, payload = identities
        present, public = self.bound(owner.module_name, row.specialization_name)
        state = _type_state(value, owner.module_name, row.specialization_name)
        if not present or public is not value or state is None:
            return False
        bases, _mro = state
        try:
            namespace = _exact_string_dict(
                MappingProxyType.copy(type.__getattribute__(value, "__dict__"))
            )
        except Exception:
            return False
        if namespace is None:
            return False
        if row.candidate_kind == "model":
            candidate_fields = _fields(namespace, "__pydantic_generic_metadata__")
            metadata = _exact_string_dict(candidate_fields[0]) if candidate_fields else None
            if metadata is None:
                return False
            metadata_fields = _fields(metadata, "origin", "args", "parameters")
            if metadata_fields is None:
                return False
            origin, arguments, parameters = metadata_fields
            return (
                type(value) is _MODEL_METACLASS
                and _tuple_is(bases, (family,))
                and origin is family
                and _tuple_is(arguments, (payload,))
                and _tuple_is(parameters, ())
            )
        base = self.bound(_CORE, _ALIAS_CANDIDATE)
        alias_fields = _fields(
            namespace, "__private_family__", "__private_payload__", "__private_tags__"
        )
        if alias_fields is None:
            return False
        actual_family, actual_payload, tags = alias_fields
        return (
            type(value) is type
            and base[0]
            and _tuple_is(bases, (base[1],))
            and actual_family is family
            and actual_payload is payload
            and self.tags(tags, self.dependencies(row, payload, owner))
        )

    def dependencies(
        self, row: _Row, payload: object, owner: _Owner
    ) -> tuple[tuple[str, object], ...] | None:
        records = _alias_dependencies_for(row.family_module, row.family_name)
        if records is None:
            return None
        result: list[tuple[str, object]] = []
        for record in records:
            if record.kind == "SOURCE":
                if (
                    type(record.source_module) is not str
                    or type(record.source_name) is not str
                    or type(record.public_module) is not str
                    or type(record.public_name) is not str
                ):
                    return None
                state = self.bound(record.source_module, record.source_name)
                branch = state[1]
                if (
                    not state[0]
                    or not self.same(
                        branch,
                        (record.source_module, record.source_name),
                        (record.public_module, record.public_name),
                    )
                    or not self.plain_shape(
                        branch, record.public_module, record.public_name, "MODEL"
                    )
                ):
                    return None
            else:
                branch = None
                for prior in owner.rows:
                    if prior is row:
                        break
                    if (
                        prior.family_module == record.prior_family_module
                        and prior.family_name == record.prior_family_name
                        and prior.payload_name == row.payload_name
                    ):
                        values = self.values(owner, prior)
                        present, candidate = self.bound(
                            owner.module_name, prior.specialization_name
                        )
                        if (
                            values is not None
                            and values[1] is payload
                            and present
                            and self.candidate(owner, prior, candidate, values)
                        ):
                            branch = candidate
                        break
                if branch is None:
                    return None
            result.append((record.literal, branch))
        return tuple(result)

    @staticmethod
    def tags(value: object, expected: tuple[tuple[str, object], ...] | None) -> bool:
        if type(value) is not tuple or expected is None or tuple.__len__(value) != len(expected):
            return False

        def valid(index: int) -> bool:
            tag = tuple.__getitem__(value, index)
            literal, branch = tuple.__getitem__(expected, index)
            return (
                type(tag) is tuple
                and tuple.__len__(tag) == 2
                and type(tuple.__getitem__(tag, 0)) is str
                and tuple.__getitem__(tag, 0) == literal
                and tuple.__getitem__(tag, 1) is branch
            )

        return all(valid(index) for index in range(len(expected)))


def _context(row: _Row, owner_name: object) -> tuple[_Owner, _SourceProof] | None:
    if type(row) is not _Row or type(owner_name) is not str:
        return None
    state = next((item for item in _ROWS if item[1] is row), None)
    if state is None or state[0].module_name != owner_name or (snapshots := _snapshots()) is None:
        return None
    return state[0], _SourceProof(snapshots)


def _verify_pre_table_source(
    row: _Row, family: object, payload: object, row_owner_module: str
) -> bool:
    context = _context(row, row_owner_module)
    return context is not None and context[1].values(context[0], row, (family, payload)) is not None


def _verify_loaded_row_source(owner: _Owner, row: _Row, family: object, payload: object) -> bool:
    context = _context(row, owner.module_name) if type(owner) is _Owner else None
    return (
        context is not None
        and context[0] is owner
        and context[1].values(owner, row, (family, payload)) is not None
    )


def _source_alias_dependencies(
    row: _Row, payload: object, row_owner_module: str
) -> tuple[tuple[str, object], ...] | None:
    context = _context(row, row_owner_module)
    if context is None:
        return None
    identities = context[1].values(context[0], row)
    if identities is None or identities[1] is not payload:
        return None
    return context[1].dependencies(row, payload, context[0])
