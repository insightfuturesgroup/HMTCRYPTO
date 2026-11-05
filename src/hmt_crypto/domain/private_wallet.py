"""Closed wallet-domain contracts for Delta private observations."""

# ruff: noqa: I001 -- grouped type-only re-exports document facade identity.

from __future__ import annotations

import sys as _sys
from types import FunctionType as _FunctionType
from types import ModuleType as _ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hmt_crypto.domain._private_wallet_contracts import (
        Balance as Balance,
        BalanceSnapshot as BalanceSnapshot,
        WalletTransaction as WalletTransaction,
        WalletTransactionMetadataProfile as WalletTransactionMetadataProfile,
        WalletTransactionMetadataValue as WalletTransactionMetadataValue,
        WalletTransactionType as WalletTransactionType,
    )
    from hmt_crypto.domain.account import (
        FieldPresence as FieldPresence,
        MissingField as MissingField,
        NullField as NullField,
        Observed as Observed,
        ObservedEnum as ObservedEnum,
        ValueField as ValueField,
    )

    ObservedWalletTransactionType = ObservedEnum[WalletTransactionType]
    WalletTransactionMetadataValueField = ValueField[WalletTransactionMetadataValue]
    WalletTransactionMetadataPresence = FieldPresence[WalletTransactionMetadataValue]
    ObservedBalanceSnapshot = Observed[BalanceSnapshot]


def _wallet_account_snapshot(module: object) -> tuple[object, ...] | None:
    if type(module) is not _ModuleType:
        return None
    try:
        namespace = dict.copy(object.__getattribute__(module, "__dict__"))
        modules = dict.copy(object.__getattribute__(_sys, "modules"))
    except (TypeError, RuntimeError):
        return None
    module_name = dict.get(namespace, "__name__")
    if type(module_name) is not str or module_name != "hmt_crypto.domain.account":
        return None
    specs = (
        ("FieldPresence", "hmt_crypto.domain._private_generic_core"),
        ("MissingField", "hmt_crypto.domain._account_contracts"),
        ("NullField", "hmt_crypto.domain._account_contracts"),
        ("Observed", "hmt_crypto.domain._private_generic_core"),
        ("ObservedEnum", "hmt_crypto.domain._private_generic_core"),
        ("ValueField", "hmt_crypto.domain._private_generic_core"),
        ("_ReviewedGenericSpecialization", "hmt_crypto.domain._private_generic_registry"),
        ("_declare_reviewed_alias_candidate", "hmt_crypto.domain._private_generic_core"),
        ("_declare_reviewed_model_candidate", "hmt_crypto.domain._private_generic_core"),
        ("_rebuild_reviewed_model_candidate", "hmt_crypto.domain._private_generic_core"),
    )
    owned: list[object] = []
    for name, source_module_name in specs:
        if not dict.__contains__(namespace, name) or not dict.__contains__(
            modules, source_module_name
        ):
            return None
        source_module = dict.__getitem__(modules, source_module_name)
        if type(source_module) is not _ModuleType:
            return None
        try:
            source_namespace = dict.copy(object.__getattribute__(source_module, "__dict__"))
        except (TypeError, RuntimeError):
            return None
        actual_source_name = dict.get(source_namespace, "__name__")
        if (
            type(actual_source_name) is not str
            or actual_source_name != source_module_name
            or not dict.__contains__(source_namespace, name)
        ):
            return None
        value = dict.__getitem__(namespace, name)
        if value is not dict.__getitem__(source_namespace, name):
            del value
            return None
        owned.append(value)
    return tuple(owned)


if not TYPE_CHECKING:
    _import_failed = False
    _facade_namespace = globals()
    try:
        import hmt_crypto.domain._private_wallet_contracts as _construction

        if type(_construction) is not _ModuleType:
            raise RuntimeError("private wallet preflight failed")
        _construction_namespace = dict.copy(object.__getattribute__(_construction, "__dict__"))
        _construction_name = dict.get(_construction_namespace, "__name__")
        _snapshot_function = dict.get(_construction_namespace, "_wallet_contract_snapshot")
        if (
            type(_construction_name) is not str
            or _construction_name != "hmt_crypto.domain._private_wallet_contracts"
            or type(_snapshot_function) is not _FunctionType
        ):
            raise RuntimeError("private wallet preflight failed")
        _snapshot_state = (
            object.__getattribute__(_snapshot_function, "__module__"),
            object.__getattribute__(_snapshot_function, "__name__"),
            object.__getattribute__(_snapshot_function, "__qualname__"),
        )
        if any(
            type(actual) is not str or actual != expected
            for actual, expected in zip(
                _snapshot_state,
                (
                    "hmt_crypto.domain._private_wallet_contracts",
                    "_wallet_contract_snapshot",
                    "_wallet_contract_snapshot",
                ),
                strict=True,
            )
        ):
            raise RuntimeError("private wallet preflight failed")
        _contracts = _snapshot_function()
        if type(_contracts) is not tuple or tuple.__len__(_contracts) != 9:
            raise RuntimeError("private wallet preflight failed")
        for _binding in (
            "_construction",
            "_construction_name",
            "_construction_namespace",
            "_snapshot_state",
            "_snapshot_function",
        ):
            dict.pop(_facade_namespace, _binding, None)

        import hmt_crypto.domain.account as _account

        _accounts = _wallet_account_snapshot(_account)
        if type(_accounts) is not tuple or tuple.__len__(_accounts) != 10:
            raise RuntimeError("private wallet preflight failed")
        dict.pop(_facade_namespace, "_account", None)

        WalletTransactionType = tuple.__getitem__(_contracts, 0)
        WalletTransactionMetadataProfile = tuple.__getitem__(_contracts, 1)
        WalletTransactionMetadataValue = tuple.__getitem__(_contracts, 2)
        Balance = tuple.__getitem__(_contracts, 3)
        BalanceSnapshot = tuple.__getitem__(_contracts, 4)
        WalletTransaction = tuple.__getitem__(_contracts, 5)
        _rebuild_wallet_independent = tuple.__getitem__(_contracts, 7)
        _rebuild_wallet_dependent = tuple.__getitem__(_contracts, 8)

        FieldPresence = tuple.__getitem__(_accounts, 0)
        MissingField = tuple.__getitem__(_accounts, 1)
        NullField = tuple.__getitem__(_accounts, 2)
        Observed = tuple.__getitem__(_accounts, 3)
        ObservedEnum = tuple.__getitem__(_accounts, 4)
        ValueField = tuple.__getitem__(_accounts, 5)
        _ReviewedGenericSpecialization = tuple.__getitem__(_accounts, 6)
        _declare_reviewed_alias_candidate = tuple.__getitem__(_accounts, 7)
        _declare_reviewed_model_candidate = tuple.__getitem__(_accounts, 8)
        _rebuild_reviewed_model_candidate = tuple.__getitem__(_accounts, 9)

        _facade_copy = dict.copy(_facade_namespace)
        _rebuild_wallet_independent(_facade_copy, _contracts)

        ObservedWalletTransactionType = _declare_reviewed_model_candidate(
            ObservedEnum,
            WalletTransactionType,
            canonical_name="ObservedWalletTransactionType",
            owner_module="hmt_crypto.domain.private_wallet",
        )
        WalletTransactionMetadataValueField = _declare_reviewed_model_candidate(
            ValueField,
            WalletTransactionMetadataValue,
            canonical_name="WalletTransactionMetadataValueField",
            owner_module="hmt_crypto.domain.private_wallet",
        )
        WalletTransactionMetadataPresence = _declare_reviewed_alias_candidate(
            FieldPresence,
            WalletTransactionMetadataValue,
            canonical_name="WalletTransactionMetadataPresence",
            owner_module="hmt_crypto.domain.private_wallet",
        )
        ObservedBalanceSnapshot = _declare_reviewed_model_candidate(
            Observed,
            BalanceSnapshot,
            canonical_name="ObservedBalanceSnapshot",
            owner_module="hmt_crypto.domain.private_wallet",
        )

        _WALLET_REVIEWED_GENERIC_SPECIALIZATIONS = (
            _ReviewedGenericSpecialization(
                ObservedEnum, WalletTransactionType, ObservedWalletTransactionType
            ),
            _ReviewedGenericSpecialization(
                ValueField,
                WalletTransactionMetadataValue,
                WalletTransactionMetadataValueField,
            ),
            _ReviewedGenericSpecialization(
                FieldPresence,
                WalletTransactionMetadataValue,
                WalletTransactionMetadataPresence,
            ),
            _ReviewedGenericSpecialization(Observed, BalanceSnapshot, ObservedBalanceSnapshot),
        )

        _rebuild_reviewed_model_candidate(ObservedWalletTransactionType)
        _rebuild_reviewed_model_candidate(WalletTransactionMetadataValueField)
        _facade_copy = dict.copy(_facade_namespace)
        _rebuild_wallet_dependent(_facade_copy, _contracts)
        _rebuild_reviewed_model_candidate(ObservedBalanceSnapshot)

        if ObservedEnum[WalletTransactionType] is not ObservedWalletTransactionType:
            raise RuntimeError("wallet transaction type binding failed")
        if ValueField[WalletTransactionMetadataValue] is not WalletTransactionMetadataValueField:
            raise RuntimeError("wallet metadata value binding failed")
        if FieldPresence[WalletTransactionMetadataValue] is not WalletTransactionMetadataPresence:
            raise RuntimeError("wallet metadata presence binding failed")
        if Observed[BalanceSnapshot] is not ObservedBalanceSnapshot:
            raise RuntimeError("wallet balance observation binding failed")
    except Exception:
        _import_failed = True
        for _binding in (
            "Balance",
            "BalanceSnapshot",
            "FieldPresence",
            "MissingField",
            "NullField",
            "Observed",
            "ObservedBalanceSnapshot",
            "ObservedEnum",
            "ObservedWalletTransactionType",
            "ValueField",
            "WalletTransaction",
            "WalletTransactionMetadataPresence",
            "WalletTransactionMetadataProfile",
            "WalletTransactionMetadataValue",
            "WalletTransactionMetadataValueField",
            "WalletTransactionType",
            "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS",
            "_FunctionType",
            "_ModuleType",
            "_ReviewedGenericSpecialization",
            "_account",
            "_accounts",
            "_construction",
            "_construction_name",
            "_construction_namespace",
            "_contracts",
            "_declare_reviewed_alias_candidate",
            "_declare_reviewed_model_candidate",
            "_facade_copy",
            "_rebuild_reviewed_model_candidate",
            "_rebuild_wallet_dependent",
            "_rebuild_wallet_independent",
            "_snapshot_state",
            "_snapshot_function",
            "_sys",
            "_wallet_account_snapshot",
        ):
            dict.pop(_facade_namespace, _binding, None)
        dict.pop(_facade_namespace, "_binding", None)

    if _import_failed:
        dict.pop(_facade_namespace, "_facade_namespace", None)
        del _import_failed
        raise RuntimeError("private domain import unavailable") from None

    for _binding in (
        "_ReviewedGenericSpecialization",
        "_FunctionType",
        "_ModuleType",
        "_accounts",
        "_contracts",
        "_declare_reviewed_alias_candidate",
        "_declare_reviewed_model_candidate",
        "_facade_copy",
        "_rebuild_reviewed_model_candidate",
        "_rebuild_wallet_dependent",
        "_rebuild_wallet_independent",
        "_sys",
        "_wallet_account_snapshot",
    ):
        dict.pop(_facade_namespace, _binding, None)
    dict.pop(_facade_namespace, "_binding", None)
    dict.pop(_facade_namespace, "_facade_namespace", None)
    del _import_failed


__all__ = [
    "Balance",
    "BalanceSnapshot",
    "ObservedBalanceSnapshot",
    "ObservedWalletTransactionType",
    "WalletTransaction",
    "WalletTransactionMetadataPresence",
    "WalletTransactionMetadataProfile",
    "WalletTransactionMetadataValue",
    "WalletTransactionMetadataValueField",
    "WalletTransactionType",
]
