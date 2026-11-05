"""One-generation public actor orchestration over bounded control mechanics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import cast

from hmt_crypto.errors import ExchangeDownError, ExchangeProtocolError
from hmt_crypto.exchange.delta.websocket_codec import encode_subscribe, encode_unsubscribe
from hmt_crypto.exchange.delta.websocket_contracts import WsChannel, WsPlane
from hmt_crypto.exchange.delta.websocket_public_parsers import (
    DeltaPublicFrameParser,
    PublicParseContext,
)
from hmt_crypto.exchange.delta.websocket_subscriptions import (
    DesiredSubscriptions,
    GenerationSubscriptionReceipt,
    StreamInvalidationKind,
    SubscriptionRegistry,
    WsSubscription,
    desired_subscriptions,
    parse_subscriptions_ack,
)
from hmt_crypto.exchange.delta.websocket_transport import StreamControlPort, WsReceivePort
from hmt_crypto.market_data import public_stream_support as _support
from hmt_crypto.market_data.contracts import (
    BookStreamError,
    CatalogueMismatchSignal,
    MarketObservation,
)
from hmt_crypto.market_data.orderbook import BookInvalidationReason, BookRecoveryAction
from hmt_crypto.market_data.public_stream_control import PublicControlSession
from hmt_crypto.market_data.public_stream_models import (
    CatalogueRefreshPort,
    CatalogueRefreshRequest,
    MonotonicNs,
    PublicCatalogueBinding,
    PublicCatalogueProvider,
    PublicConnectionGeneration,
    PublicGenerationResult,
    PublicGenerationStatus,
    SleepNs,
)
from hmt_crypto.market_data.state import MarketStateStore


class PublicGenerationRunner:
    """Compose parsing, state, registry and control for one opened connection."""

    def __init__(
        self,
        *,
        generation: PublicConnectionGeneration,
        receiver: WsReceivePort,
        control: StreamControlPort,
        registry: SubscriptionRegistry,
        parser: DeltaPublicFrameParser,
        catalogue_provider: PublicCatalogueProvider,
        catalogue_refresh: CatalogueRefreshPort,
        state: MarketStateStore,
        monotonic_ns: MonotonicNs,
        sleep_ns: SleepNs,
        utc_now: Callable[[], datetime],
    ) -> None:
        if type(generation) is not PublicConnectionGeneration:
            raise TypeError("public connection generation is invalid")
        self.generation = generation
        self.registry = registry
        self.parser = parser
        self.catalogue_provider = catalogue_provider
        self.catalogue_refresh = catalogue_refresh
        self.state = state
        self.utc_now = utc_now
        self.session = PublicControlSession(
            generation=generation,
            receiver=receiver,
            control=control,
            monotonic_ns=monotonic_ns,
            sleep_ns=sleep_ns,
        )
        self.binding: PublicCatalogueBinding | None = None
        self.local_sequence = 1
        self.observations_applied = 0
        self.desired_revision = 0
        self.finalization_deadline: int | None = None

    def _context(self) -> PublicParseContext:
        if self.binding is None:
            raise ExchangeProtocolError(
                context={"operation": "public_catalogue_binding", "scope": "public"}
            )
        return PublicParseContext(
            catalogue=self.binding.catalogue,
            catalogue_snapshot_id=self.binding.catalogue_snapshot_id,
            connection_generation=self.generation.connection_generation,
            local_sequence_start=self.local_sequence,
            received_at=self.utc_now(),
            spot_asset_ids=self.binding.spot_asset_ids,
        )

    async def _request_catalogue_refresh(
        self,
        *,
        channel: str,
        desired: DesiredSubscriptions,
        signal: CatalogueMismatchSignal | None = None,
    ) -> None:
        if self.binding is None:
            raise ExchangeProtocolError(
                context={"operation": "public_catalogue_binding", "scope": "public"}
            )
        if signal is not None:
            self.state.poison_catalogue_mismatch(signal.product_id, signal.source.channel)
        await self.session.invalidate(StreamInvalidationKind.CATALOGUE_MISMATCH, desired)
        request = CatalogueRefreshRequest(
            connection_generation=self.generation.connection_generation,
            catalogue_snapshot_id=self.binding.catalogue_snapshot_id,
            channel=channel,
        )
        await self.session.bounded(
            self.catalogue_refresh.request_catalogue_refresh(request),
            deadline=self.session.deadline(),
        )
        raise _support.CatalogueMismatch

    async def _check_binding(self, channel: str, desired: DesiredSubscriptions) -> None:
        current = _support.require_binding(self.catalogue_provider, self.generation)
        if self.binding != current:
            await self._request_catalogue_refresh(channel=channel, desired=desired)

    async def _await_ack(
        self,
        expected: DesiredSubscriptions,
        *,
        data_set: DesiredSubscriptions,
    ) -> None:
        deadline = self.session.deadline()
        while True:
            message = self.session.decode(await self.session.receive_before(deadline))
            message_type = message.get("type")
            if message_type in {"heartbeat", "pong"}:
                continue
            if message_type == "subscriptions":
                parse_subscriptions_ack(message, expected=expected)
                return
            processed = await self._process_data(message, data_set, allow_repair=False)
            if processed != data_set:
                raise ExchangeProtocolError(
                    context={"operation": "public_subscription_ack", "scope": "public"}
                )

    async def _publish_receipt(self, desired: DesiredSubscriptions) -> bool:
        current = await self.registry.snapshot()
        if current != desired:
            return False
        receipt = GenerationSubscriptionReceipt(
            plane=WsPlane.PUBLIC,
            connection_generation=self.generation.connection_generation,
            control_revision=self.session.control_revision,
            desired_revision=desired.revision,
            evidence_sha256=desired.evidence_sha256,
            accepted_at_monotonic_ns=self.session.now(),
        )
        await self.session.bounded(
            self.session.control.subscriptions_accepted(receipt),
            deadline=self.session.deadline(),
        )
        self.desired_revision = desired.revision
        return True

    async def _transition(
        self,
        current: DesiredSubscriptions,
        target: DesiredSubscriptions,
        *,
        invalidation: StreamInvalidationKind | None,
        publish_receipt: bool = True,
    ) -> DesiredSubscriptions:
        removed, intermediate, added = _support.transition_changes(current, target)
        if invalidation is not None:
            await self.session.invalidate(invalidation, current)
        sent = False
        server_state = current
        if removed:
            await self.session.send(encode_unsubscribe(removed))
            await self._await_ack(intermediate, data_set=current)
            server_state = intermediate
            sent = True
        if added:
            await self.session.send(encode_subscribe(added))
            await self._await_ack(target, data_set=target)
            server_state = target
            sent = True
        if not sent and current.entries == target.entries:
            server_state = target
        if server_state != target:
            raise ExchangeProtocolError(
                context={"operation": "public_subscription_transition", "scope": "public"}
            )
        if publish_receipt:
            await self._publish_receipt(target)
        return target

    async def _initial_subscribe(self) -> DesiredSubscriptions:
        desired = await self.registry.snapshot()
        if not desired.entries:
            raise ExchangeProtocolError(
                context={"operation": "public_subscription_initial", "scope": "public"}
            )
        return await self._transition(
            _support.empty_desired(desired.revision),
            desired,
            invalidation=None,
        )

    async def _sync_registry(self, current: DesiredSubscriptions) -> DesiredSubscriptions:
        while True:
            target = await self.registry.snapshot()
            if target == current:
                return current
            current = await self._transition(
                current,
                target,
                invalidation=StreamInvalidationKind.SUBSCRIPTION_CHANGE,
            )
            if await self.registry.snapshot() == current:
                return current

    async def _repair_book(
        self,
        current: DesiredSubscriptions,
        *,
        symbol: str,
    ) -> DesiredSubscriptions:
        reduced_entries: list[WsSubscription] = []
        found = False
        for entry in current.entries:
            if entry.channel != WsChannel.OB_UPDATES.value:
                reduced_entries.append(entry)
                continue
            remaining = tuple(item for item in entry.symbols if item != symbol)
            found = found or len(remaining) != len(entry.symbols)
            if remaining:
                reduced_entries.append(WsSubscription(channel=entry.channel, symbols=remaining))
        if not found:
            raise ExchangeProtocolError(
                context={"operation": "public_book_repair", "scope": "public"}
            )
        reduced = desired_subscriptions(
            revision=current.revision,
            entries=tuple(reduced_entries),
        )
        reduced = await self._transition(
            current,
            reduced,
            invalidation=StreamInvalidationKind.BOOK_GAP,
            publish_receipt=False,
        )
        self.session.ensure_receive()
        await self.session.wait_repair_minimum()
        latest = await self.registry.snapshot()
        return await self._transition(reduced, latest, invalidation=None)

    async def _process_data(
        self,
        message: dict[str, object],
        current: DesiredSubscriptions,
        *,
        allow_repair: bool = True,
    ) -> DesiredSubscriptions:
        if not _support.is_subscribed(message, current):
            raise ExchangeProtocolError(
                context={"operation": "public_unsolicited_channel", "scope": "public"}
            )
        channel = message.get("type")
        if type(channel) is not str:
            raise ExchangeProtocolError(
                context={"operation": "public_unsolicited_channel", "scope": "public"}
            )
        await self._check_binding(channel, current)
        outcomes = self.parser.parse(message, context=self._context())
        self.local_sequence += len(outcomes)
        for outcome in outcomes:
            if type(outcome) is CatalogueMismatchSignal:
                await self._request_catalogue_refresh(
                    channel=outcome.source.channel,
                    desired=current,
                    signal=outcome,
                )
            if type(outcome) is BookStreamError:
                self.state.invalidate_book(
                    outcome.product_id,
                    BookInvalidationReason.VENUE_ERROR,
                )
                if not allow_repair:
                    raise ExchangeProtocolError(
                        context={"operation": "public_book_repair", "scope": "public"}
                    )
                return await self._repair_book(current, symbol=outcome.symbol)
            observation = cast(MarketObservation, outcome)
            publication = self.state.apply(observation)
            self.observations_applied += int(publication.applied)
            if (
                publication.book_recovery_action
                is BookRecoveryAction.RESUBSCRIBE_SYMBOL_AFTER_5_SECONDS
            ):
                if not allow_repair or observation.source.symbol is None:
                    raise ExchangeProtocolError(
                        context={"operation": "public_book_repair", "scope": "public"}
                    )
                return await self._repair_book(current, symbol=observation.source.symbol)
        return current

    def _result(self, status: PublicGenerationStatus) -> PublicGenerationResult:
        return _support.generation_result(
            status=status,
            generation=self.generation,
            observations_applied=self.observations_applied,
            desired_revision=self.desired_revision,
        )

    def _begin_finalization(self) -> int:
        if self.finalization_deadline is None:
            self.finalization_deadline = self.session.deadline(
                after_ns=self.generation.finalization_timeout_ns
            )
        return self.finalization_deadline

    async def _terminal_control(self, current: DesiredSubscriptions, *, deadline: int) -> None:
        try:
            await self.session.invalidate(
                StreamInvalidationKind.PROTOCOL_DRIFT,
                current,
                deadline=deadline,
            )
        except (ExchangeDownError, ExchangeProtocolError, _support.ControlTimeout):
            return

    async def _terminal_result(
        self,
        status: PublicGenerationStatus,
        current: DesiredSubscriptions,
        *,
        reason: str,
        control_invalidation: bool = True,
    ) -> PublicGenerationResult:
        self.state.invalidate_generation(self.generation.connection_generation, reason)
        deadline = self._begin_finalization()
        if control_invalidation:
            await self._terminal_control(current, deadline=deadline)
        return self._result(status)

    async def run(self) -> PublicGenerationResult:
        """Run until one fail-closed terminal result or caller cancellation."""
        current = _support.empty_desired(0)
        try:
            self.binding = _support.require_binding(self.catalogue_provider, self.generation)
            current = await self._initial_subscribe()
            while True:
                frame = await self.session.receive_or_poll()
                if frame is None:
                    await self._check_binding("catalogue_binding", current)
                    current = await self._sync_registry(current)
                    continue
                message = self.session.decode(frame)
                message_type = message.get("type")
                if message_type in {"heartbeat", "pong"}:
                    continue
                if message_type == "subscriptions":
                    raise ExchangeProtocolError(
                        context={"operation": "public_unsolicited_ack", "scope": "public"}
                    )
                current = await self._process_data(message, current)
        except _support.ReceiverEnded:
            return await self._terminal_result(
                PublicGenerationStatus.RECEIVER_ENDED,
                current,
                reason="receiver_ended",
            )
        except _support.ControlTimeout:
            return await self._terminal_result(
                PublicGenerationStatus.CONTROL_TIMEOUT,
                current,
                reason="control_timeout",
            )
        except _support.CatalogueMismatch:
            return await self._terminal_result(
                PublicGenerationStatus.CATALOGUE_MISMATCH,
                current,
                reason="catalogue_mismatch",
                control_invalidation=False,
            )
        except ExchangeDownError:
            return await self._terminal_result(
                PublicGenerationStatus.RECEIVER_ENDED,
                current,
                reason="transport_down",
            )
        except (ExchangeProtocolError, ValueError, TypeError):
            return await self._terminal_result(
                PublicGenerationStatus.PROTOCOL_DRIFT,
                current,
                reason="protocol_drift",
            )
        except asyncio.CancelledError:
            self.state.invalidate_generation(self.generation.connection_generation, "cancelled")
            deadline = self._begin_finalization()
            try:
                await asyncio.shield(self._terminal_control(current, deadline=deadline))
            finally:
                raise
        finally:
            await self.session.finish_receive(deadline=self._begin_finalization())


__all__ = ["PublicGenerationRunner"]
