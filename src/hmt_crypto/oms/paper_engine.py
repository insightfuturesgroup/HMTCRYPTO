"""Pure deterministic PAPER/BACKTEST fill evaluation with no I/O authority."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

from hmt_crypto.oms.paper_brackets import bracket_is_safe_for_fill
from hmt_crypto.oms.paper_identity import config_sha256, event_sha256, fill_sha256
from hmt_crypto.oms.paper_models import (
    PaperCandle,
    PaperEvaluation,
    PaperFeeModel,
    PaperFillProposal,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperOrderState,
    PaperOrderStatus,
    PaperSide,
    PaperSimulationError,
    PaperTopOfBook,
    PaperTriggerDirection,
)
from hmt_crypto.oms.paper_state import (
    _copy_config,
    _copy_event,
    _copy_state,
    _next_state,
)
from hmt_crypto.oms.paper_values import (
    _copy_decimal,
    _copy_utc,
    _copy_uuid,
    _exact_int,
)

_TERMINAL = (
    PaperOrderStatus.FILLED,
    PaperOrderStatus.CANCELLED,
    PaperOrderStatus.REJECTED,
)
_ZERO = Decimal(0)
_MAX_POSITION_CONTRACTS = 2**63 - 1


def _fail() -> PaperSimulationError:
    return PaperSimulationError()


def _triggered(intent: PaperOrderIntent, event: PaperTopOfBook | PaperCandle) -> bool:
    stop = intent.stop_price
    direction = intent.trigger_direction
    if stop is None or direction is None:
        raise _fail() from None
    if isinstance(event, PaperTopOfBook):
        reference_low = reference_high = event.trigger_reference
    else:
        reference_low, reference_high = event.low_price, event.high_price
    return (
        reference_high >= stop
        if direction is PaperTriggerDirection.ABOVE
        else reference_low <= stop
    )


def _update_trailing(
    state: PaperOrderState,
    event: PaperTopOfBook | PaperCandle,
) -> tuple[Decimal, bool]:
    direction = state.intent.trigger_direction
    if type(event) is not PaperTopOfBook or state.intent.trail_amount is None or direction is None:
        raise _fail() from None
    reference = event.trigger_reference
    prior = state.trail_watermark
    if prior is None:
        return reference, False
    if direction is PaperTriggerDirection.BELOW:
        watermark = max(prior, reference)
        return watermark, reference <= watermark - state.intent.trail_amount
    watermark = min(prior, reference)
    return watermark, reference >= watermark + state.intent.trail_amount


def _market_data(
    side: PaperSide,
    event: PaperTopOfBook | PaperCandle,
) -> tuple[Decimal, int]:
    if isinstance(event, PaperTopOfBook):
        return (
            (event.ask_price, event.ask_contracts)
            if side is PaperSide.BUY
            else (event.bid_price, event.bid_contracts)
        )
    return event.open_price, event.volume_contracts


def _limit_data(
    intent: PaperOrderIntent,
    event: PaperTopOfBook | PaperCandle,
) -> tuple[Decimal, int] | None:
    limit = intent.limit_price
    if limit is None:
        raise _fail() from None
    if isinstance(event, PaperTopOfBook):
        if intent.side is PaperSide.BUY:
            return (event.ask_price, event.ask_contracts) if event.ask_price <= limit else None
        return (event.bid_price, event.bid_contracts) if event.bid_price >= limit else None
    if intent.side is PaperSide.BUY:
        if event.low_price > limit:
            return None
        return (event.open_price if event.open_price <= limit else limit, event.volume_contracts)
    if event.high_price < limit:
        return None
    return (event.open_price if event.open_price >= limit else limit, event.volume_contracts)


def _fill_price(
    base: Decimal,
    intent: PaperOrderIntent,
    config: PaperModelConfig,
) -> Decimal:
    direction = 1 if intent.side is PaperSide.BUY else -1
    with localcontext() as context:
        context.prec = 60
        raw = base * Decimal(10_000 + direction * config.slippage_bps) / Decimal(10_000)
        ticks = raw / intent.tick_size
        rounding = ROUND_CEILING if intent.side is PaperSide.BUY else ROUND_FLOOR
        price = ticks.to_integral_value(rounding=rounding) * intent.tick_size
    if intent.limit_price is not None and intent.kind in (
        PaperOrderKind.LIMIT,
        PaperOrderKind.STOP_LIMIT,
    ):
        price = (
            min(price, intent.limit_price)
            if intent.side is PaperSide.BUY
            else max(price, intent.limit_price)
        )
    return _copy_decimal(price, positive=True)


def _evaluate_allocated_order(
    state: PaperOrderState,
    event: PaperTopOfBook | PaperCandle,
    *,
    config: PaperModelConfig,
    position_contracts: int,
    allocation_ordinal: int,
    liquidity_before: int,
    reservation_before: int,
) -> PaperEvaluation:
    """Evaluate one portfolio-owned allocation; callers must use the portfolio API."""
    owned_state = _copy_state(state)
    owned_event = _copy_event(event)
    owned_config = _copy_config(config)
    position = _exact_int(
        position_contracts,
        minimum=-_MAX_POSITION_CONTRACTS,
        maximum=_MAX_POSITION_CONTRACTS,
    )
    ordinal = _exact_int(allocation_ordinal)
    liquidity = _exact_int(liquidity_before)
    reservation = _exact_int(reservation_before)
    if (not owned_state.intent.reduce_only and reservation != 0) or (
        owned_state.intent.reduce_only and reservation > owned_state.remaining_contracts
    ):
        raise _fail() from None
    event_digest = event_sha256(owned_event)
    if (
        owned_state.status in _TERMINAL
        or config_sha256(owned_config) != owned_state.config_sha256
        or owned_event.stream_id != owned_state.stream_id
        or owned_event.product_id != owned_state.intent.product_id
        or owned_event.event_index != owned_state.last_event_index + 1
        or owned_state.last_observed_at is not None
        and owned_event.observed_at <= owned_state.last_observed_at
    ):
        raise _fail() from None
    if owned_event.event_index < owned_state.eligible_event_index:
        next_state = _next_state(
            owned_state,
            event_index=owned_event.event_index,
            observed_at=owned_event.observed_at,
        )
        return PaperEvaluation(next_state, None, (), "WAITING_FOR_LATENCY")

    intent = owned_state.intent
    if intent.kind is PaperOrderKind.TRAILING_STOP and owned_state.triggered_event_index is None:
        watermark, trigger = _update_trailing(owned_state, owned_event)
        if not trigger:
            next_state = _next_state(
                owned_state,
                event_index=owned_event.event_index,
                observed_at=owned_event.observed_at,
                watermark=watermark,
            )
            return PaperEvaluation(next_state, None, (), "TRAILING_WATERMARK_UPDATED")
        next_state = _next_state(
            owned_state,
            status=PaperOrderStatus.TRIGGERED,
            event_index=owned_event.event_index,
            observed_at=owned_event.observed_at,
            triggered_event_index=owned_event.event_index,
            watermark=watermark,
        )
        return PaperEvaluation(next_state, None, (), "TRIGGERED_NEXT_EVENT_EXECUTION")

    if (
        intent.kind in (PaperOrderKind.STOP_MARKET, PaperOrderKind.STOP_LIMIT)
        and owned_state.triggered_event_index is None
    ):
        if not _triggered(intent, owned_event):
            next_state = _next_state(
                owned_state,
                event_index=owned_event.event_index,
                observed_at=owned_event.observed_at,
            )
            return PaperEvaluation(next_state, None, (), "TRIGGER_NOT_REACHED")
        next_state = _next_state(
            owned_state,
            status=PaperOrderStatus.TRIGGERED,
            event_index=owned_event.event_index,
            observed_at=owned_event.observed_at,
            triggered_event_index=owned_event.event_index,
        )
        return PaperEvaluation(next_state, None, (), "TRIGGERED_NEXT_EVENT_EXECUTION")

    use_limit = intent.kind in (PaperOrderKind.LIMIT, PaperOrderKind.STOP_LIMIT)
    data = _limit_data(intent, owned_event) if use_limit else _market_data(intent.side, owned_event)
    if data is None:
        next_state = _next_state(
            owned_state,
            event_index=owned_event.event_index,
            observed_at=owned_event.observed_at,
        )
        return PaperEvaluation(next_state, None, (), "NOT_MARKETABLE")
    base_price, available = data
    available = min(available, liquidity, owned_config.max_fill_contracts_per_event)
    reduction_cap: int | None = None
    if intent.reduce_only:
        reduction_cap = -position if intent.side is PaperSide.BUY else position
        if reduction_cap <= 0:
            next_state = _next_state(
                owned_state,
                status=PaperOrderStatus.CANCELLED,
                event_index=owned_event.event_index,
                observed_at=owned_event.observed_at,
            )
            return PaperEvaluation(next_state, None, (), "REDUCE_ONLY_NO_POSITION")
        reduction_cap = min(reduction_cap, reservation)
        available = min(available, reduction_cap)
    contracts = min(available, owned_state.remaining_contracts)
    if contracts <= 0:
        next_state = _next_state(
            owned_state,
            event_index=owned_event.event_index,
            observed_at=owned_event.observed_at,
        )
        return PaperEvaluation(next_state, None, (), "NO_EXECUTABLE_LIQUIDITY")
    price = _fill_price(base_price, intent, owned_config)
    next_filled = owned_state.filled_contracts + contracts
    next_remaining = owned_state.remaining_contracts - contracts
    if not bracket_is_safe_for_fill(owned_state, price):
        rejected = _next_state(
            owned_state,
            status=PaperOrderStatus.REJECTED,
            event_index=owned_event.event_index,
            observed_at=owned_event.observed_at,
        )
        return PaperEvaluation(rejected, None, (), "BRACKET_INVALID_FOR_FILL")
    reduction_cancel = (
        intent.reduce_only
        and reduction_cap is not None
        and contracts == reduction_cap
        and reduction_cap < owned_state.remaining_contracts
    )
    if reduction_cancel:
        next_status = PaperOrderStatus.CANCELLED
        reason = "REDUCE_ONLY_REMAINDER_CANCELLED"
    elif next_remaining == 0:
        next_status = PaperOrderStatus.FILLED
        reason = "FILLED"
    else:
        next_status = PaperOrderStatus.PARTIALLY_FILLED
        reason = "PARTIAL_FILL"
    next_state = _next_state(
        owned_state,
        status=next_status,
        remaining=next_remaining,
        filled=next_filled,
        event_index=owned_event.event_index,
        observed_at=owned_event.observed_at,
    )
    fill = PaperFillProposal(
        fill_key_sha256=fill_sha256(
            intent,
            owned_config,
            owned_event,
            filled_before=owned_state.filled_contracts,
            contracts=contracts,
            price=price,
            allocation_ordinal=ordinal,
            liquidity_before=liquidity,
            position_before=position,
            reservation_before=reservation,
        ),
        order_id=_copy_uuid(intent.order_id),
        paper_account_id=_copy_uuid(intent.paper_account_id),
        mode=intent.mode,
        product_id=intent.product_id,
        side=intent.side,
        contracts=contracts,
        price=price,
        observed_at=_copy_utc(owned_event.observed_at),
        fee=_ZERO,
        funding=_ZERO,
        model_label=str.__str__(PaperFeeModel.SYNTHETIC_ZERO_FEE),
        event_sha256=event_digest,
    )
    return PaperEvaluation(next_state, fill, (), reason)


__all__: list[str] = []
