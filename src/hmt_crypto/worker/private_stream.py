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
                subscription = {
                    "type": "subscribe",
                    "payload": {
                        "channels": [{"name": channel, "symbols": ["all"]} for channel in _CHANNELS]
                    },
                }
                await socket.send(json.dumps(subscription, separators=(",", ":")))
                logger.info("private_stream_authenticated", account_id=str(account_id))
                sequences: dict[str, int] = {}
                baselined: set[str] = set()
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
                                    sequence=sequences.get(channel),
                                )
                            last_heartbeat_persisted = monotonic
                        continue
                    if frame.get("type") not in _CHANNELS:
                        continue
                    channel = str(frame["type"])
                    meta = frame.get("meta")
                    candidate = frame.get("sequence_id")
                    if candidate is None and isinstance(meta, dict):
                        candidate = meta.get("seq_no")
                    sequence = candidate if type(candidate) is int and candidate >= 0 else None
                    prior = sequences.get(channel)
                    state = "snapshot" if frame.get("action") == "snapshot" else "live"
                    if sequence is not None and prior is not None and sequence != prior + 1:
                        state = "gap"
                    if sequence is not None:
                        sequences[channel] = sequence
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


__all__ = ["run_private_stream"]
