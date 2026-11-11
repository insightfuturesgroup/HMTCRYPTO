"""Add hard account risk policies and a global Live entry gate.

Revision ID: phase11_live_risk_controls_v1
Revises: phase10_private_worker_lease_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase11_live_risk_controls_v1"
down_revision: str | Sequence[str] | None = "phase10_private_worker_lease_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_risk_policies",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("max_order_notional", sa.Numeric(38, 18), nullable=False),
        sa.Column("max_position_notional", sa.Numeric(38, 18), nullable=False),
        sa.Column("max_daily_loss", sa.Numeric(38, 18), nullable=False),
        sa.Column("max_open_orders", sa.BigInteger(), nullable=False),
        sa.Column("allowed_product_ids", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "max_order_notional > 0", name="ck_account_risk_policies_max_order_notional_pos"
        ),
        sa.CheckConstraint(
            "max_position_notional > 0", name="ck_account_risk_policies_max_position_notional_pos"
        ),
        sa.CheckConstraint(
            "max_daily_loss > 0", name="ck_account_risk_policies_max_daily_loss_pos"
        ),
        sa.CheckConstraint(
            "max_open_orders >= 1", name="ck_account_risk_policies_max_open_orders_pos"
        ),
        sa.CheckConstraint("version >= 1", name="ck_account_risk_policies_version_pos"),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("account_id", name="uq_account_risk_policies_account"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_account_risk_policies_updated_by",
        "account_risk_policies",
        ["updated_by_user_id"],
        schema="hmt_crypto",
    )
    op.create_table(
        "platform_live_controls",
        sa.Column("control_key", sa.String(16), primary_key=True, nullable=False),
        sa.Column("entries_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("control_key = 'global'", name="ck_platform_live_controls_global_key"),
        sa.CheckConstraint("version >= 1", name="ck_platform_live_controls_version_pos"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "INSERT INTO hmt_crypto.platform_live_controls "
            "(control_key, entries_enabled, reason, version, updated_at) "
            "VALUES ('global', false, 'deployment_default', 1, CURRENT_TIMESTAMP)"
        )
    )
    op.create_index(
        "ix_platform_live_controls_updated_by",
        "platform_live_controls",
        ["updated_by_user_id"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE ON hmt_crypto.account_risk_policies TO hmt_crypto_app"
        )
    )
    op.execute(
        sa.text("GRANT SELECT, UPDATE ON hmt_crypto.platform_live_controls TO hmt_crypto_app")
    )


def downgrade() -> None:
    raise RuntimeError("Phase 11 is forward-fix only; restore from backup for rollback")
