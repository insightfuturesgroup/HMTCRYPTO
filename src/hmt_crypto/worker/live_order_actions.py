"""Durable, lease-fenced Delta Live cancel and shrink-only modify execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import case, desc, select

from hmt_crypto.exchange.delta.signer import DeltaSigner
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    Account,
    AccountLiveControl,
    LiveOrderAction,
    PlatformLiveControl,
    PrivateReconciliationSnapshot,
    PrivateWorkerLease,
)
from hmt_crypto.worker.live_execution import LiveExecutionSettings

_ORDER_PATH = "/v2/orders"
_ORDER_URL = "https://api.india.delta.exchange/v2/orders"
_MAX_RESPONSE_BYTES = 1_048_576
_PERMIT_ISSUER = object()


@dataclass(frozen=True, slots=True)
class LiveOrderActionResult:
    kind: str
    action_id: UUID | None


@dataclass(frozen=True, slots=True)
class _ActionPermit:
    action_id: UUID
    method: str
    body: bytes
    body_sha256: str
    expires_at: datetime
    issuer: object

    def __post_init__(self) -> None:
        if (
            self.issuer is not _PERMIT_ISSUER
            or self.method not in {"DELETE", "PUT"}
            or hashlib.sha256(self.body).hexdigest() != self.body_sha256
        ):
            raise ValueError("invalid durable action permit")


class LiveOrderActionTransportUncertain(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LiveOrderActionHttpResponse:
    status_code: int
    body: bytes


class DeltaIndiaLiveOrderActionAdapter:
    def __init__(
        self, *, signer: DeltaSigner, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if type(signer) is not DeltaSigner:
            raise TypeError("live order action adapter requires DeltaSigner")
        self._signer = signer
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def send_once(self, permit: _ActionPermit) -> LiveOrderActionHttpResponse:
        if type(permit) is not _ActionPermit or permit.expires_at <= datetime.now(UTC):
            raise LiveOrderActionTransportUncertain from None
        headers = self._signer.sign_now(
            permit.method, _ORDER_PATH, payload=permit.body
        ).as_headers()
        headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        request = self._client.build_request(
            permit.method, _ORDER_URL, headers=headers, content=permit.body
        )
        try:
            response = await self._client.send(request, stream=True)
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise LiveOrderActionTransportUncertain
                chunks.append(chunk)
            return LiveOrderActionHttpResponse(response.status_code, b"".join(chunks))
        except LiveOrderActionTransportUncertain:
            raise
        except Exception:
            raise LiveOrderActionTransportUncertain from None
        finally:
            headers.clear()
            if "response" in locals():
                await response.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()


def _payload_rows(value: str | None) -> list[object]:
    try:
        rows = json.loads(value or "")
    except (TypeError, ValueError):
        raise ValueError("invalid reconciliation payload") from None
    if not isinstance(rows, list):
        raise ValueError("invalid reconciliation payload")
    return rows


def _integer(value: object) -> int | None:
    if type(value) is int and value > 0:
        return value
    if type(value) is str and value.isascii() and value.isdigit() and int(value) > 0:
        return int(value)
    return None


def _find_order(rows: list[object], order_id: int, product_id: int) -> dict[str, object] | None:
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid reconciliation payload")
        if _integer(row.get("id")) == order_id:
            return row if _integer(row.get("product_id")) == product_id else None
    return None


def _modify_matches(action: LiveOrderAction, order: dict[str, object]) -> bool:
    if _integer(order.get("size")) != action.target_size:
        return False
    if action.target_limit_price is None:
        return True
    try:
        return str(order.get("limit_price")) == format(action.target_limit_price, "f")
    except Exception:
        return False


def _classify_response(
    action: LiveOrderAction, response: LiveOrderActionHttpResponse
) -> tuple[str, str | None]:
    if response.status_code == 200:
        try:
            envelope = json.loads(response.body)
            result = envelope.get("result") if isinstance(envelope, dict) else None
            if (
                envelope.get("success") is not True
                or not isinstance(result, dict)
                or _integer(result.get("id")) != action.order_id
                or _integer(result.get("product_id")) != action.product_id
                or (action.action == "MODIFY" and not _modify_matches(action, result))
            ):
                raise ValueError
            return "SUCCEEDED", None
        except Exception:
            return "UNCERTAIN", "invalid_exchange_response"
    if 400 <= response.status_code < 500:
        return "REJECTED", f"exchange_http_{response.status_code}"
    return "UNCERTAIN", f"exchange_http_{response.status_code}"


async def execute_one_live_order_action(
    database: Database,
    *,
    settings: LiveExecutionSettings,
    account_id: UUID,
    owner_id: UUID,
    adapter: DeltaIndiaLiveOrderActionAdapter,
) -> LiveOrderActionResult:
    """Resolve uncertainty or execute one action; never retry a possible venue write."""
    if (
        type(settings) is not LiveExecutionSettings
        or not settings.enabled
        or type(account_id) is not UUID
        or account_id.int == 0
        or type(owner_id) is not UUID
        or owner_id.int == 0
        or type(adapter) is not DeltaIndiaLiveOrderActionAdapter
    ):
        raise ValueError("live action execution is not explicitly qualified")
    now = datetime.now(UTC)
    permit: _ActionPermit | None = None
    action_id: UUID | None = None
    async with database.transaction() as session:
        lease = await session.scalar(
            select(PrivateWorkerLease)
            .where(PrivateWorkerLease.account_id == account_id)
            .with_for_update()
        )
        account = await session.get(Account, account_id)
        control = await session.scalar(
            select(AccountLiveControl).where(AccountLiveControl.account_id == account_id)
        )
        global_control = await session.get(PlatformLiveControl, "global")
        reconciliation = await session.scalar(
            select(PrivateReconciliationSnapshot)
            .where(PrivateReconciliationSnapshot.account_id == account_id)
            .order_by(desc(PrivateReconciliationSnapshot.observed_at))
            .limit(1)
        )
        if (
            lease is None
            or lease.owner_id != owner_id
            or lease.lease_until <= now
            or account is None
            or account.environment != "prod"
            or account.mode != "LIVE"
            or account.current_credential_version != lease.credential_version
            or control is None
            or control.credential_state != "verified"
            or control.reconciliation_state != "ready"
            or reconciliation is None
            or reconciliation.outcome != "ready"
            or reconciliation.observed_at < now - timedelta(seconds=60)
            or reconciliation.worker_owner_id != lease.owner_id
            or reconciliation.worker_generation != lease.generation
            or reconciliation.credential_version != lease.credential_version
        ):
            return LiveOrderActionResult("IDLE", None)
        orders = _payload_rows(reconciliation.open_orders_payload)
        unresolved = await session.scalar(
            select(LiveOrderAction)
            .where(
                LiveOrderAction.account_id == account_id,
                LiveOrderAction.status == "UNCERTAIN",
            )
            .order_by(LiveOrderAction.created_at, LiveOrderAction.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if unresolved is not None:
            order = _find_order(orders, unresolved.order_id, unresolved.product_id)
            if unresolved.action == "CANCEL" and order is None:
                unresolved.status = "SUCCEEDED"
                unresolved.last_error_code = "reconciled_order_absent"
            elif (
                unresolved.action == "MODIFY"
                and order is not None
                and _modify_matches(unresolved, order)
            ):
                unresolved.status = "SUCCEEDED"
                unresolved.last_error_code = "reconciled_modified"
            else:
                unresolved.status = "BLOCKED"
                unresolved.last_error_code = "uncertain_action_not_confirmed"
            unresolved.resolved_at = now
            unresolved.updated_at = now
            return LiveOrderActionResult("RESOLVED", unresolved.id)
        action = await session.scalar(
            select(LiveOrderAction)
            .where(
                LiveOrderAction.account_id == account_id,
                LiveOrderAction.status == "PENDING",
            )
            .order_by(
                case((LiveOrderAction.action == "CANCEL", 0), else_=1),
                LiveOrderAction.created_at,
                LiveOrderAction.id,
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if action is None:
            return LiveOrderActionResult("IDLE", None)
        body = action.request_payload.encode("ascii")
        current_order = _find_order(orders, action.order_id, action.product_id)
        if (
            current_order is None
            or hashlib.sha256(body).hexdigest() != action.request_sha256
            or (action.action == "MODIFY" and not control.entries_enabled)
            or (
                action.action == "MODIFY"
                and (global_control is None or not global_control.entries_enabled)
            )
        ):
            action.status = "BLOCKED"
            action.last_error_code = "action_invalidated"
            action.resolved_at = now
            action.updated_at = now
            return LiveOrderActionResult("BLOCKED", action.id)
        action.status = "SENDING"
        action.worker_owner_id = owner_id
        action.worker_generation = lease.generation
        action.credential_version = lease.credential_version
        action.sent_at = now
        action.updated_at = now
        action_id = action.id
        permit = _ActionPermit(
            action_id=action.id,
            method="DELETE" if action.action == "CANCEL" else "PUT",
            body=body,
            body_sha256=action.request_sha256,
            expires_at=now + timedelta(seconds=10),
            issuer=_PERMIT_ISSUER,
        )
    assert permit is not None
    try:
        response = await adapter.send_once(permit)
        response_sha256 = hashlib.sha256(response.body).hexdigest()
    except Exception:
        response = None
        response_sha256 = None
    async with database.transaction() as session:
        action = await session.get(LiveOrderAction, action_id, with_for_update=True)
        lease = await session.scalar(
            select(PrivateWorkerLease).where(PrivateWorkerLease.account_id == account_id)
        )
        if (
            action is None
            or action.status != "SENDING"
            or action.worker_owner_id != owner_id
            or lease is None
            or lease.owner_id != owner_id
            or action.worker_generation != lease.generation
            or action.credential_version != lease.credential_version
        ):
            return LiveOrderActionResult("FENCE_LOST", action_id)
        error: str | None
        if response is None:
            status, error = "UNCERTAIN", "transport_uncertain"
        else:
            status, error = _classify_response(action, response)
        action.status = status
        action.exchange_response_sha256 = response_sha256
        action.last_error_code = error
        action.resolved_at = now if status in {"SUCCEEDED", "REJECTED", "BLOCKED"} else None
        action.updated_at = datetime.now(UTC)
        return LiveOrderActionResult(status, action.id)


__all__ = [
    "DeltaIndiaLiveOrderActionAdapter",
    "LiveOrderActionHttpResponse",
    "LiveOrderActionResult",
    "execute_one_live_order_action",
]
