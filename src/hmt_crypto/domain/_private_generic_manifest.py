"""Immutable string vocabulary for reviewed private generic specializations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hmt_crypto.domain._private_generic_provenance import _validate_source_directory


@dataclass(frozen=True, slots=True, eq=False)
class _ReviewedGenericRowSpec:
    candidate_kind: Literal["model", "alias"]
    family_module: str
    family_name: str
    payload_name: str
    specialization_name: str


@dataclass(frozen=True, slots=True, eq=False)
class _ReviewedGenericOwnerSpec:
    module_name: str
    table_name: str
    rows: tuple[_ReviewedGenericRowSpec, ...]


def _row(
    kind: Literal["model", "alias"],
    family_module: str,
    family_name: str,
    payload_name: str,
    specialization_name: str,
) -> _ReviewedGenericRowSpec:
    return _ReviewedGenericRowSpec(
        kind, family_module, family_name, payload_name, specialization_name
    )


_ACCOUNT = "hmt_crypto.domain.account"
_ORDERS = "hmt_crypto.domain.private_orders"
_REVIEWED_GENERIC_VOCABULARY = (
    _ReviewedGenericOwnerSpec(
        _ACCOUNT,
        "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS",
        (
            _row("model", _ACCOUNT, "ValueField", "OpaqueCursor", "OpaqueCursorValueField"),
            _row("alias", _ACCOUNT, "NullableField", "OpaqueCursor", "NullableOpaqueCursor"),
        ),
    ),
    _ReviewedGenericOwnerSpec(
        "hmt_crypto.domain.private_wallet",
        "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
        (
            _row(
                "model",
                _ACCOUNT,
                "ObservedEnum",
                "WalletTransactionType",
                "ObservedWalletTransactionType",
            ),
            _row(
                "model",
                _ACCOUNT,
                "ValueField",
                "WalletTransactionMetadataValue",
                "WalletTransactionMetadataValueField",
            ),
            _row(
                "alias",
                _ACCOUNT,
                "FieldPresence",
                "WalletTransactionMetadataValue",
                "WalletTransactionMetadataPresence",
            ),
            _row("model", _ACCOUNT, "Observed", "BalanceSnapshot", "ObservedBalanceSnapshot"),
        ),
    ),
    _ReviewedGenericOwnerSpec(
        "hmt_crypto.domain.private_positions",
        "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
        (
            _row("model", _ACCOUNT, "ObservedEnum", "MarginMode", "ObservedMarginMode"),
            _row(
                "model", _ACCOUNT, "Observed", "MarginedPositionBatch", "ObservedMarginedPositions"
            ),
        ),
    ),
    _ReviewedGenericOwnerSpec(
        _ORDERS,
        "_ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS",
        (
            _row("model", _ACCOUNT, "ObservedEnum", "OrderSide", "ObservedOrderSide"),
            _row("model", _ACCOUNT, "ObservedEnum", "OrderType", "ObservedOrderType"),
            _row("model", _ACCOUNT, "ObservedEnum", "StopOrderType", "ObservedStopOrderType"),
            _row("model", _ACCOUNT, "ObservedEnum", "MmpLevel", "ObservedMmpLevel"),
            _row(
                "model", _ACCOUNT, "ObservedEnum", "StopTriggerMethod", "ObservedStopTriggerMethod"
            ),
            _row("model", _ACCOUNT, "ObservedEnum", "TimeInForce", "ObservedTimeInForce"),
            _row("model", _ACCOUNT, "ObservedEnum", "OrderState", "ObservedOrderState"),
            _row("model", _ACCOUNT, "ObservedEnum", "FillContractType", "ObservedFillContractType"),
            _row("model", _ACCOUNT, "ObservedEnum", "FillNotionalType", "ObservedFillNotionalType"),
            _row("model", _ACCOUNT, "ObservedEnum", "FillType", "ObservedFillType"),
            _row("model", _ACCOUNT, "ObservedEnum", "FillRole", "ObservedFillRole"),
            _row("model", _ACCOUNT, "ValueField", "ExactBool", "ExactBoolValueField"),
            _row(
                "model",
                _ACCOUNT,
                "ValueField",
                "PositivePrivateDecimal",
                "PositivePrivateDecimalValueField",
            ),
            _row("model", _ACCOUNT, "ValueField", "ObservedMmpLevel", "ObservedMmpLevelValueField"),
            _row(
                "model",
                _ACCOUNT,
                "ValueField",
                "ObservedStopTriggerMethod",
                "ObservedStopTriggerMethodValueField",
            ),
            _row(
                "model",
                _ACCOUNT,
                "ValueField",
                "ObservedTimeInForce",
                "ObservedTimeInForceValueField",
            ),
            _row("model", _ACCOUNT, "ValueField", "PrivateDecimal", "PrivateDecimalValueField"),
            _row(
                "model",
                _ACCOUNT,
                "ValueField",
                "ObservedDeltaClientOrderId",
                "ObservedDeltaClientOrderIdValueField",
            ),
            _row("model", _ACCOUNT, "ValueField", "FillMetadataValue", "FillMetadataValueField"),
            _row("alias", _ACCOUNT, "FieldPresence", "ExactBool", "ExactBoolPresence"),
            _row(
                "alias",
                _ACCOUNT,
                "FieldPresence",
                "PositivePrivateDecimal",
                "PositivePrivateDecimalPresence",
            ),
            _row(
                "alias", _ACCOUNT, "FieldPresence", "ObservedMmpLevel", "ObservedMmpLevelPresence"
            ),
            _row(
                "alias",
                _ACCOUNT,
                "FieldPresence",
                "ObservedStopTriggerMethod",
                "ObservedStopTriggerMethodPresence",
            ),
            _row(
                "alias",
                _ACCOUNT,
                "FieldPresence",
                "ObservedTimeInForce",
                "ObservedTimeInForcePresence",
            ),
            _row("alias", _ACCOUNT, "FieldPresence", "PrivateDecimal", "PrivateDecimalPresence"),
            _row(
                "alias",
                _ACCOUNT,
                "FieldPresence",
                "ObservedDeltaClientOrderId",
                "ObservedDeltaClientOrderIdPresence",
            ),
            _row("alias", _ACCOUNT, "FieldPresence", "FillMetadataValue", "FillMetadataPresence"),
            _row("model", _ORDERS, "LookupFound", "LatestPosition", "LatestPositionLookupFound"),
            _row("model", _ORDERS, "LookupFound", "Order", "OrderLookupFound"),
            _row("alias", _ORDERS, "LookupResult", "LatestPosition", "LatestPositionLookupResult"),
            _row("alias", _ORDERS, "LookupResult", "Order", "OrderLookupResult"),
            _row(
                "model",
                _ORDERS,
                "PrivateCursorPage",
                "WalletTransaction",
                "WalletTransactionCursorPage",
            ),
            _row("model", _ORDERS, "PrivateCursorPage", "Order", "OrderCursorPage"),
            _row("model", _ORDERS, "PrivateCursorPage", "Fill", "FillCursorPage"),
            _row(
                "model",
                _ORDERS,
                "StrictPrivatePaginationResult",
                "WalletTransaction",
                "StrictWalletTransactionPaginationResult",
            ),
            _row(
                "model",
                _ORDERS,
                "StrictPrivatePaginationResult",
                "Order",
                "StrictOrderPaginationResult",
            ),
            _row(
                "model",
                _ORDERS,
                "StrictPrivatePaginationResult",
                "Fill",
                "StrictFillPaginationResult",
            ),
            _row(
                "model",
                _ORDERS,
                "DiagnosticPrivatePaginationResult",
                "WalletTransaction",
                "DiagnosticWalletTransactionPaginationResult",
            ),
            _row(
                "model",
                _ORDERS,
                "DiagnosticPrivatePaginationResult",
                "Order",
                "DiagnosticOrderPaginationResult",
            ),
            _row(
                "model",
                _ORDERS,
                "DiagnosticPrivatePaginationResult",
                "Fill",
                "DiagnosticFillPaginationResult",
            ),
            _row(
                "model",
                _ACCOUNT,
                "Observed",
                "LatestPositionLookupResult",
                "ObservedLatestPositionLookupResult",
            ),
            _row("model", _ACCOUNT, "Observed", "OrderLookupResult", "ObservedOrderLookupResult"),
            _row("model", _ACCOUNT, "Observed", "Leverage", "ObservedLeverage"),
        ),
    ),
)


def _reviewed_vocabulary_for_verification() -> tuple[_ReviewedGenericOwnerSpec, ...]:
    """Return the immutable exact vocabulary for structural verification."""
    return _REVIEWED_GENERIC_VOCABULARY


_validate_source_directory(
    tuple(
        (
            owner.module_name,
            row.candidate_kind,
            row.family_module,
            row.family_name,
            row.payload_name,
            row.specialization_name,
        )
        for owner in _REVIEWED_GENERIC_VOCABULARY
        for row in owner.rows
    )
)
