"""Inert, closed account-domain contracts for Delta private observations."""

# ruff: noqa: I001 -- grouped same-name imports make facade identity explicit.

from __future__ import annotations

from typing import TYPE_CHECKING

from hmt_crypto.domain._account_contracts import (
    AccountRef as AccountRef,
    CollectionMode as CollectionMode,
    DeltaRestSource as DeltaRestSource,
    DiagnosticPaginationKind as DiagnosticPaginationKind,
    ExactBool as ExactBool,
    ExactDeltaEnvironment as ExactDeltaEnvironment,
    ExactDeltaRegion as ExactDeltaRegion,
    ExactExecutionMode as ExactExecutionMode,
    ExactTrue as ExactTrue,
    LookupFoundKind as LookupFoundKind,
    LookupNotFoundKind as LookupNotFoundKind,
    MissingField as MissingField,
    MissingKind as MissingKind,
    NullField as NullField,
    NullKind as NullKind,
    NumericFillOrderKind as NumericFillOrderKind,
    ObservationContext as ObservationContext,
    PrivatePaginationProvenance as PrivatePaginationProvenance,
    PrivatePolicyVersion as PrivatePolicyVersion,
    SettlementFillOrderKind as SettlementFillOrderKind,
    StrictPaginationKind as StrictPaginationKind,
    TruncationReason as TruncationReason,
    ValueKind as ValueKind,
    _ExactBoolean as _ExactBoolean,
    _ExactBooleanLiteral as _ExactBooleanLiteral,
    _ExactStringLiteral as _ExactStringLiteral,
    _rebuild_account_contracts,
)
from hmt_crypto.domain._private_generic_core import (
    FieldPresence as FieldPresence,
    NullableField as NullableField,
    Observed as Observed,
    ObservedEnum as ObservedEnum,
    ValueField as ValueField,
    _ClosedAliasMeta as _ClosedAliasMeta,
    _ClosedPrivateAlias as _ClosedPrivateAlias,
    _ClosedPrivateGenericModel as _ClosedPrivateGenericModel,
    _ExactTaggedUnion as _ExactTaggedUnion,
    _RejectedPrivateGenericSpecialization as _RejectedPrivateGenericSpecialization,
    _RequireClosedGenericAlias as _RequireClosedGenericAlias,
    _closed_alias_subscription as _closed_alias_subscription,
    _closed_model_subscription as _closed_model_subscription,
    _declare_reviewed_alias_candidate as _declare_reviewed_alias_candidate,
    _declare_reviewed_model_candidate as _declare_reviewed_model_candidate,
    _rebuild_reviewed_model_candidate,
)
from hmt_crypto.domain._private_generic_registry import (
    _ReviewedGenericSpecialization as _ReviewedGenericSpecialization,
    _load_reviewed_rows as _load_reviewed_rows,
    _verify_complete_manifest,
)
from hmt_crypto.domain.private_primitives import OpaqueCursor as OpaqueCursor

_REVIEWED_GENERIC_OWNER_TABLES = (
    ("hmt_crypto.domain.account", "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS", 2),
    ("hmt_crypto.domain.private_wallet", "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS", 4),
    (
        "hmt_crypto.domain.private_positions",
        "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS",
        2,
    ),
    ("hmt_crypto.domain.private_orders", "_ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS", 43),
)

_rebuild_account_contracts()

if TYPE_CHECKING:
    OpaqueCursorValueField = ValueField[OpaqueCursor]
    NullableOpaqueCursor = NullableField[OpaqueCursor]
else:
    OpaqueCursorValueField = _declare_reviewed_model_candidate(
        ValueField,
        OpaqueCursor,
        canonical_name="OpaqueCursorValueField",
        owner_module="hmt_crypto.domain.account",
    )
    NullableOpaqueCursor = _declare_reviewed_alias_candidate(
        NullableField,
        OpaqueCursor,
        canonical_name="NullableOpaqueCursor",
        owner_module="hmt_crypto.domain.account",
    )

_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS = (
    _ReviewedGenericSpecialization(ValueField, OpaqueCursor, OpaqueCursorValueField),
    _ReviewedGenericSpecialization(NullableField, OpaqueCursor, NullableOpaqueCursor),
)

_rebuild_reviewed_model_candidate(OpaqueCursorValueField)
if ValueField[OpaqueCursor] is not OpaqueCursorValueField:
    raise RuntimeError("account generic model binding failed")
if NullableField[OpaqueCursor] is not NullableOpaqueCursor:
    raise RuntimeError("account generic alias binding failed")


def _reviewed_generic_manifest_for_verification() -> tuple[_ReviewedGenericSpecialization, ...]:
    """Return all 51 runtime rows only after every owner is fully bound."""
    return _verify_complete_manifest()
