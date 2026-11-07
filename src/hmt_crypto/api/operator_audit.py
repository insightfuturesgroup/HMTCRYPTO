"""Fail-closed durable audit boundary for operator API activity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from hmt_crypto.api.control_plane import ControlPlaneUnavailable
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.errors import PersistenceError
from hmt_crypto.persistence.operator_audit_models import OperatorAuditEvent
from hmt_crypto.persistence.repository_contracts import AccountScope

OperatorEventKind = Literal[
    "LOGIN",
    "LOGOUT",
    "DASHBOARD_READ",
    "KILL_PREVIEW",
    "KILL_ENQUEUE",
    "COMMAND_STATUS",
    "ACTIVE_KILL_STATUS",
]
OperatorOutcome = Literal["SUCCEEDED", "REJECTED", "RATE_LIMITED", "NOT_FOUND", "CONFLICT"]
OperatorActionVariant = Literal["CANCEL_ONLY", "CANCEL_AND_FLATTEN"]


@dataclass(frozen=True, slots=True)
class OperatorAuditRecord:
    """Bounded semantic audit record; passwords, tokens and peer addresses have no field."""

    event_kind: OperatorEventKind
    outcome: OperatorOutcome
    occurred_at: datetime
    session_id: UUID | None = None
    correlation_id: UUID | None = None
    action_variant: OperatorActionVariant | None = None

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("operator audit timestamp must be timezone-aware") from None
        if self.event_kind != "LOGIN" and self.session_id is None:
            raise ValueError("authenticated operator event requires a session") from None
        if self.action_variant is not None and self.event_kind not in {
            "KILL_PREVIEW",
            "KILL_ENQUEUE",
        }:
            raise ValueError("operator action variant is not applicable") from None


class OperatorAuditSink(Protocol):
    """Append one record durably before an API outcome is acknowledged."""

    async def append(self, record: OperatorAuditRecord) -> None: ...


class UnavailableOperatorAuditSink:
    """Deny auditable operator activity when no durable sink was composed."""

    async def append(self, record: OperatorAuditRecord) -> None:
        del record
        raise ControlPlaneUnavailable() from None


class DatabaseOperatorAuditSink:
    """Append operator evidence into the account-scoped application database."""

    def __init__(self, database: Database, scope: AccountScope) -> None:
        if type(database) is not Database or type(scope) is not AccountScope:
            raise TypeError("invalid operator audit sink configuration")
        self._database = database
        self._scope = AccountScope.model_validate(scope)

    async def append(self, record: OperatorAuditRecord) -> None:
        if type(record) is not OperatorAuditRecord:
            raise ControlPlaneUnavailable() from None
        recorded_at = datetime.now(UTC)
        if recorded_at < record.occurred_at.astimezone(UTC):
            recorded_at = record.occurred_at.astimezone(UTC)
        try:
            async with self._database.transaction() as session:
                session.add(
                    OperatorAuditEvent(
                        id=uuid4(),
                        **self._scope.model_dump(),
                        event_kind=record.event_kind,
                        outcome=record.outcome,
                        session_id=record.session_id,
                        correlation_id=record.correlation_id,
                        action_variant=record.action_variant,
                        occurred_at=record.occurred_at.astimezone(UTC),
                        recorded_at=recorded_at,
                    )
                )
        except (PersistenceError, ValueError, TypeError):
            raise ControlPlaneUnavailable() from None


__all__ = [
    "DatabaseOperatorAuditSink",
    "OperatorActionVariant",
    "OperatorAuditRecord",
    "OperatorAuditSink",
    "OperatorEventKind",
    "OperatorOutcome",
    "UnavailableOperatorAuditSink",
]
