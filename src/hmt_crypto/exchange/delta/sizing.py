"""Public verified linear Delta contract-sizing API."""

from __future__ import annotations

from datetime import datetime

from hmt_crypto.exchange.delta.catalog import CatalogSnapshot, CatalogSource
from hmt_crypto.exchange.delta.sizing_linear import LinearContractSizer
from hmt_crypto.exchange.delta.sizing_models import (
    ContractConversion,
    ContractRounding,
    PriceBasis,
    PriceQuantization,
    SizingUnit,
)
from hmt_crypto.exchange.delta.sizing_validation import (
    clone_source,
    resolve_sizing_product,
    sizing_error,
    utc_datetime,
)


class ContractSizerRegistry:
    """Resolve product-specific sizing adapters for one exact catalogue source."""

    __slots__ = ("_source",)

    def __init__(self, *, source: CatalogSource) -> None:
        try:
            self._source = clone_source(source)
        except (TypeError, ValueError):
            raise sizing_error("contract_sizer_registry") from None

    def source_evidence(self) -> dict[str, str]:
        """Return isolated scalar source evidence safe for audit logs."""
        return {
            "region": self._source.region.value,
            "environment": self._source.environment.value,
        }

    def resolve(
        self,
        *,
        snapshot: CatalogSnapshot,
        product_id: int,
        checked_at: datetime,
        risk_increasing: bool,
    ) -> LinearContractSizer:
        """Resolve a verified linear sizer from a fresh source-bound snapshot."""
        if type(product_id) is not int or product_id <= 0:
            raise sizing_error("contract_sizer_resolve")
        if type(risk_increasing) is not bool:
            raise sizing_error("contract_sizer_resolve", product_id)
        try:
            checked = utc_datetime(checked_at)
        except (TypeError, ValueError):
            raise sizing_error("contract_sizer_resolve", product_id) from None
        resolved = resolve_sizing_product(
            snapshot=snapshot,
            expected_source=self._source,
            product_id=product_id,
            checked_at=checked,
            risk_increasing=risk_increasing,
        )
        return LinearContractSizer(resolved)


__all__ = [
    "ContractConversion",
    "ContractRounding",
    "ContractSizerRegistry",
    "LinearContractSizer",
    "PriceBasis",
    "PriceQuantization",
    "SizingUnit",
]
