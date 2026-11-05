"""Literal canonical-copy vectors and source/descriptor corruption boundaries."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.persistence.api_audit_contracts import (
    AccountAuditContext,
    AuditContextKind,
    AuditEventKind,
    AuditPayloadDescriptor,
    AuditRetentionMode,
    AuditSummaryKind,
    PayloadSlot,
    PublicAuditContext,
)
from hmt_crypto.persistence.api_audit_payload_codec import (
    derive_api_audit_payload_copy_key,
    encode_api_audit_payload_copy,
    hash_api_audit_payload_copy,
)
from hmt_crypto.persistence.api_audit_payload_contracts import AuditPayloadSourceV1
from hmt_crypto.persistence.api_audit_payload_validation import _source_from_parent
from hmt_crypto.persistence.errors import PersistenceValueError

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 5, 0, 0, 1, tzinfo=UTC)
EVENT_ID = UUID("00000000-0000-0000-0000-000000000065")
VENUE_REQUEST = (
    '{"method":"GET","path_template":"/v2/products",'
    '"request_evidence_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    '"route_id":"products","route_policy":"delta-docs-2026-11-03",'
    '"schema_version":1,"summary_kind":"VENUE_REQUEST","weight":3}'
)
VENUE_RESPONSE = (
    '{"completed_response_byte_length":3,'
    '"completed_response_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
    '"duration_ns":7,"http_status":200,"outcome_kind":"SUCCESS",'
    '"schema_version":1,"summary_kind":"VENUE_RESPONSE"}'
)
SIMULATOR_REQUEST = (
    '{"schema_version":1,"simulator_backend_id":"fixture-simulator",'
    '"simulator_profile_id":"fixture-profile","simulator_profile_version":"1",'
    '"simulator_request_evidence_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",'
    '"summary_kind":"SIMULATOR_REQUEST"}'
)
SIMULATOR_RESPONSE = (
    '{"duration_ns":7,"outcome_kind":"ACCEPTED","schema_version":1,'
    '"summary_kind":"SIMULATOR_RESPONSE"}'
)


def _public_context() -> PublicAuditContext:
    return PublicAuditContext(
        context_kind=AuditContextKind.PUBLIC,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )


def _account_context(mode: ExecutionMode) -> AccountAuditContext:
    return AccountAuditContext(
        context_kind=AuditContextKind.ACCOUNT,
        account_id=UUID(int=1),
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=mode,
    )


def _source(
    payload: str = VENUE_REQUEST,
    *,
    event_kind: AuditEventKind = AuditEventKind.ATTEMPT_STARTED,
    summary_kind: AuditSummaryKind = AuditSummaryKind.VENUE_REQUEST,
    context: PublicAuditContext | AccountAuditContext | None = None,
    slot: PayloadSlot = PayloadSlot.REQUEST,
    entry_possible: bool | None = None,
    descriptor_changes: dict[str, object] | None = None,
) -> AuditPayloadSourceV1:
    payload_bytes = payload.encode("ascii")
    descriptor_values: dict[str, object] = {
        "retention_mode": AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
        "payload_slot": slot,
        "payload_schema_version": 1,
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload_byte_length": len(payload_bytes),
        "payload_expires_at": NOW + timedelta(seconds=60),
        "payload_ttl_seconds": 60,
        "retention_policy_version": 3,
    }
    descriptor_values.update(descriptor_changes or {})
    values: dict[str, object] = {
        "owning_event_id": EVENT_ID,
        "context": context or _public_context(),
        "event_kind": event_kind,
        "summary_kind": summary_kind,
        "summary_payload": payload,
        "summary_sha256": "c" * 64,
        "audit_recorded_at": NOW,
        "descriptor": AuditPayloadDescriptor(**descriptor_values),
    }
    if entry_possible is not None:
        values["entry_possible"] = entry_possible
    return AuditPayloadSourceV1(**values)


def _parent_projection(
    source: AuditPayloadSourceV1, entry_possible: bool | None
) -> dict[str, object]:
    context = source.context
    descriptor = source.descriptor
    return {
        "id": source.owning_event_id,
        "context_kind": str.__str__(context.context_kind),
        "region": str.__str__(context.region),
        "environment": str.__str__(context.environment),
        "account_id": (context.account_id if type(context) is AccountAuditContext else None),
        "mode": (str.__str__(context.mode) if type(context) is AccountAuditContext else None),
        "event_kind": str.__str__(source.event_kind),
        "entry_possible": entry_possible,
        "summary_kind": str.__str__(source.summary_kind),
        "summary_payload": source.summary_payload,
        "summary_sha256": source.summary_sha256,
        "audit_recorded_at": source.audit_recorded_at,
        "retention_mode": str.__str__(descriptor.retention_mode),
        "payload_slot": str.__str__(descriptor.payload_slot),
        "payload_schema_version": descriptor.payload_schema_version,
        "payload_sha256": descriptor.payload_sha256,
        "payload_byte_length": descriptor.payload_byte_length,
        "payload_expires_at": descriptor.payload_expires_at,
        "payload_ttl_seconds": descriptor.payload_ttl_seconds,
        "retention_policy_version": descriptor.retention_policy_version,
    }


def _event_aware_sources() -> tuple[AuditPayloadSourceV1, ...]:
    return (
        _source(),
        _source(
            VENUE_RESPONSE,
            event_kind=AuditEventKind.ATTEMPT_FINISHED,
            summary_kind=AuditSummaryKind.VENUE_RESPONSE,
            context=_account_context(ExecutionMode.LIVE),
            slot=PayloadSlot.RESPONSE,
        ),
        _source(
            event_kind=AuditEventKind.VENUE_PRE_DISPATCH,
            context=_account_context(ExecutionMode.LIVE),
        ),
        _source(
            VENUE_RESPONSE,
            event_kind=AuditEventKind.VENUE_ENTERED,
            summary_kind=AuditSummaryKind.VENUE_RESPONSE,
            context=_account_context(ExecutionMode.LIVE),
            slot=PayloadSlot.RESPONSE,
        ),
        _source(
            SIMULATOR_REQUEST,
            event_kind=AuditEventKind.SIMULATOR_PRE_DISPATCH,
            summary_kind=AuditSummaryKind.SIMULATOR_REQUEST,
            context=_account_context(ExecutionMode.PAPER),
        ),
        _source(
            SIMULATOR_RESPONSE,
            event_kind=AuditEventKind.SIMULATOR_OUTCOME,
            summary_kind=AuditSummaryKind.SIMULATOR_RESPONSE,
            context=_account_context(ExecutionMode.BACKTEST),
            slot=PayloadSlot.RESPONSE,
            entry_possible=True,
        ),
    )


def test_parent_reconstruction_enforces_event_aware_entry_possible_truth_table() -> None:
    sources = _event_aware_sources()
    for source in sources:
        parent_entry = (
            True
            if source.event_kind in (AuditEventKind.VENUE_ENTERED, AuditEventKind.SIMULATOR_OUTCOME)
            else None
        )
        rebuilt = _source_from_parent(_parent_projection(source, parent_entry))
        assert rebuilt.event_kind is source.event_kind
        assert rebuilt.entry_possible is (
            True if source.event_kind is AuditEventKind.SIMULATOR_OUTCOME else None
        )

    for source in sources:
        wrong_entries = (
            (None, False)
            if source.event_kind in (AuditEventKind.VENUE_ENTERED, AuditEventKind.SIMULATOR_OUTCOME)
            else (False, True)
        )
        for wrong in wrong_entries:
            with pytest.raises(PersistenceValueError):
                _source_from_parent(_parent_projection(source, wrong))


@pytest.mark.parametrize(
    ("payload", "source", "expected_length", "expected_hash", "expected_key"),
    (
        (
            VENUE_REQUEST,
            _source(),
            262,
            "7bf4a73e1c3b298cc12df0b28c63c9cc3ff44e619bdd612095180835f217e68e",
            "912542ff7c8ea8ac8a38df0ca54d504830d5a9b01a9d983b249be2fd4fac9ec2",
        ),
        (
            VENUE_RESPONSE,
            _source(
                VENUE_RESPONSE,
                event_kind=AuditEventKind.ATTEMPT_FINISHED,
                summary_kind=AuditSummaryKind.VENUE_RESPONSE,
                context=_account_context(ExecutionMode.LIVE),
                slot=PayloadSlot.RESPONSE,
            ),
            241,
            "585d11d401157de5861d9594dce6b5ce606f3ee41244f32f70b35bf808256679",
            "3379dde8ae7fa057b92fdbfa7217343904b6131c8865dd87c41b1ba9e6439a84",
        ),
        (
            SIMULATOR_REQUEST,
            _source(
                SIMULATOR_REQUEST,
                event_kind=AuditEventKind.SIMULATOR_PRE_DISPATCH,
                summary_kind=AuditSummaryKind.SIMULATOR_REQUEST,
                context=_account_context(ExecutionMode.PAPER),
            ),
            274,
            "dfd416043bced5a5f414a561d243428e2f9be68d87b58966bd5053739a7f5da4",
            "912542ff7c8ea8ac8a38df0ca54d504830d5a9b01a9d983b249be2fd4fac9ec2",
        ),
        (
            SIMULATOR_RESPONSE,
            _source(
                SIMULATOR_RESPONSE,
                event_kind=AuditEventKind.SIMULATOR_OUTCOME,
                summary_kind=AuditSummaryKind.SIMULATOR_RESPONSE,
                context=_account_context(ExecutionMode.BACKTEST),
                slot=PayloadSlot.RESPONSE,
                entry_possible=True,
            ),
            98,
            "e8c16e84a7ed7009f55f37ca12b26bd3784d5d6a8bdc89cbadf1000bedcf7318",
            "3379dde8ae7fa057b92fdbfa7217343904b6131c8865dd87c41b1ba9e6439a84",
        ),
    ),
)
def test_four_allowed_summaries_copy_literal_bytes_hash_length_and_key(
    payload: str,
    source: AuditPayloadSourceV1,
    expected_length: int,
    expected_hash: str,
    expected_key: str,
) -> None:
    encoded = encode_api_audit_payload_copy(source)
    assert encoded == payload.encode("ascii")
    assert len(encoded) == expected_length
    assert hashlib.sha256(encoded).hexdigest() == expected_hash
    assert hash_api_audit_payload_copy(source) == expected_hash
    assert derive_api_audit_payload_copy_key(source) == expected_key
    assert expected_hash != source.summary_sha256


@pytest.mark.parametrize(
    "payload",
    (
        '{ "method":"GET","path_template":"/v2/products",'
        '"request_evidence_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"route_id":"products","route_policy":"delta-docs-2026-11-03",'
        '"schema_version":1,"summary_kind":"VENUE_REQUEST","weight":3}',
        VENUE_REQUEST.replace('"method":"GET",', '"weight":3,"method":"GET",').replace(
            ',"weight":3}', "}"
        ),
        VENUE_REQUEST.replace('"weight":3}', '"weight":3.0}'),
        VENUE_REQUEST.replace('"weight":3}', '"weight":3,"extra":"secret"}'),
    ),
)
def test_semantically_equal_or_noncanonical_json_never_normalizes(payload: str) -> None:
    source = _source(payload)
    with pytest.raises(PersistenceValueError):
        encode_api_audit_payload_copy(source)


@pytest.mark.parametrize(
    "descriptor_changes",
    (
        {"payload_sha256": "0" * 64},
        {"payload_byte_length": len(VENUE_REQUEST.encode("ascii")) + 1},
    ),
)
def test_codec_rejects_descriptor_hash_and_length_that_do_not_match_exact_bytes(
    descriptor_changes: dict[str, object],
) -> None:
    with pytest.raises(PersistenceValueError):
        encode_api_audit_payload_copy(_source(descriptor_changes=descriptor_changes))


def test_codec_revalidates_hostile_source_and_rejects_wrong_summary_kind() -> None:
    mapping = _source().model_dump(mode="python", exclude_none=True)
    source = _source()
    object.__getattribute__(source, "__dict__")["summary_kind"] = AuditSummaryKind.SIMULATOR_REQUEST
    with pytest.raises(PersistenceValueError):
        encode_api_audit_payload_copy(source)
    with pytest.raises(PersistenceValueError):
        encode_api_audit_payload_copy(mapping)  # type: ignore[arg-type]


def test_summary_text_boundary_accepts_2048_at_contract_and_rejects_2049() -> None:
    source = _source()
    state = source.model_dump(mode="python", exclude_none=True)
    state["summary_payload"] = "a" * 2048
    descriptor = state["descriptor"]
    assert type(descriptor) is dict
    descriptor["payload_byte_length"] = 2048
    descriptor["payload_sha256"] = hashlib.sha256(b"a" * 2048).hexdigest()
    at_limit = AuditPayloadSourceV1(**state)
    with pytest.raises(PersistenceValueError):
        encode_api_audit_payload_copy(at_limit)
    state["summary_payload"] = "a" * 2049
    descriptor["payload_byte_length"] = 2049
    descriptor["payload_sha256"] = hashlib.sha256(b"a" * 2049).hexdigest()
    with pytest.raises(ValidationError):
        AuditPayloadSourceV1(**state)


@pytest.mark.parametrize("payload", ("é", "ok\x00no"))
def test_source_contract_rejects_non_ascii_and_nul_before_codec(payload: str) -> None:
    source = _source().model_dump(mode="python")
    source["summary_payload"] = payload
    with pytest.raises(ValidationError):
        AuditPayloadSourceV1(**source)
