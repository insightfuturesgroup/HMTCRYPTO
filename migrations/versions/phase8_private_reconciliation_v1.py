"""Add bounded private-account reconciliation evidence.

Revision ID: phase8_private_reconciliation_v1
Revises: phase7_live_control_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase8_private_reconciliation_v1"
down_revision: str | Sequence[str] | None = "phase7_live_control_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "private_reconciliation_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("balance_count", sa.BigInteger(), nullable=False),
        sa.Column("position_count", sa.BigInteger(), nullable=False),
        sa.Column("open_order_count", sa.BigInteger(), nullable=False),
        sa.Column("foreign_product_ids", sa.Text(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "credential_version >= 1",
            name="ck_private_reconciliation_snapshots_credential_version_pos",
        ),
        sa.CheckConstraint(
            "balance_count >= 0",
            name="ck_private_reconciliation_snapshots_balance_count_nonneg",
        ),
        sa.CheckConstraint(
            "position_count >= 0",
            name="ck_private_reconciliation_snapshots_position_count_nonneg",
        ),
        sa.CheckConstraint(
            "open_order_count >= 0",
            name="ck_private_reconciliation_snapshots_open_order_count_nonneg",
        ),
        sa.CheckConstraint(
            "outcome IN ('ready','blocked','unavailable')",
            name="ck_private_reconciliation_snapshots_outcome_allowed",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("account_id", "request_id", name="uq_private_reconciliation_request"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_private_reconciliation_account_observed",
        "private_reconciliation_snapshots",
        ["account_id", "observed_at"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT ON hmt_crypto.private_reconciliation_snapshots TO hmt_crypto_app"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 8 is forward-fix only; restore from backup for rollback")
