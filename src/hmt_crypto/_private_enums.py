"""Neutral, fail-closed exact-enum identity and schema machinery."""

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum, EnumType, ReprEnum, StrEnum
from types import MappingProxyType
from typing import Any, Generic, TypeVar, cast

from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler, ValidationError
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import (
    CoreSchema,
    InitErrorDetails,
    PydanticCustomError,
    PydanticSerializationError,
    core_schema,
)

E = TypeVar("E", bound=StrEnum)
_ExactEnumEntries = tuple[tuple[StrEnum, str], ...]
_ExactEnumManifest = tuple[tuple[str, str], ...]
_ExactEnumRegistration = tuple[type[StrEnum], _ExactEnumEntries, _ExactEnumManifest]
_EXACT_ENUM_REGISTRY: tuple[_ExactEnumRegistration, ...] = ()
_EXACT_ENUM_FIELD_REGISTRY: tuple[tuple[object, type[StrEnum], _ExactEnumEntries], ...] = ()


def _registered_enum(enum_type: object) -> _ExactEnumRegistration | None:
    for registered_type, entries, manifest in _EXACT_ENUM_REGISTRY:
        if enum_type is registered_type:
            return registered_type, entries, manifest
    return None


def _enum_source_error() -> TypeError:
    return TypeError("invalid exact enum source class")


def _exact_manifest(value: object) -> _ExactEnumManifest:
    if type(value) is not tuple:
        raise _enum_source_error()
    for entry in value:
        if (
            type(entry) is not tuple
            or len(entry) != 2
            or type(entry[0]) is not str
            or type(entry[1]) is not str
        ):
            raise _enum_source_error()
    return cast(_ExactEnumManifest, value)


def _exact_named_value(entries: tuple[tuple[str, object], ...], name: str) -> object | None:
    for candidate_name, candidate_value in entries:
        if candidate_name == name:
            return candidate_value
    return None


def _is_exact_enum_mro(enum_type: type[StrEnum], enum_mro: tuple[object, ...]) -> bool:
    standard_tail = (StrEnum, str, ReprEnum, Enum, object)
    if len(enum_mro) == 1 + len(standard_tail):
        return enum_mro[0] is enum_type and all(
            actual is expected for actual, expected in zip(enum_mro[1:], standard_tail, strict=True)
        )
    exact_base = globals().get("ExactStrEnum")
    if len(enum_mro) == 2 + len(standard_tail):
        return (
            enum_mro[0] is enum_type
            and enum_mro[1] is exact_base
            and all(
                actual is expected
                for actual, expected in zip(enum_mro[2:], standard_tail, strict=True)
            )
        )
    return False


def _snapshot_enum(
    enum_type: type[StrEnum],
) -> tuple[_ExactEnumEntries, _ExactEnumManifest]:
    if type(enum_type) is not EnumType:
        raise _enum_source_error()
    try:
        enum_mro = type.__getattribute__(enum_type, "__mro__")
        class_namespace = type.__getattribute__(enum_type, "__dict__")
    except Exception:
        raise _enum_source_error() from None
    if (
        type(enum_mro) is not tuple
        or not _is_exact_enum_mro(enum_type, enum_mro)
        or type(class_namespace) is not MappingProxyType
    ):
        raise _enum_source_error()

    class_items = tuple(class_namespace.items())
    for name, _value in class_items:
        if type(name) is not str:
            raise _enum_source_error()
    if any(name in {"_name_", "_value_", "__dict__"} for name, _value in class_items):
        raise _enum_source_error()
    member_names = _exact_named_value(class_items, "_member_names_")
    member_map = _exact_named_value(class_items, "_member_map_")
    value_map = _exact_named_value(class_items, "_value2member_map_")
    if (
        type(member_names) is not list
        or type(member_map) is not dict
        or type(value_map) is not dict
    ):
        raise _enum_source_error()

    member_map_items = tuple(member_map.items())
    value_map_items = tuple(value_map.items())
    for name, _value in member_map_items:
        if type(name) is not str:
            raise _enum_source_error()
    for index, (_name, member) in enumerate(member_map_items):
        if type(member) is not enum_type:
            raise _enum_source_error()
        for _prior_name, prior_member in member_map_items[:index]:
            if member is prior_member:
                raise ValueError("exact enum aliases are forbidden")
    for literal, member in value_map_items:
        if type(literal) is not str or type(member) is not enum_type:
            raise _enum_source_error()
    if not (len(member_names) == len(member_map_items) == len(value_map_items)):
        raise _enum_source_error()

    entries: list[tuple[StrEnum, str]] = []
    manifest: list[tuple[str, str]] = []
    for index, name in enumerate(member_names):
        if type(name) is not str:
            raise _enum_source_error()
        member = _exact_named_value(member_map_items, name)
        if type(member) is not enum_type:
            raise _enum_source_error()
        if _exact_named_value(class_items, name) is not member:
            raise _enum_source_error()
        try:
            member_namespace = object.__getattribute__(member, "__dict__")
            literal = str.__str__(member)
        except Exception:
            raise _enum_source_error() from None
        if type(member_namespace) is not dict:
            raise _enum_source_error()
        member_items = tuple(member_namespace.items())
        for member_key, _member_value in member_items:
            if type(member_key) is not str:
                raise _enum_source_error()
        stored_name = _exact_named_value(member_items, "_name_")
        stored_value = _exact_named_value(member_items, "_value_")
        stored_class = _exact_named_value(member_items, "__objclass__")
        sort_order = _exact_named_value(member_items, "_sort_order_")
        if (
            type(stored_name) is not str
            or stored_name != name
            or type(stored_value) is not str
            or type(literal) is not str
            or stored_value != literal
            or stored_class is not enum_type
            or type(sort_order) is not int
            or sort_order != index
        ):
            raise _enum_source_error()
        value_member = _exact_named_value(value_map_items, literal)
        if value_member is not member:
            raise _enum_source_error()
        for registered_member, registered_literal in entries:
            if member is registered_member:
                raise ValueError("exact enum aliases are forbidden")
            if literal == registered_literal:
                raise ValueError("duplicate exact enum literals are forbidden")
        entries.append((member, literal))
        manifest.append((name, literal))
    return tuple(entries), tuple(manifest)


def _capture_native_exact_enum(enum_type: object) -> _ExactEnumEntries:
    global _EXACT_ENUM_REGISTRY

    registered = _registered_enum(enum_type)
    if registered is not None:
        return registered[1]
    if type(enum_type) is not EnumType:
        raise _enum_source_error()
    owned_type = cast(type[StrEnum], enum_type)
    entries, manifest = _snapshot_enum(owned_type)
    _EXACT_ENUM_REGISTRY = (*_EXACT_ENUM_REGISTRY, (owned_type, entries, manifest))
    return entries


def _capture_foreign_exact_enum(enum_type: object, expected_manifest: object) -> None:
    """Capture one ordinary source enum and its exact canonical identities."""
    global _EXACT_ENUM_REGISTRY

    if type(enum_type) is not EnumType:
        raise _enum_source_error()
    manifest = _exact_manifest(expected_manifest)
    registered = _registered_enum(enum_type)
    if registered is not None:
        if manifest != registered[2]:
            raise _enum_source_error()
        return
    owned_type = cast(type[StrEnum], enum_type)
    entries, observed_manifest = _snapshot_enum(owned_type)
    if manifest != observed_manifest:
        raise _enum_source_error()
    _EXACT_ENUM_REGISTRY = (*_EXACT_ENUM_REGISTRY, (owned_type, entries, manifest))


def _registered_enum_field(
    metadata: object,
) -> tuple[type[StrEnum], _ExactEnumEntries] | None:
    for registered_metadata, enum_type, entries in _EXACT_ENUM_FIELD_REGISTRY:
        if metadata is registered_metadata:
            return enum_type, entries
    return None


def _register_enum_field(
    metadata: object, enum_type: object
) -> tuple[type[StrEnum], _ExactEnumEntries]:
    global _EXACT_ENUM_FIELD_REGISTRY

    registered = _registered_enum_field(metadata)
    if registered is not None:
        if enum_type is not registered[0]:
            raise TypeError("exact enum metadata registration is inconsistent")
        return registered
    registered_enum = _registered_enum(enum_type)
    if registered_enum is None:
        raise TypeError("exact enum source is not captured")
    enum_class, entries, _manifest = registered_enum
    _EXACT_ENUM_FIELD_REGISTRY = (
        *_EXACT_ENUM_FIELD_REGISTRY,
        (metadata, enum_class, entries),
    )
    return enum_class, entries


def _exact_enum_json_schema(
    entries: _ExactEnumEntries, handler: GetJsonSchemaHandler
) -> JsonSchemaValue:
    if handler.mode == "validation" or not entries:
        return {"not": {}}
    return {"type": "string", "enum": [literal for _member, literal in entries]}


def _exact_enum_schema(enum_type: type[StrEnum], entries: _ExactEnumEntries) -> CoreSchema:
    def validation_error() -> ValidationError:
        details = cast(
            list[InitErrorDetails],
            [
                {
                    "type": PydanticCustomError(
                        "exact_enum_member",
                        "value must be a canonical exact enum member",
                    ),
                    "loc": (),
                    "input": None,
                }
            ],
        )
        return ValidationError.from_exception_data("ExactEnum", details, hide_input=True)

    def canonical_member(
        value: object, handler: core_schema.ValidatorFunctionWrapHandler
    ) -> StrEnum:
        del handler
        if type(value) is enum_type:
            for member, _literal in entries:
                if value is member:
                    return member
        raise validation_error() from None

    def serialize_member(value: object, info: core_schema.SerializationInfo) -> object:
        if type(value) is enum_type:
            for member, literal in entries:
                if value is member:
                    return literal if info.mode == "json" else member
        raise PydanticSerializationError("invalid canonical exact enum member")

    return core_schema.no_info_wrap_validator_function(
        canonical_member,
        core_schema.is_instance_schema(enum_type),
        json_schema_input_schema=core_schema.str_schema(),
        serialization=core_schema.plain_serializer_function_ser_schema(
            serialize_member,
            info_arg=True,
            return_schema=core_schema.str_schema(),
        ),
    )


class ExactStrEnum(StrEnum):
    """String enum with canonical identity validation and frozen literals."""

    __module__ = "hmt_crypto.domain.private_primitives"

    @property
    def value(self) -> str:
        """Return the frozen literal only for a canonical member identity."""
        return exact_enum_literal(type(self), self)

    @classmethod
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _capture_native_exact_enum(cls)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Build one fail-closed schema from the class's immutable snapshot."""
        del source_type, handler
        registered = _registered_enum(cls)
        entries = () if registered is None else registered[1]
        return _exact_enum_schema(cls, entries)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """Describe the closed output literals and absence of raw JSON input."""
        del schema
        registered = _registered_enum(cls)
        entries = () if registered is None else registered[1]
        return _exact_enum_json_schema(entries, handler)


@dataclass(frozen=True, slots=True, eq=False)
class ExactEnumField(Generic[E]):
    """Frozen Pydantic metadata for one source-registered foreign string enum."""

    __module__ = "hmt_crypto.domain.private_primitives"

    enum_type: type[E]
    _entries: _ExactEnumEntries = dataclass_field(init=False, repr=False)

    def __post_init__(self) -> None:
        enum_type = object.__getattribute__(self, "enum_type")
        _registered_type, entries = _register_enum_field(self, enum_type)
        object.__setattr__(self, "_entries", entries)

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """Reconstruct through admission so unpickled metadata is registered."""
        registered = _registered_enum_field(self)
        if registered is None:
            raise TypeError("exact enum metadata is not source registered")
        return ExactEnumField, (registered[0],)

    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Build an exact schema only for the captured enum class identity."""
        del handler
        registered = _registered_enum_field(self)
        if registered is None or source_type is not registered[0]:
            raise TypeError("exact enum metadata requires its captured enum class")
        return _exact_enum_schema(registered[0], registered[1])

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        """Describe only the identity-bound enum metadata snapshot."""
        del schema
        registered = _registered_enum_field(self)
        if registered is None:
            raise TypeError("exact enum metadata is not source registered")
        return _exact_enum_json_schema(registered[1], handler)


def _require_registered_enum(
    enum_type: object,
) -> tuple[type[StrEnum], _ExactEnumEntries]:
    registered = _registered_enum(enum_type)
    if registered is None:
        raise ValueError("enum type is not source registered")
    return registered[0], registered[1]


def exact_enum_literal(enum_type: type[E], value: object) -> str:
    """Return the frozen literal for one exact canonical enum member."""
    registered_type, entries = _require_registered_enum(enum_type)
    if type(value) is registered_type:
        for member, literal in entries:
            if value is member:
                return literal
    raise ValueError("value is not a canonical exact enum member")


exact_enum_literal.__module__ = "hmt_crypto.domain.private_primitives"


def exact_enum_member_for_literal(enum_type: type[E], literal: object) -> E | None:
    """Find a canonical member by exact frozen string using a linear scan."""
    _registered_type, entries = _require_registered_enum(enum_type)
    if type(literal) is not str:
        return None
    for member, registered_literal in entries:
        if literal == registered_literal:
            return cast(E, member)
    return None


exact_enum_member_for_literal.__module__ = "hmt_crypto.domain.private_primitives"
