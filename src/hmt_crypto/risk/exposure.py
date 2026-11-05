"""Side-aware conservative position and exposure calculations."""

from __future__ import annotations

from decimal import Decimal

from hmt_crypto.risk.math import multiply_amount, sum_amounts
from hmt_crypto.risk.models import ExposureLeg, RiskInputError, Side, _exact_side


def _contracts(value: object, *, allow_zero: bool) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value < 2**63:
        raise RiskInputError() from None
    return value


def _signed_position(value: object) -> int:
    if type(value) is not int or not -(2**63) < value < 2**63:
        raise RiskInputError() from None
    return value


def _legs(value: object) -> tuple[ExposureLeg, ...]:
    if type(value) is not tuple or any(type(item) is not ExposureLeg for item in value):
        raise RiskInputError() from None
    return value


def strict_reduction(position: object, side: object, contracts: object) -> int:
    """Return the post-trade position only for a strict non-reversing reduction."""
    current = _signed_position(position)
    direction = _exact_side(side)
    quantity = _contracts(contracts, allow_zero=False)
    delta = quantity if direction is Side.BUY else -quantity
    result = current + delta
    if current == 0 or result != 0 and (result > 0) != (current > 0) or abs(result) >= abs(current):
        raise RiskInputError() from None
    return result


def worst_position_contracts(position: object, legs: object) -> int:
    """Return the largest absolute one-sided fill extreme without order netting."""
    current = _signed_position(position)
    buys = 0
    sells = 0
    for leg in _legs(legs):
        if leg.reduce_only:
            continue
        if leg.side is Side.BUY:
            buys += leg.contracts
        else:
            sells += leg.contracts
        if buys >= 2**63 or sells >= 2**63:
            raise RiskInputError() from None
    return max(abs(current + buys), abs(current - sells))


def gross_exposure(positions: object, pending: object) -> Decimal:
    """Sum absolute position and non-reduce pending notional without netting."""
    amounts: list[Decimal] = []
    for leg in _legs(positions):
        amounts.append(multiply_amount(leg.per_contract_notional, leg.contracts))
    for leg in _legs(pending):
        if not leg.reduce_only:
            amounts.append(multiply_amount(leg.per_contract_notional, leg.contracts))
    return sum_amounts(tuple(amounts))


__all__ = ["gross_exposure", "strict_reduction", "worst_position_contracts"]
