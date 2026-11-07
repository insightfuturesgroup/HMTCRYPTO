"""Append-only, secret-free operator control-plane audit evidence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    guard_model,
    scoped_constraints,
)
from hmt_crypto.persistence.schema_support import NulFreeString
from hmt_crypto.persistence.types import UTCDateTime

OPERATOR_EVENT_KINDS = (
    "LOGIN",
    "LOGOUT",
    "DASHBOARD_READ",
    "KILL_PREVIEW",
    "KILL_ENQUEUE",
    "COMMAND_STATUS",
    "ACTIVE_KILL_STATUS",
)
OPERATOR_OUTCOMES = (
    "SUCCEEDED",
    "REJECTED",
    "RATE_LIMITED",
    "NOT_FOUND",
    "CONFLICT",
)
OPERATOR_ACTION_VARIANTS = ("CANCEL_ONLY", "CANCEL_AND_FLATTEN")


class OperatorAuditEvent(ScopedRow, Base):
    """One immutable, allowlisted operator action/result without request material."""

    __tablename__ = "operator_audit_events"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        checked("event_kind", OPERATOR_EVENT_KINDS),
        checked("outcome", OPERATOR_OUTCOMES),
        checked("action_variant", OPERATOR_ACTION_VARIANTS),
        CheckConstraint("recorded_at >= occurred_at", name="time_order"),
        CheckConstraint(
            "(event_kind = 'LOGIN') OR session_id IS NOT NULL",
            name="session_required_after_login",
        ),
        CheckConstraint(
            "action_variant IS NULL OR event_kind IN ('KILL_PREVIEW','KILL_ENQUEUE')",
            name="action_variant_applicability",
        ),
        Index("ix_operator_audit_events_time", "account_id", "recorded_at", "id"),
    )

    event_kind: Mapped[str] = mapped_column(NulFreeString(32))
    outcome: Mapped[str] = mapped_column(NulFreeString(16))
    session_id: Mapped[UUID | None] = mapped_column(Uuid)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid)
    action_variant: Mapped[str | None] = mapped_column(NulFreeString(32))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())


guard_model(OperatorAuditEvent, immutable=True)

__all__ = [
    "OPERATOR_ACTION_VARIANTS",
    "OPERATOR_EVENT_KINDS",
    "OPERATOR_OUTCOMES",
    "OperatorAuditEvent",
]
