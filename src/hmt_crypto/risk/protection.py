"""Pure protective stop coverage calculation."""

from decimal import Decimal

from hmt_crypto.risk.context import EvaluationContext, age_seconds
from hmt_crypto.risk.models import (
    Completeness,
    ProtectionOrderStatus,
    ProtectionOrderType,
    Side,
)


def _fresh_reference_price(context: EvaluationContext) -> Decimal | None:
    policy, snapshot = context.policy, context.snapshot
    price = snapshot.price
    if price.completeness is not Completeness.COMPLETE:
        return None
    if (
        price.mark_price is not None
        and price.mark_observed_at is not None
        and age_seconds(snapshot.evaluated_at, price.mark_observed_at)
        <= policy.max_mark_age_seconds
    ):
        return price.mark_price
    if (
        policy.allow_last_price_fallback
        and price.last_price is not None
        and price.last_observed_at is not None
        and age_seconds(snapshot.evaluated_at, price.last_observed_at)
        <= policy.max_last_price_age_seconds
    ):
        return price.last_price
    return None


def protection_values(context: EvaluationContext) -> tuple[int, int]:
    """Return capped verified coverage and required existing-plus-candidate contracts."""
    intent, snapshot = context.intent, context.snapshot
    required = sum(abs(item.signed_contracts) for item in snapshot.positions) + intent.contracts
    reference = _fresh_reference_price(context)
    covered = 0
    for position in snapshot.positions:
        opposite = Side.SELL if position.signed_contracts > 0 else Side.BUY
        available = sum(
            item.remaining_contracts
            for item in snapshot.protective_orders
            if item.product_id == position.product_id
            and item.side is opposite
            and item.reduce_only
            and item.order_type in {ProtectionOrderType.STOP_MARKET, ProtectionOrderType.STOP_LIMIT}
            and item.status is ProtectionOrderStatus.ACTIVE
            and (
                (position.signed_contracts > 0 and item.trigger_price < item.reference_price)
                or (position.signed_contracts < 0 and item.trigger_price > item.reference_price)
            )
        )
        covered += min(abs(position.signed_contracts), available)
    stop_direction_ok = intent.stop_price is not None and (
        (
            intent.side is Side.BUY
            and intent.stop_price < intent.order_price
            and reference is not None
            and intent.stop_price < reference
        )
        or (
            intent.side is Side.SELL
            and intent.stop_price > intent.order_price
            and reference is not None
            and intent.stop_price > reference
        )
    )
    if stop_direction_ok:
        covered += min(intent.contracts, intent.planned_protection_contracts)
    return covered, required


__all__ = ["protection_values"]
