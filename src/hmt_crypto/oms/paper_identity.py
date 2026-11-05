"""Canonical deterministic identities for inert paper-simulation evidence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256

from hmt_crypto.oms.paper_models import (
    PaperBracketRole,
    PaperCandle,
    PaperModelConfig,
    PaperOrderIntent,
    PaperTopOfBook,
)


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return "~"
    parts = value.as_tuple()
    exponent = parts.exponent
    if (
        type(parts.sign) is not int
        or parts.sign not in (0, 1)
        or type(parts.digits) is not tuple
        or not 1 <= len(parts.digits) <= 38
        or any(type(digit) is not int or not 0 <= digit <= 9 for digit in parts.digits)
        or type(exponent) is not int
        or not -18 <= exponent <= 38
        or not value.is_finite()
    ):
        raise ValueError("invalid canonical Decimal")
    digits = "".join(str(digit) for digit in parts.digits)
    return f"{parts.sign}:{digits}:{exponent}"


def _timestamp_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _digest(tag: str, *parts: object) -> str:
    digest = sha256()
    for part in (tag, *parts):
        encoded = str(part).encode("ascii")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def config_sha256(config: PaperModelConfig) -> str:
    """Hash every deterministic fill-model setting."""
    return _digest(
        "paper-config-v2",
        config.latency_events,
        config.slippage_bps,
        config.max_fill_contracts_per_event,
        config.fee_model.value,
    )


def intent_sha256(intent: PaperOrderIntent) -> str:
    """Hash the complete account/mode/product order intent."""
    bracket = intent.bracket
    return _digest(
        "paper-intent-v2",
        intent.order_id.hex,
        intent.paper_account_id.hex,
        intent.mode.value,
        intent.product_id,
        intent.side.value,
        intent.contracts,
        intent.kind.value,
        _decimal_text(intent.tick_size),
        _decimal_text(intent.limit_price),
        _decimal_text(intent.stop_price),
        _decimal_text(intent.trail_amount),
        "~" if intent.trigger_direction is None else intent.trigger_direction.value,
        int(intent.reduce_only),
        _decimal_text(None if bracket is None else bracket.stop_loss_price),
        _decimal_text(None if bracket is None else bracket.stop_loss_limit_price),
        _decimal_text(None if bracket is None else bracket.stop_loss_trail_amount),
        _decimal_text(None if bracket is None else bracket.take_profit_price),
        _decimal_text(None if bracket is None else bracket.take_profit_limit_price),
    )


def event_sha256(event: PaperTopOfBook | PaperCandle) -> str:
    """Hash stream/index/time and every executable market-data field."""
    common: tuple[object, ...] = (
        event.stream_id,
        event.product_id,
        event.event_index,
        _timestamp_text(event.observed_at),
    )
    if isinstance(event, PaperTopOfBook):
        return _digest(
            "paper-top-v2",
            *common,
            _decimal_text(event.bid_price),
            _decimal_text(event.ask_price),
            event.bid_contracts,
            event.ask_contracts,
            _decimal_text(event.trigger_reference),
        )
    return _digest(
        "paper-candle-v2",
        *common,
        _decimal_text(event.open_price),
        _decimal_text(event.high_price),
        _decimal_text(event.low_price),
        _decimal_text(event.close_price),
        event.volume_contracts,
    )


def fill_sha256(
    intent: PaperOrderIntent,
    config: PaperModelConfig,
    event: PaperTopOfBook | PaperCandle,
    *,
    filled_before: int,
    contracts: int,
    price: Decimal,
    allocation_ordinal: int,
    liquidity_before: int,
    position_before: int,
    reservation_before: int,
) -> str:
    """Hash complete intent/config/event and portfolio allocation evidence."""
    return _digest(
        "paper-fill-v3",
        intent_sha256(intent),
        config_sha256(config),
        event_sha256(event),
        allocation_ordinal,
        liquidity_before,
        position_before,
        reservation_before,
        filled_before,
        contracts,
        _decimal_text(price),
    )


def oco_sha256(intent: PaperOrderIntent, config_digest: str) -> str:
    """Return the stable OCO identity for one bracket parent."""
    return _digest("paper-oco-v2", intent_sha256(intent), config_digest)


def child_sha256(oco_group_sha256: str, role: PaperBracketRole) -> str:
    """Return a stable logical child identity independent of resize revision."""
    return _digest("paper-child-v2", oco_group_sha256, role.value)


def revision_sha256(child_id_sha256: str, contracts: int) -> str:
    """Return the deterministic replace revision for a target child size."""
    return _digest("paper-child-revision-v2", child_id_sha256, contracts)


__all__ = [
    "child_sha256",
    "config_sha256",
    "event_sha256",
    "fill_sha256",
    "intent_sha256",
    "oco_sha256",
    "revision_sha256",
]
