"""Focused contract-aware checks for non-dispatchable Live order admission."""

from decimal import Decimal

import pytest

from hmt_crypto.api.live_order_admission import (
    InsufficientAvailableBalanceError,
    LiveOrderAdmissionError,
    ensure_sufficient_available_balance,
    evaluate_order_sizing,
    position_notional,
    projected_position_contracts,
)
from hmt_crypto.domain import (
    Asset,
    ContractType,
    NotionalType,
    Product,
    ProductState,
    TradingStatus,
)

pytestmark = pytest.mark.unit


def product(**changes: object) -> Product:
    btc = Asset(id=1, symbol="BTC", precision=8, minimum_precision=2)
    usd = Asset(id=2, symbol="USD", precision=8, minimum_precision=2)
    values: dict[str, object] = {
        "id": 27,
        "symbol": "BTCUSD",
        "contract_type": ContractType.PERPETUAL_FUTURES,
        "notional_type": NotionalType.VANILLA,
        "contract_value": "0.001",
        "contract_unit_currency": "BTC",
        "tick_size": "0.5",
        "state": ProductState.LIVE,
        "trading_status": TradingStatus.OPERATIONAL,
        "underlying_asset": btc,
        "quoting_asset": usd,
        "settling_asset": usd,
        "is_quanto": False,
        "only_reduce_only_orders_allowed": False,
    }
    values.update(changes)
    return Product(**values)


def test_limit_preview_uses_exact_contract_notional() -> None:
    evidence = evaluate_order_sizing(
        product=product(),
        contracts=3,
        order_type="limit",
        limit_price="50000.5",
        market_price="50001",
        reduce_only=False,
        max_order_notional=Decimal("200"),
    )

    assert evidence.price == Decimal("50000.5")
    assert evidence.price_basis == "limit"
    assert evidence.notional == Decimal("150.0015")


def test_available_balance_covers_estimated_initial_margin() -> None:
    ensure_sufficient_available_balance(
        product=product(initial_margin="0.02"),
        balances=[
            {
                "asset_id": 2,
                "asset_symbol": "USD",
                "available_balance": "1.01",
            }
        ],
        notional=Decimal("50"),
    )


def test_available_balance_rejects_below_estimated_initial_margin() -> None:
    with pytest.raises(InsufficientAvailableBalanceError):
        ensure_sufficient_available_balance(
            product=product(initial_margin="0.02"),
            balances=[
                {
                    "asset_id": 2,
                    "asset_symbol": "USD",
                    "available_balance": "0.99",
                }
            ],
            notional=Decimal("50"),
        )


def test_available_balance_rejects_missing_settlement_evidence() -> None:
    with pytest.raises(LiveOrderAdmissionError):
        ensure_sufficient_available_balance(
            product=product(initial_margin="0.02"),
            balances=[],
            notional=Decimal("50"),
        )


@pytest.mark.parametrize(
    ("changes", "price"),
    [
        ({}, "50000.1"),
        ({"notional_type": NotionalType.INVERSE}, "50000.5"),
        ({"only_reduce_only_orders_allowed": True}, "50000.5"),
    ],
)
def test_risk_increasing_preview_rejects_unsupported_or_off_tick_product(
    changes: dict[str, object], price: str
) -> None:
    with pytest.raises(LiveOrderAdmissionError):
        evaluate_order_sizing(
            product=product(**changes),
            contracts=1,
            order_type="limit",
            limit_price=price,
            market_price="50001",
            reduce_only=False,
            max_order_notional=Decimal("100"),
        )


def test_market_preview_rejects_notional_above_policy() -> None:
    with pytest.raises(LiveOrderAdmissionError):
        evaluate_order_sizing(
            product=product(),
            contracts=3,
            order_type="market",
            limit_price=None,
            market_price="50000.5",
            reduce_only=False,
            max_order_notional=Decimal("150"),
        )


def test_position_notional_uses_bounded_exact_math() -> None:
    assert position_notional(product=product(), contracts=7, price=Decimal("50000.5")) == Decimal(
        "350.0035"
    )


def test_position_envelope_includes_pending_same_product_entries() -> None:
    projected = projected_position_contracts(
        positions=[{"product_id": 27, "size": 2}],
        open_orders=[
            {"product_id": 27, "side": "buy", "unfilled_size": 3, "reduce_only": False},
            {"product_id": 27, "side": "sell", "unfilled_size": 7, "reduce_only": False},
            {"product_id": 27, "side": "sell", "unfilled_size": 99, "reduce_only": True},
            {"product_id": 28, "side": "buy", "unfilled_size": 99, "reduce_only": False},
        ],
        product_id=27,
        new_side="buy",
        new_contracts=4,
        new_reduce_only=False,
    )

    assert projected == 9


def test_reduce_only_order_does_not_increase_position_envelope() -> None:
    assert (
        projected_position_contracts(
            positions=[{"product_id": 27, "size": -5}],
            open_orders=[],
            product_id=27,
            new_side="buy",
            new_contracts=5,
            new_reduce_only=True,
        )
        == 5
    )


def test_position_envelope_rejects_malformed_pending_order() -> None:
    with pytest.raises(LiveOrderAdmissionError):
        projected_position_contracts(
            positions=[],
            open_orders=[{"product_id": 27, "side": "buy", "unfilled_size": "bad"}],
            product_id=27,
            new_side="buy",
            new_contracts=1,
            new_reduce_only=False,
        )
