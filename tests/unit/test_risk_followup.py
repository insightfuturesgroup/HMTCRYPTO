"""Deployment fallback and bounded telemetry checks for P1-06."""

import os
from decimal import Decimal

import pytest

from hmt_crypto.risk_policy import (
    REJECTIONS,
    RiskDeploymentPolicy,
    policy_rejection,
    record_rejection,
)

pytestmark = pytest.mark.safety


def test_unconfigured_deployment_uses_conservative_defaults(monkeypatch):
    for key in os.environ:
        if key.startswith("HMT_RISK_"):
            monkeypatch.delenv(key)
    config = RiskDeploymentPolicy()
    assert config.max_order_notional == Decimal("50")
    assert config.max_position_notional == Decimal("100")
    assert config.max_daily_loss == Decimal("20")
    assert config.max_order_limit_inr == Decimal("5000")
    assert config.max_position_limit_inr == Decimal("10000")
    assert config.max_daily_loss_limit_inr == Decimal("2000")
    assert config.max_open_orders == 5
    assert config.max_inflight_signals == 5
    assert policy_rejection(None, stage="submission_claim", config=config) == "risk_policy_missing"


def test_unknown_labels_collapse_to_other():
    record_rejection("untrusted-stage", "untrusted-reason")
    samples = [sample for metric in REJECTIONS.collect() for sample in metric.samples]
    assert not any("untrusted-stage" in sample.labels.values() for sample in samples)
    assert not any("untrusted-reason" in sample.labels.values() for sample in samples)
    assert any(sample.labels == {"stage": "other", "reason": "other"} for sample in samples)


def test_field_error_codes_share_fixed_metric_category():
    record_rejection("save", "risk_policy_non_positive_max_order_limit_inr")
    samples = [sample for metric in REJECTIONS.collect() for sample in metric.samples]
    assert any(
        sample.labels == {"stage": "save", "reason": "risk_policy_non_positive"}
        for sample in samples
    )
