"""Private stream identity/snapshot/sequence/reconciliation invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from hmt_crypto.exchange.delta.private_ws_models import (
    MarginUpdate,
    OrderRow,
    OrderSnapshot,
    OrderUpdate,
    PositionSnapshot,
    PrivateWsAction,
    UserTradeUpdate,
)
from hmt_crypto.exchange.delta.private_ws_sequence import (
    PrivateWsGenerationLedger,
    PrivateWsReadiness,
    PrivateWsSequenceError,
)

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def row(*, symbol: str = "BTCUSD", product_id: int = 27, user_id: int = 12345) -> OrderRow:
    return OrderRow(
        symbol=symbol,
        product_id=product_id,
        order_id=10,
        user_id=user_id,
        client_order_id="HMT-1",
        side="buy",
        size=2,
        unfilled_size=1,
        state="open",
        limit_price=Decimal("100"),
        average_fill_price=None,
        reduce_only=False,
    )


def baseline_ledger() -> PrivateWsGenerationLedger:
    ledger = PrivateWsGenerationLedger(uuid4(), "BTCUSD", 27, "12345")
    ledger.authenticated()
    ledger.subscriptions_accepted()
    ledger.observe(PositionSnapshot(rows=()))
    ledger.observe(OrderSnapshot("BTCUSD", 7, NOW, (row(),)))
    assert ledger.snapshots_ready
    return ledger


def ready_ledger() -> PrivateWsGenerationLedger:
    ledger = baseline_ledger()
    ledger.begin_reconciliation()
    ledger.reconciliation_completed(generation=ledger.generation)
    assert ledger.readiness is PrivateWsReadiness.READY
    return ledger


def test_readiness_requires_snapshots_then_final_reconciliation() -> None:
    ledger = baseline_ledger()
    assert not ledger.authoritative
    ledger.begin_reconciliation()
    assert not ledger.authoritative
    ledger.reconciliation_completed(generation=ledger.generation)
    assert ledger.authoritative


def test_order_gap_duplicate_snapshot_and_trade_reset_invalidate_generation() -> None:
    ledger = ready_ledger()
    with pytest.raises(PrivateWsSequenceError):
        ledger.observe(OrderUpdate(PrivateWsAction.UPDATE, "fill", 9, NOW, row()))
    assert ledger.readiness is PrivateWsReadiness.INVALID

    ledger = ready_ledger()
    with pytest.raises(PrivateWsSequenceError):
        ledger.observe(OrderSnapshot("BTCUSD", 7, NOW, ()))

    ledger = ready_ledger()
    ledger.observe(OrderUpdate(PrivateWsAction.UPDATE, "fill", 8, NOW, row()))
    with pytest.raises(PrivateWsSequenceError):
        ledger.observe(OrderUpdate(PrivateWsAction.UPDATE, "fill", 8, NOW, row()))

    ledger = ready_ledger()
    first = UserTradeUpdate(
        "BTCUSD",
        "fill-1",
        "normal",
        12345,
        2,
        "buy",
        1,
        Decimal("100"),
        1,
        "taker",
        "HMT-1",
        NOW,
        4,
    )
    ledger.observe(first)
    with pytest.raises(PrivateWsSequenceError):
        ledger.observe(
            UserTradeUpdate(
                "BTCUSD",
                "fill-2",
                "normal",
                12345,
                2,
                "buy",
                1,
                Decimal("100"),
                2,
                "taker",
                "HMT-1",
                NOW,
                1,
            )
        )


@pytest.mark.parametrize(
    "observation",
    [
        OrderSnapshot("BTCUSD", 1, NOW, (row(product_id=28),)),
        OrderSnapshot("ETHUSD", 1, NOW, (row(symbol="ETHUSD"),)),
        OrderSnapshot("BTCUSD", 1, NOW, (row(user_id=999),)),
        MarginUpdate(2, "BTC", 1, 999, Decimal("1"), Decimal("1"), Decimal("0"), NOW),
        UserTradeUpdate(
            "BTCUSD",
            "foreign",
            "normal",
            999,
            2,
            "buy",
            1,
            Decimal("100"),
            1,
            "taker",
            "HMT-1",
            NOW,
            1,
        ),
    ],
)
def test_account_catalogue_or_symbol_mismatch_fails_closed(observation) -> None:
    ledger = PrivateWsGenerationLedger(uuid4(), "BTCUSD", 27, "12345")
    ledger.authenticated()
    ledger.subscriptions_accepted()
    if not isinstance(observation, OrderSnapshot):
        ledger.observe(PositionSnapshot(rows=()))
        ledger.observe(OrderSnapshot("BTCUSD", 1, NOW, (row(),)))
    with pytest.raises(PrivateWsSequenceError):
        ledger.observe(observation)


def test_foreign_reconciliation_generation_fails_closed() -> None:
    ledger = baseline_ledger()
    ledger.begin_reconciliation()
    with pytest.raises(PrivateWsSequenceError):
        ledger.reconciliation_completed(generation=uuid4())
    assert ledger.readiness is PrivateWsReadiness.INVALID
