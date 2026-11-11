"""Add durable Live cancel and modification actions.

Revision ID: phase16_live_order_actions_v1
Revises: phase15_tradingview_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase16_live_order_actions_v1"
down_revision: str | Sequence[str] | None = "phase15_tradingview_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_order_actions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("target_size", sa.BigInteger()),
        sa.Column("target_limit_price", sa.Numeric(38, 18)),
        sa.Column("request_payload", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("worker_owner_id", sa.Uuid()),
        sa.Column("worker_generation", sa.BigInteger()),
        sa.Column("credential_version", sa.BigInteger()),
        sa.Column("exchange_response_sha256", sa.String(64)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('CANCEL','MODIFY')", name="ck_live_order_actions_action_allowed"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','SENDING','UNCERTAIN','SUCCEEDED','REJECTED','BLOCKED')",
            name="ck_live_order_actions_status_allowed",
        ),
        sa.CheckConstraint("order_id > 0", name="ck_live_order_actions_order_id_positive"),
        sa.CheckConstraint("product_id > 0", name="ck_live_order_actions_product_id_positive"),
        sa.CheckConstraint(
            "target_size IS NULL OR target_size > 0",
            name="ck_live_order_actions_target_size_positive",
        ),
        sa.CheckConstraint(
            "(action = 'CANCEL' AND target_size IS NULL AND target_limit_price IS NULL) OR "
            "(action = 'MODIFY' AND target_size IS NOT NULL)",
            name="ck_live_order_actions_action_shape",
        ),
        sa.CheckConstraint("version >= 1", name="ck_live_order_actions_version_positive"),
        sa.CheckConstraint(
            "(status = 'PENDING' AND worker_owner_id IS NULL AND worker_generation IS NULL "
            "AND credential_version IS NULL) OR status <> 'PENDING'",
            name="ck_live_order_actions_pending_unclaimed",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_live_actions_request"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_live_actions_account_pending",
        "live_order_actions",
        ["account_id", "created_at"],
        unique=False,
        schema="hmt_crypto",
        postgresql_where=sa.text("status IN ('PENDING','SENDING','UNCERTAIN')"),
    )
    op.execute(
        sa.text("GRANT SELECT, INSERT, UPDATE ON hmt_crypto.live_order_actions TO hmt_crypto_app")
    )


def downgrade() -> None:
    raise RuntimeError("Phase 16 is forward-fix only; restore from backup for rollback")
