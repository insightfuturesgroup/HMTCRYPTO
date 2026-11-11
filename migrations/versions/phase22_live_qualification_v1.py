"""Add Live qualification, private-stream and INR risk evidence.

Revision ID: phase22_live_qualification_v1
Revises: phase21_credential_identity_binding_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase22_live_qualification_v1"
down_revision: str | Sequence[str] | None = "phase21_credential_identity_binding_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for _name, column in (
        (
            "stop_loss_pct",
            sa.Column("stop_loss_pct", sa.Numeric(9, 6), nullable=False, server_default="2"),
        ),
        (
            "target_pct",
            sa.Column("target_pct", sa.Numeric(9, 6), nullable=False, server_default="4"),
        ),
        (
            "trailing_offset_pct",
            sa.Column("trailing_offset_pct", sa.Numeric(9, 6), nullable=False, server_default="1"),
        ),
        (
            "protection_required",
            sa.Column(
                "protection_required", sa.Boolean(), nullable=False, server_default=sa.true()
            ),
        ),
    ):
        op.add_column("tradingview_strategies", column, schema="hmt_crypto")
    op.create_check_constraint(
        "ck_tradingview_strategies_stop_loss_pct_positive",
        "tradingview_strategies",
        "stop_loss_pct > 0",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_tradingview_strategies_target_pct_positive",
        "tradingview_strategies",
        "target_pct > 0",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_tradingview_strategies_trailing_offset_pct_positive",
        "tradingview_strategies",
        "trailing_offset_pct > 0",
        schema="hmt_crypto",
    )

    risk_columns = (
        sa.Column("max_inflight_signals", sa.BigInteger(), nullable=False, server_default="5"),
        sa.Column("limit_currency", sa.String(8), nullable=False, server_default="INR"),
        sa.Column("usd_inr_rate", sa.Numeric(18, 6), nullable=False, server_default="100"),
        sa.Column("max_order_limit_inr", sa.Numeric(38, 18), nullable=False, server_default="5000"),
        sa.Column(
            "max_position_limit_inr", sa.Numeric(38, 18), nullable=False, server_default="10000"
        ),
        sa.Column(
            "max_daily_loss_limit_inr", sa.Numeric(38, 18), nullable=False, server_default="2000"
        ),
    )
    for column in risk_columns:
        op.add_column("account_risk_policies", column, schema="hmt_crypto")
    op.create_check_constraint(
        "ck_account_risk_policies_max_inflight_signals_pos",
        "account_risk_policies",
        "max_inflight_signals >= 1",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_account_risk_policies_usd_inr_rate_pos",
        "account_risk_policies",
        "usd_inr_rate > 0",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_account_risk_policies_limit_currency_inr",
        "account_risk_policies",
        "limit_currency = 'INR'",
        schema="hmt_crypto",
    )

    op.create_table(
        "private_stream_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("sequence_number", sa.BigInteger()),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("worker_owner_id", sa.Uuid(), nullable=False),
        sa.Column("worker_generation", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "channel IN ('orders','positions','margins','v2/user_trades')",
            name="ck_private_stream_evidence_channel_allowed",
        ),
        sa.CheckConstraint(
            "state IN ('connected','snapshot','live','gap','disconnected')",
            name="ck_private_stream_evidence_state_allowed",
        ),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_private_stream_account_observed",
        "private_stream_evidence",
        ["account_id", "observed_at"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_private_stream_account_channel_observed",
        "private_stream_evidence",
        ["account_id", "channel", "observed_at"],
        schema="hmt_crypto",
    )
    op.create_table(
        "live_qualification_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("release_commit", sa.String(40), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_completed_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by_user_id", sa.Uuid()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('collecting','qualified','rejected','revoked')",
            name="ck_live_qualification_evidence_status_allowed",
        ),
        sa.UniqueConstraint("evidence_sha256", name="uq_live_qualification_digest"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_live_qualification_account_status",
        "live_qualification_evidence",
        ["account_id", "status"],
        schema="hmt_crypto",
    )
    op.create_table(
        "protection_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("entry_intent_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("exit_side", sa.String(8), nullable=False),
        sa.Column("protected_contracts", sa.BigInteger(), nullable=False),
        sa.Column("average_fill_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("strategy_version", sa.BigInteger()),
        sa.Column("protection_version", sa.BigInteger(), nullable=False),
        sa.Column("catalogue_revision", sa.BigInteger(), nullable=False),
        sa.Column("reconciliation_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("risk_policy_version", sa.BigInteger(), nullable=False),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("qualification_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("claimed_leg_id", sa.Uuid()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["entry_intent_id"], ["hmt_crypto.live_order_intents.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reconciliation_snapshot_id"],
            ["hmt_crypto.private_reconciliation_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["qualification_evidence_id"],
            ["hmt_crypto.live_qualification_evidence.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('pending_fill','armed','claiming','completed','cancelled','failed')",
            name="ck_protection_groups_status_allowed",
        ),
        sa.CheckConstraint(
            "protected_contracts > 0", name="ck_protection_groups_contracts_positive"
        ),
        sa.CheckConstraint(
            "average_fill_price > 0", name="ck_protection_groups_fill_price_positive"
        ),
        sa.CheckConstraint("version >= 1", name="ck_protection_groups_version_positive"),
        sa.UniqueConstraint("entry_intent_id", name="uq_protection_group_entry_intent"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_protection_group_account_status",
        "protection_groups",
        ["account_id", "status"],
        schema="hmt_crypto",
    )
    op.add_column("gtt_orders", sa.Column("protection_group_id", sa.Uuid()), schema="hmt_crypto")
    op.create_foreign_key(
        "fk_gtt_orders_protection_group",
        "gtt_orders",
        "protection_groups",
        ["protection_group_id"],
        ["id"],
        source_schema="hmt_crypto",
        referent_schema="hmt_crypto",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_gtt_protection_group_status",
        "gtt_orders",
        ["protection_group_id", "status"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE ON "
            "hmt_crypto.private_stream_evidence, "
            "hmt_crypto.live_qualification_evidence, "
            "hmt_crypto.protection_groups TO hmt_crypto_app"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 22 is forward-fix only; restore from backup for rollback")
