"""Deployment-bounded mandatory risk policy for risk-increasing orders."""

import warnings
from decimal import Decimal, InvalidOperation, localcontext
from functools import lru_cache
from typing import Self

from prometheus_client import Counter
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hmt_crypto.logging import get_logger


class RiskDeploymentPolicy(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HMT_RISK_", env_file=None, frozen=True)

    policy_conversion_rate: Decimal = Field(default=Decimal("100"), gt=0, allow_inf_nan=False)
    reference_rate: Decimal = Field(default=Decimal("100"), gt=0, allow_inf_nan=False)
    max_order_notional: Decimal = Field(default=Decimal("50"), gt=0, allow_inf_nan=False)
    max_position_notional: Decimal = Field(default=Decimal("100"), gt=0, allow_inf_nan=False)
    max_daily_loss: Decimal = Field(default=Decimal("20"), gt=0, allow_inf_nan=False)
    max_order_limit_inr: Decimal = Field(default=Decimal("5000"), gt=0, allow_inf_nan=False)
    max_position_limit_inr: Decimal = Field(default=Decimal("10000"), gt=0, allow_inf_nan=False)
    max_daily_loss_limit_inr: Decimal = Field(default=Decimal("2000"), gt=0, allow_inf_nan=False)
    max_open_orders: int = Field(default=5, gt=0)
    max_inflight_signals: int = Field(default=5, gt=0)

    @model_validator(mode="after")
    def warn_conversion(self) -> Self:
        if self.policy_conversion_rate < self.reference_rate:
            warnings.warn(
                "Policy conversion rate is below deployment reference rate; "
                "INR limits may not be conservative",
                RuntimeWarning,
                stacklevel=2,
            )
        return self


@lru_cache(maxsize=1)
def deployment_policy() -> RiskDeploymentPolicy:
    return RiskDeploymentPolicy()


REJECTIONS = Counter(
    "hmt_risk_policy_rejections_total", "Entry risk-policy rejections", ("stage", "reason")
)
MONEY = (
    "max_order_notional",
    "max_position_notional",
    "max_daily_loss",
    "max_order_limit_inr",
    "max_position_limit_inr",
    "max_daily_loss_limit_inr",
)
COUNTS = ("max_open_orders", "max_inflight_signals")


def policy_rejection(
    policy: object | None,
    *,
    stage: str,
    reduce_only: bool = False,
    config: RiskDeploymentPolicy | None = None,
) -> str | None:
    if reduce_only:
        return None
    config = config or deployment_policy()
    reason = _reason(policy, config)
    if reason:
        record_rejection(stage, reason)
    return reason


def record_rejection(stage: str, reason: str) -> None:
    REJECTIONS.labels(stage=stage, reason=reason).inc()
    get_logger("risk.policy").warning("risk_policy_rejected", stage=stage, reason=reason)


def _reason(policy: object | None, config: RiskDeploymentPolicy) -> str | None:
    if policy is None:
        return "risk_policy_missing"
    if getattr(policy, "enabled", None) is not True:
        return "risk_policy_disabled"
    values: dict[str, Decimal] = {}
    for field in MONEY + ("usd_inr_rate",):
        raw = getattr(policy, field, None)
        try:
            if isinstance(raw, bool) or raw is None:
                raise ValueError()
            value = Decimal(str(raw))
            if not value.is_finite():
                raise ValueError()
        except (ValueError, InvalidOperation):
            return f"risk_policy_malformed_{field}"
        if value <= 0:
            return f"risk_policy_non_positive_{field}"
        if field != "usd_inr_rate" and value > getattr(config, field):
            return f"risk_policy_ceiling_{field}"
        values[field] = value
    for field in COUNTS:
        count = getattr(policy, field, None)
        if type(count) is not int:
            return f"risk_policy_malformed_{field}"
        if count <= 0:
            return f"risk_policy_non_positive_{field}"
        if count > getattr(config, field):
            return f"risk_policy_ceiling_{field}"
    if values["usd_inr_rate"] != config.policy_conversion_rate:
        return "risk_policy_conversion_rate_mismatch"
    # The stored USD fields have scale 18. Compare at that database precision.
    with localcontext() as context:
        context.prec = 80
        for usd, inr in zip(MONEY[:3], MONEY[3:], strict=True):
            derived = (values[inr] / values["usd_inr_rate"]).quantize(Decimal("1e-18"))
            if values[usd] != derived:
                return f"risk_policy_inconsistent_{usd}"
    return None
