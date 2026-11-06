"""Construction-owned wallet contracts exposed by the private wallet facade."""

from __future__ import annotations

import sys as _sys  # noqa: F401 -- verified through an owned module snapshot.
from types import FunctionType, GenericAlias, ModuleType  # noqa: F401
from typing import TYPE_CHECKING, Annotated

from pydantic import BeforeValidator, ConfigDict

from hmt_crypto.domain.private_primitives import (
    AssetSymbol,
    ExactAwareUtcDatetime,
    ExactStrEnum,
    ExternalUserText,
    PrivateBoundaryModel,
    PrivateDecimal,
    PrivateId,
)

_WALLET_MODULE = "hmt_crypto.domain.private_wallet"

if TYPE_CHECKING:
    from hmt_crypto.domain.private_wallet import (
        ObservedWalletTransactionType,
        WalletTransactionMetadataPresence,
    )


class WalletTransactionType(ExactStrEnum):
    """Known Delta wallet transaction tokens retained without aliases."""

    __module__ = _WALLET_MODULE

    CASHFLOW = "cashflow"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    COMMISSION = "commission"
    CONVERSION = "conversion"
    FUNDING = "funding"
    SETTLEMENT = "settlement"
    LIQUIDATION_FEE = "liquidation_fee"
    SPOT_TRADE = "spot_trade"
    WITHDRAWAL_CANCELLATION = "withdrawal_cancellation"
    REFERRAL_BONUS = "referral_bonus"
    SUB_ACCOUNT_TRANSFER = "sub_account_transfer"
    COMMISSION_REBATE = "commission_rebate"
    PROMO_CREDIT = "promo_credit"
    TRADING_CREDITS = "trading_credits"
    TRADING_CREDITS_FORFEITED = "trading_credits_forfeited"
    TRADING_CREDITS_PAID = "trading_credits_paid"
    TRADING_FEE_CREDITS_PAID_LIQUIDATION_FEE = "trading_fee_credits_paid_liquidation_fee"
    TRADING_CREDITS_REVERTED = "trading_credits_reverted"
    INTEREST_CREDIT = "interest_credit"
    EXTERNAL_DEPOSIT = "external_deposit"
    CREDIT_LINE = "credit_line"
    TRADING_COMPETITION = "trading_competition"
    FUND_DEPOSIT = "fund_deposit"
    FUND_WITHDRAWAL = "fund_withdrawal"
    FUND_WALLET_DEPOSIT = "fund_wallet_deposit"
    FUND_WALLET_WITHDRAWAL = "fund_wallet_withdrawal"
    FUND_REWARD = "fund_reward"
    TRADE_FARMING_REWARD = "trade_farming_reward"
    REVERT = "revert"
    RAF_BONUS = "raf_bonus"
    FILL_APPROPRIATION = "fill_appropriation"
    INCIDENT_COMPENSATION = "incident_compensation"


class WalletTransactionMetadataProfile(ExactStrEnum):
    """Reviewed opaque metadata shapes that can cross the read boundary."""

    __module__ = _WALLET_MODULE

    EMPTY_OBJECT = "EMPTY_OBJECT"


class WalletTransactionMetadataValue(PrivateBoundaryModel):
    """A validated metadata shape; raw arbitrary metadata is never retained."""

    __module__ = _WALLET_MODULE
    model_config = ConfigDict(defer_build=True)

    profile: WalletTransactionMetadataProfile


class Balance(PrivateBoundaryModel):
    """One exact signed wallet balance observation."""

    __module__ = _WALLET_MODULE
    model_config = ConfigDict(defer_build=True)

    asset_id: PrivateId
    id: PrivateId
    user_id: ExternalUserText
    asset_symbol: AssetSymbol
    available_balance: PrivateDecimal
    available_balance_for_robo: PrivateDecimal
    balance: PrivateDecimal
    blocked_margin: PrivateDecimal
    commission: PrivateDecimal
    cross_asset_liability: PrivateDecimal
    cross_commission: PrivateDecimal
    cross_locked_collateral: PrivateDecimal
    cross_order_margin: PrivateDecimal
    cross_position_margin: PrivateDecimal
    interest_credit: PrivateDecimal
    order_margin: PrivateDecimal
    pending_referral_bonus: PrivateDecimal
    pending_trading_fee_credit: PrivateDecimal
    portfolio_margin: PrivateDecimal
    position_margin: PrivateDecimal
    trading_fee_credit: PrivateDecimal
    unvested_amount: PrivateDecimal


def _exact_balance_tuple(value: object) -> object:
    if type(value) is not tuple:
        raise ValueError("balances require an exact tuple")
    return value


_ExactBalances = Annotated[tuple[Balance, ...], BeforeValidator(_exact_balance_tuple)]


class BalanceSnapshot(PrivateBoundaryModel):
    """A complete exact-tuple balance snapshot and signed equity totals."""

    __module__ = _WALLET_MODULE
    model_config = ConfigDict(defer_build=True)

    balances: _ExactBalances
    net_equity: PrivateDecimal
    robo_trading_equity: PrivateDecimal

    @classmethod
    def _require_owned_instance(cls, value: object) -> None:
        super()._require_owned_instance(value)
        try:
            balances = object.__getattribute__(value, "balances")
        except Exception:
            balances = None
        if type(balances) is not tuple:
            del balances, value
            raise cls._safe_state_error() from None


class WalletTransaction(PrivateBoundaryModel):
    """One normalized wallet transaction with explicit metadata presence."""

    __module__ = _WALLET_MODULE
    model_config = ConfigDict(defer_build=True)

    id: PrivateId
    amount: PrivateDecimal
    balance: PrivateDecimal
    transaction_type: ObservedWalletTransactionType
    meta_data: WalletTransactionMetadataPresence
    product_id: PrivateId | None
    asset_id: PrivateId
    asset_symbol: AssetSymbol
    created_at: ExactAwareUtcDatetime


def _rebuild_wallet_independent(namespace: object, snapshot: object) -> None:
    """Build independent DTOs from an owned, preflighted facade snapshot."""
    if type(namespace) is not dict or type(snapshot) is not tuple or tuple.__len__(snapshot) != 9:
        raise RuntimeError("invalid wallet rebuild source") from None
    try:
        owned = dict.copy(namespace)
        types_namespace = dict.copy(tuple.__getitem__(snapshot, 6))
    except (TypeError, RuntimeError):
        raise RuntimeError("invalid wallet rebuild source") from None
    names = (
        "WalletTransactionType",
        "WalletTransactionMetadataProfile",
        "WalletTransactionMetadataValue",
        "Balance",
        "BalanceSnapshot",
        "WalletTransaction",
    )
    for index in range(tuple.__len__(names)):
        name = tuple.__getitem__(names, index)
        value = dict.get(owned, name)
        if value is not tuple.__getitem__(snapshot, index):
            del value
            raise RuntimeError("invalid wallet rebuild source") from None
        dict.__setitem__(types_namespace, name, value)
    models = (
        dict.__getitem__(owned, "WalletTransactionMetadataValue"),
        dict.__getitem__(owned, "Balance"),
        dict.__getitem__(owned, "BalanceSnapshot"),
    )
    for model in models:
        rebuild = type.__getattribute__(model, "model_rebuild")
        rebuild(force=True, raise_errors=True, _types_namespace=types_namespace)


def _rebuild_wallet_dependent(namespace: object, snapshot: object) -> None:
    """Build the dependent transaction DTO without consulting live leaf globals."""
    if type(namespace) is not dict or type(snapshot) is not tuple or tuple.__len__(snapshot) != 9:
        raise RuntimeError("invalid wallet rebuild source") from None
    try:
        owned = dict.copy(namespace)
        types_namespace = dict.copy(tuple.__getitem__(snapshot, 6))
    except (TypeError, RuntimeError):
        raise RuntimeError("invalid wallet rebuild source") from None
    source_names = (
        "WalletTransactionType",
        "WalletTransactionMetadataProfile",
        "WalletTransactionMetadataValue",
        "Balance",
        "BalanceSnapshot",
        "WalletTransaction",
    )
    for index in range(tuple.__len__(source_names)):
        name = tuple.__getitem__(source_names, index)
        value = dict.get(owned, name)
        if value is not tuple.__getitem__(snapshot, index):
            del value
            raise RuntimeError("invalid wallet rebuild source") from None
        dict.__setitem__(types_namespace, name, value)
    for name in ("ObservedWalletTransactionType", "WalletTransactionMetadataPresence"):
        if not dict.__contains__(owned, name):
            raise RuntimeError("invalid wallet rebuild source") from None
        dict.__setitem__(types_namespace, name, dict.__getitem__(owned, name))
    model = dict.__getitem__(owned, "WalletTransaction")
    rebuild = type.__getattribute__(model, "model_rebuild")
    rebuild(force=True, raise_errors=True, _types_namespace=types_namespace)


def _wallet_contract_snapshot() -> tuple[object, ...] | None:
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
        ("pydantic", ("BeforeValidator",)),
        (
            "hmt_crypto.domain.private_primitives",
            (
                "AssetSymbol",
                "ExactAwareUtcDatetime",
                "ExactStrEnum",
                "ExternalUserText",
                "PrivateBoundaryModel",
                "PrivateDecimal",
                "PrivateId",
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
        "WalletTransactionType",
        "WalletTransactionMetadataProfile",
        "WalletTransactionMetadataValue",
        "Balance",
        "BalanceSnapshot",
        "WalletTransaction",
        "ExactStrEnum",
        "PrivateBoundaryModel",
        "_rebuild_wallet_independent",
        "_rebuild_wallet_dependent",
    )
    values: list[object] = []
    for name in required:
        if not dict.__contains__(namespace, name):
            return None
        values.append(dict.__getitem__(namespace, name))
    enum_base = values[6]
    model_base = values[7]
    enum_metaclass = type(enum_base)
    model_metaclass = type(model_base)
    try:
        enum_base_mro = type.__getattribute__(enum_base, "__mro__")
        model_base_mro = type.__getattribute__(model_base, "__mro__")
    except Exception:
        return None
    specs = (
        (values[0], enum_metaclass, enum_base, enum_base_mro, "WalletTransactionType"),
        (
            values[1],
            enum_metaclass,
            enum_base,
            enum_base_mro,
            "WalletTransactionMetadataProfile",
        ),
        (
            values[2],
            model_metaclass,
            model_base,
            model_base_mro,
            "WalletTransactionMetadataValue",
        ),
        (values[3], model_metaclass, model_base, model_base_mro, "Balance"),
        (values[4], model_metaclass, model_base, model_base_mro, "BalanceSnapshot"),
        (values[5], model_metaclass, model_base, model_base_mro, "WalletTransaction"),
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
            or module != "hmt_crypto.domain.private_wallet"
            or type(name) is not str
            or name != expected_name
            or type(qualname) is not str
            or qualname != expected_name
        ):
            return None
    validator = dict.get(namespace, "_exact_balance_tuple")
    if type(validator) is not FunctionType:
        return None
    try:
        validator_module = object.__getattribute__(validator, "__module__")
        validator_name = object.__getattribute__(validator, "__name__")
        validator_qualname = object.__getattribute__(validator, "__qualname__")
    except Exception:
        return None
    if (
        type(validator_module) is not str
        or validator_module != "hmt_crypto.domain._private_wallet_contracts"
        or type(validator_name) is not str
        or validator_name != "_exact_balance_tuple"
        or type(validator_qualname) is not str
        or validator_qualname != "_exact_balance_tuple"
    ):
        return None
    alias = dict.get(namespace, "_ExactBalances")
    annotated_type = type(Annotated[object, None])
    if type(alias) is not annotated_type:
        return None
    try:
        inner = object.__getattribute__(alias, "__origin__")
        alias_args = object.__getattribute__(alias, "__args__")
        metadata = object.__getattribute__(alias, "__metadata__")
    except Exception:
        return None
    if type(inner) is not GenericAlias:
        return None
    try:
        origin = object.__getattribute__(inner, "__origin__")
        arguments = object.__getattribute__(inner, "__args__")
    except Exception:
        return None
    if (
        type(alias_args) is not tuple
        or tuple.__len__(alias_args) != 1
        or tuple.__getitem__(alias_args, 0) is not inner
        or type(metadata) is not tuple
        or tuple.__len__(metadata) != 1
        or origin is not tuple
        or type(arguments) is not tuple
        or tuple.__len__(arguments) != 2
        or tuple.__getitem__(arguments, 0) is not values[3]
        or tuple.__getitem__(arguments, 1) is not Ellipsis
    ):
        return None
    before = tuple.__getitem__(metadata, 0)
    if type(before) is not BeforeValidator:
        return None
    core_module = dict.get(modules, "pydantic_core")
    if type(core_module) is not module_type:
        return None
    try:
        core_namespace = dict.copy(object.__getattribute__(core_module, "__dict__"))
        before_func = object.__getattribute__(before, "func")
        json_input = object.__getattribute__(before, "json_schema_input_type")
    except (AttributeError, TypeError, RuntimeError):
        return None
    core_name = dict.get(core_namespace, "__name__")
    if (
        type(core_name) is not str
        or core_name != "pydantic_core"
        or before_func is not validator
        or json_input is not dict.get(core_namespace, "PydanticUndefined")
    ):
        return None
    for index, expected_name in (
        (8, "_rebuild_wallet_independent"),
        (9, "_rebuild_wallet_dependent"),
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
            or module != "hmt_crypto.domain._private_wallet_contracts"
            or type(name) is not str
            or name != expected_name
            or type(qualname) is not str
            or qualname != expected_name
        ):
            return None
    narrow: dict[str, object] = {}
    for name in (
        "AssetSymbol",
        "ExactAwareUtcDatetime",
        "ExactStrEnum",
        "ExternalUserText",
        "PrivateBoundaryModel",
        "PrivateDecimal",
        "PrivateId",
        "WalletTransactionType",
        "WalletTransactionMetadataProfile",
        "WalletTransactionMetadataValue",
        "Balance",
        "BalanceSnapshot",
        "WalletTransaction",
        "_ExactBalances",
    ):
        if not dict.__contains__(namespace, name):
            return None
        dict.__setitem__(narrow, name, dict.__getitem__(namespace, name))
    return (*values[:6], narrow, values[8], values[9])


__all__ = [
    "Balance",
    "BalanceSnapshot",
    "WalletTransaction",
    "WalletTransactionMetadataProfile",
    "WalletTransactionMetadataValue",
    "WalletTransactionType",
]
