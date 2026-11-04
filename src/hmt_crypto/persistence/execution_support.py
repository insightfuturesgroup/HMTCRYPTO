"""Portable execution columns and ORM evidence guards; no dispatch or repositories.

Core/raw SQL bypass ORM immutability and payload validation. Database grants,
triggers, consecutive sequencing, fencing and concurrency belong to later units.
"""

from datetime import datetime
from typing import Any, Self
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid, event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import encode_evidence, validate_stored_evidence
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    NulFreeText,
    StrictBigInteger,
    account_fk,
    bounded,
    mode_check,
    sha_check,
    venue_checks,
)
from hmt_crypto.persistence.types import UTCDateTime

STATES = (
    "UNKNOWN",
    "PREPARED",
    "SUBMITTING",
    "OPEN",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELLED",
    "REJECTED",
    "EXPIRED",
)


def checked(column: str, values: tuple[str, ...]) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IN ({','.join(repr(v) for v in values)})", name=f"{column}_enum"
    )


def scoped_fk(table: str, field: str, parent: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        [field, *SCOPE],
        [f"hmt_crypto.{parent}.{c}" for c in ("id", *SCOPE)],
        name=f"fk_{table}_{field}_scope",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


def scoped_constraints(table: str) -> tuple[Any, ...]:
    return (
        *venue_checks(),
        mode_check(),
        account_fk(table),
        UniqueConstraint("id", *SCOPE, name=f"uq_{table}_id_scope"),
        Index(f"ix_{table}_scope", *SCOPE),
    )


def pair_check(ref: str, generation: str) -> CheckConstraint:
    return CheckConstraint(
        f"({ref} IS NULL AND {generation} IS NULL) OR "
        f"({ref} IS NOT NULL AND {generation} IS NOT NULL AND {generation} >= 1)",
        name=f"{ref}_generation",
    )


def evidence_constraints() -> tuple[Any, ...]:
    return (
        CheckConstraint("evidence_schema_version = 1", name="evidence_schema_version"),
        bounded("evidence_payload", 2048),
        sha_check("evidence_sha256"),
    )


class ScopedRow:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid)
    region: Mapped[str] = mapped_column(NulFreeString(16))
    environment: Mapped[str] = mapped_column(NulFreeString(16))
    mode: Mapped[str] = mapped_column(NulFreeString(16))


class EvidenceRow:
    evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(NulFreeText)
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))

    @classmethod
    def from_evidence(cls, *, evidence: object, **values: Any) -> Self:
        """Construct owned canonical evidence, rejecting supplied hash overrides."""
        kind, operation_field = cls._evidence_config()  # type: ignore[attr-defined]
        version = values.pop("evidence_schema_version", 1)
        payload, digest = encode_evidence(kind, values[operation_field], version, evidence)
        if {"evidence_payload", "evidence_sha256"} & values.keys():
            raise PersistenceValueError()
        return cls(
            evidence_schema_version=version,
            evidence_payload=payload,
            evidence_sha256=digest,
            **values,
        )  # type: ignore[call-arg]


class RecordedRow:
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())


def guard_model(
    model: type[Any],
    *,
    evidence_kind: str | None = None,
    operation_field: str = "operation_kind",
    immutable: bool = False,
    protected: tuple[str, ...] = (),
) -> None:
    """Guard ORM evidence updates/deletes; no claim about raw SQL enforcement."""
    if evidence_kind is not None:

        def evidence_config() -> tuple[str, str]:
            return evidence_kind, operation_field

        model._evidence_config = staticmethod(evidence_config)

        def validate(mapper: object, connection: object, target: Any) -> None:
            validate_stored_evidence(
                evidence_kind,
                getattr(target, operation_field),
                target.evidence_schema_version,
                target.evidence_payload,
                target.evidence_sha256,
            )

        event.listen(model, "before_insert", validate)

    def reject_update(mapper: object, connection: object, target: Any) -> None:
        state = sa_inspect(target)
        columns = ("id", *SCOPE, *protected)
        if immutable or any(state.attrs[c].history.has_changes() for c in columns):
            raise PersistenceValueError()

    event.listen(model, "before_update", reject_update)
    if immutable:

        def reject_delete(mapper: object, connection: object, target: Any) -> None:
            raise PersistenceValueError()

        event.listen(model, "before_delete", reject_delete)
