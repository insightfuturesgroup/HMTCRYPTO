"""Add Live submission send and resolution timestamps.

Revision ID: phase13d_live_submission_outcomes_v1
Revises: phase13c_live_order_wire_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase13d_live_submission_outcomes_v1"
down_revision: str | Sequence[str] | None = "phase13c_live_order_wire_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "live_order_submissions",
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        schema="hmt_crypto",
    )
    op.add_column(
        "live_order_submissions",
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_live_order_submissions_sent_timestamp_required",
        "live_order_submissions",
        "status NOT IN ('SENDING','UNCERTAIN','ACCEPTED','REJECTED') OR sent_at IS NOT NULL",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_live_order_submissions_resolution_timestamp",
        "live_order_submissions",
        "(status IN ('ACCEPTED','REJECTED','BLOCKED') AND resolved_at IS NOT NULL) OR "
        "(status NOT IN ('ACCEPTED','REJECTED','BLOCKED') AND resolved_at IS NULL)",
        schema="hmt_crypto",
    )


def downgrade() -> None:
    raise RuntimeError("Phase 13D is forward-fix only; restore from backup for rollback")
