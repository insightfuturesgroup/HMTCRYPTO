"""Delta Exchange transport, catalogue, and sizing primitives."""

from importlib import import_module
from typing import TYPE_CHECKING

from hmt_crypto.exchange.delta.client import DeltaPublicClient, L2TimestampPolicy
from hmt_crypto.exchange.delta.endpoints import (
    DeltaEndpointSet,
    StreamingCapability,
    get_delta_endpoints,
)
from hmt_crypto.exchange.delta.paginator import (
    MISSING_AFTER,
    CursorPage,
    PaginationResult,
    collect_cursor_pages,
)
from hmt_crypto.exchange.delta.signer import DeltaSigner, SignedAuthHeaders

if TYPE_CHECKING:
    from hmt_crypto.exchange.delta.sizing import (
        ContractConversion,
        ContractRounding,
        ContractSizerRegistry,
        LinearContractSizer,
        PriceBasis,
        PriceQuantization,
        SizingUnit,
    )

_SIZING_EXPORTS = frozenset(
    {
        "ContractConversion",
        "ContractRounding",
        "ContractSizerRegistry",
        "LinearContractSizer",
        "PriceBasis",
        "PriceQuantization",
        "SizingUnit",
    }
)


def __getattr__(name: str) -> object:
    """Load sizing exports lazily to preserve config/package import boundaries."""
    if name not in _SIZING_EXPORTS:
        raise AttributeError(name)
    module = import_module("hmt_crypto.exchange.delta.sizing")
    value: object = getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    "ContractConversion",
    "ContractRounding",
    "ContractSizerRegistry",
    "CursorPage",
    "DeltaPublicClient",
    "DeltaEndpointSet",
    "DeltaSigner",
    "MISSING_AFTER",
    "L2TimestampPolicy",
    "LinearContractSizer",
    "PaginationResult",
    "PriceBasis",
    "PriceQuantization",
    "SignedAuthHeaders",
    "StreamingCapability",
    "SizingUnit",
    "collect_cursor_pages",
    "get_delta_endpoints",
]
