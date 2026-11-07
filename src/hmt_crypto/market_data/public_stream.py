"""Credential-free public actor facade for one supervisor-opened generation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from hmt_crypto.errors import ExchangeDownError
from hmt_crypto.exchange.delta.websocket_contracts import WsEndpoint, WsPlane
from hmt_crypto.exchange.delta.websocket_public_parsers import DeltaPublicFrameParser
from hmt_crypto.exchange.delta.websocket_subscriptions import SubscriptionRegistry
from hmt_crypto.exchange.delta.websocket_transport import (
    DeltaWebsocketsConnector,
    StreamControlPort,
    WebSocketAttemptAdmissionError,
    WebSocketHandshakeCategory,
    WebSocketHandshakeError,
    WsReceivePort,
    _is_owned_physical_connector,
)
from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor
from hmt_crypto.market_data.public_stream_models import (
    CatalogueRefreshMailbox,
    MonotonicNs,
    PublicCatalogueProvider,
    PublicConnectionGeneration,
    PublicGenerationResult,
    PublicGenerationStatus,
    SleepNs,
)
from hmt_crypto.market_data.public_stream_runner import _PublicGenerationRunner
from hmt_crypto.market_data.state import MarketStateStore
from hmt_crypto.market_data.stream_control_composition import issue_stream_control_owner
from hmt_crypto.market_data.stream_evidence_mailbox import StreamEvidenceMailbox


class _PhysicalFailure(StrEnum):
    CANCELLED = "cancelled"
    ATTEMPT_ADMISSION = "attempt_admission"
    TRANSPORT = "transport"


@dataclass(frozen=True, slots=True)
class _HandshakeFailure:
    category: WebSocketHandshakeCategory
    status_code: int | None


def _sink_dependencies_valid(sink: object) -> bool:
    return type(sink) is StreamEvidenceMailbox


async def _run_physical_generation(
    actor: PublicStreamActor,
    generation: PublicConnectionGeneration,
    *,
    connector: DeltaWebsocketsConnector,
    endpoint: WsEndpoint,
    attempts: ConnectionAttemptGovernor,
    sink: StreamEvidenceMailbox,
) -> PublicGenerationResult | _PhysicalFailure | _HandshakeFailure:
    if not _sink_dependencies_valid(sink):
        return _PhysicalFailure.TRANSPORT
    try:
        context = DeltaWebsocketsConnector.open(
            connector,
            endpoint,
            attempts=attempts,
        )
        async with context as connection:
            control = issue_stream_control_owner(
                connection=connection,
                sink=sink,
                monotonic_ns=actor._monotonic_ns,
                plane=WsPlane.PUBLIC,
                connection_generation=generation.connection_generation,
            )
            receiver = connection.receiver()
            return await actor._run_owned_generation(
                generation,
                receiver=receiver,
                control=control,
            )
    except asyncio.CancelledError:
        return _PhysicalFailure.CANCELLED
    except WebSocketAttemptAdmissionError:
        return _PhysicalFailure.ATTEMPT_ADMISSION
    except WebSocketHandshakeError as error:
        try:
            if type(error) is not WebSocketHandshakeError:
                return _PhysicalFailure.TRANSPORT
            category = error.category
            status_code = error.status_code
            if type(category) is not WebSocketHandshakeCategory or (
                status_code is not None
                and (type(status_code) is not int or not 100 <= status_code <= 599)
            ):
                return _PhysicalFailure.TRANSPORT
            return _HandshakeFailure(category, status_code)
        except BaseException:
            return _PhysicalFailure.TRANSPORT
    except BaseException:
        return _PhysicalFailure.TRANSPORT


class PublicStreamActor:
    """Run one public connection generation without socket lifecycle authority."""

    def __init__(
        self,
        *,
        registry: SubscriptionRegistry,
        parser: DeltaPublicFrameParser,
        catalogue_provider: PublicCatalogueProvider,
        catalogue_refresh: CatalogueRefreshMailbox,
        state: MarketStateStore,
        monotonic_ns: MonotonicNs,
        sleep_ns: SleepNs,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            type(registry) is not SubscriptionRegistry
            or type(parser) is not DeltaPublicFrameParser
            or type(state) is not MarketStateStore
            or type(catalogue_refresh) is not CatalogueRefreshMailbox
            or not callable(monotonic_ns)
            or not callable(sleep_ns)
            or (utc_now is not None and not callable(utc_now))
        ):
            del (
                registry,
                parser,
                catalogue_provider,
                catalogue_refresh,
                state,
                monotonic_ns,
                sleep_ns,
                utc_now,
            )
            raise TypeError("public stream actor dependencies are invalid") from None
        self._registry = registry
        self._parser = parser
        self._catalogue_provider = catalogue_provider
        self._catalogue_refresh = catalogue_refresh
        self._state = state
        self._monotonic_ns = monotonic_ns
        self._sleep_ns = sleep_ns
        self._utc_now = utc_now or (lambda: datetime.now(UTC))

    async def _run_owned_generation(
        self,
        generation: PublicConnectionGeneration,
        *,
        receiver: WsReceivePort,
        control: StreamControlPort,
    ) -> PublicGenerationResult:
        """Consume exact capabilities issued by the physical-entry composition."""
        runner = _PublicGenerationRunner(
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
            production_owned=True,
        )
        return await runner.run()

    async def run_physical_generation(
        self,
        generation: PublicConnectionGeneration,
        *,
        connector: DeltaWebsocketsConnector,
        endpoint: WsEndpoint,
        attempts: ConnectionAttemptGovernor,
        sink: StreamEvidenceMailbox,
    ) -> PublicGenerationResult:
        """Consume admission immediately before one supervisor-owned open."""
        valid = (
            type(generation) is PublicConnectionGeneration
            and type(endpoint) is WsEndpoint
            and type(attempts) is ConnectionAttemptGovernor
            and _is_owned_physical_connector(connector)
            and endpoint.plane is WsPlane.PUBLIC
            and endpoint.region == generation.region == attempts.region
            and endpoint.environment == generation.environment == attempts.environment
        )
        if not valid:
            del generation, connector, endpoint, attempts, sink
            raise TypeError("public physical stream dependencies are invalid") from None
        outcome = await _run_physical_generation(
            self,
            generation,
            connector=connector,
            endpoint=endpoint,
            attempts=attempts,
            sink=sink,
        )
        del generation, connector, endpoint, attempts, sink
        if outcome is _PhysicalFailure.CANCELLED:
            raise asyncio.CancelledError from None
        if outcome is _PhysicalFailure.ATTEMPT_ADMISSION:
            raise ExchangeDownError(context={"operation": "websocket_attempt_admission"}) from None
        if outcome is _PhysicalFailure.TRANSPORT:
            raise ExchangeDownError(context={"operation": "websocket_open"}) from None
        if isinstance(outcome, _HandshakeFailure):
            raise WebSocketHandshakeError(
                category=outcome.category,
                status_code=outcome.status_code,
            ) from None
        return outcome


__all__ = [
    "PublicConnectionGeneration",
    "PublicGenerationResult",
    "PublicGenerationStatus",
    "PublicStreamActor",
]
