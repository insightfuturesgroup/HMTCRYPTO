"""Secret-free operational reporting; never an authorization or execution gate."""

from datetime import datetime
from functools import lru_cache
from typing import Any

from prometheus_client import Counter
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hmt_crypto.risk_policy import COUNTS, MONEY, RiskDeploymentPolicy, _reason, deployment_policy


class RecoveryAlertSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HMT_RECOVERY_", env_file=None, frozen=True)
    warning_seconds: int = Field(default=15, gt=0)
    critical_seconds: int = Field(default=60, gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "RecoveryAlertSettings":
        if self.critical_seconds <= self.warning_seconds:
            raise ValueError("Recovery critical threshold must exceed warning threshold")
        return self


@lru_cache(maxsize=1)
def recovery_alert_settings() -> RecoveryAlertSettings:
    return RecoveryAlertSettings()


AGED_UNCERTAINTY = Counter(
    "hmt_live_recovery_aged_checks_total",
    "Recovery checks above the uncertainty age threshold (not distinct orders)",
    ("severity",),
)


def risk_health(policy: object | None) -> dict[str, object]:
    reason = _reason(policy, deployment_policy())
    return {
        "riskValid": reason is None,
        "entryBlockReason": (
            None
            if reason is None
            else "entries blocked: risk policy disabled"
            if reason == "risk_policy_disabled"
            else "entries blocked: invalid risk limits"
        ),
        "riskValidationCode": reason,
    }


def log_risk_configuration(
    component: str, logger: Any, config: RiskDeploymentPolicy | None = None
) -> None:
    config = config or deployment_policy()
    logger.info(
        "risk_deployment_configuration",
        component=component,
        ceilings={field: str(getattr(config, field)) for field in MONEY + COUNTS},
        policy_conversion_rate=str(config.policy_conversion_rate),
    )
    defaults = sorted(set(MONEY + COUNTS) - config.model_fields_set)
    if defaults:
        logger.warning("risk_ceilings_defaults_in_use", fields=defaults)


def warn_untradeable_products(
    products: Any, tickers: Any, logger: Any, config: RiskDeploymentPolicy | None = None
) -> list[str]:
    config = config or deployment_policy()
    asks = {ticker.product_id: ticker.best_ask for ticker in tickers}
    blocked = sorted(
        product.symbol
        for product in products
        if asks.get(product.id) is not None
        and product.contract_value * asks[product.id]
        > min(config.max_order_notional, config.max_position_notional)
    )
    if blocked:
        logger.warning(
            "risk_ceilings_exclude_minimum_contract",
            symbols=blocked,
            action="explicit deployment ceilings and valid account policy required",
        )
    return blocked


def uncertainty_status(sent_at: datetime | None, *, now: datetime) -> dict[str, Any]:
    config = recovery_alert_settings()
    age = None if sent_at is None else max(0, int((now - sent_at).total_seconds()))
    severity = (
        "critical"
        if age is None or age >= config.critical_seconds
        else "warning"
        if age >= config.warning_seconds
        else None
    )
    return {
        "uncertainAgeSeconds": age,
        "uncertainSeverity": severity,
        "uncertainReason": "missing_or_ambiguous_exchange_evidence",
    }


def alert_uncertainty(
    sent_at: datetime | None,
    *,
    now: datetime,
    logger: Any,
    context: dict[str, str] | None = None,
) -> None:
    state = uncertainty_status(sent_at, now=now)
    severity = state["uncertainSeverity"]
    if severity is not None:
        AGED_UNCERTAINTY.labels(severity=severity).inc()
        emit = logger.error if severity == "critical" else logger.warning
        emit("live_recovery_uncertainty_aged", **state, **(context or {}))
