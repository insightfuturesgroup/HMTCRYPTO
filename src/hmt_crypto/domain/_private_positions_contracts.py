"""Construction-owned position contracts exposed by the positions facade."""

from __future__ import annotations

import sys as _sys  # noqa: F401 -- verified through an owned module snapshot.
from types import FunctionType, GenericAlias, ModuleType  # noqa: F401
from typing import TYPE_CHECKING, Annotated, Self

from pydantic import ConfigDict, model_validator

from hmt_crypto.domain.account import ExactBool, ExactTrue
from hmt_crypto.domain.private_primitives import (
    ExactAwareUtcDatetime,
    ExactStrEnum,
    ExternalUserText,
    NonNegativePrivateDecimal,
    PrivateBoundaryModel,
    PrivateDecimal,
    PrivateId,
    ProductSymbol,
    SignedContracts,
    StrictNonNegativeInt,
)

_POSITIONS_MODULE = "hmt_crypto.domain.private_positions"

if TYPE_CHECKING:
    from hmt_crypto.domain.private_positions import ObservedMarginMode


class MarginMode(ExactStrEnum):
    """Observed response margin modes; selection policy lives outside the DTO."""

    __module__ = _POSITIONS_MODULE

    ISOLATED = "isolated"
    PORTFOLIO = "portfolio"
    CROSS = "cross"


class PositionProductIdentity(PrivateBoundaryModel):
    """Minimal product identity carried by a position response."""

    __module__ = _POSITIONS_MODULE
    model_config = ConfigDict(defer_build=True)

    id: PrivateId
    symbol: ProductSymbol


class LatestPosition(PrivateBoundaryModel):
    """Real-time latest position summary."""

    __module__ = _POSITIONS_MODULE
    model_config = ConfigDict(defer_build=True)

    product_id: PrivateId
    signed_contracts: SignedContracts
    entry_price: NonNegativePrivateDecimal


class MarginedPosition(PrivateBoundaryModel):
    """Potentially delayed margined position and signed accounting values."""

    __module__ = _POSITIONS_MODULE
    model_config = ConfigDict(defer_build=True)

    user_id: ExternalUserText
    size: SignedContracts
    entry_price: NonNegativePrivateDecimal
    margin: NonNegativePrivateDecimal
    liquidation_price: NonNegativePrivateDecimal | None
    bankruptcy_price: NonNegativePrivateDecimal | None
    adl_level: StrictNonNegativeInt | None
    product_id: PrivateId
    product_symbol: ProductSymbol
    product: PositionProductIdentity
    commission: PrivateDecimal
    realized_pnl: PrivateDecimal
    realized_funding: PrivateDecimal
    realized_cashflow: PrivateDecimal
    realized_holding_cost: PrivateDecimal
    unrealized_pnl: PrivateDecimal
    unrealized_cashflow: PrivateDecimal
    mark_price: NonNegativePrivateDecimal
    margin_mode: ObservedMarginMode
    auto_topup: ExactBool
    created_at: ExactAwareUtcDatetime
    updated_at: ExactAwareUtcDatetime
    potentially_delayed: ExactTrue

    @model_validator(mode="after")
    def require_consistent_position(self) -> Self:
        if self.product.id != self.product_id or self.product.symbol != self.product_symbol:
            raise type(self)._safe_error(
                "private_position_value", "invalid private position"
            ) from None
        if self.updated_at < self.created_at:
            raise type(self)._safe_error(
                "private_position_value", "invalid private position"
            ) from None
        return self


MarginedPositionBatch = tuple[MarginedPosition, ...]


def _rebuild_positions_independent(namespace: object, snapshot: object) -> None:
    """Build independent DTOs from an owned, preflighted facade snapshot."""
    if type(namespace) is not dict or type(snapshot) is not tuple:
        raise RuntimeError("invalid positions rebuild source") from None
    if tuple.__len__(snapshot) != 10:
        raise RuntimeError("invalid positions rebuild source") from None
    try:
        owned = dict.copy(namespace)
        types_namespace = dict.copy(tuple.__getitem__(snapshot, 7))
    except (TypeError, RuntimeError):
        raise RuntimeError("invalid positions rebuild source") from None
    names = (
        "MarginMode",
        "PositionProductIdentity",
        "LatestPosition",
        "MarginedPosition",
        "MarginedPositionBatch",
        "ExactBool",
        "ExactTrue",
    )
    for index in range(tuple.__len__(names)):
        name = tuple.__getitem__(names, index)
        if not dict.__contains__(owned, name):
            raise RuntimeError("invalid positions rebuild source") from None
        value = dict.__getitem__(owned, name)
        if value is not tuple.__getitem__(snapshot, index):
            del value
            raise RuntimeError("invalid positions rebuild source") from None
        dict.__setitem__(types_namespace, name, value)
    models = (
        dict.__getitem__(owned, "PositionProductIdentity"),
        dict.__getitem__(owned, "LatestPosition"),
    )
    for index in range(tuple.__len__(models)):
        model = tuple.__getitem__(models, index)
        rebuild = type.__getattribute__(model, "model_rebuild")
        rebuild(force=True, raise_errors=True, _types_namespace=types_namespace)


def _rebuild_positions_dependent(namespace: object, snapshot: object) -> None:
    """Build the dependent position DTO without consulting live leaf globals."""
    if type(namespace) is not dict or type(snapshot) is not tuple:
        raise RuntimeError("invalid positions rebuild source") from None
    if tuple.__len__(snapshot) != 10:
        raise RuntimeError("invalid positions rebuild source") from None
    try:
        owned = dict.copy(namespace)
        types_namespace = dict.copy(tuple.__getitem__(snapshot, 7))
    except (TypeError, RuntimeError):
        raise RuntimeError("invalid positions rebuild source") from None
    source_names = (
        "MarginMode",
        "PositionProductIdentity",
        "LatestPosition",
        "MarginedPosition",
        "MarginedPositionBatch",
        "ExactBool",
        "ExactTrue",
    )
    for index in range(tuple.__len__(source_names)):
        name = tuple.__getitem__(source_names, index)
        if not dict.__contains__(owned, name):
            raise RuntimeError("invalid positions rebuild source") from None
        value = dict.__getitem__(owned, name)
        if value is not tuple.__getitem__(snapshot, index):
            del value
            raise RuntimeError("invalid positions rebuild source") from None
        dict.__setitem__(types_namespace, name, value)
    if not dict.__contains__(owned, "ObservedMarginMode"):
        raise RuntimeError("invalid positions rebuild source") from None
    dict.__setitem__(
        types_namespace, "ObservedMarginMode", dict.__getitem__(owned, "ObservedMarginMode")
    )
    model = dict.__getitem__(owned, "MarginedPosition")
    rebuild = type.__getattribute__(model, "model_rebuild")
    rebuild(force=True, raise_errors=True, _types_namespace=types_namespace)


def _positions_contract_snapshot() -> tuple[object, ...] | None:
    """Return one callback-free construction snapshot or fail closed."""
    try:
        namespace = dict.copy(globals())
    except RuntimeError:
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
    if (
        type(dict.get(sys_namespace, "__name__")) is not str
        or dict.get(sys_namespace, "__name__") != "sys"
        or dict.get(modules, "sys") is not sys_module
    ):
        return None
    sources = (
        ("types", ("FunctionType", "GenericAlias", "ModuleType")),
        ("typing", ("Annotated",)),
        (
            "hmt_crypto.domain.private_primitives",
            (
                "ExactAwareUtcDatetime",
                "ExactStrEnum",
                "ExternalUserText",
                "NonNegativePrivateDecimal",
                "PrivateBoundaryModel",
                "PrivateDecimal",
                "PrivateId",
                "ProductSymbol",
                "SignedContracts",
                "StrictNonNegativeInt",
            ),
        ),
    )
    for source_name, names in sources:
        source = dict.get(modules, source_name)
        if type(source) is not module_type:
            return None
        try:
            source_namespace = dict.copy(object.__getattribute__(source, "__dict__"))
        except (TypeError, RuntimeError):
            return None
        actual_name = dict.get(source_namespace, "__name__")
        if type(actual_name) is not str or actual_name != source_name:
            return None
        for name in names:
            if dict.get(namespace, name) is not dict.get(source_namespace, name):
                return None
    required = (
        "MarginMode",
        "PositionProductIdentity",
        "LatestPosition",
        "MarginedPosition",
        "MarginedPositionBatch",
        "ExactBool",
        "ExactTrue",
        "ExactStrEnum",
        "PrivateBoundaryModel",
        "_rebuild_positions_independent",
        "_rebuild_positions_dependent",
    )
    values: list[object] = []
    for name in required:
        if not dict.__contains__(namespace, name):
            return None
        values.append(dict.__getitem__(namespace, name))
    enum_base = values[7]
    model_base = values[8]
    enum_metaclass = type(enum_base)
    model_metaclass = type(model_base)
    try:
        enum_base_mro = type.__getattribute__(enum_base, "__mro__")
        model_base_mro = type.__getattribute__(model_base, "__mro__")
    except Exception:
        return None
    specs = (
        (values[0], enum_metaclass, enum_base, enum_base_mro, "MarginMode"),
        (
            values[1],
            model_metaclass,
            model_base,
            model_base_mro,
            "PositionProductIdentity",
        ),
        (values[2], model_metaclass, model_base, model_base_mro, "LatestPosition"),
        (values[3], model_metaclass, model_base, model_base_mro, "MarginedPosition"),
    )
    for value, metaclass, base, base_mro, expected_name in specs:
        if type(value) is not metaclass or type(base_mro) is not tuple:
            return None
        try:
            bases = type.__getattribute__(value, "__bases__")
            mro = type.__getattribute__(value, "__mro__")
            module = type.__getattribute__(value, "__module__")
            name = type.__getattribute__(value, "__name__")
            qualname = type.__getattribute__(value, "__qualname__")
        except Exception:
            return None
        if (
            type(bases) is not tuple
            or tuple.__len__(bases) != 1
            or tuple.__getitem__(bases, 0) is not base
            or type(mro) is not tuple
            or tuple.__len__(mro) != tuple.__len__(base_mro) + 1
            or tuple.__getitem__(mro, 0) is not value
            or any(
                tuple.__getitem__(mro, index + 1) is not tuple.__getitem__(base_mro, index)
                for index in range(tuple.__len__(base_mro))
            )
            or type(module) is not str
            or module != "hmt_crypto.domain.private_positions"
            or type(name) is not str
            or name != expected_name
            or type(qualname) is not str
            or qualname != expected_name
        ):
            return None
    batch = values[4]
    if type(batch) is not GenericAlias:
        return None
    try:
        origin = object.__getattribute__(batch, "__origin__")
        arguments = object.__getattribute__(batch, "__args__")
    except Exception:
        return None
    if (
        origin is not tuple
        or type(arguments) is not tuple
        or tuple.__len__(arguments) != 2
        or tuple.__getitem__(arguments, 0) is not values[3]
        or tuple.__getitem__(arguments, 1) is not Ellipsis
    ):
        del origin, arguments, batch
        return None
    exact_bool = values[5]
    exact_true = values[6]
    annotated_type = type(Annotated[object, None])
    if type(exact_bool) is not annotated_type or type(exact_true) is not annotated_type:
        return None
    for index, expected_name in (
        (9, "_rebuild_positions_independent"),
        (10, "_rebuild_positions_dependent"),
    ):
        function = values[index]
        if type(function) is not FunctionType:
            return None
        try:
            module = object.__getattribute__(function, "__module__")
            name = object.__getattribute__(function, "__name__")
            qualname = object.__getattribute__(function, "__qualname__")
        except Exception:
            return None
        if (
            type(module) is not str
            or module != "hmt_crypto.domain._private_positions_contracts"
            or type(name) is not str
            or name != expected_name
            or type(qualname) is not str
            or qualname != expected_name
        ):
            return None
    narrow: dict[str, object] = {}
    for name in (
        "ExactAwareUtcDatetime",
        "ExactStrEnum",
        "ExternalUserText",
        "NonNegativePrivateDecimal",
        "PrivateBoundaryModel",
        "PrivateDecimal",
        "PrivateId",
        "ProductSymbol",
        "SignedContracts",
        "StrictNonNegativeInt",
        "ExactBool",
        "ExactTrue",
        "MarginMode",
        "PositionProductIdentity",
        "LatestPosition",
        "MarginedPosition",
        "MarginedPositionBatch",
    ):
        if not dict.__contains__(namespace, name):
            return None
        dict.__setitem__(narrow, name, dict.__getitem__(namespace, name))
    return (*values[:7], narrow, values[9], values[10])


__all__ = [
    "LatestPosition",
    "MarginMode",
    "MarginedPosition",
    "MarginedPositionBatch",
    "PositionProductIdentity",
]
