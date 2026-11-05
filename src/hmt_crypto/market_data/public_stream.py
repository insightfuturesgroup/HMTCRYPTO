"""Credential-free public actor facade for one supervisor-opened generation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hmt_crypto.exchange.delta.websocket_public_parsers import DeltaPublicFrameParser
from hmt_crypto.exchange.delta.websocket_subscriptions import SubscriptionRegistry
from hmt_crypto.exchange.delta.websocket_transport import StreamControlPort, WsReceivePort
from hmt_crypto.market_data.public_stream_models import (
    CatalogueRefreshPort,
    MonotonicNs,
    PublicCatalogueProvider,
    PublicConnectionGeneration,
    PublicGenerationResult,
    PublicGenerationStatus,
    SleepNs,
)
from hmt_crypto.market_data.public_stream_runner import PublicGenerationRunner
from hmt_crypto.market_data.state import MarketStateStore


class PublicStreamActor:
    """Run one public connection generation without socket lifecycle authority."""

    def __init__(
        self,
        *,
        registry: SubscriptionRegistry,
        parser: DeltaPublicFrameParser,
        catalogue_provider: PublicCatalogueProvider,
        catalogue_refresh: CatalogueRefreshPort,
        state: MarketStateStore,
        monotonic_ns: MonotonicNs,
        sleep_ns: SleepNs,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            type(registry) is not SubscriptionRegistry
            or type(parser) is not DeltaPublicFrameParser
            or type(state) is not MarketStateStore
            or not callable(monotonic_ns)
            or not callable(sleep_ns)
        ):
            raise TypeError("public stream actor dependencies are invalid")
        self._registry = registry
        self._parser = parser
        self._catalogue_provider = catalogue_provider
        self._catalogue_refresh = catalogue_refresh
        self._state = state
        self._monotonic_ns = monotonic_ns
        self._sleep_ns = sleep_ns
        self._utc_now = utc_now or (lambda: datetime.now(UTC))

    async def run_generation(
        self,
        generation: PublicConnectionGeneration,
        *,
        receiver: WsReceivePort,
        control: StreamControlPort,
    ) -> PublicGenerationResult:
        """Consume an already-open generation; never open, close, or reconnect."""
        runner = PublicGenerationRunner(
            generation=generation,
            receiver=receiver,
            control=control,
            registry=self._registry,
            parser=self._parser,
            catalogue_provider=self._catalogue_provider,
            catalogue_refresh=self._catalogue_refresh,
            state=self._state,
            monotonic_ns=self._monotonic_ns,
            sleep_ns=self._sleep_ns,
            utc_now=self._utc_now,
        )
        return await runner.run()


__all__ = [
    "PublicConnectionGeneration",
    "PublicGenerationResult",
    "PublicGenerationStatus",
    "PublicStreamActor",
]
