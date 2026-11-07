"""Crash-recoverable reconciliation barrier rows for complete venue bundles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    event,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    guard_model,
    scoped_constraints,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    NulFreeText,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.types import UTCDateTime


class AccountReconciliationCheckpoint(ScopedRow, Base):
    """One idempotent request whose terminal payload is one complete venue read bundle."""

    __tablename__ = "account_reconciliation_checkpoints"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint(
            "id",
            "account_id",
            "region",
            "environment",
            "mode",
            "request_sha256",
            name="uq_account_reconciliation_checkpoint_request",
        ),
        ForeignKeyConstraint(
            [*SCOPE, "credential_version"],
            [f"hmt_crypto.credential_versions.{field}" for field in (*SCOPE, "credential_version")],
            name="fk_account_reconciliation_checkpoints_credential_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        checked("trigger", ("STARTUP", "PERIODIC", "MANUAL", "RECOVERY", "RECONNECT")),
        checked("state", ("PENDING", "FINISHED", "FAILED")),
        checked("readiness", ("BLOCKED", "READY")),
        checked(
            "failure_code",
            (
                "REQUEST_FAILED",
                "INVALID_RESPONSE",
                "PAGINATION_INCOMPLETE",
                "LIMIT_REACHED",
                "LOCAL_STATE_CHANGED",
                "UNKNOWN",
            ),
        ),
        CheckConstraint("credential_version >= 1", name="credential_version_positive"),
        CheckConstraint("version >= 1 AND drift_count >= 0", name="positive_counters"),
        sha_check("external_user_sha256"),
        sha_check("request_sha256"),
        sha_check("evidence_sha256"),
        bounded("evidence_payload", 16_777_216),
        CheckConstraint(
            "(state = 'PENDING' AND readiness = 'BLOCKED' AND evidence_payload IS NULL "
            "AND evidence_sha256 IS NULL AND failure_code IS NULL AND finished_at IS NULL "
            "AND drift_count = 0 AND external_exposure = false AND version = 1) OR "
            "(state = 'FINISHED' AND evidence_payload IS NOT NULL "
            "AND evidence_sha256 IS NOT NULL AND failure_code IS NULL "
            "AND finished_at IS NOT NULL AND version >= 2) OR "
            "(state = 'FAILED' AND readiness = 'BLOCKED' AND evidence_payload IS NULL "
            "AND evidence_sha256 IS NULL AND failure_code IS NOT NULL "
            "AND finished_at IS NOT NULL AND version >= 2)",
            name="outcome_shape",
        ),
        CheckConstraint(
            "readiness <> 'READY' OR (state = 'FINISHED' AND drift_count = 0 "
            "AND external_exposure = false)",
            name="ready_requires_clean_truth",
        ),
        CheckConstraint(
            "created_at <= updated_at AND (finished_at IS NULL OR "
            "(created_at <= finished_at AND finished_at <= updated_at))",
            name="time_order",
        ),
        Index(
            "ix_account_reconciliation_checkpoint_latest",
            "account_id",
            "region",
            "environment",
            "mode",
            "finished_at",
        ),
    )

    trigger: Mapped[str] = mapped_column(NulFreeString(16))
    credential_version: Mapped[int] = mapped_column(StrictBigInteger())
    external_user_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    request_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    state: Mapped[str] = mapped_column(NulFreeString(16))
    readiness: Mapped[str] = mapped_column(NulFreeString(16))
    evidence_payload: Mapped[str | None] = mapped_column(NulFreeText)
    evidence_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    drift_count: Mapped[int] = mapped_column(StrictBigInteger())
    external_exposure: Mapped[bool] = mapped_column(
        Boolean(create_constraint=True, name="external_exposure_boolean")
    )
    failure_code: Mapped[str | None] = mapped_column(NulFreeString(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(StrictBigInteger())

    __mapper_args__ = {"version_id_col": version}


guard_model(
    AccountReconciliationCheckpoint,
    protected=(
        "trigger",
        "credential_version",
        "external_user_sha256",
        "request_sha256",
        "created_at",
    ),
)


def _validate_terminal_payload(target: AccountReconciliationCheckpoint) -> None:
    if target.state != "FINISHED":
        return
    payload = target.evidence_payload
    digest = target.evidence_sha256
    try:
        if (
            type(payload) is not str
            or not 2 <= len(payload.encode("ascii")) <= 16_777_216
            or type(digest) is not str
        ):
            raise ValueError
        decoded = json.loads(payload)
        if type(decoded) is not dict or set(decoded) != {
            "account_id",
            "balances",
            "credential_version",
            "drifts",
            "fills",
            "latest_positions",
            "local_projection_sha256",
            "observed_at",
            "open_orders",
            "order_history",
            "positions",
            "product_ids",
            "schema_version",
            "uncertain",
        }:
            raise ValueError
        canonical = json.dumps(
            decoded,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        if canonical != payload or hashlib.sha256(payload.encode("ascii")).hexdigest() != digest:
            raise ValueError
        product_ids = decoded["product_ids"]
        drifts = decoded["drifts"]
        uncertain = decoded["uncertain"]
        if (
            decoded["schema_version"] != 1
            or decoded["account_id"] != str(target.account_id)
            or decoded["credential_version"] != target.credential_version
            or type(decoded["local_projection_sha256"]) is not str
            or len(decoded["local_projection_sha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in decoded["local_projection_sha256"]
            )
            or type(product_ids) is not list
            or not 0 <= len(product_ids) <= 4096
            or any(type(item) is not int or not 1 <= item < 2**63 for item in product_ids)
            or product_ids != sorted(set(product_ids))
            or type(drifts) is not list
            or any(type(item) is not dict for item in drifts)
            or type(uncertain) is not list
            or any(type(item) is not dict for item in uncertain)
            or any(
                set(item) != {"kind", "subject_sha256", "product_id", "external_exposure"}
                or item["kind"]
                not in {
                    "EXTERNAL_OPEN_ORDER",
                    "EXTERNAL_POSITION",
                    "LOCAL_OPEN_ORDER_MISSING",
                    "POSITION_MISMATCH",
                    "UNCERTAIN_OPERATION",
                }
                or type(item["subject_sha256"]) is not str
                or len(item["subject_sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in item["subject_sha256"])
                or (
                    item["product_id"] is not None
                    and (type(item["product_id"]) is not int or not 1 <= item["product_id"] < 2**63)
                )
                or type(item["external_exposure"]) is not bool
                for item in drifts
            )
            or any(
                set(item) != {"attempt_id", "local_order_id", "resolution", "evidence_sha256"}
                or UUID(item["attempt_id"]).int == 0
                or (item["local_order_id"] is not None and UUID(item["local_order_id"]).int == 0)
                or item["resolution"] not in {"ACCEPTED", "NOT_SENT", "UNRESOLVED"}
                or type(item["evidence_sha256"]) is not str
                or len(item["evidence_sha256"]) != 64
                or any(character not in "0123456789abcdef" for character in item["evidence_sha256"])
                for item in uncertain
            )
            or target.drift_count != len(drifts)
            or type(target.external_exposure) is not bool
            or target.external_exposure
            != any(item.get("external_exposure") is True for item in drifts)
            or (target.readiness == "READY") != (not drifts and not uncertain)
        ):
            raise ValueError
        request_payload = json.dumps(
            {
                "account_id": str(target.account_id),
                "credential_version": target.credential_version,
                "environment": target.environment,
                "mode": target.mode,
                "product_ids": product_ids,
                "region": target.region,
                "request_id": str(target.id),
                "trigger": target.trigger,
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        observed_at = datetime.fromisoformat(decoded["observed_at"])
        if (
            hashlib.sha256(request_payload).hexdigest() != target.request_sha256
            or observed_at.tzinfo is None
            or observed_at.utcoffset() != timedelta(0)
            or target.finished_at is None
            or observed_at > target.finished_at
        ):
            raise ValueError
    except Exception:
        raise PersistenceValueError() from None


@event.listens_for(AccountReconciliationCheckpoint, "before_insert")
@event.listens_for(AccountReconciliationCheckpoint, "before_update")
def _validate_payload(
    mapper: object,
    connection: Connection,
    target: AccountReconciliationCheckpoint,
) -> None:
    _validate_terminal_payload(target)


@event.listens_for(AccountReconciliationCheckpoint, "before_update")
def _freeze_terminal(
    mapper: object, connection: Connection, target: AccountReconciliationCheckpoint
) -> None:
    from sqlalchemy import select

    prior = connection.scalar(
        select(AccountReconciliationCheckpoint.state).where(
            AccountReconciliationCheckpoint.id == target.id
        )
    )
    if prior in {"FINISHED", "FAILED"}:
        raise PersistenceValueError()


@event.listens_for(AccountReconciliationCheckpoint, "before_delete")
def _reject_delete(
    mapper: object, connection: Connection, target: AccountReconciliationCheckpoint
) -> None:
    raise PersistenceValueError()


@event.listens_for(AccountReconciliationCheckpoint, "load", restore_load_context=True)
def _load(target: AccountReconciliationCheckpoint, context: object) -> None:
    _validate_terminal_payload(target)


@event.listens_for(AccountReconciliationCheckpoint, "refresh", restore_load_context=True)
def _refresh(
    target: AccountReconciliationCheckpoint,
    context: object,
    attrs: Iterable[str] | None,
) -> None:
    _validate_terminal_payload(target)


__all__ = ["AccountReconciliationCheckpoint"]
