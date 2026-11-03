"""Exact arithmetic adapter for verified linear Delta derivatives."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Final

from hmt_crypto.domain import Ticker, TradingStatus
from hmt_crypto.errors import UnsupportedProductError
from hmt_crypto.exchange.delta.sizing_math import (
    NumericContractError,
    floor_division,
    is_zero,
    multiply,
    multiply_integer,
    positive_decimal,
    positive_integer,
)
from hmt_crypto.exchange.delta.sizing_models import (
    ContractConversion,
    ContractRounding,
    PriceBasis,
    PriceQuantization,
    SizingUnit,
)
from hmt_crypto.exchange.delta.sizing_validation import (
    ResolvedSizingProduct,
    clone_product,
    clone_source,
    sizing_error,
    unsupported,
    utc_datetime,
    validate_supported_product,
)

_PRICE_FIELDS: Final = {
    PriceBasis.MARK: "mark_price",
    PriceBasis.BEST_BID: "best_bid",
    PriceBasis.BEST_ASK: "best_ask",
    PriceBasis.CLOSE: "close",
}


class LinearContractSizer:
    """Exact sizer for the verified linear vanilla futures subset only."""

    __slots__ = (
        "_catalog_content_sha256",
        "_catalog_revision",
        "_contract_value",
        "_product",
        "_risk_increasing",
        "_source",
        "_tick_size",
    )

    def __init__(self, resolved: ResolvedSizingProduct) -> None:
        if type(resolved) is not ResolvedSizingProduct:
            raise sizing_error("linear_contract_sizer")
        try:
            owned = clone_product(resolved.product)
            owned_source = clone_source(resolved.source)
            if type(resolved.catalog_revision) is not int or resolved.catalog_revision <= 0:
                raise ValueError
            content_hash = resolved.catalog_content_sha256
            if (
                type(content_hash) is not str
                or len(content_hash) != 64
                or any(character not in "0123456789abcdef" for character in content_hash)
            ):
                raise ValueError
            if type(resolved.risk_increasing) is not bool:
                raise ValueError
            validate_supported_product(
                owned,
                risk_increasing=resolved.risk_increasing,
            )
            contract_value = positive_decimal(owned.contract_value)
            tick_size = positive_decimal(owned.tick_size)
        except UnsupportedProductError:
            raise
        except (NumericContractError, TypeError, ValueError):
            raise unsupported("linear_contract_sizer") from None
        self._product = owned
        self._source = owned_source
        self._catalog_revision = resolved.catalog_revision
        self._catalog_content_sha256 = content_hash
        self._risk_increasing = resolved.risk_increasing
        self._contract_value = contract_value
        self._tick_size = tick_size

    @property
    def product_id(self) -> int:
        """Return the environment-scoped numeric product ID."""
        return self._product.id

    @property
    def contract_value(self) -> Decimal:
        """Return the exact base-asset quantity represented by one contract."""
        return self._contract_value

    @property
    def tick_size(self) -> Decimal:
        """Return the exact current catalogue tick size."""
        return self._tick_size

    def __repr__(self) -> str:
        return (
            "LinearContractSizer("
            f"region={self._source.region.value!r}, "
            f"environment={self._source.environment.value!r}, "
            f"catalog_revision={self._catalog_revision}, "
            f"catalog_content_sha256={self._catalog_content_sha256!r}, "
            f"product_id={self.product_id}, risk_increasing={self._risk_increasing}, "
            "only_reduce_only_orders_allowed="
            f"{self._product.only_reduce_only_orders_allowed})"
        )

    def audit_evidence(self) -> dict[str, str | int | bool]:
        """Return isolated scalar provenance for logs and persisted decisions."""
        return {
            "region": self._source.region.value,
            "environment": self._source.environment.value,
            "catalog_revision": self._catalog_revision,
            "catalog_content_sha256": self._catalog_content_sha256,
            "product_id": self.product_id,
            "risk_increasing": self._risk_increasing,
            "only_reduce_only_orders_allowed": (self._product.only_reduce_only_orders_allowed),
        }

    def contracts_to_base_quantity(self, contracts: int) -> Decimal:
        """Convert a strict positive integer contract count to base quantity."""
        try:
            return multiply_integer(self._contract_value, positive_integer(contracts))
        except NumericContractError:
            raise sizing_error("contracts_to_base_quantity", self.product_id) from None

    def contracts_to_notional(self, contracts: int, price: object) -> Decimal:
        """Convert contracts and a positive price to exact quote notional."""
        try:
            quantity = multiply_integer(self._contract_value, positive_integer(contracts))
            return multiply(positive_decimal(price), quantity)
        except NumericContractError:
            raise sizing_error("contracts_to_notional", self.product_id) from None

    def quantity_to_contracts(
        self,
        quantity: object,
        *,
        rounding: ContractRounding,
    ) -> ContractConversion:
        """Represent base quantity as contracts under an explicit policy."""
        if type(rounding) is not ContractRounding:
            raise sizing_error("quantity_to_contracts", self.product_id)
        try:
            requested = positive_decimal(quantity)
            contracts, represented, remainder = floor_division(requested, self._contract_value)
            if contracts == 0 or (rounding is ContractRounding.EXACT and not is_zero(remainder)):
                raise NumericContractError
            return ContractConversion(
                requested=requested,
                represented=represented,
                contracts=contracts,
                remainder=remainder,
                contract_unit=self._contract_value,
                unit=SizingUnit.BASE_QUANTITY,
                rounding=rounding,
                price_basis=None,
            )
        except NumericContractError:
            raise sizing_error("quantity_to_contracts", self.product_id) from None

    def notional_to_contracts(
        self,
        notional: object,
        *,
        price: object,
        price_basis: PriceBasis,
        rounding: ContractRounding,
    ) -> ContractConversion:
        """Represent quote notional as contracts under an explicit price basis."""
        if type(rounding) is not ContractRounding or type(price_basis) is not PriceBasis:
            raise sizing_error("notional_to_contracts", self.product_id)
        try:
            requested = positive_decimal(notional)
            contract_unit = multiply(positive_decimal(price), self._contract_value)
            contracts, represented, remainder = floor_division(requested, contract_unit)
            if contracts == 0 or (rounding is ContractRounding.EXACT and not is_zero(remainder)):
                raise NumericContractError
            return ContractConversion(
                requested=requested,
                represented=represented,
                contracts=contracts,
                remainder=remainder,
                contract_unit=contract_unit,
                unit=SizingUnit.QUOTE_NOTIONAL,
                rounding=rounding,
                price_basis=price_basis,
            )
        except NumericContractError:
            raise sizing_error("notional_to_contracts", self.product_id) from None

    def quantize_price(
        self,
        price: object,
        *,
        rounding: ContractRounding,
    ) -> PriceQuantization:
        """Represent a positive price on the tick grid; this is not side policy."""
        if type(rounding) is not ContractRounding:
            raise sizing_error("quantize_price", self.product_id)
        try:
            requested = positive_decimal(price)
            ticks, quantized, remainder = floor_division(requested, self._tick_size)
            if ticks == 0 or (rounding is ContractRounding.EXACT and not is_zero(remainder)):
                raise NumericContractError
            return PriceQuantization(
                requested=requested,
                quantized=quantized,
                remainder=remainder,
                tick_size=self._tick_size,
                rounding=rounding,
            )
        except NumericContractError:
            raise sizing_error("quantize_price", self.product_id) from None

    def select_fresh_price(
        self,
        ticker: Ticker,
        *,
        basis: PriceBasis,
        checked_at: object,
        max_age: timedelta,
    ) -> Decimal:
        """Select one explicit fresh ticker price with no cross-field fallback."""
        if type(ticker) is not Ticker or type(basis) is not PriceBasis:
            raise sizing_error("select_fresh_price", self.product_id)
        if type(max_age) is not timedelta or max_age <= timedelta(0):
            raise sizing_error("select_fresh_price", self.product_id)
        try:
            checked = utc_datetime(checked_at)
            observed = utc_datetime(ticker.timestamp)
            if (
                type(ticker.product_id) is not int
                or ticker.product_id != self.product_id
                or type(ticker.symbol) is not str
                or ticker.symbol != self._product.symbol
            ):
                raise ValueError
            status = ticker.product_trading_status
            if type(status) is not TradingStatus or status not in {
                TradingStatus.UNKNOWN,
                TradingStatus.OPERATIONAL,
            }:
                raise ValueError
            if observed > checked or checked - observed > max_age:
                raise ValueError
            return positive_decimal(getattr(ticker, _PRICE_FIELDS[basis]))
        except (AttributeError, KeyError, NumericContractError, TypeError, ValueError):
            raise sizing_error("select_fresh_price", self.product_id) from None


__all__ = ["LinearContractSizer"]
