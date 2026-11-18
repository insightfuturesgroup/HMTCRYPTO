"""Merge production trading and temporary-password histories.

Revision ID: phase29_merge_auth_controls_v1
Revises: phase28_crypto_contract_settings_v1, phase24_temporary_password_v1
"""

from collections.abc import Sequence

revision: str = "phase29_merge_auth_controls_v1"
down_revision: str | Sequence[str] | None = (
    "phase28_crypto_contract_settings_v1",
    "phase24_temporary_password_v1",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the two existing schema histories."""


def downgrade() -> None:
    raise RuntimeError("Phase 29 is forward-fix only; restore from backup for rollback")
