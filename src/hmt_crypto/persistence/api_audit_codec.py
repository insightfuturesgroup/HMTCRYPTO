"""Canonical ASCII codecs and nonrecursive hashes for immutable API audit evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Never, TypeVar, cast

from hmt_crypto._private_enums import ExactStrEnum, exact_enum_member_for_literal
from hmt_crypto.domain._private_model import PrivateBoundaryModel
from hmt_crypto.persistence._api_audit_allocation_binding import (
    _api_audit_storage,
    _AuditMaintenanceStorageProjection,
    _exact_api_audit_allocation,
    validate_api_audit_allocation_binding,
)
from hmt_crypto.persistence._api_audit_contexts import (
    _EVENT_FACT_FIELDS as _FACT_FIELDS,
)
from hmt_crypto.persistence._api_audit_contexts import (
    _SUMMARY_ID_FIELDS,
    _TERMINAL_KINDS,
)
from hmt_crypto.persistence._api_audit_summaries import _ALL_SUMMARY_MODELS
from hmt_crypto.persistence.api_audit_contracts import (
    ApiAuditRecordV1,
    AuditCallKind,
    AuditEventKind,
    AuditPayloadDescriptor,
    AuditRetentionMode,
    AuditStorageAllocation,
    AuditSummaryKind,
    LocalRejectionReason,
    LocalRejectionSummaryV1,
    PayloadSlot,
    SimulatorOutcomeKind,
    SimulatorRequestSummaryV1,
    SimulatorResponseSummaryV1,
    TransferRejectionReason,
    TransferRejectionSummaryV1,
    VenueOutcomeKind,
    VenueRequestSummaryV1,
    VenueResponseSummaryV1,
)
from hmt_crypto.persistence.api_audit_keys import (
    _ID_FIELDS,
    _canonical_bytes,
    _context_array,
    _decoded_context,
    _decoded_utc,
    _decoded_uuid,
    _exact_record,
    _record_identity,
    _typed_record_field,
    derive_call_close_key,
    derive_event_key,
)
from hmt_crypto.persistence.errors import PersistenceValueError

E = TypeVar("E", bound=ExactStrEnum)


def _invalid() -> Never:
    raise PersistenceValueError() from None


def _summary_dict(summary: object) -> dict[str, object]:
    if type(summary) not in _ALL_SUMMARY_MODELS:
        _invalid()
    try:
        summary_type = cast(type[PrivateBoundaryModel], type(summary))
        exact = summary_type.model_validate(summary)
        return cast(
            dict[str, object],
            exact.model_dump(mode="python", exclude_none=True, exclude_unset=True),
        )
    except Exception:
        _invalid()


def encode_api_audit_summary(summary: object) -> bytes:
    """Encode one exact closed summary as compact sorted ASCII JSON."""
    encoded = _canonical_bytes(_summary_dict(summary))
    if len(encoded) > 2048:
        _invalid()
    return encoded


def hash_api_audit_payload_copy(summary: object) -> str:
    """Hash only the canonical sanitized snapshot bytes for a later copy row."""
    return hashlib.sha256(encode_api_audit_summary(summary)).hexdigest()


def _facts(record: ApiAuditRecordV1) -> dict[str, object]:
    output: dict[str, object] = {}
    for name in _FACT_FIELDS[record.event_kind]:
        value = object.__getattribute__(record, name)
        if value is not None:
            output[name] = value
    return output


def _producer(record: object) -> dict[str, object]:
    exact = _exact_record(record)
    producer: dict[str, object] = {
        "context": _context_array(exact),
        "event_kind": str.__str__(exact.event_kind),
        "identity": _record_identity(exact),
        "facts": _facts(exact),
    }
    if exact.summary is not None:
        producer["summary"] = _summary_dict(exact.summary)
    return producer


def hash_api_audit_summary(record: ApiAuditRecordV1) -> str:
    """Hash the exact context/identity/summary envelope for one summary event."""
    exact = _exact_record(record)
    if exact.summary is None:
        _invalid()
    identity: dict[str, object] = {"context": _context_array(exact)}
    record_identity = _record_identity(exact)
    for name in _SUMMARY_ID_FIELDS:
        if name in record_identity:
            identity[name] = record_identity[name]
    envelope = {
        "domain": "HMT_API_AUDIT_SUMMARY_V1",
        "event_kind": str.__str__(exact.event_kind),
        "identity": identity,
        "summary": _summary_dict(exact.summary),
    }
    return hashlib.sha256(_canonical_bytes(envelope)).hexdigest()


def encode_api_audit_closure(terminal_record: ApiAuditRecordV1) -> bytes:
    """Encode the exact terminal producer envelope used by both closure rows."""
    exact = _exact_record(terminal_record)
    if exact.event_kind not in _TERMINAL_KINDS or exact.root_event_id is None:
        _invalid()
    return _canonical_bytes(
        {
            "domain": "HMT_API_AUDIT_CLOSURE_V1",
            "root_event_id": str(exact.root_event_id),
            "terminal": _producer(exact),
        }
    )


def hash_api_audit_closure(terminal_record: ApiAuditRecordV1) -> str:
    """Hash the canonical atomic terminal source envelope."""
    return hashlib.sha256(encode_api_audit_closure(terminal_record)).hexdigest()


def encode_api_audit_evidence(
    record: ApiAuditRecordV1, allocation: AuditStorageAllocation
) -> bytes:
    """Encode all producer/storage evidence except its recursive digest."""
    exact_record = _exact_record(record)
    exact_allocation = _exact_api_audit_allocation(allocation)
    validate_api_audit_allocation_binding(exact_record, exact_allocation)
    encoded = _canonical_bytes(
        {
            "domain": "HMT_API_AUDIT_EVENT_V1",
            "producer": _producer(exact_record),
            "storage": _api_audit_storage(exact_record, exact_allocation),
        }
    )
    if len(encoded) > 16_384:
        _invalid()
    return encoded


def _encode_api_audit_maintenance_evidence(
    record: ApiAuditRecordV1,
    allocation: AuditStorageAllocation,
    maintenance: _AuditMaintenanceStorageProjection,
) -> bytes:
    """Encode database-owned maintenance evidence through its private projection."""
    exact_record = _exact_record(record)
    exact_allocation = _exact_api_audit_allocation(allocation)
    try:
        exact_maintenance = _AuditMaintenanceStorageProjection.model_validate(maintenance)
    except Exception:
        _invalid()
    validate_api_audit_allocation_binding(exact_record, exact_allocation, exact_maintenance)
    encoded = _canonical_bytes(
        {
            "domain": "HMT_API_AUDIT_EVENT_V1",
            "producer": _producer(exact_record),
            "storage": _api_audit_storage(exact_record, exact_allocation, exact_maintenance),
        }
    )
    if len(encoded) > 16_384:
        _invalid()
    return encoded


def hash_api_audit_event(record: ApiAuditRecordV1, allocation: AuditStorageAllocation) -> str:
    """Return SHA-256 of one canonical nonrecursive event envelope."""
    return hashlib.sha256(encode_api_audit_evidence(record, allocation)).hexdigest()


def _pairs_without_duplicates(pairs: list[tuple[object, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in output or value is None:
            _invalid()
        output[key] = value
    return output


def _summary_enum(enum_type: type[E], value: object) -> E:
    try:
        member = exact_enum_member_for_literal(enum_type, value)
    except Exception:
        _invalid()
    if member is None:
        _invalid()
    return member


def decode_api_audit_summary(payload: bytes) -> object:
    """Decode only canonical stored summary bytes into the exact closed DTO."""
    if type(payload) is not bytes or not 1 <= len(payload) <= 2048:
        _invalid()
    try:
        text = payload.decode("ascii")
        raw = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_float=lambda _value: _invalid(),
            parse_constant=lambda _value: _invalid(),
        )
    except Exception:
        _invalid()
    if type(raw) is not dict or _canonical_bytes(raw) != payload:
        _invalid()
    kind = _summary_enum(AuditSummaryKind, raw.get("summary_kind"))
    values = dict(raw)
    values["summary_kind"] = kind
    model: type[object]
    if kind is AuditSummaryKind.VENUE_REQUEST:
        model = VenueRequestSummaryV1
    elif kind is AuditSummaryKind.VENUE_RESPONSE:
        model = VenueResponseSummaryV1
        values["outcome_kind"] = _summary_enum(VenueOutcomeKind, values.get("outcome_kind"))
    elif kind is AuditSummaryKind.SIMULATOR_REQUEST:
        model = SimulatorRequestSummaryV1
    elif kind is AuditSummaryKind.SIMULATOR_RESPONSE:
        model = SimulatorResponseSummaryV1
        values["outcome_kind"] = _summary_enum(SimulatorOutcomeKind, values.get("outcome_kind"))
    elif kind is AuditSummaryKind.LOCAL_REJECTION:
        model = LocalRejectionSummaryV1
        values["reason"] = _summary_enum(LocalRejectionReason, values.get("reason"))
    elif kind is AuditSummaryKind.TRANSFER_REJECTION:
        model = TransferRejectionSummaryV1
        values["reason"] = _summary_enum(TransferRejectionReason, values.get("reason"))
        for name in ("prior_dispatch_event_id", "prior_consumption_id"):
            if name in values:
                values[name] = _decoded_uuid(values[name])
    else:
        _invalid()
    try:
        return model(**values)
    except Exception:
        _invalid()


def _record_from_producer(raw: object) -> ApiAuditRecordV1:
    if type(raw) is not dict:
        _invalid()
    kind = _summary_enum(AuditEventKind, raw.get("event_kind"))
    has_summary = kind not in {
        AuditEventKind.CALL_STARTED,
        AuditEventKind.CALL_FINISHED,
        AuditEventKind.MAINTENANCE_ROOT,
        AuditEventKind.PAYLOAD_PURGED,
    }
    expected = {"context", "event_kind", "identity", "facts"} | (
        {"summary"} if has_summary else set()
    )
    identity, facts = raw.get("identity"), raw.get("facts")
    if set(raw) != expected or type(identity) is not dict or type(facts) is not dict:
        _invalid()
    if not set(identity).issubset(_ID_FIELDS[kind]) or not set(facts).issubset(_FACT_FIELDS[kind]):
        _invalid()
    target = (
        _typed_record_field("target", identity.get("target"), None)
        if "target" in identity
        else None
    )
    values = {name: _typed_record_field(name, value, target) for name, value in identity.items()}
    values.update({name: _typed_record_field(name, value, target) for name, value in facts.items()})
    if has_summary:
        values["summary"] = decode_api_audit_summary(_canonical_bytes(raw["summary"]))
    try:
        return ApiAuditRecordV1.model_validate(
            {
                "event_kind": kind,
                "context": _decoded_context(raw["context"]),
                **values,
            }
        )
    except Exception:
        _invalid()


def _validated_storage(record: ApiAuditRecordV1, raw: object) -> AuditStorageAllocation:
    if type(raw) is not dict:
        _invalid()
    required = {"id", "sequence", "audit_recorded_at", "event_key", "retention_mode"}
    if record.summary is not None:
        required.add("summary_sha256")
    retention = _summary_enum(AuditRetentionMode, raw.get("retention_mode"))
    descriptor = None
    if retention is AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT:
        required.add("payload_descriptor")
        data = raw.get("payload_descriptor")
        if type(data) is not dict:
            _invalid()
        converted = dict(data)
        converted["payload_slot"] = _summary_enum(PayloadSlot, data.get("payload_slot"))
        converted["payload_expires_at"] = _decoded_utc(data.get("payload_expires_at"))
        descriptor = AuditPayloadDescriptor(retention_mode=retention, **converted)
    if record.event_kind is AuditEventKind.CALL_STARTED:
        required.add("root_event_id")
    maintenance = record.event_kind in (
        AuditEventKind.MAINTENANCE_ROOT,
        AuditEventKind.PAYLOAD_PURGED,
    )
    if maintenance:
        required.update({"maintenance_root_id", "actor_role_oid", "actor_role_name"})
    closure = "closure_key" in raw or "closure_sha256" in raw
    if closure:
        required.update({"closure_key", "closure_sha256"})
    if set(raw) != required:
        _invalid()
    identifier = _decoded_uuid(raw["id"])
    recorded = _decoded_utc(raw["audit_recorded_at"])
    if type(raw["sequence"]) is not int or not 1 <= raw["sequence"] < 2**63:
        _invalid()
    if raw["event_key"] != derive_event_key(record):
        _invalid()
    if record.summary is not None and raw["summary_sha256"] != hash_api_audit_summary(record):
        _invalid()
    eligible = {
        AuditEventKind.ATTEMPT_STARTED: PayloadSlot.REQUEST,
        AuditEventKind.ATTEMPT_FINISHED: PayloadSlot.RESPONSE,
        AuditEventKind.VENUE_PRE_DISPATCH: PayloadSlot.REQUEST,
        AuditEventKind.SIMULATOR_PRE_DISPATCH: PayloadSlot.REQUEST,
        AuditEventKind.VENUE_ENTERED: PayloadSlot.RESPONSE,
        AuditEventKind.SIMULATOR_OUTCOME: PayloadSlot.RESPONSE,
    }
    if descriptor is not None:
        summary_bytes = encode_api_audit_summary(record.summary)
        if (
            descriptor.payload_slot is not eligible.get(record.event_kind)
            or descriptor.payload_sha256 != hashlib.sha256(summary_bytes).hexdigest()
            or descriptor.payload_byte_length != len(summary_bytes)
            or (
                record.event_kind is AuditEventKind.SIMULATOR_OUTCOME
                and record.entry_possible is not True
            )
        ):
            _invalid()
    terminal = record.event_kind in _TERMINAL_KINDS
    if closure != terminal and not (record.event_kind is AuditEventKind.CALL_FINISHED and closure):
        _invalid()
    if (
        record.event_kind is AuditEventKind.CALL_FINISHED
        and record.call_kind is not AuditCallKind.READ
        and not closure
    ):
        _invalid()
    closure_source = record if terminal else None
    if closure:
        closure_sha256 = raw["closure_sha256"]
        if (
            type(closure_sha256) is not str
            or len(closure_sha256) != 64
            or any(character not in "0123456789abcdef" for character in closure_sha256)
        ):
            _invalid()
        if record.root_event_id is None or raw["closure_key"] != derive_call_close_key(
            record.root_event_id
        ):
            _invalid()
        if terminal and raw["closure_sha256"] != hash_api_audit_closure(record):
            _invalid()
    if (
        record.event_kind is AuditEventKind.CALL_STARTED
        and _decoded_uuid(raw["root_event_id"]) != identifier
    ):
        _invalid()
    if record.event_kind is AuditEventKind.CALL_STARTED and raw["sequence"] != 1:
        _invalid()
    if record.occurred_at is not None and record.occurred_at > recorded:
        _invalid()
    if record.dispatch_recorded_at is not None and record.dispatch_recorded_at > recorded:
        _invalid()
    if maintenance:
        root = _decoded_uuid(raw["maintenance_root_id"])
        actor = raw["actor_role_oid"]
        actor_name = raw["actor_role_name"]
        if (
            (record.event_kind is AuditEventKind.MAINTENANCE_ROOT and root != identifier)
            or raw["sequence"] != (1 if record.event_kind is AuditEventKind.MAINTENANCE_ROOT else 2)
            or type(actor) is not int
            or not 1 <= actor < 2**63
            or type(actor_name) is not str
            or not 1 <= len(actor_name) <= 128
            or not actor_name.isascii()
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in actor_name)
        ):
            _invalid()
    return AuditStorageAllocation(
        id=identifier,
        sequence=raw["sequence"],
        audit_recorded_at=recorded,
        descriptor=descriptor,
        closure_source=closure_source,
    )


def _decode_api_audit_evidence(
    payload: bytes, expected_sha256: str
) -> tuple[ApiAuditRecordV1, AuditStorageAllocation, dict[str, object]]:
    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= 16_384
        or type(expected_sha256) is not str
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        _invalid()
    try:
        raw = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_pairs_without_duplicates,
            parse_float=lambda _value: _invalid(),
            parse_constant=lambda _value: _invalid(),
        )
    except Exception:
        _invalid()
    if type(raw) is not dict or set(raw) != {"domain", "producer", "storage"}:
        _invalid()
    if raw.get("domain") != "HMT_API_AUDIT_EVENT_V1" or _canonical_bytes(raw) != payload:
        _invalid()
    try:
        record = _record_from_producer(raw.get("producer"))
        allocation = _validated_storage(record, raw.get("storage"))
    except Exception:
        _invalid()
    paired_finish = (
        record.event_kind is AuditEventKind.CALL_FINISHED and allocation.closure_source is None
    )
    maintenance = record.event_kind in (
        AuditEventKind.MAINTENANCE_ROOT,
        AuditEventKind.PAYLOAD_PURGED,
    )
    if hashlib.sha256(payload).hexdigest() != expected_sha256 or (
        not paired_finish
        and not maintenance
        and encode_api_audit_evidence(record, allocation) != payload
    ):
        _invalid()
    if maintenance:
        storage = raw["storage"]
        if type(storage) is not dict:
            _invalid()
        try:
            projection = _AuditMaintenanceStorageProjection(
                maintenance_root_id=_decoded_uuid(storage["maintenance_root_id"]),
                actor_role_oid=storage["actor_role_oid"],
                actor_role_name=storage["actor_role_name"],
            )
        except Exception:
            _invalid()
        if _encode_api_audit_maintenance_evidence(record, allocation, projection) != payload:
            _invalid()
    return record, allocation, cast(dict[str, object], raw)


def validate_api_audit_evidence_bytes(payload: bytes, expected_sha256: str) -> None:
    """Require bounded canonical ASCII evidence and its exact stored digest."""
    _decode_api_audit_evidence(payload, expected_sha256)


__all__ = [
    "decode_api_audit_summary",
    "encode_api_audit_closure",
    "encode_api_audit_evidence",
    "encode_api_audit_summary",
    "hash_api_audit_closure",
    "hash_api_audit_event",
    "hash_api_audit_payload_copy",
    "hash_api_audit_summary",
    "validate_api_audit_evidence_bytes",
]
