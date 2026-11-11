"""Add replay-safe TradingView strategy and signal records.

Revision ID: phase15_tradingview_v1
Revises: phase14_gtt_trigger_state_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase15_tradingview_v1"
down_revision: str | Sequence[str] | None = "phase14_gtt_trigger_state_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tradingview_strategies",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("contracts", sa.BigInteger(), nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("contracts > 0", name="ck_tv_strategies_contracts_positive"),
        sa.CheckConstraint(
            "order_type IN ('market','limit')", name="ck_tv_strategies_order_type_allowed"
        ),
        sa.CheckConstraint("version >= 1", name="ck_tv_strategies_version_positive"),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("account_id", "name", name="uq_tv_strategies_account_name"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_tv_strategies_account_enabled",
        "tradingview_strategies",
        ["account_id", "enabled"],
        schema="hmt_crypto",
    )
    op.create_table(
        "tradingview_webhook_keys",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("key_sha256", sa.String(64), nullable=False),
        sa.Column("key_fingerprint", sa.String(16), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["hmt_crypto.tradingview_strategies.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("key_sha256", name="uq_tv_webhook_keys_digest"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_tv_webhook_keys_strategy_active",
        "tradingview_webhook_keys",
        ["strategy_id", "revoked_at"],
        schema="hmt_crypto",
    )
    op.create_table(
        "tradingview_signals",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("external_signal_id", sa.String(128), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("limit_price", sa.String(64)),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rejection_code", sa.String(64)),
        sa.Column("intent_id", sa.Uuid()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("side IN ('buy','sell')", name="ck_tv_signals_side_allowed"),
        sa.CheckConstraint(
            "status IN ('received','admitted','rejected','duplicate')",
            name="ck_tv_signals_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["hmt_crypto.tradingview_strategies.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("strategy_id", "external_signal_id", name="uq_tv_signals_external"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_tv_signals_strategy_received",
        "tradingview_signals",
        ["strategy_id", "received_at"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_tv_signals_account_status",
        "tradingview_signals",
        ["account_id", "status"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE ON hmt_crypto.tradingview_strategies, "
            "hmt_crypto.tradingview_webhook_keys, hmt_crypto.tradingview_signals "
            "TO hmt_crypto_app"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 15 is forward-fix only; restore from backup for rollback")
