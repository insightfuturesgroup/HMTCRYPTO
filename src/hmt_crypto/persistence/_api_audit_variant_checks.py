"""Exact per-event required and forbidden column branches for API-audit rows."""

from sqlalchemy import CheckConstraint


def _names(value: str) -> set[str]:
    return set(value.split())


def event_target_call_sql() -> str:
    """Return exact target, call, mode, backend, and receiver-route branches."""
    return (
        "(event_kind NOT IN ('ATTEMPT_STARTED','ATTEMPT_FINISHED') "
        "OR (target = 'VENUE' AND call_kind = 'READ')) AND "
        "(event_kind NOT IN ('VENUE_PRE_DISPATCH','VENUE_NO_ENTRY','VENUE_ENTERED') "
        "OR (target = 'VENUE' AND call_kind = 'DISPATCH' "
        "AND context_kind = 'ACCOUNT' AND mode = 'LIVE' "
        "AND attempt_backend = 'VENUE' AND transport_attempt_number = 1)) AND "
        "(event_kind NOT IN ('SIMULATOR_PRE_DISPATCH','SIMULATOR_OUTCOME') "
        "OR (target = 'SIMULATOR' AND call_kind = 'DISPATCH' "
        "AND context_kind = 'ACCOUNT' AND mode IN ('PAPER','BACKTEST') "
        "AND attempt_backend = 'SIMULATOR')) AND "
        "(event_kind <> 'VENUE_TRANSFER_REJECTED' OR (target = 'VENUE' "
        "AND call_kind = 'RECEIVER_REJECTION' AND context_kind = 'ACCOUNT' "
        "AND mode = 'LIVE' AND route_family = 'PRIVATE' "
        "AND route_id IN ('place_order','modify_order'))) AND "
        "(event_kind <> 'LOCAL_REJECTED' OR (target = 'VENUE' "
        "AND call_kind IN ('READ','DISPATCH','LOCAL_REJECTION')))"
    )


_API = _names(
    "occurred_at target root_event_id root_event_kind call_kind logical_call_id "
    "correlation_id policy_version traversal_id page_number route_family route_id "
    "method path_template weight"
)
_COMMAND = _names(
    "command_id operation order_id order_attempt_id order_attempt_number "
    "attempt_backend command_evidence_sha256"
)
_ATTEMPT = _names("transport_attempt_number start_event_id start_event_kind")
_DISPATCH_PARENT = _names(
    "dispatch_event_id dispatch_event_sequence dispatch_event_kind consumption_id "
    "dispatch_recorded_at semantic_request_sha256"
)
_VENUE_TRANSPORT = _names("transport_attempt_number wire_request_evidence_sha256")
_SIMULATOR_EVIDENCE = {"simulator_request_evidence_sha256"}
_PREAUDIT = _names("pre_dispatch_audit_receipt_id pre_dispatch_event_kind")
_PROFILE = _names("simulator_backend_id simulator_profile_id simulator_profile_version")
_CLOSURE = _names("closure_key closure_sha256")
_TERMINAL_FACTS = _names("last_phase entry_possible reconciliation_required")
_ENTRY_FACTS = _names("entry_possible reconciliation_required")
_VENUE_RESPONSE = _names(
    "outcome_kind http_status exchange_code exchange_request_id "
    "completed_response_sha256 completed_response_byte_length duration_ns"
)
_SIMULATOR_RESPONSE = _names("outcome_kind duration_ns")
_SUMMARY = _names("summary_kind summary_payload summary_sha256")
_DESCRIPTOR = _names(
    "payload_slot payload_schema_version payload_sha256 payload_byte_length "
    "payload_expires_at payload_ttl_seconds retention_policy_version"
)
_MAINTENANCE = _names(
    "maintenance_root_id maintenance_root_kind source_event_id source_payload_slot "
    "source_payload_schema_version source_payload_sha256 source_payload_byte_length "
    "source_payload_expires_at source_payload_ttl_seconds "
    "source_retention_policy_version actor_role_oid actor_role_name"
)
_PRIOR = _names("prior_dispatch_event_id prior_consumption_id prior_bytes_sent_possible")
_CONDITIONAL = set().union(
    _API,
    _COMMAND,
    _ATTEMPT,
    _DISPATCH_PARENT,
    _VENUE_TRANSPORT,
    _SIMULATOR_EVIDENCE,
    _PREAUDIT,
    _PROFILE,
    _CLOSURE,
    _TERMINAL_FACTS,
    _VENUE_RESPONSE,
    _SUMMARY,
    _DESCRIPTOR,
    _MAINTENANCE,
    _PRIOR,
)


def _branch(kind: str, required: set[str], allowed: set[str]) -> str:
    clauses = [f"event_kind = '{kind}'"]
    clauses.extend(f"{name} IS NOT NULL" for name in sorted(required))
    clauses.extend(f"{name} IS NULL" for name in sorted(_CONDITIONAL - allowed))
    return "(" + " AND ".join(clauses) + ")"


def api_audit_variant_check() -> CheckConstraint:
    """Return one exhaustive exact required/null branch for every event kind."""
    api_required = _names(
        "target root_event_id root_event_kind call_kind logical_call_id correlation_id "
        "policy_version"
    )
    dispatch_required = _COMMAND | _DISPATCH_PARENT
    summary_required = _SUMMARY
    variants = (
        ("CALL_STARTED", api_required, _API | _COMMAND | _PROFILE),
        (
            "CALL_FINISHED",
            api_required | {"outcome_kind"},
            _API | _COMMAND | _PROFILE | _CLOSURE | {"outcome_kind"},
        ),
        (
            "ATTEMPT_STARTED",
            api_required | summary_required | {"transport_attempt_number"},
            _API | {"transport_attempt_number"} | _SUMMARY | _DESCRIPTOR,
        ),
        (
            "ATTEMPT_FINISHED",
            api_required | _ATTEMPT | summary_required | {"outcome_kind", "duration_ns"},
            _API | _ATTEMPT | _SUMMARY | _VENUE_RESPONSE | _DESCRIPTOR,
        ),
        (
            "LOCAL_REJECTED",
            api_required | summary_required | _CLOSURE | {"outcome_kind"},
            _API | _COMMAND | _ENTRY_FACTS | _SUMMARY | _CLOSURE | {"outcome_kind"},
        ),
        (
            "VENUE_PRE_DISPATCH",
            api_required | dispatch_required | summary_required | _VENUE_TRANSPORT,
            _API | _COMMAND | _DISPATCH_PARENT | _VENUE_TRANSPORT | _SUMMARY | _DESCRIPTOR,
        ),
        (
            "SIMULATOR_PRE_DISPATCH",
            api_required | dispatch_required | summary_required | _PROFILE | _SIMULATOR_EVIDENCE,
            _API
            | _COMMAND
            | _DISPATCH_PARENT
            | _PROFILE
            | _SIMULATOR_EVIDENCE
            | _SUMMARY
            | _DESCRIPTOR,
        ),
        (
            "VENUE_NO_ENTRY",
            api_required
            | dispatch_required
            | summary_required
            | _CLOSURE
            | _TERMINAL_FACTS
            | {"transport_attempt_number", "outcome_kind"},
            _API
            | _COMMAND
            | _DISPATCH_PARENT
            | _VENUE_TRANSPORT
            | _PREAUDIT
            | _TERMINAL_FACTS
            | _SUMMARY
            | _CLOSURE
            | {"outcome_kind"},
        ),
        (
            "VENUE_ENTERED",
            api_required
            | dispatch_required
            | summary_required
            | _CLOSURE
            | _VENUE_TRANSPORT
            | _PREAUDIT
            | _ENTRY_FACTS
            | {"outcome_kind", "duration_ns"},
            _API
            | _COMMAND
            | _DISPATCH_PARENT
            | _VENUE_TRANSPORT
            | _PREAUDIT
            | _ENTRY_FACTS
            | _VENUE_RESPONSE
            | _SUMMARY
            | _DESCRIPTOR
            | _CLOSURE,
        ),
        (
            "VENUE_TRANSFER_REJECTED",
            api_required | summary_required | _CLOSURE,
            _API | _SUMMARY | _CLOSURE | _PRIOR,
        ),
        (
            "SIMULATOR_OUTCOME",
            api_required
            | dispatch_required
            | summary_required
            | _PROFILE
            | _CLOSURE
            | _SIMULATOR_EVIDENCE
            | _ENTRY_FACTS
            | _SIMULATOR_RESPONSE,
            _API
            | _COMMAND
            | _DISPATCH_PARENT
            | _PROFILE
            | _SIMULATOR_EVIDENCE
            | _PREAUDIT
            | _ENTRY_FACTS
            | _SIMULATOR_RESPONSE
            | _SUMMARY
            | _DESCRIPTOR
            | _CLOSURE,
        ),
        ("MAINTENANCE_ROOT", _MAINTENANCE, _MAINTENANCE),
        ("PAYLOAD_PURGED", _MAINTENANCE, _MAINTENANCE),
    )
    return CheckConstraint(
        " OR ".join(
            _branch(kind, set(required), set(allowed)) for kind, required, allowed in variants
        ),
        name="variant_shape",
    )


__all__: list[str] = []
