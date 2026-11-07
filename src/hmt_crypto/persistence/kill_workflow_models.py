"""Durable global-kill workflow state and append-only transition evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid, event
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.oms.kill_switch import KillEvent, KillMode, KillState, rehydrate_kill_request
from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    guard_model,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.schema_support import SCOPE, NulFreeString, StrictBigInteger
from hmt_crypto.persistence.types import UTCDateTime

KILL_MODES = tuple(item.value for item in KillMode)
KILL_STATES = tuple(item.value for item in KillState)
KILL_EVENTS = tuple(item.value for item in KillEvent)
_TERMINAL = (KillState.COMPLETED.value, KillState.INCOMPLETE.value)


class KillWorkflow(ScopedRow, Base):
    """One current, restart-safe workflow projection for a global-kill command."""

    __tablename__ = "kill_workflows"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        scoped_fk(__tablename__, "command_id", "command_operations"),
        UniqueConstraint(*SCOPE, "command_id", name="uq_kill_workflows_scope_command"),
        checked("kill_mode", KILL_MODES),
        checked("state", KILL_STATES),
        CheckConstraint("id = command_id", name="identity_matches_command"),
        CheckConstraint(
            "transition_count >= 0 AND version = transition_count + 1",
            name="version_tracks_transitions",
        ),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint(
            "(state IN ('COMPLETED','INCOMPLETE') AND completed_at IS NOT NULL "
            "AND completed_at >= created_at AND completed_at <= updated_at) OR "
            "(state NOT IN ('COMPLETED','INCOMPLETE') AND completed_at IS NULL)",
            name="terminal_time",
        ),
        Index("ix_kill_workflows_command", "command_id", *SCOPE),
    )

    command_id: Mapped[UUID] = mapped_column(Uuid)
    kill_mode: Mapped[str] = mapped_column(NulFreeString(32))
    state: Mapped[str] = mapped_column(NulFreeString(32))
    transition_count: Mapped[int] = mapped_column(StrictBigInteger())
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class KillWorkflowEvent(ScopedRow, Base):
    """One immutable event that atomically advanced a workflow version."""

    __tablename__ = "kill_workflow_events"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        ForeignKeyConstraint(
            ["workflow_id", *SCOPE],
            [f"hmt_crypto.kill_workflows.{column}" for column in ("id", *SCOPE)],
            name="fk_kill_workflow_events_workflow_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "workflow_id", *SCOPE, "sequence", name="uq_kill_workflow_events_sequence"
        ),
        checked("event_kind", KILL_EVENTS),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        Index("ix_kill_workflow_events_workflow", "workflow_id", *SCOPE, "sequence"),
    )

    workflow_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(StrictBigInteger())
    event_kind: Mapped[str] = mapped_column(NulFreeString(48))
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())


@event.listens_for(KillWorkflow, "before_insert")
@event.listens_for(KillWorkflow, "before_update")
def _validate_workflow(mapper: object, connection: object, target: KillWorkflow) -> None:
    del mapper, connection
    try:
        if target.id != target.command_id or target.version != target.transition_count + 1:
            raise PersistenceValueError()
        request = rehydrate_kill_request(
            request_id=target.command_id,
            mode=KillMode(target.kill_mode),
            state=KillState(target.state),
            transition_count=target.transition_count,
        )
        if request.is_terminal != (target.completed_at is not None):
            raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None


@event.listens_for(KillWorkflowEvent, "before_insert")
def _validate_event(mapper: object, connection: object, target: KillWorkflowEvent) -> None:
    del mapper, connection
    if (
        type(target.sequence) is not int
        or target.sequence < 1
        or type(target.event_kind) is not str
        or target.event_kind not in KILL_EVENTS
    ):
        raise PersistenceValueError() from None


guard_model(KillWorkflow, protected=("command_id", "kill_mode", "created_at"))
guard_model(KillWorkflowEvent, immutable=True)

__all__ = ["KillWorkflow", "KillWorkflowEvent"]
