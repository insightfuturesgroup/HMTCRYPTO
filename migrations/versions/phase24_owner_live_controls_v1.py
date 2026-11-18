"""Add owner risk override, dynamic product scope, and emergency latches."""

import sqlalchemy as sa
from alembic import op

revision = "phase24_owner_live_controls_v1"
down_revision = "phase23_hmt_ui_watchlists_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "account_risk_policies",
        sa.Column("product_scope_mode", sa.String(24), nullable=False, server_default="SELECTED"),
        schema="hmt_crypto",
    )
    op.add_column(
        "account_risk_policies",
        sa.Column(
            "auto_activation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "product_scope_mode_allowed",
        "account_risk_policies",
        "product_scope_mode IN ('SELECTED','ALL_SUPPORTED')",
        schema="hmt_crypto",
    )
    op.add_column(
        "account_live_controls",
        sa.Column("emergency_disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="hmt_crypto",
    )
    op.add_column(
        "platform_live_controls",
        sa.Column("emergency_disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="hmt_crypto",
    )


def downgrade() -> None:
    op.drop_column("platform_live_controls", "emergency_disabled", schema="hmt_crypto")
    op.drop_column("account_live_controls", "emergency_disabled", schema="hmt_crypto")
    op.drop_constraint(
        "product_scope_mode_allowed",
        "account_risk_policies",
        schema="hmt_crypto",
        type_="check",
    )
    op.drop_column("account_risk_policies", "auto_activation_enabled", schema="hmt_crypto")
    op.drop_column("account_risk_policies", "product_scope_mode", schema="hmt_crypto")
