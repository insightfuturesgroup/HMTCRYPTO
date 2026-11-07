"""Construction-owned order contracts exposed by the private orders facade."""

from __future__ import annotations

import sys as _sys  # noqa: F401 -- verified through an owned module snapshot.
from types import FunctionType, ModuleType  # noqa: F401
from typing import TYPE_CHECKING

from pydantic import ConfigDict, model_validator

from hmt_crypto.domain.account import ExactBool
from hmt_crypto.domain.private_primitives import (
    BoundedDiagnosticText,
    Contracts,
    ExactAwareUtcDatetime,
    ExactStrEnum,
    ExternalUserText,
    PositivePrivateDecimal,
    PrivateBoundaryModel,
    PrivateId,
    ProductSymbol,
    UnfilledContracts,
)

_ORDERS_MODULE = "hmt_crypto.domain.private_orders"

if TYPE_CHECKING:
    from hmt_crypto.domain.private_orders import (
        ExactBoolPresence,
        ObservedDeltaClientOrderIdPresence,
        ObservedMmpLevelPresence,
        ObservedOrderSide,
        ObservedOrderState,
        ObservedOrderType,
        ObservedStopOrderType,
        ObservedStopTriggerMethodPresence,
        ObservedTimeInForcePresence,
        PositivePrivateDecimalPresence,
        PrivateDecimalPresence,
    )


class OrderSide(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    BUY = "buy"
    SELL = "sell"


class OrderType(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    LIMIT_ORDER = "limit_order"
    MARKET_ORDER = "market_order"


class OrderState(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    OPEN = "open"
    PENDING = "pending"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class StopOrderType(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    STOP_LOSS_ORDER = "stop_loss_order"
    TAKE_PROFIT_ORDER = "take_profit_order"
    LIQUIDATION_ORDER = "liquidation_order"


class MmpLevel(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    DISABLED = "disabled"
    MMP1 = "mmp1"
    MMP2 = "mmp2"
    MMP3 = "mmp3"
    MMP4 = "mmp4"
    MMP5 = "mmp5"


class StopTriggerMethod(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    MARK_PRICE = "mark_price"
    LAST_TRADED_PRICE = "last_traded_price"
    SPOT_PRICE = "spot_price"


class TimeInForce(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    GTC = "gtc"
    IOC = "ioc"


class ReduceOnlyEncoding(ExactStrEnum):
    __module__ = _ORDERS_MODULE

    JSON_BOOLEAN = "JSON_BOOLEAN"
    STRING_BOOLEAN = "STRING_BOOLEAN"


class ObservedReduceOnly(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    value: ExactBool
    encoding: ReduceOnlyEncoding


class Order(PrivateBoundaryModel):
    __module__ = _ORDERS_MODULE
    model_config = ConfigDict(defer_build=True)

    id: PrivateId
    user_id: ExternalUserText
    size: Contracts
    unfilled_size: UnfilledContracts
    side: ObservedOrderSide
    order_type: ObservedOrderType
    limit_price: PositivePrivateDecimal | None
    stop_order_type: ObservedStopOrderType | None
    stop_price: PositivePrivateDecimal | None
    average_fill_price: PositivePrivateDecimal | None
    bracket_order: ExactBoolPresence
    bracket_stop_loss_price: PositivePrivateDecimalPresence
    bracket_stop_loss_limit_price: PositivePrivateDecimalPresence
    bracket_take_profit_price: PositivePrivateDecimalPresence
    bracket_take_profit_limit_price: PositivePrivateDecimalPresence
    bracket_trail_amount: PositivePrivateDecimalPresence
    trail_amount: PositivePrivateDecimal | None
    cancellation_reason: BoundedDiagnosticText | None
    mmp: ObservedMmpLevelPresence
    stop_trigger_method: ObservedStopTriggerMethodPresence
    time_in_force: ObservedTimeInForcePresence
    updated_at: ExactAwareUtcDatetime
    paid_commission: PrivateDecimalPresence
    commission: PrivateDecimalPresence
    reduce_only: ObservedReduceOnly
    client_order_id: ObservedDeltaClientOrderIdPresence
    state: ObservedOrderState
    created_at: ExactAwareUtcDatetime
    product_id: PrivateId
    product_symbol: ProductSymbol

    @model_validator(mode="after")
    def require_consistent_order(self) -> Order:
        if self.unfilled_size > self.size or self.updated_at < self.created_at:
            raise type(self)._safe_error("private_order_value", "invalid private order") from None
        return self


def _module_namespace(
    name: str, modules: dict[str, object], module_type: object
) -> dict[str, object] | None:
    if not dict.__contains__(modules, name):
        return None
    module = dict.__getitem__(modules, name)
    if type(module) is not module_type:
        return None
    try:
        namespace = dict.copy(object.__getattribute__(module, "__dict__"))
    except (TypeError, RuntimeError):
        return None
    actual_name = dict.get(namespace, "__name__")
    return namespace if type(actual_name) is str and actual_name == name else None


def _class_shape(value: object, base: object, name: str) -> bool:
    try:
        bases = type.__getattribute__(value, "__bases__")
        mro = type.__getattribute__(value, "__mro__")
        base_mro = type.__getattribute__(base, "__mro__")
        module = type.__getattribute__(value, "__module__")
        actual_name = type.__getattribute__(value, "__name__")
        qualname = type.__getattribute__(value, "__qualname__")
    except Exception:
        return False
    return (
        type(value) is type(base)
        and type(bases) is tuple
        and tuple.__len__(bases) == 1
        and tuple.__getitem__(bases, 0) is base
        and type(mro) is tuple
        and type(base_mro) is tuple
        and tuple.__len__(mro) == tuple.__len__(base_mro) + 1
        and tuple.__getitem__(mro, 0) is value
        and all(
            tuple.__getitem__(mro, index + 1) is tuple.__getitem__(base_mro, index)
            for index in range(tuple.__len__(base_mro))
        )
        and type(module) is str
        and module == _ORDERS_MODULE
        and type(actual_name) is str
        and actual_name == name
        and type(qualname) is str
        and qualname == name
    )


_PRIMITIVE_NAMES = (
    "BoundedDiagnosticText",
    "Contracts",
    "ExactAwareUtcDatetime",
    "ExactStrEnum",
    "ExternalUserText",
    "PositivePrivateDecimal",
    "PrivateBoundaryModel",
    "PrivateId",
    "ProductSymbol",
    "UnfilledContracts",
)


def _order_contract_snapshot(_source: object = None) -> tuple[object, ...] | None:
    """Return one callback-free construction snapshot or fail closed."""
    try:
        namespace = dict.copy(globals())
    except RuntimeError:
        return None
    if type(_source) is not tuple or tuple.__len__(_source) != 2:
        return None
    names, identities = tuple.__getitem__(_source, 0), tuple.__getitem__(_source, 1)
    if (
        dict.get(namespace, "_ORDER_SOURCE_CAPTURE") is not _source
        or type(names) is not tuple
        or type(identities) is not tuple
        or tuple.__len__(names) != tuple.__len__(identities)
        or any(
            dict.get(namespace, name) is not tuple.__getitem__(identities, index)
            for index, name in enumerate(names)
        )
    ):
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
    types_source = _module_namespace("types", modules, module_type)
    primitives = _module_namespace("hmt_crypto.domain.private_primitives", modules, module_type)
    account = _module_namespace("hmt_crypto.domain.account", modules, module_type)
    account_source = _module_namespace("hmt_crypto.domain._account_contracts", modules, module_type)
    if types_source is None or primitives is None or account is None or account_source is None:
        return None
    for name in ("FunctionType", "ModuleType"):
        if dict.get(namespace, name) is not dict.get(types_source, name):
            return None
    for name in _PRIMITIVE_NAMES:
        if not dict.__contains__(namespace, name) or not dict.__contains__(primitives, name):
            return None
        if dict.__getitem__(namespace, name) is not dict.__getitem__(primitives, name):
            return None
    if not dict.__contains__(namespace, "ExactBool") or not dict.__contains__(account, "ExactBool"):
        return None
    if dict.__getitem__(namespace, "ExactBool") is not dict.__getitem__(
        account, "ExactBool"
    ) or dict.__getitem__(account, "ExactBool") is not dict.get(account_source, "ExactBool"):
        return None
    enum_base = dict.__getitem__(namespace, "ExactStrEnum")
    model_base = dict.__getitem__(namespace, "PrivateBoundaryModel")
    names = (
        "OrderSide",
        "OrderType",
        "OrderState",
        "StopOrderType",
        "MmpLevel",
        "StopTriggerMethod",
        "TimeInForce",
        "ReduceOnlyEncoding",
        "ObservedReduceOnly",
        "Order",
    )
    values: list[object] = []
    for index, name in enumerate(names):
        if not dict.__contains__(namespace, name):
            return None
        value = dict.__getitem__(namespace, name)
        base = enum_base if index < 8 else model_base
        if not _class_shape(value, base, name):
            return None
        values.append(value)
    for name in ("_rebuild_order_independent", "_rebuild_order_dependent"):
        function = dict.get(namespace, name)
        if type(function) is not FunctionType:
            return None
        module = object.__getattribute__(function, "__module__")
        actual_name = object.__getattribute__(function, "__name__")
        qualname = object.__getattribute__(function, "__qualname__")
        if not (
            type(module) is str
            and module == __name__
            and type(actual_name) is str
            and actual_name == name
            and type(qualname) is str
            and qualname == name
        ):
            return None
        values.append(function)
    narrow = {
        name: dict.__getitem__(namespace, name) for name in (*_PRIMITIVE_NAMES, "ExactBool", *names)
    }
    return (*values, narrow)


def _owned_namespace(namespace: object, snapshot: object) -> dict[str, object]:
    if type(namespace) is not dict or type(snapshot) is not tuple or len(snapshot) != 13:
        raise RuntimeError("invalid order rebuild source") from None
    owned = dict.copy(namespace)
    narrow = dict.copy(tuple.__getitem__(snapshot, 12))
    names = (
        "OrderSide",
        "OrderType",
        "OrderState",
        "StopOrderType",
        "MmpLevel",
        "StopTriggerMethod",
        "TimeInForce",
        "ReduceOnlyEncoding",
        "ObservedReduceOnly",
        "Order",
    )
    for index, name in enumerate(names):
        if dict.get(owned, name) is not tuple.__getitem__(snapshot, index):
            raise RuntimeError("invalid order rebuild source") from None
        dict.__setitem__(narrow, name, tuple.__getitem__(snapshot, index))
    return narrow


def _rebuild_order_independent(namespace: object, snapshot: object) -> None:
    types_namespace = _owned_namespace(namespace, snapshot)
    if type(snapshot) is not tuple:
        raise RuntimeError("invalid order rebuild source") from None
    model = tuple.__getitem__(snapshot, 8)
    type.__getattribute__(model, "model_rebuild")(
        force=True, raise_errors=True, _types_namespace=types_namespace
    )


def _rebuild_order_dependent(namespace: object, snapshot: object) -> None:
    types_namespace = _owned_namespace(namespace, snapshot)
    if type(namespace) is not dict:
        raise RuntimeError("invalid order rebuild source") from None
    if type(snapshot) is not tuple:
        raise RuntimeError("invalid order rebuild source") from None
    owned = dict.copy(namespace)
    for name in (
        "ExactBoolPresence",
        "ObservedDeltaClientOrderIdPresence",
        "ObservedMmpLevelPresence",
        "ObservedOrderSide",
        "ObservedOrderState",
        "ObservedOrderType",
        "ObservedStopOrderType",
        "ObservedStopTriggerMethodPresence",
        "ObservedTimeInForcePresence",
        "PositivePrivateDecimalPresence",
        "PrivateDecimalPresence",
    ):
        if not dict.__contains__(owned, name):
            raise RuntimeError("invalid order rebuild source") from None
        dict.__setitem__(types_namespace, name, dict.__getitem__(owned, name))
    model = tuple.__getitem__(snapshot, 9)
    type.__getattribute__(model, "model_rebuild")(
        force=True, raise_errors=True, _types_namespace=types_namespace
    )


# fmt: off
_ORDER_SOURCE_NAMES = (
    "OrderSide", "OrderType", "OrderState", "StopOrderType", "MmpLevel",
    "StopTriggerMethod", "TimeInForce", "ReduceOnlyEncoding", "ObservedReduceOnly",
    "Order", "_module_namespace", "_class_shape", "_order_contract_snapshot",
    "_owned_namespace", "_rebuild_order_independent", "_rebuild_order_dependent",
)
_ORDER_SOURCE_IDENTITIES = (
    OrderSide, OrderType, OrderState, StopOrderType, MmpLevel, StopTriggerMethod,
    TimeInForce, ReduceOnlyEncoding, ObservedReduceOnly, Order, _module_namespace,
    _class_shape, _order_contract_snapshot, _owned_namespace,
    _rebuild_order_independent, _rebuild_order_dependent,
)
_ORDER_SOURCE_CAPTURE = (_ORDER_SOURCE_NAMES, _ORDER_SOURCE_IDENTITIES)
_order_contract_snapshot.__defaults__ = (_ORDER_SOURCE_CAPTURE,)
# fmt: on


__all__ = [
    "MmpLevel",
    "ObservedReduceOnly",
    "Order",
    "OrderSide",
    "OrderState",
    "OrderType",
    "ReduceOnlyEncoding",
    "StopOrderType",
    "StopTriggerMethod",
    "TimeInForce",
]
