"""Typed Delta India client for the closed set of reviewed private reads."""

from __future__ import annotations

from typing import Literal, TypeAlias, cast, overload

from hmt_crypto.config import DeltaRegion
from hmt_crypto.domain import private_orders as orders
from hmt_crypto.domain.account import (
    AccountRef,
    CollectionMode,
    ObservationContext,
    PrivatePaginationProvenance,
    TruncationReason,
)
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
from hmt_crypto.domain.private_wallet import ObservedBalanceSnapshot, WalletTransaction
from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError, PaginationLimitError
from hmt_crypto.exchange.delta.private_read_fill_parsers import _parse_fill_page_payload
from hmt_crypto.exchange.delta.private_read_pagination import _collect_private_pages
from hmt_crypto.exchange.delta.private_read_parsers import (
    _parse_balance_snapshot_payload,
    _parse_latest_position_payload,
    _parse_leverage_read_payload,
    _parse_margined_position_list_payload,
    _parse_order_lookup_payload,
    _parse_order_page_payload,
    _parse_wallet_transaction_page_payload,
)
from hmt_crypto.exchange.delta.private_read_runtime import (
    _DeltaPrivateReadRuntime,
    _PrivateEnvelopeResult,
)
from hmt_crypto.exchange.delta.private_read_wire import (
    _freeze_balances_private_read,
    _freeze_private_read,
    _FrozenPrivateReadRequestV1,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId, get_private_route_spec
from hmt_crypto.exchange.delta.rate_limit_policy import POLICY_VERSION
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

_PrivateReadQuery: TypeAlias = (
    WalletTransactionQuery
    | MarginedPositionQuery
    | LatestPositionQuery
    | OpenOrderQuery
    | OrderHistoryQuery
    | FillQuery
    | OrderByIdQuery
    | OrderByClientIdQuery
    | LeverageQuery
)


def _same_account(left: AccountRef, right: AccountRef) -> bool:
    return (
        left.account_id == right.account_id
        and left.external_user_id == right.external_user_id
        and left.region is right.region
        and left.environment is right.environment
        and left.mode is right.mode
        and left.credential_version == right.credential_version
    )


class DeltaIndiaPrivateReadClient:
    """Concrete exchange-neutral protocol implementation for reviewed India reads."""

    def __init__(self, *, account: AccountRef, runtime: _DeltaPrivateReadRuntime) -> None:
        if type(account) is not AccountRef or type(runtime) is not _DeltaPrivateReadRuntime:
            raise ConfigurationError(context={"operation": "private_read_client"})
        runtime_account_value = runtime.account
        if type(runtime_account_value) is not AccountRef:
            raise ConfigurationError(context={"operation": "private_read_client"})
        self._account = AccountRef.model_validate(account).model_copy()
        runtime_account = AccountRef.model_validate(runtime_account_value).model_copy()
        if self._account.region is not DeltaRegion.INDIA or not _same_account(
            self._account, runtime_account
        ):
            raise ConfigurationError(context={"operation": "private_read_client"})
        self._runtime = runtime

    def _freeze(self, query: _PrivateReadQuery) -> _FrozenPrivateReadRequestV1:
        return _freeze_private_read(
            query,
            region=self._account.region,
            environment=self._account.environment,
        )

    def _context(
        self,
        envelope: _PrivateEnvelopeResult,
        endpoint_id: PrivateRouteId,
    ) -> ObservationContext:
        return ObservationContext(
            account=self._account,
            endpoint_id=endpoint_id,
            correlation_id=envelope.correlation_id,
            requested_at=envelope.requested_at,
            received_at=envelope.received_at,
            source="delta_rest",
        )

    async def get_balances(self) -> ObservedBalanceSnapshot:
        request = _freeze_balances_private_read(
            region=self._account.region,
            environment=self._account.environment,
        )
        envelope = await self._runtime._get_json(request)
        value = _parse_balance_snapshot_payload(
            envelope.result,
            envelope.meta,
            expected_user_id=self._account.external_user_id,
        )
        return ObservedBalanceSnapshot(
            context=self._context(envelope, PrivateRouteId.BALANCES),
            value=value,
        )

    async def get_margined_positions(
        self,
        query: MarginedPositionQuery,
    ) -> ObservedMarginedPositions:
        owned = MarginedPositionQuery.model_validate(query)
        envelope = await self._runtime._get_json(self._freeze(owned))
        value = _parse_margined_position_list_payload(
            envelope.result,
            expected_user_id=self._account.external_user_id,
            allowed_product_ids=owned.product_ids or None,
        )
        return ObservedMarginedPositions(
            context=self._context(envelope, PrivateRouteId.MARGINED_POSITIONS),
            value=value,
        )

    async def get_latest_position(
        self,
        query: LatestPositionQuery,
    ) -> ObservedLatestPositionLookupResult:
        owned = LatestPositionQuery.model_validate(query)
        envelope = await self._runtime._get_json(self._freeze(owned))
        position = _parse_latest_position_payload(
            envelope.result,
            expected_product_id=owned.product_id,
        )
        found = orders.LatestPositionLookupFound(kind="FOUND", value=position)
        return ObservedLatestPositionLookupResult(
            context=self._context(envelope, PrivateRouteId.LATEST_POSITION),
            value=cast(orders.LatestPositionLookupResult, found),
        )

    async def get_order(self, query: OrderByIdQuery) -> ObservedOrderLookupResult:
        owned = OrderByIdQuery.model_validate(query)
        envelope = await self._runtime._get_json(self._freeze(owned))
        order = _parse_order_lookup_payload(
            envelope.result,
            endpoint_id=PrivateRouteId.ORDER_BY_ID,
            expected_user_id=self._account.external_user_id,
            expected_order_id=owned.order_id,
        )
        found = orders.OrderLookupFound(kind="FOUND", value=order)
        return ObservedOrderLookupResult(
            context=self._context(envelope, PrivateRouteId.ORDER_BY_ID),
            value=cast(orders.OrderLookupResult, found),
        )

    async def get_order_by_client_id(
        self,
        query: OrderByClientIdQuery,
    ) -> ObservedOrderLookupResult:
        owned = OrderByClientIdQuery.model_validate(query)
        envelope = await self._runtime._get_json(self._freeze(owned))
        order = _parse_order_lookup_payload(
            envelope.result,
            endpoint_id=PrivateRouteId.ORDER_BY_CLIENT_ID,
            expected_user_id=self._account.external_user_id,
            expected_client_order_id=owned.client_order_id,
        )
        found = orders.OrderLookupFound(kind="FOUND", value=order)
        return ObservedOrderLookupResult(
            context=self._context(envelope, PrivateRouteId.ORDER_BY_CLIENT_ID),
            value=cast(orders.OrderLookupResult, found),
        )

    async def get_leverage(self, query: LeverageQuery) -> ObservedLeverage:
        owned = LeverageQuery.model_validate(query)
        envelope = await self._runtime._get_json(self._freeze(owned))
        leverage = _parse_leverage_read_payload(
            envelope.result,
            expected_product_id=owned.product_id,
        )
        return ObservedLeverage(
            context=self._context(envelope, PrivateRouteId.GET_LEVERAGE),
            value=leverage,
        )

    @staticmethod
    def _next_order_query(
        query: OpenOrderQuery | OrderHistoryQuery,
        cursor: str,
        *,
        backward: bool,
    ) -> OpenOrderQuery | OrderHistoryQuery:
        values = query.model_dump()
        values["after"] = None if backward else cursor
        values["before"] = cursor if backward else None
        if type(query) is OpenOrderQuery:
            return OpenOrderQuery.model_validate(values)
        return OrderHistoryQuery.model_validate(values)

    async def _get_order_pages(
        self,
        query: OpenOrderQuery | OrderHistoryQuery,
        *,
        route_id: PrivateRouteId,
        mode: CollectionMode,
    ) -> StrictOrderPaginationResult | DiagnosticOrderPaginationResult:
        if type(mode) is not CollectionMode:
            raise ConfigurationError(context={"operation": route_id.value})
        spec = get_private_route_spec(route_id)
        if spec.max_pages is None or spec.max_page_size is None:
            raise ConfigurationError(context={"operation": route_id.value})
        current = query
        backward = query.before is not None
        initial_cursor = query.before if backward else query.after
        seen = {initial_cursor} if initial_cursor is not None else set()
        items: list[orders.Order] = []
        envelopes: list[_PrivateEnvelopeResult] = []
        reason: TruncationReason | None = None
        complete = False

        while True:
            envelope = await self._runtime._get_json(self._freeze(current))
            envelopes.append(envelope)
            page_items, after, before = _parse_order_page_payload(
                envelope.result,
                envelope.meta,
                endpoint_id=route_id,
                expected_user_id=self._account.external_user_id,
                allowed_product_ids=current.product_ids or None,
                page_size=current.page_size,
            )
            next_cursor = before if backward else after
            remaining = spec.max_items - len(items)
            if len(page_items) > remaining:
                if mode is CollectionMode.STRICT:
                    raise PaginationLimitError(context={"operation": route_id.value})
                items.extend(page_items[:remaining])
                reason = TruncationReason.ITEM_LIMIT
                break
            items.extend(page_items)
            if next_cursor is None:
                complete = True
                break
            if next_cursor in seen:
                raise ExchangeProtocolError(context={"operation": route_id.value})
            seen.add(next_cursor)
            if len(items) == spec.max_items:
                if mode is CollectionMode.STRICT:
                    raise PaginationLimitError(context={"operation": route_id.value})
                reason = TruncationReason.ITEM_LIMIT
                break
            if len(envelopes) == spec.max_pages:
                if mode is CollectionMode.STRICT:
                    raise PaginationLimitError(context={"operation": route_id.value})
                reason = TruncationReason.PAGE_LIMIT
                break
            current = self._next_order_query(current, next_cursor, backward=backward)

        first, last = envelopes[0], envelopes[-1]
        provenance = PrivatePaginationProvenance(
            account=self._account,
            endpoint_id=route_id,
            first_requested_at=first.requested_at,
            last_received_at=last.received_at,
            page_correlation_ids=tuple(item.correlation_id for item in envelopes),
            credential_version=self._account.credential_version,
            policy_version=POLICY_VERSION,
            mode=mode,
        )
        if mode is CollectionMode.STRICT:
            return StrictOrderPaginationResult(
                kind="STRICT",
                items=tuple(items),
                complete=True,
                page_count=len(envelopes),
                provenance=provenance,
            )
        return DiagnosticOrderPaginationResult(
            kind="DIAGNOSTIC",
            items=tuple(items),
            complete=complete,
            truncation_reason=reason,
            page_count=len(envelopes),
            provenance=provenance,
        )

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

    async def get_open_orders(
        self,
        query: OpenOrderQuery,
        *,
        mode: CollectionMode = CollectionMode.STRICT,
    ) -> StrictOrderPaginationResult | DiagnosticOrderPaginationResult:
        owned = OpenOrderQuery.model_validate(query)
        return await self._get_order_pages(
            owned,
            route_id=PrivateRouteId.OPEN_ORDERS,
            mode=mode,
        )

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

    async def get_order_history(
        self,
        query: OrderHistoryQuery,
        *,
        mode: CollectionMode = CollectionMode.STRICT,
    ) -> StrictOrderPaginationResult | DiagnosticOrderPaginationResult:
        owned = OrderHistoryQuery.model_validate(query)
        return await self._get_order_pages(
            owned,
            route_id=PrivateRouteId.ORDER_HISTORY,
            mode=mode,
        )

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

    async def get_wallet_transactions(
        self,
        query: WalletTransactionQuery,
        *,
        mode: CollectionMode = CollectionMode.STRICT,
    ) -> StrictWalletTransactionPaginationResult | DiagnosticWalletTransactionPaginationResult:
        owned = WalletTransactionQuery.model_validate(query)

        def parse_page(
            envelope: _PrivateEnvelopeResult,
            current: WalletTransactionQuery,
        ) -> tuple[tuple[WalletTransaction, ...], str | None, str | None]:
            return _parse_wallet_transaction_page_payload(
                envelope.result,
                envelope.meta,
                allowed_asset_id=current.asset_id,
                allowed_transaction_types=(
                    tuple(item.value for item in current.transaction_types)
                    if current.transaction_types
                    else None
                ),
                page_size=current.page_size,
            )

        def next_query(
            current: WalletTransactionQuery,
            cursor: str,
            backward: bool,
        ) -> WalletTransactionQuery:
            values = current.model_dump()
            values["after"] = None if backward else cursor
            values["before"] = cursor if backward else None
            return WalletTransactionQuery.model_validate(values)

        collected = await _collect_private_pages(
            owned,
            account=self._account,
            route_id=PrivateRouteId.TRANSACTIONS,
            mode=mode,
            initial_after=owned.after,
            initial_before=owned.before,
            freeze=self._freeze,
            get_json=self._runtime._get_json,
            parse_page=parse_page,
            next_query=next_query,
        )
        if mode is CollectionMode.STRICT:
            return StrictWalletTransactionPaginationResult(
                kind="STRICT",
                items=collected.items,
                complete=True,
                page_count=collected.page_count,
                provenance=collected.provenance,
            )
        return DiagnosticWalletTransactionPaginationResult(
            kind="DIAGNOSTIC",
            items=collected.items,
            complete=collected.complete,
            truncation_reason=collected.truncation_reason,
            page_count=collected.page_count,
            provenance=collected.provenance,
        )

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

    async def get_fills(
        self,
        query: FillQuery,
        *,
        mode: CollectionMode = CollectionMode.STRICT,
    ) -> StrictFillPaginationResult | DiagnosticFillPaginationResult:
        owned = FillQuery.model_validate(query)

        def parse_page(
            envelope: _PrivateEnvelopeResult,
            current: FillQuery,
        ) -> tuple[tuple[orders.Fill, ...], str | None, str | None]:
            return _parse_fill_page_payload(
                envelope.result,
                envelope.meta,
                allowed_product_ids=current.product_ids or None,
                allowed_contract_types=(
                    tuple(item.value for item in current.contract_types)
                    if current.contract_types
                    else None
                ),
                page_size=current.page_size,
            )

        def next_query(current: FillQuery, cursor: str, backward: bool) -> FillQuery:
            values = current.model_dump()
            values["after"] = None if backward else cursor
            values["before"] = cursor if backward else None
            return FillQuery.model_validate(values)

        collected = await _collect_private_pages(
            owned,
            account=self._account,
            route_id=PrivateRouteId.FILLS,
            mode=mode,
            initial_after=owned.after,
            initial_before=owned.before,
            freeze=self._freeze,
            get_json=self._runtime._get_json,
            parse_page=parse_page,
            next_query=next_query,
        )
        if mode is CollectionMode.STRICT:
            return StrictFillPaginationResult(
                kind="STRICT",
                items=collected.items,
                complete=True,
                page_count=collected.page_count,
                provenance=collected.provenance,
            )
        return DiagnosticFillPaginationResult(
            kind="DIAGNOSTIC",
            items=collected.items,
            complete=collected.complete,
            truncation_reason=collected.truncation_reason,
            page_count=collected.page_count,
            provenance=collected.provenance,
        )

    async def aclose(self) -> None:
        """Close the owned signed runtime idempotently."""
        await self._runtime.aclose()


__all__ = ["DeltaIndiaPrivateReadClient"]
