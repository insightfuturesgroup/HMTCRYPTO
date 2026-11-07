"""Typed client tests for reviewed Delta India private reads."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef, CollectionMode
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.private_read_client import DeltaIndiaPrivateReadClient
from hmt_crypto.exchange.delta.private_read_runtime import _PrivateEnvelopeResult
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
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

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
_FIXTURES = Path(__file__).parents[1] / "fixtures" / "delta" / "private" / "mutations"


def _fixture_result(group: str, name: str) -> dict[str, object]:
    envelope = json.loads((_FIXTURES / group / name).read_text())
    assert type(envelope) is dict
    result = envelope["result"]
    assert type(result) is dict
    return result


def _account() -> AccountRef:
    return AccountRef(
        account_id=UUID("00000000-0000-0000-0000-000000000901"),
        external_user_id="12345",
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=ExecutionMode.PAPER,
        credential_version=7,
    )


class _ScriptedRuntime:
    def __init__(
        self,
        responses: list[tuple[object, object]],
        *,
        account: AccountRef | None = None,
    ) -> None:
        self.responses = responses
        self.requests: list[object] = []
        self.closed = False
        self._account = account or _account()

    @property
    def account(self) -> AccountRef:
        return self._account.model_copy()

    async def _get_json(self, request: object) -> _PrivateEnvelopeResult:
        self.requests.append(request)
        result, meta = self.responses.pop(0)
        return _PrivateEnvelopeResult(
            result=result,
            meta=meta,
            correlation_id=UUID(int=len(self.requests)),
            requested_at=_NOW,
            received_at=_NOW,
        )

    async def aclose(self) -> None:
        self.closed = True


def _scripted_client(runtime: _ScriptedRuntime) -> DeltaIndiaPrivateReadClient:
    """Build a parser-only harness without weakening the production constructor."""
    client = object.__new__(DeltaIndiaPrivateReadClient)
    client._account = runtime.account  # type: ignore[attr-defined]
    client._runtime = runtime  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_client_collects_strict_order_pages_with_exact_forward_cursor_targets() -> None:
    """Catch dropped cursors, a one-page-only client, or false strict completeness."""
    first = _fixture_result("orders", "offline_candidate_order_success.json")
    second = first | {"id": 7002, "client_order_id": "HMT_FIXTURE_2"}
    runtime = _ScriptedRuntime(
        [
            ([first], {"after": "next:cursor", "before": None}),
            ([second], {"after": None, "before": "previous:cursor"}),
        ]
    )
    client = _scripted_client(runtime)

    result = await client.get_open_orders(OpenOrderQuery(product_ids=(27,), page_size=1))

    assert result.kind == "STRICT"
    assert result.complete is True
    assert result.page_count == 2
    assert tuple(order.id for order in result.items) == (7001, 7002)
    assert result.provenance.page_correlation_ids == (UUID(int=1), UUID(int=2))
    assert result.provenance.mode is CollectionMode.STRICT
    assert runtime.requests[0].query_string == b"?product_ids=27&page_size=1"
    assert runtime.requests[1].query_string == (b"?product_ids=27&after=next%3Acursor&page_size=1")


@pytest.mark.asyncio
async def test_client_normalizes_all_nonpaged_reviewed_reads_and_closes_runtime() -> None:
    """Catch raw result dictionaries escaping any reviewed nonpaged read."""
    balance = {
        "asset_id": 11,
        "asset_symbol": "USDT",
        "available_balance": "100",
        "available_balance_for_robo": "0",
        "balance": "100",
        "blocked_margin": "0",
        "commission": "0",
        "cross_asset_liability": "0",
        "cross_commission": "0",
        "cross_locked_collateral": "0",
        "cross_order_margin": "0",
        "cross_position_margin": "0",
        "id": 12,
        "interest_credit": "0",
        "order_margin": "0",
        "pending_referral_bonus": "0",
        "pending_trading_fee_credit": "0",
        "portfolio_margin": "0",
        "position_margin": "0",
        "trading_fee_credit": "0",
        "unvested_amount": "0",
        "user_id": 12345,
    }
    order = _fixture_result("orders", "offline_candidate_order_success.json")
    position = _fixture_result("position_margin", "offline_candidate_position_success.json")
    leverage = _fixture_result("leverage", "offline_candidate_leverage_success.json")
    runtime = _ScriptedRuntime(
        [
            ([balance], {"net_equity": "100", "robo_trading_equity": "0"}),
            ([position], None),
            ({"size": -2, "entry_price": "123.45"}, None),
            (order, None),
            (order, None),
            (leverage, None),
        ]
    )
    client = _scripted_client(runtime)

    balances = await client.get_balances()
    positions = await client.get_margined_positions(MarginedPositionQuery(product_ids=(27,)))
    latest = await client.get_latest_position(LatestPositionQuery(product_id=27))
    by_id = await client.get_order(OrderByIdQuery(order_id=7001))
    by_client = await client.get_order_by_client_id(
        OrderByClientIdQuery(client_order_id="HMT_FIXTURE_1")
    )
    observed_leverage = await client.get_leverage(LeverageQuery(product_id=27))

    assert balances.value.balances[0].user_id == "12345"
    assert positions.value[0].product_id == 27
    assert latest.value.kind == "FOUND" and latest.value.value.signed_contracts == -2
    assert by_id.value.kind == "FOUND" and by_id.value.value.id == 7001
    assert by_client.value.kind == "FOUND" and by_client.value.value.id == 7001
    assert observed_leverage.value.product_id == 27
    assert tuple(request.route_id for request in runtime.requests) == (
        PrivateRouteId.BALANCES,
        PrivateRouteId.MARGINED_POSITIONS,
        PrivateRouteId.LATEST_POSITION,
        PrivateRouteId.ORDER_BY_ID,
        PrivateRouteId.ORDER_BY_CLIENT_ID,
        PrivateRouteId.GET_LEVERAGE,
    )
    await client.aclose()
    assert runtime.closed is True


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_client_supports_order_history_through_the_same_bounded_paginator() -> None:
    """Catch history silently diverging from active-order cursor handling."""
    order = _fixture_result("orders", "offline_candidate_order_success.json")
    runtime = _ScriptedRuntime([([order], {"after": None, "before": None})])
    client = _scripted_client(runtime)

    result = await client.get_order_history(OrderHistoryQuery(page_size=10))

    assert result.page_count == 1
    assert result.items[0].id == 7001
    assert runtime.requests[0].route_id is PrivateRouteId.ORDER_HISTORY


@pytest.mark.asyncio
async def test_client_dispatches_reviewed_wallet_and_fill_cursor_reads() -> None:
    """Catch either formerly-held endpoint falling back to an offline stub."""
    runtime = _ScriptedRuntime(
        [
            ([], {"after": None, "before": None}),
            ([], {"after": None, "before": None}),
        ]
    )
    client = _scripted_client(runtime)

    transactions = await client.get_wallet_transactions(WalletTransactionQuery(asset_id=11))
    fills = await client.get_fills(FillQuery(product_ids=(27,)))

    assert transactions.complete is True and transactions.items == ()
    assert fills.complete is True and fills.items == ()
    assert tuple(request.route_id for request in runtime.requests) == (
        PrivateRouteId.TRANSACTIONS,
        PrivateRouteId.FILLS,
    )
    assert runtime.requests[0].query_string == b"?asset_ids=11&page_size=10"
    assert runtime.requests[1].query_string == b"?product_ids=27&page_size=10"


@pytest.mark.asyncio
async def test_client_preserves_backward_cursor_direction_across_order_pages() -> None:
    """Catch a requested ``before`` walk being silently switched to forward traversal."""
    first = _fixture_result("orders", "offline_candidate_order_success.json")
    second = first | {"id": 6999, "client_order_id": "HMT_FIXTURE_0"}
    runtime = _ScriptedRuntime(
        [
            ([first], {"after": "newer:cursor", "before": "older:cursor"}),
            ([second], {"after": "newer:cursor-2", "before": None}),
        ]
    )
    client = _scripted_client(runtime)

    result = await client.get_order_history(OrderHistoryQuery(before="initial:cursor", page_size=1))

    assert tuple(order.id for order in result.items) == (7001, 6999)
    assert runtime.requests[0].query_string == b"?before=initial%3Acursor&page_size=1"
    assert runtime.requests[1].query_string == b"?before=older%3Acursor&page_size=1"


def test_client_rejects_non_india_account_before_any_private_dispatch() -> None:
    """Catch a Global account reaching an India-only signed runtime."""
    global_account = _account().model_copy(update={"region": DeltaRegion.GLOBAL})

    with pytest.raises(ConfigurationError):
        DeltaIndiaPrivateReadClient(
            account=global_account,
            runtime=_ScriptedRuntime([], account=global_account),
        )


def test_client_rejects_cross_account_runtime_relabeling() -> None:
    """Catch a valid runtime being presented under a different local account identity."""
    foreign = _account().model_copy(
        update={"account_id": UUID("00000000-0000-0000-0000-000000000902")}
    )
    runtime = _ScriptedRuntime([], account=foreign)

    with pytest.raises(ConfigurationError):
        DeltaIndiaPrivateReadClient(account=_account(), runtime=runtime)

    assert runtime.requests == []


@pytest.mark.asyncio
async def test_client_rejects_counterfeit_collection_mode_before_dispatch() -> None:
    """Catch mode coercion selecting a pagination completeness policy."""
    runtime = _ScriptedRuntime([])
    client = _scripted_client(runtime)

    with pytest.raises(ConfigurationError):
        await client.get_open_orders(  # type: ignore[arg-type]
            OpenOrderQuery(page_size=10),
            mode="STRICT",
        )

    assert runtime.requests == []
