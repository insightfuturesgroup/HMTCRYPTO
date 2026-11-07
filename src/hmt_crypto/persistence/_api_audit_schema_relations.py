"""Ordinary candidates, RESTRICT relations, and portable audit stage indexes."""

from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint, text

_SCOPE = ("account_id", "region", "environment", "mode")
_PUBLIC = ("context_kind", "region", "environment")
_ACCOUNT = ("context_kind", *_SCOPE)
_ROOT = (
    "event_kind",
    "target",
    "call_kind",
    "logical_call_id",
    "correlation_id",
    "route_family",
    "route_id",
    "policy_version",
)
_ATTEMPT = (
    "event_kind",
    "target",
    "root_event_id",
    "logical_call_id",
    "correlation_id",
    "transport_attempt_number",
    "route_family",
    "route_id",
    "policy_version",
)
_DESCRIPTOR = (
    "payload_slot",
    "payload_schema_version",
    "payload_sha256",
    "payload_byte_length",
    "payload_expires_at",
    "payload_ttl_seconds",
    "retention_policy_version",
)
_DISPATCH = (
    "event_kind",
    "target",
    "command_id",
    "operation",
    "order_id",
    "order_attempt_id",
    "order_attempt_number",
    "correlation_id",
    "dispatch_event_id",
    "consumption_id",
    "semantic_request_sha256",
    "command_evidence_sha256",
    "dispatch_recorded_at",
)
_SOURCE = tuple(
    "source_event_id source_payload_slot source_payload_schema_version "
    "source_payload_sha256 source_payload_byte_length source_payload_expires_at "
    "source_payload_ttl_seconds source_retention_policy_version actor_role_oid "
    "actor_role_name".split()
)


def _fk(
    name: str, child: tuple[str, ...], table: str, parent: tuple[str, ...]
) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        child,
        [f"hmt_crypto.{table}.{column}" for column in parent],
        name=name,
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


def _partial(name: str, columns: tuple[str, ...], predicate: str) -> Index:
    condition = text(predicate)
    return Index(name, *columns, unique=True, sqlite_where=condition, postgresql_where=condition)


def api_audit_relations() -> tuple[object, ...]:
    """Return fresh unfiltered FK candidates and filtered natural stage indexes."""
    root_public = ("id", *_PUBLIC, *_ROOT)
    root_account = ("id", *_ACCOUNT, *_ROOT)
    root_public_page = (*root_public, "traversal_id", "page_number")
    root_account_page = (*root_account, "traversal_id", "page_number")
    root_public_child = (
        "root_event_id",
        *_PUBLIC,
        "root_event_kind",
        "target",
        "call_kind",
        "logical_call_id",
        "correlation_id",
        "route_family",
        "route_id",
        "policy_version",
    )
    root_account_child = (
        "root_event_id",
        *_ACCOUNT,
        "root_event_kind",
        "target",
        "call_kind",
        "logical_call_id",
        "correlation_id",
        "route_family",
        "route_id",
        "policy_version",
    )
    attempt_public = ("id", *_PUBLIC, *_ATTEMPT)
    attempt_account = ("id", *_ACCOUNT, *_ATTEMPT)
    descriptor_public = ("id", *_PUBLIC, *_DESCRIPTOR)
    descriptor_account = ("id", *_ACCOUNT, *_DESCRIPTOR)
    simulator_root = (
        "id",
        *_ACCOUNT,
        "event_kind",
        "target",
        "call_kind",
        "logical_call_id",
        "correlation_id",
        "policy_version",
        "simulator_backend_id",
        "simulator_profile_id",
        "simulator_profile_version",
    )
    preaudit_venue = (
        "id",
        *_ACCOUNT,
        *_DISPATCH,
        "wire_request_evidence_sha256",
    )
    preaudit_simulator = (
        "id",
        *_ACCOUNT,
        *_DISPATCH,
        "simulator_request_evidence_sha256",
        "simulator_backend_id",
        "simulator_profile_id",
        "simulator_profile_version",
    )
    maintenance_public = ("id", *_PUBLIC, "event_kind", *_SOURCE)
    maintenance_account = ("id", *_ACCOUNT, "event_kind", *_SOURCE)
    return (
        UniqueConstraint("id", *_PUBLIC, name="uq_api_audit_events_id_public_context"),
        UniqueConstraint("id", *_ACCOUNT, name="uq_api_audit_events_id_account_context"),
        UniqueConstraint("event_key", name="uq_api_audit_events_event_key"),
        UniqueConstraint("closure_key", "event_kind", name="uq_api_audit_events_closure_kind"),
        UniqueConstraint(*root_public, name="uq_api_audit_events_root_venue_public"),
        UniqueConstraint(*root_account, name="uq_api_audit_events_root_venue_account"),
        UniqueConstraint(*root_public_page, name="uq_api_audit_events_root_venue_page_public"),
        UniqueConstraint(*root_account_page, name="uq_api_audit_events_root_venue_page_account"),
        UniqueConstraint(*attempt_public, name="uq_api_audit_events_attempt_start_public"),
        UniqueConstraint(*attempt_account, name="uq_api_audit_events_attempt_start_account"),
        UniqueConstraint(*descriptor_public, name="uq_api_audit_events_descriptor_public"),
        UniqueConstraint(*descriptor_account, name="uq_api_audit_events_descriptor_account"),
        UniqueConstraint(*simulator_root, name="uq_api_audit_events_root_simulator_account"),
        UniqueConstraint(*preaudit_venue, name="uq_api_audit_events_preaudit_venue_account"),
        UniqueConstraint(
            *preaudit_simulator, name="uq_api_audit_events_preaudit_simulator_account"
        ),
        UniqueConstraint(*maintenance_public, name="uq_api_audit_events_maintenance_public"),
        UniqueConstraint(*maintenance_account, name="uq_api_audit_events_maintenance_account"),
        _fk("fk_api_audit_events_account_scope", _SCOPE, "accounts", ("id", *_SCOPE[1:])),
        _fk(
            "fk_api_audit_events_order_attempt",
            (
                "order_attempt_id",
                *_SCOPE,
                "command_id",
                "operation",
                "order_id",
                "order_attempt_number",
                "attempt_backend",
                "correlation_id",
            ),
            "order_attempts",
            (
                "id",
                *_SCOPE,
                "command_id",
                "operation_kind",
                "order_id",
                "attempt_number",
                "backend",
                "correlation_id",
            ),
        ),
        _fk(
            "fk_api_audit_events_dispatch_event",
            (
                "dispatch_event_id",
                *_SCOPE,
                "order_attempt_id",
                "attempt_backend",
                "dispatch_event_sequence",
                "dispatch_event_kind",
            ),
            "order_attempt_events",
            ("id", *_SCOPE, "attempt_id", "backend", "sequence", "event_kind"),
        ),
        _fk(
            "fk_api_audit_events_consumption",
            (
                "consumption_id",
                *_SCOPE,
                "command_id",
                "operation",
                "order_id",
                "order_attempt_id",
                "attempt_backend",
                "dispatch_event_id",
            ),
            "risk_rate_consumptions",
            (
                "id",
                *_SCOPE,
                "command_id",
                "command_operation_kind",
                "order_id",
                "order_attempt_id",
                "attempt_backend",
                "dispatch_event_id",
            ),
        ),
        _fk(
            "fk_api_audit_events_root_public",
            root_public_child,
            "api_audit_events",
            root_public,
        ),
        _fk(
            "fk_api_audit_events_root_account",
            root_account_child,
            "api_audit_events",
            root_account,
        ),
        _fk(
            "fk_api_audit_events_root_page_public",
            (*root_public_child, "traversal_id", "page_number"),
            "api_audit_events",
            root_public_page,
        ),
        _fk(
            "fk_api_audit_events_root_page_account",
            (*root_account_child, "traversal_id", "page_number"),
            "api_audit_events",
            root_account_page,
        ),
        _fk(
            "fk_api_audit_events_root_simulator_account",
            (
                "root_event_id",
                *_ACCOUNT,
                "root_event_kind",
                "target",
                "call_kind",
                "logical_call_id",
                "correlation_id",
                "policy_version",
                "simulator_backend_id",
                "simulator_profile_id",
                "simulator_profile_version",
            ),
            "api_audit_events",
            simulator_root,
        ),
        _fk(
            "fk_api_audit_events_start_public",
            (
                "start_event_id",
                *_PUBLIC,
                "start_event_kind",
                "target",
                "root_event_id",
                "logical_call_id",
                "correlation_id",
                "transport_attempt_number",
                "route_family",
                "route_id",
                "policy_version",
            ),
            "api_audit_events",
            attempt_public,
        ),
        _fk(
            "fk_api_audit_events_start_account",
            (
                "start_event_id",
                *_ACCOUNT,
                "start_event_kind",
                "target",
                "root_event_id",
                "logical_call_id",
                "correlation_id",
                "transport_attempt_number",
                "route_family",
                "route_id",
                "policy_version",
            ),
            "api_audit_events",
            attempt_account,
        ),
        _fk(
            "fk_api_audit_events_source_descriptor_public",
            (
                "source_event_id",
                *_PUBLIC,
                "source_payload_slot",
                "source_payload_schema_version",
                "source_payload_sha256",
                "source_payload_byte_length",
                "source_payload_expires_at",
                "source_payload_ttl_seconds",
                "source_retention_policy_version",
            ),
            "api_audit_events",
            descriptor_public,
        ),
        _fk(
            "fk_api_audit_events_source_descriptor_account",
            (
                "source_event_id",
                *_ACCOUNT,
                "source_payload_slot",
                "source_payload_schema_version",
                "source_payload_sha256",
                "source_payload_byte_length",
                "source_payload_expires_at",
                "source_payload_ttl_seconds",
                "source_retention_policy_version",
            ),
            "api_audit_events",
            descriptor_account,
        ),
        _fk(
            "fk_api_audit_events_preaudit_venue",
            (
                "pre_dispatch_audit_receipt_id",
                *_ACCOUNT,
                "pre_dispatch_event_kind",
                "target",
                "command_id",
                "operation",
                "order_id",
                "order_attempt_id",
                "order_attempt_number",
                "correlation_id",
                "dispatch_event_id",
                "consumption_id",
                "semantic_request_sha256",
                "command_evidence_sha256",
                "dispatch_recorded_at",
                "wire_request_evidence_sha256",
            ),
            "api_audit_events",
            preaudit_venue,
        ),
        _fk(
            "fk_api_audit_events_preaudit_simulator",
            (
                "pre_dispatch_audit_receipt_id",
                *_ACCOUNT,
                "pre_dispatch_event_kind",
                "target",
                "command_id",
                "operation",
                "order_id",
                "order_attempt_id",
                "order_attempt_number",
                "correlation_id",
                "dispatch_event_id",
                "consumption_id",
                "semantic_request_sha256",
                "command_evidence_sha256",
                "dispatch_recorded_at",
                "simulator_request_evidence_sha256",
                "simulator_backend_id",
                "simulator_profile_id",
                "simulator_profile_version",
            ),
            "api_audit_events",
            preaudit_simulator,
        ),
        _fk(
            "fk_api_audit_events_maintenance_root_public",
            (
                "maintenance_root_id",
                *_PUBLIC,
                "maintenance_root_kind",
                *_SOURCE,
            ),
            "api_audit_events",
            maintenance_public,
        ),
        _fk(
            "fk_api_audit_events_maintenance_root_account",
            (
                "maintenance_root_id",
                *_ACCOUNT,
                "maintenance_root_kind",
                *_SOURCE,
            ),
            "api_audit_events",
            maintenance_account,
        ),
        _fk(
            "fk_api_audit_events_prior_dispatch_event",
            ("prior_dispatch_event_id", *_SCOPE),
            "order_attempt_events",
            ("id", *_SCOPE),
        ),
        _fk(
            "fk_api_audit_events_prior_consumption",
            ("prior_consumption_id", *_SCOPE),
            "risk_rate_consumptions",
            ("id", *_SCOPE),
        ),
        _partial(
            "ux_api_audit_events_call_start", ("logical_call_id",), "event_kind = 'CALL_STARTED'"
        ),
        _partial(
            "ux_api_audit_events_call_start_page",
            ("traversal_id", "page_number"),
            "event_kind = 'CALL_STARTED' AND traversal_id IS NOT NULL",
        ),
        _partial(
            "ux_api_audit_events_call_stream",
            ("root_event_id", "sequence"),
            "root_event_id IS NOT NULL",
        ),
        _partial(
            "ux_api_audit_events_attempt_start",
            ("root_event_id", "transport_attempt_number"),
            "event_kind = 'ATTEMPT_STARTED'",
        ),
        _partial(
            "ux_api_audit_events_attempt_finish",
            ("start_event_id",),
            "event_kind = 'ATTEMPT_FINISHED'",
        ),
        _partial(
            "ux_api_audit_events_call_finish", ("root_event_id",), "event_kind = 'CALL_FINISHED'"
        ),
        _partial(
            "ux_api_audit_events_pre_dispatch",
            (*_ACCOUNT, "target", "consumption_id"),
            "event_kind IN ('VENUE_PRE_DISPATCH','SIMULATOR_PRE_DISPATCH')",
        ),
        _partial(
            "ux_api_audit_events_pre_dispatch_event",
            (*_ACCOUNT, "target", "dispatch_event_id"),
            "event_kind IN ('VENUE_PRE_DISPATCH','SIMULATOR_PRE_DISPATCH')",
        ),
        _partial(
            "ux_api_audit_events_pre_dispatch_attempt",
            (*_ACCOUNT, "target", "order_attempt_id"),
            "event_kind IN ('VENUE_PRE_DISPATCH','SIMULATOR_PRE_DISPATCH')",
        ),
        _partial(
            "ux_api_audit_events_dispatch_terminal",
            (*_ACCOUNT, "target", "consumption_id"),
            "event_kind IN ('VENUE_NO_ENTRY','VENUE_ENTERED','SIMULATOR_OUTCOME')",
        ),
        _partial(
            "ux_api_audit_events_dispatch_terminal_event",
            (*_ACCOUNT, "target", "dispatch_event_id"),
            "event_kind IN ('VENUE_NO_ENTRY','VENUE_ENTERED','SIMULATOR_OUTCOME')",
        ),
        _partial(
            "ux_api_audit_events_maintenance_stream",
            ("maintenance_root_id", "sequence"),
            "maintenance_root_id IS NOT NULL",
        ),
        _partial(
            "ux_api_audit_events_local_reject", ("root_event_id",), "event_kind = 'LOCAL_REJECTED'"
        ),
        _partial(
            "ux_api_audit_events_transfer_reject",
            ("root_event_id",),
            "event_kind = 'VENUE_TRANSFER_REJECTED'",
        ),
        _partial(
            "ux_api_audit_events_maintenance_root",
            ("source_event_id", "source_payload_slot"),
            "event_kind = 'MAINTENANCE_ROOT'",
        ),
        _partial(
            "ux_api_audit_events_payload_purge",
            ("source_event_id", "source_payload_slot"),
            "event_kind = 'PAYLOAD_PURGED'",
        ),
        Index("ix_api_audit_events_correlation", "correlation_id"),
        Index("ix_api_audit_events_recorded", "audit_recorded_at"),
    )


__all__ = ["api_audit_relations"]
