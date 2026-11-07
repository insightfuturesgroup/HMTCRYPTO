"""Owned-copy and state-graph helpers for the pure paper simulator."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

from hmt_crypto.oms.paper_identity import config_sha256 as _config_sha256
from hmt_crypto.oms.paper_models import (
    PaperCandle,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperOrderState,
    PaperOrderStatus,
    PaperSimulationError,
    PaperTopOfBook,
)
from hmt_crypto.oms.paper_values import (
    _copy_decimal,
    _copy_stream_id,
    _copy_utc,
    _exact_int,
)

_STATUSES = tuple(PaperOrderStatus)
_TRIGGER_KINDS = (
    PaperOrderKind.STOP_MARKET,
    PaperOrderKind.STOP_LIMIT,
    PaperOrderKind.TRAILING_STOP,
)


def _fail() -> PaperSimulationError:
    return PaperSimulationError()


def _copy_config(value: object) -> PaperModelConfig:
    if type(value) is not PaperModelConfig:
        raise _fail() from None
    try:
        return PaperModelConfig(
            latency_events=object.__getattribute__(value, "latency_events"),
            slippage_bps=object.__getattribute__(value, "slippage_bps"),
            max_fill_contracts_per_event=object.__getattribute__(
                value, "max_fill_contracts_per_event"
            ),
            fee_model=object.__getattribute__(value, "fee_model"),
        )
    except PaperSimulationError:
        raise
    except Exception:
        raise _fail() from None


def _copy_intent(value: object) -> PaperOrderIntent:
    if type(value) is not PaperOrderIntent:
        raise _fail() from None
    try:
        return PaperOrderIntent(
            order_id=object.__getattribute__(value, "order_id"),
            paper_account_id=object.__getattribute__(value, "paper_account_id"),
            mode=object.__getattribute__(value, "mode"),
            product_id=object.__getattribute__(value, "product_id"),
            side=object.__getattribute__(value, "side"),
            contracts=object.__getattribute__(value, "contracts"),
            kind=object.__getattribute__(value, "kind"),
            tick_size=object.__getattribute__(value, "tick_size"),
            limit_price=object.__getattribute__(value, "limit_price"),
            stop_price=object.__getattribute__(value, "stop_price"),
            trail_amount=object.__getattribute__(value, "trail_amount"),
            trigger_direction=object.__getattribute__(value, "trigger_direction"),
            reduce_only=object.__getattribute__(value, "reduce_only"),
            bracket=object.__getattribute__(value, "bracket"),
        )
    except PaperSimulationError:
        raise
    except Exception:
        raise _fail() from None


def _state_shape_is_valid(
    intent: PaperOrderIntent,
    status: PaperOrderStatus,
    *,
    remaining: int,
    filled: int,
    last: int,
    eligible: int,
    triggered: int | None,
    watermark: Decimal | None,
) -> bool:
    if status in (PaperOrderStatus.WORKING, PaperOrderStatus.TRIGGERED):
        if filled != 0 or remaining != intent.contracts:
            return False
    elif status is PaperOrderStatus.PARTIALLY_FILLED:
        if filled <= 0 or remaining <= 0:
            return False
    elif status is PaperOrderStatus.FILLED:
        if filled != intent.contracts or remaining != 0:
            return False
    elif remaining <= 0:
        return False
    if status is PaperOrderStatus.REJECTED and intent.bracket is None:
        return False

    is_trigger = intent.kind in _TRIGGER_KINDS
    if status is PaperOrderStatus.CANCELLED:
        if not is_trigger:
            return triggered is None and watermark is None
        if intent.kind is not PaperOrderKind.TRAILING_STOP and watermark is not None:
            return False
        return triggered is None or (
            intent.kind is not PaperOrderKind.TRAILING_STOP or watermark is not None
        )
    if not is_trigger:
        return triggered is None and watermark is None and status is not PaperOrderStatus.TRIGGERED
    if intent.kind is not PaperOrderKind.TRAILING_STOP and watermark is not None:
        return False
    if status is PaperOrderStatus.REJECTED:
        return False
    if status is PaperOrderStatus.WORKING:
        return triggered is None and (
            intent.kind is not PaperOrderKind.TRAILING_STOP
            or last < eligible
            or watermark is not None
        )
    return triggered is not None and (
        intent.kind is not PaperOrderKind.TRAILING_STOP or watermark is not None
    )


def _copy_state(value: object) -> PaperOrderState:
    if type(value) is not PaperOrderState:
        raise _fail() from None
    try:
        intent = _copy_intent(object.__getattribute__(value, "intent"))
        stream_id = _copy_stream_id(object.__getattribute__(value, "stream_id"))
        status = object.__getattribute__(value, "status")
        remaining = _exact_int(object.__getattribute__(value, "remaining_contracts"))
        filled = _exact_int(object.__getattribute__(value, "filled_contracts"))
        submitted = _exact_int(object.__getattribute__(value, "submitted_event_index"), minimum=1)
        eligible = _exact_int(object.__getattribute__(value, "eligible_event_index"), minimum=1)
        last = _exact_int(object.__getattribute__(value, "last_event_index"), minimum=1)
        raw_last_observed = object.__getattribute__(value, "last_observed_at")
        last_observed = None if raw_last_observed is None else _copy_utc(raw_last_observed)
        raw_triggered = object.__getattribute__(value, "triggered_event_index")
        triggered = None if raw_triggered is None else _exact_int(raw_triggered, minimum=1)
        raw_watermark = object.__getattribute__(value, "trail_watermark")
        watermark = None if raw_watermark is None else _copy_decimal(raw_watermark, positive=True)
        config_digest = object.__getattribute__(value, "config_sha256")
    except PaperSimulationError:
        raise
    except Exception:
        raise _fail() from None
    if (
        not any(status is candidate for candidate in _STATUSES)
        or remaining + filled != intent.contracts
        or eligible < submitted + 1
        or last < submitted
        or (last == submitted) is not (last_observed is None)
        or triggered is not None
        and (triggered < eligible or triggered > last)
        or type(config_digest) is not str
        or len(config_digest) != 64
        or any(character not in "0123456789abcdef" for character in config_digest)
        or not _state_shape_is_valid(
            intent,
            status,
            remaining=remaining,
            filled=filled,
            last=last,
            eligible=eligible,
            triggered=triggered,
            watermark=watermark,
        )
    ):
        raise _fail() from None
    return PaperOrderState(
        intent=intent,
        stream_id=stream_id,
        status=status,
        remaining_contracts=remaining,
        filled_contracts=filled,
        submitted_event_index=submitted,
        eligible_event_index=eligible,
        last_event_index=last,
        last_observed_at=last_observed,
        triggered_event_index=triggered,
        trail_watermark=watermark,
        config_sha256=config_digest,
    )


def _copy_event(value: object) -> PaperTopOfBook | PaperCandle:
    try:
        if type(value) is PaperTopOfBook:
            return PaperTopOfBook(
                stream_id=object.__getattribute__(value, "stream_id"),
                product_id=object.__getattribute__(value, "product_id"),
                event_index=object.__getattribute__(value, "event_index"),
                observed_at=object.__getattribute__(value, "observed_at"),
                bid_price=object.__getattribute__(value, "bid_price"),
                ask_price=object.__getattribute__(value, "ask_price"),
                bid_contracts=object.__getattribute__(value, "bid_contracts"),
                ask_contracts=object.__getattribute__(value, "ask_contracts"),
                trigger_reference=object.__getattribute__(value, "trigger_reference"),
            )
        if type(value) is PaperCandle:
            return PaperCandle(
                stream_id=object.__getattribute__(value, "stream_id"),
                product_id=object.__getattribute__(value, "product_id"),
                event_index=object.__getattribute__(value, "event_index"),
                observed_at=object.__getattribute__(value, "observed_at"),
                open_price=object.__getattribute__(value, "open_price"),
                high_price=object.__getattribute__(value, "high_price"),
                low_price=object.__getattribute__(value, "low_price"),
                close_price=object.__getattribute__(value, "close_price"),
                volume_contracts=object.__getattribute__(value, "volume_contracts"),
            )
    except PaperSimulationError:
        raise
    except Exception:
        raise _fail() from None
    raise _fail() from None


def submit_paper_order(
    intent: PaperOrderIntent,
    *,
    submitted_event_index: int,
    config: PaperModelConfig,
    stream_id: str = "paper-default",
) -> PaperOrderState:
    """Create an immutable PAPER state without persistence or exchange I/O."""
    owned_intent = _copy_intent(intent)
    owned_config = _copy_config(config)
    submitted = _exact_int(submitted_event_index, minimum=1)
    owned_stream_id = _copy_stream_id(stream_id)
    eligible = _exact_int(submitted + owned_config.latency_events + 1, minimum=1)
    return PaperOrderState(
        intent=owned_intent,
        stream_id=owned_stream_id,
        status=PaperOrderStatus.WORKING,
        remaining_contracts=owned_intent.contracts,
        filled_contracts=0,
        submitted_event_index=submitted,
        eligible_event_index=eligible,
        last_event_index=submitted,
        last_observed_at=None,
        triggered_event_index=None,
        trail_watermark=None,
        config_sha256=_config_sha256(owned_config),
    )


def _next_state(
    state: PaperOrderState,
    *,
    status: PaperOrderStatus | None = None,
    remaining: int | None = None,
    filled: int | None = None,
    event_index: int,
    observed_at: object,
    triggered_event_index: int | None | object = ...,
    watermark: Decimal | None | object = ...,
) -> PaperOrderState:
    return PaperOrderState(
        intent=state.intent,
        stream_id=state.stream_id,
        status=state.status if status is None else status,
        remaining_contracts=state.remaining_contracts if remaining is None else remaining,
        filled_contracts=state.filled_contracts if filled is None else filled,
        submitted_event_index=state.submitted_event_index,
        eligible_event_index=state.eligible_event_index,
        last_event_index=event_index,
        last_observed_at=_copy_utc(observed_at),
        triggered_event_index=(
            state.triggered_event_index
            if triggered_event_index is ...
            else cast(int | None, triggered_event_index)
        ),
        trail_watermark=(
            state.trail_watermark if watermark is ... else cast(Decimal | None, watermark)
        ),
        config_sha256=state.config_sha256,
    )


__all__ = ["submit_paper_order"]
