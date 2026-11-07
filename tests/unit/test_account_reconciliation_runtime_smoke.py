"""Fast offline smoke checks for the durable reconciliation runtime."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode, Settings
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.exchange.delta.private_read_client import DeltaIndiaPrivateReadClient
from hmt_crypto.exchange.delta.private_read_runtime import _PrivateEnvelopeResult
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.private_requests import FillQuery, OrderHistoryQuery
from hmt_crypto.persistence.db import Base, Database
from hmt_crypto.persistence.identity_models import Account, CredentialVersion
from hmt_crypto.reconciliation.checkpoint_models import AccountReconciliationCheckpoint
from hmt_crypto.reconciliation.contracts import (
    LocalAccountProjection,
    LocalOrderTruth,
    UncertainResolution,
)
from hmt_crypto.reconciliation.runtime import AccountReconciliationService, _contains_identity

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
_ORDER_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "delta"
    / "private"
    / "mutations"
    / "orders"
    / "offline_candidate_order_success.json"
)
_POSITION_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "delta"
    / "private"
    / "mutations"
    / "position_margin"
    / "offline_candidate_position_success.json"
)


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
    def __init__(self, responses: list[tuple[object, object]]) -> None:
        self._account = _account()
        self.responses = responses
        self.requests: list[object] = []

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


def _client(runtime: _ScriptedRuntime) -> DeltaIndiaPrivateReadClient:
    client = object.__new__(DeltaIndiaPrivateReadClient)
    client._account = runtime.account  # type: ignore[attr-defined]
    client._runtime = runtime  # type: ignore[attr-defined]
    return client


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    # Import the existing schema aggregate so every FK target is registered.
    import hmt_crypto.persistence.models  # noqa: F401

    database = Database(
        Settings(_env_file=None, DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path}/recon.db")
    )
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with database.transaction() as session:
            owner = Account(
                id=_account().account_id,
                region="india",
                environment="testnet",
                mode="PAPER",
                profile_name="reconciliation-smoke",
                external_user_id="12345",
                external_account_id=None,
                identity_verified_at=_NOW,
                current_credential_version=None,
                version=1,
                created_at=_NOW,
                updated_at=_NOW,
            )
            session.add(owner)
            await session.flush()
            session.add(
                CredentialVersion(
                    account_id=owner.id,
                    region=owner.region,
                    environment=owner.environment,
                    mode=owner.mode,
                    credential_version=7,
                    credential_slot="DELTA_INDIA_TESTNET",
                    fingerprint_hint="a" * 12,
                    observed_external_user_id="12345",
                    observed_external_account_id=None,
                    binding_ref=None,
                    probe_ref=None,
                    reconciliation_ref=None,
                    recorded_at=_NOW,
                )
            )
            await session.flush()
            await session.execute(
                update(Account)
                .where(Account.id == owner.id)
                .values(current_credential_version=7, updated_at=_NOW)
            )
        yield database
    finally:
        await database.aclose()


def _balance() -> dict[str, object]:
    return {
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


def _clean_responses() -> list[tuple[object, object]]:
    cursor = {"after": None, "before": None}
    return [
        ([_balance()], {"net_equity": "100", "robo_trading_equity": "0"}),
        ([], None),
        ([], cursor),
        ([], cursor),
        ([], cursor),
        ([_balance()], {"net_equity": "100", "robo_trading_equity": "0"}),
        ([], None),
        ([], cursor),
        ({"size": 0, "entry_price": "0"}, None),
    ]


@pytest.mark.asyncio
async def test_identity_search_never_accepts_an_exchange_id_substring() -> None:
    """Catch order 700 being treated as accepted because unrelated order 7001 exists."""
    envelope = json.loads(_ORDER_FIXTURE.read_text())
    runtime = _ScriptedRuntime([([envelope["result"]], {"after": None, "before": None})])
    history = await _client(runtime).get_order_history(OrderHistoryQuery(page_size=10))
    intent = LocalOrderTruth(
        local_order_id=UUID("00000000-0000-0000-0000-000000000777"),
        product_id=27,
        client_order_id=None,
        exchange_order_id=700,
        uncertain_attempt_ids=(UUID("00000000-0000-0000-0000-000000000776"),),
    )

    assert _contains_identity(tuple(history.items), intent) is False


@pytest.mark.asyncio
async def test_startup_checkpoint_is_fail_closed_durable_and_read_free_on_replay(
    database: Database,
) -> None:
    """Catch non-durable readiness, partial private reads, or duplicate replay I/O."""
    runtime = _ScriptedRuntime(_clean_responses())
    service = AccountReconciliationService(
        database=database,
        account=_account(),
        client=_client(runtime),
        product_ids=(27,),
        clock=lambda: _NOW + timedelta(minutes=1),
        freshness_ttl=timedelta(minutes=2),
    )
    request_id = UUID("00000000-0000-0000-0000-000000000888")

    first = await service.run_startup(request_id=request_id)

    assert first.complete is True and first.ready is True and first.replayed is False
    assert tuple(request.route_id for request in runtime.requests) == (
        PrivateRouteId.BALANCES,
        PrivateRouteId.MARGINED_POSITIONS,
        PrivateRouteId.OPEN_ORDERS,
        PrivateRouteId.ORDER_HISTORY,
        PrivateRouteId.FILLS,
        PrivateRouteId.BALANCES,
        PrivateRouteId.MARGINED_POSITIONS,
        PrivateRouteId.OPEN_ORDERS,
        PrivateRouteId.LATEST_POSITION,
    )
    runtime.responses.clear()
    request_count = len(runtime.requests)

    replay = await service.run_startup(request_id=request_id)

    assert replay.replayed is True and replay.evidence_sha256 == first.evidence_sha256
    assert len(runtime.requests) == request_count
    async with database.transaction() as session:
        row = await session.scalar(
            select(AccountReconciliationCheckpoint).where(
                AccountReconciliationCheckpoint.id == request_id
            )
        )
        assert row is not None and row.state == "FINISHED" and row.readiness == "READY"
        assert row.evidence_payload is not None and "12345" not in row.evidence_payload


@pytest.mark.asyncio
async def test_unmanaged_position_outside_configured_products_blocks_readiness(
    database: Database,
) -> None:
    """Catch product-filtered collection silently hiding external venue exposure."""
    position = json.loads(_POSITION_FIXTURE.read_text())["result"]
    cursor = {"after": None, "before": None}
    runtime = _ScriptedRuntime(
        [
            ([_balance()], {"net_equity": "100", "robo_trading_equity": "0"}),
            ([position], None),
            ([], cursor),
            ([], cursor),
            ([], cursor),
            ([_balance()], {"net_equity": "100", "robo_trading_equity": "0"}),
            ([position], None),
            ([], cursor),
            ({"size": -2, "entry_price": "123.45"}, None),
            ({"size": 0, "entry_price": "0"}, None),
        ]
    )
    service = AccountReconciliationService(
        database=database,
        account=_account(),
        client=_client(runtime),
        product_ids=(99,),
        clock=lambda: _NOW + timedelta(minutes=1),
    )

    receipt = await service.run_manual(request_id=UUID("00000000-0000-0000-0000-000000000889"))

    assert receipt.complete is True and receipt.ready is False
    assert tuple(drift.kind.value for drift in receipt.drifts) == ("EXTERNAL_POSITION",)
    async with database.transaction() as session:
        row = await session.get(
            AccountReconciliationCheckpoint,
            UUID("00000000-0000-0000-0000-000000000889"),
        )
        assert row is not None and row.external_exposure is True


@pytest.mark.asyncio
async def test_uncertain_resolution_stops_after_client_id_acceptance(
    database: Database,
) -> None:
    """Catch an accepted uncertain order being queried again or ever resubmitted."""
    order = json.loads(_ORDER_FIXTURE.read_text())["result"]
    cursor = {"after": None, "before": None}
    runtime = _ScriptedRuntime([([], cursor), ([], cursor), (order, None)])
    client = _client(runtime)
    history = await client.get_order_history(OrderHistoryQuery(page_size=50))
    fills = await client.get_fills(FillQuery(page_size=50))
    service = AccountReconciliationService(
        database=database,
        account=_account(),
        client=client,
        product_ids=(27,),
        clock=lambda: _NOW + timedelta(minutes=1),
    )
    projection = LocalAccountProjection(
        open_orders=(
            LocalOrderTruth(
                local_order_id=UUID("00000000-0000-0000-0000-000000000778"),
                product_id=27,
                client_order_id="HMT_FIXTURE_1",
                exchange_order_id=7001,
                uncertain_attempt_ids=(UUID("00000000-0000-0000-0000-000000000780"),),
            ),
        ),
        signed_positions=(),
        unbound_uncertain_attempt_ids=(),
    )

    outcomes = await service._resolve_uncertain(projection, history, fills)

    assert outcomes[0].resolution is UncertainResolution.ACCEPTED
    assert tuple(request.route_id for request in runtime.requests) == (
        PrivateRouteId.ORDER_HISTORY,
        PrivateRouteId.FILLS,
        PrivateRouteId.ORDER_BY_CLIENT_ID,
    )


@pytest.mark.asyncio
async def test_unbound_uncertain_attempt_stays_unresolved_without_dispatch(
    database: Database,
) -> None:
    """Catch a crash before order-row binding being ignored or blindly resubmitted."""
    cursor = {"after": None, "before": None}
    runtime = _ScriptedRuntime([([], cursor), ([], cursor)])
    client = _client(runtime)
    history = await client.get_order_history(OrderHistoryQuery(page_size=50))
    fills = await client.get_fills(FillQuery(page_size=50))
    service = AccountReconciliationService(
        database=database,
        account=_account(),
        client=client,
        product_ids=(27,),
        clock=lambda: _NOW + timedelta(minutes=1),
    )
    attempt_id = UUID("00000000-0000-0000-0000-000000000779")
    projection = LocalAccountProjection(
        open_orders=(),
        signed_positions=(),
        unbound_uncertain_attempt_ids=(attempt_id,),
    )

    outcomes = await service._resolve_uncertain(projection, history, fills)

    assert outcomes[0].attempt_id == attempt_id
    assert outcomes[0].local_order_id is None
    assert outcomes[0].resolution is UncertainResolution.UNRESOLVED
    assert tuple(request.route_id for request in runtime.requests) == (
        PrivateRouteId.ORDER_HISTORY,
        PrivateRouteId.FILLS,
    )
