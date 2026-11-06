"""Sanitized physical-send outcome for the stream-control owner."""

from __future__ import annotations

import asyncio
from enum import StrEnum

from hmt_crypto.exchange.delta.websocket_transport import WsConnection


class SendOutcome(StrEnum):
    """Bounded physical-send result without dependency exceptions."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


async def send_without_retaining_failure(
    connection: WsConnection,
    message: str,
) -> SendOutcome:
    """Collapse all dependency failures without retaining their exceptions."""
    try:
        await connection.send(message)
    except asyncio.CancelledError:
        return SendOutcome.CANCELLED
    except BaseException:
        return SendOutcome.FAILED
    return SendOutcome.COMPLETED


__all__ = ["SendOutcome", "send_without_retaining_failure"]
