"""Widen Alembic's revision column before longer revision identifiers.

Revision ID: phase8_version_width_v1
Revises: phase8_private_reconciliation_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase8_version_width_v1"
down_revision: str | Sequence[str] | None = "phase8_private_reconciliation_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "hmt_crypto_alembic_version",
        "version_num",
        schema="public",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    raise RuntimeError("Phase 8 version-width migration is forward-fix only")
