"""Offline operational visibility must not weaken entry admission."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hmt_crypto import risk_operations as subject
from hmt_crypto.errors import ExchangeProtocolError
from hmt_crypto.exchange.delta.websocket_codec import decode_ws_object
from hmt_crypto.risk_policy import RiskDeploymentPolicy

pytestmark = pytest.mark.safety


@pytest.mark.parametrize("frame", ["[]", "null", "true", "42", '"text"'])
def test_private_nonobject_rejected_by_decoder(frame):
    with pytest.raises(ExchangeProtocolError) as error:
        decode_ws_object(frame, max_bytes=1_048_576, stream_label="private")
    assert error.value.context == {"operation": "decode_ws_frame", "scope": "private"}


def test_invalid_enabled_policy_health():
    assert subject.risk_health(SimpleNamespace(enabled=True)) == {
        "riskValid": False,
        "entryBlockReason": "entries blocked: invalid risk limits",
        "riskValidationCode": "risk_policy_malformed_max_order_notional",
    }


@pytest.mark.parametrize("field", ["max_order_notional", "max_position_notional", "max_daily_loss"])
def test_enabled_zero_policy_health_blocks_entries(field):
    config = RiskDeploymentPolicy()
    policy = SimpleNamespace(
        **{name: getattr(config, name) for name in subject.MONEY + subject.COUNTS},
        enabled=True,
        usd_inr_rate=config.policy_conversion_rate,
    )
    assert subject.risk_health(policy)["riskValid"] is True
    setattr(policy, field, Decimal(0))
    assert subject.risk_health(policy)["entryBlockReason"] == "entries blocked: invalid risk limits"


def test_startup_logs_own_ceilings_without_secret_configuration():
    logger = Mock()
    subject.log_risk_configuration("worker", logger, RiskDeploymentPolicy())
    payload = logger.info.call_args.kwargs
    assert payload["component"] == "worker"
    assert payload["ceilings"]["max_order_notional"] == "50"
    assert "password" not in repr(payload)
    logger.warning.assert_called_once()


def test_minimum_contract_warning_uses_executable_ask_and_position_ceiling():
    logger = Mock()
    products = [SimpleNamespace(id=101, symbol="BTC", contract_value=Decimal("0.001"))]
    tickers = [SimpleNamespace(product_id=101, best_ask=Decimal("50000.5"))]
    assert subject.warn_untradeable_products(products, tickers, logger, RiskDeploymentPolicy()) == [
        "BTC"
    ]
    assert logger.warning.call_args.kwargs["symbols"] == ["BTC"]


def test_fixture_minimum_notional_exceeds_default_order_ceiling():
    root = Path(__file__).parents[1] / "fixtures/delta"
    product = json.loads((root / "public_products_page1.json").read_text())["result"][0]
    ticker = json.loads((root / "public_ticker.json").read_text())["result"]
    assert product["id"] == ticker["product_id"]
    minimum = Decimal(product["contract_value"]) * Decimal(ticker["quotes"]["best_ask"])
    assert minimum == Decimal("50.0005")
    assert RiskDeploymentPolicy().max_order_notional < minimum
    assert RiskDeploymentPolicy().max_position_notional > minimum


def test_migration_password_reserved_characters_round_trip():
    from sqlalchemy.engine import URL, make_url

    password = "offline@:/%?#fixture"
    wire = URL.create(
        "postgresql+asyncpg",
        username="postgres",
        password=password,
        host="db.fixture.supabase.co",
        database="postgres",
    ).render_as_string(hide_password=False)
    assert make_url(wire).password == password
    assert password not in wire


@pytest.mark.parametrize("age,severity", [(14, None), (15, "warning"), (60, "critical")])
def test_uncertainty_age_thresholds(age, severity):
    now = datetime.now(UTC)
    state = subject.uncertainty_status(now - timedelta(seconds=age), now=now)
    assert state["uncertainAgeSeconds"] == age
    assert state["uncertainSeverity"] == severity


def test_missing_send_time_never_looks_fresh():
    state = subject.uncertainty_status(None, now=datetime.now(UTC))
    assert state["uncertainAgeSeconds"] is None
    assert state["uncertainSeverity"] == "critical"


def test_aged_missing_evidence_emits_fixed_label_metric_and_alert():
    now = datetime.now(UTC)
    logger = Mock()
    before = subject.AGED_UNCERTAINTY.labels(severity="critical")._value.get()
    subject.alert_uncertainty(now - timedelta(seconds=121), now=now, logger=logger)
    assert subject.AGED_UNCERTAINTY.labels(severity="critical")._value.get() == before + 1
    logger.error.assert_called_once_with(
        "live_recovery_uncertainty_aged",
        uncertainAgeSeconds=121,
        uncertainSeverity="critical",
        uncertainReason="missing_or_ambiguous_exchange_evidence",
    )


def test_threshold_configuration_rejects_reversed_thresholds():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        subject.RecoveryAlertSettings(warning_seconds=120, critical_seconds=30)
