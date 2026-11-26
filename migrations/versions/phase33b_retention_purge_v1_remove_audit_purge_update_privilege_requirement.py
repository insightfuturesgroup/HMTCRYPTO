"""Remove the audit-purge UPDATE privilege requirement.

Revision ID: phase33b_retention_purge_v1
Revises: phase33_private_function_acl_v1
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import op

revision: str = "phase33b_retention_purge_v1"
down_revision: str | Sequence[str] | None = "phase33_private_function_acl_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_READ = """    SELECT payload.* INTO source_payload
    FROM hmt_crypto.api_audit_payloads AS payload
    WHERE payload.owning_event_id = p_owning_event_id
      AND payload.payload_slot = p_payload_slot
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0002',
            MESSAGE = 'eligible audit payload was not found';
    END IF;

    sampled_at := clock_timestamp();
    IF source_payload.payload_expires_at > sampled_at THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'audit payload retention period has not expired';
    END IF;
"""

_ATOMIC_DELETE = """    DELETE FROM hmt_crypto.api_audit_payloads AS payload
    WHERE payload.owning_event_id = p_owning_event_id
      AND payload.payload_slot = p_payload_slot
      AND payload.payload_expires_at <= clock_timestamp()
    RETURNING payload.* INTO source_payload;
    IF NOT FOUND THEN
        IF EXISTS (
            SELECT 1 FROM hmt_crypto.api_audit_payloads AS payload
            WHERE payload.owning_event_id = p_owning_event_id
              AND payload.payload_slot = p_payload_slot
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'audit payload retention period has not expired';
        END IF;
        RAISE EXCEPTION USING
            ERRCODE = 'P0002',
            MESSAGE = 'eligible audit payload was not found';
    END IF;

    sampled_at := clock_timestamp();
"""

_OLD_DELETE = """    DELETE FROM hmt_crypto.api_audit_payloads AS payload
    WHERE payload.owning_event_id = p_owning_event_id
      AND payload.payload_slot = p_payload_slot;

"""

_DEFINER_PAYLOAD_GUARD = """CREATE OR REPLACE FUNCTION hmt_crypto.guard_api_audit_payload_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $payload_guard$
DECLARE purge_owner name;
BEGIN
    SELECT pg_get_userbyid(proowner) INTO purge_owner
    FROM pg_proc
    WHERE oid = 'hmt_crypto.purge_expired_audit_payload(uuid,text)'::regprocedure;
    IF purge_owner IS NULL
       OR TG_OP = 'UPDATE'
       OR current_user NOT IN ('hmt_audit_retention', purge_owner) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'audit payload mutation is restricted to retention purge';
    END IF;
    RETURN OLD;
END
$payload_guard$"""


def _phase2_hardening() -> ModuleType:
    path = Path(__file__).with_name("phase2_hardening_v1.py")
    specification = importlib.util.spec_from_file_location("phase2_hardening_for_phase33b", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("Frozen purge definition is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _definition(*, atomic: bool) -> str:
    source = _phase2_hardening().PURGE_PAYLOADS_FUNCTION
    if source.count(_OLD_READ) != 1 or source.count(_OLD_DELETE) != 1:
        raise RuntimeError("Frozen purge definition changed unexpectedly")
    if atomic:
        source = source.replace(_OLD_READ, _ATOMIC_DELETE).replace(_OLD_DELETE, "")
        source = source.replace("SECURITY INVOKER", "SECURITY DEFINER", 1)
        source = source.replace(
            "IF current_user <> 'hmt_audit_retention'\n       OR p_owning_event_id IS NULL",
            "IF p_owning_event_id IS NULL",
            1,
        )
        source = source.replace(
            "WHERE role.rolname = current_user", "WHERE role.rolname = 'hmt_audit_retention'", 1
        )
    return source.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def upgrade() -> None:
    """Run the atomic purge as its fixed-path owner; callers need EXECUTE only."""

    op.execute(sa.text(_definition(atomic=True)))
    op.execute(sa.text(_DEFINER_PAYLOAD_GUARD))
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION hmt_crypto.purge_expired_audit_payload(uuid, text) FROM PUBLIC"
        )
    )


def downgrade() -> None:
    """Restore the phase2 implementation and its SELECT FOR UPDATE requirement."""

    previous = _phase2_hardening()
    op.execute(sa.text(_definition(atomic=False)))
    op.execute(
        sa.text(
            previous.PAYLOAD_MUTATION_GUARD.replace(
                "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
            )
        )
    )
