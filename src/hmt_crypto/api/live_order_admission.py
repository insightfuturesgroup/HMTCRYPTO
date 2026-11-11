"""Pure contract/tick/notional checks for non-dispatchable Live admission."""

from dataclasses import dataclass
from decimal import Decimal

from hmt_crypto.domain import Product
from hmt_crypto.exchange.delta.sizing_math import (
    NumericContractError,
    floor_division,
    is_zero,
    multiply,
    multiply_integer,
    positive_decimal,
    positive_integer,
)
from hmt_crypto.exchange.delta.sizing_validation import validate_supported_product


class LiveOrderAdmissionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class OrderSizingEvidence:
    price: Decimal
    price_basis: str
    notional: Decimal


def position_notional(*, product: Product, contracts: int, price: Decimal) -> Decimal:
    """Return exact linear notional for one bounded absolute position size."""
    try:
        checked_contracts = positive_integer(contracts)
        contract_value = positive_decimal(product.contract_value)
        checked_price = positive_decimal(price)
        return multiply(checked_price, multiply_integer(contract_value, checked_contracts))
    except Exception:
        raise LiveOrderAdmissionError() from None


def evaluate_order_sizing(
    *,
    product: Product,
    contracts: int,
    order_type: str,
    limit_price: str | None,
    market_price: str | None,
    reduce_only: bool,
    max_order_notional: Decimal,
) -> OrderSizingEvidence:
    try:
        validate_supported_product(product, risk_increasing=not reduce_only)
        checked_contracts = positive_integer(contracts)
        contract_value = positive_decimal(product.contract_value)
        tick_size = positive_decimal(product.tick_size)
        maximum = positive_decimal(max_order_notional)
        if order_type == "limit" and limit_price is not None and market_price is not None:
            price = positive_decimal(limit_price)
            price_basis = "limit"
        elif order_type == "market" and limit_price is None and market_price is not None:
            price = positive_decimal(market_price)
            price_basis = "best_quote"
        else:
            raise NumericContractError
        ticks, _, remainder = floor_division(price, tick_size)
        if ticks == 0 or not is_zero(remainder):
            raise NumericContractError
        notional = multiply(price, multiply_integer(contract_value, checked_contracts))
        if notional > maximum:
            raise NumericContractError
        return OrderSizingEvidence(price=price, price_basis=price_basis, notional=notional)
    except Exception:
        raise LiveOrderAdmissionError() from None


__all__ = [
    "LiveOrderAdmissionError",
    "OrderSizingEvidence",
    "evaluate_order_sizing",
    "position_notional",
]
