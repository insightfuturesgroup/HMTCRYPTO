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


def _integer(value: object, *, allow_zero: bool = False) -> int:
    if type(value) is int and (value >= 0 if allow_zero else value > 0):
        return value
    if type(value) is str and value.isascii() and value.isdigit():
        parsed = int(value)
        if parsed >= (0 if allow_zero else 1):
            return parsed
    raise LiveOrderAdmissionError()


def projected_position_contracts(
    *,
    positions: list[object],
    open_orders: list[object],
    product_id: int,
    new_side: str,
    new_contracts: int,
    new_reduce_only: bool,
) -> int:
    """Return the worst same-product position after all risk-increasing orders fill."""
    if (
        type(product_id) is not int
        or product_id <= 0
        or new_side not in {"buy", "sell"}
        or type(new_contracts) is not int
        or new_contracts <= 0
        or type(new_reduce_only) is not bool
    ):
        raise LiveOrderAdmissionError()
    signed_position = 0
    for row in positions:
        if not isinstance(row, dict):
            raise LiveOrderAdmissionError()
        if row.get("product_id") == product_id:
            value = row.get("size")
            if type(value) is not int:
                raise LiveOrderAdmissionError()
            signed_position = value
    pending_buy = 0
    pending_sell = 0
    for row in open_orders:
        if not isinstance(row, dict):
            raise LiveOrderAdmissionError()
        if row.get("product_id") != product_id or row.get("reduce_only") is True:
            continue
        side = row.get("side")
        if side not in {"buy", "sell"}:
            raise LiveOrderAdmissionError()
        remaining = _integer(row.get("unfilled_size"), allow_zero=True)
        if side == "buy":
            pending_buy += remaining
        else:
            pending_sell += remaining
    if not new_reduce_only:
        if new_side == "buy":
            pending_buy += new_contracts
        else:
            pending_sell += new_contracts
    return max(abs(signed_position + pending_buy), abs(signed_position - pending_sell))


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
    "projected_position_contracts",
]
