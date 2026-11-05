"""Exact inert B2 dispatch and limiter-completion declarations."""

from __future__ import annotations

import ast
import copy
import inspect
import pickle
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol, get_args, get_type_hints
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.exchange import private_dispatch as dispatch
from hmt_crypto.exchange.delta import private_limiter_completion as completion
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

pytestmark = pytest.mark.unit

_POLICY = "delta-docs-2026-11-04-private-v1"
_SHA_A = "a" * 64
_SHA_B = "b" * 64


class _HostileText(str):
    def __eq__(self, other: object) -> bool:
        del other
        raise AssertionError("fixture-secret-text")


def _account(
    *,
    region: DeltaRegion = DeltaRegion.INDIA,
    environment: DeltaEnvironment = DeltaEnvironment.TESTNET,
    mode: ExecutionMode = ExecutionMode.LIVE,
) -> AccountRef:
    return AccountRef(
        account_id=UUID(int=1),
        external_user_id="12345",
        region=region,
        environment=environment,
        mode=mode,
        credential_version=7,
    )


def _intent_data(*, operation: str = "PLACE_ORDER") -> dict[str, object]:
    route = (
        PrivateRouteId.PLACE_ORDER if operation == "PLACE_ORDER" else PrivateRouteId.MODIFY_ORDER
    )
    return {
        "account": _account(),
        "command_id": UUID(int=2),
        "order_id": UUID(int=3),
        "order_attempt_id": UUID(int=4),
        "order_attempt_number": 1,
        "root_event_id": UUID(int=5),
        "logical_call_id": UUID(int=4),
        "correlation_id": UUID(int=6),
        "operation": operation,
        "route_id": route,
        "product_id": 27,
        "rest_weight": 5,
        "product_operations": 1,
        "policy_version": _POLICY,
        "semantic_request_sha256": _SHA_A,
        "command_evidence_sha256": _SHA_B,
        "process_id": 1234,
        "process_incarnation_id": UUID(int=7),
    }


def _binding_data() -> dict[str, object]:
    return _intent_data() | {
        "registry_owner_id": UUID(int=8),
        "limiter_instance_id": UUID(int=9),
        "held_reservation_id": UUID(int=10),
        "limiter_clock_epoch": 2,
        "limiter_accounting_generation": 3,
        "rest_window_id": UUID(int=11),
        "rest_window_end_monotonic_ns": 30_000_000_000,
        "product_window_id": UUID(int=12),
        "product_window_end_monotonic_ns": 1_000_000_000,
        "cooldown_epoch": 0,
    }


def _audit_data(*, operation: str = "PLACE_ORDER") -> dict[str, object]:
    route = (
        PrivateRouteId.PLACE_ORDER if operation == "PLACE_ORDER" else PrivateRouteId.MODIFY_ORDER
    )
    return {
        "account": _account(),
        "command_id": UUID(int=2),
        "order_id": UUID(int=3),
        "order_attempt_id": UUID(int=4),
        "root_event_id": UUID(int=5),
        "logical_call_id": UUID(int=4),
        "correlation_id": UUID(int=6),
        "order_attempt_number": 1,
        "dispatch_event_id": UUID(int=13),
        "consumption_id": UUID(int=14),
        "operation": operation,
        "route_id": route,
        "policy_version": _POLICY,
        "semantic_request_sha256": _SHA_A,
        "command_evidence_sha256": _SHA_B,
    }


def _assert_invalid(model: type[object], payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as captured:
        model(**payload)  # type: ignore[call-arg]
    assert all(detail.get("input") is None for detail in captured.value.errors())
    assert "fixture-secret" not in f"{captured.value!s} {captured.value!r}"


def test_neutral_dispatch_aliases_are_exact_closed_manifests() -> None:
    assert get_args(dispatch.TrustedDispatchExpirySource) == (
        "RATE_RISK_WINDOW",
        "COMMAND_CLAIM",
        "LEADERSHIP",
        "CATALOGUE",
        "PRICE",
        "RECONCILIATION",
        "CLOCK_READINESS",
        "CREDENTIAL_PROBE",
        "TESTNET_ADMISSION",
    )
    assert get_args(dispatch.VenueDispatchPreEntryPhase) == (
        "ISSUED",
        "CLAIMED_FOR_UNSIGNED_PREPARATION",
        "AUDIT_RECORDED",
    )
    assert get_args(dispatch.VenueNoEntryReason) == (
        "SINK_BINDING_MISMATCH",
        "PREPARATION_FAILED",
        "AUDIT_FAILED",
        "CURRENT_INVALIDATED",
        "DEADLINE_EXPIRED",
        "SIGNING_FAILED",
        "SIGNATURE_EXPIRED",
        "LIMITER_INVALIDATED",
        "CANCELLED",
        "INTERNAL_EXCEPTION",
    )


def test_trusted_dispatch_time_models_are_exact_and_conservative() -> None:
    instant = dispatch.TrustedDispatchInstant(
        process_id=1234,
        process_incarnation_id=UUID(int=20),
        clock_epoch=2,
        correlation_epoch=3,
        monotonic_ns=0,
        utc_epoch_seconds=1_800_000_000,
        utc_upper_bound_ns=1_800_000_000_000_000_001,
    )
    assert instant.model_dump() == {
        "process_id": 1234,
        "process_incarnation_id": UUID(int=20),
        "clock_epoch": 2,
        "correlation_epoch": 3,
        "monotonic_ns": 0,
        "utc_epoch_seconds": 1_800_000_000,
        "utc_upper_bound_ns": 1_800_000_000_000_000_001,
    }
    deadline = dispatch.TrustedDispatchDeadline(
        process_id=1234,
        process_incarnation_id=UUID(int=20),
        clock_epoch=2,
        monotonic_ns=1,
        correlation_epoch=3,
    )
    assert deadline.monotonic_ns == 1

    for source in get_args(dispatch.TrustedDispatchExpirySource):
        expiry = dispatch.TrustedUtcExpiry(
            expires_at=datetime(2026, 11, 5, tzinfo=UTC),
            source=source,
        )
        assert expiry.source == source


@pytest.mark.parametrize(
    "change",
    (
        {"process_id": True},
        {"process_incarnation_id": str(UUID(int=20))},
        {"clock_epoch": 0},
        {"correlation_epoch": 0},
        {"monotonic_ns": -1},
        {"utc_epoch_seconds": -1},
        {"utc_upper_bound_ns": 1_799_999_999_999_999_999},
        {"utc_upper_bound_ns": 1.8e18},
    ),
)
def test_trusted_dispatch_instant_rejects_unsafe_values(change: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "process_id": 1234,
        "process_incarnation_id": UUID(int=20),
        "clock_epoch": 2,
        "correlation_epoch": 3,
        "monotonic_ns": 0,
        "utc_epoch_seconds": 1_800_000_000,
        "utc_upper_bound_ns": 1_800_000_000_000_000_000,
    }
    _assert_invalid(dispatch.TrustedDispatchInstant, payload | change)


def test_trusted_expiry_rejects_naive_non_utc_and_hostile_source() -> None:
    invalid_times = (
        datetime(2026, 11, 5),
        datetime(2026, 11, 5, tzinfo=timezone(timedelta(hours=1))),
    )
    for value in invalid_times:
        _assert_invalid(
            dispatch.TrustedUtcExpiry,
            {"expires_at": value, "source": "LEADERSHIP"},
        )

    class Hostile(str):
        def __eq__(self, other: object) -> bool:
            del other
            raise AssertionError("fixture-secret-source")

    for source in ("UNKNOWN", Hostile("LEADERSHIP"), 1):
        _assert_invalid(
            dispatch.TrustedUtcExpiry,
            {"expires_at": datetime(2026, 11, 5, tzinfo=UTC), "source": source},
        )


def test_trusted_clock_protocol_is_typing_only_and_synchronous() -> None:
    assert issubclass(dispatch.TrustedDispatchClock, Protocol)
    assert dispatch.TrustedDispatchClock._is_protocol is True
    derive = inspect.signature(dispatch.TrustedDispatchClock.derive_deadline_nonblocking)
    observe = inspect.signature(dispatch.TrustedDispatchClock.observe_nonblocking)
    assert tuple(derive.parameters) == (
        "self",
        "utc_expiries",
        "monotonic_deadlines_ns",
        "expected_process_incarnation_id",
        "expected_clock_epoch",
    )
    assert tuple(observe.parameters) == (
        "self",
        "expected_process_incarnation_id",
        "expected_clock_epoch",
    )
    hints = get_type_hints(dispatch.TrustedDispatchClock.derive_deadline_nonblocking)
    assert hints["utc_expiries"] == tuple[dispatch.TrustedUtcExpiry, ...]
    assert hints["return"] is dispatch.TrustedDispatchDeadline
    assert not inspect.iscoroutinefunction(
        dispatch.TrustedDispatchClock.derive_deadline_nonblocking
    )
    assert not inspect.iscoroutinefunction(dispatch.TrustedDispatchClock.observe_nonblocking)


def test_venue_dispatch_audit_identity_binds_exact_attempt_and_route() -> None:
    place = dispatch.VenueDispatchAuditIdentity(**_audit_data())
    modify = dispatch.VenueDispatchAuditIdentity(**_audit_data(operation="MODIFY_ORDER"))
    assert place.logical_call_id == place.order_attempt_id
    assert place.route_id is PrivateRouteId.PLACE_ORDER
    assert modify.route_id is PrivateRouteId.MODIFY_ORDER
    assert place.policy_version == _POLICY
    assert tuple(type(place).model_fields) == (
        "account",
        "command_id",
        "order_id",
        "order_attempt_id",
        "root_event_id",
        "logical_call_id",
        "correlation_id",
        "order_attempt_number",
        "dispatch_event_id",
        "consumption_id",
        "operation",
        "route_id",
        "policy_version",
        "semantic_request_sha256",
        "command_evidence_sha256",
    )


@pytest.mark.parametrize(
    "change",
    (
        {"logical_call_id": UUID(int=30)},
        {"operation": "PLACE_ORDER", "route_id": PrivateRouteId.MODIFY_ORDER},
        {"operation": "MODIFY_ORDER", "route_id": PrivateRouteId.PLACE_ORDER},
        {"operation": "place_order"},
        {"route_id": "place_order"},
        {"policy_version": _HostileText(_POLICY)},
        {"order_attempt_number": True},
        {"semantic_request_sha256": "A" * 64},
    ),
)
def test_venue_dispatch_audit_identity_rejects_mismatches(
    change: dict[str, object],
) -> None:
    _assert_invalid(dispatch.VenueDispatchAuditIdentity, _audit_data() | change)


@pytest.mark.parametrize(
    ("region", "environment", "mode"),
    (
        (DeltaRegion.GLOBAL, DeltaEnvironment.TESTNET, ExecutionMode.LIVE),
        (DeltaRegion.INDIA, DeltaEnvironment.PROD, ExecutionMode.LIVE),
        (DeltaRegion.INDIA, DeltaEnvironment.TESTNET, ExecutionMode.PAPER),
        (DeltaRegion.INDIA, DeltaEnvironment.TESTNET, ExecutionMode.BACKTEST),
    ),
)
def test_dispatch_models_require_india_testnet_live_account(
    region: DeltaRegion,
    environment: DeltaEnvironment,
    mode: ExecutionMode,
) -> None:
    account = _account(region=region, environment=environment, mode=mode)
    _assert_invalid(dispatch.VenueDispatchAuditIdentity, _audit_data() | {"account": account})
    _assert_invalid(completion.LimiterWaitIntent, _intent_data() | {"account": account})


def test_limiter_wait_intent_and_binding_have_exact_safe_snapshots() -> None:
    intent = completion.LimiterWaitIntent(**_intent_data())
    modify_intent = completion.LimiterWaitIntent(**_intent_data(operation="MODIFY_ORDER"))
    binding = completion.LimiterWaitBinding(**_binding_data())
    assert intent.logical_call_id == intent.order_attempt_id
    assert intent.rest_weight == 5
    assert intent.product_operations == 1
    assert modify_intent.route_id is PrivateRouteId.MODIFY_ORDER
    assert binding.registry_owner_id == UUID(int=8)
    assert binding.cooldown_epoch == 0
    assert tuple(completion.LimiterWaitIntent.model_fields) == (
        "account",
        "command_id",
        "order_id",
        "order_attempt_id",
        "order_attempt_number",
        "root_event_id",
        "logical_call_id",
        "correlation_id",
        "operation",
        "route_id",
        "product_id",
        "rest_weight",
        "product_operations",
        "policy_version",
        "semantic_request_sha256",
        "command_evidence_sha256",
        "process_id",
        "process_incarnation_id",
    )
    assert tuple(completion.LimiterWaitBinding.model_fields)[-10:] == (
        "registry_owner_id",
        "limiter_instance_id",
        "held_reservation_id",
        "limiter_clock_epoch",
        "limiter_accounting_generation",
        "rest_window_id",
        "rest_window_end_monotonic_ns",
        "product_window_id",
        "product_window_end_monotonic_ns",
        "cooldown_epoch",
    )
    assert binding.model_dump_json().startswith('{"account":')


def test_dispatch_model_schemas_are_closed_required_field_manifests() -> None:
    models = (
        dispatch.TrustedDispatchInstant,
        dispatch.TrustedUtcExpiry,
        dispatch.TrustedDispatchDeadline,
        dispatch.VenueDispatchAuditIdentity,
        completion.LimiterWaitIntent,
        completion.LimiterWaitBinding,
    )
    for model in models:
        schema = model.model_json_schema()
        fields = tuple(model.model_fields)
        assert schema["additionalProperties"] is False
        assert tuple(schema["properties"]) == fields
        assert tuple(schema["required"]) == fields


@pytest.mark.parametrize(
    "change",
    (
        {"logical_call_id": UUID(int=31)},
        {"operation": "PLACE_ORDER", "route_id": PrivateRouteId.MODIFY_ORDER},
        {"operation": "MODIFY_ORDER", "route_id": PrivateRouteId.PLACE_ORDER},
        {"rest_weight": 4},
        {"rest_weight": True},
        {"product_operations": 0},
        {"policy_version": "delta-docs-future"},
        {"product_id": 0},
        {"process_id": 0},
        {"process_incarnation_id": str(UUID(int=7))},
    ),
)
def test_limiter_wait_intent_rejects_mismatch_or_coercion(
    change: dict[str, object],
) -> None:
    _assert_invalid(completion.LimiterWaitIntent, _intent_data() | change)


@pytest.mark.parametrize(
    "change",
    (
        {"registry_owner_id": UUID(int=9)},
        {"limiter_instance_id": UUID(int=10)},
        {"held_reservation_id": UUID(int=8)},
        {"rest_window_id": UUID(int=12)},
        {"limiter_clock_epoch": 0},
        {"limiter_accounting_generation": 0},
        {"rest_window_end_monotonic_ns": 0},
        {"product_window_end_monotonic_ns": 0},
        {"cooldown_epoch": -1},
    ),
)
def test_limiter_wait_binding_rejects_owner_event_or_epoch_mismatch(
    change: dict[str, object],
) -> None:
    _assert_invalid(completion.LimiterWaitBinding, _binding_data() | change)


def test_invalidation_reason_manifest_is_exact_and_closed() -> None:
    expected = (
        "WRONG_MODE_OR_TARGET",
        "EXPECTED_BINDING_MISMATCH",
        "VENUE_TRANSFER_TARGET_MISMATCH",
        "VENUE_TRANSFER_BINDING_MISMATCH",
        "VENUE_TRANSFER_REUSED",
        "D10_PRECONSUME_REJECTION",
        "DURABLE_REPLAY_NO_SEND",
        "DATABASE_FAILURE",
        "COMMIT_UNCERTAIN",
        "CANCELLED",
        "POST_COMMIT_CURRENTNESS_FAILED",
        "DEADLINE_DERIVATION_FAILED",
        "HANDOFF_ATTACHMENT_FAILED",
        "REGISTRY_CLOSED",
        "FORK_OR_PROCESS_MISMATCH",
        "LIMITER_REPLACED",
        "ACCOUNTING_EVENT_REPLACED",
        "LIMITER_CLOCK_INVALIDATED",
        "ACCOUNTING_WINDOW_EXPIRED",
        "COOLDOWN_ADVANCED",
        "INTERNAL_EXCEPTION",
    )
    enum_type = completion.LimiterReservationInvalidationReason
    assert tuple(member.name for member in enum_type) == expected
    assert tuple(member.value for member in enum_type) == expected
    adapter = TypeAdapter(enum_type)
    assert adapter.validate_python(enum_type.CANCELLED) is enum_type.CANCELLED
    for invalid in ("CANCELLED", "UNKNOWN", 1):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def _opaque_type_names() -> tuple[tuple[object, str], ...]:
    return (
        (dispatch, "_VenueDispatchOutcomeSlot"),
        (dispatch, "_VenueDispatchEntryLatch"),
        (dispatch, "_EnteredVenueDispatch"),
        (dispatch, "_VenueNoEntryDisposition"),
        (completion, "LimiterWaitCompleted"),
        (completion, "_HeldLimiterReservation"),
        (completion, "_CompletedPrivateMutationAcquisition"),
        (completion, "_RegisteredPrivateMutationAcquisition"),
        (completion, "_ConsumedLimiterWaitEvidence"),
        (completion, "_VenueLimiterReservationHandle"),
    )


@pytest.mark.parametrize(("module", "name"), _opaque_type_names())
def test_opaque_declarations_are_sealed_and_nonconstructible(
    module: object,
    name: str,
) -> None:
    opaque_type = getattr(module, name)
    assert opaque_type.__module__ in (
        "hmt_crypto.exchange.private_dispatch",
        "hmt_crypto.exchange.delta.private_limiter_completion",
    )
    assert opaque_type.__name__ == name
    assert opaque_type.__qualname__ == name
    with pytest.raises(TypeError, match="private dispatch declaration unavailable"):
        opaque_type()
    with pytest.raises(TypeError, match="private dispatch declarations cannot be subclassed"):
        type("Hostile", (opaque_type,), {})

    forged = object.__new__(opaque_type)
    assert repr(forged) == opaque_type.__name__
    with pytest.raises(TypeError):
        copy.copy(forged)
    with pytest.raises(TypeError):
        copy.deepcopy(forged)
    with pytest.raises(TypeError):
        pickle.dumps(forged)
    with pytest.raises(TypeError):
        hash(forged)
    with pytest.raises(TypeError):
        _ = forged == forged
    assert not hasattr(forged, "__dict__")
    allowed_evidence = {"wire_request_evidence_sha256"}
    assert not any(
        name not in allowed_evidence and fragment in name.casefold()
        for name in dir(forged)
        for fragment in ("send", "signer", "secret", "request", "transport", "client")
    )


def test_no_entry_disposition_declares_only_safe_read_only_evidence() -> None:
    expected = {
        "audit_identity": dispatch.VenueDispatchAuditIdentity,
        "last_phase": dispatch.VenueDispatchPreEntryPhase,
        "reason": dispatch.VenueNoEntryReason,
        "wire_request_evidence_sha256": str | None,
        "pre_dispatch_audit_receipt_id": UUID | None,
    }
    for name, return_type in expected.items():
        descriptor = inspect.getattr_static(dispatch._VenueNoEntryDisposition, name)
        assert isinstance(descriptor, property)
        assert get_type_hints(descriptor.fget)["return"] == return_type
        with pytest.raises(TypeError, match="private dispatch declaration unavailable"):
            getattr(object.__new__(dispatch._VenueNoEntryDisposition), name)


def test_internal_record_annotations_do_not_create_factories_or_authority() -> None:
    completed = get_type_hints(completion._CompletedPrivateMutationAcquisition)
    registered = get_type_hints(completion._RegisteredPrivateMutationAcquisition)
    assert completed == {
        "binding": completion.LimiterWaitBinding,
        "held_reservation": completion._HeldLimiterReservation,
    }
    assert registered == {
        "token": completion.LimiterWaitCompleted,
        "binding": completion.LimiterWaitBinding,
    }
    for module in (dispatch, completion):
        namespace = vars(module)
        assert not any(
            callable(value) and any(word in name.casefold() for word in ("mint", "factory"))
            for name, value in namespace.items()
        )


def test_package_private_protocols_have_exact_synchronous_shapes() -> None:
    protocols = (
        completion._D10LimiterWaitConsumePort,
        completion._D10LimiterReservationDispositionPort,
        completion._D10VenueHandoffTransferPort,
        completion._VenueLimiterEntryPort,
    )
    assert all(protocol._is_protocol is True for protocol in protocols)
    assert all(issubclass(protocol, Protocol) for protocol in protocols)
    expected_methods = {
        completion._D10LimiterWaitConsumePort: (
            "consume_or_invalidate_once",
            "invalidate_issued_once",
        ),
        completion._D10LimiterReservationDispositionPort: (
            "invalidate_once",
            "attach_to_venue_handoff_once",
        ),
        completion._D10VenueHandoffTransferPort: ("transfer_attached_once",),
        completion._VenueLimiterEntryPort: (
            "invalidate_before_entry_once",
            "finalize_and_enter_once",
        ),
    }
    for protocol, methods in expected_methods.items():
        assert tuple(name for name in protocol.__dict__ if not name.startswith("_")) == methods
        for method_name in methods:
            method = getattr(protocol, method_name)
            assert not inspect.iscoroutinefunction(method)
            hints = get_type_hints(method)
            assert "return" in hints
            assert all(annotation is not object for annotation in hints.values())

    transfer = get_type_hints(completion._D10VenueHandoffTransferPort.transfer_attached_once)[
        "return"
    ]
    assert "TRANSFERRED" in str(transfer)
    assert "ALREADY_ENTERED" in str(transfer)
    assert "_VenueNoEntryDisposition" in str(transfer)
    entry = get_type_hints(completion._VenueLimiterEntryPort.finalize_and_enter_once)
    assert entry["clock"] is dispatch.TrustedDispatchClock
    assert entry["signed_at"] is dispatch.TrustedDispatchInstant
    assert entry["deadline"] is dispatch.TrustedDispatchDeadline
    assert "_EnteredVenueDispatch" in str(entry["return"])
    assert "_VenueNoEntryDisposition" in str(entry["return"])


def test_modules_are_inert_acyclic_and_absent_from_package_roots() -> None:
    project = Path(__file__).resolve().parents[2]
    dispatch_tree = ast.parse((project / "src/hmt_crypto/exchange/private_dispatch.py").read_text())
    completion_tree = ast.parse(
        (project / "src/hmt_crypto/exchange/delta/private_limiter_completion.py").read_text()
    )
    forbidden_roots = {
        "asyncio",
        "httpx",
        "logging",
        "os",
        "socket",
        "sqlalchemy",
        "time",
    }
    forbidden_fragments = (
        "worker",
        "runtime",
        "persistence",
        "signer",
        "client",
        "transport",
        "limiter_accounting",
        "rate_limit_policy",
        "weighted_limiter",
        "api_audit",
        "d10",
        "private_dispatch_handoff",
    )
    for tree in (dispatch_tree, completion_tree):
        imports = [
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert forbidden_roots.isdisjoint(name.split(".")[0] for name in imports)
        assert not any(fragment in name for name in imports for fragment in forbidden_fragments)
        assert not any(
            isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree)
        )

    import hmt_crypto.exchange as exchange_root
    import hmt_crypto.exchange.delta as delta_root

    denied = {
        "TrustedDispatchInstant",
        "TrustedDispatchDeadline",
        "LimiterWaitCompleted",
        "LimiterWaitBinding",
        "LimiterReservationInvalidationReason",
    }
    assert denied.isdisjoint(exchange_root.__all__)
    assert denied.isdisjoint(delta_root.__all__)


@pytest.mark.parametrize(
    "imports",
    (
        (
            "hmt_crypto.exchange.private_dispatch",
            "hmt_crypto.exchange.delta.private_limiter_completion",
        ),
        (
            "hmt_crypto.exchange.delta.private_limiter_completion",
            "hmt_crypto.exchange.private_dispatch",
        ),
    ),
)
def test_dispatch_and_completion_import_cleanly_in_both_fresh_process_orders(
    imports: tuple[str, str],
) -> None:
    project = Path(__file__).resolve().parents[2]
    source = textwrap.dedent(
        f"""
        import importlib
        import sys
        sys.path.insert(0, "src")
        for name in {imports!r}:
            importlib.import_module(name)
        forbidden = (
            "hmt_crypto.worker.clock",
            "hmt_crypto.exchange.delta.client",
            "hmt_crypto.exchange.delta.signer",
            "hmt_crypto.persistence.db",
        )
        assert not any(name in sys.modules for name in forbidden)
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", source],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
