"""Make every application foreign-key action explicit and restrictive.

Revision ID: phase32_restrict_foreign_keys_v1
Revises: phase31_dynamic_tradingview_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase32_restrict_foreign_keys_v1"
down_revision: str | Sequence[str] | None = "phase31_dynamic_tradingview_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "hmt_crypto"

# These five constraints were intentionally CASCADE before this corrective revision.
# Downgrade restores that prior behavior; every other application FK was RESTRICT on
# delete and implicit NO ACTION on update.
PRIOR_DELETE_CASCADE = (
    "crypto_contract_settings_account_id_fkey",
    "fk_tv_webhook_keys_configuration",
    "tradingview_configuration_accounts_account_id_fkey",
    "tradingview_configuration_accounts_configuration_id_fkey",
    "tradingview_symbol_mappings_account_id_fkey",
)


def _rewrite_foreign_keys(*, update_action: str, restore_delete_actions: bool) -> None:
    cascade_names = ", ".join(f"'{name}'" for name in PRIOR_DELETE_CASCADE)
    delete_action = (
        f"CASE WHEN candidate.constraint_name IN ({cascade_names}) "
        "THEN 'CASCADE' ELSE 'RESTRICT' END"
        if restore_delete_actions
        else "'RESTRICT'"
    )
    op.execute(
        sa.text(
            f"""
DO $migration$
DECLARE
    candidate record;
    definition text;
BEGIN
    FOR candidate IN
        SELECT
            c.oid,
            c.conname AS constraint_name,
            n.nspname AS schema_name,
            t.relname AS table_name
        FROM pg_constraint AS c
        JOIN pg_class AS t ON t.oid = c.conrelid
        JOIN pg_namespace AS n ON n.oid = t.relnamespace
        WHERE n.nspname = '{SCHEMA}'
          AND c.contype = 'f'
        ORDER BY t.relname, c.conname
    LOOP
        definition := pg_get_constraintdef(candidate.oid, true);
        definition := regexp_replace(
            definition,
            '\\s+ON\\s+UPDATE\\s+(NO ACTION|RESTRICT|CASCADE|SET NULL|SET DEFAULT)',
            '',
            'gi'
        );
        definition := regexp_replace(
            definition,
            '\\s+ON\\s+DELETE\\s+(NO ACTION|RESTRICT|CASCADE|SET NULL|SET DEFAULT)',
            '',
            'gi'
        );
        definition := definition || format(
            ' ON UPDATE {update_action} ON DELETE %s',
            {delete_action}
        );

        EXECUTE format(
            'ALTER TABLE %I.%I DROP CONSTRAINT %I',
            candidate.schema_name,
            candidate.table_name,
            candidate.constraint_name
        );
        EXECUTE format(
            'ALTER TABLE %I.%I ADD CONSTRAINT %I %s NOT VALID',
            candidate.schema_name,
            candidate.table_name,
            candidate.constraint_name,
            definition
        );
        EXECUTE format(
            'ALTER TABLE %I.%I VALIDATE CONSTRAINT %I',
            candidate.schema_name,
            candidate.table_name,
            candidate.constraint_name
        );
    END LOOP;
END
$migration$;
"""
        )
    )


def upgrade() -> None:
    _rewrite_foreign_keys(update_action="RESTRICT", restore_delete_actions=False)


def downgrade() -> None:
    _rewrite_foreign_keys(update_action="NO ACTION", restore_delete_actions=True)
