"""Bounded, provider-neutral operational alert hook with no secret ownership."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class AlertDeliveryError(RuntimeError):
    """Report a bounded provider delivery failure without provider diagnostics."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("operational alert delivery failed")


class AlertKind(StrEnum):
    """Closed operational conditions that may page an operator."""

    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    CLOCK_DRIFT = "CLOCK_DRIFT"
    DAILY_LOSS_HALT = "DAILY_LOSS_HALT"
    GLOBAL_KILL_ENGAGED = "GLOBAL_KILL_ENGAGED"
    GLOBAL_KILL_INCOMPLETE = "GLOBAL_KILL_INCOMPLETE"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
    STREAM_DISCONNECTED = "STREAM_DISCONNECTED"
    WORKER_LEADERSHIP_LOST = "WORKER_LEADERSHIP_LOST"


class AlertSeverity(StrEnum):
    """Closed routing severity understood by provider adapters."""

    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class AlertEvent:
    """Secret-free operational event passed to a deployment-owned provider adapter."""

    event_id: UUID
    kind: AlertKind
    severity: AlertSeverity
    occurred_at: datetime
    region: str
    environment: str
    mode: str
    account_id: UUID | None = None
    correlation_id: UUID | None = None

    def __post_init__(self) -> None:
        if (
            type(self.event_id) is not UUID
            or self.event_id.int == 0
            or type(self.kind) is not AlertKind
            or type(self.severity) is not AlertSeverity
            or type(self.occurred_at) is not datetime
            or self.occurred_at.tzinfo is None
            or self.region not in {"india", "global"}
            or self.environment not in {"testnet", "prod"}
            or self.mode not in {"BACKTEST", "PAPER", "LIVE"}
            or self.account_id is not None
            and (type(self.account_id) is not UUID or self.account_id.int == 0)
            or self.correlation_id is not None
            and (type(self.correlation_id) is not UUID or self.correlation_id.int == 0)
        ):
            raise ValueError("invalid alert event") from None

    def safe_text(self) -> str:
        """Render one deterministic message containing only allowlisted classifications."""
        timestamp = self.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        correlation = "none" if self.correlation_id is None else str(self.correlation_id)
        return (
            f"HMT-Crypto {self.severity.value}: {self.kind.value}; "
            f"scope={self.region}/{self.environment}/{self.mode}; "
            f"occurred_at={timestamp}; correlation_id={correlation}"
        )


class AlertTransport(Protocol):
    """Deployment-owned Telegram/email/webhook adapter."""

    async def deliver(self, event: AlertEvent, *, idempotency_key: str) -> None: ...


class AlertDispatcher:
    """Apply bounded timeout/retry policy while preserving caller cancellation."""

    def __init__(
        self,
        transport: AlertTransport,
        *,
        attempts: int = 3,
        timeout_seconds: float = 5.0,
        retry_delay_seconds: float = 0.25,
    ) -> None:
        if (
            not callable(getattr(transport, "deliver", None))
            or type(attempts) is not int
            or not 1 <= attempts <= 5
            or type(timeout_seconds) is not float
            or not 0.05 <= timeout_seconds <= 30.0
            or type(retry_delay_seconds) is not float
            or not 0.0 <= retry_delay_seconds <= 5.0
        ):
            raise ValueError("invalid alert dispatcher configuration") from None
        self._transport = transport
        self._attempts = attempts
        self._timeout = timeout_seconds
        self._retry_delay = retry_delay_seconds

    async def dispatch(self, event: AlertEvent) -> None:
        """Deliver at most the configured attempts under one stable idempotency key."""
        if type(event) is not AlertEvent:
            raise AlertDeliveryError() from None
        key = f"hmt-alert:{event.event_id}"
        for attempt in range(1, self._attempts + 1):
            failed = False
            try:
                async with asyncio.timeout(self._timeout):
                    await self._transport.deliver(event, idempotency_key=key)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                failed = True
            if not failed:
                return
            if attempt == self._attempts:
                break
            if self._retry_delay:
                await asyncio.sleep(self._retry_delay)
        raise AlertDeliveryError() from None


__all__ = [
    "AlertDeliveryError",
    "AlertDispatcher",
    "AlertEvent",
    "AlertKind",
    "AlertSeverity",
    "AlertTransport",
]
