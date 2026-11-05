"""Closed generic boundary used by reviewed private account DTOs."""

from dataclasses import dataclass
from types import MappingProxyType, prepare_class
from typing import Annotated, Any, Generic, Self, TypeVar, cast

from pydantic import (
    ConfigDict,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    ValidationError,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticSerializationError, core_schema

from hmt_crypto.domain._account_contracts import (
    ObservationContext,
    ValueKind,
    _invalid_generic_declaration,
    _safe_validation_error,
)
from hmt_crypto.domain._private_generic_registry import (
    _find_reviewed_specialization,
    _is_reviewed_specialization,
    _normalize_single_parameter,
    _resolve_declaration,
    _reviewed_payload,
    _reviewed_payload_requires_exact_tuple,
)
from hmt_crypto.domain._private_generic_source import (
    _exact_string_dict,
    _module_namespace,
    _owned_lookup,
    _source_alias_dependencies,
    _verify_pre_table_source,
)
from hmt_crypto.domain.private_primitives import (
    ExactStrEnum,
    PrivateBoundaryModel,
    UnknownEnumToken,
    exact_enum_member_for_literal,
)

E = TypeVar("E", bound=ExactStrEnum)
T = TypeVar("T")
_ACCOUNT_MODULE = "hmt_crypto.domain.account"
_GENERIC_ERROR_CODE = "private_generic_specialization"
_GENERIC_ERROR_MESSAGE = "private generic specialization unavailable"
_owner_namespace = _module_namespace


def _generic_error() -> ValidationError:
    return _safe_validation_error(_GENERIC_ERROR_CODE, _GENERIC_ERROR_MESSAGE)


def _serialization_error() -> PydanticSerializationError:
    return PydanticSerializationError(_GENERIC_ERROR_MESSAGE)


def _rejected_core_schema() -> CoreSchema:
    def reject(value: object) -> object:
        del value
        raise _generic_error()

    def reject_serialization(value: object) -> object:
        del value
        raise _serialization_error()

    return core_schema.no_info_plain_validator_function(
        reject,
        json_schema_input_schema=core_schema.none_schema(),
        serialization=core_schema.plain_serializer_function_ser_schema(
            reject_serialization, return_schema=core_schema.none_schema()
        ),
    )


class _ClosedPrivateGenericModel(PrivateBoundaryModel):
    __module__ = _ACCOUNT_MODULE

    model_config = ConfigDict(defer_build=True)

    @classmethod
    def _require_reviewed_type(cls) -> None:
        if not _is_reviewed_specialization(cls):
            raise _generic_error()

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        if not _is_reviewed_specialization(cls):
            return _rejected_core_schema()
        schema = handler(source_type)
        if type(schema) is not dict:
            return _rejected_core_schema()
        owned = dict.copy(schema)
        dict.pop(owned, "ref", None)
        module_name = type.__getattribute__(cls, "__module__")
        qualname = type.__getattribute__(cls, "__qualname__")
        if type(module_name) is not str or type(qualname) is not str:
            return _rejected_core_schema()

        def admit(value: object) -> object:
            cls._require_reviewed_type()
            return value

        def serialize(
            value: object, serializer: core_schema.SerializerFunctionWrapHandler
        ) -> object:
            if not _is_reviewed_specialization(cls) or type(value) is not cls:
                raise _serialization_error()
            cls._require_owned_instance(value)
            return serializer(value)

        return core_schema.no_info_before_validator_function(
            admit,
            owned,
            ref=f"{module_name}.{qualname}:{id(cls)}",
            serialization=core_schema.wrap_serializer_function_ser_schema(serialize),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        if not _is_reviewed_specialization(cls):
            raise TypeError(_GENERIC_ERROR_MESSAGE)
        return handler(schema)

    @classmethod
    def _safe_error(cls, code: str, message: str) -> ValidationError:
        if not _is_reviewed_specialization(cls):
            return _generic_error()
        return super()._safe_error(code, message)

    @classmethod
    def _safe_root_error(cls) -> ValidationError:
        if not _is_reviewed_specialization(cls):
            return _generic_error()
        return super()._safe_root_error()

    @classmethod
    def _safe_state_error(cls) -> ValidationError:
        if not _is_reviewed_specialization(cls):
            return _generic_error()
        return super()._safe_state_error()

    @classmethod
    def _without_inputs(cls, error: ValidationError) -> ValidationError:
        if not _is_reviewed_specialization(cls):
            return _generic_error()
        return super()._without_inputs(error)

    @classmethod
    def _require_owned_instance(cls, value: object) -> None:
        cls._require_reviewed_type()
        super()._require_owned_instance(value)

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        del cls, _fields_set, values
        raise _generic_error()


class _RejectedPrivateGenericSpecialization(_ClosedPrivateGenericModel):
    __module__ = _ACCOUNT_MODULE


def _closed_model_subscription(family: object, parameters: object) -> object:
    payload = _normalize_single_parameter(parameters)
    if payload is None:
        return _RejectedPrivateGenericSpecialization
    specialization = _find_reviewed_specialization(family, payload)
    return specialization if specialization is not None else _RejectedPrivateGenericSpecialization


def _closed_alias_subscription(family: object, parameters: object) -> object:
    return _closed_model_subscription(family, parameters)


class ObservedEnum(_ClosedPrivateGenericModel, Generic[E]):
    __module__ = _ACCOUNT_MODULE

    raw: UnknownEnumToken
    known: E | None

    @classmethod
    def __class_getitem__(cls, parameters: object) -> Any:
        return _closed_model_subscription(cls, parameters)

    @model_validator(mode="after")
    def require_known_token_consistency(self) -> Self:
        payload = _reviewed_payload(type(self), ObservedEnum)
        if payload is None:
            raise _generic_error()
        member = exact_enum_member_for_literal(cast(type[ExactStrEnum], payload), self.raw)
        if (member is None and self.known is not None) or (
            member is not None and self.known is not member
        ):
            raise type(self)._safe_error(
                "private_observed_enum_value", "invalid observed enum"
            ) from None
        return self


class ValueField(_ClosedPrivateGenericModel, Generic[T]):
    __module__ = _ACCOUNT_MODULE

    kind: ValueKind
    value: T

    @classmethod
    def __class_getitem__(cls, parameters: object) -> Any:
        return _closed_model_subscription(cls, parameters)


class Observed(_ClosedPrivateGenericModel, Generic[T]):
    __module__ = _ACCOUNT_MODULE

    context: ObservationContext
    value: T

    @model_validator(mode="before")
    @classmethod
    def require_exact_tuple_payload(cls, values: object) -> object:
        requirement = _reviewed_payload_requires_exact_tuple(cls, Observed)
        if requirement is None:
            del values
            raise _generic_error()
        if not requirement:
            return values
        if type(values) is dict:
            owned = _exact_string_dict(values)
            if owned is not None:
                present, raw = _owned_lookup(owned, "value")
                if present and type(raw) is tuple:
                    return owned
                del raw, owned
            del values
            raise _generic_error()
        if type(values) is cls:
            try:
                raw = object.__getattribute__(values, "value")
            except Exception:
                raw = None
            if type(raw) is tuple:
                return values
            del raw
        del values
        raise _generic_error()

    @classmethod
    def __class_getitem__(cls, parameters: object) -> Any:
        return _closed_model_subscription(cls, parameters)

    @classmethod
    def _require_owned_instance(cls, value: object) -> None:
        super()._require_owned_instance(value)
        requirement = _reviewed_payload_requires_exact_tuple(cls, Observed)
        if requirement is False:
            return
        raw = None
        if requirement is True:
            try:
                raw = object.__getattribute__(value, "value")
            except Exception:
                pass
            if type(raw) is tuple:
                return
        del value, raw
        raise _generic_error()


def _exact_tag_entries(tags: object) -> tuple[tuple[str, type[object]], ...]:
    if type(tags) is not tuple or tuple.__len__(tags) == 0:
        raise _generic_error()
    owned: list[tuple[str, type[object]]] = []
    model_metaclass = type(PrivateBoundaryModel)
    for index in range(tuple.__len__(tags)):
        entry = tuple.__getitem__(tags, index)
        if type(entry) is not tuple or tuple.__len__(entry) != 2:
            raise _generic_error()
        literal = tuple.__getitem__(entry, 0)
        branch = tuple.__getitem__(entry, 1)
        if type(literal) is not str or type(branch) is not model_metaclass:
            raise _generic_error()
        try:
            branch_mro = type.__getattribute__(branch, "__mro__")
        except Exception:
            raise _generic_error() from None
        if type(branch_mro) is not tuple or not any(
            candidate is PrivateBoundaryModel for candidate in branch_mro
        ):
            raise _generic_error()
        owned.append((literal, branch))
    return tuple(owned)


def _copy_exact_string_dict(value: object) -> dict[str, object]:
    if (owned := _exact_string_dict(value)) is None:
        raise _generic_error()
    return owned


@dataclass(frozen=True, slots=True, eq=False)
class _ExactTaggedUnion:
    __module__ = _ACCOUNT_MODULE

    tags: tuple[tuple[str, type[object]], ...]

    def own(self, value: object) -> object:
        tags = _exact_tag_entries(object.__getattribute__(self, "tags"))
        if type(value) is dict:
            owned = _copy_exact_string_dict(value)
            tag = dict.get(owned, "kind")
            if type(tag) is not str:
                raise _generic_error()
            for literal, _branch in tags:
                if tag == literal:
                    return owned
            raise _generic_error()
        for literal, branch in tags:
            if type(value) is branch:
                try:
                    state = object.__getattribute__(value, "__dict__")
                except Exception:
                    raise _generic_error() from None
                owned = _copy_exact_string_dict(state)
                tag = dict.get(owned, "kind")
                if type(tag) is not str or tag != literal:
                    raise _generic_error()
                revalidate = type.__getattribute__(branch, "_revalidate_owned_instance")
                return cast(object, revalidate(value))
        raise _generic_error()

    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        inner = handler(source_type)

        def serialize(
            value: object, serializer: core_schema.SerializerFunctionWrapHandler
        ) -> object:
            try:
                owned = self.own(value)
            except ValidationError:
                raise _serialization_error() from None
            return serializer(owned)

        return core_schema.no_info_before_validator_function(
            self.own,
            inner,
            json_schema_input_schema=core_schema.dict_schema(),
            serialization=core_schema.wrap_serializer_function_ser_schema(serialize),
        )


class _ClosedAliasMeta(type):
    __module__ = _ACCOUNT_MODULE

    def __call__(_cls, *_args: object, **_kwargs: object) -> object:
        del _cls, _args, _kwargs
        raise _generic_error()

    def __getitem__(cls, parameters: object) -> object:
        return _closed_alias_subscription(cls, parameters)


class _RequireClosedGenericAlias(metaclass=_ClosedAliasMeta):
    __module__ = _ACCOUNT_MODULE

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        del cls, source_type, handler
        return _rejected_core_schema()

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        del cls, schema, handler
        raise TypeError(_GENERIC_ERROR_MESSAGE)


class FieldPresence(_RequireClosedGenericAlias, Generic[T]):
    __module__ = _ACCOUNT_MODULE


class NullableField(_RequireClosedGenericAlias, Generic[T]):
    __module__ = _ACCOUNT_MODULE


class _ClosedPrivateAlias:
    __module__ = _ACCOUNT_MODULE

    __private_family__: object = None
    __private_payload__: object = None
    __private_tags__: tuple[tuple[str, type[object]], ...] = ()

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        del cls, args, kwargs
        raise _generic_error()

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        if not _is_reviewed_specialization(cls):
            return _rejected_core_schema()
        raw_namespace = type.__getattribute__(cls, "__dict__")
        if type(raw_namespace) is not MappingProxyType:
            return _rejected_core_schema()
        try:
            namespace = _exact_string_dict(MappingProxyType.copy(raw_namespace))
        except RuntimeError:
            namespace = None
        if namespace is None:
            return _rejected_core_schema()
        tags_present, tags = _owned_lookup(namespace, "__private_tags__")
        if not tags_present:
            return _rejected_core_schema()
        try:
            owned_tags = _exact_tag_entries(tags)
        except ValidationError:
            return _rejected_core_schema()
        branches = tuple(branch for _literal, branch in owned_tags)
        union_type: Any = branches[0]
        for branch in branches[1:]:
            union_type = union_type | branch
        inner = handler.generate_schema(Annotated[union_type, _ExactTaggedUnion(owned_tags)])

        def admit(value: object) -> object:
            if not _is_reviewed_specialization(cls):
                raise _generic_error()
            return value

        def serialize(
            value: object, serializer: core_schema.SerializerFunctionWrapHandler
        ) -> object:
            if not _is_reviewed_specialization(cls):
                raise _serialization_error()
            return serializer(value)

        return core_schema.no_info_before_validator_function(
            admit,
            inner,
            serialization=core_schema.wrap_serializer_function_ser_schema(serialize),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        if not _is_reviewed_specialization(cls):
            raise TypeError(_GENERIC_ERROR_MESSAGE)
        return handler(schema)


def _declare_reviewed_model_candidate(
    family: object,
    payload: object,
    *,
    canonical_name: str,
    owner_module: str,
) -> type[_ClosedPrivateGenericModel]:
    resolved = _resolve_declaration("model", family, payload, canonical_name, owner_module)
    if resolved is None or not _verify_pre_table_source(
        resolved.row, family, payload, owner_module
    ):
        del family, payload, canonical_name, owner_module, resolved
        _invalid_generic_declaration("model")
    if resolved.reuse:
        return cast(type[_ClosedPrivateGenericModel], resolved.candidate)
    bases = (cast(type[object], family),)
    meta, namespace, keywords = prepare_class(canonical_name, bases)
    if meta is not type(PrivateBoundaryModel):
        raise TypeError("invalid reviewed generic model declaration")
    namespace["__module__"] = owner_module
    namespace["__qualname__"] = canonical_name
    raw_family_namespace = type.__getattribute__(family, "__dict__")
    try:
        family_namespace = _exact_string_dict(MappingProxyType.copy(raw_family_namespace))
    except RuntimeError:
        family_namespace = None
    if family_namespace is not None:
        slots_present, slots = _owned_lookup(family_namespace, "__slots__")
        if slots_present:
            namespace["__slots__"] = slots
    candidate = cast(Any, meta)(
        canonical_name,
        bases,
        namespace,
        __pydantic_generic_metadata__={
            "origin": family,
            "args": (payload,),
            "parameters": (),
        },
        __pydantic_reset_parent_namespace__=False,
        **keywords,
    )
    if type(candidate) is not type(PrivateBoundaryModel):
        raise TypeError("invalid reviewed generic model declaration")
    return cast(type[_ClosedPrivateGenericModel], candidate)


def _declare_reviewed_alias_candidate(
    family: object,
    payload: object,
    *,
    canonical_name: str,
    owner_module: str,
) -> type[_ClosedPrivateAlias]:
    resolved = _resolve_declaration("alias", family, payload, canonical_name, owner_module)
    if resolved is None or not _verify_pre_table_source(
        resolved.row, family, payload, owner_module
    ):
        del family, payload, canonical_name, owner_module, resolved
        _invalid_generic_declaration("alias")
    if resolved.reuse:
        return cast(type[_ClosedPrivateAlias], resolved.candidate)
    tags = _source_alias_dependencies(resolved.row, payload, owner_module)
    if tags is None:
        raise TypeError("invalid reviewed generic alias declaration")
    namespace = {
        "__module__": owner_module,
        "__qualname__": canonical_name,
        "__private_family__": family,
        "__private_payload__": payload,
        "__private_tags__": tags,
    }
    return cast(type[_ClosedPrivateAlias], type(canonical_name, (_ClosedPrivateAlias,), namespace))


def _rebuild_reviewed_model_candidate(model: type[_ClosedPrivateGenericModel]) -> None:
    model.model_rebuild(force=True, raise_errors=True, _types_namespace=globals())
