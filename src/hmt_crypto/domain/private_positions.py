"""Closed position-domain contracts for Delta private observations."""

# ruff: noqa: I001 -- grouped type-only re-exports document facade identity.

from __future__ import annotations

import sys as _sys
from types import FunctionType as _FunctionType
from types import ModuleType as _ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hmt_crypto.domain._private_positions_contracts import (
        LatestPosition as LatestPosition,
        MarginMode as MarginMode,
        MarginedPosition as MarginedPosition,
        MarginedPositionBatch as MarginedPositionBatch,
        PositionProductIdentity as PositionProductIdentity,
    )
    from hmt_crypto.domain.account import (
        ExactBool as ExactBool,
        ExactTrue as ExactTrue,
        Observed as Observed,
        ObservedEnum as ObservedEnum,
    )

    ObservedMarginMode = ObservedEnum[MarginMode]
    ObservedMarginedPositions = Observed[MarginedPositionBatch]

_POSITIONS_MODULE = "hmt_crypto.domain.private_positions"


def _positions_account_snapshot(module: object) -> tuple[object, ...] | None:
    """Own exact account re-exports without importing another source module."""
    if type(module) is not _ModuleType:
        return None
    try:
        namespace = dict.copy(object.__getattribute__(module, "__dict__"))
        modules = dict.copy(object.__getattribute__(_sys, "modules"))
    except (TypeError, RuntimeError):
        return None
    if not dict.__contains__(namespace, "__name__"):
        return None
    module_name = dict.__getitem__(namespace, "__name__")
    if type(module_name) is not str or module_name != "hmt_crypto.domain.account":
        return None
    specs = (
        ("ExactBool", "hmt_crypto.domain._account_contracts"),
        ("ExactTrue", "hmt_crypto.domain._account_contracts"),
        ("Observed", "hmt_crypto.domain._private_generic_core"),
        ("ObservedEnum", "hmt_crypto.domain._private_generic_core"),
        (
            "_ReviewedGenericSpecialization",
            "hmt_crypto.domain._private_generic_registry",
        ),
        (
            "_declare_reviewed_model_candidate",
            "hmt_crypto.domain._private_generic_core",
        ),
        (
            "_rebuild_reviewed_model_candidate",
            "hmt_crypto.domain._private_generic_core",
        ),
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
        if not dict.__contains__(source_namespace, "__name__"):
            return None
        actual_source_name = dict.__getitem__(source_namespace, "__name__")
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
        import hmt_crypto.domain._private_positions_contracts as _construction

        if type(_construction) is not _ModuleType:
            raise RuntimeError("private positions preflight failed")
        _construction_namespace = dict.copy(object.__getattribute__(_construction, "__dict__"))
        _construction_name = dict.get(_construction_namespace, "__name__")
        _snapshot_function = dict.get(_construction_namespace, "_positions_contract_snapshot")
        if (
            type(_construction_name) is not str
            or _construction_name != "hmt_crypto.domain._private_positions_contracts"
            or type(_snapshot_function) is not _FunctionType
        ):
            raise RuntimeError("private positions preflight failed")
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
                    "hmt_crypto.domain._private_positions_contracts",
                    "_positions_contract_snapshot",
                    "_positions_contract_snapshot",
                ),
                strict=True,
            )
        ):
            raise RuntimeError("private positions preflight failed")
        _contracts = _snapshot_function()
        if type(_contracts) is not tuple or tuple.__len__(_contracts) != 10:
            raise RuntimeError("private positions preflight failed")
        for _binding in (
            "_construction",
            "_construction_name",
            "_construction_namespace",
            "_snapshot_state",
            "_snapshot_function",
        ):
            dict.pop(_facade_namespace, _binding, None)

        import hmt_crypto.domain.account as _account

        _accounts = _positions_account_snapshot(_account)
        if type(_accounts) is not tuple or tuple.__len__(_accounts) != 7:
            raise RuntimeError("private positions preflight failed")
        dict.pop(_facade_namespace, "_account", None)

        MarginMode = tuple.__getitem__(_contracts, 0)
        PositionProductIdentity = tuple.__getitem__(_contracts, 1)
        LatestPosition = tuple.__getitem__(_contracts, 2)
        MarginedPosition = tuple.__getitem__(_contracts, 3)
        MarginedPositionBatch = tuple.__getitem__(_contracts, 4)
        ExactBool = tuple.__getitem__(_contracts, 5)
        ExactTrue = tuple.__getitem__(_contracts, 6)
        _rebuild_positions_independent = tuple.__getitem__(_contracts, 8)
        _rebuild_positions_dependent = tuple.__getitem__(_contracts, 9)

        if ExactBool is not tuple.__getitem__(_accounts, 0) or ExactTrue is not tuple.__getitem__(
            _accounts, 1
        ):
            raise RuntimeError("private positions preflight failed")
        Observed = tuple.__getitem__(_accounts, 2)
        ObservedEnum = tuple.__getitem__(_accounts, 3)
        _ReviewedGenericSpecialization = tuple.__getitem__(_accounts, 4)
        _declare_reviewed_model_candidate = tuple.__getitem__(_accounts, 5)
        _rebuild_reviewed_model_candidate = tuple.__getitem__(_accounts, 6)

        _facade_copy = dict.copy(_facade_namespace)
        _rebuild_positions_independent(_facade_copy, _contracts)

        ObservedMarginMode = _declare_reviewed_model_candidate(
            ObservedEnum,
            MarginMode,
            canonical_name="ObservedMarginMode",
            owner_module=_POSITIONS_MODULE,
        )
        ObservedMarginedPositions = _declare_reviewed_model_candidate(
            Observed,
            MarginedPositionBatch,
            canonical_name="ObservedMarginedPositions",
            owner_module=_POSITIONS_MODULE,
        )

        _POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS = (
            _ReviewedGenericSpecialization(ObservedEnum, MarginMode, ObservedMarginMode),
            _ReviewedGenericSpecialization(
                Observed, MarginedPositionBatch, ObservedMarginedPositions
            ),
        )

        _rebuild_reviewed_model_candidate(ObservedMarginMode)
        _facade_copy = dict.copy(_facade_namespace)
        _rebuild_positions_dependent(_facade_copy, _contracts)
        _rebuild_reviewed_model_candidate(ObservedMarginedPositions)

        if ObservedEnum[MarginMode] is not ObservedMarginMode:
            raise RuntimeError("position margin-mode binding failed")
        if Observed[MarginedPositionBatch] is not ObservedMarginedPositions:
            raise RuntimeError("margined position observation binding failed")
    except Exception:
        _import_failed = True
        for _binding in (
            "ExactBool",
            "ExactTrue",
            "LatestPosition",
            "MarginMode",
            "MarginedPosition",
            "MarginedPositionBatch",
            "Observed",
            "ObservedEnum",
            "ObservedMarginedPositions",
            "ObservedMarginMode",
            "PositionProductIdentity",
            "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
            "_FunctionType",
            "_ModuleType",
            "_ReviewedGenericSpecialization",
            "_account",
            "_accounts",
            "_construction",
            "_construction_name",
            "_construction_namespace",
            "_contracts",
            "_declare_reviewed_model_candidate",
            "_facade_copy",
            "_positions_account_snapshot",
            "_rebuild_positions_dependent",
            "_rebuild_positions_independent",
            "_rebuild_reviewed_model_candidate",
            "_snapshot_state",
            "_snapshot_function",
            "_sys",
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
        "_declare_reviewed_model_candidate",
        "_facade_copy",
        "_positions_account_snapshot",
        "_rebuild_positions_dependent",
        "_rebuild_positions_independent",
        "_rebuild_reviewed_model_candidate",
        "_sys",
    ):
        dict.pop(_facade_namespace, _binding, None)
    dict.pop(_facade_namespace, "_binding", None)
    dict.pop(_facade_namespace, "_facade_namespace", None)
    del _import_failed


__all__ = [
    "LatestPosition",
    "MarginMode",
    "MarginedPosition",
    "MarginedPositionBatch",
    "ObservedMarginedPositions",
    "ObservedMarginMode",
    "PositionProductIdentity",
]
