"""Fail-closed Pydantic model boundary for private exchange data."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ModelWrapValidatorHandler,
    ValidationError,
    model_validator,
)
from pydantic.config import ExtraValues
from pydantic_core import InitErrorDetails, PydanticCustomError

from hmt_crypto.domain._private_model_policy import (
    INTEGRITY_GUARDED_ATTRIBUTES,
    private_model_config,
    require_private_model_config,
)


class PrivateBoundaryModel(BaseModel):
    """Immutable strict model that owns inputs and hides them from failures."""

    __module__ = "hmt_crypto.domain.private_primitives"

    model_config = private_model_config()

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Lock the effective fail-closed policy after Pydantic builds a subclass."""
        super().__pydantic_init_subclass__(**kwargs)
        require_private_model_config(cls, PrivateBoundaryModel)

    def __getattribute__(self, name: str) -> Any:
        """Check model state before instance lookup can compare hostile keys."""
        if type(name) is str and name in INTEGRITY_GUARDED_ATTRIBUTES:
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
        require_private_model_config(cls, PrivateBoundaryModel)
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
        require_private_model_config(cls, PrivateBoundaryModel)
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
        require_private_model_config(cls, PrivateBoundaryModel)
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
            result = super().model_validate_json(
                json_data,
                strict=True,
                extra="forbid",
                context=None,
                by_alias=None,
                by_name=None,
            )
        except ValidationError as error:
            safe_error = cls._without_inputs(error)
        else:
            return result
        del json_data
        raise safe_error from None

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
        require_private_model_config(cls, PrivateBoundaryModel)
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
        require_private_model_config(cls, PrivateBoundaryModel)
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
