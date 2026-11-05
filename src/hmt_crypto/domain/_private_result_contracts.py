"""Construction-owned result contracts exposed by the private orders facade."""

# ruff: noqa: E501, I001 -- compact immutable source inventories preserve the 420-line ceiling.

from __future__ import annotations

import sys as _sys  # noqa: F401 -- verified through an owned module snapshot.
from types import FunctionType, GenericAlias, ModuleType  # noqa: F401
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BeforeValidator, ConfigDict, model_validator

# fmt: off
from hmt_crypto.domain.account import (CollectionMode, DiagnosticPaginationKind, ExactBool, ExactTrue, LookupFoundKind, LookupNotFoundKind, NullableOpaqueCursor, ObservationContext, PrivatePaginationProvenance, StrictPaginationKind, TruncationReason, _closed_model_subscription, _ClosedPrivateGenericModel, _RequireClosedGenericAlias)
# fmt: on
from hmt_crypto.domain.private_positions import ObservedMarginMode

# fmt: off
from hmt_crypto.domain.private_primitives import (ExactStrEnum, ExternalUserText, NonNegativePrivateDecimal, PositivePrivateDecimal, PrivateBoundaryModel, PrivateId, StrictPositiveInt)
# fmt: on

T = TypeVar("T")
_ORDERS_MODULE = "hmt_crypto.domain.private_orders"


class LeverageEncoding(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    DECIMAL_STRING = "DECIMAL_STRING"
    JSON_INTEGER = "JSON_INTEGER"


class Leverage(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    leverage: PositivePrivateDecimal
    leverage_encoding: LeverageEncoding
    order_margin: NonNegativePrivateDecimal
    product_id: PrivateId

    @model_validator(mode="after")
    def require_encoding_evidence(self) -> Leverage:
        if (
            self.leverage_encoding is LeverageEncoding.JSON_INTEGER
            and self.leverage != self.leverage.to_integral_value()
        ):
            raise type(self)._safe_error(
                "private_leverage_value", "invalid leverage result"
            ) from None
        return self


class MarginModeResult(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    id: ExternalUserText
    margin_mode: ObservedMarginMode


class MutationAcknowledgement(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    success: ExactTrue


class ReviewedAbsenceCode(ExactStrEnum):
    __module__ = _ORDERS_MODULE


class LookupFound(_ClosedPrivateGenericModel, Generic[T]):
    __module__ = _ORDERS_MODULE

    kind: LookupFoundKind
    value: T

    @classmethod
    def __class_getitem__(cls, parameters: object) -> Any:
        return _closed_model_subscription(cls, parameters)


class LookupNotFound(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    kind: LookupNotFoundKind
    absence_code: ReviewedAbsenceCode


class LookupResult(_RequireClosedGenericAlias, Generic[T]):
    __module__ = _ORDERS_MODULE


def _exact_items_tuple(value: object) -> object:
    if type(value) is not tuple:
        del value
        raise ValueError("items require an exact tuple") from None
    return value


_ExactItems = Annotated[tuple[T, ...], BeforeValidator(_exact_items_tuple)]


def _require_items_state(model: type[PrivateBoundaryModel], value: object) -> None:
    try:
        items = object.__getattribute__(value, "items")
    except Exception:
        items = None
    if type(items) is not tuple:
        del items, value
        raise model._safe_state_error() from None


class PrivateCursorPage(_ClosedPrivateGenericModel, Generic[T]):
    __module__ = _ORDERS_MODULE

    context: ObservationContext
    items: _ExactItems[T]
    after: NullableOpaqueCursor
    before: NullableOpaqueCursor

    @classmethod
    def __class_getitem__(cls, parameters: object) -> Any:
        return _closed_model_subscription(cls, parameters)

    @classmethod
    def _require_owned_instance(cls, value: object) -> None:
        super()._require_owned_instance(value)
        _require_items_state(cls, value)


class StrictPrivatePaginationResult(_ClosedPrivateGenericModel, Generic[T]):
    __module__ = _ORDERS_MODULE

    kind: StrictPaginationKind
    items: _ExactItems[T]
    complete: ExactTrue
    page_count: StrictPositiveInt
    provenance: PrivatePaginationProvenance

    @classmethod
    def __class_getitem__(cls, parameters: object) -> Any:
        return _closed_model_subscription(cls, parameters)

    @model_validator(mode="after")
    def require_strict_provenance(self) -> StrictPrivatePaginationResult[T]:
        if (
            self.provenance.mode is not CollectionMode.STRICT
            or len(self.provenance.page_correlation_ids) != self.page_count
        ):
            raise type(self)._safe_error(
                "private_pagination_value", "invalid private pagination result"
            ) from None
        return self

    @classmethod
    def _require_owned_instance(cls, value: object) -> None:
        super()._require_owned_instance(value)
        _require_items_state(cls, value)


class DiagnosticPrivatePaginationResult(_ClosedPrivateGenericModel, Generic[T]):
    __module__ = _ORDERS_MODULE

    kind: DiagnosticPaginationKind
    items: _ExactItems[T]
    complete: ExactBool
    truncation_reason: TruncationReason | None
    page_count: StrictPositiveInt
    provenance: PrivatePaginationProvenance

    @classmethod
    def __class_getitem__(cls, parameters: object) -> Any:
        return _closed_model_subscription(cls, parameters)

    @model_validator(mode="after")
    def require_diagnostic_provenance(self) -> DiagnosticPrivatePaginationResult[T]:
        valid_completion = (self.complete is True and self.truncation_reason is None) or (
            self.complete is False
            and self.truncation_reason in (TruncationReason.PAGE_LIMIT, TruncationReason.ITEM_LIMIT)
        )
        if (
            not valid_completion
            or self.provenance.mode is not CollectionMode.DIAGNOSTIC
            or len(self.provenance.page_correlation_ids) != self.page_count
        ):
            raise type(self)._safe_error(
                "private_pagination_value", "invalid private pagination result"
            ) from None
        return self

    @classmethod
    def _require_owned_instance(cls, value: object) -> None:
        super()._require_owned_instance(value)
        _require_items_state(cls, value)


def _module_namespace(name: str, modules: dict[str, object], module_type: object) -> dict[str, object] | None:
    try:
        module = dict.__getitem__(modules, name)
    except KeyError:
        return None
    if type(module) is not module_type:
        return None
    try:
        namespace = dict.copy(object.__getattribute__(module, "__dict__"))
    except (TypeError, RuntimeError):
        return None
    actual = dict.get(namespace, "__name__")
    if type(actual) is not str or actual != name:
        return None
    return namespace


def _class_shape(value: object, bases: tuple[object, ...], name: str) -> bool:
    try:
        actual_bases = type.__getattribute__(value, "__bases__")
        actual_mro = type.__getattribute__(value, "__mro__")
        module = type.__getattribute__(value, "__module__")
        actual_name = type.__getattribute__(value, "__name__")
        qualname = type.__getattribute__(value, "__qualname__")
    except Exception:
        return False
    if type(actual_bases) is not tuple or tuple.__len__(actual_bases) != len(bases):
        return False
    if type(value) is not type(tuple.__getitem__(bases, 0)):
        return False
    if any(
        tuple.__getitem__(actual_bases, index) is not tuple.__getitem__(bases, index)
        for index in range(len(bases))
    ):
        return False
    try:
        first_mro = type.__getattribute__(tuple.__getitem__(bases, 0), "__mro__")
    except Exception:
        return False
    if type(first_mro) is not tuple:
        return False
    expected_mro = (
        (value, *first_mro)
        if tuple.__len__(bases) == 1
        else (value, *first_mro[:-1], tuple.__getitem__(bases, 1), tuple.__getitem__(first_mro, -1))
    )
    return (
        type(actual_mro) is tuple
        and tuple.__len__(actual_mro) == tuple.__len__(expected_mro)
        and all(
            tuple.__getitem__(actual_mro, index) is tuple.__getitem__(expected_mro, index)
            for index in range(tuple.__len__(expected_mro))
        )
        and type(module) is str
        and module == _ORDERS_MODULE
        and type(actual_name) is str
        and actual_name == name
        and type(qualname) is str
        and qualname == name
    )


_PUBLIC_NAMES = ("LeverageEncoding", "Leverage", "MarginModeResult", "MutationAcknowledgement", "ReviewedAbsenceCode", "LookupFound", "LookupNotFound", "LookupResult", "PrivateCursorPage", "StrictPrivatePaginationResult", "DiagnosticPrivatePaginationResult")


def _result_contract_snapshot(_source: object = None) -> tuple[object, ...] | None:
    try:
        namespace = dict.copy(globals())
    except RuntimeError:
        return None
    if type(_source) is not tuple or tuple.__len__(_source) != 2:
        return None
    names, identities = tuple.__getitem__(_source, 0), tuple.__getitem__(_source, 1)
    if dict.get(namespace, "_RESULT_SOURCE_CAPTURE") is not _source or type(names) is not tuple or type(identities) is not tuple or tuple.__len__(names) != tuple.__len__(identities) or any(dict.get(namespace, name) is not tuple.__getitem__(identities, index) for index, name in enumerate(names)):
        return None
    sys_module = dict.get(namespace, "_sys")
    module_type = dict.get(namespace, "ModuleType")
    if type(sys_module) is not module_type:
        return None
    try:
        sys_namespace = dict.copy(object.__getattribute__(sys_module, "__dict__"))
        modules = dict.copy(object.__getattribute__(sys_module, "modules"))
    except (TypeError, RuntimeError):
        return None
    sys_name = dict.get(sys_namespace, "__name__")
    if type(sys_name) is not str or sys_name != "sys" or dict.get(modules, "sys") is not sys_module:
        return None
    # fmt: off
    sources = (
        ("types", ("FunctionType", "GenericAlias", "ModuleType")),
        ("typing", ("Annotated", "Generic")),
        ("pydantic", ("BeforeValidator",)),
        ("hmt_crypto.domain.private_primitives", ("ExactStrEnum", "ExternalUserText", "NonNegativePrivateDecimal", "PositivePrivateDecimal", "PrivateBoundaryModel", "PrivateId", "StrictPositiveInt")),
        ("hmt_crypto.domain._account_contracts", ("CollectionMode", "DiagnosticPaginationKind", "ExactBool", "ExactTrue", "LookupFoundKind", "LookupNotFoundKind", "ObservationContext", "PrivatePaginationProvenance", "StrictPaginationKind", "TruncationReason")),
        ("hmt_crypto.domain.account", ("NullableOpaqueCursor",)),
        ("hmt_crypto.domain._private_generic_core", ("_ClosedPrivateGenericModel", "_RequireClosedGenericAlias", "_closed_model_subscription")),
        ("hmt_crypto.domain.private_positions", ("ObservedMarginMode",)),
    )
    # fmt: on
    narrow: dict[str, object] = {}
    for source_name, names in sources:
        source = _module_namespace(source_name, modules, module_type)
        if source is None:
            return None
        for name in names:
            value = dict.get(namespace, name)
            if value is not dict.get(source, name):
                return None
            dict.__setitem__(narrow, name, value)
    account = _module_namespace("hmt_crypto.domain.account", modules, module_type)
    core = _module_namespace("pydantic_core", modules, module_type)
    if account is None or core is None:
        return None
    for name in tuple.__getitem__(sources, 4)[1] + tuple.__getitem__(sources, 6)[1]:
        if dict.get(namespace, name) is not dict.get(account, name):
            return None
    bases = (
        (dict.__getitem__(namespace, "ExactStrEnum"),),
        (dict.__getitem__(namespace, "PrivateBoundaryModel"),),
        (
            dict.__getitem__(namespace, "_ClosedPrivateGenericModel"),
            dict.__getitem__(namespace, "Generic"),
        ),
        (
            dict.__getitem__(namespace, "_RequireClosedGenericAlias"),
            dict.__getitem__(namespace, "Generic"),
        ),
    )
    kinds = (0, 1, 1, 1, 0, 2, 1, 3, 2, 2, 2)
    values: list[object] = []
    for index, name in enumerate(_PUBLIC_NAMES):
        value = dict.get(namespace, name)
        if not _class_shape(value, tuple.__getitem__(bases, kinds[index]), name):
            return None
        values.append(value)
        dict.__setitem__(narrow, name, value)
    alias = dict.get(namespace, "_ExactItems")
    validator = dict.get(namespace, "_exact_items_tuple")
    if type(alias) is not type(Annotated[object, None]) or type(validator) is not FunctionType:
        return None
    try:
        inner = object.__getattribute__(alias, "__origin__")
        alias_args = object.__getattribute__(alias, "__args__")
        metadata = object.__getattribute__(alias, "__metadata__")
        arguments = object.__getattribute__(inner, "__args__")
    except Exception:
        return None
    if type(metadata) is not tuple or tuple.__len__(metadata) != 1:
        return None
    before = tuple.__getitem__(metadata, 0)
    if (
        type(inner) is not GenericAlias
        or type(alias_args) is not tuple
        or tuple.__len__(alias_args) != 1
        or tuple.__getitem__(alias_args, 0) is not inner
        or object.__getattribute__(inner, "__origin__") is not tuple
        or type(arguments) is not tuple
        or tuple.__len__(arguments) != 2
        or tuple.__getitem__(arguments, 0) is not dict.get(namespace, "T")
        or tuple.__getitem__(arguments, 1) is not Ellipsis
        or type(before) is not BeforeValidator
        or object.__getattribute__(before, "func") is not validator
        or object.__getattribute__(before, "json_schema_input_type")
        is not dict.get(core, "PydanticUndefined")
    ):
        return None
    for name in ("_exact_items_tuple", "_rebuild_result_independent"):
        function = dict.get(namespace, name)
        if type(function) is not FunctionType:
            return None
        module = object.__getattribute__(function, "__module__")
        actual = object.__getattribute__(function, "__name__")
        qualname = object.__getattribute__(function, "__qualname__")
        if not (
            type(module) is str
            and module == __name__
            and type(actual) is str
            and actual == name
            and type(qualname) is str
            and qualname == name
        ):
            return None
        values.append(function)
    dict.__setitem__(narrow, "_ExactItems", dict.__getitem__(namespace, "_ExactItems"))
    dict.__setitem__(narrow, "T", dict.__getitem__(namespace, "T"))
    return (*values, narrow)


def _rebuild_result_independent(namespace: object, snapshot: object) -> None:
    if type(namespace) is not dict or type(snapshot) is not tuple:
        raise RuntimeError("invalid result rebuild source") from None
    if tuple.__len__(snapshot) != 14:
        raise RuntimeError("invalid result rebuild source") from None
    owned = dict.copy(namespace)
    try:
        types_namespace = dict.copy(tuple.__getitem__(snapshot, 13))
    except (TypeError, RuntimeError):
        raise RuntimeError("invalid result rebuild source") from None
    for index, name in enumerate(_PUBLIC_NAMES):
        value = dict.get(owned, name)
        if value is not tuple.__getitem__(snapshot, index):
            raise RuntimeError("invalid result rebuild source") from None
        dict.__setitem__(types_namespace, name, value)
    for index in (1, 2, 3, 6):
        model = tuple.__getitem__(snapshot, index)
        type.__getattribute__(model, "model_rebuild")(
            force=True, raise_errors=True, _types_namespace=types_namespace
        )


_RESULT_SOURCE_NAMES = ("T", "LeverageEncoding", "Leverage", "MarginModeResult", "MutationAcknowledgement", "ReviewedAbsenceCode", "LookupFound", "LookupNotFound", "LookupResult", "_exact_items_tuple", "_ExactItems", "_require_items_state", "PrivateCursorPage", "StrictPrivatePaginationResult", "DiagnosticPrivatePaginationResult", "_module_namespace", "_class_shape", "_PUBLIC_NAMES", "_result_contract_snapshot", "_rebuild_result_independent")
_RESULT_SOURCE_IDENTITIES = (T, LeverageEncoding, Leverage, MarginModeResult, MutationAcknowledgement, ReviewedAbsenceCode, LookupFound, LookupNotFound, LookupResult, _exact_items_tuple, _ExactItems, _require_items_state, PrivateCursorPage, StrictPrivatePaginationResult, DiagnosticPrivatePaginationResult, _module_namespace, _class_shape, _PUBLIC_NAMES, _result_contract_snapshot, _rebuild_result_independent)
_RESULT_SOURCE_CAPTURE = (_RESULT_SOURCE_NAMES, _RESULT_SOURCE_IDENTITIES)
_result_contract_snapshot.__defaults__ = (_RESULT_SOURCE_CAPTURE,)

__all__ = [
    "DiagnosticPrivatePaginationResult", "Leverage", "LeverageEncoding", "LookupFound", "LookupNotFound",
    "LookupResult", "MarginModeResult", "MutationAcknowledgement", "PrivateCursorPage",
    "ReviewedAbsenceCode", "StrictPrivatePaginationResult",
]
