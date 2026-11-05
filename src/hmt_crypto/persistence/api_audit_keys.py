"""Pure canonical stage, closure, payload-copy, and advisory key derivation."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Never, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from hmt_crypto._private_enums import exact_enum_member_for_literal
from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.persistence.api_audit_contracts import (
    AccountAuditContext,
    ApiAuditRecordV1,
    AuditCallKind,
    AuditContext,
    AuditContextKind,
    AuditEventKind,
    AuditTarget,
    DispatchLastPhase,
    PayloadSlot,
    PublicAuditContext,
    SimulatorOutcomeKind,
    VenueOutcomeKind,
)
from hmt_crypto.persistence.errors import PersistenceValueError

_API_ID = (
    "target",
    "call_kind",
    "logical_call_id",
    "correlation_id",
    "policy_version",
    "root_event_id",
    "traversal_id",
    "page_number",
    "route_family",
    "route_id",
    "method",
    "path_template",
    "weight",
    "command_id",
    "operation",
    "order_id",
    "order_attempt_id",
    "order_attempt_number",
    "attempt_backend",
    "command_evidence_sha256",
    "simulator_backend_id",
    "simulator_profile_id",
    "simulator_profile_version",
)
_READ_ID = _API_ID + ("transport_attempt_number", "start_event_id")
_DISPATCH_ID = _API_ID + (
    "transport_attempt_number",
    "dispatch_event_id",
    "consumption_id",
    "dispatch_recorded_at",
    "semantic_request_sha256",
    "wire_request_evidence_sha256",
    "simulator_request_evidence_sha256",
    "pre_dispatch_audit_receipt_id",
)
_SOURCE_ID = (
    "source_event_id",
    "source_payload_slot",
    "source_payload_schema_version",
    "source_payload_sha256",
    "source_payload_byte_length",
    "source_payload_expires_at",
    "source_payload_ttl_seconds",
    "source_retention_policy_version",
)
_UUID_FIELDS = frozenset(
    {
        "logical_call_id",
        "correlation_id",
        "root_event_id",
        "traversal_id",
        "command_id",
        "order_id",
        "order_attempt_id",
        "start_event_id",
        "dispatch_event_id",
        "consumption_id",
        "pre_dispatch_audit_receipt_id",
        "source_event_id",
    }
)
_TIME_FIELDS = frozenset({"dispatch_recorded_at", "source_payload_expires_at", "occurred_at"})
_ID_FIELDS = {
    AuditEventKind.CALL_STARTED: _API_ID,
    AuditEventKind.CALL_FINISHED: _API_ID,
    AuditEventKind.ATTEMPT_STARTED: _READ_ID,
    AuditEventKind.ATTEMPT_FINISHED: _READ_ID,
    AuditEventKind.LOCAL_REJECTED: _API_ID,
    AuditEventKind.VENUE_PRE_DISPATCH: _DISPATCH_ID,
    AuditEventKind.SIMULATOR_PRE_DISPATCH: _DISPATCH_ID,
    AuditEventKind.VENUE_NO_ENTRY: _DISPATCH_ID,
    AuditEventKind.VENUE_ENTERED: _DISPATCH_ID,
    AuditEventKind.VENUE_TRANSFER_REJECTED: _API_ID,
    AuditEventKind.SIMULATOR_OUTCOME: _DISPATCH_ID,
    AuditEventKind.MAINTENANCE_ROOT: _SOURCE_ID,
    AuditEventKind.PAYLOAD_PURGED: _SOURCE_ID,
}
_ADVISORY_RESOURCES = (
    "CALL_ROOT",
    "PAGE_ROOT",
    "CALL_STREAM",
    "STAGE",
    "PAYLOAD_DESCRIPTOR",
)


def _invalid() -> Never:
    raise PersistenceValueError() from None


def _utc_text(value: datetime) -> str:
    if type(value) is not datetime or type(value.tzinfo) not in (timezone, ZoneInfo):
        _invalid()
    try:
        if value.utcoffset() != timedelta(0):
            _invalid()
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except Exception:
        _invalid()


def _decoded_uuid(value: object) -> UUID:
    if type(value) is not str:
        _invalid()
    try:
        parsed = UUID(value)
    except Exception:
        _invalid()
    if parsed.int == 0 or str(parsed) != value:
        _invalid()
    return parsed


def _decoded_utc(value: object) -> datetime:
    if type(value) is not str:
        _invalid()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except Exception:
        _invalid()
    if _utc_text(parsed) != value:
        _invalid()
    return parsed


def _enum_member(enum_type: type[object], value: object) -> object:
    try:
        member = exact_enum_member_for_literal(enum_type, value)  # type: ignore[type-var]
    except Exception:
        _invalid()
    if member is None:
        _invalid()
    return member


def _config_member(enum_type: type[object], value: object) -> object:
    if type(value) is str:
        for member in enum_type:  # type: ignore[attr-defined]
            if str.__str__(member) == value:
                return member
    _invalid()


def _decoded_context(value: object) -> AuditContext:
    if type(value) is not list or not value:
        _invalid()
    if value[0] == "PUBLIC" and len(value) == 3:
        return PublicAuditContext(
            context_kind=AuditContextKind.PUBLIC,
            region=_config_member(DeltaRegion, value[1]),
            environment=_config_member(DeltaEnvironment, value[2]),
        )
    if value[0] == "ACCOUNT" and len(value) == 5:
        return AccountAuditContext(
            context_kind=AuditContextKind.ACCOUNT,
            account_id=_decoded_uuid(value[1]),
            region=_config_member(DeltaRegion, value[2]),
            environment=_config_member(DeltaEnvironment, value[3]),
            mode=_config_member(ExecutionMode, value[4]),
        )
    _invalid()


def _typed_record_field(name: str, value: object, target: object) -> object:
    if name in _UUID_FIELDS:
        return _decoded_uuid(value)
    if name in _TIME_FIELDS:
        return _decoded_utc(value)
    enums: dict[str, type[object]] = {
        "target": AuditTarget,
        "call_kind": AuditCallKind,
        "source_payload_slot": PayloadSlot,
        "last_phase": DispatchLastPhase,
    }
    if name in enums:
        return _enum_member(enums[name], value)
    if name == "outcome_kind":
        outcome = SimulatorOutcomeKind if target is AuditTarget.SIMULATOR else VenueOutcomeKind
        return _enum_member(outcome, value)
    return value


def _canonical_scalar(value: object) -> object:
    if type(value) in (str, bool):
        return value
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            _invalid()
        return value
    if type(value) is UUID:
        if value.int == 0:
            _invalid()
        return str(value)
    if type(value) is datetime:
        return _utc_text(value)
    if isinstance(value, str) and type(value) is not str:
        return str.__str__(value)
    if type(value) in (tuple, list):
        sequence = cast(tuple[object, ...] | list[object], value)
        return [_canonical_scalar(item) for item in sequence]
    if type(value) is dict:
        output: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str or item is None:
                _invalid()
            output[key] = _canonical_scalar(item)
        return output
    _invalid()


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _canonical_scalar(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except Exception:
        _invalid()


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _exact_record(record: object) -> ApiAuditRecordV1:
    if type(record) is not ApiAuditRecordV1:
        _invalid()
    try:
        return ApiAuditRecordV1.model_validate(record)
    except Exception:
        _invalid()


def _context_array(record: ApiAuditRecordV1) -> list[object]:
    context = record.context
    if type(context) is PublicAuditContext:
        return [
            str.__str__(context.context_kind),
            str.__str__(context.region),
            str.__str__(context.environment),
        ]
    if type(context) is AccountAuditContext:
        return [
            str.__str__(context.context_kind),
            str(context.account_id),
            str.__str__(context.region),
            str.__str__(context.environment),
            str.__str__(context.mode),
        ]
    _invalid()


def _record_identity(record: ApiAuditRecordV1) -> dict[str, object]:
    fields = _ID_FIELDS.get(record.event_kind)
    if fields is None:
        _invalid()
    identity: dict[str, object] = {}
    for name in fields:
        value = object.__getattribute__(record, name)
        if value is not None:
            identity[name] = _canonical_scalar(value)
    return identity


def _stage_from_parts(
    kind: AuditEventKind, context: list[object], identity: dict[str, object]
) -> tuple[str, list[object]]:
    if kind is AuditEventKind.CALL_STARTED:
        return "CALL_START/v1", [identity["logical_call_id"]]
    if kind is AuditEventKind.ATTEMPT_STARTED:
        return "ATTEMPT_START/v1", [
            identity["root_event_id"],
            identity["transport_attempt_number"],
        ]
    if kind is AuditEventKind.ATTEMPT_FINISHED:
        return "ATTEMPT_FINISH/v1", [identity["start_event_id"]]
    if kind is AuditEventKind.CALL_FINISHED:
        return "CALL_FINISH/v1", [identity["root_event_id"]]
    if kind is AuditEventKind.LOCAL_REJECTED:
        return "LOCAL_REJECT/v1", [identity["root_event_id"]]
    if kind in (
        AuditEventKind.VENUE_PRE_DISPATCH,
        AuditEventKind.SIMULATOR_PRE_DISPATCH,
    ):
        return "PRE_DISPATCH/v1", [context, identity["target"], identity["consumption_id"]]
    if kind in (
        AuditEventKind.VENUE_NO_ENTRY,
        AuditEventKind.VENUE_ENTERED,
        AuditEventKind.SIMULATOR_OUTCOME,
    ):
        return "DISPATCH_TERMINAL/v1", [
            context,
            identity["target"],
            identity["consumption_id"],
        ]
    if kind is AuditEventKind.VENUE_TRANSFER_REJECTED:
        return "TRANSFER_REJECT/v1", [identity["root_event_id"]]
    source = [identity[name] for name in _SOURCE_ID]
    if kind is AuditEventKind.MAINTENANCE_ROOT:
        return "MAINTENANCE_ROOT/v1", [context, *source]
    if kind is AuditEventKind.PAYLOAD_PURGED:
        return "PAYLOAD_PURGE/v1", [context, *source]
    _invalid()


def _stage(record: ApiAuditRecordV1) -> tuple[str, list[object]]:
    return _stage_from_parts(record.event_kind, _context_array(record), _record_identity(record))


def _derive_envelope_event_key(
    kind: AuditEventKind, context: list[object], identity: dict[str, object]
) -> str:
    if type(kind) is not AuditEventKind or type(context) is not list or type(identity) is not dict:
        _invalid()
    allowed = _ID_FIELDS.get(kind)
    if allowed is None or not set(identity).issubset(allowed):
        _invalid()
    stage, stage_identity = _stage_from_parts(kind, context, identity)
    return _hash({"domain": "HMT_API_AUDIT_KEY_V1", "stage": stage, "identity": stage_identity})


def _derive(record: object, allowed: tuple[AuditEventKind, ...]) -> str:
    exact = _exact_record(record)
    if exact.event_kind not in allowed:
        _invalid()
    stage, identity = _stage(exact)
    return _hash({"domain": "HMT_API_AUDIT_KEY_V1", "stage": stage, "identity": identity})


def derive_call_start_key(record: ApiAuditRecordV1) -> str:
    """Derive CALL_START/v1 from one exact CALL_STARTED record."""
    return _derive(record, (AuditEventKind.CALL_STARTED,))


def derive_attempt_start_key(record: ApiAuditRecordV1) -> str:
    """Derive ATTEMPT_START/v1 from one exact ATTEMPT_STARTED record."""
    return _derive(record, (AuditEventKind.ATTEMPT_STARTED,))


def derive_attempt_finish_key(record: ApiAuditRecordV1) -> str:
    """Derive ATTEMPT_FINISH/v1 from one exact ATTEMPT_FINISHED record."""
    return _derive(record, (AuditEventKind.ATTEMPT_FINISHED,))


def derive_call_finish_key(record: ApiAuditRecordV1) -> str:
    """Derive CALL_FINISH/v1 from one exact CALL_FINISHED record."""
    return _derive(record, (AuditEventKind.CALL_FINISHED,))


def derive_local_reject_key(record: ApiAuditRecordV1) -> str:
    """Derive LOCAL_REJECT/v1 from one exact LOCAL_REJECTED record."""
    return _derive(record, (AuditEventKind.LOCAL_REJECTED,))


def derive_pre_dispatch_key(record: ApiAuditRecordV1) -> str:
    """Derive the shared target-specific PRE_DISPATCH/v1 stage key."""
    return _derive(
        record,
        (AuditEventKind.VENUE_PRE_DISPATCH, AuditEventKind.SIMULATOR_PRE_DISPATCH),
    )


def derive_dispatch_terminal_key(record: ApiAuditRecordV1) -> str:
    """Derive the single entered/no-entry DISPATCH_TERMINAL/v1 slot."""
    return _derive(
        record,
        (
            AuditEventKind.VENUE_NO_ENTRY,
            AuditEventKind.VENUE_ENTERED,
            AuditEventKind.SIMULATOR_OUTCOME,
        ),
    )


def derive_transfer_reject_key(record: ApiAuditRecordV1) -> str:
    """Derive TRANSFER_REJECT/v1 from one receiver rejection."""
    return _derive(record, (AuditEventKind.VENUE_TRANSFER_REJECTED,))


def derive_maintenance_root_key(record: ApiAuditRecordV1) -> str:
    """Derive MAINTENANCE_ROOT/v1 from one source descriptor."""
    return _derive(record, (AuditEventKind.MAINTENANCE_ROOT,))


def derive_payload_purge_key(record: ApiAuditRecordV1) -> str:
    """Derive PAYLOAD_PURGE/v1 from one source descriptor."""
    return _derive(record, (AuditEventKind.PAYLOAD_PURGED,))


def derive_event_key(record: ApiAuditRecordV1) -> str:
    """Dispatch one closed event record to its fixed stage domain."""
    exact = _exact_record(record)
    stage, identity = _stage(exact)
    return _hash({"domain": "HMT_API_AUDIT_KEY_V1", "stage": stage, "identity": identity})


def derive_call_close_key(root_event_id: UUID) -> str:
    """Derive the shared terminal/finish CALL_CLOSE/v1 key."""
    if type(root_event_id) is not UUID or root_event_id.int == 0:
        _invalid()
    return _hash(
        {
            "domain": "HMT_API_AUDIT_KEY_V1",
            "stage": "CALL_CLOSE/v1",
            "identity": [str(root_event_id)],
        }
    )


def derive_payload_copy_key(owning_event_id: UUID, slot: PayloadSlot) -> str:
    """Derive PAYLOAD_COPY/v1 from generated event identity and fixed slot."""
    if type(owning_event_id) is not UUID or owning_event_id.int == 0:
        _invalid()
    if type(slot) is not PayloadSlot:
        _invalid()
    return _hash(
        {
            "domain": "HMT_API_AUDIT_KEY_V1",
            "stage": "PAYLOAD_COPY/v1",
            "identity": [str(owning_event_id), str.__str__(slot)],
        }
    )


def derive_api_audit_advisory_key(resource: str, identity: tuple[object, ...]) -> int:
    """Return the signed first-eight-byte advisory namespace projection."""
    if type(resource) is not str or resource not in _ADVISORY_RESOURCES:
        _invalid()
    if type(identity) is not tuple or not identity:
        _invalid()
    digest = hashlib.sha256(
        _canonical_bytes(
            {
                "domain": "HMT_API_AUDIT_XACT_LOCK_V1",
                "resource": resource,
                "identity": identity,
            }
        )
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


__all__ = [
    "derive_api_audit_advisory_key",
    "derive_attempt_finish_key",
    "derive_attempt_start_key",
    "derive_call_close_key",
    "derive_call_finish_key",
    "derive_call_start_key",
    "derive_dispatch_terminal_key",
    "derive_event_key",
    "derive_local_reject_key",
    "derive_maintenance_root_key",
    "derive_payload_copy_key",
    "derive_payload_purge_key",
    "derive_pre_dispatch_key",
    "derive_transfer_reject_key",
]
