"""Grant least-privilege access to the dedicated application login.

Revision ID: phase5_runtime_grants_v1
Revises: phase5_reconcile_checkpoint_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase5_runtime_grants_v1"
down_revision: str | Sequence[str] | None = "phase5_reconcile_checkpoint_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "hmt_crypto_app"


def _execute(statement: str) -> None:
    op.execute(sa.text(statement))


def upgrade() -> None:
    """Grant data access only; retain owner, DDL, delete and function boundaries."""

    _execute(
        "DO $runtime_role$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles "
        f"WHERE rolname = '{RUNTIME_ROLE}') THEN "
        f"RAISE EXCEPTION 'required role {RUNTIME_ROLE} does not exist'; "
        "END IF; END $runtime_role$"
    )
    _execute(f"REVOKE CREATE ON SCHEMA hmt_crypto FROM {RUNTIME_ROLE}")
    _execute(f"GRANT USAGE ON SCHEMA hmt_crypto TO {RUNTIME_ROLE}")
    _execute(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA hmt_crypto TO {RUNTIME_ROLE}")
    _execute(f"REVOKE DELETE ON ALL TABLES IN SCHEMA hmt_crypto FROM {RUNTIME_ROLE}")
    _execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA hmt_crypto FROM {RUNTIME_ROLE}")
    _execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM {RUNTIME_ROLE}")
    _execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA hmt_crypto "
        f"GRANT SELECT, INSERT, UPDATE ON TABLES TO {RUNTIME_ROLE}"
    )
    _execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA hmt_crypto REVOKE ALL ON SEQUENCES FROM {RUNTIME_ROLE}"
    )
    _execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA hmt_crypto REVOKE ALL ON FUNCTIONS FROM {RUNTIME_ROLE}"
    )


def downgrade() -> None:
    """Remove every privilege and default grant added by this revision."""

    _execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA hmt_crypto "
        f"REVOKE SELECT, INSERT, UPDATE ON TABLES FROM {RUNTIME_ROLE}"
    )
    _execute(
        f"REVOKE SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA hmt_crypto FROM {RUNTIME_ROLE}"
    )
    _execute(f"REVOKE USAGE ON SCHEMA hmt_crypto FROM {RUNTIME_ROLE}")
