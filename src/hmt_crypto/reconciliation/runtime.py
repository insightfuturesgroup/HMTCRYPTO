"""Bounded read-only account reconciliation and uncertain-order recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from hmt_crypto.domain.account import AccountRef, CollectionMode
from hmt_crypto.domain.private_orders import (
    LatestPositionLookupFound,
    LookupNotFound,
    ObservedLatestPositionLookupResult,
    ObservedOrderLookupResult,
    OrderLookupFound,
    StrictFillPaginationResult,
    StrictOrderPaginationResult,
)
from hmt_crypto.domain.private_positions import ObservedMarginedPositions
from hmt_crypto.domain.private_wallet import ObservedBalanceSnapshot
from hmt_crypto.errors import ExchangeProtocolError, PaginationLimitError
from hmt_crypto.exchange.delta.private_read_client import DeltaIndiaPrivateReadClient
from hmt_crypto.exchange.private_requests import (
    FillQuery,
    LatestPositionQuery,
    MarginedPositionQuery,
    OpenOrderQuery,
    OrderByClientIdQuery,
    OrderByIdQuery,
    OrderHistoryQuery,
)
from hmt_crypto.persistence.db import Database
from hmt_crypto.reconciliation.contracts import (
    LocalAccountProjection,
    LocalOrderTruth,
    ReconciliationDrift,
    ReconciliationDriftKind,
    ReconciliationReceipt,
    ReconciliationTrigger,
    UncertainOrderOutcome,
    UncertainResolution,
)
from hmt_crypto.reconciliation.repository import (
    ReconciliationCheckpointRepository,
    ReconciliationEvidenceLimitError,
    local_projection_sha256,
)


def _same_account(left: AccountRef, right: AccountRef) -> bool:
    return left.model_dump() == right.model_dump()


def _hash_subject(kind: str, value: object) -> str:
    payload = json.dumps(
        {"kind": kind, "value": value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _redact_private_identity(value: object) -> object:
    if type(value) is dict:
        return {
            key: _redact_private_identity(item)
            for key, item in value.items()
            if key not in {"external_user_id", "user_id"}
        }
    if type(value) is list:
        return [_redact_private_identity(item) for item in value]
    return value


def _json_model(value: object) -> object:
    dump = getattr(value, "model_dump", None)
    if not callable(dump):
        raise ValueError("private observation is not serializable")
    return _redact_private_identity(dump(mode="json"))


def _pagination_commitment(value: object) -> dict[str, object]:
    raw = _json_model(value)
    if type(raw) is not dict:
        raise ValueError("private pagination is not serializable")
    items = raw.pop("items", None)
    if type(items) is not list:
        raise ValueError("private pagination items are invalid")
    payload = json.dumps(
        items,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    raw["item_count"] = len(items)
    raw["items_sha256"] = hashlib.sha256(payload).hexdigest()
    return raw


def _observed_account(value: object) -> AccountRef:
    context = getattr(value, "context", None)
    account = getattr(context, "account", None)
    if type(account) is not AccountRef:
        raise ValueError("private observation account is missing")
    return account


def _pagination_account(value: object) -> AccountRef:
    provenance = getattr(value, "provenance", None)
    account = getattr(provenance, "account", None)
    if type(account) is not AccountRef:
        raise ValueError("private pagination account is missing")
    return account


def _lookup_found(value: ObservedOrderLookupResult) -> object | None:
    result = cast(OrderLookupFound | LookupNotFound, value.value)
    if isinstance(result, LookupNotFound):
        return None
    return result.value


def _presence_text(value: object) -> str | None:
    raw = _json_model(value)
    if type(raw) is not dict:
        return None
    candidate = raw.get("value")
    if raw.get("kind") == "VALUE" and type(candidate) is str:
        return candidate
    return None


def _order_identity(order: object) -> tuple[int, str | None, int]:
    order_id = getattr(order, "id", None)
    product_id = getattr(order, "product_id", None)
    if type(order_id) is not int or order_id <= 0 or type(product_id) is not int or product_id <= 0:
        raise ValueError("venue order identity is invalid")
    return order_id, _presence_text(getattr(order, "client_order_id", None)), product_id


def _contains_identity(items: tuple[object, ...], intent: LocalOrderTruth) -> bool:
    for item in items:
        item_product_id = getattr(item, "product_id", None)
        if type(item_product_id) is not int or item_product_id != intent.product_id:
            continue
        client_id = _presence_text(getattr(item, "client_order_id", None))
        direct_id = getattr(item, "id", None)
        direct_order_id = direct_id if type(direct_id) is int else None
        fill_reference = getattr(item, "order_id", None)
        nested_order_id = getattr(fill_reference, "order_id", None)
        fill_order_id = nested_order_id if type(nested_order_id) is int else None
        if intent.client_order_id is not None and client_id == intent.client_order_id:
            return True
        if intent.exchange_order_id is not None and intent.exchange_order_id in (
            direct_order_id,
            fill_order_id,
        ):
            return True
    return False


class AccountReconciliationService:
    """Collect complete strict venue truth and publish one fail-closed durable barrier."""

    def __init__(
        self,
        *,
        database: Database,
        account: AccountRef,
        client: DeltaIndiaPrivateReadClient,
        product_ids: tuple[int, ...],
        clock: Callable[[], datetime] | None = None,
        timeout: timedelta = timedelta(seconds=30),
        freshness_ttl: timedelta = timedelta(seconds=30),
        repository: ReconciliationCheckpointRepository | None = None,
    ) -> None:
        try:
            client_account = object.__getattribute__(client, "_account")
        except Exception:
            client_account = None
        if (
            type(database) is not Database
            or type(account) is not AccountRef
            or type(client) is not DeltaIndiaPrivateReadClient
            or type(client_account) is not AccountRef
            or not _same_account(account, client_account)
            or type(product_ids) is not tuple
            or not 0 <= len(product_ids) <= 4096
            or any(type(item) is not int or not 1 <= item < 2**63 for item in product_ids)
            or len(set(product_ids)) != len(product_ids)
            or type(timeout) is not timedelta
            or not timedelta(seconds=1) <= timeout <= timedelta(minutes=5)
            or type(freshness_ttl) is not timedelta
            or not timedelta(seconds=1) <= freshness_ttl <= timedelta(minutes=10)
            or clock is not None
            and not callable(clock)
        ):
            raise ValueError("invalid reconciliation service binding")
        self._database = database
        self._account = account.model_copy()
        self._client = client
        self._product_ids = tuple(sorted(product_ids))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._timeout = timeout
        self._freshness_ttl = freshness_ttl
        self._repository = repository or ReconciliationCheckpointRepository()
        self._lock = asyncio.Lock()
        self._ready = False
        self._ready_until: datetime | None = None
        self._last_receipt: ReconciliationReceipt | None = None

    @property
    def ready(self) -> bool:
        """Return only the most recent complete drift-free barrier state."""
        if self._ready and self._ready_until is not None:
            try:
                if self._now() < self._ready_until:
                    return True
            except Exception:
                pass
            self._ready = False
            self._ready_until = None
        return self._ready

    @property
    def last_receipt(self) -> ReconciliationReceipt | None:
        """Return the last terminal receipt observed by this service instance."""
        return self._last_receipt

    def _now(self) -> datetime:
        value = self._clock()
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("reconciliation clock must return UTC")
        return value.astimezone(UTC)

    def _publish_receipt(self, receipt: ReconciliationReceipt) -> ReconciliationReceipt:
        now = self._now()
        fresh = (
            receipt.ready
            and receipt.observed_at is not None
            and receipt.observed_at <= now < receipt.observed_at + self._freshness_ttl
        )
        published = receipt if fresh or not receipt.ready else replace(receipt, ready=False)
        self._last_receipt = published
        self._ready = fresh
        self._ready_until = (
            receipt.observed_at + self._freshness_ttl
            if fresh and receipt.observed_at is not None
            else None
        )
        return published

    async def _resolve_uncertain(
        self,
        projection: LocalAccountProjection,
        history: StrictOrderPaginationResult,
        fills: StrictFillPaginationResult,
    ) -> tuple[UncertainOrderOutcome, ...]:
        outcomes: list[UncertainOrderOutcome] = []
        for intent in projection.open_orders:
            if not intent.uncertain:
                continue
            probes: list[tuple[str, object | None]] = []
            if intent.client_order_id is not None:
                observed = await self._client.get_order_by_client_id(
                    OrderByClientIdQuery(client_order_id=intent.client_order_id)
                )
                if type(observed) is not ObservedOrderLookupResult or not _same_account(
                    _observed_account(observed), self._account
                ):
                    raise ValueError("uncertain client-order lookup is invalid")
                probes.append(("client", _lookup_found(observed)))
                if probes[-1][1] is not None:
                    _order_id, _client_id, product_id = _order_identity(probes[-1][1])
                    if product_id != intent.product_id:
                        raise ValueError("uncertain client-order lookup changed product")
            accepted = bool(probes and probes[-1][1] is not None)
            if not accepted and intent.exchange_order_id is not None:
                observed = await self._client.get_order(
                    OrderByIdQuery(order_id=intent.exchange_order_id)
                )
                if type(observed) is not ObservedOrderLookupResult or not _same_account(
                    _observed_account(observed), self._account
                ):
                    raise ValueError("uncertain exchange-order lookup is invalid")
                probes.append(("exchange", _lookup_found(observed)))
                if probes[-1][1] is not None:
                    _order_id, _client_id, product_id = _order_identity(probes[-1][1])
                    if product_id != intent.product_id:
                        raise ValueError("uncertain exchange-order lookup changed product")
                accepted = probes[-1][1] is not None
            if not accepted:
                accepted = _contains_identity(tuple(history.items), intent)
            if not accepted:
                accepted = _contains_identity(tuple(fills.items), intent)
            if accepted:
                resolution = UncertainResolution.ACCEPTED
            else:
                resolution = UncertainResolution.UNRESOLVED
            for attempt_id in intent.uncertain_attempt_ids:
                evidence = _hash_subject(
                    "uncertain_resolution",
                    {
                        "attempt_id": str(attempt_id),
                        "client_order_id": intent.client_order_id,
                        "exchange_order_id": intent.exchange_order_id,
                        "history_complete": history.complete,
                        "fills_complete": fills.complete,
                        "probe_count": len(probes),
                        "resolution": resolution.value,
                    },
                )
                outcomes.append(
                    UncertainOrderOutcome(
                        attempt_id,
                        intent.local_order_id,
                        resolution,
                        evidence,
                    )
                )
        for attempt_id in projection.unbound_uncertain_attempt_ids:
            evidence = _hash_subject(
                "unbound_uncertain_attempt",
                {
                    "attempt_id": str(attempt_id),
                    "history_complete": history.complete,
                    "fills_complete": fills.complete,
                    "resolution": UncertainResolution.UNRESOLVED.value,
                },
            )
            outcomes.append(
                UncertainOrderOutcome(
                    attempt_id,
                    None,
                    UncertainResolution.UNRESOLVED,
                    evidence,
                )
            )
        return tuple(outcomes)

    def _classify(
        self,
        projection: LocalAccountProjection,
        latest_positions: tuple[ObservedLatestPositionLookupResult, ...],
        open_orders: StrictOrderPaginationResult,
        uncertain: tuple[UncertainOrderOutcome, ...],
    ) -> tuple[ReconciliationDrift, ...]:
        venue_orders = tuple(_order_identity(item) for item in open_orders.items)
        local_clients = {
            item.client_order_id for item in projection.open_orders if item.client_order_id
        }
        local_exchange = {
            item.exchange_order_id for item in projection.open_orders if item.exchange_order_id
        }
        drifts: list[ReconciliationDrift] = []
        for exchange_id, client_id, product_id in venue_orders:
            if exchange_id not in local_exchange and (
                client_id is None or client_id not in local_clients
            ):
                drifts.append(
                    ReconciliationDrift(
                        ReconciliationDriftKind.EXTERNAL_OPEN_ORDER,
                        _hash_subject("exchange_order", exchange_id),
                        product_id,
                        True,
                    )
                )
        venue_exchange = {item[0] for item in venue_orders}
        venue_clients = {item[1] for item in venue_orders if item[1] is not None}
        for local in projection.open_orders:
            if local.uncertain:
                continue
            if (
                local.exchange_order_id is None or local.exchange_order_id not in venue_exchange
            ) and (local.client_order_id is None or local.client_order_id not in venue_clients):
                drifts.append(
                    ReconciliationDrift(
                        ReconciliationDriftKind.LOCAL_OPEN_ORDER_MISSING,
                        _hash_subject("local_order", str(local.local_order_id)),
                        local.product_id,
                    )
                )
        venue_positions: dict[int, int] = {}
        for observation in latest_positions:
            found = cast(LatestPositionLookupFound | LookupNotFound, observation.value)
            if isinstance(found, LookupNotFound):
                raise ValueError("latest position lookup is not definitive")
            position = found.value
            if position.product_id in venue_positions:
                raise ValueError("latest position lookup is duplicated")
            if position.signed_contracts != 0:
                venue_positions[position.product_id] = position.signed_contracts
        local_positions = {
            product_id: contracts
            for product_id, contracts in projection.signed_positions
            if contracts != 0
        }
        for product_id in sorted(set(venue_positions) | set(local_positions)):
            if venue_positions.get(product_id, 0) != local_positions.get(product_id, 0):
                venue_size = venue_positions.get(product_id, 0)
                drifts.append(
                    ReconciliationDrift(
                        (
                            ReconciliationDriftKind.EXTERNAL_POSITION
                            if venue_size != 0 and local_positions.get(product_id, 0) == 0
                            else ReconciliationDriftKind.POSITION_MISMATCH
                        ),
                        _hash_subject(
                            "position",
                            {
                                "product_id": product_id,
                                "venue": venue_size,
                                "local": local_positions.get(product_id, 0),
                            },
                        ),
                        product_id,
                        venue_size != 0,
                    )
                )
        for outcome in uncertain:
            drifts.append(
                ReconciliationDrift(
                    ReconciliationDriftKind.UNCERTAIN_OPERATION,
                    outcome.evidence_sha256,
                    None,
                    outcome.resolution is not UncertainResolution.NOT_SENT,
                )
            )
        return tuple(drifts)

    async def _read_current(
        self,
    ) -> tuple[
        ObservedBalanceSnapshot,
        ObservedMarginedPositions,
        StrictOrderPaginationResult,
    ]:
        balances = await self._client.get_balances()
        positions = await self._client.get_margined_positions(MarginedPositionQuery())
        open_orders = await self._client.get_open_orders(
            OpenOrderQuery(page_size=50),
            mode=CollectionMode.STRICT,
        )
        if (
            type(balances) is not ObservedBalanceSnapshot
            or type(positions) is not ObservedMarginedPositions
            or type(open_orders) is not StrictOrderPaginationResult
            or not open_orders.complete
            or open_orders.provenance.mode is not CollectionMode.STRICT
        ):
            raise ValueError("current private reconciliation response is incomplete")
        if any(
            not _same_account(item, self._account)
            for item in (
                _observed_account(balances),
                _observed_account(positions),
                _pagination_account(open_orders),
            )
        ):
            raise ValueError("current private response crossed account scope")
        return balances, positions, open_orders

    @staticmethod
    def _current_sha256(
        balances: ObservedBalanceSnapshot,
        positions: ObservedMarginedPositions,
        open_orders: StrictOrderPaginationResult,
    ) -> str:
        payload = json.dumps(
            {
                "balances": _json_model(balances.value),
                "open_orders": [_json_model(item) for item in open_orders.items],
                "positions": [_json_model(item) for item in positions.value],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("ascii")).hexdigest()

    async def _collect(
        self, projection: LocalAccountProjection
    ) -> tuple[
        dict[str, object],
        tuple[ReconciliationDrift, ...],
        tuple[UncertainOrderOutcome, ...],
        datetime,
    ]:
        first_balances, first_positions, first_open_orders = await self._read_current()
        history = await self._client.get_order_history(
            OrderHistoryQuery(page_size=50),
            mode=CollectionMode.STRICT,
        )
        fills = await self._client.get_fills(
            FillQuery(page_size=50),
            mode=CollectionMode.STRICT,
        )
        balances, positions, open_orders = await self._read_current()
        if self._current_sha256(
            first_balances,
            first_positions,
            first_open_orders,
        ) != self._current_sha256(balances, positions, open_orders):
            raise ValueError("current venue truth changed during reconciliation")
        latest_product_ids = tuple(
            sorted(
                set(self._product_ids)
                | {item.product_id for item in positions.value}
                | {item[0] for item in projection.signed_positions}
            )
        )
        latest = tuple(
            [
                await self._client.get_latest_position(LatestPositionQuery(product_id=product_id))
                for product_id in latest_product_ids
            ]
        )
        if (
            type(history) is not StrictOrderPaginationResult
            or type(fills) is not StrictFillPaginationResult
            or any(type(item) is not ObservedLatestPositionLookupResult for item in latest)
            or not history.complete
            or not fills.complete
            or history.provenance.mode is not CollectionMode.STRICT
            or fills.provenance.mode is not CollectionMode.STRICT
        ):
            raise ValueError("private reconciliation response is incomplete")
        accounts = (
            _pagination_account(history),
            _pagination_account(fills),
            *(_observed_account(item) for item in latest),
        )
        if any(not _same_account(item, self._account) for item in accounts):
            raise ValueError("private reconciliation response crossed account scope")
        uncertain = await self._resolve_uncertain(projection, history, fills)
        drifts = self._classify(projection, latest, open_orders, uncertain)
        observed_at = max(
            balances.context.received_at,
            positions.context.received_at,
            open_orders.provenance.last_received_at,
            history.provenance.last_received_at,
            fills.provenance.last_received_at,
            *(item.context.received_at for item in latest),
        ).astimezone(UTC)
        evidence = {
            "schema_version": 1,
            "account_id": str(self._account.account_id),
            "credential_version": self._account.credential_version,
            "product_ids": list(self._product_ids),
            "balances": _json_model(balances),
            "positions": _json_model(positions),
            "open_orders": _json_model(open_orders),
            "order_history": _pagination_commitment(history),
            "fills": _pagination_commitment(fills),
            "latest_positions": [_json_model(item) for item in latest],
        }
        return evidence, drifts, uncertain, observed_at

    async def reconcile(
        self,
        trigger: ReconciliationTrigger,
        *,
        request_id: UUID,
    ) -> ReconciliationReceipt:
        """Run or replay one read-only exchange-truth barrier; never dispatch an order."""
        if type(trigger) is not ReconciliationTrigger:
            raise ValueError("invalid reconciliation trigger")
        if type(request_id) is not UUID or request_id.int == 0:
            raise ValueError("invalid reconciliation request identity")
        identity = request_id
        async with self._lock:
            self._ready = False
            self._ready_until = None
            self._last_receipt = None
            async with self._database.transaction() as session:
                replay = await self._repository.begin(
                    session,
                    self._account,
                    request_id=identity,
                    trigger=trigger,
                    product_ids=self._product_ids,
                    now=self._now(),
                )
                if replay is not None:
                    return self._publish_receipt(replay)
                projection = await self._repository.local_projection(session, self._account)
                projection_sha256 = local_projection_sha256(projection)
            try:
                async with asyncio.timeout(self._timeout.total_seconds()):
                    evidence, drifts, uncertain, observed_at = await self._collect(projection)
                    evidence["local_projection_sha256"] = projection_sha256
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if type(error) is PaginationLimitError:
                    failure_code = "PAGINATION_INCOMPLETE"
                elif isinstance(error, (ExchangeProtocolError, ValueError)):
                    failure_code = "INVALID_RESPONSE"
                else:
                    failure_code = "REQUEST_FAILED"
                async with self._database.transaction() as session:
                    receipt = await self._repository.fail(
                        session,
                        self._account,
                        request_id=identity,
                        failure_code=failure_code,
                        now=self._now(),
                    )
                return self._publish_receipt(receipt)
            try:
                async with self._database.transaction() as session:
                    receipt = await self._repository.finish(
                        session,
                        self._account,
                        request_id=identity,
                        evidence=evidence,
                        drifts=drifts,
                        uncertain=uncertain,
                        expected_local_projection_sha256=projection_sha256,
                        observed_at=observed_at,
                        recorded_at=self._now(),
                    )
            except ReconciliationEvidenceLimitError:
                async with self._database.transaction() as session:
                    receipt = await self._repository.fail(
                        session,
                        self._account,
                        request_id=identity,
                        failure_code="LIMIT_REACHED",
                        now=self._now(),
                    )
            return self._publish_receipt(receipt)

    async def run_startup(self, *, request_id: UUID) -> ReconciliationReceipt:
        """Run the one-shot startup barrier for a caller-owned idempotency key."""
        return await self.reconcile(ReconciliationTrigger.STARTUP, request_id=request_id)

    async def run_manual(self, *, request_id: UUID) -> ReconciliationReceipt:
        """Run an operator-requested barrier for a caller-owned idempotency key."""
        return await self.reconcile(ReconciliationTrigger.MANUAL, request_id=request_id)

    async def run_recovery(self, *, request_id: UUID) -> ReconciliationReceipt:
        """Run a crash-recovery barrier for a caller-owned idempotency key."""
        return await self.reconcile(ReconciliationTrigger.RECOVERY, request_id=request_id)

    async def run_reconnect(self, *, request_id: UUID) -> ReconciliationReceipt:
        """Run a post-WebSocket-reconnect barrier before private state is trusted."""
        return await self.reconcile(ReconciliationTrigger.RECONNECT, request_id=request_id)

    async def run_periodic(
        self,
        stop: asyncio.Event,
        *,
        interval: timedelta,
        request_id_factory: Callable[[], UUID],
        max_runs: int = 10_000,
    ) -> int:
        """Run a bounded periodic loop; cancellation and stop are observed promptly."""
        if (
            type(stop) is not asyncio.Event
            or type(interval) is not timedelta
            or not timedelta(seconds=1) <= interval <= timedelta(days=1)
            or not callable(request_id_factory)
            or type(max_runs) is not int
            or not 1 <= max_runs <= 10_000
        ):
            raise ValueError("invalid periodic reconciliation policy")
        completed = 0
        while not stop.is_set() and completed < max_runs:
            await self.reconcile(
                ReconciliationTrigger.PERIODIC,
                request_id=request_id_factory(),
            )
            completed += 1
            try:
                async with asyncio.timeout(interval.total_seconds()):
                    await stop.wait()
            except TimeoutError:
                continue
        return completed


__all__ = ["AccountReconciliationService"]
