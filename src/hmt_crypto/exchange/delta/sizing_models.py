"""Immutable evidence models and explicit policies for contract sizing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from hmt_crypto.errors import ContractSizingError
from hmt_crypto.exchange.delta.sizing_math import (
    NumericContractError,
    add,
    equal,
    floor_division,
    is_zero,
    multiply_integer,
    nonnegative_result_decimal,
    positive_integer,
    positive_result_decimal,
)


class ContractRounding(StrEnum):
    """The only supported explicit conversion and quantization policies."""

    EXACT = "exact"
    FLOOR = "floor"


class PriceBasis(StrEnum):
    """Explicit ticker price fields; selection never falls back."""

    MARK = "mark"
    BEST_BID = "best_bid"
    BEST_ASK = "best_ask"
    CLOSE = "close"


class SizingUnit(StrEnum):
    """The denomination represented by a contract-conversion result."""

    BASE_QUANTITY = "base_quantity"
    QUOTE_NOTIONAL = "quote_notional"


def _fail(operation: str) -> ContractSizingError:
    return ContractSizingError(context={"operation": operation})


@dataclass(frozen=True, slots=True, init=False)
class ContractConversion:
    """Exact immutable proof of quantity/notional representation in contracts."""

    requested: Decimal
    represented: Decimal
    contracts: int
    remainder: Decimal
    contract_unit: Decimal
    unit: SizingUnit
    rounding: ContractRounding
    price_basis: PriceBasis | None

    def __init__(
        self,
        *,
        requested: object,
        represented: object,
        contracts: object,
        remainder: object,
        contract_unit: object,
        unit: SizingUnit,
        rounding: ContractRounding,
        price_basis: PriceBasis | None,
    ) -> None:
        try:
            checked_requested = positive_result_decimal(requested)
            checked_represented = positive_result_decimal(represented)
            checked_contracts = positive_integer(contracts)
            checked_remainder = nonnegative_result_decimal(remainder)
            checked_contract_unit = positive_result_decimal(contract_unit)
            if type(unit) is not SizingUnit or type(rounding) is not ContractRounding:
                raise NumericContractError
            if unit is SizingUnit.BASE_QUANTITY:
                if price_basis is not None:
                    raise NumericContractError
            elif type(price_basis) is not PriceBasis:
                raise NumericContractError
            expected_represented = multiply_integer(checked_contract_unit, checked_contracts)
            if not equal(checked_represented, expected_represented):
                raise NumericContractError
            if not equal(checked_requested, add(checked_represented, checked_remainder)):
                raise NumericContractError
            if checked_remainder >= checked_contract_unit:
                raise NumericContractError
            if rounding is ContractRounding.EXACT and not is_zero(checked_remainder):
                raise NumericContractError
        except (NumericContractError, TypeError, ValueError):
            raise _fail("contract_conversion_evidence") from None
        object.__setattr__(self, "requested", checked_requested)
        object.__setattr__(self, "represented", checked_represented)
        object.__setattr__(self, "contracts", checked_contracts)
        object.__setattr__(self, "remainder", checked_remainder)
        object.__setattr__(self, "contract_unit", checked_contract_unit)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "rounding", rounding)
        object.__setattr__(self, "price_basis", price_basis)


@dataclass(frozen=True, slots=True, init=False)
class PriceQuantization:
    """Exact immutable proof of price representation on a product tick grid."""

    requested: Decimal
    quantized: Decimal
    remainder: Decimal
    tick_size: Decimal
    rounding: ContractRounding

    def __init__(
        self,
        *,
        requested: object,
        quantized: object,
        remainder: object,
        tick_size: object,
        rounding: ContractRounding,
    ) -> None:
        try:
            checked_requested = positive_result_decimal(requested)
            checked_quantized = positive_result_decimal(quantized)
            checked_remainder = nonnegative_result_decimal(remainder)
            checked_tick = positive_result_decimal(tick_size)
            if type(rounding) is not ContractRounding:
                raise NumericContractError
            if not equal(checked_requested, add(checked_quantized, checked_remainder)):
                raise NumericContractError
            if checked_remainder >= checked_tick:
                raise NumericContractError
            _, _, grid_remainder = floor_division(checked_quantized, checked_tick)
            if not is_zero(grid_remainder):
                raise NumericContractError
            if rounding is ContractRounding.EXACT and not is_zero(checked_remainder):
                raise NumericContractError
        except (NumericContractError, TypeError, ValueError):
            raise _fail("price_quantization_evidence") from None
        object.__setattr__(self, "requested", checked_requested)
        object.__setattr__(self, "quantized", checked_quantized)
        object.__setattr__(self, "remainder", checked_remainder)
        object.__setattr__(self, "tick_size", checked_tick)
        object.__setattr__(self, "rounding", rounding)


__all__ = [
    "ContractConversion",
    "ContractRounding",
    "PriceBasis",
    "PriceQuantization",
    "SizingUnit",
]
