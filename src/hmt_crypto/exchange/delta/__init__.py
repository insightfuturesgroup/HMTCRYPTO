"""Delta Exchange transport, catalogue, and sizing primitives."""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    from hmt_crypto.exchange.delta.sizing import (
        ContractConversion,
        ContractRounding,
        ContractSizerRegistry,
        LinearContractSizer,
        PriceBasis,
        PriceQuantization,
        SizingUnit,
        SourceBoundTicker,
    )

_LAZY_EXPORTS = (
    (
        "ContractConversion",
        "hmt_crypto.exchange.delta.sizing",
        "ContractConversion",
    ),
    ("ContractRounding", "hmt_crypto.exchange.delta.sizing", "ContractRounding"),
    (
        "ContractSizerRegistry",
        "hmt_crypto.exchange.delta.sizing",
        "ContractSizerRegistry",
    ),
    ("CursorPage", "hmt_crypto.exchange.delta.paginator", "CursorPage"),
    ("DeltaPublicClient", "hmt_crypto.exchange.delta.client", "DeltaPublicClient"),
    ("DeltaEndpointSet", "hmt_crypto.exchange.delta.endpoints", "DeltaEndpointSet"),
    ("DeltaSigner", "hmt_crypto.exchange.delta.signer", "DeltaSigner"),
    ("MISSING_AFTER", "hmt_crypto.exchange.delta.paginator", "MISSING_AFTER"),
    ("L2TimestampPolicy", "hmt_crypto.exchange.delta.client", "L2TimestampPolicy"),
    (
        "LinearContractSizer",
        "hmt_crypto.exchange.delta.sizing",
        "LinearContractSizer",
    ),
    ("PaginationResult", "hmt_crypto.exchange.delta.paginator", "PaginationResult"),
    ("PriceBasis", "hmt_crypto.exchange.delta.sizing", "PriceBasis"),
    ("PriceQuantization", "hmt_crypto.exchange.delta.sizing", "PriceQuantization"),
    ("SignedAuthHeaders", "hmt_crypto.exchange.delta.signer", "SignedAuthHeaders"),
    (
        "StreamingCapability",
        "hmt_crypto.exchange.delta.endpoints",
        "StreamingCapability",
    ),
    ("SourceBoundTicker", "hmt_crypto.exchange.delta.sizing", "SourceBoundTicker"),
    ("SizingUnit", "hmt_crypto.exchange.delta.sizing", "SizingUnit"),
    (
        "collect_cursor_pages",
        "hmt_crypto.exchange.delta.paginator",
        "collect_cursor_pages",
    ),
    (
        "get_delta_endpoints",
        "hmt_crypto.exchange.delta.endpoints",
        "get_delta_endpoints",
    ),
)


def __getattr__(name: str) -> object:
    """Resolve one exact approved Delta package export lazily."""
    if type(name) is not str:
        raise AttributeError(
            "unknown Delta package attribute",
            name="unknown Delta package attribute",
            obj=None,
        )
    for public_name, module_name, attribute_name in _LAZY_EXPORTS:
        if name == public_name:
            module = import_module(module_name)
            value: object = getattr(module, attribute_name)
            globals()[public_name] = value
            return value
    raise AttributeError(
        "unknown Delta package attribute",
        name="unknown Delta package attribute",
        obj=None,
    )


def __dir__() -> list[str]:
    """Return a fresh list containing only the approved public surface."""
    return [public_name for public_name, _module, _attribute in _LAZY_EXPORTS]


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
    "SourceBoundTicker",
    "SizingUnit",
    "collect_cursor_pages",
    "get_delta_endpoints",
]
