"""Ensure first-login password state exists after migration-history merge.

Revision ID: phase30_ensure_password_state_v1
Revises: phase29_merge_auth_controls_v1
"""

from collections.abc import Sequence

from alembic import op

revision: str = "phase30_ensure_password_state_v1"
down_revision: str | Sequence[str] | None = "phase29_merge_auth_controls_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $migration$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'hmt_crypto'
                  AND table_name = 'platform_users'
                  AND column_name = 'password_change_required'
            ) THEN
                ALTER TABLE hmt_crypto.platform_users
                    ADD COLUMN password_change_required boolean
                    DEFAULT false NOT NULL;
            END IF;
        END
        $migration$;
        """
    )


def downgrade() -> None:
    raise RuntimeError("Phase 30 is forward-fix only; restore from backup for rollback")
