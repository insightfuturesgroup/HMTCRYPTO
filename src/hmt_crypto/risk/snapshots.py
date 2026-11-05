"""Exact immutable exchange/local truth snapshots for risk evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from hmt_crypto.risk.inputs import (
    DailyAccounting,
    _amount,
    _hash,
    _integer,
    _text,
    _utc,
)
from hmt_crypto.risk.models import (
    CatalogueStatus,
    Completeness,
    HaltCause,
    HaltSource,
    MarketStatus,
    ProtectionOrderStatus,
    ProtectionOrderType,
    RateWindowSource,
    RiskEffect,
    RiskInputError,
    Side,
    _exact_completeness,
    _exact_enum,
    _exact_side,
)


def _signed(value: object, *, allow_zero: bool = True) -> int:
    if type(value) is not int or not -(2**63) < value < 2**63 or (not allow_zero and value == 0):
        raise RiskInputError() from None
    return value


@dataclass(frozen=True, slots=True)
class ProductTruth:
    """Candidate product catalogue and venue-state observation."""

    product_id: int
    catalogue_status: CatalogueStatus
    market_status: MarketStatus
    contract_multiplier: Decimal
    max_leverage: Decimal
    catalogue_observed_at: datetime
    market_observed_at: datetime
    evidence_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_id", _integer(self.product_id, minimum=1))
        object.__setattr__(
            self, "catalogue_status", _exact_enum(self.catalogue_status, tuple(CatalogueStatus))
        )
        object.__setattr__(
            self, "market_status", _exact_enum(self.market_status, tuple(MarketStatus))
        )
        object.__setattr__(
            self, "contract_multiplier", _amount(self.contract_multiplier, positive=True)
        )
        object.__setattr__(self, "max_leverage", _amount(self.max_leverage, positive=True))
        object.__setattr__(self, "catalogue_observed_at", _utc(self.catalogue_observed_at))
        object.__setattr__(self, "market_observed_at", _utc(self.market_observed_at))
        object.__setattr__(self, "evidence_sha256", _hash(self.evidence_sha256))


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    """Raw mark and last observations; selection belongs to RiskManager."""

    product_id: int
    completeness: Completeness
    mark_price: Decimal | None
    mark_observed_at: datetime | None
    last_price: Decimal | None
    last_observed_at: datetime | None
    evidence_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_id", _integer(self.product_id, minimum=1))
        object.__setattr__(self, "completeness", _exact_completeness(self.completeness))
        for price_name, time_name in (
            ("mark_price", "mark_observed_at"),
            ("last_price", "last_observed_at"),
        ):
            price = getattr(self, price_name)
            observed = getattr(self, time_name)
            if (price is None) != (observed is None):
                raise RiskInputError() from None
            if price is not None:
                object.__setattr__(self, price_name, _amount(price, positive=True))
                object.__setattr__(self, time_name, _utc(observed))
        object.__setattr__(self, "evidence_sha256", _hash(self.evidence_sha256))


@dataclass(frozen=True, slots=True)
class PositionRisk:
    """One reconciled position valued in the policy reference asset."""

    product_id: int
    signed_contracts: int
    notional_per_contract: Decimal
    effective_leverage: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_id", _integer(self.product_id, minimum=1))
        object.__setattr__(
            self, "signed_contracts", _signed(self.signed_contracts, allow_zero=False)
        )
        object.__setattr__(
            self, "notional_per_contract", _amount(self.notional_per_contract, positive=True)
        )
        object.__setattr__(
            self, "effective_leverage", _amount(self.effective_leverage, positive=True)
        )


@dataclass(frozen=True, slots=True)
class OpenOrderRisk:
    """Unfilled order remainder normalized for risk calculations."""

    order_key: str
    intent_key_sha256: str
    product_id: int
    side: Side
    remaining_contracts: int
    notional_per_contract: Decimal
    reduce_only: bool
    configured_leverage: Decimal | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_key", _text(self.order_key, maximum=128))
        object.__setattr__(self, "intent_key_sha256", _hash(self.intent_key_sha256))
        object.__setattr__(self, "product_id", _integer(self.product_id, minimum=1))
        object.__setattr__(self, "side", _exact_side(self.side))
        object.__setattr__(
            self, "remaining_contracts", _integer(self.remaining_contracts, minimum=1)
        )
        object.__setattr__(
            self, "notional_per_contract", _amount(self.notional_per_contract, positive=True)
        )
        if type(self.reduce_only) is not bool:
            raise RiskInputError() from None
        if self.reduce_only:
            if self.configured_leverage is not None:
                raise RiskInputError() from None
        elif self.configured_leverage is None:
            raise RiskInputError() from None
        else:
            object.__setattr__(
                self,
                "configured_leverage",
                _amount(self.configured_leverage, positive=True),
            )


@dataclass(frozen=True, slots=True)
class ReservationRisk:
    """Uncommitted atomic capacity already promised to another intent."""

    reservation_key: str
    intent_key_sha256: str
    product_id: int
    side: Side
    risk_effect: RiskEffect
    contracts: int
    notional_per_contract: Decimal
    configured_leverage: Decimal | None
    worst_case_loss: Decimal
    collateral: Decimal
    open_order_slots: int
    pending_rate_units: int
    rate_policy_version: int
    rate_policy_sha256: str
    rate_window_start_at: datetime
    rate_window_end_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "reservation_key", _hash(self.reservation_key))
        object.__setattr__(self, "intent_key_sha256", _hash(self.intent_key_sha256))
        object.__setattr__(self, "product_id", _integer(self.product_id, minimum=1))
        object.__setattr__(self, "side", _exact_side(self.side))
        object.__setattr__(self, "risk_effect", _exact_enum(self.risk_effect, tuple(RiskEffect)))
        object.__setattr__(self, "contracts", _integer(self.contracts, minimum=1))
        object.__setattr__(
            self, "notional_per_contract", _amount(self.notional_per_contract, positive=True)
        )
        if self.risk_effect is RiskEffect.RISK_INCREASE:
            if self.configured_leverage is None:
                raise RiskInputError() from None
            object.__setattr__(
                self,
                "configured_leverage",
                _amount(self.configured_leverage, positive=True),
            )
        elif self.configured_leverage is not None:
            raise RiskInputError() from None
        object.__setattr__(self, "worst_case_loss", _amount(self.worst_case_loss))
        object.__setattr__(self, "collateral", _amount(self.collateral))
        if self.risk_effect is RiskEffect.RISK_INCREASE:
            if self.worst_case_loss == 0:
                raise RiskInputError() from None
        elif self.worst_case_loss != 0 or self.collateral != 0:
            raise RiskInputError() from None
        object.__setattr__(self, "open_order_slots", _integer(self.open_order_slots))
        object.__setattr__(self, "pending_rate_units", _integer(self.pending_rate_units, minimum=1))
        object.__setattr__(
            self, "rate_policy_version", _integer(self.rate_policy_version, minimum=1)
        )
        object.__setattr__(self, "rate_policy_sha256", _hash(self.rate_policy_sha256))
        start = _utc(self.rate_window_start_at)
        end = _utc(self.rate_window_end_at)
        if start is None or end is None or start >= end:
            raise RiskInputError() from None
        object.__setattr__(self, "rate_window_start_at", start)
        object.__setattr__(self, "rate_window_end_at", end)


@dataclass(frozen=True, slots=True)
class RateWindowSnapshot:
    """Cursor-proven aggregate for one authoritative order-rate window."""

    completeness: Completeness
    source: RateWindowSource | None = None
    cursor: str | None = None
    observed_at: datetime | None = None
    window_start_at: datetime | None = None
    window_end_at: datetime | None = None
    consumed_units: int | None = None
    rate_policy_version: int | None = None
    rate_policy_sha256: str | None = None
    evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        state = _exact_completeness(self.completeness)
        object.__setattr__(self, "completeness", state)
        if self.source is not None:
            object.__setattr__(self, "source", _exact_enum(self.source, tuple(RateWindowSource)))
        if self.cursor is not None:
            object.__setattr__(self, "cursor", _text(self.cursor, maximum=256))
        for name in ("observed_at", "window_start_at", "window_end_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _utc(value))
        if self.consumed_units is not None:
            object.__setattr__(self, "consumed_units", _integer(self.consumed_units))
        if self.rate_policy_version is not None:
            object.__setattr__(
                self,
                "rate_policy_version",
                _integer(self.rate_policy_version, minimum=1),
            )
        for name in ("rate_policy_sha256", "evidence_sha256"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _hash(value))
        if state is not Completeness.COMPLETE:
            return
        required = (
            self.source,
            self.cursor,
            self.observed_at,
            self.window_start_at,
            self.window_end_at,
            self.consumed_units,
            self.rate_policy_version,
            self.rate_policy_sha256,
            self.evidence_sha256,
        )
        if any(value is None for value in required):
            raise RiskInputError() from None
        assert self.observed_at is not None
        assert self.window_start_at is not None
        assert self.window_end_at is not None
        if not self.window_start_at <= self.observed_at < self.window_end_at:
            raise RiskInputError() from None


@dataclass(frozen=True, slots=True)
class ProtectionOrder:
    """One reconciled candidate for verified protective coverage."""

    order_key: str
    product_id: int
    side: Side
    remaining_contracts: int
    trigger_price: Decimal
    reference_price: Decimal
    reduce_only: bool
    order_type: ProtectionOrderType
    status: ProtectionOrderStatus
    observed_at: datetime
    reconciliation_evidence_sha256: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_key", _text(self.order_key, maximum=128))
        object.__setattr__(self, "product_id", _integer(self.product_id, minimum=1))
        object.__setattr__(self, "side", _exact_side(self.side))
        object.__setattr__(
            self, "remaining_contracts", _integer(self.remaining_contracts, minimum=1)
        )
        object.__setattr__(self, "trigger_price", _amount(self.trigger_price, positive=True))
        object.__setattr__(self, "reference_price", _amount(self.reference_price, positive=True))
        if type(self.reduce_only) is not bool:
            raise RiskInputError() from None
        object.__setattr__(
            self, "order_type", _exact_enum(self.order_type, tuple(ProtectionOrderType))
        )
        object.__setattr__(self, "status", _exact_enum(self.status, tuple(ProtectionOrderStatus)))
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        object.__setattr__(
            self,
            "reconciliation_evidence_sha256",
            _hash(self.reconciliation_evidence_sha256),
        )
        object.__setattr__(self, "evidence_sha256", _hash(self.evidence_sha256))


@dataclass(frozen=True, slots=True)
class ReconciliationSnapshot:
    """Truth-readiness proof for positions, orders and balances."""

    completeness: Completeness
    observed_at: datetime | None
    evidence_sha256: str | None

    def __post_init__(self) -> None:
        state = _exact_completeness(self.completeness)
        object.__setattr__(self, "completeness", state)
        object.__setattr__(self, "observed_at", _utc(self.observed_at, optional=True))
        if self.evidence_sha256 is not None:
            object.__setattr__(self, "evidence_sha256", _hash(self.evidence_sha256))
        if state is Completeness.COMPLETE and (
            self.observed_at is None or self.evidence_sha256 is None
        ):
            raise RiskInputError() from None


@dataclass(frozen=True, slots=True)
class HaltSnapshot:
    """Observed durable halt set, not a pass/fail assertion."""

    completeness: Completeness
    source: HaltSource | None
    observed_at: datetime | None
    read_at: datetime | None
    revision: int | None
    evidence_sha256: str | None
    active_causes: tuple[HaltCause, ...]

    def __post_init__(self) -> None:
        state = _exact_completeness(self.completeness)
        object.__setattr__(self, "completeness", state)
        if self.source is not None:
            object.__setattr__(self, "source", _exact_enum(self.source, tuple(HaltSource)))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, optional=True))
        object.__setattr__(self, "read_at", _utc(self.read_at, optional=True))
        if self.revision is not None:
            object.__setattr__(self, "revision", _integer(self.revision))
        if self.evidence_sha256 is not None:
            object.__setattr__(self, "evidence_sha256", _hash(self.evidence_sha256))
        if type(self.active_causes) is not tuple:
            raise RiskInputError() from None
        causes = tuple(_exact_enum(item, tuple(HaltCause)) for item in self.active_causes)
        if len(set(causes)) != len(causes):
            raise RiskInputError() from None
        object.__setattr__(self, "active_causes", causes)
        if (
            self.observed_at is not None
            and self.read_at is not None
            and self.observed_at > self.read_at
        ):
            raise RiskInputError() from None
        if state is Completeness.COMPLETE:
            required = (
                self.source,
                self.observed_at,
                self.read_at,
                self.revision,
                self.evidence_sha256,
            )
            if any(value is None for value in required):
                raise RiskInputError() from None


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """All immutable facts read at one caller-selected evaluation instant."""

    evaluated_at: datetime
    product: ProductTruth
    price: PriceSnapshot
    positions: tuple[PositionRisk, ...]
    open_orders: tuple[OpenOrderRisk, ...]
    reservations: tuple[ReservationRisk, ...]
    rate_window: RateWindowSnapshot
    protective_orders: tuple[ProtectionOrder, ...]
    reconciliation: ReconciliationSnapshot
    accounting: DailyAccounting
    halt: HaltSnapshot
    collateral_available: Decimal
    collateral_observed_at: datetime
    collateral_evidence_sha256: str

    def __post_init__(self) -> None:
        evaluated = _utc(self.evaluated_at)
        assert evaluated is not None
        object.__setattr__(self, "evaluated_at", evaluated)
        scalar_types = (
            (self.product, ProductTruth),
            (self.price, PriceSnapshot),
            (self.reconciliation, ReconciliationSnapshot),
            (self.accounting, DailyAccounting),
            (self.halt, HaltSnapshot),
            (self.rate_window, RateWindowSnapshot),
        )
        if any(type(value) is not expected for value, expected in scalar_types):
            raise RiskInputError() from None
        groups = (
            ("positions", self.positions, PositionRisk),
            ("open_orders", self.open_orders, OpenOrderRisk),
            ("reservations", self.reservations, ReservationRisk),
            ("protective_orders", self.protective_orders, ProtectionOrder),
        )
        for name, values, expected in groups:
            if type(values) is not tuple or any(type(item) is not expected for item in values):
                raise RiskInputError() from None
            object.__setattr__(self, name, values)
        if self.product.product_id != self.price.product_id:
            raise RiskInputError() from None
        if len({item.product_id for item in self.positions}) != len(self.positions):
            raise RiskInputError() from None
        if len({item.order_key for item in self.open_orders}) != len(self.open_orders):
            raise RiskInputError() from None
        if len({item.reservation_key for item in self.reservations}) != len(self.reservations):
            raise RiskInputError() from None
        if len({item.order_key for item in self.protective_orders}) != len(self.protective_orders):
            raise RiskInputError() from None
        if any(
            item.reconciliation_evidence_sha256 != self.reconciliation.evidence_sha256
            or item.observed_at != self.reconciliation.observed_at
            for item in self.protective_orders
        ):
            raise RiskInputError() from None
        if any(item.rate_window_start_at > evaluated for item in self.reservations):
            raise RiskInputError() from None
        object.__setattr__(self, "collateral_available", _amount(self.collateral_available))
        collateral_at = _utc(self.collateral_observed_at)
        assert collateral_at is not None
        object.__setattr__(self, "collateral_observed_at", collateral_at)
        object.__setattr__(
            self, "collateral_evidence_sha256", _hash(self.collateral_evidence_sha256)
        )
        observed = [
            self.product.catalogue_observed_at,
            self.product.market_observed_at,
            self.price.mark_observed_at,
            self.price.last_observed_at,
            self.reconciliation.observed_at,
            self.accounting.observed_at,
            self.halt.observed_at,
            self.halt.read_at,
            self.collateral_observed_at,
            self.rate_window.observed_at,
            *(item.observed_at for item in self.protective_orders),
        ]
        if any(item is not None and item > evaluated for item in observed):
            raise RiskInputError() from None


__all__ = [
    "HaltSnapshot",
    "OpenOrderRisk",
    "PositionRisk",
    "PriceSnapshot",
    "ProductTruth",
    "ProtectionOrder",
    "RateWindowSnapshot",
    "ReconciliationSnapshot",
    "ReservationRisk",
    "RiskSnapshot",
]
