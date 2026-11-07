"""Safety contract for the durable offline PAPER/BACKTEST worker."""

from datetime import UTC, timedelta
from decimal import Decimal
from importlib import import_module
from importlib.util import find_spec
from uuid import UUID

import pytest

from hmt_crypto.backtest.models import BacktestProductAssumption, LiquidityRole
from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_models import (
    PaperFeeModel,
    PaperModelConfig,
    PaperOrderIntent,
    PaperOrderKind,
    PaperSide,
    PaperTopOfBook,
)
from hmt_crypto.persistence.repository_contracts import AccountScope
from tests.unit.test_risk_manager import NOW, intent, policy, snapshot

pytestmark = pytest.mark.unit


def test_paper_worker_contract_is_available_as_a_real_module() -> None:
    """Removing the integrated worker must be caught before deployment."""

    assert find_spec("hmt_crypto.worker.paper_execution") is not None
    assert find_spec("hmt_crypto.worker.paper_runner") is not None


def test_paper_worker_exposes_closed_offline_contracts() -> None:
    """Deleting any required closed contract must break the public worker boundary."""

    module = import_module("hmt_crypto.worker.paper_execution")

    assert all(
        hasattr(module, name)
        for name in (
            "PaperCrashPoint",
            "PaperExecutionRequest",
            "PaperExecutionResult",
            "PaperExecutionService",
            "PaperFundingPolicy",
            "PaperIntentSource",
            "RecordedPaperEvent",
            "export_paper_report",
        )
    )


def test_recorded_runner_exposes_strategy_sizing_boundary() -> None:
    """Removing the explicit strategy sizing adapter must break the runner contract."""

    module = import_module("hmt_crypto.worker.paper_runner")

    assert hasattr(module, "RecordedStrategyRunner")
    assert hasattr(module, "StrategyPaperSizing")


def _request_values(*, mode: str = "BACKTEST") -> dict[str, object]:
    module = import_module("hmt_crypto.worker.paper_execution")
    execution_mode = OmsExecutionMode.BACKTEST if mode == "BACKTEST" else OmsExecutionMode.PAPER
    account_id = UUID("00000000-0000-0000-0000-000000000111")
    order = PaperOrderIntent(
        order_id=UUID("00000000-0000-0000-0000-000000000222"),
        paper_account_id=account_id,
        mode=execution_mode,
        product_id=1101,
        side=PaperSide.BUY,
        contracts=2,
        kind=PaperOrderKind.MARKET,
        tick_size=Decimal("0.5"),
        limit_price=None,
        stop_price=None,
        trail_amount=None,
        trigger_direction=None,
        reduce_only=False,
        bracket=None,
    )
    first_at = NOW + timedelta(seconds=11)
    second_at = NOW + timedelta(seconds=12)
    first = module.RecordedPaperEvent(
        sequence=1,
        market_event=PaperTopOfBook(
            stream_id="recorded-btc",
            product_id=1101,
            event_index=1,
            observed_at=first_at,
            bid_price=Decimal("99.5"),
            ask_price=Decimal("100"),
            bid_contracts=10,
            ask_contracts=10,
            trigger_reference=Decimal("100"),
        ),
        mark_price=Decimal("100"),
    )
    second = module.RecordedPaperEvent(
        sequence=2,
        market_event=PaperTopOfBook(
            stream_id="recorded-btc",
            product_id=1101,
            event_index=2,
            observed_at=second_at,
            bid_price=Decimal("100"),
            ask_price=Decimal("101"),
            bid_contracts=10,
            ask_contracts=10,
            trigger_reference=Decimal("101"),
        ),
        mark_price=Decimal("101"),
    )
    return {
        "scope": AccountScope(
            account_id=account_id,
            region="india",
            environment="testnet",
            mode=mode,
        ),
        "request_id": "manual-btc-0001",
        "source": module.PaperIntentSource.MANUAL,
        "intent": order,
        "risk_policy": policy(),
        "risk_intent": intent(),
        "risk_snapshot": snapshot(),
        "model_config": PaperModelConfig(
            latency_events=0,
            slippage_bps=0,
            max_fill_contracts_per_event=10,
            fee_model=PaperFeeModel.SYNTHETIC_ZERO_FEE,
        ),
        "recorded_events": (first, second),
        "product": BacktestProductAssumption(
            product_id=1101,
            contract_value=Decimal("1"),
            is_perpetual=False,
            maker_fee_rate=Decimal("-0.0001"),
            taker_fee_rate=Decimal("0.001"),
            fee_schedule_version="delta-test-fees-v1",
            product_snapshot_sha256="9" * 64,
        ),
        "liquidity_role": LiquidityRole.TAKER,
        "funding_schedule": None,
        "funding_events": (),
        "funding_policy": module.PaperFundingPolicy.REQUIRE_RECORDED_MARKS,
        "initial_cash": Decimal("1000"),
        "assumptions_version": "paper-assumptions-v1",
        "signal": None,
    }


def test_request_accepts_exact_backtest_scope_and_recorded_inputs() -> None:
    """Weakening the complete exact request boundary must be caught."""

    module = import_module("hmt_crypto.worker.paper_execution")

    request = module.PaperExecutionRequest(**_request_values())

    assert request.scope.mode == "BACKTEST"
    assert request.intent.mode is OmsExecutionMode.BACKTEST
    assert request.recorded_events[1].market_event.observed_at.tzinfo is UTC


def test_request_rejects_live_before_any_execution_capability_can_exist() -> None:
    """Admitting LIVE into the offline worker is a production-order safety bug."""

    module = import_module("hmt_crypto.worker.paper_execution")
    values = _request_values()
    values["scope"] = AccountScope(
        account_id=values["scope"].account_id,  # type: ignore[union-attr]
        region="india",
        environment="testnet",
        mode="LIVE",
    )

    with pytest.raises(ValueError, match="offline"):
        module.PaperExecutionRequest(**values)


def test_request_rejects_scope_and_simulator_mode_mismatch() -> None:
    """Cross-mode projection would corrupt tenant isolation and must fail closed."""

    module = import_module("hmt_crypto.worker.paper_execution")
    values = _request_values(mode="PAPER")
    original = values["intent"]
    assert isinstance(original, PaperOrderIntent)
    values["intent"] = PaperOrderIntent(
        order_id=original.order_id,
        paper_account_id=original.paper_account_id,
        mode=OmsExecutionMode.BACKTEST,
        product_id=original.product_id,
        side=original.side,
        contracts=original.contracts,
        kind=original.kind,
        tick_size=original.tick_size,
        limit_price=original.limit_price,
        stop_price=original.stop_price,
        trail_amount=original.trail_amount,
        trigger_direction=original.trigger_direction,
        reduce_only=original.reduce_only,
        bracket=original.bracket,
    )

    with pytest.raises(ValueError, match="scope"):
        module.PaperExecutionRequest(**values)
