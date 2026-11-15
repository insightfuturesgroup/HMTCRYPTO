"""Add sparse global and account-specific crypto contract settings."""

import sqlalchemy as sa
from alembic import op

revision = "phase28_crypto_contract_settings_v1"
down_revision = "phase27_query_indexes_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crypto_contract_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("atr_multiplier", sa.Numeric(18, 8), nullable=False),
        sa.Column("atr_period", sa.BigInteger(), nullable=False),
        sa.Column("atr_timeframe", sa.String(8), nullable=False),
        sa.Column("stop_loss_multiplier", sa.Numeric(18, 8), nullable=False),
        sa.Column("target_multiplier", sa.Numeric(18, 8), nullable=False),
        sa.Column("lot_multiplier", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "atr_multiplier > 0", name="ck_crypto_contract_settings_atr_multiplier_positive"
        ),
        sa.CheckConstraint(
            "atr_period > 0", name="ck_crypto_contract_settings_atr_period_positive"
        ),
        sa.CheckConstraint(
            "atr_timeframe IN ('1m','5m','15m','30m','1h','4h','1d')",
            name="ck_crypto_contract_settings_atr_timeframe_allowed",
        ),
        sa.CheckConstraint(
            "stop_loss_multiplier > 0",
            name="ck_crypto_contract_settings_stop_loss_multiplier_positive",
        ),
        sa.CheckConstraint(
            "target_multiplier > 0", name="ck_crypto_contract_settings_target_multiplier_positive"
        ),
        sa.CheckConstraint(
            "lot_multiplier > 0", name="ck_crypto_contract_settings_lot_multiplier_positive"
        ),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_crypto_contract_settings_account",
        "crypto_contract_settings",
        ["account_id"],
        schema="hmt_crypto",
    )
    op.create_index(
        "uq_crypto_contract_settings_global_product",
        "crypto_contract_settings",
        ["product_id"],
        unique=True,
        schema="hmt_crypto",
        postgresql_where=sa.text("account_id IS NULL"),
    )
    op.create_index(
        "uq_crypto_contract_settings_account_product",
        "crypto_contract_settings",
        ["account_id", "product_id"],
        unique=True,
        schema="hmt_crypto",
        postgresql_where=sa.text("account_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("crypto_contract_settings", schema="hmt_crypto")
