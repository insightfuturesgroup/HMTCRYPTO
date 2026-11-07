"""Atomic owner, hostile-binding, and two-task race proofs for B3."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.unit


def _run_isolated(body: str) -> None:
    project = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    case_body = textwrap.indent(textwrap.dedent(body).strip(), "    ")
    source = textwrap.dedent(
        """
        import asyncio
        import sys
        from uuid import UUID
        sys.path.insert(0, ".")
        sys.path.insert(0, "src")
        from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
        from hmt_crypto.errors import ConfigurationError
        from hmt_crypto.exchange.delta import private_limiter_registry as registry_module
        from hmt_crypto.exchange.delta import weighted_limiter as limiter_module
        from hmt_crypto.exchange.delta.private_limiter_completion import (
            LimiterReservationInvalidationReason as Reason,
        )
        from hmt_crypto.exchange.delta.rate_limit_policy import DELTA_RATE_LIMIT_POLICY
        from hmt_crypto.exchange.private_dispatch import (
            _EnteredVenueDispatch,
            _VenueNoEntryDisposition,
        )
        from tests.integration.support import task210_worker_clock_issuer as issuer
        from tests.unit import delta_private_limiter_test_support as support
        class FakeMonotonic:
            def __init__(self):
                self.value = 0.0

            def __call__(self):
                return self.value
        async def main():
            fake = FakeMonotonic()
            limiter_module.time.monotonic = fake
            incarnation = UUID(int=980)
            proof, currentness = support.issue_worker(issuer._FIXTURE_MARKER, incarnation)
            registry = registry_module._compose_private_limiter_registry(
                ownership=proof,
                ownership_currentness=currentness,
                process_incarnation_id=incarnation,
                policy=DELTA_RATE_LIMIT_POLICY,
            )
            limiter = registry._limiter
            store = registry._lifecycle
            async def acquire(ordinal, moment):
                fake.value = moment
                intent = support.make_intent(incarnation, ordinal=ordinal)
                slot = support.issue_outcome_slot(support.outcome_marker(), intent)
                acquisition = await asyncio.wait_for(
                    registry.acquire_mutation_once(intent=intent, outcome_slot=slot),
                    timeout=1,
                )
                state = object.__getattribute__(acquisition.token, "_grant")
                return acquisition, support.entry_latch(slot), state
            def consume(acquisition, expected=None):
                return registry.consume_or_invalidate_once(
                    acquisition.token,
                    expected=acquisition.binding if expected is None else expected,
                    actual_region=DeltaRegion.INDIA,
                    actual_environment=DeltaEnvironment.TESTNET,
                    actual_mode=ExecutionMode.LIVE,
                )
            def attach(acquisition, latch):
                return registry.attach_to_venue_handoff_once(
                    consume(acquisition), entry_latch=latch
                )
            def audit(handle, latch, wire="a" * 64, receipt=UUID(int=981)):
                registry.advance_pre_entry_once(
                    handle,
                    entry_latch=latch,
                    phase="CLAIMED_FOR_UNSIGNED_PREPARATION",
                )
                registry.advance_pre_entry_once(
                    handle,
                    entry_latch=latch,
                    phase="CLAIMED_FOR_UNSIGNED_PREPARATION",
                    wire_request_evidence_sha256=wire,
                )
                registry.advance_pre_entry_once(
                    handle,
                    entry_latch=latch,
                    phase="AUDIT_RECORDED",
                    wire_request_evidence_sha256=wire,
                    pre_dispatch_audit_receipt_id=receipt,
                )

            def finalize(acquisition, handle, latch, moment, expected=None):
                clock, clock_currentness = support.issue_clock(
                    issuer._FIXTURE_MARKER,
                    incarnation,
                    observed_monotonic_ns=moment,
                )
                signed = clock.observe_nonblocking(
                    expected_process_incarnation_id=incarnation,
                    expected_clock_epoch=1,
                )
                deadline = clock.derive_deadline_nonblocking(
                    utc_expiries=(),
                    monotonic_deadlines_ns=(moment + 800_000_000,),
                    expected_process_incarnation_id=incarnation,
                    expected_clock_epoch=1,
                )
                return registry.finalize_and_enter_once(
                    handle,
                    expected=acquisition.binding if expected is None else expected,
                    entry_latch=latch,
                    clock=clock,
                    clock_currentness=clock_currentness,
                    signed_at=signed,
                    deadline=deadline,
                    signed_timestamp=1_800_000_000,
                )

            async def ordered_race(first, left, right):
                release = asyncio.Event()
                both_ready = asyncio.Event()
                ready_count = 0

                async def run(operation):
                    nonlocal ready_count
                    ready_count += 1
                    if ready_count == 2:
                        both_ready.set()
                    await release.wait()
                    try:
                        value = operation()
                        if asyncio.iscoroutine(value):
                            value = await value
                        return "RETURNED", value
                    except BaseException as error:
                        return "RAISED", type(error)

                ordered = (left, right) if first == "left" else (right, left)
                first_task = asyncio.create_task(run(ordered[0]))
                second_task = asyncio.create_task(run(ordered[1]))
                await asyncio.wait_for(both_ready.wait(), timeout=1)
                release.set()
                outcomes = await asyncio.wait_for(
                    asyncio.gather(first_task, second_task), timeout=1
                )
                return tuple(outcomes) if first == "left" else tuple(reversed(outcomes))

            __CASE_BODY__

        asyncio.run(main())
        print("ok")
        """
    ).replace("    __CASE_BODY__", case_body)
    result = subprocess.run(
        [sys.executable, "-I", "-c", source],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_owner_phase_evidence_matrix_is_copied_into_canonical_disposition() -> None:
    _run_isolated(
        """
        first, first_latch, first_state = await acquire(1, 300.0)
        first_handle = attach(first, first_latch)
        issued = registry.invalidate_before_entry_once(
            first_handle, entry_latch=first_latch, reason="CANCELLED"
        )
        assert issued.last_phase == "ISSUED"
        assert issued.wire_request_evidence_sha256 is None
        assert issued.pre_dispatch_audit_receipt_id is None

        second, second_latch, second_state = await acquire(2, 301.0)
        second_handle = attach(second, second_latch)
        registry.advance_pre_entry_once(
            second_handle,
            entry_latch=second_latch,
            phase="CLAIMED_FOR_UNSIGNED_PREPARATION",
        )
        registry.advance_pre_entry_once(
            second_handle,
            entry_latch=second_latch,
            phase="CLAIMED_FOR_UNSIGNED_PREPARATION",
            wire_request_evidence_sha256="b" * 64,
        )
        claimed = registry.invalidate_before_entry_once(
            second_handle, entry_latch=second_latch, reason="PREPARATION_FAILED"
        )
        assert claimed.last_phase == "CLAIMED_FOR_UNSIGNED_PREPARATION"
        assert claimed.wire_request_evidence_sha256 == "b" * 64
        assert claimed.pre_dispatch_audit_receipt_id is None

        third, third_latch, third_state = await acquire(3, 302.0)
        third_handle = attach(third, third_latch)
        audit(third_handle, third_latch, wire="c" * 64, receipt=UUID(int=982))
        audited = registry.invalidate_before_entry_once(
            third_handle, entry_latch=third_latch, reason="AUDIT_FAILED"
        )
        assert audited.last_phase == "AUDIT_RECORDED"
        assert audited.wire_request_evidence_sha256 == "c" * 64
        assert audited.pre_dispatch_audit_receipt_id == UUID(int=982)
        assert registry.invalidate_before_entry_once(
            third_handle, entry_latch=third_latch, reason="INTERNAL_EXCEPTION"
        ) is audited

        fourth, fourth_latch, fourth_state = await acquire(4, 303.0)
        fourth_handle = attach(fourth, fourth_latch)
        try:
            registry.advance_pre_entry_once(
                fourth_handle,
                entry_latch=fourth_latch,
                phase="AUDIT_RECORDED",
                wire_request_evidence_sha256="d" * 64,
                pre_dispatch_audit_receipt_id=UUID(int=983),
            )
        except ConfigurationError:
            pass
        else:
            raise AssertionError("ISSUED skipped directly to AUDIT_RECORDED")
        assert fourth_state.disposition.last_phase == "ISSUED"
        assert fourth_state.disposition.reason == "INTERNAL_EXCEPTION"
        """
    )


def test_hostile_expected_binding_callbacks_never_run_under_gate_or_leave_orphan() -> None:
    _run_isolated(
        """
        callbacks = []

        class HostileIdentity:
            def __eq__(self, _other):
                callbacks.append("equality")
                assert not limiter._accounting_gate.held_by_current_thread()
                raise RuntimeError("hostile equality callback")

        first, first_latch, first_state = await acquire(1, 300.0)
        hostile_consume = first.binding.model_copy(deep=True)
        object.__setattr__(hostile_consume, "command_id", HostileIdentity())
        try:
            consume(first, hostile_consume)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("hostile consumed")
        assert first_state.disposition.reason == "LIMITER_INVALIDATED"
        assert first_state.token_state == first_state.reservation_state == "INVALIDATED"

        second, second_latch, second_state = await acquire(2, 301.0)
        second_handle = attach(second, second_latch)
        audit(second_handle, second_latch)
        assert registry.transfer_attached_once(
            second_handle, entry_latch=second_latch, rejection_reason=None
        ) == "TRANSFERRED"
        hostile_entry = second.binding.model_copy(deep=True)
        object.__setattr__(hostile_entry, "command_id", HostileIdentity())
        disposition = finalize(
            second, second_handle, second_latch, 301_100_000_000, hostile_entry
        )
        assert type(disposition) is _VenueNoEntryDisposition
        assert disposition.reason == "SINK_BINDING_MISMATCH"
        assert second_state.disposition is disposition
        assert second_state.reservation_state == "INVALIDATED"
        assert registry.transfer_attached_once(
            second_handle, entry_latch=second_latch, rejection_reason=None
        ) is disposition

        third, third_latch, third_state = await acquire(3, 302.0)
        third_handle = attach(third, third_latch)
        audit(third_handle, third_latch, receipt=UUID(int=982))
        assert registry.transfer_attached_once(
            third_handle, entry_latch=third_latch, rejection_reason=None
        ) == "TRANSFERRED"
        clean_expected = third.binding.model_copy(deep=True)
        object.__setattr__(third_state.binding, "command_id", HostileIdentity())
        corrupted = finalize(
            third,
            third_handle,
            third_latch,
            302_100_000_000,
            clean_expected,
        )
        assert type(corrupted) is _VenueNoEntryDisposition
        assert third_state.disposition is corrupted
        assert third_state.reservation_state == "INVALIDATED"
        assert callbacks == []
        """
    )


_PARITY_CASES = (
    ("consume", "state.registration", "registry_owner_id", "UUID(int=991)"),
    ("consume", "state.binding", "registry_owner_id", "UUID(int=992)"),
    ("consume", "state.registration", "registry_open_epoch", "2"),
    ("consume", "state.registration", "ownership_proof_id", "UUID(int=993)"),
    ("attach", "state.registration", "limiter_instance_id", "UUID(int=994)"),
    ("attach", "state.binding", "limiter_instance_id", "UUID(int=995)"),
    ("attach", "state.binding", "held_reservation_id", "UUID(int=996)"),
    ("attach", "state.registration", "held_reservation_id", "UUID(int=1002)"),
    ("attach", "state.registration", "rest_event_id", "UUID(int=997)"),
    ("attach", "state.binding", "rest_window_id", "UUID(int=1003)"),
    ("attach", "state.registration", "product_event_id", "UUID(int=1004)"),
    ("transfer", "state.binding", "product_window_id", "UUID(int=998)"),
    ("transfer", "state.registration", "limiter_clock_epoch", "2"),
    ("transfer", "state.binding", "limiter_clock_epoch", "2"),
    ("transfer", "state.registration", "limiter_accounting_generation", "2"),
    ("transfer", "state.binding", "limiter_accounting_generation", "2"),
    ("transfer", "state.registration", "cooldown_epoch", "1"),
    ("transfer", "state.binding", "cooldown_epoch", "1"),
    ("entry", "state.registration", "process_id", "state.registration.process_id + 1"),
    ("entry", "state.binding", "process_id", "state.binding.process_id + 1"),
    ("entry", "state.registration", "process_incarnation_id", "UUID(int=1005)"),
    ("entry", "state.binding", "process_incarnation_id", "UUID(int=999)"),
    ("entry", "state.registration.intent", "command_id", "UUID(int=1001)"),
    ("entry", "state.registration.intent", "order_id", "UUID(int=1006)"),
    ("entry", "state.registration.intent", "product_id", "28"),
    ("entry", "state.registration.intent", "rest_weight", "6"),
    ("entry", "state.registration.intent", "product_operations", "2"),
    ("entry", "state.registration.intent", "semantic_request_sha256", "'c' * 64"),
    ("entry", "state.registration.intent", "command_evidence_sha256", "'d' * 64"),
    ("entry", "state.registration.intent.account", "account_id", "UUID(int=1007)"),
    ("entry", "state.registration.intent.account", "credential_version", "2"),
    ("entry", "state.registration.intent.account", "region", "DeltaRegion.GLOBAL"),
    (
        "entry",
        "state.registration.intent.account",
        "environment",
        "DeltaEnvironment.PROD",
    ),
    ("entry", "state.registration.intent.account", "mode", "ExecutionMode.PAPER"),
    ("entry", "state.binding.account", "external_user_id", "'54321'"),
    (
        "entry",
        "state.binding",
        "rest_window_end_monotonic_ns",
        "state.binding.rest_window_end_monotonic_ns + 1",
    ),
    (
        "entry",
        "state.binding",
        "product_window_end_monotonic_ns",
        "state.binding.product_window_end_monotonic_ns + 1",
    ),
    ("entry", "state.registration.intent", "policy_version", "'replacement-policy'"),
)


@pytest.mark.parametrize(("stage", "owner", "field", "replacement"), _PARITY_CASES)
def test_every_frozen_currentness_scalar_mismatch_terminalizes_under_gate(
    stage: str,
    owner: str,
    field: str,
    replacement: str,
) -> None:
    _run_isolated(
        f"""
        acquisition, latch, state = await acquire(1, 300.0)
        evidence = handle = None
        if {stage!r} != "consume":
            evidence = consume(acquisition)
        if {stage!r} in ("transfer", "entry"):
            handle = registry.attach_to_venue_handoff_once(evidence, entry_latch=latch)
        if {stage!r} == "entry":
            audit(handle, latch)
            assert registry.transfer_attached_once(
                handle, entry_latch=latch, rejection_reason=None
            ) == "TRANSFERRED"
        object.__setattr__({owner}, {field!r}, {replacement})
        try:
            if {stage!r} == "consume":
                consume(acquisition)
            elif {stage!r} == "attach":
                registry.attach_to_venue_handoff_once(evidence, entry_latch=latch)
            elif {stage!r} == "transfer":
                registry.transfer_attached_once(
                    handle, entry_latch=latch, rejection_reason=None
                )
            else:
                finalize(acquisition, handle, latch, 300_100_000_000)
        except ConfigurationError:
            pass
        assert state.disposition is not None
        assert state.entered is None
        assert state.reservation_state == "INVALIDATED"
        assert state.terminal_reason in (
            Reason.LIMITER_REPLACED,
            Reason.ACCOUNTING_EVENT_REPLACED,
            Reason.LIMITER_CLOCK_INVALIDATED,
        ), state.terminal_reason
        assert sum(event.amount for event in limiter._rest_events) == 5
        assert store._grants[state.registration.registration_id] is state
        assert store._outcomes[id(state.registration.outcome_slot)] is state
        """
    )


@pytest.mark.parametrize("first", ("left", "right"))
@pytest.mark.parametrize("hazard", ("close", "429"))
def test_close_or_429_vs_entry_has_one_atomic_terminal_winner(
    hazard: str,
    first: str,
) -> None:
    _run_isolated(
        f"""
        acquisition, latch, state = await acquire(1, 300.0)
        handle = attach(acquisition, latch)
        audit(handle, latch)
        assert registry.transfer_attached_once(
            handle, entry_latch=latch, rejection_reason=None
        ) == "TRANSFERRED"
        hazard = (
            registry.close
            if {hazard!r} == "close"
            else lambda: limiter.observe_429("1")
        )
        left, right = await ordered_race(
            {first!r},
            hazard,
            lambda: finalize(acquisition, handle, latch, 300_100_000_000),
        )
        assert left[0] == right[0] == "RETURNED"
        terminal = state.entered or state.disposition
        assert terminal is not None
        assert (state.entered is None) != (state.disposition is None)
        assert right[1] is terminal
        if {first!r} == "left":
            assert type(terminal) is _VenueNoEntryDisposition
            assert state.reservation_state == "INVALIDATED"
        else:
            assert type(terminal) is _EnteredVenueDispatch
            assert state.reservation_state == "FINALIZED_FOR_ENTRY"
        assert finalize(
            acquisition, handle, latch, 300_200_000_000
        ) is terminal
        assert sum(event.amount for event in limiter._rest_events) == 5
        assert len(limiter._event_index) == 2
        assert store._tokens[acquisition.token._token_id] is state
        assert store._outcomes[id(state.registration.outcome_slot)] is state
        """
    )


@pytest.mark.parametrize("first", ("left", "right"))
@pytest.mark.parametrize("stage", ("consume", "attach", "entry"))
def test_exact_expiry_prune_vs_lifecycle_has_one_bounded_winner(
    stage: str,
    first: str,
) -> None:
    _run_isolated(
        f"""
        acquisition, latch, state = await acquire(1, 300.0)
        evidence = handle = None
        if {stage!r} != "consume":
            evidence = consume(acquisition)
        if {stage!r} == "entry":
            handle = registry.attach_to_venue_handoff_once(evidence, entry_latch=latch)
            audit(handle, latch)
            assert registry.transfer_attached_once(
                handle, entry_latch=latch, rejection_reason=None
            ) == "TRANSFERRED"

        async def prune_at_equality():
            fake.value = 301.0
            return await limiter.snapshot()

        if {stage!r} == "consume":
            operation = lambda: consume(acquisition)
        elif {stage!r} == "attach":
            operation = lambda: registry.attach_to_venue_handoff_once(
                evidence, entry_latch=latch
            )
        else:
            operation = lambda: finalize(
                acquisition, handle, latch, 300_100_000_000
            )
        fake.value = 300.5
        left, right = await ordered_race(
            {first!r}, prune_at_equality, operation
        )
        assert left[0] == "RETURNED"
        if {first!r} == "left" and {stage!r} != "entry":
            assert right == ("RAISED", ConfigurationError)
        else:
            assert right[0] == "RETURNED"
        terminal = state.entered or state.disposition
        assert terminal is not None
        assert (state.entered is None) != (state.disposition is None)
        assert state.reservation_state in ("INVALIDATED", "FINALIZED_FOR_ENTRY")
        assert sum(event.amount for event in limiter._rest_events) == 5
        assert acquisition.binding.product_window_id not in limiter._event_index
        assert acquisition.binding.rest_window_id in limiter._event_index
        assert store._tokens[acquisition.token._token_id] is state
        assert store._outcomes[id(state.registration.outcome_slot)] is state
        """
    )


@pytest.mark.parametrize("first", ("left", "right"))
@pytest.mark.parametrize("stage", ("consume", "entry"))
@pytest.mark.parametrize("hazard", ("accounting", "clock", "limiter", "worker"))
def test_replacement_or_poison_vs_lifecycle_is_atomic(
    hazard: str,
    stage: str,
    first: str,
) -> None:
    _run_isolated(
        f"""
        acquisition, latch, state = await acquire(1, 300.0)
        handle = None
        if {stage!r} == "entry":
            handle = attach(acquisition, latch)
            audit(handle, latch)
            assert registry.transfer_attached_once(
                handle, entry_latch=latch, rejection_reason=None
            ) == "TRANSFERRED"
        limiter_id = limiter._limiter_instance_id

        def poison_accounting():
            with limiter._accounting_gate.hold():
                limiter._poison_accounting_locked()

        def poison_clock():
            with limiter._accounting_gate.hold():
                limiter._poison_clock_locked()

        def replace_worker():
            replacement = support.replace_worker(
                issuer._FIXTURE_MARKER, currentness, proof
            )
            return registry_module._replace_private_limiter_worker_proof_nonblocking(
                registry,
                current=proof,
                replacement=replacement,
                currentness=currentness,
            )

        hazards = {{
            "accounting": poison_accounting,
            "clock": poison_clock,
            "limiter": limiter.close,
            "worker": replace_worker,
        }}
        operation = (
            (lambda: consume(acquisition))
            if {stage!r} == "consume"
            else lambda: finalize(acquisition, handle, latch, 300_100_000_000)
        )
        left, right = await ordered_race(
            {first!r}, hazards[{hazard!r}], operation
        )
        assert left[0] == "RETURNED"
        if {first!r} == "left" and {stage!r} == "consume":
            assert right == ("RAISED", ConfigurationError)
        else:
            assert right[0] == "RETURNED"
        terminal = state.entered or state.disposition
        assert terminal is not None
        assert (state.entered is None) != (state.disposition is None)
        assert state.reservation_state in ("INVALIDATED", "FINALIZED_FOR_ENTRY")
        assert limiter._limiter_instance_id == limiter_id
        assert sum(event.amount for event in limiter._rest_events) == 5
        assert len(limiter._event_index) == 2
        assert store._tokens[acquisition.token._token_id] is state
        assert store._outcomes[id(state.registration.outcome_slot)] is state
        """
    )


@pytest.mark.parametrize("first", ("left", "right"))
@pytest.mark.parametrize("race", ("consume_invalidate", "attach_invalidate"))
def test_consume_or_attach_vs_invalidation_has_no_orphan(
    race: str,
    first: str,
) -> None:
    _run_isolated(
        f"""
        acquisition, latch, state = await acquire(1, 300.0)
        evidence = consume(acquisition) if {race!r} == "attach_invalidate" else None
        if {race!r} == "consume_invalidate":
            left_operation = lambda: consume(acquisition)
            right_operation = lambda: registry.invalidate_issued_once(
                acquisition.token, reason=Reason.D10_PRECONSUME_REJECTION
            )
        else:
            left_operation = lambda: registry.attach_to_venue_handoff_once(
                evidence, entry_latch=latch
            )
            right_operation = lambda: registry.invalidate_once(
                evidence, reason=Reason.DURABLE_REPLAY_NO_SEND
            )
        left, right = await ordered_race(
            {first!r}, left_operation, right_operation
        )
        if {first!r} == "left":
            assert left[0] == "RETURNED"
            assert right == ("RAISED", ConfigurationError)
            if {race!r} == "consume_invalidate":
                registry.invalidate_once(
                    left[1], reason=Reason.DURABLE_REPLAY_NO_SEND
                )
            else:
                registry.invalidate_before_entry_once(
                    left[1], entry_latch=latch, reason="CANCELLED"
                )
        else:
            assert right[0] == "RETURNED"
            assert left == ("RAISED", ConfigurationError)
        disposition = state.disposition
        assert type(disposition) is _VenueNoEntryDisposition
        assert state.entered is None
        assert state.reservation_state == "INVALIDATED"
        with limiter._accounting_gate.hold():
            assert store.invalidate(
                state.registration, Reason.INTERNAL_EXCEPTION
            ) is disposition
        assert sum(event.amount for event in limiter._rest_events) == 5
        assert len(limiter._event_index) == 2
        assert store._tokens[acquisition.token._token_id] is state
        assert store._outcomes[id(state.registration.outcome_slot)] is state
        """
    )


@pytest.mark.parametrize("first", ("left", "right"))
def test_transfer_vs_entry_has_one_terminal_result(first: str) -> None:
    _run_isolated(
        f"""
        acquisition, latch, state = await acquire(1, 300.0)
        handle = attach(acquisition, latch)
        audit(handle, latch)
        left, right = await ordered_race(
            {first!r},
            lambda: registry.transfer_attached_once(
                handle, entry_latch=latch, rejection_reason=None
            ),
            lambda: finalize(acquisition, handle, latch, 300_100_000_000),
        )
        assert left[0] == right[0] == "RETURNED"
        terminal = state.entered or state.disposition
        assert terminal is not None
        assert (state.entered is None) != (state.disposition is None)
        if {first!r} == "left":
            assert left[1] == "TRANSFERRED"
            assert right[1] is terminal
            assert type(terminal) is _EnteredVenueDispatch
        else:
            assert left[1] is right[1] is terminal
            assert type(terminal) is _VenueNoEntryDisposition
        assert finalize(
            acquisition, handle, latch, 300_200_000_000
        ) is terminal
        assert sum(event.amount for event in limiter._rest_events) == 5
        assert len(limiter._event_index) == 2
        assert store._handles[id(handle)] is state
        """
    )


@pytest.mark.parametrize("first", ("left", "right"))
def test_latch_to_gate_reverse_task_fails_before_gate(first: str) -> None:
    _run_isolated(
        f"""
        acquisition, latch, state = await acquire(1, 300.0)
        handle = attach(acquisition, latch)
        owner = state.latch_owner
        gate_type = type(limiter._accounting_gate)
        original_hold = gate_type.hold

        def reverse():
            def forbidden_hold(_owner):
                raise AssertionError("reverse attempt reached accounting gate")

            owner._lock.acquire(blocking=False)
            object.__setattr__(
                owner, "_active_holder", __import__("threading").get_ident()
            )
            gate_type.hold = forbidden_hold
            try:
                return registry.transfer_attached_once(
                    handle, entry_latch=latch, rejection_reason=None
                )
            finally:
                gate_type.hold = original_hold
                object.__setattr__(owner, "_active_holder", None)
                owner._lock.release()

        left, right = await ordered_race({first!r}, reverse, lambda: None)
        assert left == ("RAISED", ConfigurationError)
        assert right == ("RETURNED", None)
        assert state.reservation_state == "ATTACHED_TO_VENUE_HANDOFF"
        assert state.disposition is None and state.entered is None
        assert owner._transfer_used is False
        assert registry.invalidate_before_entry_once(
            handle, entry_latch=latch, reason="CANCELLED"
        ) is state.disposition
        """
    )


@pytest.mark.parametrize("first", ("left", "right"))
@pytest.mark.parametrize("hazard", ("clock", "worker"))
def test_clock_or_worker_replacement_vs_grant_preserves_one_registration(
    hazard: str,
    first: str,
) -> None:
    _run_isolated(
        f"""
        intent = support.make_intent(incarnation, ordinal=1)
        slot = support.issue_outcome_slot(support.outcome_marker(), intent)
        fake.value = 300.0

        def poison_clock():
            with limiter._accounting_gate.hold():
                limiter._poison_clock_locked()

        def replace_worker():
            replacement = support.replace_worker(
                issuer._FIXTURE_MARKER, currentness, proof
            )
            return registry_module._replace_private_limiter_worker_proof_nonblocking(
                registry,
                current=proof,
                replacement=replacement,
                currentness=currentness,
            )

        operation = lambda: registry.acquire_mutation_once(
            intent=intent, outcome_slot=slot
        )
        left, right = await ordered_race(
            {first!r},
            poison_clock if {hazard!r} == "clock" else replace_worker,
            operation,
        )
        assert left[0] == "RETURNED"
        state = store._outcomes[id(slot)]
        if {hazard!r} == "clock" and {first!r} == "left":
            assert right == ("RAISED", ConfigurationError)
            assert state.token_state == state.reservation_state == "UNISSUED"
            assert not limiter._event_index
        else:
            assert right[0] == "RETURNED"
            assert sum(event.amount for event in limiter._rest_events) == 5
            assert len(limiter._event_index) == 2
            if state.disposition is None:
                registry.invalidate_issued_once(
                    right[1].token, reason=Reason.D10_PRECONSUME_REJECTION
                )
        disposition = state.disposition
        assert type(disposition) is _VenueNoEntryDisposition
        assert state.entered is None
        assert store._grants[state.registration.registration_id] is state
        assert store._tokens[state.registration.token_id] is state
        assert store._reservations[state.registration.held_reservation_id] is state
        assert store._outcomes[id(slot)] is state
        try:
            await asyncio.wait_for(
                registry.acquire_mutation_once(intent=intent, outcome_slot=slot),
                timeout=1,
            )
        except ConfigurationError:
            pass
        else:
            raise AssertionError("terminal slot was reissued")
        assert store._outcomes[id(slot)].disposition is disposition
        """
    )


def test_atomic_race_releases_both_ready_contenders_together() -> None:
    _run_isolated(
        """
        both_started = asyncio.Event()
        arrivals = 0

        async def contender():
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return arrivals

        left, right = await ordered_race("left", contender, contender)
        assert left == right == ("RETURNED", 2)
        """
    )
