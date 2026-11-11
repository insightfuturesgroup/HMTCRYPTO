"""Add per-account private worker leases.

Revision ID: phase10_private_worker_lease_v1
Revises: phase9_private_state_projection_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase10_private_worker_lease_v1"
down_revision: str | Sequence[str] | None = "phase9_private_state_projection_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "private_reconciliation_snapshots",
        sa.Column("worker_owner_id", sa.Uuid(), nullable=True),
        schema="hmt_crypto",
    )
    op.add_column(
        "private_reconciliation_snapshots",
        sa.Column("worker_generation", sa.BigInteger(), nullable=True),
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_private_reconciliation_snapshots_worker_fence_pair",
        "private_reconciliation_snapshots",
        "(worker_owner_id IS NULL AND worker_generation IS NULL) OR "
        "(worker_owner_id IS NOT NULL AND worker_generation >= 1)",
        schema="hmt_crypto",
    )
    op.create_table(
        "private_worker_leases",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "credential_version >= 1", name="ck_private_worker_leases_credential_version_pos"
        ),
        sa.CheckConstraint("generation >= 1", name="ck_private_worker_leases_generation_pos"),
        sa.CheckConstraint(
            "lease_until > heartbeat_at", name="ck_private_worker_leases_lease_after_heartbeat"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("account_id", name="uq_private_worker_leases_account"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_private_worker_leases_expiry",
        "private_worker_leases",
        ["lease_until"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE ON hmt_crypto.private_worker_leases TO hmt_crypto_app"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 10 is forward-fix only; restore from backup for rollback")
