"""Strict retention, source-copy, and controlled-purge result contracts."""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from hmt_crypto.persistence.api_audit_payload_contracts import (
    AuditPayloadPurgeReceipt,
    AuditPayloadSourceV1,
    AuditRetentionPolicyV1,
)
from hmt_crypto.persistence.api_audit_purge_contract import (
    AUDIT_PAYLOAD_PURGE_ARGUMENT_TYPES,
    AUDIT_PAYLOAD_PURGE_FUNCTION,
    decode_audit_payload_purge_result,
)
from hmt_crypto.persistence.errors import PersistenceValueError

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 5, 0, 0, 1, tzinfo=UTC)
EVENT_ID = UUID("00000000-0000-0000-0000-000000000065")
MAINTENANCE_ID = UUID("00000000-0000-0000-0000-00000000012c")
PURGE_ID = UUID("00000000-0000-0000-0000-00000000012d")
VENUE_REQUEST = (
    '{"method":"GET","path_template":"/v2/products",'
    '"request_evidence_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    '"route_id":"products","route_policy":"delta-docs-2026-11-03",'
    '"schema_version":1,"summary_kind":"VENUE_REQUEST","weight":3}'
)


def _public_context() -> PublicAuditContext:
    return PublicAuditContext(
        context_kind=AuditContextKind.PUBLIC,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )


def _account_context(*, mode: ExecutionMode = ExecutionMode.LIVE) -> AccountAuditContext:
    return AccountAuditContext(
        context_kind=AuditContextKind.ACCOUNT,
        account_id=UUID(int=1),
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=mode,
    )


def _descriptor(slot: PayloadSlot = PayloadSlot.REQUEST) -> AuditPayloadDescriptor:
    payload = VENUE_REQUEST.encode("ascii")
    return AuditPayloadDescriptor(
        retention_mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
        payload_slot=slot,
        payload_schema_version=1,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_byte_length=len(payload),
        payload_expires_at=NOW + timedelta(seconds=60),
        payload_ttl_seconds=60,
        retention_policy_version=3,
    )


def _source(**changes: object) -> AuditPayloadSourceV1:
    values: dict[str, object] = {
        "owning_event_id": EVENT_ID,
        "context": _public_context(),
        "event_kind": AuditEventKind.ATTEMPT_STARTED,
        "entry_possible": None,
        "summary_kind": AuditSummaryKind.VENUE_REQUEST,
        "summary_payload": VENUE_REQUEST,
        "summary_sha256": "b" * 64,
        "audit_recorded_at": NOW,
        "descriptor": _descriptor(),
    }
    values.pop("entry_possible")
    values.update(changes)
    return AuditPayloadSourceV1(**values)


def _purge_row(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "maintenance_root_id": MAINTENANCE_ID,
        "purge_event_id": PURGE_ID,
        "owning_event_id": EVENT_ID,
        "context": _public_context(),
        "payload_slot": PayloadSlot.REQUEST,
        "payload_schema_version": 1,
        "payload_sha256": hashlib.sha256(VENUE_REQUEST.encode("ascii")).hexdigest(),
        "payload_byte_length": len(VENUE_REQUEST.encode("ascii")),
        "payload_expires_at": NOW + timedelta(seconds=60),
        "payload_ttl_seconds": 60,
        "retention_policy_version": 3,
        "actor_role_oid": 16_384,
        "actor_role_name": "hmt_audit_retention",
        "purged_at": NOW + timedelta(seconds=60),
        "audit_recorded_at": NOW + timedelta(seconds=60),
    }
    values.update(changes)
    return values


def test_contract_field_sets_are_exact_and_closed() -> None:
    assert tuple(AuditRetentionPolicyV1.model_fields) == (
        "mode",
        "ttl_seconds",
        "retention_policy_version",
    )
    assert tuple(AuditPayloadSourceV1.model_fields) == (
        "owning_event_id",
        "context",
        "event_kind",
        "entry_possible",
        "summary_kind",
        "summary_payload",
        "summary_sha256",
        "audit_recorded_at",
        "descriptor",
    )
    assert tuple(AuditPayloadPurgeReceipt.model_fields) == tuple(_purge_row())
    with pytest.raises(ValidationError):
        AuditRetentionPolicyV1(mode=AuditRetentionMode.SUMMARY_ONLY, extra="secret")
    with pytest.raises(ValidationError):
        _source(previously_purged=True)


@pytest.mark.parametrize(
    "values",
    (
        {"mode": AuditRetentionMode.SUMMARY_ONLY},
        {
            "mode": AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
            "ttl_seconds": 1,
            "retention_policy_version": 1,
        },
        {
            "mode": AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
            "ttl_seconds": 2_592_000,
            "retention_policy_version": 2**63 - 1,
        },
    ),
)
def test_retention_policy_accepts_only_the_two_exact_truth_branches(
    values: dict[str, object],
) -> None:
    policy = AuditRetentionPolicyV1(**values)
    assert policy.mode is values["mode"]


@pytest.mark.parametrize(
    "values",
    (
        {"mode": AuditRetentionMode.SUMMARY_ONLY, "ttl_seconds": 60},
        {"mode": AuditRetentionMode.SUMMARY_ONLY, "retention_policy_version": 1},
        {"mode": AuditRetentionMode.SUMMARY_ONLY, "ttl_seconds": None},
        {"mode": AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT},
        {
            "mode": AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
            "ttl_seconds": 60,
        },
        {
            "mode": AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
            "ttl_seconds": None,
            "retention_policy_version": 1,
        },
    ),
)
def test_retention_policy_rejects_missing_extra_and_explicit_null_branches(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AuditRetentionPolicyV1(**values)


@pytest.mark.parametrize("ttl", (0, -1, True, 1.0, "1", 2_592_001, 2**63))
def test_retention_policy_rejects_every_ttl_boundary_and_nonexact_type(ttl: object) -> None:
    with pytest.raises(ValidationError):
        AuditRetentionPolicyV1(
            mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
            ttl_seconds=ttl,
            retention_policy_version=1,
        )


@pytest.mark.parametrize("version", (0, -1, True, 1.0, "1", 2**63))
def test_retention_policy_rejects_every_policy_version_nonexact_type(version: object) -> None:
    with pytest.raises(ValidationError):
        AuditRetentionPolicyV1(
            mode=AuditRetentionMode.RETAIN_SANITIZED_SNAPSHOT,
            ttl_seconds=60,
            retention_policy_version=version,
        )


@pytest.mark.parametrize(
    ("event_kind", "context", "slot", "summary_kind", "entry_possible"),
    (
        (
            AuditEventKind.ATTEMPT_STARTED,
            _public_context(),
            PayloadSlot.REQUEST,
            AuditSummaryKind.VENUE_REQUEST,
            None,
        ),
        (
            AuditEventKind.ATTEMPT_FINISHED,
            _account_context(),
            PayloadSlot.RESPONSE,
            AuditSummaryKind.VENUE_RESPONSE,
            None,
        ),
        (
            AuditEventKind.VENUE_PRE_DISPATCH,
            _account_context(),
            PayloadSlot.REQUEST,
            AuditSummaryKind.VENUE_REQUEST,
            None,
        ),
        (
            AuditEventKind.VENUE_ENTERED,
            _account_context(),
            PayloadSlot.RESPONSE,
            AuditSummaryKind.VENUE_RESPONSE,
            None,
        ),
        (
            AuditEventKind.SIMULATOR_PRE_DISPATCH,
            _account_context(mode=ExecutionMode.PAPER),
            PayloadSlot.REQUEST,
            AuditSummaryKind.SIMULATOR_REQUEST,
            None,
        ),
        (
            AuditEventKind.SIMULATOR_OUTCOME,
            _account_context(mode=ExecutionMode.BACKTEST),
            PayloadSlot.RESPONSE,
            AuditSummaryKind.SIMULATOR_RESPONSE,
            True,
        ),
    ),
)
def test_payload_source_accepts_only_eligible_kind_context_slot_and_entry_branches(
    event_kind: AuditEventKind,
    context: PublicAuditContext | AccountAuditContext,
    slot: PayloadSlot,
    summary_kind: AuditSummaryKind,
    entry_possible: bool | None,
) -> None:
    changes: dict[str, object] = {
        "event_kind": event_kind,
        "context": context,
        "summary_kind": summary_kind,
        "descriptor": _descriptor(slot),
    }
    if entry_possible is not None:
        changes["entry_possible"] = entry_possible
    source = _source(**changes)
    assert source.event_kind is event_kind
    assert source.descriptor.payload_slot is slot


@pytest.mark.parametrize(
    "changes",
    (
        {"event_kind": AuditEventKind.CALL_STARTED},
        {"event_kind": AuditEventKind.LOCAL_REJECTED},
        {"descriptor": _descriptor(PayloadSlot.RESPONSE)},
        {"summary_kind": AuditSummaryKind.VENUE_RESPONSE},
        {"entry_possible": True},
        {
            "event_kind": AuditEventKind.VENUE_PRE_DISPATCH,
            "context": _public_context(),
        },
        {
            "event_kind": AuditEventKind.SIMULATOR_PRE_DISPATCH,
            "context": _account_context(),
            "summary_kind": AuditSummaryKind.SIMULATOR_REQUEST,
        },
        {
            "event_kind": AuditEventKind.SIMULATOR_OUTCOME,
            "context": _account_context(mode=ExecutionMode.PAPER),
            "summary_kind": AuditSummaryKind.SIMULATOR_RESPONSE,
            "descriptor": _descriptor(PayloadSlot.RESPONSE),
        },
        {
            "event_kind": AuditEventKind.SIMULATOR_OUTCOME,
            "context": _account_context(mode=ExecutionMode.PAPER),
            "summary_kind": AuditSummaryKind.SIMULATOR_RESPONSE,
            "descriptor": _descriptor(PayloadSlot.RESPONSE),
            "entry_possible": False,
        },
        {"descriptor": AuditPayloadDescriptor(retention_mode=AuditRetentionMode.SUMMARY_ONLY)},
        {
            "descriptor": _descriptor().model_copy(
                update={"payload_expires_at": NOW + timedelta(seconds=61)}
            )
        },
        {"entry_possible": None},
    ),
)
def test_payload_source_rejects_ineligible_or_cross_bound_projection(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _source(**changes)


def test_payload_source_revalidates_exact_instances_and_hides_secret_inputs() -> None:
    source = _source()
    revalidated = AuditPayloadSourceV1.model_validate(source)
    assert revalidated == source
    assert revalidated is not source
    hostile = source.model_copy()
    object.__getattribute__(hostile, "__dict__")["summary_sha256"] = "payload-secret-canary"
    with pytest.raises(ValidationError) as captured:
        AuditPayloadSourceV1.model_validate(hostile)
    assert "payload-secret-canary" not in str(captured.value)

    class HostileDict(dict[str, object]):
        pass

    with pytest.raises(ValidationError):
        AuditPayloadSourceV1.model_validate(HostileDict(source.model_dump()))


def test_purge_operation_identity_has_no_actor_time_or_payload_arguments() -> None:
    assert AUDIT_PAYLOAD_PURGE_FUNCTION == "hmt_crypto.purge_expired_audit_payload"
    assert AUDIT_PAYLOAD_PURGE_ARGUMENT_TYPES == ("UUID", "TEXT")


def test_purge_decoder_accepts_exact_ordered_database_result_only() -> None:
    row = _purge_row()
    receipt = decode_audit_payload_purge_result(row)
    assert type(receipt) is AuditPayloadPurgeReceipt
    assert receipt.owning_event_id == EVENT_ID
    assert receipt.purged_at == receipt.audit_recorded_at
    assert receipt.payload_expires_at <= receipt.purged_at
    assert receipt.actor_role_name == "hmt_audit_retention"


@pytest.mark.parametrize(
    "change",
    (
        {"audit_recorded_at": NOW + timedelta(seconds=61)},
        {"purged_at": NOW + timedelta(seconds=59)},
        {"actor_role_name": "arbitrary_actor"},
        {"actor_role_oid": True},
        {"payload_ttl_seconds": 0},
        {"payload_schema_version": 2},
        {"maintenance_root_id": PURGE_ID},
        {"context": {"context_kind": "PUBLIC"}},
    ),
)
def test_purge_decoder_rejects_wrong_time_actor_identity_type_and_context(
    change: dict[str, object],
) -> None:
    with pytest.raises(PersistenceValueError):
        decode_audit_payload_purge_result(_purge_row(**change))


def test_purge_decoder_rejects_wrong_columns_order_and_raw_driver_objects_safely() -> None:
    row = _purge_row()
    reordered = dict(reversed(tuple(row.items())))
    missing = dict(row)
    missing.pop("purge_event_id")
    extra = dict(row)
    extra["driver_secret_canary"] = "driver-secret-canary"

    class DriverRow(dict[str, object]):
        pass

    for invalid in (reordered, missing, extra, DriverRow(row), tuple(row.values()), object()):
        with pytest.raises(PersistenceValueError) as captured:
            decode_audit_payload_purge_result(invalid)
        assert "driver-secret-canary" not in str(captured.value)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None


def test_d13b_production_graph_has_no_purge_or_external_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    modules = (
        "api_audit_payload_contracts.py",
        "api_audit_payload_codec.py",
        "api_audit_payload_models.py",
        "api_audit_payload_validation.py",
        "api_audit_purge_contract.py",
        "_api_audit_payload_schema.py",
    )
    forbidden_imports = {
        "aiohttp",
        "asyncio",
        "httpx",
        "os",
        "pathlib",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    forbidden_calls = {
        "clock_timestamp",
        "commit",
        "connect",
        "create_async_engine",
        "create_engine",
        "delete",
        "now",
        "open",
        "request",
        "rollback",
        "send",
        "set_role",
        "time",
        "truncate",
        "utcnow",
        "write_text",
    }
    for name in modules:
        path = root / "src/hmt_crypto/persistence" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert forbidden_imports.isdisjoint(roots)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                assert node.module.split(".", 1)[0] not in forbidden_imports
            elif isinstance(node, ast.Call):
                called = node.func
                leaf = (
                    called.id
                    if isinstance(called, ast.Name)
                    else called.attr
                    if isinstance(called, ast.Attribute)
                    else None
                )
                assert leaf not in forbidden_calls
