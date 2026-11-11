"""Add durable operational notifications and acknowledgement evidence.

Revision ID: phase19_notifications_v1
Revises: phase18_market_candles_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase19_notifications_v1"
down_revision: str | Sequence[str] | None = "phase18_market_candles_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_notifications",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("finding_key", sa.String(256), nullable=False),
        sa.Column("account_id", sa.Uuid()),
        sa.Column("account_name", sa.String(128), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("message", sa.String(512), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by_user_id", sa.Uuid()),
        sa.CheckConstraint(
            "severity IN ('critical','warning')", name="ck_platform_notifications_severity"
        ),
        sa.CheckConstraint(
            "last_observed_at >= first_observed_at", name="ck_platform_notifications_observed_order"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("finding_key", name="uq_platform_notifications_finding_key"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_platform_notifications_active",
        "platform_notifications",
        ["severity", "last_observed_at"],
        schema="hmt_crypto",
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE ON hmt_crypto.platform_notifications TO hmt_crypto_app"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 19 is forward-fix only; restore from backup for rollback")
