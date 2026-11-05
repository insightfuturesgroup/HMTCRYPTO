"""Exchange-neutral integration boundaries."""

from importlib import import_module
from typing import TYPE_CHECKING

from hmt_crypto.exchange.base import (
    CandleRequest,
    CandleResolution,
    CandleResult,
    MarketDataClient,
    OrderBookLevel,
    OrderBookRequest,
    OrderBookSnapshot,
    ProductQuery,
    ProductRef,
)

if TYPE_CHECKING:
    from hmt_crypto.exchange.private import PrivateReadClient as PrivateReadClient
    from hmt_crypto.exchange.private_requests import (
        FillQuery as FillQuery,
    )
    from hmt_crypto.exchange.private_requests import (
        LatestPositionQuery as LatestPositionQuery,
    )
    from hmt_crypto.exchange.private_requests import (
        LeverageQuery as LeverageQuery,
    )
    from hmt_crypto.exchange.private_requests import (
        MarginedPositionContractTypeFilter as MarginedPositionContractTypeFilter,
    )
    from hmt_crypto.exchange.private_requests import (
        MarginedPositionQuery as MarginedPositionQuery,
    )
    from hmt_crypto.exchange.private_requests import (
        OpenOrderQuery as OpenOrderQuery,
    )
    from hmt_crypto.exchange.private_requests import (
        OpenOrderStateFilter as OpenOrderStateFilter,
    )
    from hmt_crypto.exchange.private_requests import (
        OrderByClientIdQuery as OrderByClientIdQuery,
    )
    from hmt_crypto.exchange.private_requests import (
        OrderByIdQuery as OrderByIdQuery,
    )
    from hmt_crypto.exchange.private_requests import (
        OrderHistoryQuery as OrderHistoryQuery,
    )
    from hmt_crypto.exchange.private_requests import (
        OrderTypeFilter as OrderTypeFilter,
    )
    from hmt_crypto.exchange.private_requests import (
        PrivateContractTypeFilter as PrivateContractTypeFilter,
    )
    from hmt_crypto.exchange.private_requests import (
        WalletTransactionQuery as WalletTransactionQuery,
    )
else:
    _PRIVATE_EXPORTS = (
        ("PrivateReadClient", "hmt_crypto.exchange.private"),
        *(
            (name, "hmt_crypto.exchange.private_requests")
            for name in (
                "PrivateContractTypeFilter",
                "MarginedPositionContractTypeFilter",
                "OpenOrderStateFilter",
                "OrderTypeFilter",
                "WalletTransactionQuery",
                "MarginedPositionQuery",
                "LatestPositionQuery",
                "OpenOrderQuery",
                "OrderHistoryQuery",
                "FillQuery",
                "OrderByIdQuery",
                "OrderByClientIdQuery",
                "LeverageQuery",
            )
        ),
    )

    def __getattr__(name: str) -> object:
        """Resolve one exact approved private-exchange package export lazily."""
        if type(name) is str:
            for public_name, module_name in _PRIVATE_EXPORTS:
                if name == public_name:
                    value: object = getattr(import_module(module_name), public_name)
                    globals()[public_name] = value
                    return value
        raise AttributeError(
            "unknown exchange package attribute",
            name="unknown exchange package attribute",
            obj=None,
        )

    def __dir__() -> list[str]:
        """Return existing globals plus approved lazy names without resolving them."""
        return sorted({*globals(), *__all__})


__all__ = [
    "CandleRequest",
    "CandleResolution",
    "CandleResult",
    "MarketDataClient",
    "OrderBookLevel",
    "OrderBookRequest",
    "OrderBookSnapshot",
    "ProductQuery",
    "ProductRef",
    "PrivateReadClient",
    "PrivateContractTypeFilter",
    "MarginedPositionContractTypeFilter",
    "OpenOrderStateFilter",
    "OrderTypeFilter",
    "WalletTransactionQuery",
    "MarginedPositionQuery",
    "LatestPositionQuery",
    "OpenOrderQuery",
    "OrderHistoryQuery",
    "FillQuery",
    "OrderByIdQuery",
    "OrderByClientIdQuery",
    "LeverageQuery",
]
