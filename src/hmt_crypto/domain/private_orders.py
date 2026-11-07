"""Closed order, fill, lookup, and pagination response contracts."""

# ruff: noqa: E501, I001 -- staged source ownership has binding line ceilings.

from __future__ import annotations

import sys as _sys
from types import FunctionType as _FunctionType
from types import ModuleType as _ModuleType
from typing import TYPE_CHECKING

_ORDERS_MODULE = "hmt_crypto.domain.private_orders"


# fmt: off
def _source_values(module: object, module_name: str, specs: tuple[tuple[str, str], ...]) -> tuple[object, ...] | None:
    """Snapshot exact re-exports against their already-loaded source modules."""
    if type(module) is not _ModuleType or type(module_name) is not str:
        return None
    try:
        namespace = dict.copy(object.__getattribute__(module, "__dict__"))
        modules = dict.copy(object.__getattribute__(_sys, "modules"))
    except (TypeError, RuntimeError):
        return None
    actual_name = dict.get(namespace, "__name__")
    if type(actual_name) is not str or actual_name != module_name:
        return None
    values: list[object] = []
    for name, source_name in specs:
        if type(name) is not str or type(source_name) is not str:
            return None
        source = dict.get(modules, source_name)
        if type(source) is not _ModuleType:
            return None
        try:
            source_namespace = dict.copy(object.__getattribute__(source, "__dict__"))
        except (TypeError, RuntimeError):
            return None
        source_actual = dict.get(source_namespace, "__name__")
        value = dict.get(namespace, name)
        if (
            type(source_actual) is not str
            or source_actual != source_name
            or value is not dict.get(source_namespace, name)
        ):
            return None
        values.append(value)
    return tuple(values)
def _leaf_snapshot(module: object, module_name: str, helper_name: str, capture_name: str, size: int) -> tuple[object, ...] | None:
    if type(module) is not _ModuleType:
        return None
    try:
        namespace = dict.copy(object.__getattribute__(module, "__dict__"))
    except (TypeError, RuntimeError):
        return None
    actual_module = dict.get(namespace, "__name__")
    helper = dict.get(namespace, helper_name)
    if (
        type(actual_module) is not str
        or actual_module != module_name
        or type(helper) is not _FunctionType
    ):
        return None
    defaults = object.__getattribute__(helper, "__defaults__")
    capture = tuple.__getitem__(defaults, 0) if type(defaults) is tuple and tuple.__len__(defaults) == 1 else None
    names, identities = (tuple.__getitem__(capture, 0), tuple.__getitem__(capture, 1)) if type(capture) is tuple and tuple.__len__(capture) == 2 else (None, None)
    if dict.get(namespace, capture_name) is not capture or type(names) is not tuple or type(identities) is not tuple or tuple.__len__(names) != tuple.__len__(identities) or not any(tuple.__getitem__(identities, index) is helper for index in range(tuple.__len__(identities))) or any(type(name) is not str or dict.get(namespace, name) is not tuple.__getitem__(identities, index) for index, name in enumerate(names)):
        return None
    helper_module = object.__getattribute__(helper, "__module__")
    actual_name = object.__getattribute__(helper, "__name__")
    qualname = object.__getattribute__(helper, "__qualname__")
    if not (
        type(helper_module) is str
        and helper_module == module_name
        and type(actual_name) is str
        and actual_name == helper_name
        and type(qualname) is str
        and qualname == helper_name
    ):
        return None
    snapshot = helper()
    return snapshot if type(snapshot) is tuple and tuple.__len__(snapshot) == size else None


# fmt: off
if TYPE_CHECKING:
    from hmt_crypto.domain._private_fill_contracts import (
        Fill, FillAssetSummary, FillContractType, FillMetadataProfile,
        FillMetadataValue, FillNotionalType, FillOrderRef, FillProductSummary,
        FillRole, FillSpotIndexSummary, FillType, NumericFillOrderRef,
        SettlementFillOrderProfile, SettlementFillOrderRef,
        parse_compact_fill_uuid, parse_numeric_fill_order_ref,
    )
    from hmt_crypto.domain._private_order_contracts import (
        MmpLevel, ObservedReduceOnly, Order, OrderSide, OrderState, OrderType,
        ReduceOnlyEncoding, StopOrderType, StopTriggerMethod, TimeInForce,
    )
    from hmt_crypto.domain._private_result_contracts import (
        DiagnosticPrivatePaginationResult, Leverage, LeverageEncoding, LookupFound,
        LookupNotFound, LookupResult, MarginModeResult, MutationAcknowledgement,
        PrivateCursorPage, ReviewedAbsenceCode, StrictPrivatePaginationResult,
    )
    from hmt_crypto.domain.account import ExactBool, FieldPresence, Observed, ObservedEnum, ValueField
    from hmt_crypto.domain.private_positions import LatestPosition, ObservedMarginMode
    from hmt_crypto.domain.private_primitives import (
        ObservedDeltaClientOrderId, PositivePrivateDecimal, PrivateDecimal,
    )
    from hmt_crypto.domain.private_wallet import WalletTransaction

    ObservedOrderSide = ObservedEnum[OrderSide]
    ObservedOrderType = ObservedEnum[OrderType]
    ObservedStopOrderType = ObservedEnum[StopOrderType]
    ObservedMmpLevel = ObservedEnum[MmpLevel]
    ObservedStopTriggerMethod = ObservedEnum[StopTriggerMethod]
    ObservedTimeInForce = ObservedEnum[TimeInForce]
    ObservedOrderState = ObservedEnum[OrderState]
    ObservedFillContractType = ObservedEnum[FillContractType]
    ObservedFillNotionalType = ObservedEnum[FillNotionalType]
    ObservedFillType = ObservedEnum[FillType]
    ObservedFillRole = ObservedEnum[FillRole]
    ExactBoolValueField = ValueField[ExactBool]
    PositivePrivateDecimalValueField = ValueField[PositivePrivateDecimal]
    ObservedMmpLevelValueField = ValueField[ObservedMmpLevel]
    ObservedStopTriggerMethodValueField = ValueField[ObservedStopTriggerMethod]
    ObservedTimeInForceValueField = ValueField[ObservedTimeInForce]
    PrivateDecimalValueField = ValueField[PrivateDecimal]
    ObservedDeltaClientOrderIdValueField = ValueField[ObservedDeltaClientOrderId]
    FillMetadataValueField = ValueField[FillMetadataValue]
    ExactBoolPresence = FieldPresence[ExactBool]
    PositivePrivateDecimalPresence = FieldPresence[PositivePrivateDecimal]
    ObservedMmpLevelPresence = FieldPresence[ObservedMmpLevel]
    ObservedStopTriggerMethodPresence = FieldPresence[ObservedStopTriggerMethod]
    ObservedTimeInForcePresence = FieldPresence[ObservedTimeInForce]
    PrivateDecimalPresence = FieldPresence[PrivateDecimal]
    ObservedDeltaClientOrderIdPresence = FieldPresence[ObservedDeltaClientOrderId]
    FillMetadataPresence = FieldPresence[FillMetadataValue]
    LatestPositionLookupFound = LookupFound[LatestPosition]
    OrderLookupFound = LookupFound[Order]
    LatestPositionLookupResult = LookupResult[LatestPosition]
    OrderLookupResult = LookupResult[Order]
    WalletTransactionCursorPage = PrivateCursorPage[WalletTransaction]
    OrderCursorPage = PrivateCursorPage[Order]
    FillCursorPage = PrivateCursorPage[Fill]
    StrictWalletTransactionPaginationResult = StrictPrivatePaginationResult[WalletTransaction]
    StrictOrderPaginationResult = StrictPrivatePaginationResult[Order]
    StrictFillPaginationResult = StrictPrivatePaginationResult[Fill]
    DiagnosticWalletTransactionPaginationResult = DiagnosticPrivatePaginationResult[WalletTransaction]
    DiagnosticOrderPaginationResult = DiagnosticPrivatePaginationResult[Order]
    DiagnosticFillPaginationResult = DiagnosticPrivatePaginationResult[Fill]
    ObservedLatestPositionLookupResult = Observed[LatestPositionLookupResult]
    ObservedOrderLookupResult = Observed[OrderLookupResult]
    ObservedLeverage = Observed[Leverage]
# fmt: on


if not TYPE_CHECKING:
    _failed = False
    _facade = globals()
    try:
        import hmt_crypto.domain._private_fill_contracts as _fill_leaf
        import hmt_crypto.domain._private_order_contracts as _order_leaf
        import hmt_crypto.domain._private_result_contracts as _result_leaf

        # fmt: off
        _order = _leaf_snapshot(
            _order_leaf, "hmt_crypto.domain._private_order_contracts",
            "_order_contract_snapshot", "_ORDER_SOURCE_CAPTURE", 13,
        )
        _fill = _leaf_snapshot(
            _fill_leaf, "hmt_crypto.domain._private_fill_contracts", "_fill_contract_snapshot", "_FILL_SOURCE_CAPTURE", 19
        )
        _result = _leaf_snapshot(
            _result_leaf, "hmt_crypto.domain._private_result_contracts",
            "_result_contract_snapshot", "_RESULT_SOURCE_CAPTURE", 14,
        )
        # fmt: on
        if _order is None or _fill is None or _result is None:
            raise RuntimeError("private orders preflight failed")

        # fmt: off
        _order_names = (
            "OrderSide", "OrderType", "OrderState", "StopOrderType", "MmpLevel",
            "StopTriggerMethod", "TimeInForce", "ReduceOnlyEncoding",
            "ObservedReduceOnly", "Order",
        )
        _fill_names = (
            "FillType", "FillRole", "FillContractType", "FillNotionalType",
            "FillMetadataProfile", "SettlementFillOrderProfile", "FillMetadataValue",
            "FillAssetSummary", "FillSpotIndexSummary", "FillProductSummary",
            "NumericFillOrderRef", "SettlementFillOrderRef", "Fill",
        )
        _result_names = (
            "LeverageEncoding", "Leverage", "MarginModeResult",
            "MutationAcknowledgement", "ReviewedAbsenceCode", "LookupFound",
            "LookupNotFound", "LookupResult", "PrivateCursorPage",
            "StrictPrivatePaginationResult", "DiagnosticPrivatePaginationResult",
        )
        # fmt: on
        for _snapshot, _names in (
            (_order, _order_names),
            (_fill, _fill_names),
            (_result, _result_names),
        ):
            for _index, _name in enumerate(_names):
                dict.__setitem__(_facade, _name, tuple.__getitem__(_snapshot, _index))
        for _name, _value in dict.items(dict.copy(tuple.__getitem__(_result, 13))):
            if not dict.__contains__(_facade, _name):
                dict.__setitem__(_facade, _name, _value)
        FillOrderRef = tuple.__getitem__(_fill, 17)
        parse_compact_fill_uuid = tuple.__getitem__(_fill, 13)
        parse_numeric_fill_order_ref = tuple.__getitem__(_fill, 14)
        _rebuild_order_independent = tuple.__getitem__(_order, 10)
        _rebuild_order_dependent = tuple.__getitem__(_order, 11)
        _rebuild_fill_independent = tuple.__getitem__(_fill, 15)
        _rebuild_fill_dependent = tuple.__getitem__(_fill, 16)
        _rebuild_result_independent = tuple.__getitem__(_result, 12)

        import hmt_crypto.domain.account as _account
        import hmt_crypto.domain.private_positions as _positions
        import hmt_crypto.domain.private_primitives as _primitives
        import hmt_crypto.domain.private_wallet as _wallet

        _account_specs = (
            ("ObservedEnum", "hmt_crypto.domain._private_generic_core"),
            ("ValueField", "hmt_crypto.domain._private_generic_core"),
            ("FieldPresence", "hmt_crypto.domain._private_generic_core"),
            ("Observed", "hmt_crypto.domain._private_generic_core"),
            ("ExactBool", "hmt_crypto.domain._account_contracts"),
            ("_ReviewedGenericSpecialization", "hmt_crypto.domain._private_generic_registry"),
            ("_declare_reviewed_model_candidate", "hmt_crypto.domain._private_generic_core"),
            ("_declare_reviewed_alias_candidate", "hmt_crypto.domain._private_generic_core"),
            ("_rebuild_reviewed_model_candidate", "hmt_crypto.domain._private_generic_core"),
            ("_load_reviewed_rows", "hmt_crypto.domain._private_generic_registry"),
        )
        _accounts = _source_values(_account, "hmt_crypto.domain.account", _account_specs)
        _primitive_specs = tuple(
            (_name, "hmt_crypto.domain.private_primitives")
            for _name in ("PositivePrivateDecimal", "PrivateDecimal", "ObservedDeltaClientOrderId")
        )
        _primitive_values = _source_values(
            _primitives, "hmt_crypto.domain.private_primitives", _primitive_specs
        )
        _wallet_values = _source_values(
            _wallet,
            "hmt_crypto.domain.private_wallet",
            (("WalletTransaction", "hmt_crypto.domain._private_wallet_contracts"),),
        )
        _position_values = _source_values(
            _positions,
            "hmt_crypto.domain.private_positions",
            (("LatestPosition", "hmt_crypto.domain._private_positions_contracts"),),
        )
        if any(
            _values is None
            for _values in (_accounts, _primitive_values, _wallet_values, _position_values)
        ):
            raise RuntimeError("private orders preflight failed")
        _loaded = tuple.__getitem__(_accounts, 9)()
        _result_types = tuple.__getitem__(_result, 13)
        if (
            type(_loaded) is not tuple
            or tuple.__len__(_loaded) != 8
            or object.__getattribute__(tuple.__getitem__(_loaded, 1), "specialization")
            is not dict.__getitem__(_result_types, "NullableOpaqueCursor")
            or object.__getattribute__(tuple.__getitem__(_loaded, 6), "specialization")
            is not dict.__getitem__(_result_types, "ObservedMarginMode")
        ):
            raise RuntimeError("private orders preflight failed")

        for _index, _name in enumerate(
            ("ObservedEnum", "ValueField", "FieldPresence", "Observed", "ExactBool")
        ):
            dict.__setitem__(_facade, _name, tuple.__getitem__(_accounts, _index))
        for _index, _name in enumerate(
            ("PositivePrivateDecimal", "PrivateDecimal", "ObservedDeltaClientOrderId")
        ):
            dict.__setitem__(_facade, _name, tuple.__getitem__(_primitive_values, _index))
        WalletTransaction = tuple.__getitem__(_wallet_values, 0)
        LatestPosition = tuple.__getitem__(_position_values, 0)
        ObservedMarginMode = _positions.ObservedMarginMode
        _Record = tuple.__getitem__(_accounts, 5)
        _model = tuple.__getitem__(_accounts, 6)
        _alias = tuple.__getitem__(_accounts, 7)
        _rebuild = tuple.__getitem__(_accounts, 8)

        _copy = dict.copy(_facade)
        _rebuild_order_independent(_copy, _order)
        _rebuild_fill_independent(_copy, _fill)
        _rebuild_result_independent(_copy, _result)

        # fmt: off
        _specs = (
            ("m", "ObservedEnum", "OrderSide", "ObservedOrderSide"),
            ("m", "ObservedEnum", "OrderType", "ObservedOrderType"),
            ("m", "ObservedEnum", "StopOrderType", "ObservedStopOrderType"),
            ("m", "ObservedEnum", "MmpLevel", "ObservedMmpLevel"),
            ("m", "ObservedEnum", "StopTriggerMethod", "ObservedStopTriggerMethod"),
            ("m", "ObservedEnum", "TimeInForce", "ObservedTimeInForce"),
            ("m", "ObservedEnum", "OrderState", "ObservedOrderState"),
            ("m", "ObservedEnum", "FillContractType", "ObservedFillContractType"),
            ("m", "ObservedEnum", "FillNotionalType", "ObservedFillNotionalType"),
            ("m", "ObservedEnum", "FillType", "ObservedFillType"),
            ("m", "ObservedEnum", "FillRole", "ObservedFillRole"),
            ("m", "ValueField", "ExactBool", "ExactBoolValueField"),
            ("m", "ValueField", "PositivePrivateDecimal", "PositivePrivateDecimalValueField"),
            ("m", "ValueField", "ObservedMmpLevel", "ObservedMmpLevelValueField"),
            ("m", "ValueField", "ObservedStopTriggerMethod", "ObservedStopTriggerMethodValueField"),
            ("m", "ValueField", "ObservedTimeInForce", "ObservedTimeInForceValueField"),
            ("m", "ValueField", "PrivateDecimal", "PrivateDecimalValueField"),
            ("m", "ValueField", "ObservedDeltaClientOrderId", "ObservedDeltaClientOrderIdValueField"),
            ("m", "ValueField", "FillMetadataValue", "FillMetadataValueField"),
            ("a", "FieldPresence", "ExactBool", "ExactBoolPresence"),
            ("a", "FieldPresence", "PositivePrivateDecimal", "PositivePrivateDecimalPresence"),
            ("a", "FieldPresence", "ObservedMmpLevel", "ObservedMmpLevelPresence"),
            ("a", "FieldPresence", "ObservedStopTriggerMethod", "ObservedStopTriggerMethodPresence"),
            ("a", "FieldPresence", "ObservedTimeInForce", "ObservedTimeInForcePresence"),
            ("a", "FieldPresence", "PrivateDecimal", "PrivateDecimalPresence"),
            ("a", "FieldPresence", "ObservedDeltaClientOrderId", "ObservedDeltaClientOrderIdPresence"),
            ("a", "FieldPresence", "FillMetadataValue", "FillMetadataPresence"),
            ("m", "LookupFound", "LatestPosition", "LatestPositionLookupFound"),
            ("m", "LookupFound", "Order", "OrderLookupFound"),
            ("a", "LookupResult", "LatestPosition", "LatestPositionLookupResult"),
            ("a", "LookupResult", "Order", "OrderLookupResult"),
            ("m", "PrivateCursorPage", "WalletTransaction", "WalletTransactionCursorPage"),
            ("m", "PrivateCursorPage", "Order", "OrderCursorPage"),
            ("m", "PrivateCursorPage", "Fill", "FillCursorPage"),
            ("m", "StrictPrivatePaginationResult", "WalletTransaction", "StrictWalletTransactionPaginationResult"),
            ("m", "StrictPrivatePaginationResult", "Order", "StrictOrderPaginationResult"),
            ("m", "StrictPrivatePaginationResult", "Fill", "StrictFillPaginationResult"),
            ("m", "DiagnosticPrivatePaginationResult", "WalletTransaction", "DiagnosticWalletTransactionPaginationResult"),
            ("m", "DiagnosticPrivatePaginationResult", "Order", "DiagnosticOrderPaginationResult"),
            ("m", "DiagnosticPrivatePaginationResult", "Fill", "DiagnosticFillPaginationResult"),
            ("m", "Observed", "LatestPositionLookupResult", "ObservedLatestPositionLookupResult"),
            ("m", "Observed", "OrderLookupResult", "ObservedOrderLookupResult"),
            ("m", "Observed", "Leverage", "ObservedLeverage"),
        )
        # fmt: on
        _rows: list[object] = []
        for _kind, _family_name, _payload_name, _candidate_name in _specs:
            _family = dict.__getitem__(_facade, _family_name)
            _payload = dict.__getitem__(_facade, _payload_name)
            _candidate = (_model if _kind == "m" else _alias)(
                _family,
                _payload,
                canonical_name=_candidate_name,
                owner_module=_ORDERS_MODULE,
            )
            dict.__setitem__(_facade, _candidate_name, _candidate)
            _rows.append(_Record(_family, _payload, _candidate))

        _ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS = tuple(_rows)
        for _index in range(19):
            _rebuild(
                tuple.__getitem__(_ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS, _index).specialization
            )
        _copy = dict.copy(_facade)
        _rebuild_order_dependent(_copy, _order)
        _rebuild_fill_dependent(_copy, _fill)
        for _index in range(27, 43):
            if tuple.__getitem__(_specs, _index)[0] == "m":
                _rebuild(
                    tuple.__getitem__(
                        _ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS, _index
                    ).specialization
                )
        for _row in _ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS:
            if _row.family[_row.payload] is not _row.specialization:
                raise RuntimeError("private orders binding failed")
    except Exception:
        _failed = True
        for _name in tuple(_facade):
            if (
                type(_name) is str
                and not _name.startswith("__")
                and _name not in ("_facade", "_failed")
            ):
                dict.pop(_facade, _name, None)

    if _failed:
        dict.pop(_facade, "_facade", None)
        raise RuntimeError("private domain import unavailable") from None

    for _name in tuple(_facade):
        if (
            type(_name) is str
            and _name.startswith("_")
            and not _name.startswith("__")
            and _name not in ("_ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS", "_facade")
        ):
            dict.pop(_facade, _name, None)
    dict.pop(_facade, "_facade", None)


# fmt: off
__all__ = [
    "DiagnosticFillPaginationResult", "DiagnosticOrderPaginationResult",
    "DiagnosticPrivatePaginationResult", "DiagnosticWalletTransactionPaginationResult",
    "Fill", "FillAssetSummary", "FillContractType", "FillMetadataPresence",
    "FillMetadataProfile", "FillMetadataValue", "FillMetadataValueField",
    "FillNotionalType", "FillOrderRef", "FillProductSummary", "FillRole",
    "FillSpotIndexSummary", "FillType", "Leverage", "LeverageEncoding",
    "LookupFound", "LookupNotFound", "LookupResult", "MarginModeResult", "MmpLevel",
    "MutationAcknowledgement", "NumericFillOrderRef", "ObservedFillContractType",
    "ObservedFillNotionalType", "ObservedFillRole", "ObservedFillType", "ObservedLeverage",
    "ObservedOrderLookupResult", "ObservedOrderSide", "ObservedOrderState",
    "ObservedOrderType", "ObservedReduceOnly", "ObservedStopOrderType",
    "ObservedStopTriggerMethod", "ObservedTimeInForce", "Order", "OrderCursorPage",
    "OrderLookupFound", "OrderLookupResult", "OrderSide", "OrderState", "OrderType",
    "PrivateCursorPage", "ReduceOnlyEncoding", "ReviewedAbsenceCode",
    "SettlementFillOrderProfile", "SettlementFillOrderRef", "StopOrderType",
    "StopTriggerMethod", "StrictFillPaginationResult", "StrictOrderPaginationResult",
    "StrictPrivatePaginationResult", "StrictWalletTransactionPaginationResult",
    "TimeInForce", "WalletTransactionCursorPage", "parse_compact_fill_uuid",
    "parse_numeric_fill_order_ref",
]
# fmt: on
