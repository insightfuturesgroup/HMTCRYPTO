"""Private-stream authority, barrier, provenance and reconnect integration tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import (
    AccountRef,
    CollectionMode,
    ObservationContext,
    PrivatePaginationProvenance,
)
from hmt_crypto.domain.private_orders import (
    Fill,
    FillContractType,
    FillNotionalType,
    FillRole,
    FillType,
    ObservedFillContractType,
    ObservedFillNotionalType,
    ObservedFillRole,
    ObservedFillType,
    ObservedOrderSide,
    OrderSide,
    StrictFillPaginationResult,
    StrictOrderPaginationResult,
)
from hmt_crypto.domain.private_positions import ObservedMarginedPositions
from hmt_crypto.domain.private_wallet import (
    BalanceSnapshot,
    ObservedBalanceSnapshot,
)
from hmt_crypto.errors import (
    AuthError,
    ConfigurationError,
    ExchangeDownError,
    ExchangeProtocolError,
    SignatureExpiredError,
)
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.delta import private_read_clock
from hmt_crypto.exchange.delta.catalog import CatalogProductLoad, ProductCatalog
from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot, CatalogSource
from hmt_crypto.exchange.delta.paginator import PaginationResult
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.delta.private_ws_auth import bind_private_ws_auth_issuer
from hmt_crypto.exchange.delta.private_ws_connection import (
    _attest_private_ws_connection,
    bind_private_ws_connection_authority,
)
from hmt_crypto.exchange.delta.private_ws_models import (
    OrderSnapshot,
    PositionSnapshot,
    UserTradeUpdate,
)
from hmt_crypto.exchange.delta.private_ws_protocol import (
    PrivateWsAuthorityClass,
    PrivateWsGenerationInvalidated,
    PrivateWsInvalidationReason,
    _invalidate_private_ws_generation,
    _issue_private_ws_generation,
    attest_private_ws_delivery,
    private_ws_subscription_plan,
)
from hmt_crypto.exchange.delta.rate_limit_policy import (
    POLICY_VERSION,
    RateLimitScope,
    ScopeKind,
)
from hmt_crypto.exchange.delta.websocket_contracts import WsEndpoint, WsPlane
from hmt_crypto.exchange.delta.websocket_ports import WsReceivePort
from hmt_crypto.exchange.delta.websocket_transport import DeltaWebsocketsConnector
from hmt_crypto.exchange.delta.wire_schemas import parse_product
from hmt_crypto.exchange.private_dispatch import TrustedDispatchInstant
from hmt_crypto.market_data import connection_attempts as connection_attempts_module
from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor
from hmt_crypto.market_data.reconnect_policy import ReconnectPolicy
from hmt_crypto.private_stream import reconciliation as reconciliation_module
from hmt_crypto.private_stream import runtime
from hmt_crypto.private_stream.reconciliation import (
    PrivateWsReconciliationReceipt,
    PrivateWsRestReconciler,
    _attest_private_ws_reconciliation,
    _issue_private_ws_reconciliation_reset,
    attest_private_ws_reconciliation_reset,
)
from hmt_crypto.worker import clock as worker_clock
from hmt_crypto.worker import leadership

NOW = datetime(2026, 11, 6, tzinfo=UTC)
CATALOGUE = UUID("20000000-0000-4000-8000-000000000001")
SYMBOL = "XBTUSD_CAT"
PRODUCT_ID = 1101
ACCOUNT_ID = UUID("00000000-0000-0000-0000-00000000b402")
INCARNATION = UUID("00000000-0000-0000-0000-00000000b401")
FIXTURES = Path(__file__).parents[1] / "fixtures" / "delta"


def endpoint() -> WsEndpoint:
    return WsEndpoint(
        plane=WsPlane.PRIVATE,
        url="wss://socket-ind.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )


def bound_authority(*, observations: int = 1) -> tuple[object, dict[str, Any]]:
    account = AccountRef(
        account_id=ACCOUNT_ID,
        external_user_id="12345",
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=ExecutionMode.PAPER,
        credential_version=7,
    )
    origin = _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET)
    signer, _enrollment = credentials._issue_enrollment_credential_for_tests(
        credentials._task27_fixture_marker_for_tests(),
        snapshot_id=UUID("00000000-0000-0000-0000-00000000b403"),
        credential_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
        credential_version=7,
        account_id=ACCOUNT_ID,
        external_user_id="12345",
        origin=origin,
        mode=ExecutionMode.PAPER,
        credential_epoch=11,
        api_key=SecretStr("b4-test-api-key"),
        api_secret=SecretStr("b4-test-api-secret"),
        user_agent="hmt-crypto/b4-test",
    )
    issued = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    proof, credential_currentness = credentials._issue_account_binding_proof_for_tests(
        credentials._task27_fixture_marker_for_tests(),
        signer,
        proof_id=UUID("00000000-0000-0000-0000-00000000b404"),
        account=account,
        origin=origin,
        binding_ref=UUID("00000000-0000-0000-0000-00000000b405"),
        probe_ref=UUID("00000000-0000-0000-0000-00000000b406"),
        credential_epoch=11,
        proof_epoch=13,
        observed_at=issued,
        expires_at=issued + timedelta(minutes=5),
    )
    clock, clock_currentness = worker_clock._issue_trusted_dispatch_clock_for_tests(
        worker_clock._task210_fixture_marker_for_tests(),
        clock_owner_id=UUID(int=104),
        process_incarnation_id=INCARNATION,
        clock_epoch=1,
        correlation_epoch=1,
        sample_monotonic_ns=400_000_000_000,
        observed_monotonic_ns=400_000_000_000,
        utc_epoch_seconds=1_800_000_000,
        utc_upper_bound_ns=1_800_000_000_100_000_000,
        nonblocking_execution_guard_ns=1,
    )
    instants = tuple(
        TrustedDispatchInstant(
            process_id=clock.process_id,
            process_incarnation_id=clock.process_incarnation_id,
            clock_epoch=clock.clock_epoch,
            correlation_epoch=clock.correlation_epoch,
            monotonic_ns=400_000_000_000 + index * 3_000_000_000,
            utc_epoch_seconds=1_800_000_000 + index * 3,
            utc_upper_bound_ns=(1_800_000_000 + index * 3) * 1_000_000_000 + 100_000_000,
        )
        for index in range(1, observations + 1)
    )
    instant_issuer = private_read_clock._issue_private_read_trusted_instant_issuer_for_tests(
        private_read_clock._private_read_trusted_instant_marker_for_tests(),
        clock=clock,
        clock_currentness=clock_currentness,
        observations=instants,
    )
    authority = bind_private_ws_auth_issuer(
        endpoint=endpoint(),
        account=account,
        signer=signer,
        account_binding=proof,
        credential_currentness=credential_currentness,
        clock=clock,
        clock_currentness=clock_currentness,
        trusted_instant_issuer=instant_issuer,
    )
    worker_proof, worker_currentness = leadership._issue_single_private_worker_for_tests(
        leadership._task210_fixture_marker_for_tests(),
        proof_id=uuid4(),
        deployment_revision="private-ws-runtime-test",
        external_user_id=account.external_user_id,
        worker_incarnation_id=uuid4(),
        process_incarnation_id=INCARNATION,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        ownership_epoch=1,
        issued_at=issued,
        expires_at=issued + timedelta(minutes=5),
        observed_at=issued,
        process_started_monotonic_ns=0,
    )
    return authority, {
        "clock": clock,
        "worker_proof": worker_proof,
        "worker_currentness": worker_currentness,
    }


async def published_catalogue(
    *,
    region: DeltaRegion = DeltaRegion.INDIA,
) -> CatalogSnapshot:
    rows = json.loads((FIXTURES / "products_catalog_valid.json").read_text())
    products = tuple(parse_product(row, endpoint_id="private_ws_test") for row in rows)
    source = CatalogSource(region=region, environment=DeltaEnvironment.TESTNET)

    async def fetch_products() -> CatalogProductLoad:
        return CatalogProductLoad(
            source=source,
            pagination=PaginationResult(
                items=products,
                page_count=1,
                complete=True,
                truncated=False,
                terminal_after=None,
            ),
        )

    catalog = ProductCatalog(
        source=source,
        fetch_products=fetch_products,
        ttl=timedelta(minutes=5),
        clock=lambda: NOW,
    )
    await catalog.refresh()
    snapshot = catalog.peek()
    assert snapshot is not None
    return snapshot


def frame(payload: dict[str, object]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def auth_success() -> str:
    return frame(
        {
            "type": "key-auth",
            "success": True,
            "status_code": 200,
            "status": "authenticated",
        }
    )


def auth_failure(status: str = "invalid_signature", code: int = 401) -> str:
    return frame(
        {
            "type": "key-auth",
            "success": False,
            "status_code": code,
            "status": status,
            "message": "redacted failure",
        }
    )


def subscriptions_ack(catalogue: CatalogSnapshot) -> str:
    plan = private_ws_subscription_plan(
        symbol=SYMBOL, catalogue=catalogue, catalogue_snapshot_id=CATALOGUE
    )
    return frame(
        {
            "type": "subscriptions",
            "channels": [
                {
                    "name": entry.channel,
                    **({"symbols": list(entry.symbols)} if entry.symbols else {}),
                }
                for entry in plan.desired.entries
            ],
        }
    )


def position_snapshot() -> str:
    return frame({"result": [], "success": True, "type": "positions", "action": "snapshot"})


def order_snapshot() -> str:
    return frame(
        {
            "meta": {"seq_no": 7, "timestamp": 1_594_149_235_554_045},
            "result": [
                {
                    "id": 1_592_130,
                    "limit_price": "9000",
                    "order_type": "limit_order",
                    "product_id": PRODUCT_ID,
                    "reduce_only": False,
                    "side": "buy",
                    "size": 1,
                    "state": "open",
                    "stop_order_type": None,
                    "stop_price": None,
                    "time_in_force": "gtc",
                    "trail_amount": None,
                    "unfilled_size": 1,
                    "average_fill_price": None,
                    "user_id": 12345,
                }
            ],
            "success": True,
            "symbol": SYMBOL,
            "type": "orders",
            "action": "snapshot",
        }
    )


class Monotonic:
    def __init__(self) -> None:
        self.value = 1_000_000_000

    def __call__(self) -> int:
        self.value += 1_000_000
        return self.value


class ScriptedMonotonic:
    def __init__(self, values: list[int]) -> None:
        self.values = values
        self.last = values[-1]

    def __call__(self) -> int:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


class FakeReceiver(WsReceivePort):
    def __init__(self, frames: list[str], *, empty_delay: float = 0.02) -> None:
        self.frames = frames
        self.empty_delay = empty_delay

    async def recv(self) -> str | bytes:
        if not self.frames:
            await asyncio.sleep(self.empty_delay)
            raise ExchangeDownError(context={"operation": "fake_disconnect"})
        return self.frames.pop(0)


class HangingReceiver(FakeReceiver):
    async def recv(self) -> str | bytes:
        if self.frames:
            return await super().recv()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FakeConnection:
    def __init__(self, frames: list[str], *, hanging: bool = False) -> None:
        self._receiver: WsReceivePort = HangingReceiver(frames) if hanging else FakeReceiver(frames)
        self._receiver_issued = False
        self.sent: list[str] = []

    def receiver(self) -> WsReceivePort:
        if self._receiver_issued:
            raise AssertionError("receiver issued twice")
        self._receiver_issued = True
        return self._receiver

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason


class HangingAuthSendConnection(FakeConnection):
    async def send(self, message: str) -> None:
        self.sent.append(message)
        if json.loads(message).get("type") == "key-auth":
            await asyncio.Event().wait()


class HangingControlSendConnection(FakeConnection):
    def __init__(
        self,
        frames: list[str],
        *,
        hang_type: str,
        hanging: bool = False,
    ) -> None:
        super().__init__(frames, hanging=hanging)
        self._hang_type = hang_type

    async def send(self, message: str) -> None:
        self.sent.append(message)
        if json.loads(message).get("type") == self._hang_type:
            await asyncio.Event().wait()


class FakeReceipt:
    def __init__(self, generation: UUID, account: Any, catalogue: CatalogSnapshot) -> None:
        self.generation = generation
        self.account = account
        self.product_id = PRODUCT_ID
        self.catalogue_snapshot_id = CATALOGUE
        self.catalogue_revision = catalogue.revision
        self.catalogue_content_sha256 = catalogue.content_sha256
        self.observed_at = NOW
        self.evidence_sha256 = "a" * 64


class FakeReset:
    def __init__(self, receipt: FakeReceipt) -> None:
        self.receipt = receipt


class Reconciler:
    def __init__(
        self,
        *,
        account: Any,
        catalogue: CatalogSnapshot,
        client: object | None = None,
    ) -> None:
        del client
        self._account = account
        self._catalogue = catalogue
        self.calls: list[UUID] = []

    @property
    def account(self):
        return self._account.model_copy()

    async def reconcile_after_baselines(
        self,
        *,
        generation: UUID,
        product_id: int,
        catalogue_snapshot_id: UUID,
        catalogue_revision: int,
        catalogue_content_sha256: str,
    ) -> FakeReceipt:
        assert product_id == PRODUCT_ID
        assert catalogue_snapshot_id == CATALOGUE
        assert catalogue_revision == self._catalogue.revision
        assert catalogue_content_sha256 == self._catalogue.content_sha256
        self.calls.append(generation)
        await asyncio.sleep(0)
        return FakeReceipt(generation, self._account, self._catalogue)


class CancellationResistantReconciler(Reconciler):
    def __init__(self, *, account: Any, catalogue: CatalogSnapshot) -> None:
        super().__init__(account=account, catalogue=catalogue)
        self.started = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.release = asyncio.Event()

    async def reconcile_after_baselines(
        self,
        *,
        generation: UUID,
        product_id: int,
        catalogue_snapshot_id: UUID,
        catalogue_revision: int,
        catalogue_content_sha256: str,
    ) -> FakeReceipt:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await self.release.wait()
        return await super().reconcile_after_baselines(
            generation=generation,
            product_id=product_id,
            catalogue_snapshot_id=catalogue_snapshot_id,
            catalogue_revision=catalogue_revision,
            catalogue_content_sha256=catalogue_content_sha256,
        )


def observation_context(
    account: AccountRef, route: PrivateRouteId, correlation: int
) -> ObservationContext:
    return ObservationContext(
        account=account,
        endpoint_id=route,
        correlation_id=UUID(int=correlation),
        requested_at=NOW,
        received_at=NOW,
        source="delta_rest",
    )


def pagination_provenance(
    account: AccountRef, route: PrivateRouteId, correlation: int
) -> PrivatePaginationProvenance:
    return PrivatePaginationProvenance(
        account=account,
        endpoint_id=route,
        first_requested_at=NOW,
        last_received_at=NOW,
        page_correlation_ids=(UUID(int=correlation),),
        credential_version=account.credential_version,
        policy_version=POLICY_VERSION,
        mode=CollectionMode.STRICT,
    )


def missed_reconnect_fill() -> Fill:
    asset = {"symbol": "USD", "precision": 8, "minimum_precision": 2}
    return Fill(
        id=UUID("071b5513-6557-4d0a-ad99-557931137dbd"),
        size=Decimal("1"),
        notional=Decimal("101"),
        product={
            "id": PRODUCT_ID,
            "symbol": SYMBOL,
            "contract_type": ObservedFillContractType(
                raw="perpetual_futures",
                known=FillContractType.PERPETUAL_FUTURES,
            ),
            "tick_size": Decimal("0.5"),
            "contract_value": Decimal("1"),
            "contract_unit_currency": "BTC",
            "notional_type": ObservedFillNotionalType(
                raw="vanilla", known=FillNotionalType.VANILLA
            ),
            "quoting_asset": asset,
            "underlying_asset": {
                "symbol": "BTC",
                "precision": 8,
                "minimum_precision": 1,
            },
            "settling_asset": asset,
            "spot_index": {"symbol": SYMBOL},
        },
        fill_type=ObservedFillType(raw="normal", known=FillType.NORMAL),
        side=ObservedOrderSide(raw="buy", known=OrderSide.BUY),
        price=Decimal("101"),
        role=ObservedFillRole(raw="taker", known=FillRole.TAKER),
        commission=Decimal("-0.1"),
        created_at=NOW,
        product_id=PRODUCT_ID,
        product_symbol=SYMBOL,
        order_id={"kind": "NUMERIC", "raw": "99", "order_id": 99},
        settling_asset_id=5,
        settling_asset_symbol="USD",
        meta_data={"kind": "NULL"},
    )


class ExactRestTruthClient:
    def __init__(self, account: AccountRef, *, include_fill: bool) -> None:
        self._account = account.model_copy()
        self._include_fill = include_fill

    async def get_balances(self) -> ObservedBalanceSnapshot:
        return ObservedBalanceSnapshot(
            context=observation_context(self._account, PrivateRouteId.BALANCES, 201),
            value=BalanceSnapshot(
                balances=(),
                net_equity=Decimal("321.5"),
                robo_trading_equity=Decimal("0"),
            ),
        )

    async def get_margined_positions(self, _query: object) -> ObservedMarginedPositions:
        return ObservedMarginedPositions(
            context=observation_context(self._account, PrivateRouteId.MARGINED_POSITIONS, 202),
            value=(),
        )

    async def get_open_orders(
        self, _query: object, *, mode: CollectionMode
    ) -> StrictOrderPaginationResult:
        assert mode is CollectionMode.STRICT
        return StrictOrderPaginationResult(
            kind="STRICT",
            items=(),
            complete=True,
            page_count=1,
            provenance=pagination_provenance(self._account, PrivateRouteId.OPEN_ORDERS, 203),
        )

    async def get_fills(
        self, _query: object, *, mode: CollectionMode
    ) -> StrictFillPaginationResult:
        assert mode is CollectionMode.STRICT
        return StrictFillPaginationResult(
            kind="STRICT",
            items=(missed_reconnect_fill(),) if self._include_fill else (),
            complete=True,
            page_count=1,
            provenance=pagination_provenance(self._account, PrivateRouteId.FILLS, 204),
        )


def fake_attest(
    value: object,
    *,
    generation: UUID,
    account: Any,
    product_id: int,
    catalogue_snapshot_id: UUID,
    catalogue_revision: int,
    catalogue_content_sha256: str,
) -> FakeReceipt:
    assert isinstance(value, FakeReceipt)
    assert value.generation == generation
    assert value.account.model_dump() == account.model_dump()
    assert value.product_id == product_id == PRODUCT_ID
    assert value.catalogue_snapshot_id == catalogue_snapshot_id
    assert value.catalogue_revision == catalogue_revision
    assert value.catalogue_content_sha256 == catalogue_content_sha256
    return value


def fake_reset(*, lease: object, receipt: FakeReceipt, received_at: datetime) -> FakeReset:
    del lease
    assert received_at == NOW
    return FakeReset(receipt)


def patch_actor_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "PrivateWsRestReconciler", Reconciler)
    monkeypatch.setattr(runtime, "_attest_private_ws_reconciliation", fake_attest)
    monkeypatch.setattr(
        runtime,
        "_attest_private_ws_reconciliation_barrier",
        lambda receipt, *, pending: receipt,
    )
    monkeypatch.setattr(runtime, "_issue_private_ws_reconciliation_reset", fake_reset)
    monkeypatch.setattr(runtime, "_is_reviewed_connection", lambda _value: True)
    monkeypatch.setattr(runtime, "_utc_now", lambda: NOW)


def generation_lease(
    authority: Any,
    catalogue: CatalogSnapshot,
    *,
    generation: UUID | None = None,
):
    plan = private_ws_subscription_plan(
        symbol=SYMBOL,
        catalogue=catalogue,
        catalogue_snapshot_id=CATALOGUE,
    )
    return plan, _issue_private_ws_generation(
        account=authority.account,
        plan=plan,
        generation=generation or uuid4(),
    )


@pytest.mark.asyncio
async def test_plan_requires_published_india_testnet_catalogue_and_derives_product_binding() -> (
    None
):
    catalogue = await published_catalogue()
    plan = private_ws_subscription_plan(
        symbol=SYMBOL,
        catalogue=catalogue,
        catalogue_snapshot_id=CATALOGUE,
    )
    assert plan.product_id == PRODUCT_ID
    assert plan.catalogue_revision == catalogue.revision
    assert plan.catalogue_content_sha256 == catalogue.content_sha256
    assert tuple(entry.channel for entry in plan.desired.entries) == (
        "margins",
        "orders",
        "positions",
        "v2/user_trades",
    )
    assert all(entry.symbols in {(), (SYMBOL,)} for entry in plan.desired.entries)

    unpublished = CatalogSnapshot(
        source=catalogue.source,
        revision=catalogue.revision,
        fetched_at=NOW,
        validated_at=NOW,
        ttl=timedelta(minutes=5),
        products=catalogue.products,
    )
    global_catalogue = await published_catalogue(region=DeltaRegion.GLOBAL)
    for candidate, symbol in (
        (unpublished, SYMBOL),
        (global_catalogue, SYMBOL),
        (catalogue, "UNKNOWN"),
    ):
        with pytest.raises(ConfigurationError):
            private_ws_subscription_plan(
                symbol=symbol,
                catalogue=candidate,
                catalogue_snapshot_id=CATALOGUE,
            )


@pytest.mark.asyncio
async def test_reconciliation_reset_carries_actual_exchange_truth_and_missed_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reconciliation_module, "DeltaIndiaPrivateReadClient", ExactRestTruthClient)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    generation = runtime._generation_state(lease).generation
    common = {
        "generation": generation,
        "product_id": plan.product_id,
        "catalogue_snapshot_id": plan.catalogue_snapshot_id,
        "catalogue_revision": plan.catalogue_revision,
        "catalogue_content_sha256": plan.catalogue_content_sha256,
    }
    filled = PrivateWsRestReconciler(
        account=authority.account,
        client=ExactRestTruthClient(authority.account, include_fill=True),
    )
    empty = PrivateWsRestReconciler(
        account=authority.account,
        client=ExactRestTruthClient(authority.account, include_fill=False),
    )
    filled_receipt = await filled.reconcile_after_baselines(**common)
    empty_receipt = await empty.reconcile_after_baselines(**common)
    assert filled_receipt.balances.value.net_equity == Decimal("321.5")
    assert filled_receipt.fills.items[0].id == missed_reconnect_fill().id
    assert filled_receipt.evidence_sha256 != empty_receipt.evidence_sha256

    reset = _issue_private_ws_reconciliation_reset(
        lease=lease,
        receipt=filled_receipt,
        received_at=NOW,
    )
    assert attest_private_ws_reconciliation_reset(reset) is reset
    assert reset.source.local_sequence == 1
    assert reset.balances.value.net_equity == Decimal("321.5")
    assert reset.fills.items[0].id == missed_reconnect_fill().id
    _invalidate_private_ws_generation(
        lease=lease,
        reason=PrivateWsInvalidationReason.DISCONNECTED,
        invalidated_at=NOW,
    )
    with pytest.raises(ExchangeProtocolError):
        attest_private_ws_reconciliation_reset(reset)
    with pytest.raises(ExchangeProtocolError):
        _ = reset.fills


@pytest.mark.asyncio
async def test_reconciliation_barrier_rejects_observation_after_its_rest_request_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reconciliation_module, "DeltaIndiaPrivateReadClient", ExactRestTruthClient)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    receipt = await PrivateWsRestReconciler(
        account=authority.account,
        client=ExactRestTruthClient(authority.account, include_fill=True),
    ).reconcile_after_baselines(
        generation=runtime._generation_state(lease).generation,
        product_id=plan.product_id,
        catalogue_snapshot_id=plan.catalogue_snapshot_id,
        catalogue_revision=plan.catalogue_revision,
        catalogue_content_sha256=plan.catalogue_content_sha256,
    )
    fill = UserTradeUpdate(
        symbol=SYMBOL,
        fill_id="fill-after-rest-start",
        reason="normal",
        user_id=12345,
        order_id=99,
        side="buy",
        size=1,
        price=Decimal("101"),
        position_after=1,
        role="taker",
        client_order_id="HMT-99",
        observed_at=NOW,
        sequence=1,
    )

    assert (
        reconciliation_module._attest_private_ws_reconciliation_barrier(
            receipt,
            pending=((fill, NOW - timedelta(microseconds=1)),),
        )
        is receipt
    )
    with pytest.raises(ExchangeProtocolError):
        reconciliation_module._attest_private_ws_reconciliation_barrier(
            receipt,
            pending=((fill, NOW + timedelta(microseconds=1)),),
        )


def test_reconciliation_receipt_and_reconciler_are_not_structurally_forgeable() -> None:
    authority, _context = bound_authority()
    with pytest.raises(TypeError):
        PrivateWsReconciliationReceipt()
    counterfeit = object.__new__(PrivateWsReconciliationReceipt)
    with pytest.raises(ExchangeProtocolError):
        _attest_private_ws_reconciliation(
            counterfeit,
            generation=uuid4(),
            account=authority.account,
            product_id=PRODUCT_ID,
            catalogue_snapshot_id=CATALOGUE,
            catalogue_revision=1,
            catalogue_content_sha256="a" * 64,
        )
    with pytest.raises(ConfigurationError):
        PrivateWsRestReconciler(
            account=authority.account,
            client=object(),  # type: ignore[arg-type]
        )


def test_first_and_later_compact_fills_remain_provisional() -> None:
    first = UserTradeUpdate(
        symbol=SYMBOL,
        fill_id="fill-1",
        reason="normal",
        user_id=12345,
        order_id=1,
        side="buy",
        size=1,
        price=Decimal("100"),
        position_after=1,
        role="taker",
        client_order_id="HMT-1",
        observed_at=NOW,
        sequence=1,
    )
    later = UserTradeUpdate(
        symbol=SYMBOL,
        fill_id="fill-2",
        reason="normal",
        user_id=12345,
        order_id=1,
        side="buy",
        size=1,
        price=Decimal("101"),
        position_after=2,
        role="taker",
        client_order_id="HMT-1",
        observed_at=NOW,
        sequence=2,
    )
    assert runtime._authority(first) is PrivateWsAuthorityClass.PROVISIONAL
    assert runtime._authority(later) is PrivateWsAuthorityClass.PROVISIONAL


def test_connection_authority_binds_exact_worker_account_process_egress_and_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection_attempts_module, "_PROCESS_OWNER_REF", None)
    connection_attempts_module._GOVERNOR_STATES.clear()
    auth, context = bound_authority()
    proof = context["worker_proof"]
    currentness = context["worker_currentness"]
    scope = RateLimitScope(
        region="india",
        environment="testnet",
        kind=ScopeKind.IP,
        external_id="private-ws-test-egress",
    )
    policy = ReconnectPolicy()
    attempts = ConnectionAttemptGovernor.for_single_process_egress(
        scope,
        ownership_proof=proof,
        ownership_currentness=currentness,
        policy=policy,
    )
    connector = DeltaWebsocketsConnector()
    sealed = bind_private_ws_connection_authority(
        connector=connector,
        attempts=attempts,
        auth=auth,
        egress_scope=scope,
        worker_proof=proof,
        worker_currentness=currentness,
        reconnect_policy=policy,
    )
    checked = _attest_private_ws_connection(sealed)
    assert checked[:3] == (connector, attempts, auth)
    assert checked[3] == endpoint()
    assert checked[4] == policy

    wrong_scope = RateLimitScope(
        region="india",
        environment="testnet",
        kind=ScopeKind.IP,
        external_id="different-egress",
    )
    with pytest.raises(ConfigurationError):
        bind_private_ws_connection_authority(
            connector=connector,
            attempts=attempts,
            auth=auth,
            egress_scope=wrong_scope,
            worker_proof=proof,
            worker_currentness=currentness,
            reconnect_policy=policy,
        )

    with pytest.raises(ConfigurationError):
        bind_private_ws_connection_authority(
            connector=connector,
            attempts=attempts,
            auth=auth,
            egress_scope=scope,
            worker_proof=proof,
            worker_currentness=currentness,
            reconnect_policy=ReconnectPolicy(base_delay_ns=2_000_000_000),
        )

    foreign_proof, foreign_currentness = leadership._issue_single_private_worker_for_tests(
        leadership._task210_fixture_marker_for_tests(),
        proof_id=uuid4(),
        deployment_revision="private-ws-runtime-test",
        external_user_id="99999",
        worker_incarnation_id=uuid4(),
        process_incarnation_id=uuid4(),
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        ownership_epoch=1,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        observed_at=NOW,
        process_started_monotonic_ns=0,
    )
    with pytest.raises(ConfigurationError):
        bind_private_ws_connection_authority(
            connector=connector,
            attempts=attempts,
            auth=auth,
            egress_scope=scope,
            worker_proof=foreign_proof,
            worker_currentness=foreign_currentness,
            reconnect_policy=policy,
        )

    leadership._invalidate_worker_ownership_for_tests(
        leadership._task210_fixture_marker_for_tests(), currentness
    )
    with pytest.raises(ConfigurationError):
        _attest_private_ws_connection(sealed)


@pytest.mark.asyncio
async def test_snapshots_precede_final_rest_reconciliation_and_deliveries_have_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    connection = FakeConnection(
        [auth_success(), subscriptions_ack(catalogue), position_snapshot(), order_snapshot()]
    )
    plan, lease = generation_lease(authority, catalogue)
    output: asyncio.Queue[Any] = asyncio.Queue(maxsize=8)
    reconciler = Reconciler(account=authority.account, catalogue=catalogue)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=reconciler,
        output=output,
        monotonic_ns=Monotonic(),
    )
    barrier_pending: list[tuple[object, datetime]] = []

    def attest_barrier(receipt: FakeReceipt, *, pending):
        barrier_pending.extend(pending)
        return receipt

    monkeypatch.setattr(runtime, "_attest_private_ws_reconciliation_barrier", attest_barrier)
    generation = runtime._generation_state(lease).generation
    with pytest.raises(ExchangeDownError):
        await actor.run(connection, lease=lease)

    assert reconciler.calls == [generation]
    assert json.loads(connection.sent[1])["type"] == "key-auth"
    assert json.loads(connection.sent[2])["type"] == "subscribe"
    reset = output.get_nowait()
    assert isinstance(reset, FakeReset)
    assert [type(observation) for observation, _received_at in barrier_pending] == [
        PositionSnapshot,
        OrderSnapshot,
    ]
    assert output.empty()


@pytest.mark.asyncio
async def test_auth_rejection_never_subscribes_or_reconciles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    connection = FakeConnection([auth_failure()])
    reconciler = Reconciler(account=authority.account, catalogue=catalogue)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=reconciler,
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=Monotonic(),
    )
    with pytest.raises(AuthError):
        await actor.run(connection, lease=lease)
    assert len(connection.sent) == 2
    assert reconciler.calls == []


@pytest.mark.asyncio
async def test_auth_ack_deadline_is_under_five_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    monkeypatch.setattr(runtime, "_AUTH_DEADLINE_SECONDS", 0.01)
    authority, _context = bound_authority(observations=2)
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=Monotonic(),
    )
    with pytest.raises(SignatureExpiredError):
        await actor.run(FakeConnection([], hanging=True), lease=lease)
    assert runtime._AUTH_DEADLINE_SECONDS < 5


@pytest.mark.asyncio
async def test_same_auth_deadline_covers_signed_frame_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    monkeypatch.setattr(runtime, "_AUTH_DEADLINE_SECONDS", 0.01)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=Monotonic(),
    )
    connection = HangingAuthSendConnection([])
    with pytest.raises(SignatureExpiredError):
        await actor.run(connection, lease=lease)
    assert [json.loads(value)["type"] for value in connection.sent] == [
        "enable_heartbeat",
        "key-auth",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("hang_type", ["enable_heartbeat", "subscribe"])
async def test_control_frame_send_timeout_closes_generation(
    monkeypatch: pytest.MonkeyPatch,
    hang_type: str,
) -> None:
    patch_actor_dependencies(monkeypatch)
    monkeypatch.setattr(runtime, "_CONTROL_SEND_SECONDS", 0.01, raising=False)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    frames = [auth_success()] if hang_type == "subscribe" else []
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=Monotonic(),
    )
    connection = HangingControlSendConnection(frames, hang_type=hang_type, hanging=True)
    with pytest.raises(ExchangeDownError):
        await asyncio.wait_for(actor.run(connection, lease=lease), timeout=0.2)


@pytest.mark.asyncio
async def test_ping_send_timeout_closes_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_actor_dependencies(monkeypatch)
    monkeypatch.setattr(runtime, "_CONTROL_SEND_SECONDS", 0.01, raising=False)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, _lease = generation_lease(authority, catalogue)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=lambda: 30_000_000_000,
    )
    connection = HangingControlSendConnection([], hang_type="ping", hanging=True)
    with pytest.raises(ExchangeDownError):
        await asyncio.wait_for(
            actor._receive(
                connection,
                connection.receiver(),
                runtime.initial_liveness(now_ns=0),
            ),
            timeout=0.2,
        )


@pytest.mark.asyncio
async def test_missing_initial_snapshots_have_a_generation_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    monkeypatch.setattr(runtime, "_SNAPSHOT_DEADLINE_SECONDS", 0.01, raising=False)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=Monotonic(),
    )
    with pytest.raises(ExchangeDownError) as captured:
        await asyncio.wait_for(
            actor.run(
                FakeConnection(
                    [auth_success(), subscriptions_ack(catalogue)],
                    hanging=True,
                ),
                lease=lease,
            ),
            timeout=0.2,
        )
    assert captured.value.context["state"] == "snapshot_timeout"


@pytest.mark.asyncio
async def test_active_generation_revalidates_connection_authority_within_a_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    monkeypatch.setattr(runtime, "_AUTHORITY_REVALIDATION_SECONDS", 0.01, raising=False)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    current = True

    def attest_currentness() -> None:
        if not current:
            raise ConfigurationError(context={"operation": "test_authority_revoked"})

    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=Monotonic(),
        attest_currentness=attest_currentness,
    )
    task = asyncio.create_task(
        actor.run(
            FakeConnection(
                [auth_success(), subscriptions_ack(catalogue)],
                hanging=True,
            ),
            lease=lease,
        )
    )
    await asyncio.sleep(0)
    current = False
    with pytest.raises(ConfigurationError):
        await asyncio.wait_for(task, timeout=0.2)


@pytest.mark.asyncio
async def test_frame_arriving_after_heartbeat_deadline_closes_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=ScriptedMonotonic([0, 0, 36_000_000_000]),
    )
    with pytest.raises(ExchangeDownError) as captured:
        await actor.run(FakeConnection([auth_success()]), lease=lease)
    assert captured.value.context["state"] == "late_frame"


@pytest.mark.asyncio
async def test_venue_request_expired_revokes_credential_and_clock_currentness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    authority, _context = bound_authority(observations=2)
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=Monotonic(),
    )
    with pytest.raises(SignatureExpiredError):
        await actor.run(
            FakeConnection([auth_failure("request_expired", 408)]),
            lease=lease,
        )
    with pytest.raises(ConfigurationError):
        authority.encode_fresh_auth()


@pytest.mark.asyncio
async def test_reset_only_batch_fits_minimum_capacity_without_replaying_baselines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    output: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=output,
        monotonic_ns=Monotonic(),
    )
    connection = FakeConnection(
        [auth_success(), subscriptions_ack(catalogue), position_snapshot(), order_snapshot()]
    )
    with pytest.raises(ExchangeDownError):
        await actor.run(connection, lease=lease)
    assert isinstance(output.get_nowait(), FakeReset)
    assert output.empty()


@pytest.mark.asyncio
async def test_generation_cleanup_defers_repeated_cancellation_until_owned_tasks_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    monkeypatch.setattr(runtime, "PrivateWsRestReconciler", CancellationResistantReconciler)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    reconciler = CancellationResistantReconciler(
        account=authority.account,
        catalogue=catalogue,
    )
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=reconciler,
        output=asyncio.Queue(maxsize=8),
        monotonic_ns=Monotonic(),
    )
    connection = FakeConnection(
        [auth_success(), subscriptions_ack(catalogue), position_snapshot(), order_snapshot()],
        hanging=True,
    )
    task = asyncio.create_task(actor.run(connection, lease=lease))
    await asyncio.wait_for(reconciler.started.wait(), timeout=1)
    task.cancel()
    await asyncio.wait_for(reconciler.cancel_seen.wait(), timeout=1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    reconciler.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)


class ConnectionContext(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class Connector:
    def __init__(self, connections: list[FakeConnection]) -> None:
        self.connections = connections
        self.opened: list[WsEndpoint] = []

    def open(
        self, endpoint_value: WsEndpoint, *, attempts: object
    ) -> AbstractAsyncContextManager[FakeConnection]:
        del attempts
        self.opened.append(endpoint_value)
        return ConnectionContext(self.connections.pop(0))


class Attempts:
    region = "india"
    environment = "testnet"


class ConnectionAuthority:
    pass


def patch_supervisor_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connector: Connector,
    attempts: Attempts,
    authority: Any,
    policy: ReconnectPolicy,
) -> ConnectionAuthority:
    sealed = ConnectionAuthority()
    monkeypatch.setattr(runtime, "PrivateWsConnectionAuthority", ConnectionAuthority)
    monkeypatch.setattr(
        runtime,
        "_attest_private_ws_connection",
        lambda value: (
            (
                connector,
                attempts,
                authority,
                endpoint(),
                policy,
            )
            if value is sealed
            else (_ for _ in ()).throw(ConfigurationError(context={"operation": "test"}))
        ),
    )
    return sealed


@pytest.mark.asyncio
async def test_supervisor_reauthenticates_rereconciles_and_drains_old_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    authority, _context = bound_authority(observations=2)
    catalogue = await published_catalogue()
    first = FakeConnection(
        [auth_success(), subscriptions_ack(catalogue), position_snapshot(), order_snapshot()]
    )
    second = FakeConnection([auth_failure()])
    connector = Connector([first, second])
    attempts = Attempts()
    reconciler = Reconciler(account=authority.account, catalogue=catalogue)
    slept: list[float] = []

    async def sleep(delay: float) -> None:
        slept.append(delay)

    policy = ReconnectPolicy(
        base_delay_ns=1_000_000_000,
        min_spacing_ns=1_000_000_000,
    )
    connection_authority = patch_supervisor_dependencies(
        monkeypatch,
        connector=connector,
        attempts=attempts,
        authority=authority,
        policy=policy,
    )
    supervisor = runtime.PrivateStreamSupervisor(
        connection_authority=connection_authority,
        symbol=SYMBOL,
        catalogue=catalogue,
        catalogue_snapshot_id=CATALOGUE,
        reconciliation=reconciler,
        output_capacity=8,
        jitter=lambda: Decimal("0.5"),
        monotonic_ns=Monotonic(),
        sleep=sleep,
    )
    with pytest.raises(AuthError):
        await supervisor.run()
    assert connector.opened == [endpoint(), endpoint()]
    assert len(reconciler.calls) == 1
    assert slept == [1.0]
    assert not hasattr(supervisor.output, "put")
    terminal = supervisor.output.get_nowait()
    assert isinstance(terminal, PrivateWsGenerationInvalidated)
    assert terminal.reason is PrivateWsInvalidationReason.AUTH
    assert supervisor.output.empty()


@pytest.mark.asyncio
async def test_supervisor_rejects_structural_connection_authority_or_reconciler() -> None:
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    with pytest.raises(ExchangeProtocolError):
        runtime.PrivateStreamSupervisor(
            connection_authority=object(),  # type: ignore[arg-type]
            symbol=SYMBOL,
            catalogue=catalogue,
            catalogue_snapshot_id=CATALOGUE,
            reconciliation=object(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_supervisor_has_single_run_lease_and_releases_it_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    policy = ReconnectPolicy()
    connector = Connector([])
    connection_authority = patch_supervisor_dependencies(
        monkeypatch,
        connector=connector,
        attempts=Attempts(),
        authority=authority,
        policy=policy,
    )
    supervisor = runtime.PrivateStreamSupervisor(
        connection_authority=connection_authority,
        symbol=SYMBOL,
        catalogue=catalogue,
        catalogue_snapshot_id=CATALOGUE,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
    )
    entered = asyncio.Event()

    async def hang() -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(supervisor, "_run_owned", hang)
    owner = asyncio.create_task(supervisor.run())
    await asyncio.wait_for(entered.wait(), timeout=1)
    with pytest.raises(ExchangeProtocolError):
        await supervisor.run()
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    async def complete() -> None:
        return None

    monkeypatch.setattr(supervisor, "_run_owned", complete)
    await supervisor.run()


@pytest.mark.asyncio
async def test_held_delivery_and_source_revoke_synchronously_on_generation_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_actor_dependencies(monkeypatch)
    authority, _context = bound_authority()
    catalogue = await published_catalogue()
    plan, lease = generation_lease(authority, catalogue)
    output: asyncio.Queue[Any] = asyncio.Queue(maxsize=8)
    actor = runtime._PrivateStreamGeneration(
        auth=authority,
        plan=plan,
        reconciliation=Reconciler(account=authority.account, catalogue=catalogue),
        output=output,
        monotonic_ns=Monotonic(),
    )
    with pytest.raises(ExchangeDownError):
        await actor.run(
            FakeConnection(
                [
                    auth_success(),
                    subscriptions_ack(catalogue),
                    position_snapshot(),
                    order_snapshot(),
                ]
            ),
            lease=lease,
        )
    _reset = output.get_nowait()
    observation = runtime.parse_private_ws_observation(
        runtime.decode_ws_object(
            order_snapshot(),
            max_bytes=64 * 1024,
            stream_label="private",
        )
    )
    delivery = runtime._issue_private_ws_delivery(
        lease=lease,
        authority=PrivateWsAuthorityClass.PROVISIONAL,
        observation=observation,
        received_at=NOW,
        reconciliation_evidence_sha256="a" * 64,
    )
    source = delivery.source
    assert attest_private_ws_delivery(delivery) is delivery
    _invalidate_private_ws_generation(
        lease=lease,
        reason=PrivateWsInvalidationReason.DISCONNECTED,
        invalidated_at=NOW,
    )
    with pytest.raises(ExchangeProtocolError):
        attest_private_ws_delivery(delivery)
    with pytest.raises(ExchangeProtocolError):
        _ = delivery.observation
    with pytest.raises(ExchangeProtocolError):
        _ = source.account_id
