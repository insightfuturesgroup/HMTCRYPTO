"""Append-only reconciliation drift and operator-review evidence.

These rows classify discrepancies and record review/notice evidence only. They
cannot resolve uncertainty, clear a halt, authorize execution, or establish
readiness. Core/raw SQL cross-column digest and append-only enforcement remains
deferred to reviewed PostgreSQL triggers and grants.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Self, cast
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    Uuid,
    event,
    select,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection, Dialect
from sqlalchemy.orm import Mapped, mapped_column, object_session
from sqlalchemy.orm.state import InstanceState

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    evidence_constraints,
    guard_model,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.reconciliation_evidence import (
    ACKNOWLEDGEMENT_KINDS,
    ACKNOWLEDGEMENT_POLICIES,
    DRIFT_KINDS,
    REASON_CODES,
    SEVERITIES,
    SUBJECT_KINDS,
    AcknowledgementEvidencePayload,
    DriftEvidencePayload,
    decode_acknowledgement_evidence,
    decode_drift_evidence,
    encode_acknowledgement_evidence,
    encode_drift_evidence,
)
from hmt_crypto.persistence.reconciliation_query import RESOURCE_KINDS
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import UTCDateTime

EVIDENCE_FIELDS = ("evidence_schema_version", "evidence_payload", "evidence_sha256")
DRIFT_SEMANTIC_COLUMNS = (
    "drift_kind",
    "subject_kind",
    "subject_key_sha256",
    "product_id",
    "asset_id",
    "severity",
    "acknowledgement_policy",
)
ACKNOWLEDGEMENT_SEMANTIC_COLUMNS = (
    "drift_id",
    "acknowledgement_kind",
    "acknowledgement_policy",
    "safety_effect",
    "actor_ref",
    "idempotency_key",
    "reason_code",
)
DRIFT_PAYLOAD_ONLY = frozenset({"comparison", "expected_sha256", "observed_sha256"})
ACKNOWLEDGEMENT_PAYLOAD_ONLY = frozenset({"supporting_evidence_sha256"})


class BoundedReviewText(NulFreeString):
    """Reject blank, NUL-bearing, non-string, or overlong review identity text."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        result = super().process_bind_param(value, dialect)
        if result is None or not 1 <= len(result) <= 128 or not result.strip():
            raise PersistenceValueError()
        return result

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)


def _resource_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["resource_id", *SCOPE, "run_id", "resource_kind"],
        [
            f"hmt_crypto.reconciliation_resources.{field}"
            for field in ("id", *SCOPE, "run_id", "resource_kind")
        ],
        name="fk_reconciliation_drifts_resource_scope",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class ReconciliationDrift(ScopedRow, Base):
    """A deterministic per-run discrepancy classification with safe evidence."""

    __tablename__ = "reconciliation_drifts"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        UniqueConstraint(
            *SCOPE,
            "run_id",
            "drift_kind",
            "subject_kind",
            "subject_key_sha256",
            name="uq_reconciliation_drifts_identity",
        ),
        UniqueConstraint(
            "id",
            *SCOPE,
            "run_id",
            "drift_kind",
            "acknowledgement_policy",
            name="uq_reconciliation_drifts_ack_parent",
        ),
        scoped_fk(__tablename__, "run_id", "reconciliation_runs"),
        _resource_fk(),
        Index("ix_reconciliation_drifts_run_parent", "run_id", *SCOPE),
        Index(
            "ix_reconciliation_drifts_resource_parent",
            "resource_id",
            *SCOPE,
            "run_id",
            "resource_kind",
        ),
        checked("resource_kind", RESOURCE_KINDS),
        checked("drift_kind", DRIFT_KINDS),
        checked("subject_kind", SUBJECT_KINDS),
        checked("severity", SEVERITIES),
        checked("acknowledgement_policy", ACKNOWLEDGEMENT_POLICIES),
        CheckConstraint(
            "(resource_id IS NULL AND resource_kind IS NULL) OR "
            "(resource_id IS NOT NULL AND resource_kind IS NOT NULL)",
            name="resource_pair",
        ),
        sha_check("subject_key_sha256"),
        CheckConstraint("product_id IS NULL OR product_id >= 1", name="product_positive"),
        CheckConstraint("asset_id IS NULL OR asset_id >= 1", name="asset_positive"),
        CheckConstraint(
            "drift_kind NOT IN ('RESOURCE_INCOMPLETE','UNRESOLVED_OPERATION',"
            "'UNKNOWN_EXPOSURE','INADEQUATE_PROTECTION','UNCLASSIFIED') OR "
            "(severity = 'CRITICAL' AND acknowledgement_policy = 'NON_OVERRIDABLE')",
            name="hard_non_overridable",
        ),
        CheckConstraint("detected_at <= recorded_at", name="time_order"),
    )

    run_id: Mapped[UUID] = mapped_column(Uuid)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid)
    resource_kind: Mapped[str | None] = mapped_column(NulFreeString(32))
    drift_kind: Mapped[str] = mapped_column(NulFreeString(32))
    subject_kind: Mapped[str] = mapped_column(NulFreeString(32))
    subject_key_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    product_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    asset_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    severity: Mapped[str] = mapped_column(NulFreeString(16))
    acknowledgement_policy: Mapped[str] = mapped_column(NulFreeString(16))
    evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(DriftEvidencePayload())
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    @classmethod
    def from_evidence(cls, **values: Any) -> Self:
        """Own safe discrepancy inputs and derive canonical evidence bytes."""
        if set(EVIDENCE_FIELDS) & values.keys():
            raise PersistenceValueError()
        allowed = (set(cls.__table__.c.keys()) - set(EVIDENCE_FIELDS)) | set(DRIFT_PAYLOAD_ONLY)
        if values.keys() - allowed:
            raise PersistenceValueError()
        owned = dict(values)
        try:
            payload_values = {
                "comparison": owned.pop("comparison"),
                "subject_key_sha256": owned["subject_key_sha256"],
                "product_id": owned["product_id"],
                "asset_id": owned["asset_id"],
                "expected_sha256": owned.pop("expected_sha256"),
                "observed_sha256": owned.pop("observed_sha256"),
            }
            payload, digest = encode_drift_evidence(
                1,
                owned["drift_kind"],
                owned["subject_kind"],
                owned["severity"],
                owned["acknowledgement_policy"],
                payload_values,
            )
        except Exception:
            raise PersistenceValueError() from None
        return cls(
            evidence_schema_version=1,
            evidence_payload=payload,
            evidence_sha256=digest,
            **owned,
        )


class ReconciliationAcknowledgement(ScopedRow, Base):
    """An operator review/notice event with an immutable literal no-safety effect."""

    __tablename__ = "reconciliation_acknowledgements"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        UniqueConstraint(
            *SCOPE,
            "idempotency_key",
            name="uq_reconciliation_acknowledgements_scope_idempotency",
        ),
        ForeignKeyConstraint(
            [
                "drift_id",
                *SCOPE,
                "run_id",
                "drift_kind",
                "acknowledgement_policy",
            ],
            [
                f"hmt_crypto.reconciliation_drifts.{field}"
                for field in (
                    "id",
                    *SCOPE,
                    "run_id",
                    "drift_kind",
                    "acknowledgement_policy",
                )
            ],
            name="fk_reconciliation_acknowledgements_drift_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index(
            "ix_reconciliation_acknowledgements_drift_parent",
            "drift_id",
            *SCOPE,
            "run_id",
            "drift_kind",
            "acknowledgement_policy",
        ),
        checked("drift_kind", DRIFT_KINDS),
        checked("acknowledgement_policy", ACKNOWLEDGEMENT_POLICIES),
        checked("acknowledgement_kind", ACKNOWLEDGEMENT_KINDS),
        checked("reason_code", REASON_CODES),
        bounded("idempotency_key"),
        bounded("actor_ref"),
        CheckConstraint("safety_effect = 'NONE'", name="safety_effect"),
        CheckConstraint("actor_kind = 'OPERATOR'", name="actor_kind"),
        CheckConstraint(
            "(acknowledgement_policy = 'REVIEWABLE' "
            "AND acknowledgement_kind = 'REVIEW_EVIDENCE') OR "
            "(acknowledgement_policy = 'NON_OVERRIDABLE' "
            "AND acknowledgement_kind = 'NOTICE_EVIDENCE')",
            name="policy_kind_shape",
        ),
        CheckConstraint(
            "acknowledgement_policy <> 'NON_OVERRIDABLE' OR reason_code = 'OPERATOR_NOTED'",
            name="non_overridable_reason",
        ),
        CheckConstraint("acknowledged_at <= recorded_at", name="time_order"),
    )

    drift_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    drift_kind: Mapped[str] = mapped_column(NulFreeString(32))
    acknowledgement_policy: Mapped[str] = mapped_column(NulFreeString(16))
    idempotency_key: Mapped[str] = mapped_column(BoundedReviewText(128))
    acknowledgement_kind: Mapped[str] = mapped_column(NulFreeString(24))
    safety_effect: Mapped[str] = mapped_column(NulFreeString(8))
    actor_kind: Mapped[str] = mapped_column(NulFreeString(16))
    actor_ref: Mapped[str] = mapped_column(BoundedReviewText(128))
    reason_code: Mapped[str] = mapped_column(NulFreeString(32))
    evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(AcknowledgementEvidencePayload())
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    acknowledged_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    @classmethod
    def from_evidence(cls, **values: Any) -> Self:
        """Own operator-review inputs and derive canonical no-effect evidence."""
        if set(EVIDENCE_FIELDS) & values.keys():
            raise PersistenceValueError()
        allowed = (set(cls.__table__.c.keys()) - set(EVIDENCE_FIELDS)) | set(
            ACKNOWLEDGEMENT_PAYLOAD_ONLY
        )
        if values.keys() - allowed:
            raise PersistenceValueError()
        owned = dict(values)
        try:
            payload_values = {
                "actor_ref": owned["actor_ref"],
                "idempotency_key": owned["idempotency_key"],
                "reason_code": owned["reason_code"],
                "supporting_evidence_sha256": owned.pop("supporting_evidence_sha256"),
            }
            payload, digest = encode_acknowledgement_evidence(
                1,
                owned["drift_id"],
                owned["acknowledgement_kind"],
                owned["acknowledgement_policy"],
                owned["safety_effect"],
                payload_values,
            )
        except Exception:
            raise PersistenceValueError() from None
        return cls(
            evidence_schema_version=1,
            evidence_payload=payload,
            evidence_sha256=digest,
            **owned,
        )


def _load_values(
    target: ReconciliationDrift | ReconciliationAcknowledgement,
    fields: tuple[str, ...],
    connection: Connection | None,
) -> dict[str, object]:
    state = cast(InstanceState[Any], sa_inspect(target))
    values = {field: state.dict[field] for field in fields if field in state.dict}
    missing = [field for field in fields if field not in values]
    if missing:
        session = object_session(target)
        if state.identity is None or (connection is None and session is None):
            raise PersistenceValueError()
        if connection is None:
            assert session is not None
            connection = session.connection()
        table = target.__table__
        values.update(
            connection.execute(
                select(*(table.c[field] for field in missing)).where(
                    table.c.id == state.identity[0]
                )
            )
            .mappings()
            .one()
        )
    return values


def _validate_drift(target: ReconciliationDrift, connection: Connection | None = None) -> None:
    fields = (*EVIDENCE_FIELDS, *DRIFT_SEMANTIC_COLUMNS)
    values = _load_values(target, fields, connection)
    payload = decode_drift_evidence(values["evidence_payload"])
    if any(
        payload[field] != values[field]
        for field in ("subject_key_sha256", "product_id", "asset_id")
    ):
        raise PersistenceValueError()
    if encode_drift_evidence(
        values["evidence_schema_version"],
        values["drift_kind"],
        values["subject_kind"],
        values["severity"],
        values["acknowledgement_policy"],
        payload,
    ) != (values["evidence_payload"], values["evidence_sha256"]):
        raise PersistenceValueError()


def _validate_acknowledgement(
    target: ReconciliationAcknowledgement, connection: Connection | None = None
) -> None:
    fields = (*EVIDENCE_FIELDS, *ACKNOWLEDGEMENT_SEMANTIC_COLUMNS)
    values = _load_values(target, fields, connection)
    payload = decode_acknowledgement_evidence(values["evidence_payload"])
    if any(
        payload[field] != values[field] for field in ("actor_ref", "idempotency_key", "reason_code")
    ):
        raise PersistenceValueError()
    if encode_acknowledgement_evidence(
        values["evidence_schema_version"],
        values["drift_id"],
        values["acknowledgement_kind"],
        values["acknowledgement_policy"],
        values["safety_effect"],
        payload,
    ) != (values["evidence_payload"], values["evidence_sha256"]):
        raise PersistenceValueError()


def _install_validation(
    model: type[ReconciliationDrift] | type[ReconciliationAcknowledgement],
) -> None:
    validate = _validate_drift if model is ReconciliationDrift else _validate_acknowledgement

    def before_write(mapper: object, connection: Connection, target: Any) -> None:
        validate(target, connection)

    def on_load(target: Any, context: object) -> None:
        validate(target)

    def on_refresh(target: Any, context: object, attrs: Iterable[str] | None) -> None:
        validate(target)

    event.listen(model, "before_insert", before_write)
    event.listen(model, "before_update", before_write)
    event.listen(model, "load", on_load, restore_load_context=True)
    event.listen(model, "refresh", on_refresh, restore_load_context=True)
    guard_model(model, immutable=True)


_install_validation(ReconciliationDrift)
_install_validation(ReconciliationAcknowledgement)
