"""Immutable caller snapshots consumed by the pure risk evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from hmt_crypto.risk.math import amount_from_scaled, scaled_amount
from hmt_crypto.risk.models import (
    Completeness,
    OperationKind,
    RiskEffect,
    RiskInputError,
    Side,
    _exact_completeness,
    _exact_enum,
    _exact_side,
)

_HASH_CHARS = frozenset("0123456789abcdef")


def _utc(value: object, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if type(value) is not datetime or value.tzinfo is not UTC:
        raise RiskInputError() from None
    return value


def _text(value: object, *, optional: bool = False, maximum: int = 256) -> str | None:
    if value is None and optional:
        return None
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or not value.strip()
        or "\x00" in value
    ):
        raise RiskInputError() from None
    return value


def _hash(value: object) -> str:
    if type(value) is not str or len(value) != 64 or not set(value) <= _HASH_CHARS:
        raise RiskInputError() from None
    return value


def _hashes(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise RiskInputError() from None
    result = tuple(_hash(item) for item in value)
    if len(set(result)) != len(result):
        raise RiskInputError() from None
    return result


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value < 2**63:
        raise RiskInputError() from None
    return value


def _amount(value: object, *, positive: bool = False) -> Decimal:
    scaled = scaled_amount(value)
    if scaled < (1 if positive else 0):
        raise RiskInputError() from None
    return amount_from_scaled(scaled)


def _signed_amount(value: object) -> Decimal:
    return amount_from_scaled(scaled_amount(value))


@dataclass(frozen=True, slots=True)
class AssetRef:
    """Exact policy asset identity."""

    asset_id: int
    symbol: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _integer(self.asset_id, minimum=1))
        object.__setattr__(self, "symbol", _text(self.symbol, maximum=32))


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Versioned immutable limits; it contains no configuration authority."""

    version: int
    policy_sha256: str
    reference_asset: AssetRef
    collateral_asset: AssetRef
    max_position_contracts: int
    max_total_exposure: Decimal
    max_leverage: Decimal
    max_loss_per_trade: Decimal
    max_daily_loss: Decimal
    max_open_orders: int
    max_order_rate: int
    rate_window_seconds: int
    rate_policy_version: int
    rate_policy_sha256: str
    price_collar_bps: Decimal
    max_catalogue_age_seconds: int
    max_mark_age_seconds: int
    allow_last_price_fallback: bool
    max_last_price_age_seconds: int
    max_reconciliation_age_seconds: int
    max_accounting_age_seconds: int
    max_halt_age_seconds: int
    daily_reset_second_utc: int
    collateral_buffer_bps: Decimal

    def __post_init__(self) -> None:
        for name in (
            "version",
            "max_position_contracts",
            "max_open_orders",
            "max_order_rate",
            "rate_window_seconds",
            "rate_policy_version",
        ):
            object.__setattr__(self, name, _integer(getattr(self, name), minimum=1))
        for name in (
            "max_catalogue_age_seconds",
            "max_mark_age_seconds",
            "max_last_price_age_seconds",
            "max_reconciliation_age_seconds",
            "max_accounting_age_seconds",
            "max_halt_age_seconds",
        ):
            object.__setattr__(self, name, _integer(getattr(self, name)))
        if (
            type(self.reference_asset) is not AssetRef
            or type(self.collateral_asset) is not AssetRef
            or type(self.allow_last_price_fallback) is not bool
            or type(self.daily_reset_second_utc) is not int
            or not 0 <= self.daily_reset_second_utc < 86_400
        ):
            raise RiskInputError() from None
        for name in (
            "max_total_exposure",
            "max_leverage",
            "max_loss_per_trade",
            "max_daily_loss",
        ):
            object.__setattr__(self, name, _amount(getattr(self, name), positive=True))
        object.__setattr__(self, "price_collar_bps", _amount(self.price_collar_bps))
        object.__setattr__(self, "collateral_buffer_bps", _amount(self.collateral_buffer_bps))
        object.__setattr__(self, "policy_sha256", _hash(self.policy_sha256))
        object.__setattr__(self, "rate_policy_sha256", _hash(self.rate_policy_sha256))


@dataclass(frozen=True, slots=True)
class RiskIntent:
    """Raw order intent from which every candidate risk amount is derived."""

    product_id: int
    operation: OperationKind
    target_order_key: str | None
    side: Side
    contracts: int
    risk_effect: RiskEffect
    order_price: Decimal
    leverage: Decimal
    stop_price: Decimal | None
    loss_fee_reserve: Decimal
    planned_protection_contracts: int
    intent_owner: str
    intent_key: str

    def __post_init__(self) -> None:
        operation = _exact_enum(self.operation, tuple(OperationKind))
        effect = _exact_enum(self.risk_effect, tuple(RiskEffect))
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "risk_effect", effect)
        object.__setattr__(self, "side", _exact_side(self.side))
        object.__setattr__(self, "product_id", _integer(self.product_id, minimum=1))
        object.__setattr__(self, "contracts", _integer(self.contracts, minimum=1))
        object.__setattr__(
            self,
            "planned_protection_contracts",
            _integer(self.planned_protection_contracts),
        )
        object.__setattr__(self, "order_price", _amount(self.order_price, positive=True))
        object.__setattr__(self, "leverage", _amount(self.leverage, positive=True))
        object.__setattr__(self, "loss_fee_reserve", _amount(self.loss_fee_reserve))
        object.__setattr__(self, "intent_owner", _text(self.intent_owner, maximum=128))
        object.__setattr__(self, "intent_key", _text(self.intent_key))
        if self.stop_price is not None:
            object.__setattr__(self, "stop_price", _amount(self.stop_price, positive=True))
        if operation is OperationKind.PLACE_ORDER:
            if self.target_order_key is not None:
                raise RiskInputError() from None
        else:
            object.__setattr__(self, "target_order_key", _text(self.target_order_key, maximum=128))
        if effect is RiskEffect.REDUCE_ONLY and (
            self.stop_price is not None or self.planned_protection_contracts != 0
        ):
            raise RiskInputError() from None


@dataclass(frozen=True, slots=True)
class DailyAccounting:
    """One reset-baseline and ledger-cursor proven daily accounting snapshot."""

    completeness: Completeness
    observed_at: datetime | None
    window_start_at: datetime | None
    baseline_observed_at: datetime | None
    baseline_equity: Decimal | None
    current_equity: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    unresolved_loss_reserve: Decimal | None
    ledger_cursor_start: str | None
    ledger_cursor_end: str | None
    ledger_evidence_sha256: str | None
    included_reservation_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        state = _exact_completeness(self.completeness)
        object.__setattr__(self, "completeness", state)
        object.__setattr__(self, "observed_at", _utc(self.observed_at, optional=True))
        object.__setattr__(self, "window_start_at", _utc(self.window_start_at, optional=True))
        object.__setattr__(
            self, "baseline_observed_at", _utc(self.baseline_observed_at, optional=True)
        )
        object.__setattr__(
            self, "included_reservation_keys", _hashes(self.included_reservation_keys)
        )
        for name in (
            "baseline_equity",
            "current_equity",
            "unresolved_loss_reserve",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _amount(value))
        for name in ("realized_pnl", "unrealized_pnl"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _signed_amount(value))
        if self.baseline_equity is not None and scaled_amount(self.baseline_equity) <= 0:
            raise RiskInputError() from None
        if self.current_equity is not None and scaled_amount(self.current_equity) < 0:
            raise RiskInputError() from None
        if (
            self.unresolved_loss_reserve is not None
            and scaled_amount(self.unresolved_loss_reserve) < 0
        ):
            raise RiskInputError() from None
        for name in ("ledger_cursor_start", "ledger_cursor_end"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value))
        if self.ledger_evidence_sha256 is not None:
            object.__setattr__(
                self,
                "ledger_evidence_sha256",
                _hash(self.ledger_evidence_sha256),
            )
        if state is not Completeness.COMPLETE:
            return
        times = (self.observed_at, self.window_start_at, self.baseline_observed_at)
        amounts = (
            self.baseline_equity,
            self.current_equity,
            self.realized_pnl,
            self.unrealized_pnl,
            self.unresolved_loss_reserve,
        )
        proof = (
            self.ledger_cursor_start,
            self.ledger_cursor_end,
            self.ledger_evidence_sha256,
        )
        if any(value is None for value in times + amounts + proof):
            raise RiskInputError() from None
        assert self.baseline_equity is not None
        assert self.current_equity is not None
        assert self.unresolved_loss_reserve is not None
        assert self.observed_at is not None
        assert self.window_start_at is not None
        if (
            self.baseline_observed_at != self.window_start_at
            or self.observed_at < self.window_start_at
        ):
            raise RiskInputError() from None


from hmt_crypto.risk.snapshots import (  # noqa: E402
    HaltSnapshot,
    OpenOrderRisk,
    PositionRisk,
    PriceSnapshot,
    ProductTruth,
    ProtectionOrder,
    RateWindowSnapshot,
    ReconciliationSnapshot,
    ReservationRisk,
    RiskSnapshot,
)

__all__ = [
    "AssetRef",
    "DailyAccounting",
    "HaltSnapshot",
    "OpenOrderRisk",
    "PositionRisk",
    "PriceSnapshot",
    "ProductTruth",
    "ProtectionOrder",
    "RateWindowSnapshot",
    "ReconciliationSnapshot",
    "ReservationRisk",
    "RiskIntent",
    "RiskPolicy",
    "RiskSnapshot",
]
