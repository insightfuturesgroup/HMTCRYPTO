"""Add bounded sanitized private account-state projections.

Revision ID: phase9_private_state_projection_v1
Revises: phase8_version_width_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase9_private_state_projection_v1"
down_revision: str | Sequence[str] | None = "phase8_version_width_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = "private_reconciliation_snapshots"
    op.add_column(
        table,
        sa.Column("fill_count", sa.BigInteger(), nullable=False, server_default="0"),
        schema="hmt_crypto",
    )
    op.add_column(
        table,
        sa.Column("wallet_transaction_count", sa.BigInteger(), nullable=False, server_default="0"),
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_private_reconciliation_snapshots_fill_count_nonneg",
        table,
        "fill_count >= 0",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_private_reconciliation_snapshots_wallet_tx_count_nonneg",
        table,
        "wallet_transaction_count >= 0",
        schema="hmt_crypto",
    )
    for column, constraint_name in (
        ("balances_payload", "balances_payload_bounded"),
        ("positions_payload", "positions_payload_bounded"),
        ("open_orders_payload", "open_orders_payload_bounded"),
        ("fills_payload", "fills_payload_bounded"),
        ("wallet_transactions_payload", "wallet_tx_payload_bounded"),
    ):
        op.add_column(table, sa.Column(column, sa.Text(), nullable=True), schema="hmt_crypto")
        op.create_check_constraint(
            f"ck_private_reconciliation_snapshots_{constraint_name}",
            table,
            f"{column} IS NULL OR octet_length({column}) <= 1048576",
            schema="hmt_crypto",
        )


def downgrade() -> None:
    raise RuntimeError("Phase 9 is forward-fix only; restore from backup for rollback")
