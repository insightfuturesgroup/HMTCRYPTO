"""Add online retention indexes for operational history pruning."""

import sqlalchemy as sa
from alembic import op

revision = "phase27_query_indexes_v1"
down_revision = "phase26_order_reconciliation_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    context = op.get_context()
    with context.autocommit_block():
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "ix_private_stream_evidence_observed_at "
                "ON hmt_crypto.private_stream_evidence (observed_at)"
            )
        )
        op.execute(
            sa.text(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_market_candles_timestamp "
                'ON hmt_crypto.market_candles ("timestamp")'
            )
        )


def downgrade() -> None:
    context = op.get_context()
    with context.autocommit_block():
        op.execute(
            sa.text("DROP INDEX CONCURRENTLY IF EXISTS hmt_crypto.ix_market_candles_timestamp")
        )
        op.execute(
            sa.text(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "hmt_crypto.ix_private_stream_evidence_observed_at"
            )
        )
