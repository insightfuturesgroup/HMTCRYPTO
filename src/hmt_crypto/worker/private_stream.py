"""Lease-fenced Delta India private WebSocket evidence supervision."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from websockets.asyncio.client import connect as websocket_connect

from hmt_crypto.logging import get_logger
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    AccountLiveControl,
    PrivateStreamEvidence,
    PrivateWorkerLease,
)

_ORIGIN = "wss://socket.india.delta.exchange"
_CHANNELS = ("orders", "positions", "margins", "v2/user_trades")
_SYMBOLS = ("BTCUSD", "ETHUSD")


def _subscription_payload() -> dict[str, object]:
    return {
        "type": "subscribe",
        "payload": {
            "channels": [
                {"name": "orders", "symbols": list(_SYMBOLS)},
                {"name": "positions", "symbols": list(_SYMBOLS)},
                {"name": "margins"},
                {"name": "v2/user_trades", "symbols": list(_SYMBOLS)},
            ]
        },
    }


def _acknowledged_channels(frame: object) -> set[str]:
    if not isinstance(frame, dict) or frame.get("type") != "subscriptions":
        raise ValueError("private websocket subscription acknowledgement missing")
    channels = frame.get("channels")
    if not isinstance(channels, list):
        raise ValueError("private websocket subscription acknowledgement invalid")
    acknowledged: set[str] = set()
    for item in channels:
        if not isinstance(item, dict) or item.get("error") is not None:
            raise ValueError("private websocket subscription rejected")
        name = item.get("name")
        if isinstance(name, str):
            acknowledged.add(name)
    if acknowledged != set(_CHANNELS):
        raise ValueError("private websocket subscription incomplete")
    return acknowledged


def _stream_key(channel: str, frame: dict[str, object]) -> tuple[str, str]:
    symbol = frame.get("symbol") or frame.get("sy")
    return channel, symbol if isinstance(symbol, str) else "account"


async def _persist(
    database: Database,
    *,
    account_id: UUID,
    owner_id: UUID,
    generation: int,
    credential_version: int,
    channel: str,
    state: str,
    sequence: int | None,
) -> bool:
    now = datetime.now(UTC)
    async with database.transaction() as session:
        lease = await session.scalar(
            select(PrivateWorkerLease).where(
                PrivateWorkerLease.account_id == account_id,
                PrivateWorkerLease.owner_id == owner_id,
                PrivateWorkerLease.generation == generation,
                PrivateWorkerLease.credential_version == credential_version,
                PrivateWorkerLease.lease_until > now,
            )
        )
        if lease is None:
            return False
        session.add(
            PrivateStreamEvidence(
                id=uuid4(),
                account_id=account_id,
                channel=channel,
                state=state,
                sequence_number=sequence,
                credential_version=credential_version,
                worker_owner_id=owner_id,
                worker_generation=generation,
                observed_at=now,
                updated_at=now,
            )
        )
        if state in {"gap", "disconnected"}:
            control = await session.scalar(
                select(AccountLiveControl).where(AccountLiveControl.account_id == account_id)
            )
            if control is not None:
                control.reconciliation_state = "degraded"
                control.entries_enabled = False
                control.exits_enabled = False
                control.last_error_code = (
                    "private_stream_sequence_gap"
                    if state == "gap"
                    else "private_stream_disconnected"
                )
                control.updated_at = now
        return True


async def _mark_disconnected(
    database: Database,
    *,
    account_id: UUID,
    owner_id: UUID,
    generation: int,
    credential_version: int,
) -> None:
    for channel in _CHANNELS:
        if not await _persist(
            database,
            account_id=account_id,
            owner_id=owner_id,
            generation=generation,
            credential_version=credential_version,
            channel=channel,
            state="disconnected",
            sequence=None,
        ):
            return


async def run_private_stream(
    database: Database,
    *,
    account_id: UUID,
    owner_id: UUID,
    generation: int,
    credential_version: int,
    api_key: str,
    api_secret: str,
    stop: asyncio.Event,
) -> None:
    logger = get_logger("worker.private.websocket")
    backoff = 1
    while not stop.is_set():
        try:
            timestamp = int(time.time())
            signature = hmac.new(
                api_secret.encode(),
                f"GET{timestamp}/live".encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            async with websocket_connect(_ORIGIN, open_timeout=10, close_timeout=5) as socket:
                auth_message = {
                    "type": "key-auth",
                    "payload": {
                        "api-key": api_key,
                        "timestamp": timestamp,
                        "signature": signature,
                    },
                }
                await socket.send(json.dumps(auth_message, separators=(",", ":")))
                auth = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                if not isinstance(auth, dict) or auth.get("success") is not True:
                    raise ValueError("private websocket authentication rejected")
                await socket.send(json.dumps({"type": "enable_heartbeat"}))
                subscription = _subscription_payload()
                await socket.send(json.dumps(subscription, separators=(",", ":")))
                acknowledgement = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                acknowledged = _acknowledged_channels(acknowledgement)
                for channel in acknowledged:
                    if not await _persist(
                        database,
                        account_id=account_id,
                        owner_id=owner_id,
                        generation=generation,
                        credential_version=credential_version,
                        channel=channel,
                        state="live",
                        sequence=None,
                    ):
                        return
                logger.info("private_stream_authenticated", account_id=str(account_id))
                sequences: dict[tuple[str, str], int] = {}
                baselined: set[str] = set(acknowledged)
                last_heartbeat_persisted = 0.0
                backoff = 1
                async for raw in socket:
                    frame = json.loads(raw)
                    if not isinstance(frame, dict):
                        continue
                    if frame.get("type") in {"heartbeat", "pong"}:
                        monotonic = time.monotonic()
                        if monotonic - last_heartbeat_persisted >= 10:
                            for channel in baselined:
                                await _persist(
                                    database,
                                    account_id=account_id,
                                    owner_id=owner_id,
                                    generation=generation,
                                    credential_version=credential_version,
                                    channel=channel,
                                    state="live",
                                    sequence=max(
                                        (
                                            value
                                            for (stream_channel, _), value in sequences.items()
                                            if stream_channel == channel
                                        ),
                                        default=None,
                                    ),
                                )
                            last_heartbeat_persisted = monotonic
                        continue
                    if frame.get("type") not in _CHANNELS:
                        continue
                    channel = str(frame["type"])
                    stream_key = _stream_key(channel, frame)
                    meta = frame.get("meta")
                    candidate = frame.get("sequence_id")
                    if candidate is None and isinstance(meta, dict):
                        candidate = meta.get("seq_no")
                    sequence = candidate if type(candidate) is int and candidate >= 0 else None
                    prior = sequences.get(stream_key)
                    state = "snapshot" if frame.get("action") == "snapshot" else "live"
                    if sequence is not None and prior is not None and sequence != prior + 1:
                        state = "gap"
                    if sequence is not None:
                        sequences[stream_key] = sequence
                    if state != "gap":
                        baselined.add(channel)
                    persisted = await _persist(
                        database,
                        account_id=account_id,
                        owner_id=owner_id,
                        generation=generation,
                        credential_version=credential_version,
                        channel=channel,
                        state=state,
                        sequence=sequence,
                    )
                    if not persisted:
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            await _mark_disconnected(
                database,
                account_id=account_id,
                owner_id=owner_id,
                generation=generation,
                credential_version=credential_version,
            )
            logger.warning(
                "private_stream_reconnect", account_id=str(account_id), backoff_seconds=backoff
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, 30)


__all__ = [
    "_acknowledged_channels",
    "_stream_key",
    "_subscription_payload",
    "run_private_stream",
]
