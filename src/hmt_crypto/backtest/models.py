"""Immutable assumptions and event inputs for deterministic replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from hmt_crypto.backtest.errors import BacktestInputError
from hmt_crypto.backtest.values import (
    exact_decimal,
    exact_int,
    exact_sha256,
    exact_text,
    exact_utc,
    exact_uuid,
)
from hmt_crypto.oms.paper_models import (
    PaperBracketSpec,
    PaperCandle,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperTopOfBook,
)


class LiquidityRole(StrEnum):
    """Explicit commission role assigned to a simulated fill."""

    MAKER = "MAKER"
    TAKER = "TAKER"


class BacktestRunStatus(StrEnum):
    """Terminal quality/safety state of a deterministic run."""

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    HALTED_LIQUIDATION_GUARD = "HALTED_LIQUIDATION_GUARD"


_ROLES = tuple(LiquidityRole)
_HARD_MAX_FUNDING_REALIZATIONS = 1_000_000
_HARD_MAX_DECISIONS_PER_EVENT = 10_000


def _role(value: object, *, optional: bool = False) -> LiquidityRole | None:
    if value is None and optional:
        return None
    if not any(value is role for role in _ROLES):
        raise BacktestInputError("liquidity role must be a canonical enum member") from None
    assert isinstance(value, LiquidityRole)
    return value


def _copy_bracket(value: PaperBracketSpec | None) -> PaperBracketSpec | None:
    if value is None:
        return None
    if type(value) is not PaperBracketSpec:
        raise BacktestInputError("bracket assumption must be exact") from None
    return PaperBracketSpec(
        stop_loss_price=value.stop_loss_price,
        stop_loss_limit_price=value.stop_loss_limit_price,
        stop_loss_trail_amount=value.stop_loss_trail_amount,
        take_profit_price=value.take_profit_price,
        take_profit_limit_price=value.take_profit_limit_price,
    )


def _copy_intent(value: object) -> PaperOrderIntent:
    if type(value) is not PaperOrderIntent:
        raise BacktestInputError("order intent must be an exact PAPER contract") from None
    return PaperOrderIntent(
        order_id=value.order_id,
        paper_account_id=value.paper_account_id,
        mode=value.mode,
        product_id=value.product_id,
        side=value.side,
        contracts=value.contracts,
        kind=value.kind,
        tick_size=value.tick_size,
        limit_price=value.limit_price,
        stop_price=value.stop_price,
        trail_amount=value.trail_amount,
        trigger_direction=value.trigger_direction,
        reduce_only=value.reduce_only,
        bracket=_copy_bracket(value.bracket),
    )


def _copy_model_config(value: object) -> PaperModelConfig:
    if type(value) is not PaperModelConfig:
        raise BacktestInputError("PAPER model config must be exact") from None
    return PaperModelConfig(
        latency_events=value.latency_events,
        slippage_bps=value.slippage_bps,
        max_fill_contracts_per_event=value.max_fill_contracts_per_event,
        fee_model=value.fee_model,
    )


def _copy_market(value: object) -> PaperTopOfBook | PaperCandle:
    if type(value) is PaperTopOfBook:
        return PaperTopOfBook(
            stream_id=value.stream_id,
            product_id=value.product_id,
            event_index=value.event_index,
            observed_at=value.observed_at,
            bid_price=value.bid_price,
            ask_price=value.ask_price,
            bid_contracts=value.bid_contracts,
            ask_contracts=value.ask_contracts,
            trigger_reference=value.trigger_reference,
        )
    if type(value) is PaperCandle:
        return PaperCandle(
            stream_id=value.stream_id,
            product_id=value.product_id,
            event_index=value.event_index,
            observed_at=value.observed_at,
            open_price=value.open_price,
            high_price=value.high_price,
            low_price=value.low_price,
            close_price=value.close_price,
            volume_contracts=value.volume_contracts,
        )
    raise BacktestInputError("market event must be an exact PAPER event") from None


@dataclass(frozen=True, slots=True)
class BacktestProductAssumption:
    """Versioned linear-notional product and commission assumptions."""

    product_id: int
    contract_value: Decimal
    is_perpetual: bool
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    fee_schedule_version: str
    product_snapshot_sha256: str

    def __post_init__(self) -> None:
        if type(self.is_perpetual) is not bool:
            raise BacktestInputError("perpetual flag must be an exact bool") from None
        object.__setattr__(self, "product_id", exact_int(self.product_id, minimum=1))
        object.__setattr__(
            self, "contract_value", exact_decimal(self.contract_value, positive=True)
        )
        object.__setattr__(
            self,
            "maker_fee_rate",
            exact_decimal(self.maker_fee_rate, absolute_max=Decimal("1")),
        )
        object.__setattr__(
            self,
            "taker_fee_rate",
            exact_decimal(self.taker_fee_rate, nonnegative=True, absolute_max=Decimal("1")),
        )
        object.__setattr__(self, "fee_schedule_version", exact_text(self.fee_schedule_version))
        object.__setattr__(
            self, "product_snapshot_sha256", exact_sha256(self.product_snapshot_sha256)
        )


@dataclass(frozen=True, slots=True)
class BacktestFundingSchedule:
    """Explicit product-specific realization calendar and coverage evidence."""

    product_id: int
    version: str
    source_sha256: str
    coverage_start: datetime
    coverage_end: datetime
    realization_times: tuple[datetime, ...]

    def __post_init__(self) -> None:
        product = exact_int(self.product_id, minimum=1)
        start = exact_utc(self.coverage_start)
        end = exact_utc(self.coverage_end)
        if (
            type(self.realization_times) is not tuple
            or len(self.realization_times) > _HARD_MAX_FUNDING_REALIZATIONS
        ):
            raise BacktestInputError("funding realizations must be an immutable tuple") from None
        times = tuple(exact_utc(item) for item in self.realization_times)
        if (
            start > end
            or tuple(sorted(set(times))) != times
            or any(item < start or item > end for item in times)
        ):
            raise BacktestInputError("funding schedule coverage or ordering is invalid") from None
        object.__setattr__(self, "product_id", product)
        object.__setattr__(self, "version", exact_text(self.version))
        object.__setattr__(self, "source_sha256", exact_sha256(self.source_sha256))
        object.__setattr__(self, "coverage_start", start)
        object.__setattr__(self, "coverage_end", end)
        object.__setattr__(self, "realization_times", times)


@dataclass(frozen=True, slots=True)
class BacktestOrderDecision:
    """An order decision submitted only after its containing market event."""

    intent: PaperOrderIntent
    model_config: PaperModelConfig
    liquidity_role: LiquidityRole
    stop_loss_liquidity_role: LiquidityRole | None
    take_profit_liquidity_role: LiquidityRole | None

    def __post_init__(self) -> None:
        intent = _copy_intent(self.intent)
        config = _copy_model_config(self.model_config)
        role = _role(self.liquidity_role)
        stop_role = _role(self.stop_loss_liquidity_role, optional=True)
        profit_role = _role(self.take_profit_liquidity_role, optional=True)
        assert role is not None
        bracket = intent.bracket
        has_stop = bracket is not None and (
            bracket.stop_loss_price is not None or bracket.stop_loss_trail_amount is not None
        )
        has_profit = bracket is not None and bracket.take_profit_price is not None
        if (
            intent.kind
            in (PaperOrderKind.MARKET, PaperOrderKind.STOP_MARKET, PaperOrderKind.TRAILING_STOP)
            and role is not LiquidityRole.TAKER
            or has_stop != (stop_role is not None)
            or has_profit != (profit_role is not None)
            or bracket is None
            and (stop_role is not None or profit_role is not None)
        ):
            raise BacktestInputError("fee role assumptions do not match the order shape") from None
        object.__setattr__(self, "intent", intent)
        object.__setattr__(self, "model_config", config)
        object.__setattr__(self, "liquidity_role", role)
        object.__setattr__(self, "stop_loss_liquidity_role", stop_role)
        object.__setattr__(self, "take_profit_liquidity_role", profit_role)


@dataclass(frozen=True, slots=True)
class BacktestMarketEvent:
    """A sequenced market event followed by zero or more close-derived decisions."""

    sequence: int
    market_event: PaperTopOfBook | PaperCandle
    mark_price: Decimal
    decisions: tuple[BacktestOrderDecision, ...]
    long_liquidation_price: Decimal | None
    short_liquidation_price: Decimal | None

    def __post_init__(self) -> None:
        sequence = exact_int(self.sequence, minimum=1)
        market = _copy_market(self.market_event)
        if (
            type(self.decisions) is not tuple
            or len(self.decisions) > _HARD_MAX_DECISIONS_PER_EVENT
            or any(type(item) is not BacktestOrderDecision for item in self.decisions)
        ):
            raise BacktestInputError("decisions must be an exact immutable tuple") from None
        decisions = tuple(
            BacktestOrderDecision(
                item.intent,
                item.model_config,
                item.liquidity_role,
                item.stop_loss_liquidity_role,
                item.take_profit_liquidity_role,
            )
            for item in self.decisions
        )
        if any(item.intent.product_id != market.product_id for item in decisions):
            raise BacktestInputError("a decision must target its source market product") from None
        long_price = (
            None
            if self.long_liquidation_price is None
            else exact_decimal(self.long_liquidation_price, positive=True)
        )
        short_price = (
            None
            if self.short_liquidation_price is None
            else exact_decimal(self.short_liquidation_price, positive=True)
        )
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "market_event", market)
        object.__setattr__(self, "mark_price", exact_decimal(self.mark_price, positive=True))
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "long_liquidation_price", long_price)
        object.__setattr__(self, "short_liquidation_price", short_price)

    @property
    def observed_at(self) -> datetime:
        """Return the copied source event timestamp."""
        return self.market_event.observed_at

    @property
    def product_id(self) -> int:
        """Return the copied source event product namespace."""
        return self.market_event.product_id


@dataclass(frozen=True, slots=True)
class BacktestFundingEvent:
    """An explicit funding realization rate and valuation snapshot."""

    sequence: int
    observed_at: datetime
    product_id: int
    rate: Decimal
    reference_price: Decimal
    schedule_version: str
    rate_source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "sequence", exact_int(self.sequence, minimum=1))
        object.__setattr__(self, "observed_at", exact_utc(self.observed_at))
        object.__setattr__(self, "product_id", exact_int(self.product_id, minimum=1))
        object.__setattr__(self, "rate", exact_decimal(self.rate, absolute_max=Decimal("1")))
        object.__setattr__(
            self, "reference_price", exact_decimal(self.reference_price, positive=True)
        )
        object.__setattr__(self, "schedule_version", exact_text(self.schedule_version))
        object.__setattr__(self, "rate_source", exact_text(self.rate_source))


BacktestEvent = BacktestMarketEvent | BacktestFundingEvent


@dataclass(frozen=True, slots=True)
class BacktestLimits:
    """Deterministic run-local limits bounded by absolute safety ceilings."""

    max_products: int = 1_024
    max_funding_realizations_per_schedule: int = 100_000
    max_decisions_per_event: int = 1_000
    max_total_decisions: int = 100_000
    max_work_units: int = 10_000_000
    max_output_records: int = 1_000_000

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "max_products", exact_int(self.max_products, minimum=1, maximum=4_096)
        )
        object.__setattr__(
            self,
            "max_funding_realizations_per_schedule",
            exact_int(
                self.max_funding_realizations_per_schedule,
                minimum=1,
                maximum=1_000_000,
            ),
        )
        object.__setattr__(
            self,
            "max_decisions_per_event",
            exact_int(self.max_decisions_per_event, minimum=1, maximum=10_000),
        )
        object.__setattr__(
            self,
            "max_total_decisions",
            exact_int(self.max_total_decisions, minimum=1, maximum=1_000_000),
        )
        object.__setattr__(
            self,
            "max_work_units",
            exact_int(self.max_work_units, minimum=1, maximum=1_000_000_000),
        )
        object.__setattr__(
            self,
            "max_output_records",
            exact_int(self.max_output_records, minimum=1, maximum=10_000_000),
        )


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Complete immutable run assumptions; no ambient configuration is read."""

    paper_account_id: UUID
    initial_cash: Decimal
    products: tuple[BacktestProductAssumption, ...]
    funding_schedules: tuple[BacktestFundingSchedule, ...]
    strict_funding: bool
    assumptions_version: str
    max_events: int
    limits: BacktestLimits = BacktestLimits()

    def __post_init__(self) -> None:
        if (
            type(self.products) is not tuple
            or not self.products
            or any(type(item) is not BacktestProductAssumption for item in self.products)
            or type(self.funding_schedules) is not tuple
            or any(type(item) is not BacktestFundingSchedule for item in self.funding_schedules)
            or type(self.strict_funding) is not bool
            or type(self.limits) is not BacktestLimits
        ):
            raise BacktestInputError("config collections and flags must be exact") from None
        limits = BacktestLimits(
            self.limits.max_products,
            self.limits.max_funding_realizations_per_schedule,
            self.limits.max_decisions_per_event,
            self.limits.max_total_decisions,
            self.limits.max_work_units,
            self.limits.max_output_records,
        )
        if (
            not 1 <= len(self.products) <= limits.max_products
            or (len(self.funding_schedules) > limits.max_products)
            or any(
                len(item.realization_times) > limits.max_funding_realizations_per_schedule
                for item in self.funding_schedules
            )
        ):
            raise BacktestInputError("product or funding schedule limit is exceeded") from None
        configuration_work = len(self.products) + sum(
            len(item.realization_times) for item in self.funding_schedules
        )
        if configuration_work > limits.max_work_units:
            raise BacktestInputError("configuration work budget is exceeded") from None
        products = tuple(
            BacktestProductAssumption(
                item.product_id,
                item.contract_value,
                item.is_perpetual,
                item.maker_fee_rate,
                item.taker_fee_rate,
                item.fee_schedule_version,
                item.product_snapshot_sha256,
            )
            for item in self.products
        )
        schedules = tuple(
            BacktestFundingSchedule(
                item.product_id,
                item.version,
                item.source_sha256,
                item.coverage_start,
                item.coverage_end,
                item.realization_times,
            )
            for item in self.funding_schedules
        )
        product_ids = {item.product_id for item in products}
        if (
            len(product_ids) != len(products)
            or len({item.product_id for item in schedules}) != len(schedules)
            or any(item.product_id not in product_ids for item in schedules)
            or any(
                not next(
                    product.is_perpetual
                    for product in products
                    if product.product_id == item.product_id
                )
                for item in schedules
            )
        ):
            raise BacktestInputError("product and funding namespaces are inconsistent") from None
        object.__setattr__(self, "paper_account_id", exact_uuid(self.paper_account_id))
        object.__setattr__(self, "initial_cash", exact_decimal(self.initial_cash, nonnegative=True))
        object.__setattr__(
            self, "products", tuple(sorted(products, key=lambda item: item.product_id))
        )
        object.__setattr__(
            self, "funding_schedules", tuple(sorted(schedules, key=lambda item: item.product_id))
        )
        object.__setattr__(self, "assumptions_version", exact_text(self.assumptions_version))
        object.__setattr__(
            self, "max_events", exact_int(self.max_events, minimum=1, maximum=1_000_000)
        )
        object.__setattr__(self, "limits", limits)


__all__ = [
    "BacktestConfig",
    "BacktestEvent",
    "BacktestFundingEvent",
    "BacktestFundingSchedule",
    "BacktestLimits",
    "BacktestMarketEvent",
    "BacktestOrderDecision",
    "BacktestProductAssumption",
    "BacktestRunStatus",
    "LiquidityRole",
]
