"""Add durable GTT evaluation and trigger evidence.

Revision ID: phase14_gtt_trigger_state_v1
Revises: phase13e_uncertain_recovery_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase14_gtt_trigger_state_v1"
down_revision: str | Sequence[str] | None = "phase13e_uncertain_recovery_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gtt_orders", sa.Column("trail_anchor", sa.Numeric(38, 18)), schema="hmt_crypto")
    op.add_column("gtt_orders", sa.Column("last_quote_id", sa.Uuid()), schema="hmt_crypto")
    op.add_column(
        "gtt_orders", sa.Column("triggered_at", sa.DateTime(timezone=True)), schema="hmt_crypto"
    )
    op.add_column(
        "gtt_orders", sa.Column("trigger_evidence_sha256", sa.String(64)), schema="hmt_crypto"
    )
    op.add_column("gtt_orders", sa.Column("last_error_code", sa.String(64)), schema="hmt_crypto")
    op.create_foreign_key(
        "fk_gtt_orders_last_quote",
        "gtt_orders",
        "market_quotes",
        ["last_quote_id"],
        ["id"],
        source_schema="hmt_crypto",
        referent_schema="hmt_crypto",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_gtt_orders_trail_anchor_only_for_trailing",
        "gtt_orders",
        "trail_anchor IS NULL OR trigger_kind = 'trailing_stop'",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_gtt_orders_trigger_evidence_pair",
        "gtt_orders",
        "(triggered_at IS NULL AND trigger_evidence_sha256 IS NULL) OR "
        "(triggered_at IS NOT NULL AND trigger_evidence_sha256 IS NOT NULL)",
        schema="hmt_crypto",
    )
    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE ON hmt_crypto.gtt_orders TO hmt_crypto_app"))


def downgrade() -> None:
    raise RuntimeError("Phase 14 is forward-fix only; restore from backup for rollback")
