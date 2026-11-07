"""One immutable query/configuration per collection attempt, with mutable progress.

Completion is a stored shape, never coverage, readiness or execution authority.
No traversal, HTTP, parser, observation or parent-run mutation exists here.
Core/raw SQL bypass ORM history/freeze and cross-column query hash validation;
real PostgreSQL enforcement and allocation/consecutive attempts are deferred.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Self
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, Uuid, event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column, object_session

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    guard_model,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.reconciliation_query import (
    RESOURCE_KINDS,
    QueryPayload,
    encode_query,
    validate_query,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import UTCDateTime

TERMINAL = ("COLLECTED", "TRUNCATED", "FAILED", "CANCELLED")
QUERY_FIELDS = (
    "resource_kind",
    "query_schema_version",
    "query_payload",
    "query_sha256",
    "pagination_kind",
)


class ReconciliationResource(ScopedRow, Base):
    """Admitted response evidence; reported totals and budgets prove no coverage."""

    __tablename__ = "reconciliation_resources"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(
            *SCOPE,
            "run_id",
            "resource_kind",
            "query_sha256",
            "collection_attempt",
            name="uq_reconciliation_resources_attempt",
        ),
        UniqueConstraint(
            "id",
            *SCOPE,
            "run_id",
            "resource_kind",
            name="uq_reconciliation_resources_observation_parent",
        ),
        scoped_fk(__tablename__, "run_id", "reconciliation_runs"),
        Index("ix_reconciliation_resources_run_scope", "run_id", *SCOPE),
        checked("resource_kind", RESOURCE_KINDS),
        checked("pagination_kind", ("NONE", "CURSOR", "UNKNOWN")),
        checked("state", ("PENDING", "COLLECTING", *TERMINAL)),
        checked(
            "completion_basis",
            ("UNKNOWN", "SINGLE_RESPONSE", "FORWARD_EXHAUSTED", "BACKWARD_EXHAUSTED"),
        ),
        checked(
            "failure_code",
            (
                "REQUEST_FAILED",
                "INVALID_RESPONSE",
                "PAGINATION_INCOMPLETE",
                "LIMIT_REACHED",
                "CANCELLED",
                "UNKNOWN",
            ),
        ),
        CheckConstraint("query_schema_version = 1", name="query_schema_version"),
        bounded("query_payload", 2048),
        sha_check("query_sha256"),
        *(
            CheckConstraint(f"{field} >= 1", name=f"{field}_positive")
            for field in ("collection_attempt", "version", "max_pages", "max_items")
        ),
        *(
            CheckConstraint(f"{field} >= 0", name=f"{field}_nonnegative")
            for field in ("pages_received", "items_received", "reported_total")
        ),
        *(
            constraint
            for side in ("after", "before")
            for constraint in (
                checked(f"{side}_cursor_state", ("UNKNOWN", "ABSENT", "PRESENT", "NOT_APPLICABLE")),
                sha_check(f"{side}_cursor_sha256"),
                CheckConstraint(
                    f"({side}_cursor_state = 'PRESENT' AND {side}_cursor_sha256 IS NOT NULL) OR "
                    f"({side}_cursor_state <> 'PRESENT' AND {side}_cursor_sha256 IS NULL)",
                    name=f"{side}_cursor_pair",
                ),
            )
        ),
        CheckConstraint(
            "(resource_kind IN ('BALANCES','LATEST_POSITION','MARGINED_POSITIONS') "
            "AND pagination_kind = 'NONE') OR "
            "(resource_kind IN ('WALLET_TRANSACTIONS','OPEN_ORDERS','ORDER_HISTORY','FILLS') "
            "AND pagination_kind IN ('CURSOR','UNKNOWN'))",
            name="resource_pagination",
        ),
        CheckConstraint(
            "(pagination_kind = 'NONE' AND max_pages IS NULL AND max_items IS NULL "
            "AND after_cursor_state = 'NOT_APPLICABLE' "
            "AND before_cursor_state = 'NOT_APPLICABLE' AND pages_received <= 1) OR "
            "(pagination_kind = 'CURSOR' AND after_cursor_state IN ('UNKNOWN','ABSENT','PRESENT') "
            "AND before_cursor_state IN ('UNKNOWN','ABSENT','PRESENT')) OR "
            "(pagination_kind = 'UNKNOWN' AND max_pages IS NULL AND max_items IS NULL "
            "AND after_cursor_state = 'UNKNOWN' AND before_cursor_state = 'UNKNOWN' "
            "AND state NOT IN ('COLLECTED','TRUNCATED'))",
            name="pagination_shape",
        ),
        CheckConstraint(
            "(pages_received = 0 AND items_received = 0 AND last_received_at IS NULL) OR "
            "(pages_received > 0 AND last_received_at IS NOT NULL)",
            name="received_shape",
        ),
        CheckConstraint(
            "last_received_at IS NULL OR (first_requested_at IS NOT NULL "
            "AND last_received_at >= first_requested_at)",
            name="receive_order",
        ),
        CheckConstraint(
            "(completion_basis = 'UNKNOWN') OR "
            "(completion_basis = 'SINGLE_RESPONSE' AND pagination_kind = 'NONE' "
            "AND pages_received = 1) OR "
            "(completion_basis = 'FORWARD_EXHAUSTED' AND pagination_kind = 'CURSOR' "
            "AND after_cursor_state = 'ABSENT') OR "
            "(completion_basis = 'BACKWARD_EXHAUSTED' AND pagination_kind = 'CURSOR' "
            "AND before_cursor_state = 'ABSENT')",
            name="completion_shape",
        ),
        CheckConstraint(
            "(state = 'PENDING' AND version = 1 AND pages_received = 0 AND items_received = 0 "
            "AND reported_total IS NULL AND first_requested_at IS NULL "
            "AND last_received_at IS NULL "
            "AND finished_at IS NULL AND failure_code IS NULL AND completion_basis = 'UNKNOWN' "
            "AND ((pagination_kind = 'NONE' AND after_cursor_state = 'NOT_APPLICABLE' "
            "AND before_cursor_state = 'NOT_APPLICABLE') "
            "OR (pagination_kind <> 'NONE' AND after_cursor_state = 'UNKNOWN' "
            "AND before_cursor_state = 'UNKNOWN'))) OR "
            "(state = 'COLLECTING' AND first_requested_at IS NOT NULL AND finished_at IS NULL "
            "AND failure_code IS NULL AND completion_basis = 'UNKNOWN') OR "
            "(state = 'COLLECTED' AND pages_received >= 1 AND first_requested_at IS NOT NULL "
            "AND last_received_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND failure_code IS NULL AND completion_basis <> 'UNKNOWN') OR "
            "(state = 'TRUNCATED' AND pagination_kind = 'CURSOR' "
            "AND first_requested_at IS NOT NULL "
            "AND pages_received >= 1 AND finished_at IS NOT NULL AND completion_basis = 'UNKNOWN' "
            "AND failure_code IS NOT NULL "
            "AND failure_code IN ('LIMIT_REACHED','PAGINATION_INCOMPLETE')) OR "
            "(state = 'FAILED' AND finished_at IS NOT NULL AND completion_basis = 'UNKNOWN' "
            "AND failure_code IS NOT NULL AND failure_code IN "
            "('REQUEST_FAILED','INVALID_RESPONSE','PAGINATION_INCOMPLETE','UNKNOWN')) OR "
            "(state = 'CANCELLED' AND finished_at IS NOT NULL AND completion_basis = 'UNKNOWN' "
            "AND failure_code IS NOT NULL AND failure_code = 'CANCELLED')",
            name="outcome_shape",
        ),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        *(
            CheckConstraint(
                f"{field} IS NULL OR (created_at <= {field} AND {field} <= updated_at)",
                name=f"{field}_bounds",
            )
            for field in ("first_requested_at", "last_received_at", "finished_at")
        ),
        *(
            CheckConstraint(
                f"finished_at IS NULL OR {field} IS NULL OR finished_at >= {field}",
                name=f"finish_after_{field}",
            )
            for field in ("first_requested_at", "last_received_at")
        ),
    )

    run_id: Mapped[UUID] = mapped_column(Uuid)
    resource_kind: Mapped[str] = mapped_column(NulFreeString(32))
    collection_attempt: Mapped[int] = mapped_column(StrictBigInteger())
    query_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    query_payload: Mapped[str] = mapped_column(QueryPayload())
    query_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    pagination_kind: Mapped[str] = mapped_column(NulFreeString(16))
    max_pages: Mapped[int | None] = mapped_column(StrictBigInteger())
    max_items: Mapped[int | None] = mapped_column(StrictBigInteger())
    state: Mapped[str] = mapped_column(NulFreeString(16))
    pages_received: Mapped[int] = mapped_column(StrictBigInteger())
    items_received: Mapped[int] = mapped_column(StrictBigInteger())
    reported_total: Mapped[int | None] = mapped_column(StrictBigInteger())
    after_cursor_state: Mapped[str] = mapped_column(NulFreeString(16))
    after_cursor_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    before_cursor_state: Mapped[str] = mapped_column(NulFreeString(16))
    before_cursor_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    completion_basis: Mapped[str] = mapped_column(NulFreeString(32))
    first_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_received_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failure_code: Mapped[str | None] = mapped_column(NulFreeString(32))
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": version}

    @classmethod
    def from_query(cls, *, query: object, **values: Any) -> Self:
        """Own canonical query bytes, rejecting caller payload/digest overrides."""
        if {"query_payload", "query_sha256"} & values.keys():
            raise PersistenceValueError()
        version = values.pop("query_schema_version", 1)
        payload, digest = encode_query(values.get("resource_kind"), version, query)
        return cls(
            query_schema_version=version, query_payload=payload, query_sha256=digest, **values
        )


guard_model(
    ReconciliationResource,
    protected=(
        "run_id",
        "resource_kind",
        "collection_attempt",
        "query_schema_version",
        "query_payload",
        "query_sha256",
        "pagination_kind",
        "max_pages",
        "max_items",
        "created_at",
        "version",
    ),
)


def _validate_tuple(target: ReconciliationResource, connection: Connection | None = None) -> None:
    # Fetch only missing typed columns without invoking instrumented attributes,
    # autoflush, recursive load/refresh or changing pending local values.
    state = sa_inspect(target)
    values = {field: state.dict[field] for field in QUERY_FIELDS if field in state.dict}
    missing = [field for field in QUERY_FIELDS if field not in values]
    if missing:
        session = object_session(target)
        if state.identity is None or (connection is None and session is None):
            raise PersistenceValueError()
        if connection is None:
            assert session is not None
            connection = session.connection()
        table = ReconciliationResource.__table__
        values.update(
            connection.execute(
                select(*(table.c[f] for f in missing)).where(table.c.id == state.identity[0])
            )
            .mappings()
            .one()
        )
    query = validate_query(
        values["resource_kind"],
        values["query_schema_version"],
        values["query_payload"],
        values["query_sha256"],
    )
    if (
        values["pagination_kind"] in {"NONE", "UNKNOWN"}
        and query.initial_cursor_direction != "NONE"
    ):
        raise PersistenceValueError()
    if values["pagination_kind"] == "UNKNOWN" and "page_size" in query.model_fields_set:
        raise PersistenceValueError()


@event.listens_for(ReconciliationResource, "before_insert")
@event.listens_for(ReconciliationResource, "before_update")
def _before_write(mapper: object, connection: Connection, target: ReconciliationResource) -> None:
    if type(target.version) is not int or not 1 <= target.version < 2**63:
        raise PersistenceValueError()
    _validate_tuple(target, connection)


@event.listens_for(ReconciliationResource, "before_insert")
def _pending_version(
    mapper: object, connection: Connection, target: ReconciliationResource
) -> None:
    if target.state == "PENDING" and target.version != 1:
        raise PersistenceValueError()


@event.listens_for(ReconciliationResource, "before_update")
def _freeze_terminal(
    mapper: object, connection: Connection, target: ReconciliationResource
) -> None:
    prior = connection.scalar(
        select(ReconciliationResource.state).where(ReconciliationResource.id == target.id)
    )
    if prior in TERMINAL:
        raise PersistenceValueError()


@event.listens_for(ReconciliationResource, "before_delete")
def _reject_delete(mapper: object, connection: Connection, target: ReconciliationResource) -> None:
    raise PersistenceValueError()


@event.listens_for(ReconciliationResource, "load", restore_load_context=True)
def _load(target: ReconciliationResource, context: object) -> None:
    _validate_tuple(target)


@event.listens_for(ReconciliationResource, "refresh", restore_load_context=True)
def _refresh(target: ReconciliationResource, context: object, attrs: Iterable[str] | None) -> None:
    _validate_tuple(target)
