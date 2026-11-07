"""Strict canonical v1 risk-decision evidence.

The payload records historical policy output only.  It is deliberately free of
network, exchange, repository, and dispatch capabilities.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, NoReturn, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.types import _decimal_text, _utc

CHECK_KINDS = (
    "PRODUCT_CATALOGUE",
    "MARKET_TRADABILITY",
    "MAX_POSITION",
    "TOTAL_EXPOSURE",
    "MAX_LEVERAGE",
    "MAX_LOSS_PER_TRADE",
    "MAX_DAILY_LOSS",
    "MAX_OPEN_ORDERS",
    "MAX_ORDER_RATE",
    "PRICE_COLLAR",
    "DUPLICATE_ORDER",
    "COLLATERAL_MARGIN",
    "KILL_HALT",
    "FRESHNESS_RECONCILIATION",
    "PROTECTION",
    "REDUCE_ONLY_NO_REVERSAL",
)
FAIL_REASONS = {
    "PRODUCT_CATALOGUE": frozenset({"UNSUPPORTED_PRODUCT", "PRODUCT_DISABLED", "POLICY_BLOCKED"}),
    "MARKET_TRADABILITY": frozenset({"MARKET_NOT_TRADABLE", "POLICY_BLOCKED"}),
    "MAX_POSITION": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
    "TOTAL_EXPOSURE": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
    "MAX_LEVERAGE": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
    "MAX_LOSS_PER_TRADE": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
    "MAX_DAILY_LOSS": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
    "MAX_OPEN_ORDERS": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
    "MAX_ORDER_RATE": frozenset({"LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
    "PRICE_COLLAR": frozenset({"PRICE_OUTSIDE_COLLAR", "POLICY_BLOCKED"}),
    "DUPLICATE_ORDER": frozenset({"DUPLICATE_ORDER", "POLICY_BLOCKED"}),
    "COLLATERAL_MARGIN": frozenset({"INSUFFICIENT_COLLATERAL", "POLICY_BLOCKED"}),
    "KILL_HALT": frozenset({"HALT_ACTIVE", "POLICY_BLOCKED"}),
    "FRESHNESS_RECONCILIATION": frozenset({"RECONCILIATION_NOT_READY", "POLICY_BLOCKED"}),
    "PROTECTION": frozenset({"PROTECTION_INADEQUATE", "POLICY_BLOCKED"}),
    "REDUCE_ONLY_NO_REVERSAL": frozenset({"POSITION_REVERSAL", "LIMIT_EXCEEDED", "POLICY_BLOCKED"}),
}

CHECK_EVIDENCE_FIELDS = frozenset(
    {
        "check_kind",
        "outcome",
        "reason_code",
        "metric_kind",
        "observed_decimal",
        "limit_decimal",
        "observed_integer",
        "limit_integer",
        "unit",
        "evidence_observed_at",
        "input_set_sha256",
        "evidence_sha256",
    }
)
CHECK_INPUT_FIELDS = CHECK_EVIDENCE_FIELDS - {"evidence_sha256"}
RESERVATION_PLAN_STORED_FIELDS = frozenset(
    {
        "approved_contracts",
        "approved_side",
        "reference_asset_id",
        "reference_asset_symbol",
        "collateral_asset_id",
        "collateral_asset_symbol",
        "reserved_incremental_gross_exposure",
        "reserved_worst_case_loss",
        "reserved_collateral",
        "reserved_open_order_slots",
        "reserved_pending_rate_units",
        "rate_policy_version",
        "rate_policy_sha256",
        "rate_window_start_at",
        "rate_window_end_at",
        "reservation_plan_sha256",
    }
)
RESERVATION_PLAN_INPUT_FIELDS = RESERVATION_PLAN_STORED_FIELDS - {"reservation_plan_sha256"}
DECISION_PAYLOAD_FIELDS = frozenset(
    {
        "account_id",
        "check_results",
        "check_set_sha256",
        "check_set_version",
        "command_evidence_payload",
        "command_evidence_schema_version",
        "command_evidence_sha256",
        "command_id",
        "command_operation_kind",
        "command_requested_contracts",
        "decision_id",
        "decision_key",
        "environment",
        "evaluated_at",
        "evaluation_collateral_asset_id",
        "evaluation_collateral_asset_symbol",
        "evaluation_reference_asset_id",
        "evaluation_reference_asset_symbol",
        "leadership_lease_id",
        "mode",
        "observed_accounting_state",
        "observed_claim_expires_at",
        "observed_claim_generation",
        "observed_claim_owner_id",
        "observed_command_leader_generation",
        "observed_command_leader_ref",
        "observed_command_recovery_generation",
        "observed_command_recovery_ref",
        "observed_command_version",
        "observed_halt_revision",
        "observed_halt_set_state",
        "observed_leadership_expires_at",
        "observed_leadership_generation",
        "observed_leadership_owner_id",
        "observed_leadership_version",
        "observed_order_transition_sequence",
        "observed_order_version",
        "observed_reconciliation_state",
        "observed_risk_state_version",
        "order_id",
        "order_origin_command_id",
        "order_requested_contracts",
        "order_requested_reduce_only",
        "order_requested_side",
        "outcome",
        "pnl_calculation_version",
        "pnl_completeness",
        "pnl_evidence_sha256",
        "pnl_input_set_sha256",
        "pnl_reference_asset_id",
        "pnl_reference_asset_symbol",
        "pnl_snapshot_id",
        "policy_sha256",
        "policy_version",
        "product_id",
        "reconciliation_completeness",
        "reconciliation_observed_at",
        "reconciliation_requirements_sha256",
        "reconciliation_requirements_version",
        "reconciliation_run_id",
        "reconciliation_run_version",
        "recovery_incarnation_id",
        "region",
        "reservation_plan",
        "risk_effect",
        "risk_state_id",
    }
)
CONTEXT_FIELDS = DECISION_PAYLOAD_FIELDS - {
    "check_results",
    "check_set_sha256",
    "check_set_version",
    "reservation_plan",
}

_HASH_CHARS = frozenset("0123456789abcdef")
_OUTCOMES = frozenset({"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"})
_UNITS = frozenset(
    {
        "NONE",
        "CONTRACTS",
        "REFERENCE_ASSET",
        "COLLATERAL_ASSET",
        "LEVERAGE",
        "ORDERS",
        "REQUESTS",
        "PRICE",
        "BASIS_POINTS",
        "SECONDS",
    }
)
_NUMERIC_SHAPES = {
    "MAX_POSITION": ("INTEGER", "CONTRACTS"),
    "TOTAL_EXPOSURE": ("DECIMAL", "REFERENCE_ASSET"),
    "MAX_LEVERAGE": ("DECIMAL", "LEVERAGE"),
    "MAX_LOSS_PER_TRADE": ("DECIMAL", "REFERENCE_ASSET"),
    "MAX_DAILY_LOSS": ("DECIMAL", "REFERENCE_ASSET"),
    "MAX_OPEN_ORDERS": ("INTEGER", "ORDERS"),
    "MAX_ORDER_RATE": ("INTEGER", "REQUESTS"),
    "PRICE_COLLAR": ("DECIMAL", "BASIS_POINTS"),
    "COLLATERAL_MARGIN": ("DECIMAL", "COLLATERAL_ASSET"),
    "FRESHNESS_RECONCILIATION": ("INTEGER", "SECONDS"),
    "PROTECTION": ("INTEGER", "CONTRACTS"),
    "REDUCE_ONLY_NO_REVERSAL": ("INTEGER", "CONTRACTS"),
}
_REDUCE_NA = frozenset(
    {
        "MAX_POSITION",
        "TOTAL_EXPOSURE",
        "MAX_LEVERAGE",
        "MAX_LOSS_PER_TRADE",
        "MAX_DAILY_LOSS",
        "PROTECTION",
    }
)


def _fail() -> NoReturn:
    raise PersistenceValueError()


def _hash(value: object) -> str:
    if type(value) is not str or len(value) != 64 or not set(value) <= _HASH_CHARS:
        _fail()
    return value


def _uuid(value: object, *, optional: bool = False) -> UUID | None:
    if value is None and optional:
        return None
    if type(value) is not UUID or value.int == 0:
        _fail()
    return value


def _integer(value: object, *, positive: bool = True, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value < 2**63:
        _fail()
    return value


def _signed_integer(value: object, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or not -(2**63) <= value < 2**63:
        _fail()
    return value


def _text(value: object, *, maximum: int = 128) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or not value.strip()
        or "\x00" in value
    ):
        _fail()
    return value


def _enum(value: object, allowed: set[str] | frozenset[str] | tuple[str, ...]) -> str:
    if type(value) is not str or value not in allowed:
        _fail()
    return value


def _time(value: object, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    return _utc(value)


def _decimal(value: object, *, optional: bool = False, nonnegative: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    encoded = _decimal_text(value, 38, 18)
    result = Decimal(encoded)
    if nonnegative and result < 0:
        _fail()
    return result


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat(timespec="microseconds")


class _OwnedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except ValidationError:
            raise PersistenceValueError() from None


class RiskCheckV1(_OwnedModel):
    """Caller-owned v1 check input; the digest is always derived."""

    check_kind: str
    outcome: str
    reason_code: str
    metric_kind: str
    observed_decimal: Decimal | None
    limit_decimal: Decimal | None
    observed_integer: int | None
    limit_integer: int | None
    unit: str
    evidence_observed_at: datetime | None
    input_set_sha256: str

    @field_validator("check_kind", mode="before")
    @classmethod
    def _check_kind(cls, value: object) -> str:
        return _enum(value, CHECK_KINDS)

    @field_validator("outcome", mode="before")
    @classmethod
    def _outcome(cls, value: object) -> str:
        return _enum(value, _OUTCOMES)

    @field_validator("metric_kind", mode="before")
    @classmethod
    def _metric(cls, value: object) -> str:
        return _enum(value, frozenset({"NONE", "DECIMAL", "INTEGER"}))

    @field_validator("unit", mode="before")
    @classmethod
    def _unit(cls, value: object) -> str:
        return _enum(value, _UNITS)

    @field_validator("observed_decimal", "limit_decimal", mode="before")
    @classmethod
    def _decimals(cls, value: object) -> Decimal | None:
        return _decimal(value, optional=True)

    @field_validator("observed_integer", "limit_integer", mode="before")
    @classmethod
    def _integers(cls, value: object) -> int | None:
        return _signed_integer(value, optional=True)

    @field_validator("evidence_observed_at", mode="before")
    @classmethod
    def _evidence_time(cls, value: object) -> datetime | None:
        return _time(value, optional=True)

    @field_validator("input_set_sha256", mode="before")
    @classmethod
    def _input_hash(cls, value: object) -> str:
        return _hash(value)


class ReservationPlanV1(_OwnedModel):
    """Caller-owned v1 reservation proposal; its digest is always derived."""

    approved_contracts: int
    approved_side: str
    reference_asset_id: int
    reference_asset_symbol: str
    collateral_asset_id: int
    collateral_asset_symbol: str
    reserved_incremental_gross_exposure: Decimal
    reserved_worst_case_loss: Decimal
    reserved_collateral: Decimal
    reserved_open_order_slots: int
    reserved_pending_rate_units: int
    rate_policy_version: int
    rate_policy_sha256: str
    rate_window_start_at: datetime
    rate_window_end_at: datetime

    @field_validator(
        "approved_contracts",
        "reference_asset_id",
        "collateral_asset_id",
        "reserved_pending_rate_units",
        "rate_policy_version",
        mode="before",
    )
    @classmethod
    def _positive(cls, value: object) -> int:
        result = _integer(value)
        assert result is not None
        return result

    @field_validator("reserved_open_order_slots", mode="before")
    @classmethod
    def _nonnegative(cls, value: object) -> int:
        result = _integer(value, positive=False)
        assert result is not None
        return result

    @field_validator("approved_side", mode="before")
    @classmethod
    def _side(cls, value: object) -> str:
        return _enum(value, frozenset({"buy", "sell"}))

    @field_validator("reference_asset_symbol", "collateral_asset_symbol", mode="before")
    @classmethod
    def _symbol(cls, value: object) -> str:
        return _text(value)

    @field_validator(
        "reserved_incremental_gross_exposure",
        "reserved_worst_case_loss",
        "reserved_collateral",
        mode="before",
    )
    @classmethod
    def _amount(cls, value: object) -> Decimal:
        result = _decimal(value, nonnegative=True)
        assert result is not None
        return result

    @field_validator("rate_policy_sha256", mode="before")
    @classmethod
    def _policy_hash(cls, value: object) -> str:
        return _hash(value)

    @field_validator("rate_window_start_at", "rate_window_end_at", mode="before")
    @classmethod
    def _window_time(cls, value: object) -> datetime:
        result = _time(value)
        assert result is not None
        return result


@dataclass(frozen=True)
class RiskDecisionEncoding:
    """Canonical stored payload and all independently persisted digests."""

    payload: str
    evidence_sha256: str
    check_set_sha256: str
    reservation_plan_sha256: str | None
