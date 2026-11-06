"""Deterministic doubles for public-stream transport failure tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ExchangeDownError
from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot, CatalogSource
from hmt_crypto.exchange.delta.websocket_contracts import WsPlane
from hmt_crypto.exchange.delta.websocket_public_parsers import DeltaPublicFrameParser
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    GenerationControlInvalidation,
    GenerationSubscriptionReceipt,
    SubscriptionRegistry,
    WsSubscription,
)
from hmt_crypto.exchange.delta.websocket_transport import InboundKind
from hmt_crypto.exchange.delta.wire_schemas import parse_product
from hmt_crypto.market_data.public_stream import PublicConnectionGeneration, PublicStreamActor
from hmt_crypto.market_data.public_stream_models import (
    CatalogueRefreshMailbox,
    PublicCatalogueBinding,
)
from hmt_crypto.market_data.public_stream_runner import _PublicGenerationRunner
from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
from hmt_crypto.market_data.state import MarketStateStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "delta"
GENERATION = UUID("10000000-0000-0000-0000-000000000001")
CATALOGUE_ID = UUID("20000000-0000-0000-0000-000000000002")


class ManualTime:
    """Injected monotonic clock whose waits move only when advanced."""

    def __init__(self) -> None:
        self.now = 1_000_000_000
        self.waiters: list[tuple[int, asyncio.Future[None]]] = []

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep_ns(self, delay_ns: int) -> None:
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.now + delay_ns, future))
        await future

    def advance(self, delay_ns: int) -> None:
        self.now += delay_ns
        for target, future in tuple(self.waiters):
            if target <= self.now and not future.done():
                future.set_result(None)
        self.waiters = [(target, future) for target, future in self.waiters if not future.done()]


class ScriptedReceiver:
    """Mirror production recv failures and an indefinitely cancellation-resistant recv."""

    def __init__(self, *frames: str | BaseException, resist_after: int | None = None) -> None:
        self.frames = list(frames)
        self.resist_after = resist_after
        self.calls = 0
        self.active = 0
        self.cancellations = 0
        self.release = asyncio.Event()

    async def recv(self) -> str | bytes:
        self.calls += 1
        self.active += 1
        try:
            if self.frames:
                value = self.frames.pop(0)
                if isinstance(value, BaseException):
                    raise value
                return value
            if self.resist_after is not None and self.calls >= self.resist_after:
                while not self.release.is_set():
                    try:
                        await self.release.wait()
                    except asyncio.CancelledError:
                        self.cancellations += 1
                raise ExchangeDownError(context={"operation": "websocket_receive"})
            return await asyncio.Future()
        finally:
            self.active -= 1


class ScriptedControl:
    """Mirror production send failure or a send that absorbs cancellation."""

    def __init__(
        self,
        *,
        fail_send_at: int | None = None,
        resist_send_at: int | None = None,
    ) -> None:
        self.fail_send_at = fail_send_at
        self.resist_send_at = resist_send_at
        self.send_calls = 0
        self.active_sends = 0
        self.send_cancellations = 0
        self.release_send = asyncio.Event()
        self.events: list[str] = []
        self.invalidations: list[GenerationControlInvalidation] = []
        self.receipts: list[GenerationSubscriptionReceipt] = []

    async def send(self, message: str) -> None:
        del message
        self.send_calls += 1
        call = self.send_calls
        if call == self.fail_send_at:
            raise ExchangeDownError(context={"operation": "websocket_send"})
        self.events.append("send")
        if call != self.resist_send_at:
            return
        self.active_sends += 1
        try:
            while not self.release_send.is_set():
                try:
                    await self.release_send.wait()
                except asyncio.CancelledError:
                    self.send_cancellations += 1
        finally:
            self.active_sends -= 1

    def observe_inbound(self, kind: InboundKind) -> None:
        del kind

    async def invalidate(self, receipt: GenerationControlInvalidation) -> None:
        self.events.append("invalidate")
        self.invalidations.append(receipt)

    async def subscriptions_accepted(self, receipt: GenerationSubscriptionReceipt) -> None:
        self.events.append("receipt")
        self.receipts.append(receipt)


class CatalogueProvider:
    """One authoritative immutable India-testnet publication."""

    def __init__(self) -> None:
        rows = json.loads((FIXTURES / "products_catalog_valid.json").read_text())
        products = tuple(parse_product(row, endpoint_id="transport_failure") for row in rows)
        now = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
        catalogue = CatalogSnapshot(
            source=CatalogSource(
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
            ),
            revision=7,
            fetched_at=now,
            validated_at=now,
            ttl=timedelta(minutes=5),
            products=products,
        )
        self.binding = PublicCatalogueBinding(
            catalogue=catalogue,
            catalogue_snapshot_id=CATALOGUE_ID,
            spot_asset_ids={".DEUSDTUSD": 12},
        )

    def current(self) -> PublicCatalogueBinding:
        return self.binding


def generation() -> PublicConnectionGeneration:
    """Return one generation with independently bounded control/finalization."""
    return PublicConnectionGeneration(
        connection_generation=GENERATION,
        region="india",
        environment="testnet",
        control_timeout_ns=2_000_000_000,
        registry_poll_ns=100_000_000,
        repair_delay_ns=5_000_000_000,
        finalization_timeout_ns=500_000_000,
        max_frame_bytes=4096,
    )


def ack(*entries: WsSubscription) -> str:
    return json.dumps(
        {
            "type": "subscriptions",
            "channels": [
                {"name": entry.channel, "symbols": list(entry.symbols)} for entry in entries
            ],
        },
        separators=(",", ":"),
    )


def ticker_frame() -> str:
    return (
        '{"type":"ticker","ts":1793872800000000,"sy":"XBTUSD_CAT","sp":"100.10",'
        '"d":[{"i":1101,"s":"XBTUSD_CAT","m":"100.00","m24hc":"1.5",'
        '"ohlc":["90","110","80","100"],"oi":["3","1"],"pb":["70","130"],'
        '"q":["99.5","2","100.5","3","100"],"qiv":["0.4","0.5","0.45"],'
        '"to":["500","20"],"g":["0.1","0.2","0.3","0.4","0.5"]}]}'
    )


def harness(time: ManualTime) -> tuple[PublicStreamActor, SubscriptionRegistry, MarketStateStore]:
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    state = MarketStateStore(
        freshness_policy=MarketFreshnessPolicy(),
        monotonic_ns=time.monotonic_ns,
    )
    actor = PublicStreamActor(
        registry=registry,
        parser=DeltaPublicFrameParser(),
        catalogue_provider=CatalogueProvider(),
        catalogue_refresh=CatalogueRefreshMailbox(),
        state=state,
        monotonic_ns=time.monotonic_ns,
        sleep_ns=time.sleep_ns,
    )
    return actor, registry, state


async def run_scripted_generation(
    actor: PublicStreamActor,
    selected: PublicConnectionGeneration,
    *,
    receiver: object,
    control: object,
):  # type: ignore[no-untyped-def]
    """Run the non-production structural test harness outside the package API."""
    runner = _PublicGenerationRunner(
        generation=selected,
        receiver=receiver,  # type: ignore[arg-type]
        control=control,  # type: ignore[arg-type]
        registry=actor._registry,
        parser=actor._parser,
        catalogue_provider=actor._catalogue_provider,
        catalogue_refresh=actor._catalogue_refresh,
        state=actor._state,
        monotonic_ns=actor._monotonic_ns,
        sleep_ns=actor._sleep_ns,
        utc_now=actor._utc_now,
        production_owned=False,
    )
    return await runner.run()


async def settle() -> None:
    for _ in range(100):
        await asyncio.sleep(0)


__all__ = [
    "CATALOGUE_ID",
    "GENERATION",
    "ManualTime",
    "ScriptedControl",
    "ScriptedReceiver",
    "ack",
    "generation",
    "harness",
    "settle",
    "ticker_frame",
]
