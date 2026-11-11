"""Add append-only Live submission transition evidence.

Revision ID: phase13b_live_submission_events_v1
Revises: phase13_live_submission_queue_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase13b_live_submission_events_v1"
down_revision: str | Sequence[str] | None = "phase13_live_submission_queue_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_order_submission_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=False),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("worker_owner_id", sa.Uuid(), nullable=False),
        sa.Column("worker_generation", sa.BigInteger(), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_submission_events_sequence_positive"),
        sa.CheckConstraint(
            "from_status IN ('PENDING','CLAIMED','PREPARED','SENDING','UNCERTAIN',"
            "'ACCEPTED','REJECTED','BLOCKED')",
            name="ck_submission_events_from_status_allowed",
        ),
        sa.CheckConstraint(
            "to_status IN ('PENDING','CLAIMED','PREPARED','SENDING','UNCERTAIN',"
            "'ACCEPTED','REJECTED','BLOCKED')",
            name="ck_submission_events_to_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"], ["hmt_crypto.live_order_submissions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("submission_id", "sequence", name="uq_submission_events_sequence"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_submission_events_account_occurred",
        "live_order_submission_events",
        ["account_id", "occurred_at"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text("GRANT SELECT, INSERT ON hmt_crypto.live_order_submission_events TO hmt_crypto_app")
    )


def downgrade() -> None:
    raise RuntimeError("Phase 13B is forward-fix only; restore from backup for rollback")
