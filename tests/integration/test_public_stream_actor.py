"""Credential-free one-generation public stream actor integration tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from public_stream_failure_support import run_scripted_generation

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot, CatalogSource
from hmt_crypto.exchange.delta.websocket_codec import encode_enable_heartbeat
from hmt_crypto.exchange.delta.websocket_contracts import (
    WsCandleResolution,
    WsChannel,
    WsPlane,
)
from hmt_crypto.exchange.delta.websocket_public_parsers import DeltaPublicFrameParser
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    GenerationControlInvalidation,
    GenerationSubscriptionReceipt,
    SubscriptionRegistry,
    WsSubscription,
    desired_subscriptions,
)
from hmt_crypto.exchange.delta.websocket_transport import InboundKind
from hmt_crypto.exchange.delta.wire_schemas import parse_product
from hmt_crypto.market_data.public_stream_models import (
    CatalogueRefreshMailbox,
    PublicCatalogueBinding,
)
from hmt_crypto.market_data.readiness import MarketFreshnessPolicy
from hmt_crypto.market_data.state import MarketStateStore

FIXTURES = Path(__file__).parents[1] / "fixtures" / "delta"
GENERATION = UUID("10000000-0000-0000-0000-000000000001")
CATALOGUE_ID = UUID("20000000-0000-0000-0000-000000000002")


def _catalogue() -> CatalogSnapshot:
    rows = json.loads((FIXTURES / "products_catalog_valid.json").read_text())
    products = tuple(parse_product(row, endpoint_id="actor_test") for row in rows)
    now = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    return CatalogSnapshot(
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


class ManualTime:
    def __init__(self) -> None:
        self.now = 1_000_000_000
        self.waiters: list[tuple[int, asyncio.Future[None]]] = []
        self.delays: list[int] = []

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep_ns(self, delay_ns: int) -> None:
        self.delays.append(delay_ns)
        target = self.now + delay_ns
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((target, future))
        await future

    def advance(self, delay_ns: int) -> None:
        self.now += delay_ns
        for target, future in tuple(self.waiters):
            if target <= self.now and not future.done():
                future.set_result(None)
        self.waiters = [(target, future) for target, future in self.waiters if not future.done()]


class Receiver:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str | bytes | BaseException] = asyncio.Queue()
        self.active = 0
        self.max_active = 0

    async def recv(self) -> str | bytes:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            value = await self.queue.get()
        finally:
            self.active -= 1
        if isinstance(value, BaseException):
            raise value
        return value

    async def feed(self, *frames: str | bytes | BaseException) -> None:
        for frame in frames:
            await self.queue.put(frame)


class Control:
    def __init__(self, *, first_sent: str | None = None) -> None:
        self.events: list[tuple[str, object]] = []
        self.sent: list[str] = [] if first_sent is None else [first_sent]
        self.inbound: list[InboundKind] = []
        self.invalidations: list[GenerationControlInvalidation] = []
        self.receipts: list[GenerationSubscriptionReceipt] = []
        self.send_gate: asyncio.Event | None = None

    async def send(self, message: str) -> None:
        if self.send_gate is not None:
            await self.send_gate.wait()
        self.events.append(("send", message))
        self.sent.append(message)

    def observe_inbound(self, kind: InboundKind) -> None:
        self.inbound.append(kind)

    async def invalidate(self, receipt: GenerationControlInvalidation) -> None:
        self.events.append(("invalidate", receipt))
        self.invalidations.append(receipt)

    async def subscriptions_accepted(self, receipt: GenerationSubscriptionReceipt) -> None:
        self.events.append(("receipt", receipt))
        self.receipts.append(receipt)


class CatalogueProvider:
    def __init__(self):
        self.binding = PublicCatalogueBinding(
            catalogue=_catalogue(),
            catalogue_snapshot_id=CATALOGUE_ID,
            spot_asset_ids={".DEUSDTUSD": 12},
        )

    def current(self):
        return self.binding


RefreshPort = CatalogueRefreshMailbox


class HostileRefresh:
    def __getattribute__(self, name: str) -> object:
        if name == "request_catalogue_refresh":
            raise BaseException("structural-refresh-secret")
        return object.__getattribute__(self, name)

    def __repr__(self) -> str:
        return "<structural-refresh-secret>"


def _source_trace(error: BaseException) -> str:
    rendered: list[str] = [repr(error), repr(error.__cause__), repr(error.__context__)]
    traceback = error.__traceback__
    while traceback is not None:
        if "/src/hmt_crypto/" in traceback.tb_frame.f_code.co_filename:
            for value in traceback.tb_frame.f_locals.values():
                try:
                    rendered.append(repr(value))
                except BaseException:
                    rendered.append("<unprintable>")
        traceback = traceback.tb_next
    return "\n".join(rendered)


async def _settle() -> None:
    for _ in range(100):
        await asyncio.sleep(0)


def _ack(*entries: WsSubscription) -> str:
    return json.dumps(
        {
            "type": "subscriptions",
            "channels": [
                {"name": entry.channel, "symbols": list(entry.symbols)}
                if entry.symbols
                else {"name": entry.channel}
                for entry in entries
            ],
        },
        separators=(",", ":"),
    )


def _generation():
    from hmt_crypto.market_data.public_stream import PublicConnectionGeneration

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


def _actor(
    registry: SubscriptionRegistry,
    time: ManualTime,
    *,
    refresh: RefreshPort | None = None,
    provider: CatalogueProvider | None = None,
    state: MarketStateStore | None = None,
):
    from hmt_crypto.market_data.public_stream import PublicStreamActor

    return PublicStreamActor(
        registry=registry,
        parser=DeltaPublicFrameParser(),
        catalogue_provider=provider or CatalogueProvider(),
        catalogue_refresh=refresh or RefreshPort(),
        state=state
        or MarketStateStore(
            freshness_policy=MarketFreshnessPolicy(), monotonic_ns=time.monotonic_ns
        ),
        monotonic_ns=time.monotonic_ns,
        sleep_ns=time.sleep_ns,
    )


def _ticker_frame(*, product_id: int = 1101, symbol: str = "XBTUSD_CAT") -> str:
    return (
        f'{{"type":"ticker","ts":1793872800000000,"sy":"{symbol}",'
        f'"sp":"100.10","d":[{{"i":{product_id},"s":"{symbol}",'
        '"m":"100.00","m24hc":"1.5","ohlc":["90","110","80","100"],'
        '"oi":["3","1"],"pb":["70","130"],'
        '"q":["99.5","2","100.5","3","100"],'
        '"qiv":["0.4","0.5","0.45"],"to":["500","20"],'
        '"g":["0.1","0.2","0.3","0.4","0.5"]}]}'
    )


def _ticker_view(state: MarketStateStore):
    from hmt_crypto.exchange.base import MarketComponent
    from hmt_crypto.market_data.readiness import (
        FreshnessRequirement,
        MarketReadinessEvidence,
        MarketRequirements,
    )

    return state.snapshot(
        MarketRequirements(
            product_ids=(1101,),
            components=(FreshnessRequirement(component=MarketComponent.TICKER),),
        ),
        evidence=MarketReadinessEvidence(
            connection_generation=GENERATION,
            catalogue_snapshot_id=CATALOGUE_ID,
            catalogue_fresh=True,
            supervisor_barrier_current=True,
        ),
    )


def test_symbol_admission_uses_exact_raw_mark_candle_and_symbol_less_contracts() -> None:
    from hmt_crypto.market_data.public_stream_support import is_subscribed

    mark = WsSubscription.for_channel(WsChannel.MARK_PRICE, symbols=("XBTUSD_CAT",))
    candle = WsSubscription.for_candles(
        WsCandleResolution.ONE_MINUTE,
        symbols=("XBTUSD_CAT",),
    )
    system = WsSubscription.for_channel(WsChannel.SYSTEM_STATUS, symbols=())
    desired = desired_subscriptions(revision=1, entries=(mark, candle, system))

    assert is_subscribed({"type": "mark_price", "sy": "MARK:XBTUSD_CAT"}, desired)
    assert not is_subscribed({"type": "mark_price", "sy": "MARK:ETHUSD_CAT"}, desired)
    assert is_subscribed({"type": "candlestick_1m", "sy": "XBTUSD_CAT"}, desired)
    assert not is_subscribed({"type": "candlestick_1m", "sy": "MARK:XBTUSD_CAT"}, desired)
    assert is_subscribed({"type": "system_status"}, desired)


def test_structural_refresh_is_rejected_without_method_lookup_or_secret_trace() -> None:
    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    hostile = HostileRefresh()

    with pytest.raises(TypeError) as captured:
        _actor(registry, time, refresh=hostile)  # type: ignore[arg-type]

    assert "structural-refresh-secret" not in _source_trace(captured.value)


@pytest.mark.asyncio
async def test_initial_ack_and_live_data_use_receive_only_capability_without_auth() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    receiver = Receiver()
    control = Control(first_sent=encode_enable_heartbeat())
    await receiver.feed(_ack(ticker), _ticker_frame(), EOFError())

    result = await run_scripted_generation(
        _actor(registry, time), _generation(), receiver=receiver, control=control
    )
    await lease.aclose()

    assert result.status is PublicGenerationStatus.RECEIVER_ENDED
    assert result.observations_applied == 1
    assert control.sent[0] == encode_enable_heartbeat()
    assert json.loads(control.sent[1])["type"] == "subscribe"
    assert len(control.receipts) == 1
    assert control.receipts[0].connection_generation == GENERATION
    assert InboundKind.DATA in control.inbound
    assert receiver.max_active == 1
    assert not any("auth" in message.casefold() for message in control.sent)


@pytest.mark.asyncio
async def test_registry_change_without_inbound_frames_invalidates_before_dynamic_send() -> None:
    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    first = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    first_lease = await registry.acquire(owner_id=UUID(int=1), entries=(first,))
    receiver = Receiver()
    control = Control()
    await receiver.feed(_ack(first))
    task = asyncio.create_task(
        run_scripted_generation(
            _actor(registry, time), _generation(), receiver=receiver, control=control
        )
    )
    await _settle()
    assert len(control.receipts) == 1

    second = WsSubscription.for_channel(WsChannel.TICKER, symbols=("ETHUSD_CAT",))
    second_lease = await registry.acquire(owner_id=UUID(int=2), entries=(second,))
    time.advance(100_000_000)
    await _settle()
    combined = WsSubscription.for_channel(WsChannel.TICKER, symbols=("ETHUSD_CAT", "XBTUSD_CAT"))
    assert control.events[-2][0] == "invalidate"
    assert control.events[-1][0] == "send"
    await receiver.feed(_ticker_frame(), _ack(combined), EOFError())
    result = await task

    assert len(control.receipts) == 2
    assert result.observations_applied == 1
    assert control.receipts[-1].desired_revision == 2
    assert receiver.max_active == 1
    await first_lease.aclose()
    await second_lease.aclose()


@pytest.mark.asyncio
async def test_heartbeats_do_not_extend_initial_ack_deadline() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    receiver = Receiver()
    control = Control()
    await receiver.feed('{"type":"heartbeat"}', '{"type":"pong"}')
    task = asyncio.create_task(
        run_scripted_generation(
            _actor(registry, time), _generation(), receiver=receiver, control=control
        )
    )
    await _settle()
    time.advance(2_000_000_000)
    result = await task
    await lease.aclose()

    assert result.status is PublicGenerationStatus.CONTROL_TIMEOUT
    assert InboundKind.HEARTBEAT in control.inbound
    assert InboundKind.PONG in control.inbound
    assert not control.receipts
    assert control.invalidations


@pytest.mark.asyncio
async def test_book_gap_repairs_only_symbol_after_five_seconds_and_folds_release() -> None:
    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    xbt = WsSubscription.for_channel(WsChannel.OB_UPDATES, symbols=("XBTUSD_CAT",))
    eth = WsSubscription.for_channel(WsChannel.OB_UPDATES, symbols=("ETHUSD_CAT",))
    xbt_lease = await registry.acquire(owner_id=UUID(int=1), entries=(xbt,))
    eth_lease = await registry.acquire(owner_id=UUID(int=2), entries=(eth,))
    desired = WsSubscription.for_channel(WsChannel.OB_UPDATES, symbols=("ETHUSD_CAT", "XBTUSD_CAT"))
    receiver = Receiver()
    control = Control()
    await receiver.feed(
        _ack(desired),
        '{"type":"ob_updates","action":"snapshot",'
        '"a":[["100.00","2"],["101","1"]],'
        '"b":[["99","3"],["98.00","4"]],'
        '"ts":1793872800000000,"seq":5,"sy":"XBTUSD_CAT","cs":772313654}',
        '{"type":"ob_updates","action":"update","a":[["101","5"]],'
        '"b":[["98.00","0"],["97","1"]],'
        '"ts":1793872800000001,"seq":7,"sy":"XBTUSD_CAT","cs":863104686}',
    )
    task = asyncio.create_task(
        run_scripted_generation(
            _actor(registry, time), _generation(), receiver=receiver, control=control
        )
    )
    await _settle()
    assert control.invalidations[-1].kind.value == "book_gap"
    assert json.loads(control.sent[-1])["type"] == "unsubscribe"
    await receiver.feed(_ack(eth))
    await _settle()
    await xbt_lease.aclose()
    time.advance(5_000_000_000)
    await _settle()
    assert 5_000_000_000 in time.delays
    assert len(control.sent) == 2
    await receiver.feed(EOFError())
    await task

    assert len(control.receipts) == 2
    invalidate_index = next(
        index for index, event in enumerate(control.events) if event[0] == "invalidate"
    )
    assert control.events[invalidate_index + 1][0] == "send"
    await eth_lease.aclose()


@pytest.mark.asyncio
async def test_catalogue_mismatch_requests_refresh_and_closes_generation_safely() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    receiver = Receiver()
    control = Control()
    refresh = RefreshPort()
    actor = _actor(registry, time, refresh=refresh)
    await receiver.feed(_ack(ticker), _ticker_frame(product_id=9999, symbol="XBTUSD_CAT"))
    result = await run_scripted_generation(actor, _generation(), receiver=receiver, control=control)
    await lease.aclose()

    assert result.status is PublicGenerationStatus.CATALOGUE_MISMATCH
    assert len(refresh.requests) == 1
    assert refresh.requests[0].channel == "ticker"
    assert control.invalidations[-1].kind.value == "catalogue_mismatch"


@pytest.mark.asyncio
async def test_catalogue_mismatch_poisons_only_identified_component_before_reconnect() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    receiver = Receiver()
    control = Control()
    state = MarketStateStore(
        freshness_policy=MarketFreshnessPolicy(), monotonic_ns=time.monotonic_ns
    )
    await receiver.feed(
        _ack(ticker),
        _ticker_frame(),
        _ticker_frame(product_id=9999, symbol="XBTUSD_CAT"),
    )
    result = await run_scripted_generation(
        _actor(registry, time, state=state),
        _generation(),
        receiver=receiver,
        control=control,
    )
    await lease.aclose()

    assert result.status is PublicGenerationStatus.CATALOGUE_MISMATCH
    assert result.observations_applied == 1
    assert "catalogue_identity_mismatch" in _ticker_view(state).reasons


@pytest.mark.asyncio
async def test_send_completion_is_bounded_and_actor_cancellation_cleans_receive_task() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    receiver = Receiver()
    control = Control()
    control.send_gate = asyncio.Event()
    task = asyncio.create_task(
        run_scripted_generation(
            _actor(registry, time), _generation(), receiver=receiver, control=control
        )
    )
    await _settle()
    time.advance(2_000_000_000)
    result = await task
    assert result.status is PublicGenerationStatus.CONTROL_TIMEOUT

    control.send_gate.set()
    receiver2 = Receiver()
    control2 = Control()
    state2 = MarketStateStore(
        freshness_policy=MarketFreshnessPolicy(), monotonic_ns=time.monotonic_ns
    )
    await receiver2.feed(_ack(ticker), _ticker_frame())
    task2 = asyncio.create_task(
        run_scripted_generation(
            _actor(registry, time, state=state2),
            _generation(),
            receiver=receiver2,
            control=control2,
        )
    )
    await _settle()
    task2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task2
    await _settle()
    assert receiver2.active == 0
    assert "generation_invalidated" in _ticker_view(state2).reasons
    await lease.aclose()


@pytest.mark.asyncio
async def test_interleaved_current_data_is_applied_while_fixed_ack_deadline_remains_open() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    receiver = Receiver()
    control = Control()
    await receiver.feed(_ticker_frame(), _ack(ticker), EOFError())

    result = await run_scripted_generation(
        _actor(registry, time), _generation(), receiver=receiver, control=control
    )
    await lease.aclose()

    assert result.status is PublicGenerationStatus.RECEIVER_ENDED
    assert result.observations_applied == 1
    assert len(control.receipts) == 1


@pytest.mark.asyncio
async def test_catalogue_valid_but_unsubscribed_symbol_is_rejected_before_publication() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    receiver = Receiver()
    control = Control()
    await receiver.feed(
        _ack(ticker),
        _ticker_frame(product_id=1102, symbol="ETHUSD_CAT"),
        EOFError(),
    )
    result = await run_scripted_generation(
        _actor(registry, time), _generation(), receiver=receiver, control=control
    )
    await lease.aclose()

    assert result.status is PublicGenerationStatus.PROTOCOL_DRIFT
    assert result.observations_applied == 0


@pytest.mark.asyncio
async def test_catalogue_publication_is_bound_once_for_connection_generation() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    receiver = Receiver()
    control = Control()
    provider = CatalogueProvider()
    refresh = RefreshPort()
    await receiver.feed(_ack(ticker))
    task = asyncio.create_task(
        run_scripted_generation(
            _actor(registry, time, provider=provider, refresh=refresh),
            _generation(),
            receiver=receiver,
            control=control,
        )
    )
    await _settle()
    provider.binding = PublicCatalogueBinding(
        catalogue=_catalogue(),
        catalogue_snapshot_id=UUID("40000000-0000-0000-0000-000000000004"),
        spot_asset_ids={".DEUSDTUSD": 12},
    )
    await receiver.feed(_ticker_frame(), EOFError())
    result = await task
    await lease.aclose()

    assert result.status is PublicGenerationStatus.CATALOGUE_MISMATCH
    assert len(refresh.requests) == 1
    assert result.observations_applied == 0


@pytest.mark.asyncio
async def test_receiver_end_invalidates_state_before_returning_terminal_result() -> None:
    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    ticker = WsSubscription.for_channel(WsChannel.TICKER, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(ticker,))
    receiver = Receiver()
    control = Control()
    state = MarketStateStore(
        freshness_policy=MarketFreshnessPolicy(), monotonic_ns=time.monotonic_ns
    )
    await receiver.feed(_ack(ticker), _ticker_frame(), EOFError())
    await run_scripted_generation(
        _actor(registry, time, state=state),
        _generation(),
        receiver=receiver,
        control=control,
    )
    await lease.aclose()

    view = _ticker_view(state)
    assert not view.usable
    assert "generation_invalidated" in view.reasons


@pytest.mark.asyncio
async def test_repair_rejects_early_returning_sleep_without_resubscribing() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    class EarlyRepairTime(ManualTime):
        async def sleep_ns(self, delay_ns: int) -> None:
            self.delays.append(delay_ns)
            if delay_ns == 5_000_000_000:
                return
            await super().sleep_ns(delay_ns)

    time = EarlyRepairTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    book = WsSubscription.for_channel(WsChannel.OB_UPDATES, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(book,))
    receiver = Receiver()
    control = Control()
    await receiver.feed(
        _ack(book),
        '{"type":"ob_updates","action":"error","symbol":"XBTUSD_CAT","msg":"snapshot unavailable"}',
        _ack(),
        _ack(book),
        EOFError(),
    )
    result = await run_scripted_generation(
        _actor(registry, time), _generation(), receiver=receiver, control=control
    )
    await lease.aclose()

    assert result.status is PublicGenerationStatus.CONTROL_TIMEOUT
    assert len(control.sent) == 2


@pytest.mark.asyncio
async def test_repair_hang_is_cut_off_by_independent_safety_deadline() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    class HangingRepairTime(ManualTime):
        async def sleep_ns(self, delay_ns: int) -> None:
            self.delays.append(delay_ns)
            if delay_ns == 5_000_000_000:
                await asyncio.Event().wait()
            await super().sleep_ns(delay_ns)

    time = HangingRepairTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    book = WsSubscription.for_channel(WsChannel.OB_UPDATES, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(book,))
    receiver = Receiver()
    control = Control()
    await receiver.feed(
        _ack(book),
        '{"type":"ob_updates","action":"error","symbol":"XBTUSD_CAT","msg":"snapshot unavailable"}',
        _ack(),
    )
    task = asyncio.create_task(
        run_scripted_generation(
            _actor(registry, time), _generation(), receiver=receiver, control=control
        )
    )
    await _settle()
    time.advance(7_000_000_000)
    result = await task
    await lease.aclose()

    assert result.status is PublicGenerationStatus.CONTROL_TIMEOUT
    assert len(control.sent) == 2


@pytest.mark.asyncio
async def test_repair_of_released_sole_symbol_finishes_with_empty_desired_set() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    book = WsSubscription.for_channel(WsChannel.OB_UPDATES, symbols=("XBTUSD_CAT",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(book,))
    receiver = Receiver()
    control = Control()
    await receiver.feed(
        _ack(book),
        '{"type":"ob_updates","action":"error","symbol":"XBTUSD_CAT","msg":"snapshot unavailable"}',
        _ack(),
    )
    task = asyncio.create_task(
        run_scripted_generation(
            _actor(registry, time), _generation(), receiver=receiver, control=control
        )
    )
    await _settle()
    await lease.aclose()
    time.advance(5_000_000_000)
    await _settle()
    await receiver.feed(EOFError())
    result = await task

    assert result.status is PublicGenerationStatus.RECEIVER_ENDED
    assert result.desired_revision == 2
    assert control.receipts[-1].evidence_sha256
    assert len(control.sent) == 2


@pytest.mark.asyncio
async def test_non_ticker_catalogue_mismatch_schedules_channel_refresh() -> None:
    from hmt_crypto.market_data.public_stream import PublicGenerationStatus

    time = ManualTime()
    registry = SubscriptionRegistry(plane=WsPlane.PUBLIC)
    trades = WsSubscription.for_channel(WsChannel.TRADES, symbols=("UNKNOWN",))
    lease = await registry.acquire(owner_id=UUID(int=1), entries=(trades,))
    receiver = Receiver()
    control = Control()
    refresh = RefreshPort()
    await receiver.feed(
        _ack(trades),
        '{"type":"trades","sy":"UNKNOWN","ts":1793872800000000,'
        '"t":1793872799000000,"p":"100","s":"2","r":"m"}',
    )
    result = await run_scripted_generation(
        _actor(registry, time, refresh=refresh),
        _generation(),
        receiver=receiver,
        control=control,
    )
    await lease.aclose()

    assert result.status is PublicGenerationStatus.CATALOGUE_MISMATCH
    assert refresh.requests[0].channel == "trades"
