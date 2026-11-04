"""Mutable pre-dispatch capacity reservations; never dispatch authority.

This module deliberately exposes only the owned initial-row factory.  The
reviewed PostgreSQL locking repository and the atomic command-terminalization
writer do not exist in Task 2.1d9b.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Self
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.risk_decision_models import RiskDecision
from hmt_crypto.persistence.risk_reservation_evidence import (
    RiskReservationReleaseProofPayload,
    derive_reservation_key,
)
from hmt_crypto.persistence.risk_reservation_validation import (
    install_risk_reservation_validation,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime, _utc, exact_decimal_checks

DECIMAL_FIELDS = (
    "planned_incremental_gross_exposure",
    "planned_worst_case_loss",
    "planned_collateral",
    "remaining_incremental_gross_exposure",
    "remaining_worst_case_loss",
    "remaining_collateral",
)
RELEASE_FIELDS = (
    "release_key",
    "release_reason_code",
    "release_proof_version",
    "proof_observed_at",
    "observed_terminal_command_status",
    "observed_command_version",
    "observed_command_attempt_count",
    "observed_order_version",
    "order_attempt_event_evidence_count",
    "order_attempt_evidence_count",
    "attempt_evidence_set_sha256",
    "release_proof_payload",
    "release_proof_sha256",
)
IMMUTABLE_FIELDS = (
    "id",
    *SCOPE,
    "reservation_key",
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
    "approved_contracts",
    "approved_side",
    "reference_asset_id",
    "reference_asset_symbol",
    "collateral_asset_id",
    "collateral_asset_symbol",
    "planned_incremental_gross_exposure",
    "planned_worst_case_loss",
    "planned_collateral",
    "planned_open_order_slots",
    "planned_pending_rate_units",
    "rate_policy_version",
    "rate_policy_sha256",
    "rate_window_start_at",
    "rate_window_end_at",
    "created_at",
)
DECISION_CHILD = (
    "decision_id",
    *SCOPE,
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
)
DECISION_PARENT = (
    "id",
    *SCOPE,
    "risk_state_id",
    "command_id",
    "command_operation_kind",
    "order_id",
    "product_id",
    "outcome",
    "risk_effect",
    "reservation_plan_sha256",
    "evaluated_at",
    "recorded_at",
)
RATE_CONSUMPTION_PARENT = (
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


def _composite_fk(
    name: str, child: tuple[str, ...], parent: str, parent_columns: tuple[str, ...]
) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        child,
        [f"hmt_crypto.{parent}.{field}" for field in parent_columns],
        name=name,
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class RiskReservation(ScopedRow, Base):
    """One mutable capacity hold between an approved decision and dispatch."""

    __tablename__ = "risk_reservations"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(
            *SCOPE,
            "reservation_key",
            name="uq_risk_reservations_scope_reservation_key",
        ),
        UniqueConstraint("decision_id", *SCOPE, name="uq_risk_reservations_decision_scope"),
        UniqueConstraint("command_id", *SCOPE, name="uq_risk_reservations_command_scope"),
        UniqueConstraint(*SCOPE, "release_key", name="uq_risk_reservations_scope_release_key"),
        UniqueConstraint(
            *RATE_CONSUMPTION_PARENT,
            name="uq_risk_reservations_rate_consumption_parent",
        ),
        _composite_fk(
            "fk_risk_reservations_decision_parent",
            DECISION_CHILD,
            "risk_decisions",
            DECISION_PARENT,
        ),
        scoped_fk(__tablename__, "risk_state_id", "risk_state"),
        scoped_fk(__tablename__, "command_id", "command_operations"),
        _composite_fk(
            "fk_risk_reservations_order_parent",
            ("order_id", *SCOPE, "product_id"),
            "orders",
            ("id", *SCOPE, "product_id"),
        ),
        Index("ix_risk_reservations_decision_parent", *DECISION_CHILD),
        Index("ix_risk_reservations_risk_state_parent", "risk_state_id", *SCOPE),
        Index("ix_risk_reservations_command_parent", "command_id", *SCOPE),
        Index("ix_risk_reservations_order_parent", "order_id", *SCOPE, "product_id"),
        Index("ix_risk_reservations_scope_status_product", *SCOPE, "status", "product_id"),
        checked("command_operation_kind", ("PLACE_ORDER", "MODIFY_ORDER")),
        checked("decision_outcome", ("APPROVED",)),
        checked("risk_effect", ("RISK_INCREASE", "REDUCE_ONLY")),
        checked("approved_side", ("buy", "sell")),
        checked("status", ("PENDING_DISPATCH", "RELEASED")),
        checked("release_reason_code", ("DEFINITE_NOT_DISPATCHED",)),
        checked("observed_terminal_command_status", ("FAILED", "CANCELLED")),
        bounded("reference_asset_symbol"),
        bounded("collateral_asset_symbol"),
        bounded("release_proof_payload", 16_384),
        sha_check("reservation_key"),
        sha_check("reservation_plan_sha256"),
        sha_check("rate_policy_sha256"),
        sha_check("release_key"),
        sha_check("attempt_evidence_set_sha256"),
        sha_check("release_proof_sha256"),
        CheckConstraint(
            "approved_contracts >= 1 AND reference_asset_id >= 1 "
            "AND collateral_asset_id >= 1 AND rate_policy_version >= 1 "
            "AND planned_open_order_slots >= 0 AND planned_pending_rate_units = 1",
            name="planned_integer_values",
        ),
        CheckConstraint(
            "remaining_contracts >= 0 AND remaining_open_order_slots >= 0 "
            "AND pending_rate_units >= 0",
            name="remaining_integer_values",
        ),
        CheckConstraint(
            "rate_window_start_at <= decision_evaluated_at "
            "AND decision_evaluated_at <= decision_recorded_at "
            "AND decision_recorded_at <= created_at "
            "AND created_at < rate_window_end_at",
            name="time_order",
        ),
        CheckConstraint(
            "(command_operation_kind = 'PLACE_ORDER' AND planned_open_order_slots = 1) "
            "OR (command_operation_kind = 'MODIFY_ORDER' AND planned_open_order_slots = 0)",
            name="operation_slot_shape",
        ),
        CheckConstraint(
            "(risk_effect = 'RISK_INCREASE' "
            "AND planned_incremental_gross_exposure > 0 "
            "AND planned_worst_case_loss > 0 AND planned_collateral >= 0) "
            "OR (risk_effect = 'REDUCE_ONLY' "
            "AND planned_incremental_gross_exposure = 0 "
            "AND planned_worst_case_loss = 0 AND planned_collateral = 0)",
            name="risk_effect_plan_shape",
        ),
        *(
            constraint
            for field in DECIMAL_FIELDS
            for constraint in exact_decimal_checks(field, ExactDecimal(38, 18))
        ),
        *(
            CheckConstraint(f"{field} >= 0", name=f"{field}_nonnegative")
            for field in DECIMAL_FIELDS
        ),
        CheckConstraint(
            "(status = 'PENDING_DISPATCH' AND version = 1 "
            "AND created_at = updated_at "
            "AND remaining_contracts = approved_contracts "
            "AND remaining_incremental_gross_exposure = planned_incremental_gross_exposure "
            "AND remaining_worst_case_loss = planned_worst_case_loss "
            "AND remaining_collateral = planned_collateral "
            "AND remaining_open_order_slots = planned_open_order_slots "
            "AND pending_rate_units = planned_pending_rate_units AND "
            + " AND ".join(f"{field} IS NULL" for field in RELEASE_FIELDS)
            + ") OR (status = 'RELEASED' AND version = 2 "
            "AND remaining_contracts = 0 "
            "AND remaining_incremental_gross_exposure = 0 "
            "AND remaining_worst_case_loss = 0 AND remaining_collateral = 0 "
            "AND remaining_open_order_slots = 0 AND pending_rate_units = 0 "
            "AND release_reason_code = 'DEFINITE_NOT_DISPATCHED' "
            "AND release_proof_version = 1 AND proof_observed_at >= created_at "
            "AND updated_at = proof_observed_at "
            "AND observed_command_version >= 1 "
            "AND observed_command_attempt_count >= 0 "
            "AND observed_order_version >= 1 "
            "AND order_attempt_event_evidence_count >= 0 "
            "AND order_attempt_evidence_count >= 0 "
            "AND order_attempt_event_evidence_count = order_attempt_evidence_count AND "
            + " AND ".join(f"{field} IS NOT NULL" for field in RELEASE_FIELDS)
            + ")",
            name="lifecycle_shape",
        ),
    )

    reservation_key: Mapped[str] = mapped_column(NulFreeString(64))
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

    approved_contracts: Mapped[int] = mapped_column(StrictBigInteger())
    approved_side: Mapped[str] = mapped_column(NulFreeString(16))
    reference_asset_id: Mapped[int] = mapped_column(StrictBigInteger())
    reference_asset_symbol: Mapped[str] = mapped_column(NulFreeString(128))
    collateral_asset_id: Mapped[int] = mapped_column(StrictBigInteger())
    collateral_asset_symbol: Mapped[str] = mapped_column(NulFreeString(128))
    planned_incremental_gross_exposure: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    planned_worst_case_loss: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    planned_collateral: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    planned_open_order_slots: Mapped[int] = mapped_column(StrictBigInteger())
    planned_pending_rate_units: Mapped[int] = mapped_column(StrictBigInteger())
    rate_policy_version: Mapped[int] = mapped_column(StrictBigInteger())
    rate_policy_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    rate_window_start_at: Mapped[datetime] = mapped_column(UTCDateTime())
    rate_window_end_at: Mapped[datetime] = mapped_column(UTCDateTime())

    remaining_contracts: Mapped[int] = mapped_column(StrictBigInteger())
    remaining_incremental_gross_exposure: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    remaining_worst_case_loss: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    remaining_collateral: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    remaining_open_order_slots: Mapped[int] = mapped_column(StrictBigInteger())
    pending_rate_units: Mapped[int] = mapped_column(StrictBigInteger())

    status: Mapped[str] = mapped_column(NulFreeString(32))
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    release_key: Mapped[str | None] = mapped_column(NulFreeString(64))
    release_reason_code: Mapped[str | None] = mapped_column(NulFreeString(32))
    release_proof_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    proof_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    observed_terminal_command_status: Mapped[str | None] = mapped_column(NulFreeString(16))
    observed_command_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    observed_command_attempt_count: Mapped[int | None] = mapped_column(StrictBigInteger())
    observed_order_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    order_attempt_event_evidence_count: Mapped[int | None] = mapped_column(StrictBigInteger())
    order_attempt_evidence_count: Mapped[int | None] = mapped_column(StrictBigInteger())
    attempt_evidence_set_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    release_proof_payload: Mapped[str | None] = mapped_column(RiskReservationReleaseProofPayload())
    release_proof_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))

    @classmethod
    def from_decision(cls, *, decision: object, created_at: object, **values: Any) -> Self:
        """Own one approved D9a plan and build its sole initial row shape.

        The timestamp is an input only for portable model/testing admission.  A
        future production creator must replace it with PostgreSQL wall time
        sampled after the reviewed locks are held.
        """
        try:
            if set(values) - {"id"}:
                raise PersistenceValueError()
            identifier = values.get("id", uuid4())
            if type(identifier) is not UUID or identifier.int == 0:
                raise PersistenceValueError()
            timestamp = _utc(created_at)
            if type(decision) is not RiskDecision:
                raise PersistenceValueError()
            parent = decision
            outcome = parent.outcome
            if type(outcome) is not str or outcome != "APPROVED":
                raise PersistenceValueError()
            window_start = _utc(parent.rate_window_start_at)
            evaluated_at = _utc(parent.evaluated_at)
            recorded_at = _utc(parent.recorded_at)
            window_end = _utc(parent.rate_window_end_at)
            if not (window_start <= evaluated_at <= recorded_at <= timestamp < window_end):
                raise PersistenceValueError()
            copied = {
                "id": identifier,
                "account_id": parent.account_id,
                "region": parent.region,
                "environment": parent.environment,
                "mode": parent.mode,
                "decision_id": parent.id,
                "risk_state_id": parent.risk_state_id,
                "command_id": parent.command_id,
                "command_operation_kind": parent.command_operation_kind,
                "order_id": parent.order_id,
                "product_id": parent.product_id,
                "decision_outcome": outcome,
                "risk_effect": parent.risk_effect,
                "reservation_plan_sha256": parent.reservation_plan_sha256,
                "decision_evaluated_at": parent.evaluated_at,
                "decision_recorded_at": parent.recorded_at,
                "approved_contracts": parent.approved_contracts,
                "approved_side": parent.approved_side,
                "reference_asset_id": parent.reference_asset_id,
                "reference_asset_symbol": parent.reference_asset_symbol,
                "collateral_asset_id": parent.collateral_asset_id,
                "collateral_asset_symbol": parent.collateral_asset_symbol,
                "planned_incremental_gross_exposure": parent.reserved_incremental_gross_exposure,
                "planned_worst_case_loss": parent.reserved_worst_case_loss,
                "planned_collateral": parent.reserved_collateral,
                "planned_open_order_slots": parent.reserved_open_order_slots,
                "planned_pending_rate_units": parent.reserved_pending_rate_units,
                "rate_policy_version": parent.rate_policy_version,
                "rate_policy_sha256": parent.rate_policy_sha256,
                "rate_window_start_at": parent.rate_window_start_at,
                "rate_window_end_at": parent.rate_window_end_at,
            }
            copied["reservation_key"] = derive_reservation_key(
                1,
                {
                    "account_id": copied["account_id"],
                    "command_id": copied["command_id"],
                    "command_operation_kind": copied["command_operation_kind"],
                    "decision_evaluated_at": copied["decision_evaluated_at"],
                    "decision_id": copied["decision_id"],
                    "decision_outcome": copied["decision_outcome"],
                    "decision_recorded_at": copied["decision_recorded_at"],
                    "environment": copied["environment"],
                    "mode": copied["mode"],
                    "order_id": copied["order_id"],
                    "product_id": copied["product_id"],
                    "region": copied["region"],
                    "reservation_plan_sha256": copied["reservation_plan_sha256"],
                    "risk_effect": copied["risk_effect"],
                    "risk_state_id": copied["risk_state_id"],
                },
            )
            return cls(
                **copied,
                remaining_contracts=copied["approved_contracts"],
                remaining_incremental_gross_exposure=copied["planned_incremental_gross_exposure"],
                remaining_worst_case_loss=copied["planned_worst_case_loss"],
                remaining_collateral=copied["planned_collateral"],
                remaining_open_order_slots=copied["planned_open_order_slots"],
                pending_rate_units=copied["planned_pending_rate_units"],
                status="PENDING_DISPATCH",
                version=1,
                created_at=timestamp,
                updated_at=timestamp,
                **{field: None for field in RELEASE_FIELDS},
            )
        except Exception:
            raise PersistenceValueError() from None


install_risk_reservation_validation(RiskReservation, IMMUTABLE_FIELDS, RELEASE_FIELDS)
