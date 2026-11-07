"""Immutable versioned PnL calculation claims; never readiness or authority.

The typed payload validates standalone canonical shape on Core paths. ORM loads
also validate complete sibling agreement. Raw-SQL append-only and cross-column
enforcement remain deferred to reviewed PostgreSQL grants and triggers.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any, Self, cast
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, Uuid, event, select
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
from hmt_crypto.persistence.pnl_evidence import (
    COMPLETENESS,
    LOSS_FIELDS,
    MEASURE_FIELDS,
    PNL_FIELDS,
    PnlEvidencePayload,
    encode_pnl_evidence,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime, exact_decimal_checks

EVIDENCE_FIELDS = ("evidence_schema_version", "evidence_payload", "evidence_sha256")
SEMANTIC_COLUMN = {
    field: "run_id" if field == "reconciliation_run_id" else field for field in PNL_FIELDS
}


class BoundedPnlText(NulFreeString):
    """Require exact bounded nonblank text on bind and result paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        result = super().process_bind_param(value, dialect)
        if result is None or not 1 <= len(result) <= 128 or not result.strip():
            raise PersistenceValueError()
        return result

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)


class PnlSnapshot(ScopedRow, Base):
    """An append-only local accounting calculation tied to historical inputs."""

    __tablename__ = "pnl_snapshots"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        scoped_fk(__tablename__, "run_id", "reconciliation_runs"),
        UniqueConstraint(*SCOPE, "snapshot_key", name="uq_pnl_snapshots_scope_key"),
        UniqueConstraint(
            "id",
            *SCOPE,
            "completeness",
            "calculation_version",
            "reference_asset_id",
            "reference_asset_symbol",
            "evidence_sha256",
            "input_set_sha256",
            name="uq_pnl_snapshots_risk_decision_parent",
        ),
        Index("ix_pnl_snapshots_run_parent", "run_id", *SCOPE),
        Index(
            "ix_pnl_snapshots_scope_window",
            *SCOPE,
            "window_start_at",
            "window_end_at",
        ),
        bounded("snapshot_key"),
        bounded("reference_asset_symbol"),
        checked("completeness", COMPLETENESS),
        CheckConstraint("reconciliation_version >= 1", name="reconciliation_version_positive"),
        CheckConstraint("reference_asset_id >= 1", name="reference_asset_positive"),
        CheckConstraint("calculation_version >= 1", name="calculation_version_positive"),
        sha_check("calculation_policy_sha256"),
        sha_check("input_set_sha256"),
        *(
            constraint
            for field in MEASURE_FIELDS
            for constraint in exact_decimal_checks(field, ExactDecimal(38, 18))
        ),
        *(
            CheckConstraint(f"{field} IS NULL OR {field} >= 0", name=f"{field}_nonnegative")
            for field in LOSS_FIELDS
        ),
        CheckConstraint(
            "(completeness = 'UNKNOWN' AND "
            + " AND ".join(f"{field} IS NULL" for field in MEASURE_FIELDS)
            + ") OR (completeness = 'PARTIAL' AND ("
            + " OR ".join(f"{field} IS NOT NULL" for field in MEASURE_FIELDS[:-1])
            + ") AND conservative_daily_loss IS NULL) OR "
            "(completeness = 'COMPLETE' AND "
            + " AND ".join(f"{field} IS NOT NULL" for field in MEASURE_FIELDS)
            + ")",
            name="completeness_shape",
        ),
        CheckConstraint(
            "window_start_at <= window_end_at "
            "AND window_end_at <= calculated_at "
            "AND calculated_at <= recorded_at",
            name="time_order",
        ),
    )

    snapshot_key: Mapped[str] = mapped_column(BoundedPnlText(128))
    run_id: Mapped[UUID] = mapped_column(Uuid)
    reconciliation_version: Mapped[int] = mapped_column(StrictBigInteger())
    window_start_at: Mapped[datetime] = mapped_column(UTCDateTime())
    window_end_at: Mapped[datetime] = mapped_column(UTCDateTime())
    calculated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    reference_asset_id: Mapped[int] = mapped_column(StrictBigInteger())
    reference_asset_symbol: Mapped[str] = mapped_column(BoundedPnlText(128))
    completeness: Mapped[str] = mapped_column(NulFreeString(16))
    baseline_equity: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    observed_equity: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    reconciled_realized_pnl: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    mark_to_market_unrealized_pnl: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    equity_drawdown_loss: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    unresolved_loss_reserve: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    conservative_daily_loss: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    calculation_version: Mapped[int] = mapped_column(StrictBigInteger())
    calculation_policy_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    input_set_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(PnlEvidencePayload())
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))

    @classmethod
    def from_evidence(cls, **values: Any) -> Self:
        """Own normalized calculation fields and derive canonical evidence."""
        if set(EVIDENCE_FIELDS) & values.keys():
            raise PersistenceValueError()
        allowed = set(cls.__table__.c.keys()) - set(EVIDENCE_FIELDS)
        if values.keys() - allowed:
            raise PersistenceValueError()
        owned = dict(values)
        try:
            semantic = {
                evidence_field: owned[column] for evidence_field, column in SEMANTIC_COLUMN.items()
            }
            payload, digest = encode_pnl_evidence(1, semantic)
        except Exception:
            raise PersistenceValueError() from None
        return cls(
            evidence_schema_version=1,
            evidence_payload=payload,
            evidence_sha256=digest,
            **owned,
        )


def _validate_tuple(target: PnlSnapshot, connection: Connection | None = None) -> None:
    columns = (*EVIDENCE_FIELDS, *SEMANTIC_COLUMN.values())
    state = cast(InstanceState[Any], sa_inspect(target))
    values = {field: state.dict[field] for field in columns if field in state.dict}
    missing = [field for field in columns if field not in values]
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
    semantic = {
        evidence_field: values[column] for evidence_field, column in SEMANTIC_COLUMN.items()
    }
    if encode_pnl_evidence(values["evidence_schema_version"], semantic) != (
        values["evidence_payload"],
        values["evidence_sha256"],
    ):
        raise PersistenceValueError()


@event.listens_for(PnlSnapshot, "before_insert")
@event.listens_for(PnlSnapshot, "before_update")
def _before_write(mapper: object, connection: Connection, target: PnlSnapshot) -> None:
    _validate_tuple(target, connection)


@event.listens_for(PnlSnapshot, "load", restore_load_context=True)
def _load(target: PnlSnapshot, context: object) -> None:
    _validate_tuple(target)


@event.listens_for(PnlSnapshot, "refresh", restore_load_context=True)
def _refresh(target: PnlSnapshot, context: object, attrs: Iterable[str] | None) -> None:
    _validate_tuple(target)


guard_model(PnlSnapshot, immutable=True)
