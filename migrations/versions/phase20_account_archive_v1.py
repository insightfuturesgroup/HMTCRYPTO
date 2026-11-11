"""Add recoverable account archival without deleting trading evidence.

Revision ID: phase20_account_archive_v1
Revises: phase19_notifications_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase20_account_archive_v1"
down_revision: str | Sequence[str] | None = "phase19_notifications_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_accounts_active_scope",
        "accounts",
        ["region", "environment", "mode", "profile_name"],
        schema="hmt_crypto",
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    raise RuntimeError("Phase 20 is forward-fix only; restore from backup for rollback")
