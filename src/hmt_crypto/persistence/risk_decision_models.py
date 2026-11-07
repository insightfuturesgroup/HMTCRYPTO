"""Append-only historical risk decisions; never current dispatch authority."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, Self, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.risk_decision_contracts import (
    CONTEXT_FIELDS,
    RESERVATION_PLAN_INPUT_FIELDS,
    ReservationPlanV1,
)
from hmt_crypto.persistence.risk_decision_evidence import (
    RiskDecisionEvidencePayload,
    encode_risk_decision_evidence,
)
from hmt_crypto.persistence.risk_decision_validation import (
    install_risk_decision_validation,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import (
    ExactDecimal,
    UTCDateTime,
    exact_decimal_checks,
)

COMMAND_FIELDS = (
    "command_id",
    "command_operation_kind",
    "command_evidence_schema_version",
    "command_evidence_payload",
    "command_evidence_sha256",
    "command_requested_contracts",
)
PLAN_FIELDS = (
    "approved_contracts",
    "approved_side",
    "reference_asset_id",
    "reference_asset_symbol",
    "collateral_asset_id",
    "collateral_asset_symbol",
    "reserved_incremental_gross_exposure",
    "reserved_worst_case_loss",
    "reserved_collateral",
    "reserved_open_order_slots",
    "reserved_pending_rate_units",
    "rate_policy_version",
    "rate_policy_sha256",
    "rate_window_start_at",
    "rate_window_end_at",
    "reservation_plan_sha256",
)
EVIDENCE_FIELDS = (
    "check_set_version",
    "check_set_sha256",
    "evidence_schema_version",
    "evidence_payload",
    "evidence_sha256",
)


class _CommandEvidenceParent(Protocol):
    id: UUID
    operation_kind: str
    evidence_schema_version: int
    evidence_payload: str
    evidence_sha256: str


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


class RiskDecision(ScopedRow, Base):
    """One immutable atomic policy-evaluation record."""

    __tablename__ = "risk_decisions"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(*SCOPE, "decision_key", name="uq_risk_decisions_scope_decision_key"),
        UniqueConstraint(
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
            name="uq_risk_decisions_reservation_parent",
        ),
        scoped_fk(__tablename__, "risk_state_id", "risk_state"),
        _composite_fk(
            "fk_risk_decisions_command_parent",
            ("command_id", *SCOPE, "command_operation_kind", "command_evidence_sha256"),
            "command_operations",
            ("id", *SCOPE, "operation_kind", "evidence_sha256"),
        ),
        _composite_fk(
            "fk_risk_decisions_order_parent",
            (
                "order_id",
                *SCOPE,
                "product_id",
                "order_origin_command_id",
                "order_requested_contracts",
                "order_requested_side",
                "order_requested_reduce_only",
            ),
            "orders",
            (
                "id",
                *SCOPE,
                "product_id",
                "command_id",
                "requested_contracts",
                "requested_side",
                "requested_reduce_only",
            ),
        ),
        scoped_fk(__tablename__, "leadership_lease_id", "leadership_leases"),
        scoped_fk(__tablename__, "recovery_incarnation_id", "recovery_incarnations"),
        _composite_fk(
            "fk_risk_decisions_reconciliation_parent",
            (
                "reconciliation_run_id",
                *SCOPE,
                "reconciliation_requirements_version",
                "reconciliation_requirements_sha256",
            ),
            "reconciliation_runs",
            ("id", *SCOPE, "requirements_version", "requirements_sha256"),
        ),
        _composite_fk(
            "fk_risk_decisions_pnl_parent",
            (
                "pnl_snapshot_id",
                *SCOPE,
                "pnl_completeness",
                "pnl_calculation_version",
                "pnl_reference_asset_id",
                "pnl_reference_asset_symbol",
                "pnl_evidence_sha256",
                "pnl_input_set_sha256",
            ),
            "pnl_snapshots",
            (
                "id",
                *SCOPE,
                "completeness",
                "calculation_version",
                "reference_asset_id",
                "reference_asset_symbol",
                "evidence_sha256",
                "input_set_sha256",
            ),
        ),
        Index("ix_risk_decisions_risk_state_parent", "risk_state_id", *SCOPE),
        Index(
            "ix_risk_decisions_command_parent",
            "command_id",
            *SCOPE,
            "command_operation_kind",
            "command_evidence_sha256",
        ),
        Index(
            "ix_risk_decisions_order_parent",
            "order_id",
            *SCOPE,
            "product_id",
            "order_origin_command_id",
            "order_requested_contracts",
            "order_requested_side",
            "order_requested_reduce_only",
        ),
        Index("ix_risk_decisions_leadership_parent", "leadership_lease_id", *SCOPE),
        Index("ix_risk_decisions_recovery_parent", "recovery_incarnation_id", *SCOPE),
        Index(
            "ix_risk_decisions_reconciliation_parent",
            "reconciliation_run_id",
            *SCOPE,
            "reconciliation_requirements_version",
            "reconciliation_requirements_sha256",
        ),
        Index(
            "ix_risk_decisions_pnl_parent",
            "pnl_snapshot_id",
            *SCOPE,
            "pnl_completeness",
            "pnl_calculation_version",
            "pnl_reference_asset_id",
            "pnl_reference_asset_symbol",
            "pnl_evidence_sha256",
            "pnl_input_set_sha256",
        ),
        checked("command_operation_kind", ("PLACE_ORDER", "MODIFY_ORDER")),
        checked("outcome", ("APPROVED", "DENIED")),
        checked("risk_effect", ("RISK_INCREASE", "REDUCE_ONLY")),
        checked("order_requested_side", ("buy", "sell")),
        checked("observed_halt_set_state", ("UNINITIALIZED", "INITIALIZED")),
        checked("observed_reconciliation_state", ("UNKNOWN", "BLOCKED", "READY_CLAIM")),
        checked("observed_accounting_state", ("UNKNOWN", "PARTIAL", "COMPLETE_CLAIM")),
        checked("reconciliation_completeness", ("UNKNOWN", "PARTIAL", "COMPLETE")),
        checked("pnl_completeness", ("PARTIAL", "COMPLETE")),
        checked("approved_side", ("buy", "sell")),
        bounded("decision_key"),
        bounded("command_evidence_payload", 2048),
        bounded("evaluation_reference_asset_symbol"),
        bounded("evaluation_collateral_asset_symbol"),
        bounded("pnl_reference_asset_symbol"),
        bounded("reference_asset_symbol"),
        bounded("collateral_asset_symbol"),
        sha_check("command_evidence_sha256"),
        sha_check("policy_sha256"),
        sha_check("check_set_sha256"),
        sha_check("reconciliation_requirements_sha256"),
        sha_check("pnl_evidence_sha256"),
        sha_check("pnl_input_set_sha256"),
        sha_check("rate_policy_sha256"),
        sha_check("reservation_plan_sha256"),
        sha_check("evidence_sha256"),
        CheckConstraint(
            "command_evidence_schema_version = 1 AND check_set_version = 1 "
            "AND evidence_schema_version = 1",
            name="schema_versions_v1",
        ),
        CheckConstraint(
            "product_id >= 1 AND order_requested_contracts >= 1 "
            "AND policy_version >= 1 AND observed_risk_state_version >= 1 "
            "AND observed_command_version >= 1 AND observed_claim_generation >= 1 "
            "AND observed_order_version >= 1",
            name="required_positive_values",
        ),
        CheckConstraint(
            "observed_halt_revision >= 0 AND observed_order_transition_sequence >= 0",
            name="required_nonnegative_values",
        ),
        CheckConstraint(
            "command_requested_contracts IS NULL OR command_requested_contracts >= 1",
            name="command_requested_contracts_positive",
        ),
        CheckConstraint(
            "evaluation_reference_asset_id IS NULL OR evaluation_reference_asset_id >= 1",
            name="evaluation_reference_asset_positive",
        ),
        CheckConstraint(
            "evaluation_collateral_asset_id IS NULL OR evaluation_collateral_asset_id >= 1",
            name="evaluation_collateral_asset_positive",
        ),
        CheckConstraint(
            "(evaluation_reference_asset_id IS NULL) = (evaluation_reference_asset_symbol IS NULL)",
            name="evaluation_reference_asset_pair",
        ),
        CheckConstraint(
            "(evaluation_collateral_asset_id IS NULL) = "
            "(evaluation_collateral_asset_symbol IS NULL)",
            name="evaluation_collateral_asset_pair",
        ),
        CheckConstraint("evaluated_at <= recorded_at", name="time_order"),
        CheckConstraint(
            "evaluated_at < observed_claim_expires_at", name="claim_live_at_evaluation"
        ),
        CheckConstraint(
            "(mode IN ('PAPER','BACKTEST') AND leadership_lease_id IS NULL "
            "AND recovery_incarnation_id IS NULL AND observed_leadership_owner_id IS NULL "
            "AND observed_leadership_generation IS NULL "
            "AND observed_leadership_expires_at IS NULL "
            "AND observed_leadership_version IS NULL "
            "AND observed_command_leader_ref IS NULL "
            "AND observed_command_leader_generation IS NULL "
            "AND observed_command_recovery_ref IS NULL "
            "AND observed_command_recovery_generation IS NULL) OR "
            "(mode = 'LIVE' AND leadership_lease_id IS NOT NULL "
            "AND recovery_incarnation_id IS NOT NULL "
            "AND observed_leadership_owner_id IS NOT NULL "
            "AND observed_leadership_owner_id = observed_claim_owner_id "
            "AND observed_leadership_generation IS NOT NULL "
            "AND observed_leadership_generation >= 1 "
            "AND observed_leadership_expires_at IS NOT NULL "
            "AND observed_leadership_expires_at > evaluated_at "
            "AND observed_leadership_version IS NOT NULL "
            "AND observed_leadership_version >= 1 "
            "AND observed_command_leader_ref IS NOT NULL "
            "AND observed_command_recovery_ref IS NOT NULL "
            "AND observed_command_leader_generation IS NOT NULL "
            "AND observed_command_recovery_generation IS NOT NULL "
            "AND observed_command_leader_ref = leadership_lease_id "
            "AND observed_command_recovery_ref = recovery_incarnation_id "
            "AND observed_command_leader_generation = observed_leadership_generation "
            "AND observed_command_recovery_generation = observed_leadership_generation)",
            name="leadership_shape",
        ),
        CheckConstraint(
            "(observed_reconciliation_state = 'UNKNOWN' "
            "AND reconciliation_run_id IS NULL AND reconciliation_run_version IS NULL "
            "AND reconciliation_requirements_version IS NULL "
            "AND reconciliation_requirements_sha256 IS NULL "
            "AND reconciliation_observed_at IS NULL "
            "AND reconciliation_completeness IS NULL) OR "
            "(observed_reconciliation_state IN ('BLOCKED','READY_CLAIM') "
            "AND reconciliation_run_id IS NOT NULL "
            "AND reconciliation_run_version IS NOT NULL "
            "AND reconciliation_run_version >= 1 "
            "AND reconciliation_requirements_version IS NOT NULL "
            "AND reconciliation_requirements_version >= 1 "
            "AND reconciliation_requirements_sha256 IS NOT NULL "
            "AND reconciliation_observed_at IS NOT NULL "
            "AND reconciliation_observed_at <= evaluated_at "
            "AND reconciliation_completeness IS NOT NULL "
            "AND (observed_reconciliation_state <> 'READY_CLAIM' "
            "OR reconciliation_completeness = 'COMPLETE'))",
            name="reconciliation_shape",
        ),
        CheckConstraint(
            "(observed_accounting_state = 'UNKNOWN' AND pnl_snapshot_id IS NULL "
            "AND pnl_completeness IS NULL AND pnl_calculation_version IS NULL "
            "AND pnl_reference_asset_id IS NULL AND pnl_reference_asset_symbol IS NULL "
            "AND pnl_evidence_sha256 IS NULL AND pnl_input_set_sha256 IS NULL) OR "
            "(observed_accounting_state = 'PARTIAL' AND pnl_snapshot_id IS NOT NULL "
            "AND pnl_completeness IS NOT NULL AND pnl_completeness = 'PARTIAL' "
            "AND pnl_calculation_version IS NOT NULL "
            "AND pnl_calculation_version >= 1 "
            "AND pnl_reference_asset_id IS NOT NULL AND pnl_reference_asset_id >= 1 "
            "AND pnl_reference_asset_symbol IS NOT NULL "
            "AND pnl_evidence_sha256 IS NOT NULL AND pnl_input_set_sha256 IS NOT NULL) OR "
            "(observed_accounting_state = 'COMPLETE_CLAIM' AND pnl_snapshot_id IS NOT NULL "
            "AND pnl_completeness IS NOT NULL AND pnl_completeness = 'COMPLETE' "
            "AND pnl_calculation_version IS NOT NULL "
            "AND pnl_calculation_version >= 1 "
            "AND pnl_reference_asset_id IS NOT NULL AND pnl_reference_asset_id >= 1 "
            "AND pnl_reference_asset_symbol IS NOT NULL "
            "AND pnl_evidence_sha256 IS NOT NULL AND pnl_input_set_sha256 IS NOT NULL)",
            name="pnl_shape",
        ),
        CheckConstraint(
            "pnl_snapshot_id IS NULL OR "
            "(evaluation_reference_asset_id IS NOT NULL "
            "AND evaluation_reference_asset_symbol IS NOT NULL "
            "AND pnl_reference_asset_id = evaluation_reference_asset_id "
            "AND pnl_reference_asset_symbol = evaluation_reference_asset_symbol)",
            name="pnl_evaluation_denomination",
        ),
        CheckConstraint(
            "(outcome = 'DENIED' AND "
            + " AND ".join(f"{field} IS NULL" for field in PLAN_FIELDS)
            + ") OR (outcome = 'APPROVED' AND "
            + " AND ".join(f"{field} IS NOT NULL" for field in PLAN_FIELDS)
            + ")",
            name="reservation_plan_shape",
        ),
        CheckConstraint(
            "outcome <> 'APPROVED' OR (observed_halt_set_state = 'INITIALIZED' "
            "AND observed_reconciliation_state = 'READY_CLAIM' "
            "AND reconciliation_completeness = 'COMPLETE')",
            name="approved_ready_claim",
        ),
        CheckConstraint(
            "outcome <> 'APPROVED' OR (approved_contracts >= 1 "
            "AND reference_asset_id >= 1 AND collateral_asset_id >= 1 "
            "AND reserved_open_order_slots >= 0 AND reserved_pending_rate_units = 1 "
            "AND rate_policy_version >= 1 AND rate_window_start_at <= evaluated_at "
            "AND evaluated_at < rate_window_end_at "
            "AND evaluation_reference_asset_id IS NOT NULL "
            "AND evaluation_reference_asset_symbol IS NOT NULL "
            "AND evaluation_collateral_asset_id IS NOT NULL "
            "AND evaluation_collateral_asset_symbol IS NOT NULL "
            "AND reference_asset_id = evaluation_reference_asset_id "
            "AND reference_asset_symbol = evaluation_reference_asset_symbol "
            "AND collateral_asset_id = evaluation_collateral_asset_id "
            "AND collateral_asset_symbol = evaluation_collateral_asset_symbol)",
            name="approved_plan_values",
        ),
        CheckConstraint(
            "(order_requested_reduce_only AND risk_effect = 'REDUCE_ONLY') OR "
            "(NOT order_requested_reduce_only AND risk_effect = 'RISK_INCREASE')",
            name="order_risk_effect",
        ),
        CheckConstraint(
            "(command_operation_kind = 'PLACE_ORDER' "
            "AND command_id = order_origin_command_id "
            "AND command_requested_contracts IS NOT NULL "
            "AND command_requested_contracts = order_requested_contracts) OR "
            "command_operation_kind = 'MODIFY_ORDER'",
            name="operation_order_shape",
        ),
        CheckConstraint(
            "outcome <> 'APPROVED' OR (command_requested_contracts IS NOT NULL "
            "AND approved_contracts = command_requested_contracts "
            "AND approved_side = order_requested_side "
            "AND ((command_operation_kind = 'PLACE_ORDER' "
            "AND reserved_open_order_slots = 1) OR "
            "(command_operation_kind = 'MODIFY_ORDER' "
            "AND reserved_open_order_slots = 0)))",
            name="approved_operation_plan",
        ),
        CheckConstraint(
            "outcome <> 'APPROVED' OR risk_effect <> 'RISK_INCREASE' OR "
            "(observed_accounting_state = 'COMPLETE_CLAIM' "
            "AND pnl_completeness = 'COMPLETE' "
            "AND reserved_incremental_gross_exposure > 0 "
            "AND reserved_worst_case_loss > 0)",
            name="approved_risk_increase",
        ),
        CheckConstraint(
            "outcome <> 'APPROVED' OR risk_effect <> 'REDUCE_ONLY' OR "
            "(reserved_incremental_gross_exposure = 0 "
            "AND reserved_worst_case_loss = 0 AND reserved_collateral = 0)",
            name="approved_reduce_only",
        ),
        *(
            constraint
            for field in (
                "reserved_incremental_gross_exposure",
                "reserved_worst_case_loss",
                "reserved_collateral",
            )
            for constraint in exact_decimal_checks(field, ExactDecimal(38, 18))
        ),
        CheckConstraint(
            "reserved_incremental_gross_exposure IS NULL "
            "OR reserved_incremental_gross_exposure >= 0",
            name="reserved_incremental_gross_exposure_nonnegative",
        ),
        CheckConstraint(
            "reserved_worst_case_loss IS NULL OR reserved_worst_case_loss >= 0",
            name="reserved_worst_case_loss_nonnegative",
        ),
        CheckConstraint(
            "reserved_collateral IS NULL OR reserved_collateral >= 0",
            name="reserved_collateral_nonnegative",
        ),
    )

    decision_key: Mapped[str] = mapped_column(NulFreeString(128))
    risk_state_id: Mapped[UUID] = mapped_column(Uuid)
    command_id: Mapped[UUID] = mapped_column(Uuid)
    command_operation_kind: Mapped[str] = mapped_column(NulFreeString(32))
    command_evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    command_evidence_payload: Mapped[str] = mapped_column(NulFreeString(2048))
    command_evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    command_requested_contracts: Mapped[int | None] = mapped_column(StrictBigInteger())
    order_id: Mapped[UUID] = mapped_column(Uuid)
    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    order_origin_command_id: Mapped[UUID] = mapped_column(Uuid)
    order_requested_contracts: Mapped[int] = mapped_column(StrictBigInteger())
    order_requested_side: Mapped[str] = mapped_column(NulFreeString(16))
    order_requested_reduce_only: Mapped[bool] = mapped_column(Boolean)
    outcome: Mapped[str] = mapped_column(NulFreeString(16))
    risk_effect: Mapped[str] = mapped_column(NulFreeString(16))
    policy_version: Mapped[int] = mapped_column(StrictBigInteger())
    policy_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    check_set_version: Mapped[int] = mapped_column(StrictBigInteger())
    check_set_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    evaluation_reference_asset_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    evaluation_reference_asset_symbol: Mapped[str | None] = mapped_column(NulFreeString(128))
    evaluation_collateral_asset_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    evaluation_collateral_asset_symbol: Mapped[str | None] = mapped_column(NulFreeString(128))
    observed_risk_state_version: Mapped[int] = mapped_column(StrictBigInteger())
    observed_halt_set_state: Mapped[str] = mapped_column(NulFreeString(32))
    observed_halt_revision: Mapped[int] = mapped_column(StrictBigInteger())
    observed_reconciliation_state: Mapped[str] = mapped_column(NulFreeString(32))
    observed_accounting_state: Mapped[str] = mapped_column(NulFreeString(32))
    observed_command_version: Mapped[int] = mapped_column(StrictBigInteger())
    observed_claim_owner_id: Mapped[UUID] = mapped_column(Uuid)
    observed_claim_generation: Mapped[int] = mapped_column(StrictBigInteger())
    observed_claim_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    observed_order_version: Mapped[int] = mapped_column(StrictBigInteger())
    observed_order_transition_sequence: Mapped[int] = mapped_column(StrictBigInteger())
    observed_command_leader_ref: Mapped[UUID | None] = mapped_column(Uuid)
    observed_command_leader_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    observed_command_recovery_ref: Mapped[UUID | None] = mapped_column(Uuid)
    observed_command_recovery_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    leadership_lease_id: Mapped[UUID | None] = mapped_column(Uuid)
    recovery_incarnation_id: Mapped[UUID | None] = mapped_column(Uuid)
    observed_leadership_owner_id: Mapped[UUID | None] = mapped_column(Uuid)
    observed_leadership_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    observed_leadership_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    observed_leadership_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    reconciliation_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    reconciliation_run_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    reconciliation_requirements_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    reconciliation_requirements_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    reconciliation_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reconciliation_completeness: Mapped[str | None] = mapped_column(NulFreeString(16))
    pnl_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    pnl_completeness: Mapped[str | None] = mapped_column(NulFreeString(16))
    pnl_calculation_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    pnl_reference_asset_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    pnl_reference_asset_symbol: Mapped[str | None] = mapped_column(NulFreeString(128))
    pnl_evidence_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    pnl_input_set_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    approved_contracts: Mapped[int | None] = mapped_column(StrictBigInteger())
    approved_side: Mapped[str | None] = mapped_column(NulFreeString(16))
    reference_asset_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    reference_asset_symbol: Mapped[str | None] = mapped_column(NulFreeString(128))
    collateral_asset_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    collateral_asset_symbol: Mapped[str | None] = mapped_column(NulFreeString(128))
    reserved_incremental_gross_exposure: Mapped[Decimal | None] = mapped_column(
        ExactDecimal(38, 18)
    )
    reserved_worst_case_loss: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    reserved_collateral: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    reserved_open_order_slots: Mapped[int | None] = mapped_column(StrictBigInteger())
    reserved_pending_rate_units: Mapped[int | None] = mapped_column(StrictBigInteger())
    rate_policy_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    rate_policy_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    rate_window_start_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    rate_window_end_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reservation_plan_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(RiskDecisionEvidencePayload())
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    @classmethod
    def from_evidence(
        cls,
        *,
        command: object,
        check_results: object,
        reservation_plan: object,
        **values: Any,
    ) -> Self:
        """Own immutable command/check/plan inputs and derive all evidence fields."""
        forbidden = set(COMMAND_FIELDS) | set(PLAN_FIELDS) | set(EVIDENCE_FIELDS)
        if forbidden & values.keys() or values.keys() - set(cls.__table__.c.keys()):
            raise PersistenceValueError()
        try:
            owned = dict(values)
            owned.setdefault("id", uuid4())
            parent = cast(_CommandEvidenceParent, command)
            command_values = {
                "command_id": parent.id,
                "command_operation_kind": parent.operation_kind,
                "command_evidence_schema_version": parent.evidence_schema_version,
                "command_evidence_payload": parent.evidence_payload,
                "command_evidence_sha256": parent.evidence_sha256,
            }
            intent = json.loads(parent.evidence_payload)
            command_values["command_requested_contracts"] = intent.get("requested_contracts")
            context = {
                field: (
                    command_values[field]
                    if field in command_values
                    else owned["id"]
                    if field == "decision_id"
                    else owned[field]
                )
                for field in CONTEXT_FIELDS
            }
            encoded = encode_risk_decision_evidence(1, context, check_results, reservation_plan)
            if reservation_plan is None:
                plan_values: dict[str, object] = {field: None for field in PLAN_FIELDS}
            else:
                plan = ReservationPlanV1.model_validate(reservation_plan)
                plan_values = {
                    field: getattr(plan, field) for field in RESERVATION_PLAN_INPUT_FIELDS
                }
                plan_values["reservation_plan_sha256"] = encoded.reservation_plan_sha256
            return cls(
                **owned,
                **command_values,
                **plan_values,
                check_set_version=1,
                check_set_sha256=encoded.check_set_sha256,
                evidence_schema_version=1,
                evidence_payload=encoded.payload,
                evidence_sha256=encoded.evidence_sha256,
            )
        except Exception:
            raise PersistenceValueError() from None


install_risk_decision_validation(RiskDecision, PLAN_FIELDS)
