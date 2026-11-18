"""Add audit-safe local order visibility controls."""

import sqlalchemy as sa
from alembic import op

revision = "phase25_order_visibility_v1"
down_revision = "phase24_owner_live_controls_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_order_intents",
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        schema="hmt_crypto",
    )
    op.add_column(
        "live_order_intents",
        sa.Column("hidden_by_user_id", sa.Uuid(), nullable=True),
        schema="hmt_crypto",
    )
    op.create_foreign_key(
        "fk_live_order_intents_hidden_by_user",
        "live_order_intents",
        "platform_users",
        ["hidden_by_user_id"],
        ["id"],
        source_schema="hmt_crypto",
        referent_schema="hmt_crypto",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_live_order_intents_hidden_by_user",
        "live_order_intents",
        schema="hmt_crypto",
        type_="foreignkey",
    )
    op.drop_column("live_order_intents", "hidden_by_user_id", schema="hmt_crypto")
    op.drop_column("live_order_intents", "hidden_at", schema="hmt_crypto")
