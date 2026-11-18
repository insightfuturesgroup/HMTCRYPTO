"""Add first-login password-change state for provisioned users.

Revision ID: phase24_temporary_password_v1
Revises: phase23_hmt_ui_watchlists_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase24_temporary_password_v1"
down_revision: str | Sequence[str] | None = "phase23_hmt_ui_watchlists_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_users",
        sa.Column(
            "password_change_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema="hmt_crypto",
    )


def downgrade() -> None:
    raise RuntimeError("Phase 24 is forward-fix only; restore from backup for rollback")
