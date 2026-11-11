"""Add lease-fenced Live order submission queue.

Revision ID: phase13_live_submission_queue_v1
Revises: phase12_live_order_admission_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase13_live_submission_queue_v1"
down_revision: str | Sequence[str] | None = "phase12_live_order_admission_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_order_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("intent_id", sa.Uuid(), nullable=False),
        sa.Column("client_order_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("worker_owner_id", sa.Uuid(), nullable=True),
        sa.Column("worker_generation", sa.BigInteger(), nullable=True),
        sa.Column("credential_version", sa.BigInteger(), nullable=True),
        sa.Column("claim_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_number", sa.BigInteger(), nullable=False),
        sa.Column("exchange_order_id", sa.BigInteger(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING','CLAIMED','PREPARED','SENDING','UNCERTAIN',"
            "'ACCEPTED','REJECTED','BLOCKED')",
            name="ck_live_order_submissions_status_allowed",
        ),
        sa.CheckConstraint(
            "attempt_number >= 0", name="ck_live_order_submissions_attempt_nonnegative"
        ),
        sa.CheckConstraint(
            "status NOT IN ('PREPARED','SENDING','UNCERTAIN','ACCEPTED','REJECTED') "
            "OR attempt_number >= 1",
            name="ck_live_order_submissions_attempt_required",
        ),
        sa.CheckConstraint("version >= 1", name="ck_live_order_submissions_version_positive"),
        sa.CheckConstraint(
            "(status = 'PENDING' AND worker_owner_id IS NULL AND worker_generation IS NULL "
            "AND credential_version IS NULL AND claim_until IS NULL) OR status <> 'PENDING'",
            name="ck_live_order_submissions_pending_unclaimed",
        ),
        sa.CheckConstraint(
            "status = 'PENDING' OR (worker_owner_id IS NOT NULL AND worker_generation IS NOT NULL "
            "AND credential_version IS NOT NULL AND claim_until IS NOT NULL)",
            name="ck_live_order_submissions_claimed_fence_required",
        ),
        sa.CheckConstraint(
            "(status = 'ACCEPTED' AND exchange_order_id IS NOT NULL) OR "
            "(status <> 'ACCEPTED' AND exchange_order_id IS NULL)",
            name="ck_live_order_submissions_accepted_exchange_order_required",
        ),
        sa.CheckConstraint(
            "status <> 'UNCERTAIN' OR last_error_code IS NOT NULL",
            name="ck_live_order_submissions_uncertain_error_required",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["intent_id"], ["hmt_crypto.live_order_intents.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("intent_id", name="uq_live_order_submissions_intent"),
        sa.UniqueConstraint(
            "account_id", "client_order_id", name="uq_live_order_submissions_client_order"
        ),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_live_order_submissions_account_status",
        "live_order_submissions",
        ["account_id", "status", "created_at"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_live_order_submissions_claim",
        "live_order_submissions",
        ["status", "claim_until"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE ON hmt_crypto.live_order_submissions TO hmt_crypto_app"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 13 is forward-fix only; restore from backup for rollback")
