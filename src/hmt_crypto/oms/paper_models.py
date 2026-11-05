"""Exact, inert contracts for deterministic PAPER/BACKTEST execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_values import (
    PaperSimulationError,
    _copy_decimal,
    _copy_stream_id,
    _copy_utc,
    _copy_uuid,
    _exact_int,
    _fail,
    _is_tick_aligned,
)


class PaperSide(StrEnum):
    """Order direction within the simulator."""

    BUY = "BUY"
    SELL = "SELL"


class PaperOrderKind(StrEnum):
    """Closed order forms implemented by the Phase 2 simulator."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"


class PaperTriggerDirection(StrEnum):
    """Market-reference direction that activates a stop independently of side."""

    ABOVE = "ABOVE"
    BELOW = "BELOW"


class PaperOrderStatus(StrEnum):
    """Pure simulator state; it is not an exchange order state."""

    WORKING = "WORKING"
    TRIGGERED = "TRIGGERED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PaperFeeModel(StrEnum):
    """Phase 2 supports only an explicitly synthetic zero-fee assumption."""

    SYNTHETIC_ZERO_FEE = "SYNTHETIC_ZERO_FEE_NOT_LIVE_DELTA"


class PaperBracketAction(StrEnum):
    """Non-authorizing bracket lifecycle proposal."""

    CREATE = "CREATE"
    REPLACE = "REPLACE"
    CANCEL = "CANCEL"


class PaperBracketRole(StrEnum):
    """Stable role of one child within an OCO bracket."""

    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


_SIDES = tuple(PaperSide)
_KINDS = tuple(PaperOrderKind)
_TRIGGER_DIRECTIONS = tuple(PaperTriggerDirection)
_STATUSES = tuple(PaperOrderStatus)
_FEE_MODELS = tuple(PaperFeeModel)
_MODES = (OmsExecutionMode.PAPER, OmsExecutionMode.BACKTEST)


def _canonical(value: object, members: tuple[object, ...]) -> bool:
    return any(value is member for member in members)


@dataclass(frozen=True, slots=True)
class PaperBracketSpec:
    """Optional reduce-only stop/take-profit children for a parent fill."""

    stop_loss_price: Decimal | None
    stop_loss_limit_price: Decimal | None
    stop_loss_trail_amount: Decimal | None
    take_profit_price: Decimal | None
    take_profit_limit_price: Decimal | None

    def __post_init__(self) -> None:
        values = {
            name: None if value is None else _copy_decimal(value, positive=True)
            for name, value in (
                ("stop_loss_price", self.stop_loss_price),
                ("stop_loss_limit_price", self.stop_loss_limit_price),
                ("stop_loss_trail_amount", self.stop_loss_trail_amount),
                ("take_profit_price", self.take_profit_price),
                ("take_profit_limit_price", self.take_profit_limit_price),
            )
        }
        fixed_stop = values["stop_loss_price"] is not None
        trailing_stop = values["stop_loss_trail_amount"] is not None
        if (
            fixed_stop == trailing_stop
            and (fixed_stop or trailing_stop)
            or values["stop_loss_limit_price"] is not None
            and not fixed_stop
            or values["take_profit_limit_price"] is not None
            and values["take_profit_price"] is None
            or not (fixed_stop or trailing_stop or values["take_profit_price"] is not None)
        ):
            raise _fail() from None
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class PaperOrderIntent:
    """Inert simulator intent; it cannot dispatch or persist an order."""

    order_id: UUID
    paper_account_id: UUID
    mode: OmsExecutionMode
    product_id: int
    side: PaperSide
    contracts: int
    kind: PaperOrderKind
    tick_size: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    trail_amount: Decimal | None
    trigger_direction: PaperTriggerDirection | None
    reduce_only: bool
    bracket: PaperBracketSpec | None

    def __post_init__(self) -> None:
        order_id = _copy_uuid(self.order_id)
        account_id = _copy_uuid(self.paper_account_id)
        product_id = _exact_int(self.product_id, minimum=1)
        contracts = _exact_int(self.contracts, minimum=1)
        tick = _copy_decimal(self.tick_size, positive=True)
        limit_price = (
            None if self.limit_price is None else _copy_decimal(self.limit_price, positive=True)
        )
        stop_price = (
            None if self.stop_price is None else _copy_decimal(self.stop_price, positive=True)
        )
        trail_amount = (
            None if self.trail_amount is None else _copy_decimal(self.trail_amount, positive=True)
        )
        bracket = None if self.bracket is None else _copy_bracket(self.bracket)
        if (
            not _canonical(self.mode, _MODES)
            or not _canonical(self.side, _SIDES)
            or not _canonical(self.kind, _KINDS)
            or (
                self.trigger_direction is not None
                and not _canonical(self.trigger_direction, _TRIGGER_DIRECTIONS)
            )
            or type(self.reduce_only) is not bool
        ):
            raise _fail() from None
        valid_shape = {
            PaperOrderKind.MARKET: (
                limit_price is None
                and stop_price is None
                and trail_amount is None
                and self.trigger_direction is None
            ),
            PaperOrderKind.LIMIT: (
                limit_price is not None
                and stop_price is None
                and trail_amount is None
                and self.trigger_direction is None
            ),
            PaperOrderKind.STOP_MARKET: (
                limit_price is None
                and stop_price is not None
                and trail_amount is None
                and self.trigger_direction is not None
            ),
            PaperOrderKind.STOP_LIMIT: (
                limit_price is not None
                and stop_price is not None
                and trail_amount is None
                and self.trigger_direction is not None
            ),
            PaperOrderKind.TRAILING_STOP: (
                limit_price is None
                and stop_price is None
                and trail_amount is not None
                and self.trigger_direction is not None
            ),
        }
        if not valid_shape[self.kind]:
            raise _fail() from None
        if bracket is not None and (
            self.reduce_only or self.kind not in (PaperOrderKind.MARKET, PaperOrderKind.LIMIT)
        ):
            raise _fail() from None
        prices = [limit_price, stop_price, trail_amount]
        if bracket is not None:
            prices.extend(
                (
                    bracket.stop_loss_price,
                    bracket.stop_loss_limit_price,
                    bracket.stop_loss_trail_amount,
                    bracket.take_profit_price,
                    bracket.take_profit_limit_price,
                )
            )
        if any(value is not None and not _is_tick_aligned(value, tick) for value in prices):
            raise _fail() from None
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "paper_account_id", account_id)
        object.__setattr__(self, "product_id", product_id)
        object.__setattr__(self, "contracts", contracts)
        object.__setattr__(self, "tick_size", tick)
        object.__setattr__(self, "limit_price", limit_price)
        object.__setattr__(self, "stop_price", stop_price)
        object.__setattr__(self, "trail_amount", trail_amount)
        object.__setattr__(self, "bracket", bracket)


@dataclass(frozen=True, slots=True)
class PaperModelConfig:
    """Immutable deterministic assumptions bound to an order at submission."""

    latency_events: int
    slippage_bps: int
    max_fill_contracts_per_event: int
    fee_model: PaperFeeModel = PaperFeeModel.SYNTHETIC_ZERO_FEE

    def __post_init__(self) -> None:
        object.__setattr__(self, "latency_events", _exact_int(self.latency_events))
        object.__setattr__(self, "slippage_bps", _exact_int(self.slippage_bps, maximum=9999))
        object.__setattr__(
            self,
            "max_fill_contracts_per_event",
            _exact_int(self.max_fill_contracts_per_event, minimum=1),
        )
        if not _canonical(self.fee_model, _FEE_MODELS):
            raise _fail() from None


@dataclass(frozen=True, slots=True)
class PaperTopOfBook:
    """One sequenced quote event with explicit executable liquidity."""

    stream_id: str
    product_id: int
    event_index: int
    observed_at: datetime
    bid_price: Decimal
    ask_price: Decimal
    bid_contracts: int
    ask_contracts: int
    trigger_reference: Decimal

    def __post_init__(self) -> None:
        stream_id = _copy_stream_id(self.stream_id)
        product_id = _exact_int(self.product_id, minimum=1)
        event_index = _exact_int(self.event_index, minimum=1)
        observed = _copy_utc(self.observed_at)
        bid = _copy_decimal(self.bid_price, positive=True)
        ask = _copy_decimal(self.ask_price, positive=True)
        trigger = _copy_decimal(self.trigger_reference, positive=True)
        if bid > ask:
            raise _fail() from None
        object.__setattr__(self, "stream_id", stream_id)
        object.__setattr__(self, "product_id", product_id)
        object.__setattr__(self, "event_index", event_index)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "bid_price", bid)
        object.__setattr__(self, "ask_price", ask)
        object.__setattr__(self, "trigger_reference", trigger)
        object.__setattr__(self, "bid_contracts", _exact_int(self.bid_contracts))
        object.__setattr__(self, "ask_contracts", _exact_int(self.ask_contracts))


@dataclass(frozen=True, slots=True)
class PaperCandle:
    """One completed candle; its open is the only market-fill price."""

    stream_id: str
    product_id: int
    event_index: int
    observed_at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume_contracts: int

    def __post_init__(self) -> None:
        stream_id = _copy_stream_id(self.stream_id)
        product_id = _exact_int(self.product_id, minimum=1)
        event_index = _exact_int(self.event_index, minimum=1)
        observed = _copy_utc(self.observed_at)
        opening = _copy_decimal(self.open_price, positive=True)
        high = _copy_decimal(self.high_price, positive=True)
        low = _copy_decimal(self.low_price, positive=True)
        close = _copy_decimal(self.close_price, positive=True)
        if low > high or not low <= opening <= high or not low <= close <= high:
            raise _fail() from None
        object.__setattr__(self, "stream_id", stream_id)
        object.__setattr__(self, "product_id", product_id)
        object.__setattr__(self, "event_index", event_index)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "open_price", opening)
        object.__setattr__(self, "high_price", high)
        object.__setattr__(self, "low_price", low)
        object.__setattr__(self, "close_price", close)
        object.__setattr__(self, "volume_contracts", _exact_int(self.volume_contracts))


@dataclass(frozen=True, slots=True)
class PaperOrderState:
    """Immutable simulator projection consumed by one later market event."""

    intent: PaperOrderIntent
    stream_id: str
    status: PaperOrderStatus
    remaining_contracts: int
    filled_contracts: int
    submitted_event_index: int
    eligible_event_index: int
    last_event_index: int
    last_observed_at: datetime | None
    triggered_event_index: int | None
    trail_watermark: Decimal | None
    config_sha256: str


@dataclass(frozen=True, slots=True)
class PaperFillProposal:
    """Non-authorizing simulated fill evidence under synthetic assumptions."""

    fill_key_sha256: str
    order_id: UUID
    paper_account_id: UUID
    mode: OmsExecutionMode
    product_id: int
    side: PaperSide
    contracts: int
    price: Decimal
    observed_at: datetime
    fee: Decimal
    funding: Decimal
    model_label: str
    event_sha256: str


@dataclass(frozen=True, slots=True)
class PaperChildOrderProposal:
    """Non-authorizing reduce-only bracket child target."""

    parent_order_id: UUID
    child_id_sha256: str
    revision_sha256: str
    replaces_revision_sha256: str | None
    oco_group_sha256: str
    action: PaperBracketAction
    role: PaperBracketRole
    product_id: int
    side: PaperSide
    contracts: int
    kind: PaperOrderKind
    stop_price: Decimal | None
    limit_price: Decimal | None
    trail_amount: Decimal | None
    trigger_direction: PaperTriggerDirection
    reduce_only: bool


@dataclass(frozen=True, slots=True)
class PaperEvaluation:
    """Pure next state and optional simulated fill/child proposals."""

    state: PaperOrderState
    fill: PaperFillProposal | None
    children: tuple[PaperChildOrderProposal, ...]
    reason: str


def _copy_bracket(value: object) -> PaperBracketSpec:
    if type(value) is not PaperBracketSpec:
        raise _fail() from None
    try:
        return PaperBracketSpec(
            stop_loss_price=object.__getattribute__(value, "stop_loss_price"),
            stop_loss_limit_price=object.__getattribute__(value, "stop_loss_limit_price"),
            stop_loss_trail_amount=object.__getattribute__(value, "stop_loss_trail_amount"),
            take_profit_price=object.__getattribute__(value, "take_profit_price"),
            take_profit_limit_price=object.__getattribute__(value, "take_profit_limit_price"),
        )
    except PaperSimulationError:
        raise
    except Exception:
        raise _fail() from None


__all__ = [
    "PaperBracketAction",
    "PaperBracketRole",
    "PaperBracketSpec",
    "PaperCandle",
    "PaperChildOrderProposal",
    "PaperEvaluation",
    "PaperFeeModel",
    "PaperFillProposal",
    "PaperModelConfig",
    "PaperOrderIntent",
    "PaperOrderKind",
    "PaperOrderState",
    "PaperOrderStatus",
    "PaperSide",
    "PaperSimulationError",
    "PaperTopOfBook",
    "PaperTriggerDirection",
]
