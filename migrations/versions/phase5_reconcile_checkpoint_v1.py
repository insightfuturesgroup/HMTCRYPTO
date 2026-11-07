"""Add the durable account-reconciliation publication barrier.

Revision ID: phase5_reconcile_checkpoint_v1
Revises: phase5_control_v1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase5_reconcile_checkpoint_v1"
down_revision: str | None = "phase5_control_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one immutable terminal checkpoint per reconciliation request."""
    op.create_table(
        "account_reconciliation_checkpoints",
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("external_user_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("readiness", sa.String(length=16), nullable=False),
        sa.Column("evidence_payload", sa.Text(), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("drift_count", sa.BigInteger(), nullable=False),
        sa.Column("external_exposure", sa.Boolean(), nullable=False),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "region IN ('india', 'global')",
            name="ck_account_reconciliation_checkpoints_region",
        ),
        sa.CheckConstraint(
            "environment IN ('testnet', 'prod')",
            name="ck_account_reconciliation_checkpoints_environment",
        ),
        sa.CheckConstraint(
            "mode IN ('BACKTEST', 'PAPER', 'LIVE')",
            name="ck_account_reconciliation_checkpoints_mode",
        ),
        sa.CheckConstraint(
            "trigger IN ('STARTUP','PERIODIC','MANUAL','RECOVERY','RECONNECT')",
            name="ck_account_reconciliation_checkpoints_trigger_enum",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING','FINISHED','FAILED')",
            name="ck_account_reconciliation_checkpoints_state_enum",
        ),
        sa.CheckConstraint(
            "readiness IN ('BLOCKED','READY')",
            name="ck_account_reconciliation_checkpoints_readiness_enum",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code IN "
            "('REQUEST_FAILED','INVALID_RESPONSE','PAGINATION_INCOMPLETE','LIMIT_REACHED',"
            "'LOCAL_STATE_CHANGED','UNKNOWN')",
            name="ck_account_reconciliation_checkpoints_failure_code_enum",
        ),
        sa.CheckConstraint(
            "credential_version >= 1",
            name="ck_account_reconciliation_checkpoints_credential_versio_42e4",
        ),
        sa.CheckConstraint(
            "version >= 1 AND drift_count >= 0",
            name="ck_account_reconciliation_checkpoints_positive_counters",
        ),
        sa.CheckConstraint(
            "length(external_user_sha256) = 64 AND external_user_sha256 !~ '[^0-9a-f]'",
            name="ck_account_reconciliation_checkpoints_external_user_sha_9324",
        ),
        sa.CheckConstraint(
            "length(request_sha256) = 64 AND request_sha256 !~ '[^0-9a-f]'",
            name="ck_account_reconciliation_checkpoints_request_sha256_sha256",
        ),
        sa.CheckConstraint(
            "evidence_sha256 IS NULL OR (length(evidence_sha256) = 64 AND "
            "evidence_sha256 !~ '[^0-9a-f]')",
            name="ck_account_reconciliation_checkpoints_evidence_sha256_sha256",
        ),
        sa.CheckConstraint(
            "evidence_payload IS NULL OR (length(evidence_payload) BETWEEN 1 AND 16777216 "
            "AND length(trim(evidence_payload)) > 0)",
            name="ck_account_reconciliation_checkpoints_evidence_payload_bounded",
        ),
        sa.CheckConstraint(
            "(state = 'PENDING' AND readiness = 'BLOCKED' AND evidence_payload IS NULL "
            "AND evidence_sha256 IS NULL AND failure_code IS NULL AND finished_at IS NULL "
            "AND drift_count = 0 AND external_exposure = false AND version = 1) OR "
            "(state = 'FINISHED' AND evidence_payload IS NOT NULL "
            "AND evidence_sha256 IS NOT NULL AND failure_code IS NULL "
            "AND finished_at IS NOT NULL AND version >= 2) OR "
            "(state = 'FAILED' AND readiness = 'BLOCKED' AND evidence_payload IS NULL "
            "AND evidence_sha256 IS NULL AND failure_code IS NOT NULL "
            "AND finished_at IS NOT NULL AND version >= 2)",
            name="ck_account_reconciliation_checkpoints_outcome_shape",
        ),
        sa.CheckConstraint(
            "readiness <> 'READY' OR (state = 'FINISHED' AND drift_count = 0 "
            "AND external_exposure = false)",
            name="ck_account_reconciliation_checkpoints_ready_requires_cl_9b0a",
        ),
        sa.CheckConstraint(
            "created_at <= updated_at AND (finished_at IS NULL OR "
            "(created_at <= finished_at AND finished_at <= updated_at))",
            name="ck_account_reconciliation_checkpoints_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "region", "environment", "mode"],
            [
                "hmt_crypto.accounts.id",
                "hmt_crypto.accounts.region",
                "hmt_crypto.accounts.environment",
                "hmt_crypto.accounts.mode",
            ],
            name="fk_account_reconciliation_checkpoints_account_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "region", "environment", "mode", "credential_version"],
            [
                "hmt_crypto.credential_versions.account_id",
                "hmt_crypto.credential_versions.region",
                "hmt_crypto.credential_versions.environment",
                "hmt_crypto.credential_versions.mode",
                "hmt_crypto.credential_versions.credential_version",
            ],
            name="fk_account_reconciliation_checkpoints_credential_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_reconciliation_checkpoints"),
        sa.UniqueConstraint(
            "id",
            "account_id",
            "region",
            "environment",
            "mode",
            name="uq_account_reconciliation_checkpoints_id_scope",
        ),
        sa.UniqueConstraint(
            "id",
            "account_id",
            "region",
            "environment",
            "mode",
            "request_sha256",
            name="uq_account_reconciliation_checkpoint_request",
        ),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_account_reconciliation_checkpoints_scope",
        "account_reconciliation_checkpoints",
        ["account_id", "region", "environment", "mode"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_account_reconciliation_checkpoint_latest",
        "account_reconciliation_checkpoints",
        ["account_id", "region", "environment", "mode", "finished_at"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text("""
CREATE FUNCTION hmt_crypto.guard_account_reconciliation_checkpoints_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'account reconciliation checkpoints are not deletable';
    END IF;
    IF OLD.state IN ('FINISHED', 'FAILED') THEN
        RAISE EXCEPTION 'terminal account reconciliation checkpoints are immutable';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.account_id IS DISTINCT FROM OLD.account_id
       OR NEW.region IS DISTINCT FROM OLD.region
       OR NEW.environment IS DISTINCT FROM OLD.environment
       OR NEW.mode IS DISTINCT FROM OLD.mode
       OR NEW.trigger IS DISTINCT FROM OLD.trigger
       OR NEW.credential_version IS DISTINCT FROM OLD.credential_version
       OR NEW.external_user_sha256 IS DISTINCT FROM OLD.external_user_sha256
       OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'account reconciliation checkpoint identity is immutable';
    END IF;
    IF NEW.state NOT IN ('FINISHED', 'FAILED') OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'invalid account reconciliation checkpoint transition';
    END IF;
    RETURN NEW;
END;
$$
""")
    )
    op.execute(
        sa.text("""
CREATE TRIGGER guard_account_reconciliation_checkpoints_v1
BEFORE UPDATE OR DELETE ON hmt_crypto.account_reconciliation_checkpoints
FOR EACH ROW EXECUTE FUNCTION hmt_crypto.guard_account_reconciliation_checkpoints_v1()
""")
    )
    op.execute(
        sa.text("REVOKE ALL ON TABLE hmt_crypto.account_reconciliation_checkpoints FROM PUBLIC")
    )


def downgrade() -> None:
    """Refuse to discard reconciliation evidence, then remove an unused table."""
    op.execute(
        sa.text("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM hmt_crypto.account_reconciliation_checkpoints) THEN
        RAISE EXCEPTION 'refusing to drop non-empty account reconciliation checkpoints';
    END IF;
END
$$
""")
    )
    op.execute(
        sa.text(
            "DROP TRIGGER guard_account_reconciliation_checkpoints_v1 "
            "ON hmt_crypto.account_reconciliation_checkpoints"
        )
    )
    op.execute(sa.text("DROP FUNCTION hmt_crypto.guard_account_reconciliation_checkpoints_v1()"))
    op.drop_index(
        "ix_account_reconciliation_checkpoint_latest",
        table_name="account_reconciliation_checkpoints",
        schema="hmt_crypto",
    )
    op.drop_index(
        "ix_account_reconciliation_checkpoints_scope",
        table_name="account_reconciliation_checkpoints",
        schema="hmt_crypto",
    )
    op.drop_table("account_reconciliation_checkpoints", schema="hmt_crypto")
