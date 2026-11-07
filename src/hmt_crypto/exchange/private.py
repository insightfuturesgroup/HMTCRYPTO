"""Exchange-neutral, read-only private account client protocol."""

from __future__ import annotations

from typing import Literal, Protocol, overload

from hmt_crypto.domain.account import CollectionMode
from hmt_crypto.domain.private_orders import (
    DiagnosticFillPaginationResult,
    DiagnosticOrderPaginationResult,
    DiagnosticWalletTransactionPaginationResult,
    ObservedLatestPositionLookupResult,
    ObservedLeverage,
    ObservedOrderLookupResult,
    StrictFillPaginationResult,
    StrictOrderPaginationResult,
    StrictWalletTransactionPaginationResult,
)
from hmt_crypto.domain.private_positions import ObservedMarginedPositions
from hmt_crypto.domain.private_wallet import ObservedBalanceSnapshot
from hmt_crypto.exchange.private_requests import (
    FillQuery,
    LatestPositionQuery,
    LeverageQuery,
    MarginedPositionQuery,
    OpenOrderQuery,
    OrderByClientIdQuery,
    OrderByIdQuery,
    OrderHistoryQuery,
    WalletTransactionQuery,
)


class PrivateReadClient(Protocol):
    """Structural boundary for exact private reads and lifecycle closure."""

    async def get_balances(self) -> ObservedBalanceSnapshot: ...

    @overload
    async def get_wallet_transactions(
        self,
        query: WalletTransactionQuery,
        *,
        mode: Literal[CollectionMode.STRICT] = CollectionMode.STRICT,
    ) -> StrictWalletTransactionPaginationResult: ...

    @overload
    async def get_wallet_transactions(
        self,
        query: WalletTransactionQuery,
        *,
        mode: Literal[CollectionMode.DIAGNOSTIC],
    ) -> DiagnosticWalletTransactionPaginationResult: ...

    async def get_latest_position(
        self, query: LatestPositionQuery
    ) -> ObservedLatestPositionLookupResult: ...

    async def get_margined_positions(
        self, query: MarginedPositionQuery
    ) -> ObservedMarginedPositions: ...

    @overload
    async def get_open_orders(
        self,
        query: OpenOrderQuery,
        *,
        mode: Literal[CollectionMode.STRICT] = CollectionMode.STRICT,
    ) -> StrictOrderPaginationResult: ...

    @overload
    async def get_open_orders(
        self,
        query: OpenOrderQuery,
        *,
        mode: Literal[CollectionMode.DIAGNOSTIC],
    ) -> DiagnosticOrderPaginationResult: ...

    @overload
    async def get_order_history(
        self,
        query: OrderHistoryQuery,
        *,
        mode: Literal[CollectionMode.STRICT] = CollectionMode.STRICT,
    ) -> StrictOrderPaginationResult: ...

    @overload
    async def get_order_history(
        self,
        query: OrderHistoryQuery,
        *,
        mode: Literal[CollectionMode.DIAGNOSTIC],
    ) -> DiagnosticOrderPaginationResult: ...

    @overload
    async def get_fills(
        self,
        query: FillQuery,
        *,
        mode: Literal[CollectionMode.STRICT] = CollectionMode.STRICT,
    ) -> StrictFillPaginationResult: ...

    @overload
    async def get_fills(
        self,
        query: FillQuery,
        *,
        mode: Literal[CollectionMode.DIAGNOSTIC],
    ) -> DiagnosticFillPaginationResult: ...

    async def get_order(self, query: OrderByIdQuery) -> ObservedOrderLookupResult: ...

    async def get_order_by_client_id(
        self, query: OrderByClientIdQuery
    ) -> ObservedOrderLookupResult: ...

    async def get_leverage(self, query: LeverageQuery) -> ObservedLeverage: ...

    async def aclose(self) -> None: ...


__all__ = ["PrivateReadClient"]
