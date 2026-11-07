"""Admit one durable, single-active global kill command per account scope.

Revision ID: phase5_control_v1
Revises: phase2_hardening_v1
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase5_control_v1"
down_revision: str | None = "phase2_hardening_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXECUTION_OPERATIONS = (
    "PLACE_ORDER",
    "MODIFY_ORDER",
    "CANCEL_ORDER",
    "SET_LEVERAGE",
    "SET_MARGIN_MODE",
    "CHANGE_POSITION_MARGIN",
)
_COMMAND_OPERATIONS = (*_EXECUTION_OPERATIONS, "GLOBAL_KILL")
_ACTIVE_KILL = "operation_kind = 'GLOBAL_KILL' AND status IN ('QUEUED','CLAIMED')"


def _enum_sql(values: tuple[str, ...]) -> str:
    return "operation_kind IN (" + ",".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    """Widen command intent and add append-only operator control evidence."""
    op.drop_constraint(
        "ck_command_operations_operation_kind_enum",
        "command_operations",
        schema="hmt_crypto",
        type_="check",
    )
    op.create_check_constraint(
        "ck_command_operations_operation_kind_enum",
        "command_operations",
        _enum_sql(_COMMAND_OPERATIONS),
        schema="hmt_crypto",
    )
    op.create_index(
        "uq_command_operations_one_active_global_kill",
        "command_operations",
        ["account_id", "region", "environment", "mode"],
        unique=True,
        schema="hmt_crypto",
        postgresql_where=sa.text(_ACTIVE_KILL),
    )
    op.create_table(
        "kill_workflows",
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("kill_mode", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("transition_count", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.CheckConstraint("region IN ('india', 'global')", name="ck_kill_workflows_region"),
        sa.CheckConstraint(
            "environment IN ('testnet', 'prod')",
            name="ck_kill_workflows_environment",
        ),
        sa.CheckConstraint("mode IN ('BACKTEST', 'PAPER', 'LIVE')", name="ck_kill_workflows_mode"),
        sa.CheckConstraint(
            "kill_mode IN ('CANCEL_ONLY','CANCEL_AND_FLATTEN')",
            name="ck_kill_workflows_kill_mode_enum",
        ),
        sa.CheckConstraint(
            "state IN ('REQUESTED','CANCELLING_RISK_INCREASING','FLATTENING',"
            "'CANCELLING_REMAINDER','RECONCILING','PROTECTION_REESTABLISHING',"
            "'PROTECTION_VERIFYING','FINAL_RECONCILING','COMPLETED','INCOMPLETE')",
            name="ck_kill_workflows_state_enum",
        ),
        sa.CheckConstraint("id = command_id", name="ck_kill_workflows_identity_matches_command"),
        sa.CheckConstraint(
            "transition_count >= 0 AND version = transition_count + 1",
            name="ck_kill_workflows_version_tracks_transitions",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_kill_workflows_time_order"),
        sa.CheckConstraint(
            "(state IN ('COMPLETED','INCOMPLETE') AND completed_at IS NOT NULL "
            "AND completed_at >= created_at AND completed_at <= updated_at) OR "
            "(state NOT IN ('COMPLETED','INCOMPLETE') AND completed_at IS NULL)",
            name="ck_kill_workflows_terminal_time",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "region", "environment", "mode"],
            [
                "hmt_crypto.accounts.id",
                "hmt_crypto.accounts.region",
                "hmt_crypto.accounts.environment",
                "hmt_crypto.accounts.mode",
            ],
            name="fk_kill_workflows_account_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["command_id", "account_id", "region", "environment", "mode"],
            [
                "hmt_crypto.command_operations.id",
                "hmt_crypto.command_operations.account_id",
                "hmt_crypto.command_operations.region",
                "hmt_crypto.command_operations.environment",
                "hmt_crypto.command_operations.mode",
            ],
            name="fk_kill_workflows_command_id_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kill_workflows"),
        sa.UniqueConstraint(
            "id",
            "account_id",
            "region",
            "environment",
            "mode",
            name="uq_kill_workflows_id_scope",
        ),
        sa.UniqueConstraint(
            "account_id",
            "region",
            "environment",
            "mode",
            "command_id",
            name="uq_kill_workflows_scope_command",
        ),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_kill_workflows_scope",
        "kill_workflows",
        ["account_id", "region", "environment", "mode"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_kill_workflows_command",
        "kill_workflows",
        ["command_id", "account_id", "region", "environment", "mode"],
        schema="hmt_crypto",
    )
    op.create_table(
        "kill_workflow_events",
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_kind", sa.String(length=48), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.CheckConstraint("region IN ('india', 'global')", name="ck_kill_workflow_events_region"),
        sa.CheckConstraint(
            "environment IN ('testnet', 'prod')",
            name="ck_kill_workflow_events_environment",
        ),
        sa.CheckConstraint(
            "mode IN ('BACKTEST', 'PAPER', 'LIVE')",
            name="ck_kill_workflow_events_mode",
        ),
        sa.CheckConstraint(
            "event_kind IN ('RISK_HALT_PERSISTED','RISK_INCREASING_CANCEL_COMPLETED',"
            "'RISK_INCREASING_CANCEL_UNRESOLVED','FLATTEN_COMPLETED','FLATTEN_UNRESOLVED',"
            "'REMAINDER_CANCEL_COMPLETED','REMAINDER_CANCEL_UNRESOLVED','RECONCILED_FLAT',"
            "'RECONCILED_RETAINED','RECONCILIATION_UNRESOLVED','PROTECTION_SUBMITTED',"
            "'PROTECTION_UNRESOLVED','PROTECTION_VERIFIED','PROTECTION_UNVERIFIED',"
            "'FINAL_RECONCILED_SAFE','FINAL_RECONCILIATION_UNRESOLVED')",
            name="ck_kill_workflow_events_event_kind_enum",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_kill_workflow_events_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["account_id", "region", "environment", "mode"],
            [
                "hmt_crypto.accounts.id",
                "hmt_crypto.accounts.region",
                "hmt_crypto.accounts.environment",
                "hmt_crypto.accounts.mode",
            ],
            name="fk_kill_workflow_events_account_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "account_id", "region", "environment", "mode"],
            [
                "hmt_crypto.kill_workflows.id",
                "hmt_crypto.kill_workflows.account_id",
                "hmt_crypto.kill_workflows.region",
                "hmt_crypto.kill_workflows.environment",
                "hmt_crypto.kill_workflows.mode",
            ],
            name="fk_kill_workflow_events_workflow_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kill_workflow_events"),
        sa.UniqueConstraint(
            "id",
            "account_id",
            "region",
            "environment",
            "mode",
            name="uq_kill_workflow_events_id_scope",
        ),
        sa.UniqueConstraint(
            "workflow_id",
            "account_id",
            "region",
            "environment",
            "mode",
            "sequence",
            name="uq_kill_workflow_events_sequence",
        ),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_kill_workflow_events_scope",
        "kill_workflow_events",
        ["account_id", "region", "environment", "mode"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_kill_workflow_events_workflow",
        "kill_workflow_events",
        ["workflow_id", "account_id", "region", "environment", "mode", "sequence"],
        schema="hmt_crypto",
    )
    op.create_table(
        "paper_execution_heads",
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("intent_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("claim_owner", sa.Uuid(), nullable=True),
        sa.Column("claim_generation", sa.BigInteger(), nullable=False),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.CheckConstraint("region IN ('india', 'global')", name="ck_paper_execution_heads_region"),
        sa.CheckConstraint(
            "environment IN ('testnet', 'prod')",
            name="ck_paper_execution_heads_environment",
        ),
        sa.CheckConstraint(
            "mode IN ('BACKTEST', 'PAPER', 'LIVE')",
            name="ck_paper_execution_heads_mode",
        ),
        sa.CheckConstraint(
            "mode IN ('BACKTEST','PAPER')",
            name="ck_paper_execution_heads_paper_mode_only",
        ),
        sa.CheckConstraint(
            "status IN ('INTENT_RECORDED','DENIED','PREPARED','SIMULATED','COMPLETE','INCOMPLETE')",
            name="ck_paper_execution_heads_status_enum",
        ),
        sa.CheckConstraint(
            "length(request_id) BETWEEN 1 AND 128 AND length(trim(request_id)) > 0",
            name="ck_paper_execution_heads_request_id_bounded",
        ),
        sa.CheckConstraint(
            "intent_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_paper_execution_heads_intent_sha256_sha256",
        ),
        sa.CheckConstraint(
            "version >= 1 AND last_sequence >= 1 AND claim_generation >= 0",
            name="ck_paper_execution_heads_positive_counters",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_paper_execution_heads_time_order"),
        sa.CheckConstraint(
            "(claim_owner IS NULL AND claim_expires_at IS NULL) OR "
            "(claim_owner IS NOT NULL AND claim_generation >= 1 "
            "AND claim_expires_at IS NOT NULL AND claim_expires_at > updated_at)",
            name="ck_paper_execution_heads_claim_consistency",
        ),
        sa.CheckConstraint(
            "(status IN ('DENIED','COMPLETE','INCOMPLETE') AND completed_at IS NOT NULL "
            "AND completed_at >= created_at AND completed_at <= updated_at "
            "AND claim_owner IS NULL) OR "
            "(status NOT IN ('DENIED','COMPLETE','INCOMPLETE') AND completed_at IS NULL)",
            name="ck_paper_execution_heads_terminal_consistency",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "region", "environment", "mode"],
            [
                "hmt_crypto.accounts.id",
                "hmt_crypto.accounts.region",
                "hmt_crypto.accounts.environment",
                "hmt_crypto.accounts.mode",
            ],
            name="fk_paper_execution_heads_account_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_execution_heads"),
        sa.UniqueConstraint(
            "id",
            "account_id",
            "region",
            "environment",
            "mode",
            name="uq_paper_execution_heads_id_scope",
        ),
        sa.UniqueConstraint(
            "account_id",
            "region",
            "environment",
            "mode",
            "request_id",
            name="uq_paper_heads_scope_request",
        ),
        sa.UniqueConstraint(
            "id",
            "account_id",
            "region",
            "environment",
            "mode",
            "request_id",
            name="uq_paper_heads_event_parent",
        ),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_paper_execution_heads_scope",
        "paper_execution_heads",
        ["account_id", "region", "environment", "mode"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_paper_heads_claim",
        "paper_execution_heads",
        ["account_id", "region", "environment", "mode", "status", "claim_expires_at"],
        schema="hmt_crypto",
    )
    op.create_table(
        "paper_execution_events",
        sa.Column("head_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("scope_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column("event_kind", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_schema_version", sa.BigInteger(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "region IN ('india', 'global')", name="ck_paper_execution_events_region"
        ),
        sa.CheckConstraint(
            "environment IN ('testnet', 'prod')",
            name="ck_paper_execution_events_environment",
        ),
        sa.CheckConstraint(
            "mode IN ('BACKTEST', 'PAPER', 'LIVE')",
            name="ck_paper_execution_events_mode",
        ),
        sa.CheckConstraint(
            "mode IN ('BACKTEST','PAPER')",
            name="ck_paper_execution_events_paper_mode_only",
        ),
        sa.CheckConstraint(
            "event_kind IN ('INTENT','SIGNAL','RISK_DENIED','RISK_APPROVED','RESERVATION',"
            "'COMMAND','ATTEMPT','SIMULATION','FILL','POSITION','PNL','FUNDING','RISK_HALT',"
            "'ORDER_CANCELLED','FLATTEN_FILL','RECONCILIATION','PROTECTION','KILL_ACTION',"
            "'RUN_INCOMPLETE','RUN_COMPLETE')",
            name="ck_paper_execution_events_event_kind_enum",
        ),
        sa.CheckConstraint(
            "length(request_id) BETWEEN 1 AND 128 AND length(trim(request_id)) > 0",
            name="ck_paper_execution_events_request_id_bounded",
        ),
        sa.CheckConstraint(
            "event_key ~ '^[0-9a-f]{64}$'",
            name="ck_paper_execution_events_event_key_sha256",
        ),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_paper_execution_events_payload_sha256_sha256",
        ),
        sa.CheckConstraint(
            "sequence >= 1 AND scope_sequence >= 1",
            name="ck_paper_execution_events_sequence_positive",
        ),
        sa.CheckConstraint(
            "payload_schema_version = 1",
            name="ck_paper_execution_events_payload_schema_version",
        ),
        sa.CheckConstraint(
            "occurred_at <= recorded_at", name="ck_paper_execution_events_time_order"
        ),
        sa.CheckConstraint(
            "length(payload) BETWEEN 2 AND 65536",
            name="ck_paper_execution_events_payload_size",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "region", "environment", "mode"],
            [
                "hmt_crypto.accounts.id",
                "hmt_crypto.accounts.region",
                "hmt_crypto.accounts.environment",
                "hmt_crypto.accounts.mode",
            ],
            name="fk_paper_execution_events_account_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["head_id", "account_id", "region", "environment", "mode", "request_id"],
            [
                "hmt_crypto.paper_execution_heads.id",
                "hmt_crypto.paper_execution_heads.account_id",
                "hmt_crypto.paper_execution_heads.region",
                "hmt_crypto.paper_execution_heads.environment",
                "hmt_crypto.paper_execution_heads.mode",
                "hmt_crypto.paper_execution_heads.request_id",
            ],
            name="fk_paper_events_head_scope_request",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_paper_execution_events"),
        sa.UniqueConstraint(
            "id",
            "account_id",
            "region",
            "environment",
            "mode",
            name="uq_paper_execution_events_id_scope",
        ),
        sa.UniqueConstraint(
            "head_id",
            "account_id",
            "region",
            "environment",
            "mode",
            "sequence",
            name="uq_paper_events_head_sequence",
        ),
        sa.UniqueConstraint(
            "account_id",
            "region",
            "environment",
            "mode",
            "scope_sequence",
            name="uq_paper_events_scope_sequence",
        ),
        sa.UniqueConstraint(
            "account_id",
            "region",
            "environment",
            "mode",
            "event_key",
            name="uq_paper_events_scope_key",
        ),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_paper_execution_events_scope",
        "paper_execution_events",
        ["account_id", "region", "environment", "mode"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_paper_events_replay",
        "paper_execution_events",
        ["head_id", "account_id", "region", "environment", "mode", "sequence"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_paper_events_scope_replay",
        "paper_execution_events",
        ["account_id", "region", "environment", "mode", "scope_sequence"],
        schema="hmt_crypto",
    )
    op.create_table(
        "operator_audit_events",
        sa.Column("event_kind", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("action_variant", sa.String(length=32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("environment", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "event_kind IN ('LOGIN','LOGOUT','DASHBOARD_READ','KILL_PREVIEW',"
            "'KILL_ENQUEUE','COMMAND_STATUS','ACTIVE_KILL_STATUS')",
            name="ck_operator_audit_events_event_kind_enum",
        ),
        sa.CheckConstraint(
            "outcome IN ('SUCCEEDED','REJECTED','RATE_LIMITED','NOT_FOUND','CONFLICT')",
            name="ck_operator_audit_events_outcome_enum",
        ),
        sa.CheckConstraint(
            "action_variant IN ('CANCEL_ONLY','CANCEL_AND_FLATTEN')",
            name="ck_operator_audit_events_action_variant_enum",
        ),
        sa.CheckConstraint("region IN ('india', 'global')", name="ck_operator_audit_events_region"),
        sa.CheckConstraint(
            "environment IN ('testnet', 'prod')",
            name="ck_operator_audit_events_environment",
        ),
        sa.CheckConstraint(
            "mode IN ('BACKTEST', 'PAPER', 'LIVE')", name="ck_operator_audit_events_mode"
        ),
        sa.CheckConstraint(
            "recorded_at >= occurred_at", name="ck_operator_audit_events_time_order"
        ),
        sa.CheckConstraint(
            "(event_kind = 'LOGIN') OR session_id IS NOT NULL",
            name="ck_operator_audit_events_session_required_after_login",
        ),
        sa.CheckConstraint(
            "action_variant IS NULL OR event_kind IN ('KILL_PREVIEW','KILL_ENQUEUE')",
            name="ck_operator_audit_events_action_variant_applicability",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "region", "environment", "mode"],
            [
                "hmt_crypto.accounts.id",
                "hmt_crypto.accounts.region",
                "hmt_crypto.accounts.environment",
                "hmt_crypto.accounts.mode",
            ],
            name="fk_operator_audit_events_account_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_operator_audit_events"),
        sa.UniqueConstraint(
            "id",
            "account_id",
            "region",
            "environment",
            "mode",
            name="uq_operator_audit_events_id_scope",
        ),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_operator_audit_events_scope",
        "operator_audit_events",
        ["account_id", "region", "environment", "mode"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_operator_audit_events_time",
        "operator_audit_events",
        ["account_id", "recorded_at", "id"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER hmt_crypto_append_only BEFORE UPDATE OR DELETE "
            "ON hmt_crypto.operator_audit_events FOR EACH ROW "
            "EXECUTE FUNCTION hmt_crypto.reject_append_only_mutation()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER hmt_crypto_append_only BEFORE UPDATE OR DELETE "
            "ON hmt_crypto.kill_workflow_events FOR EACH ROW "
            "EXECUTE FUNCTION hmt_crypto.reject_append_only_mutation()"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER hmt_crypto_append_only BEFORE UPDATE OR DELETE "
            "ON hmt_crypto.paper_execution_events FOR EACH ROW "
            "EXECUTE FUNCTION hmt_crypto.reject_append_only_mutation()"
        )
    )
    op.execute(sa.text("REVOKE ALL ON TABLE hmt_crypto.operator_audit_events FROM PUBLIC"))
    op.execute(sa.text("REVOKE ALL ON TABLE hmt_crypto.kill_workflows FROM PUBLIC"))
    op.execute(sa.text("REVOKE ALL ON TABLE hmt_crypto.kill_workflow_events FROM PUBLIC"))
    op.execute(sa.text("REVOKE ALL ON TABLE hmt_crypto.paper_execution_heads FROM PUBLIC"))
    op.execute(sa.text("REVOKE ALL ON TABLE hmt_crypto.paper_execution_events FROM PUBLIC"))


def downgrade() -> None:
    """Refuse to erase the vocabulary while any global-kill evidence remains."""
    op.execute(
        sa.text(
            """DO $hmt_control_downgrade$
BEGIN
    IF EXISTS (SELECT 1 FROM hmt_crypto.paper_execution_events)
       OR EXISTS (SELECT 1 FROM hmt_crypto.paper_execution_heads) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'paper execution journal prevents downgrade';
    END IF;
    IF EXISTS (SELECT 1 FROM hmt_crypto.kill_workflow_events)
       OR EXISTS (SELECT 1 FROM hmt_crypto.kill_workflows) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'global kill workflow history prevents downgrade';
    END IF;
    IF EXISTS (
        SELECT 1 FROM hmt_crypto.command_operations
        WHERE operation_kind = 'GLOBAL_KILL'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'global kill command history prevents downgrade';
    END IF;
    IF EXISTS (SELECT 1 FROM hmt_crypto.operator_audit_events) THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = 'operator audit history prevents downgrade';
    END IF;
END
$hmt_control_downgrade$"""
        )
    )
    op.drop_index(
        "uq_command_operations_one_active_global_kill",
        table_name="command_operations",
        schema="hmt_crypto",
    )
    op.drop_index(
        "ix_kill_workflow_events_workflow",
        table_name="kill_workflow_events",
        schema="hmt_crypto",
    )
    op.drop_index(
        "ix_kill_workflow_events_scope",
        table_name="kill_workflow_events",
        schema="hmt_crypto",
    )
    op.drop_table("kill_workflow_events", schema="hmt_crypto")
    op.drop_index(
        "ix_kill_workflows_command",
        table_name="kill_workflows",
        schema="hmt_crypto",
    )
    op.drop_index(
        "ix_kill_workflows_scope",
        table_name="kill_workflows",
        schema="hmt_crypto",
    )
    op.drop_table("kill_workflows", schema="hmt_crypto")
    op.drop_index(
        "ix_paper_events_scope_replay",
        table_name="paper_execution_events",
        schema="hmt_crypto",
    )
    op.drop_index(
        "ix_paper_events_replay",
        table_name="paper_execution_events",
        schema="hmt_crypto",
    )
    op.drop_index(
        "ix_paper_execution_events_scope",
        table_name="paper_execution_events",
        schema="hmt_crypto",
    )
    op.drop_table("paper_execution_events", schema="hmt_crypto")
    op.drop_index(
        "ix_paper_heads_claim",
        table_name="paper_execution_heads",
        schema="hmt_crypto",
    )
    op.drop_index(
        "ix_paper_execution_heads_scope",
        table_name="paper_execution_heads",
        schema="hmt_crypto",
    )
    op.drop_table("paper_execution_heads", schema="hmt_crypto")
    op.drop_index(
        "ix_operator_audit_events_time",
        table_name="operator_audit_events",
        schema="hmt_crypto",
    )
    op.drop_index(
        "ix_operator_audit_events_scope",
        table_name="operator_audit_events",
        schema="hmt_crypto",
    )
    op.drop_table("operator_audit_events", schema="hmt_crypto")
    op.drop_constraint(
        "ck_command_operations_operation_kind_enum",
        "command_operations",
        schema="hmt_crypto",
        type_="check",
    )
    op.create_check_constraint(
        "ck_command_operations_operation_kind_enum",
        "command_operations",
        _enum_sql(_EXECUTION_OPERATIONS),
        schema="hmt_crypto",
    )
