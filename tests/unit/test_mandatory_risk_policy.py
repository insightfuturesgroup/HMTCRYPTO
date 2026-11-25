"""Mandatory entry risk validation; isolated from all live services."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from hmt_crypto.risk_policy import RiskDeploymentPolicy, policy_rejection

pytestmark = pytest.mark.safety


def valid_policy():
    return SimpleNamespace(
        enabled=True,
        max_order_notional=Decimal("50"),
        max_position_notional=Decimal("100"),
        max_daily_loss=Decimal("20"),
        max_order_limit_inr=Decimal("5000"),
        max_position_limit_inr=Decimal("10000"),
        max_daily_loss_limit_inr=Decimal("2000"),
        usd_inr_rate=Decimal("100"),
        max_open_orders=5,
        max_inflight_signals=5,
    )


def test_missing_and_disabled_policy():
    assert policy_rejection(None, stage="preview") == "risk_policy_missing"
    policy = valid_policy()
    policy.enabled = False
    assert policy_rejection(policy, stage="preview") == "risk_policy_disabled"


@pytest.mark.parametrize(
    "field",
    [
        "max_order_notional",
        "max_position_notional",
        "max_daily_loss",
        "max_order_limit_inr",
        "max_position_limit_inr",
        "max_daily_loss_limit_inr",
    ],
)
@pytest.mark.parametrize(
    "value,reason",
    [
        (0, "non_positive"),
        (-1, "non_positive"),
        ("NaN", "malformed"),
        ("Infinity", "malformed"),
        ("bad", "malformed"),
        (None, "malformed"),
    ],
)
def test_bad_monetary_limits(field, value, reason):
    policy = valid_policy()
    setattr(policy, field, value)
    assert policy_rejection(policy, stage="signal_claim") == f"risk_policy_{reason}_{field}"


@pytest.mark.parametrize("field", ["max_open_orders", "max_inflight_signals"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "5", None])
def test_count_requires_positive_integer(field, value):
    policy = valid_policy()
    setattr(policy, field, value)
    assert policy_rejection(policy, stage="submission_claim").startswith("risk_policy_")


def test_inconsistent_derived_usd_rejected():
    policy = valid_policy()
    policy.max_daily_loss = Decimal("19")
    assert policy_rejection(policy, stage="admission") == "risk_policy_inconsistent_max_daily_loss"


def test_deployment_ceiling():
    config = RiskDeploymentPolicy(max_open_orders=4)
    assert (
        policy_rejection(valid_policy(), stage="save", config=config)
        == "risk_policy_ceiling_max_open_orders"
    )


@pytest.mark.parametrize(
    "field",
    [
        "max_order_notional",
        "max_position_notional",
        "max_daily_loss",
        "max_order_limit_inr",
        "max_position_limit_inr",
        "max_daily_loss_limit_inr",
        "max_open_orders",
        "max_inflight_signals",
    ],
)
def test_every_field_has_a_deployment_ceiling(field):
    policy = valid_policy()
    setattr(policy, field, getattr(policy, field) * 2)
    assert policy_rejection(policy, stage="save") == f"risk_policy_ceiling_{field}"


@pytest.mark.parametrize(
    "stage", ["preview", "admission", "receipt", "signal_claim", "submission_claim"]
)
def test_exit_exempt_and_entry_requires_enabled_policy(stage):
    policy = valid_policy()
    policy.enabled = False
    assert policy_rejection(policy, stage=stage, reduce_only=True) is None
    assert policy_rejection(policy, stage=stage) == "risk_policy_disabled"


def test_valid_policy():
    assert policy_rejection(valid_policy(), stage="preview") is None


def test_conversion_startup_warning():
    with pytest.warns(RuntimeWarning, match="below deployment reference"):
        RiskDeploymentPolicy(policy_conversion_rate=Decimal("99"), reference_rate=Decimal("100"))


def test_reduce_only_sizing_exempts_policy_limits():
    from test_live_order_admission import product

    from hmt_crypto.api.live_order_admission import LiveOrderAdmissionError, evaluate_order_sizing

    arguments = dict(
        product=product(),
        contracts=1,
        order_type="market",
        limit_price=None,
        market_price="50000",
        max_order_notional=Decimal(0),
    )
    assert evaluate_order_sizing(**arguments, reduce_only=True).notional == Decimal("50")
    with pytest.raises(LiveOrderAdmissionError):
        evaluate_order_sizing(**arguments, reduce_only=False)


@pytest.mark.parametrize(
    "value,reason",
    [
        ("0", "non_positive"),
        ("-1", "non_positive"),
        ("NaN", "malformed"),
        ("Infinity", "malformed"),
        ("bad", "malformed"),
    ],
)
def test_save_rejects_invalid_values_with_distinct_codes(value, reason):
    from fastapi import HTTPException

    from hmt_crypto.api.platform_api import _save_policy_decimal

    with pytest.raises(HTTPException) as caught:
        _save_policy_decimal(value, "max_order_limit_inr")
    assert caught.value.detail == f"risk_policy_{reason}_max_order_limit_inr"


def test_api_entry_validation_keeps_exit_exemption():
    from fastapi import HTTPException

    from hmt_crypto.api.platform_api import _require_entry_policy

    for stage in ("preview", "admission", "receipt"):
        _require_entry_policy(None, stage=stage, reduce_only=True)
        with pytest.raises(HTTPException) as caught:
            _require_entry_policy(None, stage=stage, reduce_only=False)
        assert caught.value.detail == "risk_policy_missing"
