"""Precision-loss regressions for phase34 application paths."""

import json
from decimal import Decimal

from sqlalchemy.dialects import sqlite

from hmt_crypto.api.live_order_admission import evaluate_order_sizing
from hmt_crypto.persistence.models import LiveOrderAction, ProtectionGroup
from hmt_crypto.risk_policy import RiskDeploymentPolicy
from tests.unit.test_live_order_admission import product


def _sqlite_round_trip(model: type, column_name: str, value: Decimal) -> Decimal:
    decimal_type = model.__table__.c[column_name].type
    dialect = sqlite.dialect()
    stored = decimal_type.process_bind_param(value, dialect)
    return decimal_type.process_result_value(stored, dialect)


def test_protection_average_fill_price_round_trip_preserves_18_places() -> None:
    value = Decimal("50000.123456789012345678")
    assert _sqlite_round_trip(ProtectionGroup, "average_fill_price", value) == value


def test_risk_admission_and_policy_schema_preserve_18_places() -> None:
    value = Decimal("50000.123456789012345678")
    policy = RiskDeploymentPolicy(
        policy_conversion_rate="100.123456789012345678",
        reference_rate="100",
    )
    evidence = evaluate_order_sizing(
        product=product(tick_size="0.000000000000000001", contract_value="0.001"),
        contracts=1,
        order_type="limit",
        limit_price=format(value, "f"),
        market_price=format(value, "f"),
        reduce_only=True,
        max_order_notional=Decimal("1"),
    )
    assert policy.policy_conversion_rate == Decimal("100.123456789012345678")
    assert evidence.price == value
    assert evidence.notional == Decimal("50.000123456789012345678")


def test_dispatch_payload_price_round_trip_preserves_18_places() -> None:
    value = Decimal("59000.123456789012345678")
    stored = _sqlite_round_trip(LiveOrderAction, "target_limit_price", value)
    payload = json.dumps(
        {"id": 41, "limit_price": format(stored, "f"), "product_id": 27, "size": 2},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert payload == (
        '{"id":41,"limit_price":"59000.123456789012345678","product_id":27,"size":2}'
    )
