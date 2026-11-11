"""Permit exactly one immutable credential-to-Delta-user binding.

Revision ID: phase21_credential_identity_binding_v1
Revises: phase20_account_archive_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase21_credential_identity_binding_v1"
down_revision: str | Sequence[str] | None = "phase20_account_archive_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GUARD_FUNCTION = """
CREATE FUNCTION hmt_crypto.guard_credential_identity_binding()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $credential_binding$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.observed_external_user_id IS NULL
       AND NEW.observed_external_user_id IS NOT NULL
       AND ROW(
           NEW.id,
           NEW.account_id,
           NEW.region,
           NEW.environment,
           NEW.mode,
           NEW.credential_version,
           NEW.credential_slot,
           NEW.fingerprint_hint,
           NEW.observed_external_account_id,
           NEW.binding_ref,
           NEW.probe_ref,
           NEW.reconciliation_ref,
           NEW.recorded_at
       ) IS NOT DISTINCT FROM ROW(
           OLD.id,
           OLD.account_id,
           OLD.region,
           OLD.environment,
           OLD.mode,
           OLD.credential_version,
           OLD.credential_slot,
           OLD.fingerprint_hint,
           OLD.observed_external_account_id,
           OLD.binding_ref,
           OLD.probe_ref,
           OLD.reconciliation_ref,
           OLD.recorded_at
       )
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'credential evidence is immutable after its one-time identity binding';
END
$credential_binding$
"""


def upgrade() -> None:
    """Replace the contradictory blanket guard with a one-time binding guard."""

    op.execute(sa.text("DROP TRIGGER hmt_crypto_append_only ON hmt_crypto.credential_versions"))
    op.execute(sa.text(_GUARD_FUNCTION))
    op.execute(
        sa.text("REVOKE ALL ON FUNCTION hmt_crypto.guard_credential_identity_binding() FROM PUBLIC")
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER hmt_crypto_credential_identity_binding_guard "
            "BEFORE UPDATE OR DELETE ON hmt_crypto.credential_versions "
            "FOR EACH ROW EXECUTE FUNCTION hmt_crypto.guard_credential_identity_binding()"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 21 is forward-fix only; restore from backup for rollback")
