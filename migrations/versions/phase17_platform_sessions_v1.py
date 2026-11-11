"""Add revocable application sessions for the multi-user platform.

Revision ID: phase17_platform_sessions_v1
Revises: phase16_live_order_actions_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase17_platform_sessions_v1"
down_revision: str | Sequence[str] | None = "phase16_live_order_actions_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("user_agent_sha256", sa.String(64)),
        sa.CheckConstraint("expires_at > created_at", name="ck_platform_sessions_expiry_order"),
        sa.CheckConstraint(
            "last_seen_at >= created_at", name="ck_platform_sessions_last_seen_order"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("token_sha256", name="uq_platform_sessions_token_sha256"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_platform_sessions_user_active",
        "platform_sessions",
        ["user_id", "expires_at"],
        schema="hmt_crypto",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.execute(
        sa.text("GRANT SELECT, INSERT, UPDATE ON hmt_crypto.platform_sessions TO hmt_crypto_app")
    )


def downgrade() -> None:
    raise RuntimeError("Phase 17 is forward-fix only; restore from backup for rollback")
