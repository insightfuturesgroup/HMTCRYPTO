"""Literal canonical-byte, hash, stage-key, and advisory vectors for D13a."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.persistence._api_audit_allocation_binding import (
    _AuditMaintenanceStorageProjection,
)
from hmt_crypto.persistence.api_audit_codec import (
    _encode_api_audit_maintenance_evidence,
    _record_from_producer,
    decode_api_audit_summary,
    encode_api_audit_evidence,
    encode_api_audit_summary,
    hash_api_audit_event,
    hash_api_audit_summary,
    validate_api_audit_evidence_bytes,
)
from hmt_crypto.persistence.api_audit_contracts import (
    AccountAuditContext,
    ApiAuditRecordV1,
    AuditCallKind,
    AuditContextKind,
    AuditEventKind,
    AuditPayloadDescriptor,
    AuditRetentionMode,
    AuditStorageAllocation,
    AuditSummaryKind,
    AuditTarget,
    PayloadSlot,
    PublicAuditContext,
    VenueRequestSummaryV1,
)
from hmt_crypto.persistence.api_audit_keys import (
    derive_api_audit_advisory_key,
    derive_call_close_key,
    derive_call_start_key,
    derive_event_key,
    derive_payload_copy_key,
)
from hmt_crypto.persistence.errors import PersistenceValueError

pytestmark = pytest.mark.unit
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "api_audit"

T0 = datetime(2026, 11, 5, 0, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 5, 0, 0, 1, tzinfo=UTC)
U = lambda value: UUID(int=value)  # noqa: E731


def _public() -> PublicAuditContext:
    return PublicAuditContext(
        context_kind=AuditContextKind.PUBLIC,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )


def _account() -> AccountAuditContext:
    return AccountAuditContext(
        context_kind=AuditContextKind.ACCOUNT,
        account_id=U(1),
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=ExecutionMode.LIVE,
    )


def _read_root() -> ApiAuditRecordV1:
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=_public(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=U(6),
        correlation_id=U(6),
        policy_version="delta-docs-2026-11-03",
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
    )


def _dispatch_root() -> ApiAuditRecordV1:
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.CALL_STARTED,
        context=_account(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.DISPATCH,
        logical_call_id=U(4),
        correlation_id=U(5),
        policy_version="delta-docs-2026-11-04-private-v1",
        route_family="PRIVATE",
        route_id="place_order",
        method="POST",
        path_template="/v2/orders",
        weight=5,
        command_id=U(2),
        operation="PLACE_ORDER",
        order_id=U(3),
        order_attempt_id=U(4),
        order_attempt_number=1,
        attempt_backend="VENUE",
        command_evidence_sha256="c" * 64,
    )


def _attempt_started() -> ApiAuditRecordV1:
    return ApiAuditRecordV1(
        event_kind=AuditEventKind.ATTEMPT_STARTED,
        context=_public(),
        target=AuditTarget.VENUE,
        call_kind=AuditCallKind.READ,
        logical_call_id=U(6),
        correlation_id=U(6),
        policy_version="delta-docs-2026-11-03",
        root_event_id=U(8),
        route_family="PUBLIC",
        route_id="products",
        method="GET",
        path_template="/v2/products",
        weight=3,
        transport_attempt_number=1,
        occurred_at=T0,
        summary=VenueRequestSummaryV1(
            schema_version=1,
            summary_kind=AuditSummaryKind.VENUE_REQUEST,
            route_id="products",
            method="GET",
            path_template="/v2/products",
            route_policy="delta-docs-2026-11-03",
            weight=3,
            request_evidence_sha256="a" * 64,
        ),
    )


def _allocation(identifier: int, sequence: int) -> AuditStorageAllocation:
    return AuditStorageAllocation(
        id=U(identifier),
        sequence=sequence,
        audit_recorded_at=T1,
        descriptor=None,
        closure_source=None,
    )


def test_literal_call_start_and_dispatch_root_vectors() -> None:
    read = _read_root()
    dispatch = _dispatch_root()
    assert derive_call_start_key(read) == (
        "afde671dc33ab2bf4442d78f7ade42000697dc0a8ac4e2d33e23a4b32a47e852"
    )
    assert derive_event_key(read) == derive_call_start_key(read)
    assert hash_api_audit_event(read, _allocation(8, 1)) == (
        "954668fec1a02f37cc56b24978ecd197de14c5ae4d620668c03a1105e8a1cd84"
    )
    assert derive_call_start_key(dispatch) == (
        "f659a0e6e0c3057aac7d23b2a2742ef283d422360c2494ff5acac566adbe5b59"
    )
    assert hash_api_audit_event(dispatch, _allocation(100, 1)) == (
        "877153cf55cb5b70d382c5827aa248fe42d541e0cada84963bcd97ea1a4f34ed"
    )


def test_literal_summary_and_attempt_started_vectors() -> None:
    record = _attempt_started()
    summary_bytes = encode_api_audit_summary(record.summary)
    assert summary_bytes == (
        b'{"method":"GET","path_template":"/v2/products",'
        b'"request_evidence_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"route_id":"products","route_policy":"delta-docs-2026-11-03",'
        b'"schema_version":1,"summary_kind":"VENUE_REQUEST","weight":3}'
    )
    assert hashlib.sha256(summary_bytes).hexdigest() == (
        "7bf4a73e1c3b298cc12df0b28c63c9cc3ff44e619bdd612095180835f217e68e"
    )
    assert hash_api_audit_summary(record) == (
        "016c2ed89c4ee4e779c411b5998766740b7ce49b7eb2253578269333b13d9e5b"
    )
    assert hash_api_audit_event(record, _allocation(102, 2)) == (
        "fb5457e9761e3f5bb1f81ea77bf928e04cc45bdd991cd954f99d7581e17ed812"
    )


def test_copy_close_and_signed_advisory_literal_vectors() -> None:
    assert derive_payload_copy_key(U(101), PayloadSlot.REQUEST) == (
        "912542ff7c8ea8ac8a38df0ca54d504830d5a9b01a9d983b249be2fd4fac9ec2"
    )
    assert derive_call_close_key(U(100)) == (
        "22021b59d9b585569caad3e7100c0e5141cdec9c4586a479fe765f3e1ee83ccd"
    )
    vectors = (
        ("CALL_ROOT", (U(4),), 7_581_962_903_166_070_394),
        ("PAGE_ROOT", (U(7), 1), -5_790_806_471_562_640_287),
        ("CALL_STREAM", (U(100),), -5_354_703_459_564_644_206),
        (
            "STAGE",
            (
                "DISPATCH_TERMINAL/v1",
                ("ACCOUNT", U(1), "india", "testnet", "LIVE"),
                "VENUE",
                U(11),
            ),
            77_687_109_620_568_956,
        ),
        (
            "PAYLOAD_DESCRIPTOR",
            (
                "ff11b8d1b32f92c8764bc67a5bd003fc3ba490cbf735059914116a2de1e42288",
                "REQUEST",
            ),
            1_341_828_885_734_580_240,
        ),
    )
    for resource, identity, expected in vectors:
        assert derive_api_audit_advisory_key(resource, identity) == expected


def test_event_encoding_is_exact_ascii_and_rejects_non_contract_inputs() -> None:
    encoded = encode_api_audit_evidence(_read_root(), _allocation(8, 1))
    assert encoded == json.dumps(
        json.loads(encoded), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    with pytest.raises(PersistenceValueError):
        derive_call_start_key({"logical_call_id": str(U(6))})  # type: ignore[arg-type]
    with pytest.raises(PersistenceValueError):
        encode_api_audit_summary({"schema_version": 1})  # type: ignore[arg-type]


def test_public_event_encoder_rejects_cross_bound_allocation_and_maintenance() -> None:
    attempt = _attempt_started()
    assert attempt.summary is not None
    encoded_summary = encode_api_audit_summary(attempt.summary)
    wrong_descriptor = AuditPayloadDescriptor(
        retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
        payload_slot=PayloadSlot.RESPONSE,
        payload_schema_version=1,
        payload_sha256=hashlib.sha256(encoded_summary).hexdigest(),
        payload_byte_length=len(encoded_summary),
        payload_expires_at=datetime(2026, 11, 5, 0, 1, 1, tzinfo=UTC),
        payload_ttl_seconds=60,
        retention_policy_version=1,
    )
    wrong_allocation = AuditStorageAllocation(
        id=U(102),
        sequence=2,
        audit_recorded_at=T1,
        descriptor=wrong_descriptor,
        closure_source=None,
    )
    with pytest.raises(PersistenceValueError):
        encode_api_audit_evidence(attempt, wrong_allocation)
    with pytest.raises(PersistenceValueError):
        hash_api_audit_event(attempt, wrong_allocation)

    fixture = json.loads((FIXTURES / "v1_event_vectors.jsonl").read_text().splitlines()[12])
    maintenance = _record_from_producer(fixture["producer"])
    allocation = AuditStorageAllocation(
        id=U(300),
        sequence=1,
        audit_recorded_at=datetime(2026, 11, 5, 0, 1, 1, tzinfo=UTC),
        descriptor=None,
        closure_source=None,
    )
    with pytest.raises(PersistenceValueError):
        encode_api_audit_evidence(maintenance, allocation)
    with pytest.raises(PersistenceValueError):
        hash_api_audit_event(maintenance, allocation)


def test_private_maintenance_encoder_requires_actor_and_root_projection() -> None:
    fixture = json.loads((FIXTURES / "v1_event_vectors.jsonl").read_text().splitlines()[13])
    record = _record_from_producer(fixture["producer"])
    allocation = AuditStorageAllocation(
        id=U(301),
        sequence=2,
        audit_recorded_at=datetime(2026, 11, 5, 0, 1, 1, tzinfo=UTC),
        descriptor=None,
        closure_source=None,
    )
    projection = _AuditMaintenanceStorageProjection(
        maintenance_root_id=U(300),
        actor_role_oid=16_384,
        actor_role_name="hmt_audit_retention",
    )
    encoded = _encode_api_audit_maintenance_evidence(record, allocation, projection)
    assert json.loads(encoded) == {
        "domain": "HMT_API_AUDIT_EVENT_V1",
        "producer": fixture["producer"],
        "storage": fixture["storage"],
    }
    validate_api_audit_evidence_bytes(encoded, hashlib.sha256(encoded).hexdigest())


def test_all_fifteen_binding_event_vectors_are_literal_and_independent() -> None:
    rows = [
        json.loads(line) for line in (FIXTURES / "v1_event_vectors.jsonl").read_text().splitlines()
    ]
    assert [row["fixture"] for row in rows] == [
        "CALL_STARTED",
        "CALL_STARTED_DISPATCH",
        "CALL_FINISHED",
        "ATTEMPT_STARTED",
        "ATTEMPT_FINISHED",
        "LOCAL_REJECTED",
        "VENUE_PRE_DISPATCH",
        "SIMULATOR_PRE_DISPATCH",
        "VENUE_NO_ENTRY",
        "VENUE_ENTERED",
        "VENUE_TRANSFER_REJECTED",
        "SIMULATOR_OUTCOME",
        "MAINTENANCE_ROOT",
        "PAYLOAD_PURGED",
        "CALL_FINISHED_CLOSURE",
    ]
    for row in rows:
        encoded = json.dumps(
            {
                "domain": "HMT_API_AUDIT_EVENT_V1",
                "producer": row["producer"],
                "storage": row["storage"],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        assert hashlib.sha256(encoded).hexdigest() == row["expected_event_sha256"]
        validate_api_audit_evidence_bytes(encoded, row["expected_event_sha256"])


def test_all_fixture_variants_reject_missing_null_and_extra_fields() -> None:
    rows = {
        row["fixture"]: row
        for row in (
            json.loads(line)
            for line in (FIXTURES / "v1_event_vectors.jsonl").read_text().splitlines()
        )
    }
    required = {
        "CALL_STARTED": ("identity", "logical_call_id"),
        "CALL_STARTED_DISPATCH": ("identity", "command_id"),
        "CALL_FINISHED": ("facts", "outcome_kind"),
        "ATTEMPT_STARTED": ("identity", "transport_attempt_number"),
        "ATTEMPT_FINISHED": ("identity", "start_event_id"),
        "LOCAL_REJECTED": ("summary", "reason"),
        "VENUE_PRE_DISPATCH": ("identity", "wire_request_evidence_sha256"),
        "SIMULATOR_PRE_DISPATCH": ("identity", "simulator_request_evidence_sha256"),
        "VENUE_NO_ENTRY": ("facts", "last_phase"),
        "VENUE_ENTERED": ("facts", "entry_possible"),
        "VENUE_TRANSFER_REJECTED": ("summary", "reason"),
        "SIMULATOR_OUTCOME": ("facts", "entry_possible"),
        "MAINTENANCE_ROOT": ("identity", "source_event_id"),
        "PAYLOAD_PURGED": ("identity", "source_event_id"),
        "CALL_FINISHED_CLOSURE": ("facts", "outcome_kind"),
    }
    for name, row in rows.items():
        container, field = required[name]
        for replacement in ("missing", None):
            producer = json.loads(json.dumps(row["producer"]))
            if replacement == "missing":
                del producer[container][field]
            else:
                producer[container][field] = None
            with pytest.raises(PersistenceValueError):
                _record_from_producer(producer)
        for target in ("identity", "facts"):
            producer = json.loads(json.dumps(row["producer"]))
            producer[target]["unexpected"] = "fixture-secret"
            with pytest.raises(PersistenceValueError) as captured:
                _record_from_producer(producer)
            assert "fixture-secret" not in str(captured.value)
        producer = json.loads(json.dumps(row["producer"]))
        producer["unexpected"] = "fixture-secret"
        with pytest.raises(PersistenceValueError):
            _record_from_producer(producer)
        if "summary" in row["producer"]:
            producer = json.loads(json.dumps(row["producer"]))
            producer["summary"]["unexpected"] = "fixture-secret"
            with pytest.raises(PersistenceValueError):
                _record_from_producer(producer)


def _canonical_padding(size: int) -> bytes:
    prefix = b'{"x":"'
    suffix = b'"}'
    return prefix + b"a" * (size - len(prefix) - len(suffix)) + suffix


def test_summary_decode_2048_and_2049_byte_boundaries_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hmt_crypto.persistence.api_audit_codec as codec

    entered: list[int] = []

    def observed_loads(*args: object, **kwargs: object) -> object:
        del args, kwargs
        entered.append(1)
        raise ValueError

    monkeypatch.setattr(codec.json, "loads", observed_loads)
    with pytest.raises(PersistenceValueError):
        decode_api_audit_summary(_canonical_padding(2048))
    assert entered == [1]
    with pytest.raises(PersistenceValueError):
        decode_api_audit_summary(_canonical_padding(2049))
    assert entered == [1]


def test_event_decode_rejects_generic_json_and_16385_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generic = _canonical_padding(16_384)
    with pytest.raises(PersistenceValueError):
        validate_api_audit_evidence_bytes(generic, hashlib.sha256(generic).hexdigest())
    rejected = _canonical_padding(16_385)
    entered: list[int] = []
    monkeypatch.setattr(json, "loads", lambda *args, **kwargs: entered.append(1))
    with pytest.raises(PersistenceValueError):
        validate_api_audit_evidence_bytes(rejected, hashlib.sha256(rejected).hexdigest())
    assert entered == []


@pytest.mark.parametrize(
    "payload",
    (
        b'{"schema_version":1,"schema_version":1}',
        b'{ "schema_version":1}',
        b'{"schema_version":1.0}',
        b'{"schema_version":null}',
        b'{"schema_version":"\x00"}',
    ),
)
def test_summary_decoder_rejects_duplicate_noncanonical_float_null_and_nul(
    payload: bytes,
) -> None:
    with pytest.raises(PersistenceValueError):
        decode_api_audit_summary(payload)
