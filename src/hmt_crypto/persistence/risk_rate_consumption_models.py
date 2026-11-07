"""Immutable historical local risk-rate charges; never dispatch authority."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.attempt_models import OrderAttempt
from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_event_models import OrderAttemptEvent
from hmt_crypto.persistence.execution_support import ScopedRow, checked, scoped_constraints
from hmt_crypto.persistence.risk_rate_consumption_evidence import (
    NEUTRAL_EVENT_PAYLOAD,
    NEUTRAL_EVENT_SHA256,
    RiskRateConsumptionPayload,
    derive_dispatch_event_key,
    encode_risk_rate_consumption_evidence,
)
from hmt_crypto.persistence.risk_rate_consumption_validation import (
    install_risk_rate_consumption_validation,
)
from hmt_crypto.persistence.risk_reservation_models import RiskReservation
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import UTCDateTime, _utc

RESERVATION_CHILD = (
    "reservation_id",
    *SCOPE,
    "decision_id",
    "risk_state_id",
    "command_id",
    "command_operation_kind",
    "order_id",
    "product_id",
    "decision_outcome",
    "risk_effect",
    "reservation_plan_sha256",
    "decision_evaluated_at",
    "decision_recorded_at",
    "reservation_created_at",
    "rate_policy_version",
    "rate_policy_sha256",
    "rate_window_start_at",
    "rate_window_end_at",
    "consumed_units",
)
RESERVATION_PARENT = (
    "id",
    *SCOPE,
    "decision_id",
    "risk_state_id",
    "command_id",
    "command_operation_kind",
    "order_id",
    "product_id",
    "decision_outcome",
    "risk_effect",
    "reservation_plan_sha256",
    "decision_evaluated_at",
    "decision_recorded_at",
    "created_at",
    "rate_policy_version",
    "rate_policy_sha256",
    "rate_window_start_at",
    "rate_window_end_at",
    "planned_pending_rate_units",
)
ATTEMPT_CHILD = (
    "order_attempt_id",
    *SCOPE,
    "command_id",
    "command_operation_kind",
    "order_id",
    "attempt_number",
    "attempt_backend",
    "attempt_prepared_at",
    "attempt_recorded_at",
    "attempt_evidence_schema_version",
    "attempt_evidence_sha256",
)
ATTEMPT_PARENT = (
    "id",
    *SCOPE,
    "command_id",
    "operation_kind",
    "order_id",
    "attempt_number",
    "backend",
    "prepared_at",
    "recorded_at",
    "evidence_schema_version",
    "evidence_sha256",
)
EVENT_CHILD = (
    "dispatch_event_id",
    *SCOPE,
    "order_attempt_id",
    "attempt_backend",
    "dispatch_event_sequence",
    "dispatch_event_key",
    "dispatch_event_kind",
    "recorded_at",
    "dispatch_event_evidence_schema_version",
    "dispatch_event_evidence_sha256",
)
EVENT_PARENT = (
    "id",
    *SCOPE,
    "attempt_id",
    "backend",
    "sequence",
    "event_key",
    "event_kind",
    "recorded_at",
    "evidence_schema_version",
    "evidence_sha256",
)


def _fk(
    name: str, child: tuple[str, ...], parent: str, parent_columns: tuple[str, ...]
) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        child,
        [f"hmt_crypto.{parent}.{column}" for column in parent_columns],
        name=name,
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class RiskRateConsumption(ScopedRow, Base):
    """One immutable account-scoped MAX_ORDER_RATE charge."""

    __tablename__ = "risk_rate_consumptions"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(
            *SCOPE,
            "consumption_key",
            name="uq_risk_rate_consumptions_scope_consumption_key",
        ),
        UniqueConstraint(
            *SCOPE, "reservation_id", name="uq_risk_rate_consumptions_scope_reservation"
        ),
        UniqueConstraint(
            *SCOPE, "order_attempt_id", name="uq_risk_rate_consumptions_scope_attempt"
        ),
        UniqueConstraint(
            *SCOPE,
            "dispatch_event_id",
            name="uq_risk_rate_consumptions_scope_dispatch_event",
        ),
        UniqueConstraint(
            "id",
            *SCOPE,
            "command_id",
            "command_operation_kind",
            "order_id",
            "order_attempt_id",
            "attempt_backend",
            "dispatch_event_id",
            name="uq_risk_rate_consumptions_api_audit_parent",
        ),
        _fk(
            "fk_risk_rate_consumptions_reservation",
            RESERVATION_CHILD,
            "risk_reservations",
            RESERVATION_PARENT,
        ),
        _fk(
            "fk_risk_rate_consumptions_attempt",
            ATTEMPT_CHILD,
            "order_attempts",
            ATTEMPT_PARENT,
        ),
        _fk(
            "fk_risk_rate_consumptions_dispatch_event",
            EVENT_CHILD,
            "order_attempt_events",
            EVENT_PARENT,
        ),
        Index("ix_risk_rate_consumptions_reservation_parent", *RESERVATION_CHILD),
        Index("ix_risk_rate_consumptions_attempt_parent", *ATTEMPT_CHILD),
        Index("ix_risk_rate_consumptions_dispatch_event_parent", *EVENT_CHILD),
        Index(
            "ix_risk_rate_consumptions_rate_window",
            *SCOPE,
            "rate_policy_version",
            "rate_policy_sha256",
            "rate_window_start_at",
            "rate_window_end_at",
        ),
        checked("command_operation_kind", ("PLACE_ORDER", "MODIFY_ORDER")),
        checked("decision_outcome", ("APPROVED",)),
        checked("risk_effect", ("RISK_INCREASE", "REDUCE_ONLY")),
        checked("attempt_backend", ("VENUE", "SIMULATOR")),
        checked("dispatch_event_kind", ("DISPATCH_STARTED",)),
        bounded("consumption_key", 64),
        bounded("dispatch_event_key"),
        bounded("attempt_evidence_payload", 2048),
        bounded("dispatch_event_evidence_payload", 2048),
        bounded("evidence_payload", 8192),
        sha_check("consumption_key"),
        sha_check("reservation_plan_sha256"),
        sha_check("attempt_evidence_sha256"),
        sha_check("dispatch_event_evidence_sha256"),
        sha_check("rate_policy_sha256"),
        sha_check("evidence_sha256"),
        CheckConstraint(
            "product_id >= 1 AND attempt_number >= 1 AND rate_policy_version >= 1",
            name="positive_values",
        ),
        CheckConstraint(
            "attempt_evidence_schema_version = 1 "
            "AND dispatch_event_evidence_schema_version = 1 "
            "AND evidence_schema_version = 1 AND dispatch_event_sequence = 1 "
            "AND consumed_units = 1",
            name="v1_constants",
        ),
        CheckConstraint(
            "(mode = 'LIVE' AND attempt_backend = 'VENUE') OR "
            "(mode IN ('PAPER','BACKTEST') AND attempt_backend = 'SIMULATOR')",
            name="backend_mode",
        ),
        CheckConstraint(
            "rate_window_start_at <= decision_evaluated_at "
            "AND decision_evaluated_at <= decision_recorded_at "
            "AND decision_recorded_at <= reservation_created_at "
            "AND reservation_created_at <= attempt_prepared_at "
            "AND attempt_prepared_at <= attempt_recorded_at "
            "AND attempt_recorded_at <= recorded_at "
            "AND recorded_at < rate_window_end_at",
            name="time_order",
        ),
    )

    consumption_key: Mapped[str] = mapped_column(NulFreeString(64))
    reservation_id: Mapped[UUID] = mapped_column(Uuid)
    decision_id: Mapped[UUID] = mapped_column(Uuid)
    risk_state_id: Mapped[UUID] = mapped_column(Uuid)
    command_id: Mapped[UUID] = mapped_column(Uuid)
    command_operation_kind: Mapped[str] = mapped_column(NulFreeString(32))
    order_id: Mapped[UUID] = mapped_column(Uuid)
    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    decision_outcome: Mapped[str] = mapped_column(NulFreeString(16))
    risk_effect: Mapped[str] = mapped_column(NulFreeString(16))
    reservation_plan_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    decision_evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    decision_recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    reservation_created_at: Mapped[datetime] = mapped_column(UTCDateTime())

    order_attempt_id: Mapped[UUID] = mapped_column(Uuid)
    attempt_number: Mapped[int] = mapped_column(StrictBigInteger())
    attempt_backend: Mapped[str] = mapped_column(NulFreeString(16))
    attempt_prepared_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempt_recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempt_evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    attempt_evidence_payload: Mapped[str] = mapped_column(NulFreeString(2048))
    attempt_evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))

    dispatch_event_id: Mapped[UUID] = mapped_column(Uuid)
    dispatch_event_sequence: Mapped[int] = mapped_column(StrictBigInteger())
    dispatch_event_key: Mapped[str] = mapped_column(NulFreeString(128))
    dispatch_event_kind: Mapped[str] = mapped_column(NulFreeString(32))
    dispatch_event_evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    dispatch_event_evidence_payload: Mapped[str] = mapped_column(NulFreeString(2048))
    dispatch_event_evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))

    rate_policy_version: Mapped[int] = mapped_column(StrictBigInteger())
    rate_policy_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    rate_window_start_at: Mapped[datetime] = mapped_column(UTCDateTime())
    rate_window_end_at: Mapped[datetime] = mapped_column(UTCDateTime())
    consumed_units: Mapped[int] = mapped_column(StrictBigInteger())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(RiskRateConsumptionPayload())
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))

    @classmethod
    def from_parents(
        cls,
        *,
        reservation: object,
        attempt: object,
        dispatch_event: object,
        **values: Any,
    ) -> Self:
        """Copy exact immutable parents and own every derived evidence field."""
        try:
            if set(values) - {"id"}:
                raise PersistenceValueError()
            identifier = values.get("id", uuid4())
            if type(identifier) is not UUID or identifier.int == 0:
                raise PersistenceValueError()
            if (
                type(reservation) is not RiskReservation
                or type(attempt) is not OrderAttempt
                or type(dispatch_event) is not OrderAttemptEvent
            ):
                raise PersistenceValueError()
            if any(
                getattr(reservation, field) != getattr(attempt, field)
                or getattr(reservation, field) != getattr(dispatch_event, field)
                for field in SCOPE
            ) or any(
                (
                    reservation.command_id != attempt.command_id,
                    reservation.command_operation_kind != attempt.operation_kind,
                    reservation.order_id != attempt.order_id,
                    dispatch_event.attempt_id != attempt.id,
                    dispatch_event.backend != attempt.backend,
                    dispatch_event.sequence != 1,
                    dispatch_event.event_key != derive_dispatch_event_key(attempt.id),
                    dispatch_event.event_kind != "DISPATCH_STARTED",
                    dispatch_event.evidence_schema_version != 1,
                    dispatch_event.evidence_payload != NEUTRAL_EVENT_PAYLOAD,
                    dispatch_event.evidence_sha256 != NEUTRAL_EVENT_SHA256,
                )
            ):
                raise PersistenceValueError()
            event_nullable = (
                "observed_at",
                "ingester_id",
                "ingester_generation",
                "reconciliation_ref",
                "ingester_leader_ref",
                "ingester_leader_generation",
                "ingester_recovery_ref",
                "ingester_recovery_generation",
            )
            if any(getattr(dispatch_event, field) is not None for field in event_nullable):
                raise PersistenceValueError()
            recorded_at = _utc(dispatch_event.recorded_at)
            copied: dict[str, object] = {
                "consumption_id": identifier,
                "account_id": reservation.account_id,
                "region": reservation.region,
                "environment": reservation.environment,
                "mode": reservation.mode,
                "reservation_id": reservation.id,
                "decision_id": reservation.decision_id,
                "risk_state_id": reservation.risk_state_id,
                "command_id": reservation.command_id,
                "command_operation_kind": reservation.command_operation_kind,
                "order_id": reservation.order_id,
                "product_id": reservation.product_id,
                "decision_outcome": reservation.decision_outcome,
                "risk_effect": reservation.risk_effect,
                "reservation_plan_sha256": reservation.reservation_plan_sha256,
                "decision_evaluated_at": reservation.decision_evaluated_at,
                "decision_recorded_at": reservation.decision_recorded_at,
                "reservation_created_at": reservation.created_at,
                "order_attempt_id": attempt.id,
                "attempt_number": attempt.attempt_number,
                "attempt_backend": attempt.backend,
                "attempt_prepared_at": attempt.prepared_at,
                "attempt_recorded_at": attempt.recorded_at,
                "attempt_evidence_schema_version": attempt.evidence_schema_version,
                "attempt_evidence_payload": attempt.evidence_payload,
                "attempt_evidence_sha256": attempt.evidence_sha256,
                "dispatch_event_id": dispatch_event.id,
                "dispatch_event_sequence": dispatch_event.sequence,
                "dispatch_event_kind": dispatch_event.event_kind,
                "dispatch_event_evidence_schema_version": dispatch_event.evidence_schema_version,
                "dispatch_event_evidence_payload": dispatch_event.evidence_payload,
                "dispatch_event_evidence_sha256": dispatch_event.evidence_sha256,
                "rate_policy_version": reservation.rate_policy_version,
                "rate_policy_sha256": reservation.rate_policy_sha256,
                "rate_window_start_at": reservation.rate_window_start_at,
                "rate_window_end_at": reservation.rate_window_end_at,
                "consumed_units": 1,
                "recorded_at": recorded_at,
            }
            encoded = encode_risk_rate_consumption_evidence(1, copied)
            stored = dict(copied)
            stored.pop("consumption_id")
            return cls(
                id=identifier,
                consumption_key=encoded.consumption_key,
                dispatch_event_key=encoded.dispatch_event_key,
                evidence_schema_version=1,
                evidence_payload=encoded.payload,
                evidence_sha256=encoded.sha256,
                **stored,
            )
        except Exception:
            raise PersistenceValueError() from None


install_risk_rate_consumption_validation(RiskRateConsumption)
