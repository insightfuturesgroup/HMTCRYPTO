"""Portable named CHECK families for the immutable API-audit metadata table."""

from sqlalchemy import CheckConstraint

from hmt_crypto.persistence._api_audit_contexts import (
    _EXCHANGE_CODES,
    AuditEventKind,
    AuditSummaryKind,
)
from hmt_crypto.persistence._api_audit_records import _terminal_truth_sql
from hmt_crypto.persistence._api_audit_summaries import (
    _response_route_bound_sql,
    _response_truth_sql,
    _route_profile_sql,
)
from hmt_crypto.persistence._api_audit_variant_checks import (
    api_audit_variant_check,
    event_target_call_sql,
)
from hmt_crypto.persistence.schema_support import bounded, sha_check

_EVENTS = tuple(str.__str__(item) for item in AuditEventKind)
_SUMMARIES = tuple(str.__str__(item) for item in AuditSummaryKind)


def _enum(column: str, values: tuple[str, ...]) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR {column} IN ({','.join(repr(value) for value in values)})",
        name=f"{column}_enum",
    )


def _optional_bounded(column: str, maximum: int) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR (length({column}) BETWEEN 1 AND {maximum} "
        f"AND length(trim({column})) > 0)",
        name=f"{column}_optional_bounded",
    )


def _optional_sha(column: str) -> CheckConstraint:
    remainder = column
    for digit in "0123456789abcdef":
        remainder = f"replace({remainder}, '{digit}', '')"
    return CheckConstraint(
        f"{column} IS NULL OR (length({column}) = 64 AND length({remainder}) = 0)",
        name=f"{column}_optional_sha256",
    )


def _optional_i63(column: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR {column} BETWEEN {-(2**63)} AND {2**63 - 1}",
        name=f"{column}_signed_bigint",
    )


def api_audit_checks() -> tuple[CheckConstraint, ...]:
    """Return fresh branch-explicit checks; ORM validation owns semantic parity."""
    hashes = (
        "event_key",
        "evidence_sha256",
        "semantic_request_sha256",
        "command_evidence_sha256",
        "wire_request_evidence_sha256",
        "simulator_request_evidence_sha256",
        "closure_key",
        "closure_sha256",
        "completed_response_sha256",
        "summary_sha256",
        "payload_sha256",
        "source_payload_sha256",
    )
    optional_hashes = tuple(_optional_sha(name) for name in hashes[2:])
    return (
        _enum("event_kind", _EVENTS),
        _enum("context_kind", ("PUBLIC", "ACCOUNT")),
        _enum("region", ("india", "global")),
        _enum("environment", ("testnet", "prod")),
        _enum("target", ("VENUE", "SIMULATOR")),
        _enum("call_kind", ("READ", "DISPATCH", "LOCAL_REJECTION", "RECEIVER_REJECTION")),
        _enum("route_family", ("PUBLIC", "PRIVATE", "UNRESOLVED")),
        _enum("method", ("GET", "POST", "PUT", "DELETE")),
        _enum("operation", ("PLACE_ORDER", "MODIFY_ORDER")),
        _enum("attempt_backend", ("VENUE", "SIMULATOR")),
        _enum("summary_kind", _SUMMARIES),
        _enum(
            "outcome_kind",
            (
                "SUCCESS",
                "REJECTED",
                "AUTH_REJECTED",
                "RATE_LIMITED",
                "TIMEOUT",
                "TRANSPORT_ERROR",
                "PROTOCOL_ERROR",
                "CANCELLED",
                "AMBIGUOUS",
                "LOCAL_REJECTED",
                "ACCEPTED",
                "NOT_SUBMITTED",
            ),
        ),
        _enum("exchange_code", tuple(sorted(_EXCHANGE_CODES))),
        _enum("retention_mode", ("SUMMARY_ONLY", "RETAIN_SANITIZED_SNAPSHOT")),
        _enum("payload_slot", ("REQUEST", "RESPONSE")),
        _enum("source_payload_slot", ("REQUEST", "RESPONSE")),
        _enum(
            "last_phase",
            ("ISSUED", "CLAIMED_FOR_UNSIGNED_PREPARATION", "AUDIT_RECORDED"),
        ),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("audit_schema_version = 1", name="audit_schema_version_one"),
        CheckConstraint(
            "occurred_at IS NULL OR occurred_at <= audit_recorded_at",
            name="occurrence_not_after_storage",
        ),
        CheckConstraint(
            "dispatch_recorded_at IS NULL OR dispatch_recorded_at <= audit_recorded_at",
            name="dispatch_not_after_storage",
        ),
        CheckConstraint(
            "(context_kind = 'PUBLIC' AND account_id IS NULL AND mode IS NULL) OR "
            "(context_kind = 'ACCOUNT' AND account_id IS NOT NULL "
            "AND mode IN ('BACKTEST','PAPER','LIVE'))",
            name="context_shape",
        ),
        CheckConstraint(
            "(closure_key IS NULL AND closure_sha256 IS NULL) OR "
            "(closure_key IS NOT NULL AND closure_sha256 IS NOT NULL)",
            name="closure_pair",
        ),
        CheckConstraint(
            "(event_kind IN ('LOCAL_REJECTED','VENUE_NO_ENTRY','VENUE_ENTERED',"
            "'VENUE_TRANSFER_REJECTED','SIMULATOR_OUTCOME') AND closure_key IS NOT NULL "
            "AND closure_sha256 IS NOT NULL) OR (event_kind = 'CALL_FINISHED' "
            "AND (call_kind = 'READ' OR closure_key IS NOT NULL)) OR "
            "(event_kind NOT IN ('CALL_FINISHED','LOCAL_REJECTED','VENUE_NO_ENTRY',"
            "'VENUE_ENTERED','VENUE_TRANSFER_REJECTED','SIMULATOR_OUTCOME') "
            "AND closure_key IS NULL AND closure_sha256 IS NULL)",
            name="closure_eligibility",
        ),
        CheckConstraint(
            "(completed_response_sha256 IS NULL AND completed_response_byte_length IS NULL) OR "
            "(completed_response_sha256 IS NOT NULL "
            "AND completed_response_byte_length IS NOT NULL "
            "AND completed_response_byte_length >= 0)",
            name="completed_response_pair",
        ),
        CheckConstraint(
            "(traversal_id IS NULL AND page_number IS NULL) OR "
            "(traversal_id IS NOT NULL AND page_number IS NOT NULL AND page_number >= 1)",
            name="page_pair",
        ),
        CheckConstraint(
            "(retention_mode = 'SUMMARY_ONLY' AND payload_slot IS NULL "
            "AND payload_schema_version IS NULL AND payload_sha256 IS NULL "
            "AND payload_byte_length IS NULL AND payload_expires_at IS NULL "
            "AND payload_ttl_seconds IS NULL AND retention_policy_version IS NULL) OR "
            "(retention_mode = 'RETAIN_SANITIZED_SNAPSHOT' AND payload_slot IS NOT NULL "
            "AND payload_schema_version = 1 AND payload_sha256 IS NOT NULL "
            "AND payload_byte_length BETWEEN 1 AND 2048 AND payload_expires_at IS NOT NULL "
            "AND payload_ttl_seconds BETWEEN 1 AND 2592000 "
            "AND retention_policy_version >= 1)",
            name="descriptor_shape",
        ),
        CheckConstraint(
            "(retention_mode = 'SUMMARY_ONLY') OR "
            "(event_kind IN ('ATTEMPT_STARTED','VENUE_PRE_DISPATCH',"
            "'SIMULATOR_PRE_DISPATCH') AND payload_slot = 'REQUEST') OR "
            "(event_kind IN ('ATTEMPT_FINISHED','VENUE_ENTERED') "
            "AND payload_slot = 'RESPONSE') OR "
            "(event_kind = 'SIMULATOR_OUTCOME' AND entry_possible = 1 "
            "AND payload_slot = 'RESPONSE')",
            name="descriptor_slot",
        ),
        CheckConstraint(
            "(summary_kind IS NULL AND summary_payload IS NULL AND summary_sha256 IS NULL) OR "
            "(summary_kind IS NOT NULL AND summary_payload IS NOT NULL "
            "AND length(summary_payload) BETWEEN 1 AND 2048 AND summary_sha256 IS NOT NULL)",
            name="summary_shape",
        ),
        CheckConstraint(
            "((event_kind = 'CALL_STARTED' AND root_event_id = id "
            "AND root_event_kind = 'CALL_STARTED' "
            "AND sequence = 1) OR "
            "(event_kind IN ('MAINTENANCE_ROOT','PAYLOAD_PURGED') "
            "AND root_event_id IS NULL AND root_event_kind IS NULL) OR "
            "(event_kind NOT IN ('CALL_STARTED','MAINTENANCE_ROOT','PAYLOAD_PURGED') "
            "AND root_event_id IS NOT NULL AND root_event_kind = 'CALL_STARTED'))",
            name="event_shape",
        ),
        CheckConstraint(
            "(event_kind NOT IN ('MAINTENANCE_ROOT','PAYLOAD_PURGED') "
            "AND maintenance_root_id IS NULL AND maintenance_root_kind IS NULL "
            "AND source_event_id IS NULL) OR "
            "(event_kind = 'MAINTENANCE_ROOT' AND maintenance_root_id = id "
            "AND maintenance_root_kind = 'MAINTENANCE_ROOT' AND sequence = 1 "
            "AND source_event_id IS NOT NULL) OR "
            "(event_kind = 'PAYLOAD_PURGED' AND maintenance_root_id IS NOT NULL "
            "AND maintenance_root_kind = 'MAINTENANCE_ROOT' AND sequence = 2 "
            "AND source_event_id IS NOT NULL)",
            name="maintenance_shape",
        ),
        CheckConstraint(
            "(event_kind NOT IN ('MAINTENANCE_ROOT','PAYLOAD_PURGED') "
            "AND maintenance_root_id IS NULL AND maintenance_root_kind IS NULL "
            "AND source_event_id IS NULL AND source_payload_slot IS NULL "
            "AND source_payload_schema_version IS NULL AND source_payload_sha256 IS NULL "
            "AND source_payload_byte_length IS NULL AND source_payload_expires_at IS NULL "
            "AND source_payload_ttl_seconds IS NULL "
            "AND source_retention_policy_version IS NULL AND actor_role_oid IS NULL "
            "AND actor_role_name IS NULL) OR "
            "(event_kind IN ('MAINTENANCE_ROOT','PAYLOAD_PURGED') "
            "AND maintenance_root_id IS NOT NULL AND maintenance_root_kind = 'MAINTENANCE_ROOT' "
            "AND source_event_id IS NOT NULL AND source_payload_slot IS NOT NULL "
            "AND source_payload_schema_version = 1 AND source_payload_sha256 IS NOT NULL "
            "AND source_payload_byte_length BETWEEN 1 AND 2048 "
            "AND source_payload_expires_at IS NOT NULL "
            "AND source_payload_ttl_seconds BETWEEN 1 AND 2592000 "
            "AND source_retention_policy_version >= 1 AND actor_role_oid >= 1 "
            "AND actor_role_name IS NOT NULL)",
            name="maintenance_bundle",
        ),
        CheckConstraint(
            "(target = 'SIMULATOR' AND context_kind = 'ACCOUNT' "
            "AND mode IN ('PAPER','BACKTEST') AND route_family IS NULL "
            "AND route_id IS NULL AND method IS NULL AND path_template IS NULL AND weight IS NULL "
            "AND simulator_backend_id IS NOT NULL AND simulator_profile_id IS NOT NULL "
            "AND simulator_profile_version IS NOT NULL) OR "
            "((target IS NULL OR target <> 'SIMULATOR') AND simulator_backend_id IS NULL "
            "AND simulator_profile_id IS NULL AND simulator_profile_version IS NULL)",
            name="simulator_shape",
        ),
        CheckConstraint(
            "(call_kind = 'DISPATCH' AND context_kind = 'ACCOUNT' "
            "AND logical_call_id = order_attempt_id AND command_id IS NOT NULL "
            "AND operation IS NOT NULL AND order_id IS NOT NULL "
            "AND order_attempt_number >= 1 AND attempt_backend IS NOT NULL "
            "AND command_evidence_sha256 IS NOT NULL) OR "
            "((call_kind IS NULL OR call_kind <> 'DISPATCH') AND command_id IS NULL "
            "AND operation IS NULL AND order_id IS NULL AND order_attempt_id IS NULL "
            "AND order_attempt_number IS NULL AND attempt_backend IS NULL "
            "AND command_evidence_sha256 IS NULL)",
            name="dispatch_identity",
        ),
        CheckConstraint(
            "(start_event_kind IS NULL OR start_event_kind = 'ATTEMPT_STARTED') AND "
            "(dispatch_event_kind IS NULL OR (dispatch_event_sequence = 1 "
            "AND dispatch_event_kind = 'DISPATCH_STARTED')) AND "
            "(pre_dispatch_event_kind IS NULL OR "
            "(target = 'VENUE' AND pre_dispatch_event_kind = 'VENUE_PRE_DISPATCH') OR "
            "(target = 'SIMULATOR' AND pre_dispatch_event_kind = 'SIMULATOR_PRE_DISPATCH'))",
            name="causal_literal_shape",
        ),
        CheckConstraint(event_target_call_sql(), name="event_target_call_shape"),
        CheckConstraint(_terminal_truth_sql(), name="terminal_truth_shape"),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="http_status_domain",
        ),
        CheckConstraint(
            "duration_ns IS NULL OR duration_ns >= 0",
            name="duration_nonnegative",
        ),
        CheckConstraint(
            "(event_kind IN ('ATTEMPT_STARTED','VENUE_PRE_DISPATCH') "
            "AND summary_kind = 'VENUE_REQUEST') OR "
            "(event_kind IN ('ATTEMPT_FINISHED','VENUE_ENTERED') "
            "AND summary_kind = 'VENUE_RESPONSE') OR "
            "(event_kind = 'SIMULATOR_PRE_DISPATCH' "
            "AND summary_kind = 'SIMULATOR_REQUEST') OR "
            "(event_kind = 'SIMULATOR_OUTCOME' "
            "AND summary_kind = 'SIMULATOR_RESPONSE') OR "
            "(event_kind IN ('LOCAL_REJECTED','VENUE_NO_ENTRY') "
            "AND summary_kind = 'LOCAL_REJECTION') OR "
            "(event_kind = 'VENUE_TRANSFER_REJECTED' "
            "AND summary_kind = 'TRANSFER_REJECTION') OR "
            "(event_kind IN ('CALL_STARTED','CALL_FINISHED','MAINTENANCE_ROOT',"
            "'PAYLOAD_PURGED') AND summary_kind IS NULL)",
            name="summary_event_pairing",
        ),
        CheckConstraint(_response_truth_sql(), name="response_truth_shape"),
        CheckConstraint(_response_route_bound_sql(), name="response_route_bound"),
        CheckConstraint(
            "(prior_dispatch_event_id IS NULL AND prior_consumption_id IS NULL "
            "AND prior_bytes_sent_possible IS NULL) OR "
            "(prior_dispatch_event_id IS NOT NULL AND prior_consumption_id IS NOT NULL "
            "AND prior_bytes_sent_possible = 1)",
            name="transfer_prior_bundle",
        ),
        api_audit_variant_check(),
        CheckConstraint(_route_profile_sql(), name="route_profile"),
        bounded("event_key", 64),
        bounded("evidence_payload", 16_384),
        sha_check("event_key"),
        sha_check("evidence_sha256"),
        *optional_hashes,
        *tuple(
            _optional_i63(name)
            for name in (
                "sequence",
                "audit_schema_version",
                "page_number",
                "weight",
                "transport_attempt_number",
                "order_attempt_number",
                "dispatch_event_sequence",
                "http_status",
                "completed_response_byte_length",
                "duration_ns",
                "payload_schema_version",
                "payload_byte_length",
                "payload_ttl_seconds",
                "retention_policy_version",
                "source_payload_schema_version",
                "source_payload_byte_length",
                "source_payload_ttl_seconds",
                "source_retention_policy_version",
                "actor_role_oid",
            )
        ),
        *tuple(
            _optional_bounded(name, maximum)
            for name, maximum in (
                ("policy_version", 128),
                ("route_id", 128),
                ("method", 8),
                ("path_template", 256),
                ("exchange_request_id", 128),
                ("simulator_backend_id", 128),
                ("simulator_profile_id", 128),
                ("simulator_profile_version", 128),
                ("summary_payload", 2048),
                ("actor_role_name", 128),
            )
        ),
    )


__all__ = ["api_audit_checks"]
