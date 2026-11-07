from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from hmt_crypto.api.control_plane import DashboardSnapshot, OrderView, PositionView
from hmt_crypto.api.views import dashboard_view


def test_typescript_dashboard_fixture_is_emitted_by_python_contract() -> None:
    """Keep the checked-in browser fixture owned by the Python serializer."""
    observed_at = datetime(2026, 11, 6, 12, tzinfo=UTC)
    snapshot = DashboardSnapshot(
        sequence=42,
        generated_at=observed_at,
        evidence_observed_at=observed_at,
        region="india",
        environment="testnet",
        mode="PAPER",
        account_label="testnet-paper",
        catalogue_fresh=True,
        stream_connected=True,
        reconciliation_state="READY_CLAIM",
        risk_halted=False,
        collateral_asset="INR",
        equity="90071992547409931234567890.125",
        available_balance="75000.00",
        total_exposure="25000.00",
        daily_pnl="-0.00000000000000000001",
        margin_usage_percent="25.00",
        positions=(
            PositionView(
                product_id=27,
                symbol="BTCUSD",
                side="long",
                contracts="2",
                entry_price="60000.00",
                last_price="61010.00",
                mark_price="61000.00",
                leverage="2",
                liquidation_price="40000.00",
                unrealised_pnl="250.00",
                realised_pnl="50.00",
                margin_used="10000.00",
                funding_rate="0.0100",
                protection_state="PAPER_SIMULATED",
                protection_observed_at=observed_at,
                protection_reconciliation_generation=3,
            ),
        ),
        open_orders=(
            OrderView(
                local_order_id=UUID("d0c5479a-e1ce-45fb-b6e5-08cf85bb2387"),
                exchange_order_id="exchange-order-1",
                client_order_id="client-order-1",
                symbol="BTCUSD",
                side="sell",
                order_type="limit_order",
                contracts="2",
                limit_price="62000.00",
                status="open",
                reduce_only=True,
                updated_at=observed_at,
            ),
        ),
    )
    fixture_path = (
        Path(__file__).resolve().parents[2] / "ui" / "src" / "test" / "python_dashboard_view.json"
    )

    assert json.loads(fixture_path.read_text(encoding="utf-8")) == dashboard_view(snapshot)
