"""Add canonical prepared Live order request evidence.

Revision ID: phase13c_live_order_wire_v1
Revises: phase13b_live_submission_events_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase13c_live_order_wire_v1"
down_revision: str | Sequence[str] | None = "phase13b_live_submission_events_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "live_order_submissions",
        sa.Column("prepared_request_payload", sa.Text(), nullable=True),
        schema="hmt_crypto",
    )
    op.add_column(
        "live_order_submissions",
        sa.Column("prepared_request_sha256", sa.String(64), nullable=True),
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_live_order_submissions_prepared_evidence_bundle",
        "live_order_submissions",
        "(prepared_request_payload IS NULL AND prepared_request_sha256 IS NULL) OR "
        "(prepared_request_payload IS NOT NULL AND prepared_request_sha256 IS NOT NULL)",
        schema="hmt_crypto",
    )
    op.create_check_constraint(
        "ck_live_order_submissions_prepared_evidence_required",
        "live_order_submissions",
        "status NOT IN ('PREPARED','SENDING','UNCERTAIN','ACCEPTED','REJECTED') OR "
        "prepared_request_sha256 IS NOT NULL",
        schema="hmt_crypto",
    )


def downgrade() -> None:
    raise RuntimeError("Phase 13C is forward-fix only; restore from backup for rollback")
