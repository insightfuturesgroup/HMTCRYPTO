"""Least-privilege grants and append-only database enforcement.

Revision ID: phase2_hardening_v1
Revises: phase2_baseline_v1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase2_hardening_v1"
down_revision: str | None = "phase2_baseline_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES: tuple[str, ...] = (
    "api_audit_events",
    "balance_snapshots",
    "catalogue_products",
    "catalogue_snapshots",
    "credential_versions",
    "fill_order_links",
    "fills",
    "funding_snapshots",
    "order_attempt_events",
    "order_attempts",
    "order_transitions",
    "pnl_snapshots",
    "position_protection_orders",
    "position_snapshots",
    "protection_authorizations",
    "reconciliation_acknowledgements",
    "reconciliation_drifts",
    "recovery_incarnations",
    "risk_decisions",
    "risk_halt_events",
    "risk_halts",
    "risk_rate_consumptions",
    "signals",
    "wallet_transactions",
)
REJECT_MUTATION_FUNCTION = """CREATE FUNCTION hmt_crypto.reject_append_only_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $reject_mutation$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'append-only relation cannot be updated or deleted';
END
$reject_mutation$"""

PAYLOAD_MUTATION_GUARD = """CREATE FUNCTION hmt_crypto.guard_api_audit_payload_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $payload_guard$
BEGIN
    IF TG_OP = 'UPDATE' OR current_user <> 'hmt_audit_retention' THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'audit payload mutation is restricted to retention purge';
    END IF;
    RETURN OLD;
END
$payload_guard$"""

PURGE_PAYLOADS_FUNCTION = """CREATE FUNCTION hmt_crypto.purge_expired_audit_payload(
    p_owning_event_id uuid,
    p_payload_slot text
)
RETURNS TABLE (
    maintenance_root_id uuid,
    purge_event_id uuid,
    owning_event_id uuid,
    context jsonb,
    payload_slot text,
    payload_schema_version bigint,
    payload_sha256 text,
    payload_byte_length bigint,
    payload_expires_at timestamp with time zone,
    payload_ttl_seconds bigint,
    retention_policy_version bigint,
    actor_role_oid bigint,
    actor_role_name text,
    purged_at timestamp with time zone,
    audit_recorded_at timestamp with time zone
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $purge_payload$
DECLARE
    source_payload hmt_crypto.api_audit_payloads%ROWTYPE;
    role_oid bigint;
    sampled_at timestamp with time zone;
    root_id uuid;
    purge_id uuid;
    context_array text;
    context_object jsonb;
    source_identity text;
    recorded_text text;
    root_key text;
    purge_key text;
    root_evidence text;
    purge_evidence text;
BEGIN
    IF current_user <> 'hmt_audit_retention'
       OR p_owning_event_id IS NULL
       OR p_payload_slot NOT IN ('REQUEST', 'RESPONSE') THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'audit payload purge is not authorized';
    END IF;

    SELECT payload.* INTO source_payload
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

    SELECT role.oid::bigint INTO role_oid
    FROM pg_catalog.pg_roles AS role
    WHERE role.rolname = current_user;
    IF role_oid IS NULL OR role_oid < 1 THEN
        RAISE EXCEPTION USING
            ERRCODE = '42501',
            MESSAGE = 'audit payload purge role is invalid';
    END IF;

    root_id := gen_random_uuid();
    purge_id := gen_random_uuid();
    WHILE purge_id = root_id OR root_id = p_owning_event_id OR purge_id = p_owning_event_id LOOP
        root_id := gen_random_uuid();
        purge_id := gen_random_uuid();
    END LOOP;

    IF source_payload.context_kind = 'PUBLIC' THEN
        context_array := '[' || to_jsonb('PUBLIC'::text)::text || ','
            || to_jsonb(source_payload.region)::text || ','
            || to_jsonb(source_payload.environment)::text || ']';
        context_object := jsonb_build_object(
            'context_kind', 'PUBLIC',
            'region', source_payload.region,
            'environment', source_payload.environment
        );
    ELSE
        context_array := '[' || to_jsonb('ACCOUNT'::text)::text || ','
            || to_jsonb(source_payload.account_id::text)::text || ','
            || to_jsonb(source_payload.region)::text || ','
            || to_jsonb(source_payload.environment)::text || ','
            || to_jsonb(source_payload.mode)::text || ']';
        context_object := jsonb_build_object(
            'context_kind', 'ACCOUNT',
            'account_id', source_payload.account_id::text,
            'region', source_payload.region,
            'environment', source_payload.environment,
            'mode', source_payload.mode
        );
    END IF;

    source_identity := '{"source_event_id":' || to_jsonb(p_owning_event_id::text)::text
        || ',"source_payload_byte_length":' || source_payload.payload_byte_length::text
        || ',"source_payload_expires_at":'
        || to_jsonb(to_char(
            source_payload.payload_expires_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ))::text
        || ',"source_payload_schema_version":' || source_payload.payload_schema_version::text
        || ',"source_payload_sha256":' || to_jsonb(source_payload.payload_sha256)::text
        || ',"source_payload_slot":' || to_jsonb(source_payload.payload_slot)::text
        || ',"source_payload_ttl_seconds":' || source_payload.payload_ttl_seconds::text
        || ',"source_retention_policy_version":'
        || source_payload.retention_policy_version::text || '}';
    recorded_text := to_char(
        sampled_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
    );

    root_key := encode(sha256(convert_to(
        '{"domain":"HMT_API_AUDIT_KEY_V1","identity":[' || context_array || ','
        || to_jsonb(p_owning_event_id::text)::text || ','
        || to_jsonb(source_payload.payload_slot)::text || ','
        || source_payload.payload_schema_version::text || ','
        || to_jsonb(source_payload.payload_sha256)::text || ','
        || source_payload.payload_byte_length::text || ','
        || to_jsonb(to_char(
            source_payload.payload_expires_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ))::text || ',' || source_payload.payload_ttl_seconds::text || ','
        || source_payload.retention_policy_version::text
        || '],"stage":"MAINTENANCE_ROOT/v1"}', 'UTF8'
    )), 'hex');
    purge_key := encode(sha256(convert_to(
        '{"domain":"HMT_API_AUDIT_KEY_V1","identity":[' || context_array || ','
        || to_jsonb(p_owning_event_id::text)::text || ','
        || to_jsonb(source_payload.payload_slot)::text || ','
        || source_payload.payload_schema_version::text || ','
        || to_jsonb(source_payload.payload_sha256)::text || ','
        || source_payload.payload_byte_length::text || ','
        || to_jsonb(to_char(
            source_payload.payload_expires_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ))::text || ',' || source_payload.payload_ttl_seconds::text || ','
        || source_payload.retention_policy_version::text
        || '],"stage":"PAYLOAD_PURGE/v1"}', 'UTF8'
    )), 'hex');

    root_evidence := '{"domain":"HMT_API_AUDIT_EVENT_V1","producer":{"context":'
        || context_array || ',"event_kind":"MAINTENANCE_ROOT","facts":{},"identity":'
        || source_identity || '},"storage":{"actor_role_name":"hmt_audit_retention"'
        || ',"actor_role_oid":' || role_oid::text
        || ',"audit_recorded_at":' || to_jsonb(recorded_text)::text
        || ',"event_key":' || to_jsonb(root_key)::text
        || ',"id":' || to_jsonb(root_id::text)::text
        || ',"maintenance_root_id":' || to_jsonb(root_id::text)::text
        || ',"retention_mode":"SUMMARY_ONLY","sequence"\\:1}}';
    purge_evidence := '{"domain":"HMT_API_AUDIT_EVENT_V1","producer":{"context":'
        || context_array || ',"event_kind":"PAYLOAD_PURGED","facts":{},"identity":'
        || source_identity || '},"storage":{"actor_role_name":"hmt_audit_retention"'
        || ',"actor_role_oid":' || role_oid::text
        || ',"audit_recorded_at":' || to_jsonb(recorded_text)::text
        || ',"event_key":' || to_jsonb(purge_key)::text
        || ',"id":' || to_jsonb(purge_id::text)::text
        || ',"maintenance_root_id":' || to_jsonb(root_id::text)::text
        || ',"retention_mode":"SUMMARY_ONLY","sequence"\\:2}}';

    INSERT INTO hmt_crypto.api_audit_events (
        id, event_kind, context_kind, region, environment, sequence, event_key,
        audit_schema_version, audit_recorded_at, evidence_payload, evidence_sha256,
        account_id, mode, retention_mode, maintenance_root_id, maintenance_root_kind,
        source_event_id, source_payload_slot, source_payload_schema_version,
        source_payload_sha256, source_payload_byte_length, source_payload_expires_at,
        source_payload_ttl_seconds, source_retention_policy_version,
        actor_role_oid, actor_role_name
    ) VALUES (
        root_id, 'MAINTENANCE_ROOT', source_payload.context_kind,
        source_payload.region, source_payload.environment, 1, root_key,
        1, sampled_at, root_evidence,
        encode(sha256(convert_to(root_evidence, 'UTF8')), 'hex'),
        source_payload.account_id, source_payload.mode, 'SUMMARY_ONLY',
        root_id, 'MAINTENANCE_ROOT', p_owning_event_id, source_payload.payload_slot,
        source_payload.payload_schema_version, source_payload.payload_sha256,
        source_payload.payload_byte_length, source_payload.payload_expires_at,
        source_payload.payload_ttl_seconds, source_payload.retention_policy_version,
        role_oid, 'hmt_audit_retention'
    ), (
        purge_id, 'PAYLOAD_PURGED', source_payload.context_kind,
        source_payload.region, source_payload.environment, 2, purge_key,
        1, sampled_at, purge_evidence,
        encode(sha256(convert_to(purge_evidence, 'UTF8')), 'hex'),
        source_payload.account_id, source_payload.mode, 'SUMMARY_ONLY',
        root_id, 'MAINTENANCE_ROOT', p_owning_event_id, source_payload.payload_slot,
        source_payload.payload_schema_version, source_payload.payload_sha256,
        source_payload.payload_byte_length, source_payload.payload_expires_at,
        source_payload.payload_ttl_seconds, source_payload.retention_policy_version,
        role_oid, 'hmt_audit_retention'
    );

    DELETE FROM hmt_crypto.api_audit_payloads AS payload
    WHERE payload.owning_event_id = p_owning_event_id
      AND payload.payload_slot = p_payload_slot;

    RETURN QUERY SELECT
        root_id,
        purge_id,
        p_owning_event_id,
        context_object,
        source_payload.payload_slot::text,
        source_payload.payload_schema_version,
        source_payload.payload_sha256::text,
        source_payload.payload_byte_length,
        source_payload.payload_expires_at,
        source_payload.payload_ttl_seconds,
        source_payload.retention_policy_version,
        role_oid,
        'hmt_audit_retention'::text,
        sampled_at,
        sampled_at;
END
$purge_payload$"""

REVOKE_BROWSER_ACCESS = """DO $revoke_browser_roles$
DECLARE
    browser_role text;
BEGIN
    FOREACH browser_role IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = browser_role) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA hmt_crypto FROM %I', browser_role);
            EXECUTE format(
                'REVOKE ALL ON ALL TABLES IN SCHEMA hmt_crypto FROM %I', browser_role
            );
            EXECUTE format(
                'REVOKE ALL ON ALL SEQUENCES IN SCHEMA hmt_crypto FROM %I', browser_role
            );
            EXECUTE format(
                'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM %I', browser_role
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA hmt_crypto '
                'REVOKE ALL ON TABLES FROM %I', browser_role
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA hmt_crypto '
                'REVOKE ALL ON SEQUENCES FROM %I', browser_role
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA hmt_crypto '
                'REVOKE ALL ON FUNCTIONS FROM %I', browser_role
            );
            IF to_regclass('public.hmt_crypto_alembic_version') IS NOT NULL THEN
                EXECUTE format(
                    'REVOKE ALL ON TABLE public.hmt_crypto_alembic_version FROM %I',
                    browser_role
                );
            END IF;
        END IF;
    END LOOP;
END
$revoke_browser_roles$"""


def upgrade() -> None:
    """Remove browser/API access and enforce immutable evidence at the database."""

    public_revokes = (
        "REVOKE ALL ON SCHEMA hmt_crypto FROM PUBLIC",
        "REVOKE ALL ON ALL TABLES IN SCHEMA hmt_crypto FROM PUBLIC",
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA hmt_crypto FROM PUBLIC",
        "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA hmt_crypto FROM PUBLIC",
        "REVOKE ALL ON TABLE public.hmt_crypto_alembic_version FROM PUBLIC",
    )
    for statement in public_revokes:
        op.execute(sa.text(statement))
    op.execute(sa.text(REVOKE_BROWSER_ACCESS))
    for object_kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
        op.execute(
            sa.text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA hmt_crypto "
                f"REVOKE ALL ON {object_kind} FROM PUBLIC"
            )
        )
    op.execute(sa.text(REJECT_MUTATION_FUNCTION))
    op.execute(
        sa.text("REVOKE ALL ON FUNCTION hmt_crypto.reject_append_only_mutation() FROM PUBLIC")
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            sa.text(
                f"CREATE TRIGGER hmt_crypto_append_only BEFORE UPDATE OR DELETE "
                f"ON hmt_crypto.{table} FOR EACH ROW "
                "EXECUTE FUNCTION hmt_crypto.reject_append_only_mutation()"
            )
        )
    op.execute(sa.text(PAYLOAD_MUTATION_GUARD))
    op.execute(
        sa.text("REVOKE ALL ON FUNCTION hmt_crypto.guard_api_audit_payload_mutation() FROM PUBLIC")
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER hmt_crypto_payload_mutation_guard BEFORE UPDATE OR DELETE "
            "ON hmt_crypto.api_audit_payloads FOR EACH ROW "
            "EXECUTE FUNCTION hmt_crypto.guard_api_audit_payload_mutation()"
        )
    )
    op.execute(sa.text(PURGE_PAYLOADS_FUNCTION))
    op.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION hmt_crypto.purge_expired_audit_payload(uuid, text) FROM PUBLIC"
        )
    )


def downgrade() -> None:
    """Refuse an unsafe partial reversal of database security boundaries."""

    message = "The Phase 2 hardening revision is irreversible; restore or forward-fix instead"
    raise RuntimeError(message)
