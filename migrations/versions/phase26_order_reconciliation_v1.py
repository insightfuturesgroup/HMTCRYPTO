"""Persist authoritative Delta order state for recovery and visibility."""

import sqlalchemy as sa
from alembic import op

revision = "phase26_order_reconciliation_v1"
down_revision = "phase25_order_visibility_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("exchange_order_state", sa.String(16), nullable=True),
        sa.Column("exchange_order_size", sa.BigInteger(), nullable=True),
        sa.Column("exchange_unfilled_size", sa.BigInteger(), nullable=True),
        sa.Column("exchange_cancellation_reason", sa.String(256), nullable=True),
    ):
        op.add_column("live_order_submissions", column, schema="hmt_crypto")
    op.create_check_constraint(
        "ck_live_order_submissions_exchange_order_state_allowed",
        "live_order_submissions",
        "exchange_order_state IS NULL OR exchange_order_state IN "
        "('open','pending','closed','cancelled')",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_live_order_submissions_exchange_order_size_nonnegative",
        "live_order_submissions",
        "exchange_order_size IS NULL OR exchange_order_size >= 0",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_live_order_submissions_exchange_unfilled_size_nonnegative",
        "live_order_submissions",
        "exchange_unfilled_size IS NULL OR exchange_unfilled_size >= 0",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_live_order_submissions_exchange_unfilled_within_size",
        "live_order_submissions",
        "exchange_order_size IS NULL OR exchange_unfilled_size IS NULL OR "
        "exchange_unfilled_size <= exchange_order_size",
        schema="hmt_crypto",
    )


def downgrade() -> None:
    for constraint in (
        "ck_live_order_submissions_exchange_unfilled_within_size",
        "ck_live_order_submissions_exchange_unfilled_size_nonnegative",
        "ck_live_order_submissions_exchange_order_size_nonnegative",
        "ck_live_order_submissions_exchange_order_state_allowed",
    ):
        op.drop_constraint(
            constraint,
            "live_order_submissions",
            schema="hmt_crypto",
            type_="check",
        )
    for column in (
        "exchange_cancellation_reason",
        "exchange_unfilled_size",
        "exchange_order_size",
        "exchange_order_state",
    ):
        op.drop_column("live_order_submissions", column, schema="hmt_crypto")
