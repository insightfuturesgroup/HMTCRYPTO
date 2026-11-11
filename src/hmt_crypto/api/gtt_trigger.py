"""Deterministic application-managed GTT trigger evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256


class GttTriggerError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class GttTriggerDecision:
    triggered: bool
    observed_price: Decimal
    trail_anchor: Decimal | None
    evidence_sha256: str


def _positive(value: str | Decimal | None) -> Decimal:
    try:
        parsed = value if type(value) is Decimal else Decimal(value or "")
    except Exception:
        raise GttTriggerError from None
    if not parsed.is_finite() or parsed <= 0:
        raise GttTriggerError
    return parsed


def evaluate_gtt_trigger(
    *,
    gtt_id: str,
    side: str,
    trigger_kind: str,
    trigger_price: str | None,
    trail_offset: str | None,
    prior_anchor: Decimal | None,
    best_bid: str | None,
    best_ask: str | None,
    quote_id: str,
) -> GttTriggerDecision:
    """Evaluate one exit against the executable side of one fresh quote."""
    if side not in {"buy", "sell"} or trigger_kind not in {
        "stop_loss",
        "target",
        "trailing_stop",
    }:
        raise GttTriggerError
    observed = _positive(best_ask if side == "buy" else best_bid)
    anchor: Decimal | None = None
    if trigger_kind == "trailing_stop":
        offset = _positive(trail_offset)
        if prior_anchor is None:
            anchor = observed
        else:
            checked_anchor = _positive(prior_anchor)
            anchor = (
                min(checked_anchor, observed) if side == "buy" else max(checked_anchor, observed)
            )
        triggered = observed >= anchor + offset if side == "buy" else observed <= anchor - offset
    else:
        threshold = _positive(trigger_price)
        if trigger_kind == "stop_loss":
            triggered = observed >= threshold if side == "buy" else observed <= threshold
        else:
            triggered = observed <= threshold if side == "buy" else observed >= threshold
    evidence = sha256(
        (
            f"gtt-trigger:v1:{gtt_id}:{quote_id}:{side}:{trigger_kind}:"
            f"{format(observed, 'f')}:{'' if anchor is None else format(anchor, 'f')}:"
            f"{int(triggered)}"
        ).encode("ascii")
    ).hexdigest()
    return GttTriggerDecision(triggered, observed, anchor, evidence)


__all__ = ["GttTriggerDecision", "GttTriggerError", "evaluate_gtt_trigger"]
