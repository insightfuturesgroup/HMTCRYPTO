"""Add fail-closed per-account Live readiness controls.

Revision ID: phase7_live_control_v1
Revises: phase6_multi_user_platform_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase7_live_control_v1"
down_revision: str | Sequence[str] | None = "phase6_multi_user_platform_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_live_controls",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("credential_state", sa.String(16), nullable=False),
        sa.Column("reconciliation_state", sa.String(16), nullable=False),
        sa.Column("entries_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exits_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "credential_state IN ('unconfigured','pending','verified','failed')",
            name="ck_account_live_controls_credential_state_allowed",
        ),
        sa.CheckConstraint(
            "reconciliation_state IN ('unknown','running','ready','degraded','blocked')",
            name="ck_account_live_controls_reconciliation_state_allowed",
        ),
        sa.CheckConstraint("version >= 1", name="ck_account_live_controls_version_positive"),
        sa.ForeignKeyConstraint(["account_id"], ["hmt_crypto.accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("account_id", name="uq_account_live_controls_account"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_account_live_controls_readiness",
        "account_live_controls",
        ["credential_state", "reconciliation_state"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE ON hmt_crypto.account_live_controls TO hmt_crypto_app"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 7 is forward-fix only; restore from backup for rollback")
