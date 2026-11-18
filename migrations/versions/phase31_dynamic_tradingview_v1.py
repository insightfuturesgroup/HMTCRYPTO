"""Add dynamic TradingView webhook routing.

Revision ID: phase31_dynamic_tradingview_v1
Revises: phase30_ensure_password_state_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase31_dynamic_tradingview_v1"
down_revision: str | Sequence[str] | None = "phase30_ensure_password_state_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "hmt_crypto"


def upgrade() -> None:
    op.create_table(
        "tradingview_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], [f"{SCHEMA}.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("created_by_user_id", "name", name="uq_tv_configurations_owner_name"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tv_configurations_owner_enabled",
        "tradingview_configurations",
        ["created_by_user_id", "enabled"],
        schema=SCHEMA,
    )
    op.create_table(
        "tradingview_configuration_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("configuration_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["configuration_id"], [f"{SCHEMA}.tradingview_configurations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("configuration_id", "account_id", name="uq_tv_configuration_accounts"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tv_configuration_accounts_account",
        "tradingview_configuration_accounts",
        ["account_id"],
        schema=SCHEMA,
    )
    op.create_table(
        "tradingview_symbol_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("tradingview_symbol", sa.String(128), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("delta_symbol", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], [f"{SCHEMA}.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_tv_symbol_mappings_global",
        "tradingview_symbol_mappings",
        ["tradingview_symbol"],
        unique=True,
        postgresql_where=sa.text("account_id IS NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_tv_symbol_mappings_account",
        "tradingview_symbol_mappings",
        ["account_id", "tradingview_symbol"],
        unique=True,
        postgresql_where=sa.text("account_id IS NOT NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_tv_symbol_mappings_global_product",
        "tradingview_symbol_mappings",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("account_id IS NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_tv_symbol_mappings_account_product",
        "tradingview_symbol_mappings",
        ["account_id", "product_id"],
        unique=True,
        postgresql_where=sa.text("account_id IS NOT NULL"),
        schema=SCHEMA,
    )

    op.add_column(
        "tradingview_webhook_keys",
        sa.Column("configuration_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.alter_column(
        "tradingview_webhook_keys",
        "strategy_id",
        existing_type=sa.Uuid(),
        nullable=True,
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_tv_webhook_keys_configuration",
        "tradingview_webhook_keys",
        "tradingview_configurations",
        ["configuration_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_tradingview_webhook_keys_exactly_one_target",
        "tradingview_webhook_keys",
        "(strategy_id IS NULL) <> (configuration_id IS NULL)",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tv_webhook_keys_configuration_active",
        "tradingview_webhook_keys",
        ["configuration_id", "revoked_at"],
        schema=SCHEMA,
    )

    op.add_column(
        "tradingview_signals",
        sa.Column("configuration_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tradingview_signals",
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tradingview_signals", sa.Column("symbol", sa.String(128), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "tradingview_signals", sa.Column("contracts", sa.BigInteger(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "tradingview_signals", sa.Column("order_type", sa.String(8), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "tradingview_signals", sa.Column("reduce_only", sa.Boolean(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "tradingview_signals",
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tradingview_signals",
        sa.Column("atr_value", sa.Numeric(38, 18), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tradingview_signals",
        sa.Column("atr_multiplier", sa.Numeric(18, 8), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tradingview_signals",
        sa.Column("stop_loss_multiplier", sa.Numeric(18, 8), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "tradingview_signals",
        sa.Column("target_multiplier", sa.Numeric(18, 8), nullable=True),
        schema=SCHEMA,
    )
    op.alter_column(
        "tradingview_signals", "strategy_id", existing_type=sa.Uuid(), nullable=True, schema=SCHEMA
    )
    op.create_foreign_key(
        "fk_tv_signals_configuration",
        "tradingview_signals",
        "tradingview_configurations",
        ["configuration_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_tv_signals_created_by",
        "tradingview_signals",
        "platform_users",
        ["created_by_user_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_tv_signals_configuration_external",
        "tradingview_signals",
        ["configuration_id", "account_id", "external_signal_id"],
        unique=True,
        postgresql_where=sa.text("configuration_id IS NOT NULL"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    raise RuntimeError("Phase 31 is forward-fix only; restore from backup for rollback")
