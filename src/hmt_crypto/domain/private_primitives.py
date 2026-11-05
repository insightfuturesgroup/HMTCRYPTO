"""Owned, fail-closed primitives for money-moving private exchange data."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum, EnumType, ReprEnum, StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Generic, Literal, Self, TypeVar, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    ModelWrapValidatorHandler,
    ValidationError,
    model_validator,
)
from pydantic.config import ExtraValues
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
_EXACT_ENUM_REGISTRY: tuple[tuple[type[StrEnum], _ExactEnumEntries], ...] = ()
_EXACT_ENUM_FIELD_REGISTRY: tuple[tuple[object, type[StrEnum], _ExactEnumEntries], ...] = ()


def _registered_enum(
    enum_type: object,
) -> tuple[type[StrEnum], _ExactEnumEntries] | None:
    for registered_type, entries in _EXACT_ENUM_REGISTRY:
        if enum_type is registered_type:
            return registered_type, entries
    return None


def _enum_source_error() -> TypeError:
    return TypeError("invalid exact enum source class")


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


def _snapshot_enum(enum_type: type[StrEnum]) -> _ExactEnumEntries:
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
    return tuple(entries)


def _register_enum(enum_type: object) -> _ExactEnumEntries:
    global _EXACT_ENUM_REGISTRY

    registered = _registered_enum(enum_type)
    if registered is not None:
        return registered[1]
    if type(enum_type) is not EnumType:
        raise TypeError("exact enum registration requires a standard enum class")
    owned_type = cast(type[StrEnum], enum_type)
    entries = _snapshot_enum(owned_type)
    _EXACT_ENUM_REGISTRY = (*_EXACT_ENUM_REGISTRY, (owned_type, entries))
    return entries


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
    entries = _register_enum(enum_type)
    registered_enum = cast(type[StrEnum], enum_type)
    _EXACT_ENUM_FIELD_REGISTRY = (
        *_EXACT_ENUM_FIELD_REGISTRY,
        (metadata, registered_enum, entries),
    )
    return registered_enum, entries


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

    @property
    def value(self) -> str:
        """Return the frozen literal only for a canonical member identity."""
        return exact_enum_literal(type(self), self)

    @classmethod
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _register_enum(cls)

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

    enum_type: type[E]
    _entries: _ExactEnumEntries = dataclass_field(init=False, repr=False)

    def __post_init__(self) -> None:
        enum_type = object.__getattribute__(self, "enum_type")
        _registered_type, entries = _register_enum_field(self, enum_type)
        object.__setattr__(self, "_entries", entries)

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
    return registered


def exact_enum_literal(enum_type: type[E], value: object) -> str:
    """Return the frozen literal for one exact canonical enum member."""
    registered_type, entries = _require_registered_enum(enum_type)
    if type(value) is registered_type:
        for member, literal in entries:
            if value is member:
                return literal
    raise ValueError("value is not a canonical exact enum member")


def exact_enum_member_for_literal(enum_type: type[E], literal: object) -> E | None:
    """Find a canonical member by exact frozen string using a linear scan."""
    _registered_type, entries = _require_registered_enum(enum_type)
    if type(literal) is not str:
        return None
    for member, registered_literal in entries:
        if literal == registered_literal:
            return cast(E, member)
    return None


_MAX_I63 = 2**63 - 1
_MAX_PID = 2**31 - 1
_MAX_UUID_INT = 2**128 - 1
_UUID_INT_SLOT = vars(UUID)["int"]
_MIN_PRIVATE_TIME = datetime(2000, 1, 1, tzinfo=UTC)
_MAX_PRIVATE_TIME = datetime(2100, 1, 1, tzinfo=UTC)
_MIN_EPOCH_MICROSECONDS = 946_684_800_000_000
_MAX_EPOCH_MICROSECONDS = 4_102_444_800_000_000
_INTEGRITY_GUARDED_ATTRIBUTES = (
    "__copy__",
    "__deepcopy__",
    "model_copy",
    "model_dump",
    "model_dump_json",
)

_EXTERNAL_USER = re.compile(r"[1-9][0-9]{0,18}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_OMS_CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{1,32}\Z", re.ASCII)
_OBSERVED_CLIENT_ID = re.compile(r"[A-Za-z0-9._:-]{1,32}\Z", re.ASCII)
_PRODUCT_SYMBOL = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z", re.ASCII)
_ASSET_SYMBOL = re.compile(r"[A-Za-z0-9._:-]{1,32}\Z", re.ASCII)
_UNKNOWN_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z", re.ASCII)
_DECIMAL_TEXT = re.compile(
    r"-?(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,18})?\Z",
    re.ASCII,
)
_EPOCH_MICROSECONDS = re.compile(r"[1-9][0-9]{0,18}\Z", re.ASCII)
_POSITION_ISO = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?"
    r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))\Z",
    re.ASCII,
)


class PrivateBoundaryModel(BaseModel):
    """Immutable strict model that owns inputs and hides them from failures."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )

    def __getattribute__(self, name: str) -> Any:
        """Check model state before instance lookup can compare hostile keys."""
        if type(name) is str and name in _INTEGRITY_GUARDED_ATTRIBUTES:
            instance_type = type(self)
            require_owned = type.__getattribute__(instance_type, "_require_owned_instance")
            require_owned(self)
        return super().__getattribute__(name)

    @model_validator(mode="wrap")
    @classmethod
    def remove_structured_validation_inputs(
        cls, values: Any, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        """Remove raw caller or wire values from structured Pydantic errors."""
        if type(values) is dict:
            cls._require_exact_string_keys(values)
        elif type(values) is not cls:
            raise cls._safe_root_error() from None
        else:
            cls._require_owned_instance(values)
        try:
            result = handler(values)
        except ValidationError as error:
            raise cls._without_inputs(error) from None
        cls._require_owned_instance(result)
        return result

    @classmethod
    def _require_validation_controls(
        cls,
        *,
        strict: object,
        extra: object,
        from_attributes: object,
        context: object,
        by_alias: object,
        by_name: object,
    ) -> None:
        if not (
            (strict is None or type(strict) is bool and strict)
            and (extra is None or type(extra) is str and extra == "forbid")
            and (from_attributes is None or type(from_attributes) is bool and not from_attributes)
            and context is None
            and by_alias is None
            and by_name is None
        ):
            raise cls._safe_error(
                "private_model_controls", "invalid private model controls"
            ) from None

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Validate with invariant strict, extra-forbid owned controls."""
        cls._require_validation_controls(
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
        try:
            return super().model_validate(
                obj,
                strict=True,
                extra="forbid",
                from_attributes=False,
                context=None,
                by_alias=None,
                by_name=None,
            )
        except ValidationError as error:
            raise cls._without_inputs(error) from None

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Validate exact JSON buffers without retaining malformed raw input."""
        cls._require_validation_controls(
            strict=strict,
            extra=extra,
            from_attributes=None,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
        if type(json_data) not in (str, bytes, bytearray):
            raise cls._safe_root_error() from None
        try:
            return super().model_validate_json(
                json_data,
                strict=True,
                extra="forbid",
                context=None,
                by_alias=None,
                by_name=None,
            )
        except ValidationError as error:
            raise cls._without_inputs(error) from None

    @classmethod
    def model_validate_strings(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Validate only an owned exact string-keyed dictionary."""
        cls._require_validation_controls(
            strict=strict,
            extra=extra,
            from_attributes=None,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
        if type(obj) is not dict:
            raise cls._safe_root_error() from None
        cls._require_exact_string_keys(obj)
        try:
            return super().model_validate_strings(
                obj,
                strict=True,
                extra="forbid",
                context=None,
                by_alias=None,
                by_name=None,
            )
        except ValidationError as error:
            raise cls._without_inputs(error) from None

    @classmethod
    def _require_exact_string_keys(cls, values: dict[object, object]) -> None:
        for key in values:
            if type(key) is not str:
                raise cls._safe_root_error() from None

    @classmethod
    def _require_owned_instance(cls, value: object) -> None:
        if type(value) is not cls:
            raise cls._safe_state_error() from None
        try:
            state = object.__getattribute__(value, "__dict__")
            fields_set = object.__getattribute__(value, "__pydantic_fields_set__")
            extra_state = object.__getattribute__(value, "__pydantic_extra__")
            private_state = object.__getattribute__(value, "__pydantic_private__")
            declared_fields = type.__getattribute__(cls, "__pydantic_fields__")
            declared_private = type.__getattribute__(cls, "__private_attributes__")
        except Exception:
            raise cls._safe_state_error() from None

        if type(declared_fields) is not dict or type(declared_private) is not dict:
            raise cls._safe_state_error() from None
        field_names = cls._exact_declared_names(declared_fields)
        private_names = cls._exact_declared_names(declared_private)

        if type(state) is not dict or len(state) != len(field_names):
            raise cls._safe_state_error() from None
        for key in state:
            if type(key) is not str or not cls._is_declared_name(key, field_names):
                raise cls._safe_state_error() from None

        if type(fields_set) is not set:
            raise cls._safe_state_error() from None
        for key in fields_set:
            if type(key) is not str or not cls._is_declared_name(key, field_names):
                raise cls._safe_state_error() from None
        for name, field in declared_fields.items():
            if field.is_required() and name not in fields_set:
                raise cls._safe_state_error() from None

        if extra_state is not None:
            raise cls._safe_state_error() from None
        if private_names:
            if type(private_state) is not dict:
                raise cls._safe_state_error() from None
            for key in private_state:
                if type(key) is not str or not cls._is_declared_name(key, private_names):
                    raise cls._safe_state_error() from None
        elif private_state is not None:
            raise cls._safe_state_error() from None

    @classmethod
    def _exact_declared_names(cls, declarations: dict[str, object]) -> tuple[str, ...]:
        names: list[str] = []
        for name in declarations:
            if type(name) is not str:
                raise cls._safe_state_error() from None
            names.append(name)
        return tuple(names)

    @staticmethod
    def _is_declared_name(candidate: str, declared_names: tuple[str, ...]) -> bool:
        return any(candidate == name for name in declared_names)

    @classmethod
    def _without_inputs(cls, error: ValidationError) -> ValidationError:
        del error
        return cls._safe_error("private_model_value", "invalid private model value")

    @classmethod
    def _safe_error(cls, code: str, message: str) -> ValidationError:
        details = cast(
            list[InitErrorDetails],
            [
                {
                    "type": PydanticCustomError(code, message),
                    "loc": (),
                    "input": None,
                }
            ],
        )
        return ValidationError.from_exception_data(cls.__name__, details, hide_input=True)

    @classmethod
    def _safe_root_error(cls) -> ValidationError:
        return cls._safe_error("private_model_root", "invalid private model root")

    @classmethod
    def _safe_state_error(cls) -> ValidationError:
        return cls._safe_error("private_model_state", "invalid private model state")

    @classmethod
    def _revalidate_owned_instance(cls, value: Self) -> Self:
        cls._require_owned_instance(value)
        return cls.model_validate(value, strict=True)

    @staticmethod
    def _serialization_controls_error() -> TypeError:
        return TypeError("invalid private serialization controls")

    @classmethod
    def _require_common_serialization_controls(
        cls,
        *,
        include: object,
        exclude: object,
        context: object,
        by_alias: object,
        exclude_unset: object,
        exclude_defaults: object,
        exclude_none: object,
        exclude_computed_fields: object,
        round_trip: object,
        warnings: object,
        fallback: object,
        serialize_as_any: object,
        polymorphic_serialization: object,
    ) -> None:
        if (
            include is not None
            or exclude is not None
            or context is not None
            or fallback is not None
        ):
            raise cls._serialization_controls_error()
        if by_alias is not None and type(by_alias) is not bool:
            raise cls._serialization_controls_error()
        for flag in (
            exclude_unset,
            exclude_defaults,
            exclude_none,
            exclude_computed_fields,
            round_trip,
        ):
            if type(flag) is not bool:
                raise cls._serialization_controls_error()
        if not (
            type(warnings) is bool
            or (type(warnings) is str and warnings in ("none", "warn", "error"))
        ):
            raise cls._serialization_controls_error()
        if type(serialize_as_any) is not bool or serialize_as_any:
            raise cls._serialization_controls_error()
        if polymorphic_serialization is not None and (
            type(polymorphic_serialization) is not bool or polymorphic_serialization
        ):
            raise cls._serialization_controls_error()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Return a revalidated owned copy; updates never bypass field validation."""
        if type(deep) is not bool:
            raise type(self)._serialization_controls_error()
        clean = type(self)._revalidate_owned_instance(self)
        values = BaseModel.model_dump(
            clean,
            mode="python",
            round_trip=True,
            exclude_unset=True,
            serialize_as_any=False,
        )
        if update is not None:
            if type(update) is not dict:
                raise TypeError("model updates require an exact dictionary")
            for key in update:
                if type(key) is not str:
                    raise TypeError("model update keys require exact strings")
            values.update(update)
        return type(self).model_validate(values, strict=True)

    def __copy__(self) -> Self:
        """Return a validated copy after checking all Pydantic state containers."""
        return self.model_copy()

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Self:
        """Return a validated defensive copy without traversing unowned state."""
        del memo
        return self.model_copy(deep=True)

    def model_dump(
        self,
        *,
        mode: Literal["json", "python"] | str = "python",
        include: Any = None,
        exclude: Any = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: bool | Literal["none", "warn", "error"] = True,
        fallback: Callable[[Any], Any] | None = None,
        serialize_as_any: bool = False,
        polymorphic_serialization: bool | None = None,
    ) -> dict[str, Any]:
        """Serialize only after validating the owned model-state envelope."""
        if type(serialize_as_any) is not bool or serialize_as_any:
            raise PrivateBoundaryModel._safe_error(
                "private_model_duck_serialization",
                "private model duck serialization unavailable",
            ) from None
        if type(mode) is not str or mode not in ("python", "json"):
            raise type(self)._serialization_controls_error()
        type(self)._require_common_serialization_controls(
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=False,
            polymorphic_serialization=polymorphic_serialization,
        )
        clean = type(self)._revalidate_owned_instance(self)
        return BaseModel.model_dump(
            clean,
            mode=mode,
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=False,
            polymorphic_serialization=polymorphic_serialization,
        )

    def model_dump_json(
        self,
        *,
        indent: int | None = None,
        ensure_ascii: bool = False,
        include: Any = None,
        exclude: Any = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: bool | Literal["none", "warn", "error"] = True,
        fallback: Callable[[Any], Any] | None = None,
        serialize_as_any: bool = False,
        polymorphic_serialization: bool | None = None,
    ) -> str:
        """Encode JSON only after validating the owned model-state envelope."""
        if type(serialize_as_any) is not bool or serialize_as_any:
            raise PrivateBoundaryModel._safe_error(
                "private_model_duck_serialization",
                "private model duck serialization unavailable",
            ) from None
        if indent is not None and (type(indent) is not int or not 0 <= indent <= 8):
            raise type(self)._serialization_controls_error()
        if type(ensure_ascii) is not bool:
            raise type(self)._serialization_controls_error()
        type(self)._require_common_serialization_controls(
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=False,
            polymorphic_serialization=polymorphic_serialization,
        )
        clean = type(self)._revalidate_owned_instance(self)
        return BaseModel.model_dump_json(
            clean,
            indent=indent,
            ensure_ascii=ensure_ascii,
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=False,
            polymorphic_serialization=polymorphic_serialization,
        )


def _bounded_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError("value must be an exact bounded integer")
    return value


def _positive_i63(value: object) -> int:
    return _bounded_int(value, minimum=1, maximum=_MAX_I63)


def _nonnegative_i63(value: object) -> int:
    return _bounded_int(value, minimum=0, maximum=_MAX_I63)


def _signed_i63(value: object) -> int:
    return _bounded_int(value, minimum=-_MAX_I63, maximum=_MAX_I63)


def _positive_pid(value: object) -> int:
    return _bounded_int(value, minimum=1, maximum=_MAX_PID)


def _private_id(value: object) -> int:
    return _positive_i63(value)


def _contracts(value: object) -> int:
    return _positive_i63(value)


def _unfilled_contracts(value: object) -> int:
    return _nonnegative_i63(value)


def _signed_contracts(value: object) -> int:
    return _signed_i63(value)


def _credential_version(value: object) -> int:
    return _positive_i63(value)


def _strict_epoch(value: object) -> int:
    return _positive_i63(value)


def _strict_positive_pid(value: object) -> int:
    return _positive_pid(value)


def _strict_positive_int(value: object) -> int:
    return _positive_i63(value)


def _strict_nonnegative_int(value: object) -> int:
    return _nonnegative_i63(value)


PrivateId = Annotated[int, BeforeValidator(_private_id)]
Contracts = Annotated[int, BeforeValidator(_contracts)]
UnfilledContracts = Annotated[int, BeforeValidator(_unfilled_contracts)]
SignedContracts = Annotated[int, BeforeValidator(_signed_contracts)]
CredentialVersion = Annotated[int, BeforeValidator(_credential_version)]
StrictEpoch = Annotated[int, BeforeValidator(_strict_epoch)]
StrictPositivePid = Annotated[int, BeforeValidator(_strict_positive_pid)]
StrictPositiveInt = Annotated[int, BeforeValidator(_strict_positive_int)]
StrictNonNegativeInt = Annotated[int, BeforeValidator(_strict_nonnegative_int)]


def _exact_text(value: object, pattern: re.Pattern[str], *, label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError(f"value must be a valid {label}")
    return value


def _external_user(value: object) -> str:
    result = _exact_text(value, _EXTERNAL_USER, label="external user identifier")
    if int(result) > _MAX_I63:
        raise ValueError("value must be a valid external user identifier")
    return result


def _sha256(value: object) -> str:
    return _exact_text(value, _SHA256, label="SHA-256 digest")


def _oms_client_id(value: object) -> str:
    return _exact_text(value, _OMS_CLIENT_ID, label="OMS client order identifier")


def _observed_client_id(value: object) -> str:
    return _exact_text(value, _OBSERVED_CLIENT_ID, label="observed client order identifier")


def _product_symbol(value: object) -> str:
    return _exact_text(value, _PRODUCT_SYMBOL, label="product symbol")


def _asset_symbol(value: object) -> str:
    return _exact_text(value, _ASSET_SYMBOL, label="asset symbol")


def _unknown_token(value: object) -> str:
    return _exact_text(value, _UNKNOWN_TOKEN, label="observed enum token")


def _opaque_cursor(value: object) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 1024
        or not value.isascii()
        or any(not 0x21 <= ord(character) <= 0x7E for character in value)
    ):
        raise ValueError("value must be an opaque visible-ASCII cursor")
    return value


def _bounded_diagnostic(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= 256 or not value.isprintable():
        raise ValueError("value must be bounded printable diagnostic text")
    return value


ExternalUserText = Annotated[str, BeforeValidator(_external_user)]
Sha256Hex = Annotated[str, BeforeValidator(_sha256)]
OmsClientOrderId = Annotated[str, BeforeValidator(_oms_client_id)]
ObservedDeltaClientOrderId = Annotated[str, BeforeValidator(_observed_client_id)]
ProductSymbol = Annotated[str, BeforeValidator(_product_symbol)]
AssetSymbol = Annotated[str, BeforeValidator(_asset_symbol)]
UnknownEnumToken = Annotated[str, BeforeValidator(_unknown_token)]
OpaqueCursor = Annotated[str, BeforeValidator(_opaque_cursor)]
BoundedDiagnosticText = Annotated[str, BeforeValidator(_bounded_diagnostic)]


def _exact_uuid(value: object) -> UUID:
    if type(value) is not UUID:
        raise ValueError("value must be an exact UUID object")
    try:
        raw = _UUID_INT_SLOT.__get__(value, UUID)
    except Exception:
        raise ValueError("value must be an exact UUID object") from None
    if type(raw) is not int or not 0 <= raw <= _MAX_UUID_INT:
        raise ValueError("value must be an exact UUID object")
    return UUID(int=raw)


ExactUuid = Annotated[UUID, BeforeValidator(_exact_uuid)]


def _exact_utc(value: object) -> datetime:
    if type(value) is not datetime or type(value.tzinfo) not in (timezone, ZoneInfo):
        raise ValueError("value must be an exact UTC-aware datetime")
    try:
        if value.utcoffset() != timedelta(0):
            raise ValueError
        return value.astimezone(UTC)
    except Exception:
        raise ValueError("value must be an exact UTC-aware datetime") from None


ExactAwareUtcDatetime = Annotated[datetime, BeforeValidator(_exact_utc)]


def _canonical_decimal_text(value: object) -> str:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("value must be an exact private decimal")
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast(int, raw_exponent)
    if not any(digits):
        return "0"
    end = len(digits)
    while end > 1 and digits[end - 1] == 0:
        end -= 1
        exponent += 1
    if -exponent > 18 or end + exponent > 20:
        raise ValueError("value must fit the private decimal 38/18 boundary")
    return format(Decimal((sign, digits[:end], exponent)), "f")


def _private_decimal(value: object) -> Decimal:
    return Decimal(_canonical_decimal_text(value))


def _positive_private_decimal(value: object) -> Decimal:
    result = _private_decimal(value)
    if result <= 0:
        raise ValueError("value must be a positive private decimal")
    return result


def _nonnegative_private_decimal(value: object) -> Decimal:
    result = _private_decimal(value)
    if result < 0:
        raise ValueError("value must be a nonnegative private decimal")
    return result


def _nonzero_private_decimal(value: object) -> Decimal:
    result = _private_decimal(value)
    if result == 0:
        raise ValueError("value must be a nonzero private decimal")
    return result


PrivateDecimal = Annotated[Decimal, BeforeValidator(_private_decimal)]
PositivePrivateDecimal = Annotated[Decimal, BeforeValidator(_positive_private_decimal)]
NonNegativePrivateDecimal = Annotated[Decimal, BeforeValidator(_nonnegative_private_decimal)]
NonZeroPrivateDecimal = Annotated[Decimal, BeforeValidator(_nonzero_private_decimal)]


def parse_private_decimal_text(value: object) -> Decimal:
    """Parse one canonical non-exponent decimal string into owned 38/18 data."""
    if type(value) is not str or _DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError("invalid private decimal text")
    try:
        return _private_decimal(Decimal(value))
    except Exception:
        raise ValueError("invalid private decimal text") from None


def parse_epoch_microseconds(value: object) -> datetime:
    """Parse one strict epoch-microsecond string in the local 2000–2100 range."""
    if type(value) is not str or _EPOCH_MICROSECONDS.fullmatch(value) is None:
        raise ValueError("invalid epoch microseconds")
    raw = int(value)
    if not _MIN_EPOCH_MICROSECONDS <= raw < _MAX_EPOCH_MICROSECONDS:
        raise ValueError("invalid epoch microseconds")
    seconds, microseconds = divmod(raw, 1_000_000)
    return datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=microseconds)


def parse_position_iso_datetime(value: object) -> datetime:
    """Parse one bounded aware ISO position timestamp and normalize it to UTC."""
    if (
        type(value) is not str
        or not 20 <= len(value) <= 35
        or _POSITION_ISO.fullmatch(value) is None
    ):
        raise ValueError("invalid position timestamp")
    try:
        encoded = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        result = datetime.fromisoformat(encoded)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        result = result.astimezone(UTC)
    except Exception:
        raise ValueError("invalid position timestamp") from None
    if not _MIN_PRIVATE_TIME <= result < _MAX_PRIVATE_TIME:
        raise ValueError("invalid position timestamp")
    return result


__all__ = [
    "AssetSymbol",
    "BoundedDiagnosticText",
    "Contracts",
    "CredentialVersion",
    "ExactAwareUtcDatetime",
    "ExactEnumField",
    "ExactStrEnum",
    "ExactUuid",
    "ExternalUserText",
    "NonNegativePrivateDecimal",
    "NonZeroPrivateDecimal",
    "ObservedDeltaClientOrderId",
    "OmsClientOrderId",
    "OpaqueCursor",
    "PositivePrivateDecimal",
    "PrivateBoundaryModel",
    "PrivateDecimal",
    "PrivateId",
    "ProductSymbol",
    "Sha256Hex",
    "SignedContracts",
    "StrictEpoch",
    "StrictNonNegativeInt",
    "StrictPositiveInt",
    "StrictPositivePid",
    "UnfilledContracts",
    "UnknownEnumToken",
    "exact_enum_literal",
    "exact_enum_member_for_literal",
    "parse_epoch_microseconds",
    "parse_position_iso_datetime",
    "parse_private_decimal_text",
]
