"""Add append-only uncertain submission lookup evidence.

Revision ID: phase13e_uncertain_recovery_v1
Revises: phase13d_live_submission_outcomes_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase13e_uncertain_recovery_v1"
down_revision: str | Sequence[str] | None = "phase13d_live_submission_outcomes_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_order_recovery_checks",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.BigInteger(), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("client_order_id", sa.String(32), nullable=False),
        sa.Column("exchange_order_id", sa.BigInteger()),
        sa.Column("worker_owner_id", sa.Uuid(), nullable=False),
        sa.Column("worker_generation", sa.BigInteger(), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt >= 1", name="ck_recovery_checks_attempt_positive"),
        sa.CheckConstraint(
            "result IN ('FOUND','NOT_FOUND','UNAVAILABLE')",
            name="ck_recovery_checks_result_allowed",
        ),
        sa.CheckConstraint(
            "(result = 'FOUND' AND exchange_order_id IS NOT NULL) OR "
            "(result <> 'FOUND' AND exchange_order_id IS NULL)",
            name="ck_recovery_checks_found_order_required",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["hmt_crypto.live_order_submissions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("submission_id", "attempt", name="uq_recovery_checks_attempt"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_recovery_checks_account_occurred",
        "live_order_recovery_checks",
        ["account_id", "occurred_at"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text("GRANT SELECT, INSERT ON hmt_crypto.live_order_recovery_checks TO hmt_crypto_app")
    )


def downgrade() -> None:
    raise RuntimeError("Phase 13E is forward-fix only; restore from backup for rollback")
