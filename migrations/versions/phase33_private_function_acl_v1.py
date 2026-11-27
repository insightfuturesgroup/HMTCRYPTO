"""Remove browser EXECUTE from private functions and future owner-created routines.

Revision ID: phase33_private_function_acl_v1
Revises: phase32_restrict_foreign_keys_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase33_private_function_acl_v1"
down_revision: str | Sequence[str] | None = "phase32_restrict_foreign_keys_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM PUBLIC"))
    op.execute(
        sa.text(
            """DO $private_functions$
DECLARE
    creator text;
    browser text;
BEGIN
    FOREACH browser IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = browser) THEN
            EXECUTE format(
                'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM %I', browser
            );
        END IF;
    END LOOP;
    FOR creator IN
        SELECT current_user::text
        UNION
        SELECT pg_get_userbyid(p.proowner)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'hmt_crypto'
    LOOP
        -- A schema-local revoke cannot subtract the built-in global PUBLIC grant.
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I '
            'REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC', creator);
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I '
            'IN SCHEMA hmt_crypto REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC', creator);
        FOREACH browser IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = browser) THEN
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I '
                    'REVOKE EXECUTE ON FUNCTIONS FROM %I', creator, browser);
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I '
                    'IN SCHEMA hmt_crypto REVOKE EXECUTE ON FUNCTIONS FROM %I',
                    creator, browser);
            END IF;
        END LOOP;
    END LOOP;
END
$private_functions$;"""
        )
    )


def downgrade() -> None:
    """Restore repository-defined phase32 behavior, not custom out-of-band ACLs.

    This deliberately reopens the old PUBLIC grant. Roll back the app without
    downgrading this security revision unless that reopening is explicitly reviewed.
    """
    op.execute(sa.text("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM PUBLIC"))
    op.execute(
        sa.text(
            """DO $restore_private_function_acl$
DECLARE browser text;
BEGIN
    FOREACH browser IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT FROM pg_roles WHERE rolname = browser) THEN
            EXECUTE format(
                'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM %I', browser
            );
        END IF;
    END LOOP;
END
$restore_private_function_acl$;"""
        )
    )
    op.execute(
        sa.text(
            "GRANT EXECUTE ON FUNCTION "
            "hmt_crypto.guard_account_reconciliation_checkpoints_v1() TO PUBLIC"
        )
    )
    op.execute(
        sa.text(
            """DO $restore_function_defaults$
DECLARE creator text;
BEGIN
    FOR creator IN
        SELECT current_user::text
        UNION
        SELECT pg_get_userbyid(p.proowner)
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'hmt_crypto'
    LOOP
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I '
            'GRANT EXECUTE ON FUNCTIONS TO PUBLIC', creator);
    END LOOP;
END
$restore_function_defaults$;"""
        )
    )
