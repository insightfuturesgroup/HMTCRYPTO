"""Add non-dispatchable Live order previews and admitted intents.

Revision ID: phase12_live_order_admission_v1
Revises: phase11_live_risk_controls_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase12_live_order_admission_v1"
down_revision: str | Sequence[str] | None = "phase11_live_risk_controls_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_order_previews",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("contracts", sa.BigInteger(), nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False),
        sa.Column("limit_price", sa.Numeric(38, 18), nullable=True),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        sa.Column("price_basis", sa.String(16), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("notional", sa.Numeric(38, 18), nullable=False),
        sa.Column("catalogue_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("risk_policy_version", sa.BigInteger(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("side IN ('buy','sell')", name="ck_live_order_previews_side_allowed"),
        sa.CheckConstraint(
            "order_type IN ('market','limit')", name="ck_live_order_previews_order_type_allowed"
        ),
        sa.CheckConstraint("contracts >= 1", name="ck_live_order_previews_contracts_pos"),
        sa.CheckConstraint("notional > 0", name="ck_live_order_previews_notional_pos"),
        sa.CheckConstraint("expires_at > created_at", name="ck_live_order_previews_expiry_order"),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["catalogue_snapshot_id"], ["hmt_crypto.catalogue_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reconciliation_snapshot_id"],
            ["hmt_crypto.private_reconciliation_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("account_id", "request_id", name="uq_live_order_previews_request"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_live_order_previews_account_expiry",
        "live_order_previews",
        ["account_id", "expires_at"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_live_order_previews_created_by",
        "live_order_previews",
        ["created_by_user_id"],
        schema="hmt_crypto",
    )
    op.create_table(
        "live_order_intents",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("preview_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("intent_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'ADMITTED'", name="ck_live_order_intents_admitted_only"),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["preview_id"], ["hmt_crypto.live_order_previews.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("preview_id", name="uq_live_order_intents_preview"),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_live_order_intents_request"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_live_order_intents_account_created",
        "live_order_intents",
        ["account_id", "created_at"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_live_order_intents_created_by",
        "live_order_intents",
        ["created_by_user_id"],
        schema="hmt_crypto",
    )
    op.execute(sa.text("GRANT SELECT, INSERT ON hmt_crypto.live_order_previews TO hmt_crypto_app"))
    op.execute(sa.text("GRANT SELECT, INSERT ON hmt_crypto.live_order_intents TO hmt_crypto_app"))


def downgrade() -> None:
    raise RuntimeError("Phase 12 is forward-fix only; restore from backup for rollback")
