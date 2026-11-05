"""Public exact-value domain models and enumerations."""

from importlib import import_module
from typing import TYPE_CHECKING

from hmt_crypto.domain.enums import ContractType, NotionalType, ProductState, TradingStatus
from hmt_crypto.domain.models import Asset, Candle, Product, RequestReference, Ticker
from hmt_crypto.domain.money import (
    ExactDecimal,
    NonNegativeDecimal,
    PositiveDecimal,
    UtcDateTime,
    parse_decimal,
    parse_utc_datetime,
)

if TYPE_CHECKING:
    from hmt_crypto.domain.account import (
        AccountRef as AccountRef,
    )
    from hmt_crypto.domain.account import (
        CollectionMode as CollectionMode,
    )
    from hmt_crypto.domain.account import (
        MissingField as MissingField,
    )
    from hmt_crypto.domain.account import (
        NullField as NullField,
    )
    from hmt_crypto.domain.account import (
        ObservationContext as ObservationContext,
    )
    from hmt_crypto.domain.account import (
        PrivatePaginationProvenance as PrivatePaginationProvenance,
    )
    from hmt_crypto.domain.account import (
        TruncationReason as TruncationReason,
    )
    from hmt_crypto.domain.private_orders import (
        DiagnosticFillPaginationResult as DiagnosticFillPaginationResult,
    )
    from hmt_crypto.domain.private_orders import (
        DiagnosticOrderPaginationResult as DiagnosticOrderPaginationResult,
    )
    from hmt_crypto.domain.private_orders import (
        DiagnosticWalletTransactionPaginationResult as DiagnosticWalletTransactionPaginationResult,
    )
    from hmt_crypto.domain.private_orders import (
        Fill as Fill,
    )
    from hmt_crypto.domain.private_orders import (
        FillAssetSummary as FillAssetSummary,
    )
    from hmt_crypto.domain.private_orders import (
        FillContractType as FillContractType,
    )
    from hmt_crypto.domain.private_orders import (
        FillNotionalType as FillNotionalType,
    )
    from hmt_crypto.domain.private_orders import (
        FillOrderRef as FillOrderRef,
    )
    from hmt_crypto.domain.private_orders import (
        FillProductSummary as FillProductSummary,
    )
    from hmt_crypto.domain.private_orders import (
        FillRole as FillRole,
    )
    from hmt_crypto.domain.private_orders import (
        FillSpotIndexSummary as FillSpotIndexSummary,
    )
    from hmt_crypto.domain.private_orders import (
        FillType as FillType,
    )
    from hmt_crypto.domain.private_orders import (
        Leverage as Leverage,
    )
    from hmt_crypto.domain.private_orders import (
        LeverageEncoding as LeverageEncoding,
    )
    from hmt_crypto.domain.private_orders import (
        MmpLevel as MmpLevel,
    )
    from hmt_crypto.domain.private_orders import (
        NumericFillOrderRef as NumericFillOrderRef,
    )
    from hmt_crypto.domain.private_orders import (
        ObservedLatestPositionLookupResult as ObservedLatestPositionLookupResult,
    )
    from hmt_crypto.domain.private_orders import (
        ObservedLeverage as ObservedLeverage,
    )
    from hmt_crypto.domain.private_orders import (
        ObservedOrderLookupResult as ObservedOrderLookupResult,
    )
    from hmt_crypto.domain.private_orders import (
        ObservedReduceOnly as ObservedReduceOnly,
    )
    from hmt_crypto.domain.private_orders import (
        Order as Order,
    )
    from hmt_crypto.domain.private_orders import (
        OrderSide as OrderSide,
    )
    from hmt_crypto.domain.private_orders import (
        OrderState as OrderState,
    )
    from hmt_crypto.domain.private_orders import (
        OrderType as OrderType,
    )
    from hmt_crypto.domain.private_orders import (
        ReduceOnlyEncoding as ReduceOnlyEncoding,
    )
    from hmt_crypto.domain.private_orders import (
        StopOrderType as StopOrderType,
    )
    from hmt_crypto.domain.private_orders import (
        StopTriggerMethod as StopTriggerMethod,
    )
    from hmt_crypto.domain.private_orders import (
        StrictFillPaginationResult as StrictFillPaginationResult,
    )
    from hmt_crypto.domain.private_orders import (
        StrictOrderPaginationResult as StrictOrderPaginationResult,
    )
    from hmt_crypto.domain.private_orders import (
        StrictWalletTransactionPaginationResult as StrictWalletTransactionPaginationResult,
    )
    from hmt_crypto.domain.private_orders import (
        TimeInForce as TimeInForce,
    )
    from hmt_crypto.domain.private_positions import (
        LatestPosition as LatestPosition,
    )
    from hmt_crypto.domain.private_positions import (
        MarginedPosition as MarginedPosition,
    )
    from hmt_crypto.domain.private_positions import (
        MarginMode as MarginMode,
    )
    from hmt_crypto.domain.private_positions import (
        ObservedMarginedPositions as ObservedMarginedPositions,
    )
    from hmt_crypto.domain.private_positions import (
        PositionProductIdentity as PositionProductIdentity,
    )
    from hmt_crypto.domain.private_wallet import (
        Balance as Balance,
    )
    from hmt_crypto.domain.private_wallet import (
        BalanceSnapshot as BalanceSnapshot,
    )
    from hmt_crypto.domain.private_wallet import (
        ObservedBalanceSnapshot as ObservedBalanceSnapshot,
    )
    from hmt_crypto.domain.private_wallet import (
        WalletTransaction as WalletTransaction,
    )
    from hmt_crypto.domain.private_wallet import (
        WalletTransactionType as WalletTransactionType,
    )
else:
    _PRIVATE_EXPORTS = (
        ("AccountRef", "hmt_crypto.domain.account"),
        ("ObservationContext", "hmt_crypto.domain.account"),
        ("MissingField", "hmt_crypto.domain.account"),
        ("NullField", "hmt_crypto.domain.account"),
        ("CollectionMode", "hmt_crypto.domain.account"),
        ("TruncationReason", "hmt_crypto.domain.account"),
        ("PrivatePaginationProvenance", "hmt_crypto.domain.account"),
        ("WalletTransactionType", "hmt_crypto.domain.private_wallet"),
        ("Balance", "hmt_crypto.domain.private_wallet"),
        ("BalanceSnapshot", "hmt_crypto.domain.private_wallet"),
        ("WalletTransaction", "hmt_crypto.domain.private_wallet"),
        ("MarginMode", "hmt_crypto.domain.private_positions"),
        ("PositionProductIdentity", "hmt_crypto.domain.private_positions"),
        ("LatestPosition", "hmt_crypto.domain.private_positions"),
        ("MarginedPosition", "hmt_crypto.domain.private_positions"),
        *(
            (name, "hmt_crypto.domain.private_orders")
            for name in (
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
                "FillType",
                "FillRole",
                "FillContractType",
                "FillNotionalType",
                "FillAssetSummary",
                "FillSpotIndexSummary",
                "FillProductSummary",
                "NumericFillOrderRef",
                "FillOrderRef",
                "Fill",
                "LeverageEncoding",
                "Leverage",
            )
        ),
    )
    _PRIVATE_RESULT_EXPORTS = (
        ("ObservedBalanceSnapshot", "hmt_crypto.domain.private_wallet"),
        (
            "StrictWalletTransactionPaginationResult",
            "hmt_crypto.domain.private_orders",
        ),
        (
            "DiagnosticWalletTransactionPaginationResult",
            "hmt_crypto.domain.private_orders",
        ),
        ("ObservedLatestPositionLookupResult", "hmt_crypto.domain.private_orders"),
        ("ObservedMarginedPositions", "hmt_crypto.domain.private_positions"),
        ("StrictOrderPaginationResult", "hmt_crypto.domain.private_orders"),
        ("DiagnosticOrderPaginationResult", "hmt_crypto.domain.private_orders"),
        ("StrictFillPaginationResult", "hmt_crypto.domain.private_orders"),
        ("DiagnosticFillPaginationResult", "hmt_crypto.domain.private_orders"),
        ("ObservedOrderLookupResult", "hmt_crypto.domain.private_orders"),
        ("ObservedLeverage", "hmt_crypto.domain.private_orders"),
    )

    def __getattr__(name: str) -> object:
        """Resolve one exact approved private-domain package export lazily."""
        if type(name) is str:
            for public_name, module_name in (
                *_PRIVATE_EXPORTS,
                *_PRIVATE_RESULT_EXPORTS,
            ):
                if name == public_name:
                    value: object = getattr(import_module(module_name), public_name)
                    globals()[public_name] = value
                    return value
        raise AttributeError(
            "unknown domain package attribute",
            name="unknown domain package attribute",
            obj=None,
        )

    def __dir__() -> list[str]:
        """Return existing globals plus approved lazy names without resolving them."""
        return sorted({*globals(), *__all__})


__all__ = [
    "Asset",
    "Candle",
    "ContractType",
    "ExactDecimal",
    "NonNegativeDecimal",
    "NotionalType",
    "PositiveDecimal",
    "Product",
    "ProductState",
    "RequestReference",
    "Ticker",
    "TradingStatus",
    "UtcDateTime",
    "parse_decimal",
    "parse_utc_datetime",
    "AccountRef",
    "ObservationContext",
    "MissingField",
    "NullField",
    "CollectionMode",
    "TruncationReason",
    "PrivatePaginationProvenance",
    "WalletTransactionType",
    "Balance",
    "BalanceSnapshot",
    "WalletTransaction",
    "MarginMode",
    "PositionProductIdentity",
    "LatestPosition",
    "MarginedPosition",
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
    "FillType",
    "FillRole",
    "FillContractType",
    "FillNotionalType",
    "FillAssetSummary",
    "FillSpotIndexSummary",
    "FillProductSummary",
    "NumericFillOrderRef",
    "FillOrderRef",
    "Fill",
    "LeverageEncoding",
    "Leverage",
    "ObservedBalanceSnapshot",
    "StrictWalletTransactionPaginationResult",
    "DiagnosticWalletTransactionPaginationResult",
    "ObservedLatestPositionLookupResult",
    "ObservedMarginedPositions",
    "StrictOrderPaginationResult",
    "DiagnosticOrderPaginationResult",
    "StrictFillPaginationResult",
    "DiagnosticFillPaginationResult",
    "ObservedOrderLookupResult",
    "ObservedLeverage",
]
