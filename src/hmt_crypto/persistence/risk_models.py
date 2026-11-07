"""Scoped risk claims and immutable independent halt-cycle evidence.

These rows record claims and halt history only. They cannot authorize dispatch,
clear another cause, calculate risk, or enable production writes. Core/raw SQL
history, sequencing, version and append-only enforcement remains deferred to
reviewed PostgreSQL triggers and grants.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
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
    text,
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
from hmt_crypto.persistence.risk_evidence import (
    HALT_ACTORS,
    HALT_CAUSES,
    HALT_EVENT_KINDS,
    HALT_REASONS,
    HALT_STATES,
    HaltEventEvidencePayload,
    decode_halt_event_evidence,
    encode_halt_event_evidence,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime, exact_decimal_checks

RECONCILIATION_STATES = ("UNKNOWN", "BLOCKED", "READY_CLAIM")
ACCOUNTING_STATES = ("UNKNOWN", "PARTIAL", "COMPLETE_CLAIM")
HALT_SET_STATES = ("UNINITIALIZED", "INITIALIZED")
EVIDENCE_FIELDS = ("evidence_schema_version", "evidence_payload", "evidence_sha256")
EVENT_ENVELOPE_FIELDS = (
    "halt_cause",
    "generation",
    "sequence",
    "event_kind",
    "from_state",
    "to_state",
)
EVENT_PAYLOAD_FIELDS = (
    "actor_kind",
    "reason_code",
    "reconciliation_run_id",
    "pnl_snapshot_id",
    "drift_id",
    "supporting_evidence_sha256",
)
RECONCILIATION_FIELDS = (
    "reconciliation_run_id",
    "reconciliation_run_version",
    "reconciliation_observed_at",
)
ACCOUNTING_FIELDS = (
    "pnl_snapshot_id",
    "accounting_calculation_version",
    "accounting_input_set_sha256",
    "accounting_window_start_at",
    "accounting_window_end_at",
    "reference_asset_id",
    "reference_asset_symbol",
    "baseline_equity",
    "conservative_daily_loss",
)
RISK_STATE_FIELDS = (
    "halt_set_state",
    "halt_revision",
    "reconciliation_state",
    *RECONCILIATION_FIELDS,
    "accounting_state",
    *ACCOUNTING_FIELDS,
    "version",
    "created_at",
    "updated_at",
)


class OptionalBoundedRiskText(NulFreeString):
    """Admit NULL or exact bounded nonblank text on bind and result paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        if value is None:
            return None
        result = super().process_bind_param(value, dialect)
        if result is None or not 1 <= len(result) <= 128 or not result.strip():
            raise PersistenceValueError()
        return result

    def process_result_value(self, value: object, dialect: Dialect) -> str | None:
        return self.process_bind_param(value, dialect)


class BoundedRiskText(NulFreeString):
    """Admit exact bounded nonblank text on bind and result paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        result = super().process_bind_param(value, dialect)
        if result is None or not 1 <= len(result) <= 128 or not result.strip():
            raise PersistenceValueError()
        return result

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)


class RiskState(ScopedRow, Base):
    """One mutable account-wide risk lock/invalidation projection per scope."""

    __tablename__ = "risk_state"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(*SCOPE, name="uq_risk_state_scope"),
        scoped_fk(__tablename__, "reconciliation_run_id", "reconciliation_runs"),
        scoped_fk(__tablename__, "pnl_snapshot_id", "pnl_snapshots"),
        Index("ix_risk_state_reconciliation_parent", "reconciliation_run_id", *SCOPE),
        Index("ix_risk_state_pnl_parent", "pnl_snapshot_id", *SCOPE),
        checked("halt_set_state", HALT_SET_STATES),
        checked("reconciliation_state", RECONCILIATION_STATES),
        checked("accounting_state", ACCOUNTING_STATES),
        CheckConstraint("halt_revision >= 0", name="halt_revision_nonnegative"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(reconciliation_state = 'UNKNOWN' AND reconciliation_run_id IS NULL "
            "AND reconciliation_run_version IS NULL AND reconciliation_observed_at IS NULL) OR "
            "(reconciliation_state IN ('BLOCKED','READY_CLAIM') "
            "AND reconciliation_run_id IS NOT NULL "
            "AND reconciliation_run_version IS NOT NULL AND reconciliation_run_version >= 1 "
            "AND reconciliation_observed_at IS NOT NULL)",
            name="reconciliation_shape",
        ),
        CheckConstraint(
            "(accounting_state = 'UNKNOWN' AND "
            + " AND ".join(f"{field} IS NULL" for field in ACCOUNTING_FIELDS)
            + ") OR (accounting_state = 'PARTIAL' AND "
            + " AND ".join(
                f"{field} IS NOT NULL"
                for field in ACCOUNTING_FIELDS
                if field not in {"baseline_equity", "conservative_daily_loss"}
            )
            + " AND conservative_daily_loss IS NULL) OR "
            "(accounting_state = 'COMPLETE_CLAIM' AND "
            + " AND ".join(f"{field} IS NOT NULL" for field in ACCOUNTING_FIELDS)
            + ")",
            name="accounting_shape",
        ),
        CheckConstraint(
            "halt_set_state <> 'UNINITIALIZED' OR "
            "(halt_revision = 0 AND version = 1 AND reconciliation_state = 'UNKNOWN' "
            "AND accounting_state = 'UNKNOWN')",
            name="initial_shape",
        ),
        CheckConstraint(
            "accounting_calculation_version IS NULL OR accounting_calculation_version >= 1",
            name="accounting_calculation_version_positive",
        ),
        CheckConstraint(
            "reference_asset_id IS NULL OR reference_asset_id >= 1",
            name="reference_asset_positive",
        ),
        sha_check("accounting_input_set_sha256"),
        bounded("reference_asset_symbol"),
        *exact_decimal_checks("baseline_equity", ExactDecimal(38, 18)),
        *exact_decimal_checks("conservative_daily_loss", ExactDecimal(38, 18)),
        CheckConstraint(
            "conservative_daily_loss IS NULL OR conservative_daily_loss >= 0",
            name="conservative_daily_loss_nonnegative",
        ),
        CheckConstraint(
            "accounting_window_start_at IS NULL OR "
            "accounting_window_start_at <= accounting_window_end_at",
            name="accounting_window_order",
        ),
        CheckConstraint("created_at <= updated_at", name="time_order"),
    )

    halt_set_state: Mapped[str] = mapped_column(NulFreeString(16))
    halt_revision: Mapped[int] = mapped_column(StrictBigInteger())
    reconciliation_state: Mapped[str] = mapped_column(NulFreeString(16))
    reconciliation_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    reconciliation_run_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    reconciliation_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    accounting_state: Mapped[str] = mapped_column(NulFreeString(16))
    pnl_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    accounting_calculation_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    accounting_input_set_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    accounting_window_start_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    accounting_window_end_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reference_asset_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    reference_asset_symbol: Mapped[str | None] = mapped_column(OptionalBoundedRiskText(128))
    baseline_equity: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    conservative_daily_loss: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": version}


def _halt_state_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["risk_state_id", *SCOPE],
        [f"hmt_crypto.risk_state.{field}" for field in ("id", *SCOPE)],
        name="fk_risk_halts_risk_state_scope",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class RiskHalt(ScopedRow, Base):
    """An immutable engagement-cycle header for one independent halt cause."""

    __tablename__ = "risk_halts"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        _halt_state_fk(),
        UniqueConstraint(
            *SCOPE,
            "halt_cause",
            "generation",
            name="uq_risk_halts_cause_generation",
        ),
        UniqueConstraint(
            "id",
            *SCOPE,
            "risk_state_id",
            "halt_cause",
            "generation",
            name="uq_risk_halts_event_parent",
        ),
        Index("ix_risk_halts_state_parent", "risk_state_id", *SCOPE),
        checked("halt_cause", HALT_CAUSES),
        CheckConstraint("generation >= 1", name="generation_positive"),
    )

    risk_state_id: Mapped[UUID] = mapped_column(Uuid)
    halt_cause: Mapped[str] = mapped_column(NulFreeString(24))
    generation: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


def _event_halt_fk() -> ForeignKeyConstraint:
    fields = ("id", *SCOPE, "risk_state_id", "halt_cause", "generation")
    return ForeignKeyConstraint(
        ["halt_id", *SCOPE, "risk_state_id", "halt_cause", "generation"],
        [f"hmt_crypto.risk_halts.{field}" for field in fields],
        name="fk_risk_halt_events_halt_scope",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class RiskHaltEvent(ScopedRow, Base):
    """An immutable lifecycle event whose release affects one cause only."""

    __tablename__ = "risk_halt_events"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        _event_halt_fk(),
        scoped_fk(__tablename__, "reconciliation_run_id", "reconciliation_runs"),
        scoped_fk(__tablename__, "pnl_snapshot_id", "pnl_snapshots"),
        scoped_fk(__tablename__, "drift_id", "reconciliation_drifts"),
        UniqueConstraint("halt_id", *SCOPE, "sequence", name="uq_risk_halt_events_cycle_sequence"),
        UniqueConstraint(
            *SCOPE,
            "idempotency_key",
            name="uq_risk_halt_events_scope_idempotency",
        ),
        UniqueConstraint(
            "id",
            *SCOPE,
            "halt_id",
            "halt_cause",
            "event_kind",
            "generation",
            name="uq_risk_halt_events_kill_parent",
        ),
        Index(
            "uq_risk_halt_events_one_release",
            "halt_id",
            *SCOPE,
            unique=True,
            postgresql_where=text("event_kind = 'RELEASED'"),
            sqlite_where=text("event_kind = 'RELEASED'"),
        ),
        Index(
            "ix_risk_halt_events_halt_parent",
            "halt_id",
            *SCOPE,
            "risk_state_id",
            "halt_cause",
            "generation",
        ),
        Index("ix_risk_halt_events_reconciliation_parent", "reconciliation_run_id", *SCOPE),
        Index("ix_risk_halt_events_pnl_parent", "pnl_snapshot_id", *SCOPE),
        Index("ix_risk_halt_events_drift_parent", "drift_id", *SCOPE),
        checked("halt_cause", HALT_CAUSES),
        checked("event_kind", HALT_EVENT_KINDS),
        checked("from_state", HALT_STATES),
        checked("to_state", HALT_STATES),
        checked("actor_kind", HALT_ACTORS),
        checked("reason_code", HALT_REASONS),
        bounded("idempotency_key"),
        sha_check("supporting_evidence_sha256"),
        CheckConstraint("generation >= 1", name="generation_positive"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            "(event_kind = 'ENGAGED' AND sequence = 1 "
            "AND from_state = 'CLEARED' AND to_state = 'ACTIVE') OR "
            "(event_kind = 'REAFFIRMED' AND sequence >= 2 "
            "AND from_state = 'ACTIVE' AND to_state = 'ACTIVE') OR "
            "(event_kind = 'RELEASED' AND sequence >= 2 "
            "AND from_state = 'ACTIVE' AND to_state = 'CLEARED')",
            name="lifecycle_shape",
        ),
        CheckConstraint(
            "halt_cause <> 'GLOBAL_KILL' OR actor_kind = 'OPERATOR'",
            name="global_kill_operator",
        ),
        CheckConstraint(
            "(event_kind IN ('ENGAGED','REAFFIRMED') AND ("
            "(halt_cause = 'GLOBAL_KILL' AND reason_code = 'OPERATOR_KILL') OR "
            "(halt_cause = 'DAILY_LOSS' AND reason_code = 'DAILY_LOSS_LIMIT') OR "
            "(halt_cause = 'RECONCILIATION' AND reason_code IN "
            "('RECONCILIATION_INCOMPLETE','STALE_RECONCILIATION','CRITICAL_DRIFT')) OR "
            "(halt_cause = 'AUTOMATIC_RISK' AND reason_code IN "
            "('ACCOUNTING_UNKNOWN','PROTECTION_INADEQUATE','RISK_POLICY','RECOVERY_REQUIRED'))"
            ")) OR (event_kind = 'RELEASED' AND reason_code IN "
            "('OPERATOR_RELEASE','UTC_RESET','RECONCILIATION_RECOVERED',"
            "'ACCOUNTING_RECOVERED','PROTECTION_RESTORED','RECOVERY_COMPLETED'))",
            name="cause_reason_shape",
        ),
        CheckConstraint("occurred_at <= recorded_at", name="time_order"),
    )

    halt_id: Mapped[UUID] = mapped_column(Uuid)
    risk_state_id: Mapped[UUID] = mapped_column(Uuid)
    halt_cause: Mapped[str] = mapped_column(NulFreeString(24))
    generation: Mapped[int] = mapped_column(StrictBigInteger())
    sequence: Mapped[int] = mapped_column(StrictBigInteger())
    event_kind: Mapped[str] = mapped_column(NulFreeString(16))
    from_state: Mapped[str] = mapped_column(NulFreeString(16))
    to_state: Mapped[str] = mapped_column(NulFreeString(16))
    idempotency_key: Mapped[str] = mapped_column(BoundedRiskText(128))
    actor_kind: Mapped[str] = mapped_column(NulFreeString(16))
    reason_code: Mapped[str] = mapped_column(NulFreeString(32))
    reconciliation_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    pnl_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    drift_id: Mapped[UUID | None] = mapped_column(Uuid)
    supporting_evidence_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(HaltEventEvidencePayload())
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    @classmethod
    def from_evidence(cls, **values: Any) -> Self:
        """Own typed event inputs and derive canonical evidence bytes."""
        if set(EVIDENCE_FIELDS) & values.keys():
            raise PersistenceValueError()
        allowed = set(cls.__table__.c.keys()) - set(EVIDENCE_FIELDS)
        if values.keys() - allowed:
            raise PersistenceValueError()
        owned = dict(values)
        try:
            payload_values = {field: owned[field] for field in EVENT_PAYLOAD_FIELDS}
            payload, digest = encode_halt_event_evidence(
                1,
                owned["halt_cause"],
                owned["generation"],
                owned["sequence"],
                owned["event_kind"],
                owned["from_state"],
                owned["to_state"],
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
    target: RiskState | RiskHalt | RiskHaltEvent,
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


def _positive_bigint(value: object) -> bool:
    return type(value) is int and 1 <= value < 2**63


def _nonnegative_bigint(value: object) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _ordered_datetimes(start: object, end: object) -> bool:
    return type(start) is datetime and type(end) is datetime and start <= end


def _lowercase_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _bounded_nonblank_text(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 128
        and bool(value.strip())
        and "\x00" not in value
    )


def _nonnegative_decimal(value: object) -> bool:
    return type(value) is Decimal and value.is_finite() and value >= 0


def _validate_risk_state(target: RiskState, connection: Connection | None = None) -> None:
    values = _load_values(target, RISK_STATE_FIELDS, connection)
    if (
        values["halt_set_state"] not in HALT_SET_STATES
        or values["reconciliation_state"] not in RECONCILIATION_STATES
        or values["accounting_state"] not in ACCOUNTING_STATES
        or not _ordered_datetimes(values["created_at"], values["updated_at"])
        or not _positive_bigint(values["version"])
        or not _nonnegative_bigint(values["halt_revision"])
    ):
        raise PersistenceValueError()
    reconciliation_present = tuple(values[field] is not None for field in RECONCILIATION_FIELDS)
    if values["reconciliation_state"] == "UNKNOWN":
        if reconciliation_present != (False, False, False):
            raise PersistenceValueError()
    elif reconciliation_present != (True, True, True) or not _positive_bigint(
        values["reconciliation_run_version"]
    ):
        raise PersistenceValueError()
    required_accounting = tuple(
        field
        for field in ACCOUNTING_FIELDS
        if field not in {"baseline_equity", "conservative_daily_loss"}
    )
    if values["accounting_state"] == "UNKNOWN":
        if any(values[field] is not None for field in ACCOUNTING_FIELDS):
            raise PersistenceValueError()
    elif values["accounting_state"] == "PARTIAL":
        if (
            any(values[field] is None for field in required_accounting)
            or values["conservative_daily_loss"] is not None
        ):
            raise PersistenceValueError()
    elif values["accounting_state"] == "COMPLETE_CLAIM":
        if any(values[field] is None for field in ACCOUNTING_FIELDS):
            raise PersistenceValueError()
    else:
        raise PersistenceValueError()
    if values["accounting_state"] != "UNKNOWN":
        accounting_hash = values["accounting_input_set_sha256"]
        symbol = values["reference_asset_symbol"]
        if (
            not _positive_bigint(values["accounting_calculation_version"])
            or not _lowercase_sha256(accounting_hash)
            or not _ordered_datetimes(
                values["accounting_window_start_at"], values["accounting_window_end_at"]
            )
            or not _positive_bigint(values["reference_asset_id"])
            or not _bounded_nonblank_text(symbol)
            or (
                values["conservative_daily_loss"] is not None
                and not _nonnegative_decimal(values["conservative_daily_loss"])
            )
        ):
            raise PersistenceValueError()
    if values["halt_set_state"] == "UNINITIALIZED" and (
        values["halt_revision"] != 0
        or values["version"] != 1
        or values["reconciliation_state"] != "UNKNOWN"
        or values["accounting_state"] != "UNKNOWN"
    ):
        raise PersistenceValueError()


def _validate_risk_halt(target: RiskHalt, connection: Connection | None = None) -> None:
    values = _load_values(target, ("halt_cause", "generation"), connection)
    if values["halt_cause"] not in HALT_CAUSES or not _positive_bigint(values["generation"]):
        raise PersistenceValueError()


def _validate_halt_event(target: RiskHaltEvent, connection: Connection | None = None) -> None:
    fields = (
        *EVIDENCE_FIELDS,
        *EVENT_ENVELOPE_FIELDS,
        *EVENT_PAYLOAD_FIELDS,
        "occurred_at",
        "recorded_at",
    )
    values = _load_values(target, fields, connection)
    payload = decode_halt_event_evidence(values["evidence_payload"])
    if any(getattr(payload, field) != values[field] for field in EVENT_PAYLOAD_FIELDS) or not (
        _ordered_datetimes(values["occurred_at"], values["recorded_at"])
    ):
        raise PersistenceValueError()
    if encode_halt_event_evidence(
        values["evidence_schema_version"],
        values["halt_cause"],
        values["generation"],
        values["sequence"],
        values["event_kind"],
        values["from_state"],
        values["to_state"],
        payload,
    ) != (values["evidence_payload"], values["evidence_sha256"]):
        raise PersistenceValueError()


def _install_validation(model: type[RiskState] | type[RiskHalt] | type[RiskHaltEvent]) -> None:
    def validate(target: Any, connection: Connection | None = None) -> None:
        if model is RiskState:
            _validate_risk_state(cast(RiskState, target), connection)
        elif model is RiskHalt:
            _validate_risk_halt(cast(RiskHalt, target), connection)
        else:
            _validate_halt_event(cast(RiskHaltEvent, target), connection)

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


guard_model(RiskState, protected=("created_at", "version"))
guard_model(RiskHalt, immutable=True)
guard_model(RiskHaltEvent, immutable=True)
_install_validation(RiskState)
_install_validation(RiskHalt)
_install_validation(RiskHaltEvent)
