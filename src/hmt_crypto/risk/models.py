"""Closed immutable contracts for pure risk calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class RiskInputError(Exception):
    """An input cannot support a safe deterministic risk decision."""

    code = "risk_input_invalid"
    message = "Risk evaluation input is invalid"

    def __init__(self) -> None:
        super().__init__(f"{self.code}: {self.message}")

    def __repr__(self) -> str:
        return "RiskInputError(code='risk_input_invalid')"


class Side(StrEnum):
    """Signed order direction."""

    BUY = "buy"
    SELL = "sell"


class Completeness(StrEnum):
    """Truth quality carried by a read-only input snapshot."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


class OperationKind(StrEnum):
    """Order mutation being evaluated."""

    PLACE_ORDER = "PLACE_ORDER"
    MODIFY_ORDER = "MODIFY_ORDER"


class RiskEffect(StrEnum):
    """Whether an intent can increase risk or is a strict reduction."""

    RISK_INCREASE = "RISK_INCREASE"
    REDUCE_ONLY = "REDUCE_ONLY"


class CatalogueStatus(StrEnum):
    """Raw product-catalogue truth."""

    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    UNSUPPORTED = "UNSUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"


class MarketStatus(StrEnum):
    """Raw venue product state."""

    TRADABLE = "TRADABLE"
    CLOSED = "CLOSED"
    AMBIGUOUS = "AMBIGUOUS"


class HaltCause(StrEnum):
    """Observed or proposed durable halt cause."""

    KILL_SWITCH = "KILL_SWITCH"
    OPERATOR = "OPERATOR"
    DAILY_LOSS = "DAILY_LOSS"
    ACCOUNTING_UNKNOWN = "ACCOUNTING_UNKNOWN"


class HaltSource(StrEnum):
    """Authoritative source of one durable halt-state read."""

    DURABLE_HALT_LEDGER = "DURABLE_HALT_LEDGER"


class CheckKind(StrEnum):
    """Canonical persistence-compatible mandatory check order."""

    PRODUCT_CATALOGUE = "PRODUCT_CATALOGUE"
    MARKET_TRADABILITY = "MARKET_TRADABILITY"
    MAX_POSITION = "MAX_POSITION"
    TOTAL_EXPOSURE = "TOTAL_EXPOSURE"
    MAX_LEVERAGE = "MAX_LEVERAGE"
    MAX_LOSS_PER_TRADE = "MAX_LOSS_PER_TRADE"
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS"
    MAX_OPEN_ORDERS = "MAX_OPEN_ORDERS"
    MAX_ORDER_RATE = "MAX_ORDER_RATE"
    PRICE_COLLAR = "PRICE_COLLAR"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    COLLATERAL_MARGIN = "COLLATERAL_MARGIN"
    KILL_HALT = "KILL_HALT"
    FRESHNESS_RECONCILIATION = "FRESHNESS_RECONCILIATION"
    PROTECTION = "PROTECTION"
    REDUCE_ONLY_NO_REVERSAL = "REDUCE_ONLY_NO_REVERSAL"


class CheckOutcome(StrEnum):
    """One computed check result."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DecisionOutcome(StrEnum):
    """Overall fail-closed result."""

    APPROVED = "APPROVED"
    DENIED = "DENIED"


class MetricKind(StrEnum):
    """Persistence-compatible metric representation."""

    NONE = "NONE"
    DECIMAL = "DECIMAL"
    INTEGER = "INTEGER"


class Unit(StrEnum):
    """Persistence-compatible risk metric unit."""

    NONE = "NONE"
    CONTRACTS = "CONTRACTS"
    REFERENCE_ASSET = "REFERENCE_ASSET"
    COLLATERAL_ASSET = "COLLATERAL_ASSET"
    LEVERAGE = "LEVERAGE"
    ORDERS = "ORDERS"
    REQUESTS = "REQUESTS"
    BASIS_POINTS = "BASIS_POINTS"
    SECONDS = "SECONDS"


class PriceSource(StrEnum):
    """Price selected by the manager for collar evaluation."""

    MARK = "MARK"
    LAST = "LAST"
    NONE = "NONE"


class ProtectionOrderType(StrEnum):
    """Normalized order type carried by reconciled protection evidence."""

    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    LIMIT = "LIMIT"


class ProtectionOrderStatus(StrEnum):
    """Normalized liveness carried by reconciled protection evidence."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class RateWindowSource(StrEnum):
    """Authoritative source of an order-rate window aggregate."""

    CONSUMPTION_LEDGER = "CONSUMPTION_LEDGER"


_SIDES = (Side.BUY, Side.SELL)
_COMPLETENESS = (
    Completeness.COMPLETE,
    Completeness.PARTIAL,
    Completeness.MISSING,
    Completeness.AMBIGUOUS,
)


def _exact_side(value: object) -> Side:
    for member in _SIDES:
        if value is member:
            return member
    raise RiskInputError() from None


def _exact_completeness(value: object) -> Completeness:
    for member in _COMPLETENESS:
        if value is member:
            return member
    raise RiskInputError() from None


def _exact_enum(value: object, members: tuple[StrEnum, ...]) -> StrEnum:
    for member in members:
        if value is member:
            return member
    raise RiskInputError() from None


@dataclass(frozen=True, slots=True)
class ExposureLeg:
    """One exact contract leg normalized into the policy reference asset."""

    side: Side
    contracts: int
    per_contract_notional: Decimal
    reduce_only: bool = False

    def __post_init__(self) -> None:
        from hmt_crypto.risk.math import scaled_amount

        side = _exact_side(self.side)
        if (
            type(self.contracts) is not int
            or not 1 <= self.contracts < 2**63
            or type(self.reduce_only) is not bool
            or scaled_amount(self.per_contract_notional) <= 0
        ):
            raise RiskInputError() from None
        object.__setattr__(self, "side", side)


@dataclass(frozen=True, slots=True)
class RiskCheck:
    """Manager-owned immutable check evidence."""

    kind: CheckKind
    outcome: CheckOutcome
    reason_code: str
    metric_kind: MetricKind
    observed_decimal: Decimal | None
    limit_decimal: Decimal | None
    observed_integer: int | None
    limit_integer: int | None
    unit: Unit
    evidence_observed_at: datetime | None
    input_set_sha256: str
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ReservationProposal:
    """Non-authorizing capacity proposal for a later atomic repository step."""

    product_id: int
    risk_effect: RiskEffect
    intent_key_sha256: str
    input_context_sha256: str
    policy_version: int
    policy_sha256: str
    configured_leverage: Decimal | None
    approved_contracts: int
    approved_side: Side
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
    reservation_plan_sha256: str


@dataclass(frozen=True, slots=True)
class HaltDirective:
    """Evidence-only request; it never mutates or authorizes durable state."""

    cause: HaltCause
    observed_at: datetime
    evidence_sha256: str
    authorizes_side_effect: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class RiskEvaluation:
    """Complete pure result; approval still requires an atomic reservation."""

    outcome: DecisionOutcome
    risk_effect: RiskEffect
    checks: tuple[RiskCheck, ...]
    reservation: ReservationProposal | None
    halt_directive: HaltDirective | None
    price_source: PriceSource
    intent_key_sha256: str
    check_set_sha256: str
    decision_sha256: str


__all__ = [
    "CatalogueStatus",
    "CheckKind",
    "CheckOutcome",
    "Completeness",
    "DecisionOutcome",
    "ExposureLeg",
    "HaltCause",
    "HaltDirective",
    "HaltSource",
    "MarketStatus",
    "MetricKind",
    "OperationKind",
    "PriceSource",
    "ProtectionOrderStatus",
    "ProtectionOrderType",
    "RateWindowSource",
    "ReservationProposal",
    "RiskCheck",
    "RiskEffect",
    "RiskEvaluation",
    "RiskInputError",
    "Side",
    "Unit",
]
