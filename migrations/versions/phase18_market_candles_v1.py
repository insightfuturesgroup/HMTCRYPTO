"""Add bounded public one-minute candle persistence.

Revision ID: phase18_market_candles_v1
Revises: phase17_platform_sessions_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase18_market_candles_v1"
down_revision: str | Sequence[str] | None = "phase17_platform_sessions_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_candles",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("resolution", sa.String(8), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(38, 18), nullable=False),
        sa.Column("high", sa.Numeric(38, 18), nullable=False),
        sa.Column("low", sa.Numeric(38, 18), nullable=False),
        sa.Column("close", sa.Numeric(38, 18), nullable=False),
        sa.Column("volume", sa.Numeric(38, 18)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("region = 'india'", name="ck_market_candles_india_only"),
        sa.CheckConstraint("environment = 'prod'", name="ck_market_candles_prod_only"),
        sa.CheckConstraint("resolution = '1m'", name="ck_market_candles_resolution"),
        sa.CheckConstraint(
            "open > 0 AND high > 0 AND low > 0 AND close > 0",
            name="ck_market_candles_prices_positive",
        ),
        sa.CheckConstraint("high >= low", name="ck_market_candles_range"),
        sa.CheckConstraint(
            "volume IS NULL OR volume >= 0", name="ck_market_candles_volume_nonnegative"
        ),
        sa.UniqueConstraint(
            "region",
            "environment",
            "product_id",
            "resolution",
            "timestamp",
            name="uq_market_candles_identity",
        ),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_market_candles_product_time",
        "market_candles",
        ["product_id", "resolution", sa.text("timestamp DESC")],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON hmt_crypto.market_candles TO hmt_crypto_app"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 18 is forward-fix only; restore from backup for rollback")
