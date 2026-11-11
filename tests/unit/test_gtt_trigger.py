"""Exact GTT trigger direction and trailing-watermark tests."""

from decimal import Decimal

import pytest

from hmt_crypto.api.gtt_trigger import GttTriggerError, evaluate_gtt_trigger
from hmt_crypto.persistence.platform_models import GttOrder

pytestmark = pytest.mark.unit


def test_gtt_model_contains_durable_trigger_evidence() -> None:
    assert {
        "trail_anchor",
        "last_quote_id",
        "triggered_at",
        "trigger_evidence_sha256",
        "last_error_code",
    } <= set(GttOrder.__table__.c.keys())
    assert "ck_gtt_orders_trigger_evidence_pair" in {
        constraint.name for constraint in GttOrder.__table__.constraints
    }


@pytest.mark.parametrize(
    ("side", "kind", "price", "triggered"),
    [
        ("sell", "stop_loss", "99", True),
        ("sell", "target", "101", True),
        ("buy", "stop_loss", "101", True),
        ("buy", "target", "99", True),
    ],
)
def test_fixed_triggers_use_the_executable_quote_side(
    side: str, kind: str, price: str, triggered: bool
) -> None:
    decision = evaluate_gtt_trigger(
        gtt_id="10000000-0000-4000-8000-000000000001",
        side=side,
        trigger_kind=kind,
        trigger_price="100",
        trail_offset=None,
        prior_anchor=None,
        best_bid=price,
        best_ask=price,
        quote_id="20000000-0000-4000-8000-000000000001",
    )

    assert decision.triggered is triggered
    assert len(decision.evidence_sha256) == 64


def test_sell_trailing_stop_raises_watermark_and_then_triggers() -> None:
    raised = evaluate_gtt_trigger(
        gtt_id="gtt",
        side="sell",
        trigger_kind="trailing_stop",
        trigger_price=None,
        trail_offset="5",
        prior_anchor=Decimal("100"),
        best_bid="110",
        best_ask="111",
        quote_id="quote-1",
    )
    triggered = evaluate_gtt_trigger(
        gtt_id="gtt",
        side="sell",
        trigger_kind="trailing_stop",
        trigger_price=None,
        trail_offset="5",
        prior_anchor=raised.trail_anchor,
        best_bid="105",
        best_ask="106",
        quote_id="quote-2",
    )

    assert raised.trail_anchor == Decimal("110")
    assert raised.triggered is False
    assert triggered.triggered is True


def test_buy_trailing_stop_lowers_watermark_and_then_triggers() -> None:
    lowered = evaluate_gtt_trigger(
        gtt_id="gtt",
        side="buy",
        trigger_kind="trailing_stop",
        trigger_price=None,
        trail_offset="5",
        prior_anchor=Decimal("100"),
        best_bid="89",
        best_ask="90",
        quote_id="quote-1",
    )
    triggered = evaluate_gtt_trigger(
        gtt_id="gtt",
        side="buy",
        trigger_kind="trailing_stop",
        trigger_price=None,
        trail_offset="5",
        prior_anchor=lowered.trail_anchor,
        best_bid="94",
        best_ask="95",
        quote_id="quote-2",
    )

    assert lowered.trail_anchor == Decimal("90")
    assert lowered.triggered is False
    assert triggered.triggered is True


@pytest.mark.parametrize("bad", [None, "", "0", "-1", "NaN", "Infinity"])
def test_invalid_or_missing_executable_quote_fails_closed(bad: str | None) -> None:
    with pytest.raises(GttTriggerError):
        evaluate_gtt_trigger(
            gtt_id="gtt",
            side="sell",
            trigger_kind="stop_loss",
            trigger_price="100",
            trail_offset=None,
            prior_anchor=None,
            best_bid=bad,
            best_ask="100",
            quote_id="quote",
        )
