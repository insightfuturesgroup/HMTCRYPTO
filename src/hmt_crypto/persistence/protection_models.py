"""Requested exits, immutable authorization history and fail-closed projections.

No stored record grants production dispatch authority. PROTECTED is a claimed
projection with necessary admission checks, never a freshness/applicability proof.
Native acknowledgements and synthetic monitoring are not verified venue coverage.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.execution_evidence import EXIT_PURPOSES, PROTECTION_DECISIONS
from hmt_crypto.persistence.execution_support import (
    EvidenceRow,
    RecordedRow,
    ScopedRow,
    checked,
    evidence_constraints,
    guard_model,
    pair_check,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.protection_support import (
    AUTH_IDENTITY,
    MECHANISMS,
    PositivePrice,
    ProtectionPayload,
    StrictBoolean,
    product_fk,
    validate_protection_model,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import UTCDateTime


class ExitIntent(ScopedRow, EvidenceRow, Base):
    """Immutable requested semantic policy beside a mutable lifecycle receipt."""

    __tablename__ = "exit_intents"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        scoped_fk(__tablename__, "command_id", "command_operations"),
        product_fk(__tablename__, "entry_order_id", "orders"),
        UniqueConstraint("id", *SCOPE, "product_id", name="uq_exit_intents_id_scope_product"),
        UniqueConstraint(
            "id",
            *SCOPE,
            "product_id",
            "policy_version",
            "evidence_sha256",
            "mechanism",
            name="uq_exit_intents_policy_identity",
        ),
        UniqueConstraint(
            "id", *SCOPE, "product_id", "purpose", name="uq_exit_intents_role_identity"
        ),
        UniqueConstraint(*SCOPE, "intent_key", name="uq_exit_intents_scope_key"),
        checked("purpose", EXIT_PURPOSES),
        checked("mechanism", MECHANISMS),
        checked("state", ("REQUESTED", "ACTIVE", "CANCELLED", "COMPLETED", "FAILED")),
        bounded("intent_key"),
        CheckConstraint("reduce_only = true", name="strict_reduce_only"),
        CheckConstraint(
            "product_id >= 1 AND requested_contracts >= 1 AND policy_version >= 1 AND version >= 1",
            name="positive_counts",
        ),
        CheckConstraint(
            "(purpose = 'CLOSE_POSITION' AND requested_trigger_price IS NULL) OR "
            "(purpose IN ('STOP_LOSS','TAKE_PROFIT') AND "
            "requested_trigger_price IS NOT NULL)",
            name="price_applicability",
        ),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint(
            "(state IN ('CANCELLED','COMPLETED','FAILED') AND terminal_at IS NOT NULL "
            "AND terminal_at >= created_at AND terminal_at <= updated_at) OR "
            "(state IN ('REQUESTED','ACTIVE') AND terminal_at IS NULL)",
            name="terminal_time",
        ),
        Index("ix_exit_intents_command", "command_id", *SCOPE),
        Index("ix_exit_intents_entry_order", "entry_order_id", *SCOPE, "product_id"),
    )

    command_id: Mapped[UUID] = mapped_column(Uuid)
    entry_order_id: Mapped[UUID | None] = mapped_column(Uuid)
    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    intent_key: Mapped[str] = mapped_column(NulFreeString(128))
    purpose: Mapped[str] = mapped_column(NulFreeString(32))
    mechanism: Mapped[str] = mapped_column(NulFreeString(32))
    reduce_only: Mapped[bool] = mapped_column(
        StrictBoolean(create_constraint=True, name="reduce_only_boolean")
    )
    requested_contracts: Mapped[int] = mapped_column(StrictBigInteger())
    requested_trigger_price: Mapped[Decimal | None] = mapped_column(PositivePrice(38, 18))
    policy_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(ProtectionPayload("exit_policy"))
    state: Mapped[str] = mapped_column(NulFreeString(32))
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    terminal_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ProtectionAuthorization(ScopedRow, EvidenceRow, RecordedRow, Base):
    """Immutable local evidence; even positive categories cannot activate production."""

    __tablename__ = "protection_authorizations"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        ForeignKeyConstraint(
            [
                "exit_intent_id",
                *SCOPE,
                "product_id",
                "policy_version",
                "policy_sha256",
                "mechanism",
            ],
            [
                f"hmt_crypto.exit_intents.{c}"
                for c in (
                    "id",
                    *SCOPE,
                    "product_id",
                    "policy_version",
                    "evidence_sha256",
                    "mechanism",
                )
            ],
            name="fk_protection_authorizations_intent_policy",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            *SCOPE,
            "product_id",
            *AUTH_IDENTITY,
            name="uq_protection_authorizations_exact_identity",
        ),
        UniqueConstraint(
            *SCOPE,
            "product_id",
            "exit_intent_id",
            "policy_version",
            "backend",
            "authorization_version",
            name="uq_protection_authorizations_history",
        ),
        checked("decision", PROTECTION_DECISIONS),
        checked("backend", ("SIMULATOR", "VENUE")),
        checked("mechanism", MECHANISMS),
        sha_check("policy_sha256"),
        CheckConstraint(
            "product_id >= 1 AND policy_version >= 1 AND authorization_version >= 1",
            name="positive_counts",
        ),
        CheckConstraint(
            "(backend = 'SIMULATOR' AND mode IN ('PAPER','BACKTEST')) OR "
            "(backend = 'VENUE' AND mode = 'LIVE')",
            name="backend_mode",
        ),
        CheckConstraint(
            "decision IN ('DENIED','UNVERIFIED') OR "
            "(decision = 'PAPER_AUTHORIZED' AND mode = 'PAPER' AND backend = 'SIMULATOR') OR "
            "(decision = 'TESTNET_NATIVE_VERIFIED' AND mode = 'LIVE' AND backend = 'VENUE' "
            "AND environment = 'testnet' AND mechanism = 'NATIVE_REDUCE_ONLY') OR "
            "(decision = 'SYNTHETIC_AUTHORIZED' AND mode = 'LIVE' AND backend = 'VENUE' "
            "AND environment = 'testnet' AND mechanism = 'SYNTHETIC_MONITOR')",
            name="decision_applicability",
        ),
        CheckConstraint(
            "(decision IN ('DENIED','UNVERIFIED') AND verified_at IS NULL) OR "
            "(decision NOT IN ('DENIED','UNVERIFIED') AND verified_at IS NOT NULL "
            "AND verified_at <= recorded_at)",
            name="verification_time",
        ),
        Index(
            "ix_protection_authorizations_intent_policy",
            "exit_intent_id",
            *SCOPE,
            "product_id",
            "policy_version",
            "policy_sha256",
            "mechanism",
        ),
    )

    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    exit_intent_id: Mapped[UUID] = mapped_column(Uuid)
    policy_version: Mapped[int] = mapped_column(StrictBigInteger())
    policy_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    mechanism: Mapped[str] = mapped_column(NulFreeString(32))
    backend: Mapped[str] = mapped_column(NulFreeString(16))
    decision: Mapped[str] = mapped_column(NulFreeString(32))
    authorization_version: Mapped[int] = mapped_column(StrictBigInteger())
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    evidence_payload: Mapped[str] = mapped_column(ProtectionPayload("protection_authorization"))


_AUTH_COLUMNS = (
    "authorization_id",
    "authorization_exit_intent_id",
    "authorization_policy_version",
    "authorization_policy_sha256",
    "authorization_backend",
    "authorization_decision",
)


class PositionProtection(ScopedRow, Base):
    """One current projection; later references are non-authoritative placeholders."""

    __tablename__ = "position_protections"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(
            "id", *SCOPE, "product_id", name="uq_position_protections_id_scope_product"
        ),
        UniqueConstraint(*SCOPE, "product_id", name="uq_position_protections_scope_product"),
        ForeignKeyConstraint(
            [
                "authorization_id",
                *SCOPE,
                "product_id",
                "authorization_exit_intent_id",
                "authorization_policy_version",
                "authorization_policy_sha256",
                "authorization_backend",
                "mechanism",
                "authorization_decision",
            ],
            [
                f"hmt_crypto.protection_authorizations.{c}"
                for c in ("id", *SCOPE, "product_id", *AUTH_IDENTITY)
            ],
            name="fk_position_protections_exact_authorization",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        checked(
            "state",
            (
                "UNKNOWN",
                "NO_REQUIRED_COVERAGE",
                "UNPROTECTED",
                "VERIFYING",
                "PROTECTED",
                "SYNTHETIC_MONITORING",
            ),
        ),
        checked("mechanism", ("NONE", *MECHANISMS)),
        checked("authorization_backend", ("SIMULATOR", "VENUE")),
        checked("authorization_decision", PROTECTION_DECISIONS),
        checked(
            "reason",
            (
                "UNKNOWN",
                "NO_POSITION",
                "AWAITING_VERIFICATION",
                "VERIFIED",
                "MONITORING",
                "DENIED",
                "STALE",
                "INCOMPLETE",
            ),
        ),
        sha_check("authorization_policy_sha256"),
        CheckConstraint("product_id >= 1 AND version >= 1", name="positive_counts"),
        CheckConstraint(
            "required_contracts >= 0 AND verified_contracts >= 0", name="nonnegative_coverage"
        ),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint(
            "("
            + " AND ".join(f"{c} IS NULL" for c in _AUTH_COLUMNS)
            + " AND mechanism = 'NONE') OR ("
            + " AND ".join(f"{c} IS NOT NULL" for c in _AUTH_COLUMNS)
            + " AND authorization_policy_version >= 1 AND mechanism <> 'NONE')",
            name="authorization_all_or_none",
        ),
        *(
            pair_check(ref, f"{ref.removesuffix('_ref')}_generation")
            for ref in ("reconciliation_ref", "qualification_ref", "risk_ref")
        ),
        CheckConstraint(
            "(state = 'UNKNOWN' AND observed_position_contracts IS NULL "
            "AND required_contracts IS NULL AND verified_contracts IS NULL "
            "AND authorization_id IS NULL AND verified_at IS NULL) OR "
            "(state = 'NO_REQUIRED_COVERAGE' AND observed_position_contracts IS NOT NULL "
            "AND required_contracts IS NOT NULL AND verified_contracts IS NOT NULL "
            "AND observed_position_contracts = 0 AND required_contracts = 0 "
            "AND verified_contracts = 0 AND authorization_id IS NULL AND verified_at IS NULL) OR "
            "(state IN ('UNPROTECTED','VERIFYING','PROTECTED','SYNTHETIC_MONITORING') "
            "AND observed_position_contracts IS NOT NULL AND observed_position_contracts <> 0 "
            "AND required_contracts IS NOT NULL AND required_contracts >= 1)",
            name="position_state_consistency",
        ),
        CheckConstraint(
            "(verified_at IS NULL AND (verified_contracts IS NULL OR verified_contracts = 0)) OR "
            "(verified_at IS NOT NULL AND verified_contracts IS NOT NULL "
            "AND verified_contracts > 0 AND authorization_id IS NOT NULL "
            "AND reconciliation_ref IS NOT NULL "
            "AND verified_at >= created_at AND verified_at <= updated_at)",
            name="verified_evidence_time",
        ),
        CheckConstraint(
            "state <> 'PROTECTED' OR (required_contracts > 0 AND verified_contracts > 0 "
            "AND verified_at IS NOT NULL AND mechanism = 'NATIVE_REDUCE_ONLY' "
            "AND authorization_decision IN ('PAPER_AUTHORIZED','TESTNET_NATIVE_VERIFIED'))",
            name="protected_requires_native_evidence",
        ),
        CheckConstraint(
            "state <> 'SYNTHETIC_MONITORING' OR (mechanism = 'SYNTHETIC_MONITOR' "
            "AND authorization_decision = 'SYNTHETIC_AUTHORIZED' AND authorization_id IS NOT NULL)",
            name="synthetic_monitoring_distinct",
        ),
        Index(
            "ix_position_protections_authorization",
            "authorization_id",
            *SCOPE,
            "product_id",
            "authorization_exit_intent_id",
            "authorization_policy_version",
            "authorization_policy_sha256",
            "authorization_backend",
            "mechanism",
            "authorization_decision",
        ),
    )

    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    state: Mapped[str] = mapped_column(NulFreeString(32))
    version: Mapped[int] = mapped_column(StrictBigInteger())
    observed_position_contracts: Mapped[int | None] = mapped_column(StrictBigInteger())
    required_contracts: Mapped[int | None] = mapped_column(StrictBigInteger())
    verified_contracts: Mapped[int | None] = mapped_column(StrictBigInteger())
    mechanism: Mapped[str] = mapped_column(NulFreeString(32))
    authorization_id: Mapped[UUID | None] = mapped_column(Uuid)
    authorization_exit_intent_id: Mapped[UUID | None] = mapped_column(Uuid)
    authorization_policy_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    authorization_policy_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    authorization_backend: Mapped[str | None] = mapped_column(NulFreeString(16))
    authorization_decision: Mapped[str | None] = mapped_column(NulFreeString(32))
    # Later tables do not exist. These typed pairs confer no authority and have no FK.
    reconciliation_ref: Mapped[UUID | None] = mapped_column(Uuid)
    reconciliation_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    qualification_ref: Mapped[UUID | None] = mapped_column(Uuid)
    qualification_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    risk_ref: Mapped[UUID | None] = mapped_column(Uuid)
    risk_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reason: Mapped[str] = mapped_column(NulFreeString(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class PositionProtectionOrder(ScopedRow, RecordedRow, Base):
    """Immutable relational membership; roles must match the requested exit purpose."""

    __tablename__ = "position_protection_orders"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        product_fk(__tablename__, "protection_id", "position_protections"),
        product_fk(__tablename__, "order_id", "orders"),
        ForeignKeyConstraint(
            ["exit_intent_id", *SCOPE, "product_id", "role"],
            [f"hmt_crypto.exit_intents.{c}" for c in ("id", *SCOPE, "product_id", "purpose")],
            name="fk_position_protection_orders_intent_role",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("order_id", name="uq_position_protection_orders_order_identity"),
        UniqueConstraint(
            "protection_id",
            "exit_intent_id",
            "order_id",
            "role",
            name="uq_position_protection_orders_membership",
        ),
        checked("role", EXIT_PURPOSES),
        CheckConstraint("product_id >= 1", name="product_positive"),
        Index("ix_position_protection_orders_protection", "protection_id", *SCOPE, "product_id"),
        Index(
            "ix_position_protection_orders_intent", "exit_intent_id", *SCOPE, "product_id", "role"
        ),
    )

    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    protection_id: Mapped[UUID] = mapped_column(Uuid)
    exit_intent_id: Mapped[UUID] = mapped_column(Uuid)
    order_id: Mapped[UUID] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(NulFreeString(32))


guard_model(
    ExitIntent,
    evidence_kind="exit_policy",
    operation_field="purpose",
    protected=(
        "command_id",
        "entry_order_id",
        "product_id",
        "intent_key",
        "purpose",
        "mechanism",
        "reduce_only",
        "requested_contracts",
        "requested_trigger_price",
        "policy_version",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
        "created_at",
    ),
)
guard_model(
    ProtectionAuthorization,
    evidence_kind="protection_authorization",
    operation_field="decision",
    immutable=True,
)
guard_model(PositionProtection, protected=("product_id", "created_at"))
guard_model(PositionProtectionOrder, immutable=True)
validate_protection_model(ExitIntent, "exit_policy")
validate_protection_model(ProtectionAuthorization, "protection_authorization")
validate_protection_model(PositionProtection, "projection")
