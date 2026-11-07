"""Offline SQLite/PostgreSQL structural acceptance for immutable audit metadata."""

from __future__ import annotations

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.attempt_models import OrderAttempt
from hmt_crypto.persistence.execution_event_models import OrderAttemptEvent
from hmt_crypto.persistence.models import metadata
from hmt_crypto.persistence.risk_rate_consumption_models import RiskRateConsumption


def _unique(model: type[object], name: str) -> tuple[str, ...]:
    for constraint in model.__table__.constraints:  # type: ignore[attr-defined]
        if isinstance(constraint, UniqueConstraint) and constraint.name == name:
            return tuple(column.name for column in constraint.columns)
    raise AssertionError(name)


def _foreign_key(name: str) -> ForeignKeyConstraint:
    for constraint in ApiAuditEvent.__table__.constraints:
        if isinstance(constraint, ForeignKeyConstraint) and constraint.name == name:
            return constraint
    raise AssertionError(name)


def _index(name: str) -> tuple[tuple[str, ...], str]:
    for index in ApiAuditEvent.__table__.indexes:
        if index.name == name:
            columns = tuple(column.name for column in index.columns)
            predicate = str(index.dialect_options["sqlite"]["where"])
            return columns, predicate
    raise AssertionError(name)


def test_api_audit_event_exact_column_inventory_and_nullability() -> None:
    expected = {
        "id",
        "event_kind",
        "context_kind",
        "region",
        "environment",
        "sequence",
        "event_key",
        "audit_schema_version",
        "audit_recorded_at",
        "evidence_payload",
        "evidence_sha256",
        "account_id",
        "mode",
        "occurred_at",
        "target",
        "root_event_id",
        "root_event_kind",
        "call_kind",
        "logical_call_id",
        "correlation_id",
        "policy_version",
        "traversal_id",
        "page_number",
        "route_family",
        "route_id",
        "method",
        "path_template",
        "weight",
        "transport_attempt_number",
        "start_event_id",
        "start_event_kind",
        "command_id",
        "operation",
        "order_id",
        "order_attempt_id",
        "order_attempt_number",
        "attempt_backend",
        "dispatch_event_id",
        "dispatch_event_sequence",
        "dispatch_event_kind",
        "consumption_id",
        "dispatch_recorded_at",
        "semantic_request_sha256",
        "command_evidence_sha256",
        "wire_request_evidence_sha256",
        "simulator_request_evidence_sha256",
        "simulator_backend_id",
        "simulator_profile_id",
        "simulator_profile_version",
        "closure_key",
        "closure_sha256",
        "pre_dispatch_audit_receipt_id",
        "pre_dispatch_event_kind",
        "last_phase",
        "entry_possible",
        "reconciliation_required",
        "outcome_kind",
        "http_status",
        "exchange_code",
        "exchange_request_id",
        "completed_response_sha256",
        "completed_response_byte_length",
        "duration_ns",
        "summary_kind",
        "summary_payload",
        "summary_sha256",
        "retention_mode",
        "payload_slot",
        "payload_schema_version",
        "payload_sha256",
        "payload_byte_length",
        "payload_expires_at",
        "payload_ttl_seconds",
        "retention_policy_version",
        "maintenance_root_id",
        "maintenance_root_kind",
        "source_event_id",
        "source_payload_slot",
        "source_payload_schema_version",
        "source_payload_sha256",
        "source_payload_byte_length",
        "source_payload_expires_at",
        "source_payload_ttl_seconds",
        "source_retention_policy_version",
        "actor_role_oid",
        "actor_role_name",
        "prior_dispatch_event_id",
        "prior_consumption_id",
        "prior_bytes_sent_possible",
    }
    assert set(ApiAuditEvent.__table__.columns.keys()) == expected
    required = {
        "id",
        "event_kind",
        "context_kind",
        "region",
        "environment",
        "sequence",
        "event_key",
        "audit_schema_version",
        "audit_recorded_at",
        "evidence_payload",
        "evidence_sha256",
        "retention_mode",
    }
    assert {
        column.name for column in ApiAuditEvent.__table__.columns if not column.nullable
    } == required


def test_parent_candidates_are_exact_and_correlation_inclusive() -> None:
    scope = ("account_id", "region", "environment", "mode")
    assert _unique(OrderAttempt, "uq_order_attempts_api_audit_parent") == (
        "id",
        *scope,
        "command_id",
        "operation_kind",
        "order_id",
        "attempt_number",
        "backend",
        "correlation_id",
    )
    assert _unique(OrderAttemptEvent, "uq_order_attempt_events_api_audit_parent") == (
        "id",
        *scope,
        "attempt_id",
        "backend",
        "sequence",
        "event_kind",
    )
    assert _unique(RiskRateConsumption, "uq_risk_rate_consumptions_api_audit_parent") == (
        "id",
        *scope,
        "command_id",
        "command_operation_kind",
        "order_id",
        "order_attempt_id",
        "attempt_backend",
        "dispatch_event_id",
    )


def test_table_has_portable_context_shape_checks_candidates_and_stage_indexes() -> None:
    names = {item.name for item in ApiAuditEvent.__table__.constraints}
    assert {
        "ck_api_audit_events_context_shape",
        "ck_api_audit_events_descriptor_shape",
        "ck_api_audit_events_closure_pair",
        "ck_api_audit_events_event_shape",
        "ck_api_audit_events_variant_shape",
        "ck_api_audit_events_closure_eligibility",
        "ck_api_audit_events_descriptor_slot",
        "ck_api_audit_events_maintenance_bundle",
        "uq_api_audit_events_id_public_context",
        "uq_api_audit_events_id_account_context",
        "uq_api_audit_events_root_venue_page_public",
        "uq_api_audit_events_root_venue_page_account",
        "uq_api_audit_events_event_key",
    } <= names
    index_names = {item.name for item in ApiAuditEvent.__table__.indexes}
    assert {
        "ux_api_audit_events_call_start",
        "ux_api_audit_events_call_stream",
        "ux_api_audit_events_attempt_start",
        "ux_api_audit_events_attempt_finish",
        "ux_api_audit_events_call_finish",
        "ux_api_audit_events_pre_dispatch",
        "ux_api_audit_events_pre_dispatch_event",
        "ux_api_audit_events_pre_dispatch_attempt",
        "ux_api_audit_events_dispatch_terminal",
        "ux_api_audit_events_dispatch_terminal_event",
        "ux_api_audit_events_call_start_page",
        "ux_api_audit_events_maintenance_stream",
    } <= index_names


def test_sqlite_and_postgresql_create_table_compile_offline() -> None:
    table = ApiAuditEvent.__table__
    sqlite_ddl = str(CreateTable(table).compile(dialect=sqlite.dialect()))
    postgres_ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    for ddl in (sqlite_ddl, postgres_ddl):
        assert "api_audit_events" in ddl
        assert "context_shape" in ddl
        assert "descriptor_shape" in ddl
        assert "closure_pair" in ddl
    assert metadata.tables["hmt_crypto.api_audit_events"] is table


def test_root_page_candidates_and_restrict_foreign_keys_are_exact() -> None:
    public = ("context_kind", "region", "environment")
    account = ("context_kind", "account_id", "region", "environment", "mode")
    root = (
        "event_kind",
        "target",
        "call_kind",
        "logical_call_id",
        "correlation_id",
        "route_family",
        "route_id",
        "policy_version",
        "traversal_id",
        "page_number",
    )
    assert _unique(ApiAuditEvent, "uq_api_audit_events_root_venue_page_public") == (
        "id",
        *public,
        *root,
    )
    assert _unique(ApiAuditEvent, "uq_api_audit_events_root_venue_page_account") == (
        "id",
        *account,
        *root,
    )
    for name, scope in (
        ("fk_api_audit_events_root_page_public", public),
        ("fk_api_audit_events_root_page_account", account),
    ):
        foreign_key = _foreign_key(name)
        assert tuple(column.name for column in foreign_key.columns) == (
            "root_event_id",
            *scope,
            "root_event_kind",
            "target",
            "call_kind",
            "logical_call_id",
            "correlation_id",
            "route_family",
            "route_id",
            "policy_version",
            "traversal_id",
            "page_number",
        )
        assert tuple(
            element.target_fullname.rsplit(".", 1)[-1] for element in foreign_key.elements
        ) == ("id", *scope, *root)
        assert foreign_key.ondelete == foreign_key.onupdate == "RESTRICT"


def test_every_stage_index_has_exact_columns_predicate_and_no_fk_uses_it() -> None:
    account = ("context_kind", "account_id", "region", "environment", "mode")
    expected = {
        "ux_api_audit_events_call_start": (("logical_call_id",), "event_kind = 'CALL_STARTED'"),
        "ux_api_audit_events_call_start_page": (
            ("traversal_id", "page_number"),
            "event_kind = 'CALL_STARTED' AND traversal_id IS NOT NULL",
        ),
        "ux_api_audit_events_call_stream": (
            ("root_event_id", "sequence"),
            "root_event_id IS NOT NULL",
        ),
        "ux_api_audit_events_attempt_start": (
            ("root_event_id", "transport_attempt_number"),
            "event_kind = 'ATTEMPT_STARTED'",
        ),
        "ux_api_audit_events_attempt_finish": (
            ("start_event_id",),
            "event_kind = 'ATTEMPT_FINISHED'",
        ),
        "ux_api_audit_events_call_finish": (
            ("root_event_id",),
            "event_kind = 'CALL_FINISHED'",
        ),
        "ux_api_audit_events_local_reject": (
            ("root_event_id",),
            "event_kind = 'LOCAL_REJECTED'",
        ),
        "ux_api_audit_events_pre_dispatch": (
            (*account, "target", "consumption_id"),
            "event_kind IN ('VENUE_PRE_DISPATCH','SIMULATOR_PRE_DISPATCH')",
        ),
        "ux_api_audit_events_pre_dispatch_event": (
            (*account, "target", "dispatch_event_id"),
            "event_kind IN ('VENUE_PRE_DISPATCH','SIMULATOR_PRE_DISPATCH')",
        ),
        "ux_api_audit_events_pre_dispatch_attempt": (
            (*account, "target", "order_attempt_id"),
            "event_kind IN ('VENUE_PRE_DISPATCH','SIMULATOR_PRE_DISPATCH')",
        ),
        "ux_api_audit_events_dispatch_terminal": (
            (*account, "target", "consumption_id"),
            "event_kind IN ('VENUE_NO_ENTRY','VENUE_ENTERED','SIMULATOR_OUTCOME')",
        ),
        "ux_api_audit_events_dispatch_terminal_event": (
            (*account, "target", "dispatch_event_id"),
            "event_kind IN ('VENUE_NO_ENTRY','VENUE_ENTERED','SIMULATOR_OUTCOME')",
        ),
        "ux_api_audit_events_transfer_reject": (
            ("root_event_id",),
            "event_kind = 'VENUE_TRANSFER_REJECTED'",
        ),
        "ux_api_audit_events_maintenance_root": (
            ("source_event_id", "source_payload_slot"),
            "event_kind = 'MAINTENANCE_ROOT'",
        ),
        "ux_api_audit_events_payload_purge": (
            ("source_event_id", "source_payload_slot"),
            "event_kind = 'PAYLOAD_PURGED'",
        ),
        "ux_api_audit_events_maintenance_stream": (
            ("maintenance_root_id", "sequence"),
            "maintenance_root_id IS NOT NULL",
        ),
    }
    assert {name: _index(name) for name in expected} == expected
    for constraint in ApiAuditEvent.__table__.foreign_key_constraints:
        assert constraint.ondelete == constraint.onupdate == "RESTRICT"
