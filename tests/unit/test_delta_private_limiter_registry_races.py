"""Isolated deterministic two-winner races for B3 private limiter lifecycle."""

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
            incarnation = UUID(int=880)
            proof, currentness = support.issue_worker(issuer._FIXTURE_MARKER, incarnation)
            registry = registry_module._compose_private_limiter_registry(
                ownership=proof,
                ownership_currentness=currentness,
                process_incarnation_id=incarnation,
                policy=DELTA_RATE_LIMIT_POLICY,
            )
            limiter = object.__getattribute__(registry, "_limiter")
            store = object.__getattribute__(registry, "_lifecycle")

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

            def consume(acquisition):
                return registry.consume_or_invalidate_once(
                    acquisition.token,
                    expected=acquisition.binding,
                    actual_region=DeltaRegion.INDIA,
                    actual_environment=DeltaEnvironment.TESTNET,
                    actual_mode=ExecutionMode.LIVE,
                )

            def finalize(acquisition, handle, latch, moment):
                state = object.__getattribute__(acquisition.token, "_grant")
                if state.phase == "ISSUED":
                    support.advance_audited(
                        registry,
                        handle,
                        latch,
                        ordinal=acquisition.binding.order_attempt_number,
                    )
                clock, clock_currentness = support.issue_clock(
                    issuer._FIXTURE_MARKER,
                    incarnation,
                    observed_monotonic_ns=moment,
                )
                signed_at = clock.observe_nonblocking(
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
                    expected=acquisition.binding,
                    entry_latch=latch,
                    clock=clock,
                    clock_currentness=clock_currentness,
                    signed_at=signed_at,
                    deadline=deadline,
                    signed_timestamp=1_800_000_000,
                )

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


def test_close_wins_queued_and_post_publication_without_refund_or_orphan() -> None:
    _run_isolated(
        """
        queued_at_gate = asyncio.Event()
        release_gate = asyncio.Event()

        async def barrier(_owner):
            queued_at_gate.set()
            await release_gate.wait()

        limiter._before_grant_entry_barrier = barrier.__get__(limiter)
        fake.value = 300.0
        intent = support.make_intent(incarnation, ordinal=1)
        slot = support.issue_outcome_slot(support.outcome_marker(), intent)
        task = asyncio.create_task(
            registry.acquire_mutation_once(intent=intent, outcome_slot=slot)
        )
        await asyncio.wait_for(queued_at_gate.wait(), timeout=1)
        registry.close()
        release_gate.set()
        try:
            await asyncio.wait_for(task, timeout=1)
        except Exception:
            pass
        else:
            raise AssertionError("closed queued grant delivered")
        state = store._outcomes[id(slot)]
        assert state.registration_state == "INVALIDATED"
        assert state.token_state == state.reservation_state == "UNISSUED"
        assert state.disposition.reason == "LIMITER_INVALIDATED"
        assert sum(event.amount for event in limiter._rest_events) == 0
        assert not limiter._event_index
        """
    )


def test_consume_attach_and_entry_each_lose_at_exact_product_expiry() -> None:
    _run_isolated(
        """
        first, first_latch, first_state = await acquire(1, 300.0)
        fake.value = 301.0
        try:
            consume(first)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("expiry equality consumed")
        assert first_state.terminal_reason is Reason.ACCOUNTING_WINDOW_EXPIRED

        second, second_latch, second_state = await acquire(2, 301.0)
        evidence = consume(second)
        fake.value = 302.0
        try:
            registry.attach_to_venue_handoff_once(evidence, entry_latch=second_latch)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("expiry equality attached")
        assert second_state.terminal_reason is Reason.ACCOUNTING_WINDOW_EXPIRED

        third, third_latch, third_state = await acquire(3, 302.0)
        evidence = consume(third)
        handle = registry.attach_to_venue_handoff_once(evidence, entry_latch=third_latch)
        assert registry.transfer_attached_once(
            handle, entry_latch=third_latch, rejection_reason=None
        ) == "TRANSFERRED"
        support.advance_audited(registry, handle, third_latch, ordinal=3)
        fake.value = 303.0
        result = finalize(third, handle, third_latch, 303_000_000_000)
        assert type(result) is _VenueNoEntryDisposition
        assert result.reason == "LIMITER_INVALIDATED"
        assert third_state.terminal_reason is Reason.ACCOUNTING_WINDOW_EXPIRED
        assert third_state.disposition is result
        """
    )


def test_close_and_entry_each_win_one_linearization_order() -> None:
    _run_isolated(
        """
        acquisition, latch, state = await acquire(1, 300.0)
        handle = registry.attach_to_venue_handoff_once(consume(acquisition), entry_latch=latch)
        assert registry.transfer_attached_once(
            handle, entry_latch=latch, rejection_reason=None
        ) == "TRANSFERRED"
        support.advance_audited(registry, handle, latch, ordinal=1)
        registry.close()
        result = finalize(acquisition, handle, latch, 300_100_000_000)
        assert type(result) is _VenueNoEntryDisposition
        assert result.reason == "LIMITER_INVALIDATED"
        assert state.disposition is result
        assert state.token_state == "CONSUMED_BY_D10"
        assert state.reservation_state == "INVALIDATED"
        assert sum(event.amount for event in limiter._rest_events) == 5
        """
    )


def test_429_and_entry_each_win_one_linearization_order() -> None:
    _run_isolated(
        """
        acquisition, latch, state = await acquire(1, 300.0)
        handle = registry.attach_to_venue_handoff_once(consume(acquisition), entry_latch=latch)
        assert registry.transfer_attached_once(
            handle, entry_latch=latch, rejection_reason=None
        ) == "TRANSFERRED"
        support.advance_audited(registry, handle, latch, ordinal=1)
        await asyncio.wait_for(limiter.observe_429("1"), timeout=1)
        result = finalize(acquisition, handle, latch, 300_100_000_000)
        assert type(result) is _VenueNoEntryDisposition
        assert state.terminal_reason is Reason.COOLDOWN_ADVANCED
        assert state.disposition is result
        """
    )
    _run_isolated(
        """
        acquisition, latch, state = await acquire(1, 300.0)
        handle = registry.attach_to_venue_handoff_once(consume(acquisition), entry_latch=latch)
        assert registry.transfer_attached_once(
            handle, entry_latch=latch, rejection_reason=None
        ) == "TRANSFERRED"
        entered = finalize(acquisition, handle, latch, 300_100_000_000)
        assert type(entered) is _EnteredVenueDispatch
        await asyncio.wait_for(limiter.observe_429("1"), timeout=1)
        assert state.entered is entered
        assert state.disposition is None
        assert state.reservation_state == "FINALIZED_FOR_ENTRY"
        """
    )
    _run_isolated(
        """
        acquisition, latch, state = await acquire(1, 300.0)
        handle = registry.attach_to_venue_handoff_once(consume(acquisition), entry_latch=latch)
        assert registry.transfer_attached_once(
            handle, entry_latch=latch, rejection_reason=None
        ) == "TRANSFERRED"
        entered = finalize(acquisition, handle, latch, 300_100_000_000)
        assert type(entered) is _EnteredVenueDispatch
        registry.close()
        assert state.reservation_state == "FINALIZED_FOR_ENTRY"
        assert state.disposition is None
        assert state.entered is entered
        assert finalize(acquisition, handle, latch, 300_200_000_000) is entered
        """
    )


def test_consume_attach_transfer_and_entry_terminal_winner_orders() -> None:
    _run_isolated(
        """
        first, first_latch, first_state = await acquire(1, 300.0)
        registry.invalidate_issued_once(
            first.token,
            reason=Reason.D10_PRECONSUME_REJECTION,
        )
        try:
            consume(first)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("invalidated token consumed")
        assert first_state.token_state == first_state.reservation_state == "INVALIDATED"

        second, second_latch, second_state = await acquire(2, 301.0)
        evidence = consume(second)
        registry.invalidate_once(evidence, reason=Reason.CANCELLED)
        try:
            registry.attach_to_venue_handoff_once(evidence, entry_latch=second_latch)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("invalidated evidence attached")
        assert second_state.disposition.reason == "CANCELLED"

        third, third_latch, third_state = await acquire(3, 302.0)
        handle = registry.attach_to_venue_handoff_once(consume(third), entry_latch=third_latch)
        result = finalize(third, handle, third_latch, 302_100_000_000)
        assert type(result) is _VenueNoEntryDisposition
        assert result.reason == "SINK_BINDING_MISMATCH"
        assert registry.transfer_attached_once(
            handle, entry_latch=third_latch, rejection_reason=None
        ) is result

        fourth, fourth_latch, fourth_state = await acquire(4, 303.0)
        handle = registry.attach_to_venue_handoff_once(consume(fourth), entry_latch=fourth_latch)
        assert registry.transfer_attached_once(
            handle, entry_latch=fourth_latch, rejection_reason=None
        ) == "TRANSFERRED"
        reused = registry.transfer_attached_once(
            handle, entry_latch=fourth_latch, rejection_reason=None
        )
        assert reused.reason == "SINK_BINDING_MISMATCH"
        assert fourth_state.terminal_reason is Reason.VENUE_TRANSFER_REUSED
        assert fourth_state.disposition is reused
        """
    )


def test_event_replacement_and_clock_regression_compositely_poison_state() -> None:
    _run_isolated(
        """
        first, first_latch, first_state = await acquire(1, 300.0)
        indexed = limiter._event_index[first.binding.product_window_id]
        from dataclasses import replace
        limiter._event_index[first.binding.product_window_id] = replace(
            indexed, event=replace(indexed.event)
        )
        try:
            consume(first)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("replaced event consumed")
        assert first_state.terminal_reason is Reason.ACCOUNTING_EVENT_REPLACED
        assert first_state.disposition.reason == "LIMITER_INVALIDATED"
        assert limiter._accounting_poisoned is True
        """
    )
    _run_isolated(
        """
        acquisition, latch, state = await acquire(1, 300.0)
        fake.value = 299.0
        try:
            consume(acquisition)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("regressed clock consumed")
        assert limiter._clock_poisoned is True
        assert state.terminal_reason is Reason.LIMITER_CLOCK_INVALIDATED
        assert state.token_state == state.reservation_state == "INVALIDATED"
        assert state.disposition.reason == "CURRENT_INVALIDATED"
        assert sum(event.amount for event in limiter._rest_events) == 5
        """
    )


def test_worker_replacement_invalidates_live_grant_and_preserves_quota_owner() -> None:
    _run_isolated(
        """
        acquisition, latch, state = await acquire(1, 300.0)
        limiter_id = limiter._limiter_instance_id
        quota = (
            tuple(limiter._rest_events),
            dict(limiter._event_index),
            limiter._cooldown_epoch,
            limiter._limiter_accounting_generation,
        )
        replacement = support.replace_worker(issuer._FIXTURE_MARKER, currentness, proof)
        assert registry_module._replace_private_limiter_worker_proof_nonblocking(
            registry,
            current=proof,
            replacement=replacement,
            currentness=currentness,
        ) is registry
        assert state.terminal_reason is Reason.LIMITER_REPLACED
        assert state.token_state == state.reservation_state == "INVALIDATED"
        assert state.disposition.reason == "LIMITER_INVALIDATED"
        assert limiter._limiter_instance_id == limiter_id
        assert quota == (
            tuple(limiter._rest_events),
            dict(limiter._event_index),
            limiter._cooldown_epoch,
            limiter._limiter_accounting_generation,
        )
        try:
            consume(acquisition)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("old grant survived proof replacement")
        renewed, renewed_latch, renewed_state = await acquire(2, 301.0)
        assert renewed.binding.limiter_instance_id == limiter_id
        assert sum(event.amount for event in limiter._rest_events) == 10
        """
    )
    _run_isolated(
        """
        acquisition, latch, state = await acquire(1, 300.0)
        await asyncio.wait_for(limiter.close(), timeout=1)
        assert state.terminal_reason is Reason.LIMITER_REPLACED
        assert state.token_state == state.reservation_state == "INVALIDATED"
        assert state.disposition.reason == "LIMITER_INVALIDATED"
        assert sum(event.amount for event in limiter._rest_events) == 5
        try:
            consume(acquisition)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("closed limiter grant consumed")
        """
    )


def test_latch_to_gate_reverse_attempt_fails_without_transition() -> None:
    _run_isolated(
        """
        acquisition, latch, state = await acquire(1, 300.0)
        handle = registry.attach_to_venue_handoff_once(consume(acquisition), entry_latch=latch)
        owner = state.latch_owner
        gate_type = type(limiter._accounting_gate)
        original_hold = gate_type.hold

        def forbidden_hold(_owner):
            raise AssertionError("latch-to-gate inversion acquired accounting gate")

        owner._lock.acquire(blocking=False)
        object.__setattr__(owner, "_active_holder", __import__("threading").get_ident())
        gate_type.hold = forbidden_hold
        try:
            registry.transfer_attached_once(handle, entry_latch=latch, rejection_reason=None)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("latch-to-gate inversion admitted")
        finally:
            gate_type.hold = original_hold
            object.__setattr__(owner, "_active_holder", None)
            owner._lock.release()
        assert state.reservation_state == "ATTACHED_TO_VENUE_HANDOFF"
        assert state.disposition is None
        assert owner._transfer_used is False
        """
    )


def test_exact_clock_currentness_order_and_terminal_winner_evidence() -> None:
    _run_isolated(
        """
        acquisition, latch, state = await acquire(1, 300.0)
        handle = registry.attach_to_venue_handoff_once(consume(acquisition), entry_latch=latch)
        assert registry.transfer_attached_once(
            handle, entry_latch=latch, rejection_reason=None
        ) == "TRANSFERRED"
        support.advance_audited(registry, handle, latch, ordinal=1)
        clock, clock_currentness = support.issue_clock(
            issuer._FIXTURE_MARKER,
            incarnation,
            observed_monotonic_ns=300_100_000_000,
        )
        signed = clock.observe_nonblocking(
            expected_process_incarnation_id=incarnation,
            expected_clock_epoch=1,
        )
        deadline = clock.derive_deadline_nonblocking(
            utc_expiries=(),
            monotonic_deadlines_ns=(300_900_000_000,),
            expected_process_incarnation_id=incarnation,
            expected_clock_epoch=1,
        )
        clock_type = type(clock)
        currentness_type = type(clock_currentness)
        original_observe = clock_type.observe_nonblocking
        original_revalidate = currentness_type.revalidate_dispatch_clock_nonblocking
        order = []

        def observed(owner, **values):
            order.append("observe")
            assert limiter._accounting_gate.held_by_current_thread()
            assert state.latch_owner.held_by_current_thread()
            return original_observe(owner, **values)

        def revalidated(owner, value, **values):
            order.append("currentness")
            assert not limiter._accounting_gate.held_by_current_thread()
            assert not state.latch_owner.held_by_current_thread()
            return original_revalidate(owner, value, **values)

        clock_type.observe_nonblocking = observed
        currentness_type.revalidate_dispatch_clock_nonblocking = revalidated
        entered = registry.finalize_and_enter_once(
            handle,
            expected=acquisition.binding,
            entry_latch=latch,
            clock=clock,
            clock_currentness=clock_currentness,
            signed_at=signed,
            deadline=deadline,
            signed_timestamp=1_800_000_000,
        )
        clock_type.observe_nonblocking = original_observe
        currentness_type.revalidate_dispatch_clock_nonblocking = original_revalidate
        assert type(entered) is _EnteredVenueDispatch
        assert order == ["currentness", "currentness", "observe"]
        assert state.entry_clock is clock
        assert state.entry_clock_currentness is clock_currentness

        other_clock, other_currentness = support.issue_clock(
            issuer._FIXTURE_MARKER,
            incarnation,
            observed_monotonic_ns=300_200_000_000,
        )
        assert registry.finalize_and_enter_once(
            handle,
            expected=acquisition.binding,
            entry_latch=latch,
            clock=clock,
            clock_currentness=other_currentness,
            signed_at=signed,
            deadline=deadline,
            signed_timestamp=1_800_000_000,
        ) is entered

        rejected, rejected_latch, rejected_state = await acquire(2, 301.0)
        rejected_handle = registry.attach_to_venue_handoff_once(
            consume(rejected), entry_latch=rejected_latch
        )
        assert registry.transfer_attached_once(
            rejected_handle, entry_latch=rejected_latch, rejection_reason=None
        ) == "TRANSFERRED"
        support.advance_audited(registry, rejected_handle, rejected_latch, ordinal=2)
        rejected_clock, rejected_currentness = support.issue_clock(
            issuer._FIXTURE_MARKER,
            incarnation,
            observed_monotonic_ns=301_100_000_000,
        )
        rejected_signed = rejected_clock.observe_nonblocking(
            expected_process_incarnation_id=incarnation,
            expected_clock_epoch=1,
        )
        rejected_deadline = rejected_clock.derive_deadline_nonblocking(
            utc_expiries=(),
            monotonic_deadlines_ns=(301_900_000_000,),
            expected_process_incarnation_id=incarnation,
            expected_clock_epoch=1,
        )
        disposition = registry.finalize_and_enter_once(
            rejected_handle,
            expected=rejected.binding,
            entry_latch=rejected_latch,
            clock=rejected_clock,
            clock_currentness=other_currentness,
            signed_at=rejected_signed,
            deadline=rejected_deadline,
            signed_timestamp=1_800_000_000,
        )
        assert type(disposition) is _VenueNoEntryDisposition
        assert disposition.reason == "CURRENT_INVALIDATED"
        assert rejected_state.disposition is disposition

        invalidated, invalidated_latch, invalidated_state = await acquire(3, 302.0)
        invalidated_handle = registry.attach_to_venue_handoff_once(
            consume(invalidated), entry_latch=invalidated_latch
        )
        assert registry.transfer_attached_once(
            invalidated_handle, entry_latch=invalidated_latch, rejection_reason=None
        ) == "TRANSFERRED"
        support.advance_audited(registry, invalidated_handle, invalidated_latch, ordinal=3)
        invalid_clock, invalid_currentness = support.issue_clock(
            issuer._FIXTURE_MARKER,
            incarnation,
            observed_monotonic_ns=302_100_000_000,
        )
        invalid_signed = invalid_clock.observe_nonblocking(
            expected_process_incarnation_id=incarnation,
            expected_clock_epoch=1,
        )
        invalid_deadline = invalid_clock.derive_deadline_nonblocking(
            utc_expiries=(),
            monotonic_deadlines_ns=(302_900_000_000,),
            expected_process_incarnation_id=incarnation,
            expected_clock_epoch=1,
        )
        invalid_currentness.invalidate_signature_expired_nonblocking(
            invalid_clock,
            correlation_id=UUID(int=889),
        )
        invalid_disposition = registry.finalize_and_enter_once(
            invalidated_handle,
            expected=invalidated.binding,
            entry_latch=invalidated_latch,
            clock=invalid_clock,
            clock_currentness=invalid_currentness,
            signed_at=invalid_signed,
            deadline=invalid_deadline,
            signed_timestamp=1_800_000_000,
        )
        assert invalid_disposition.reason == "CURRENT_INVALIDATED"
        assert invalidated_state.disposition is invalid_disposition

        expired, expired_latch, expired_state = await acquire(4, 303.0)
        expired_handle = registry.attach_to_venue_handoff_once(
            consume(expired), entry_latch=expired_latch
        )
        assert registry.transfer_attached_once(
            expired_handle, entry_latch=expired_latch, rejection_reason=None
        ) == "TRANSFERRED"
        support.advance_audited(registry, expired_handle, expired_latch, ordinal=4)
        expiry_clock, expiry_currentness = support.issue_clock(
            issuer._FIXTURE_MARKER,
            incarnation,
            observed_monotonic_ns=303_100_000_000,
        )
        expiry_signed = expiry_clock.observe_nonblocking(
            expected_process_incarnation_id=incarnation,
            expected_clock_epoch=1,
        )
        expiry_deadline = type(deadline)(
            process_id=expiry_signed.process_id,
            process_incarnation_id=expiry_signed.process_incarnation_id,
            clock_epoch=expiry_signed.clock_epoch,
            monotonic_ns=expiry_signed.monotonic_ns,
            correlation_epoch=expiry_signed.correlation_epoch,
        )
        expired_disposition = registry.finalize_and_enter_once(
            expired_handle,
            expected=expired.binding,
            entry_latch=expired_latch,
            clock=expiry_clock,
            clock_currentness=expiry_currentness,
            signed_at=expiry_signed,
            deadline=expiry_deadline,
            signed_timestamp=1_800_000_000,
        )
        assert expired_disposition.reason == "DEADLINE_EXPIRED"
        assert expired_state.disposition is expired_disposition
        """
    )


def test_entry_time_evidence_is_copied_before_any_accounting_lock() -> None:
    _run_isolated(
        """
        callbacks = []

        class HostileScalar:
            def __eq__(self, _other):
                callbacks.append("eq")
                assert not limiter._accounting_gate.held_by_current_thread()
                raise RuntimeError("hostile time equality")

            def __lt__(self, _other):
                callbacks.append("lt")
                assert not limiter._accounting_gate.held_by_current_thread()
                raise RuntimeError("hostile time ordering")

        async def rejected(ordinal, corrupt_deadline):
            acquisition, latch, state = await acquire(ordinal, 299.0 + ordinal)
            handle = registry.attach_to_venue_handoff_once(
                consume(acquisition), entry_latch=latch
            )
            support.advance_audited(registry, handle, latch, ordinal=ordinal)
            assert registry.transfer_attached_once(
                handle, entry_latch=latch, rejection_reason=None
            ) == "TRANSFERRED"
            moment = (299 + ordinal) * 1_000_000_000 + 100_000_000
            clock, currentness = support.issue_clock(
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
            target = deadline if corrupt_deadline else signed
            object.__setattr__(target, "monotonic_ns", HostileScalar())
            result = registry.finalize_and_enter_once(
                handle,
                expected=acquisition.binding,
                entry_latch=latch,
                clock=clock,
                clock_currentness=currentness,
                signed_at=signed,
                deadline=deadline,
                signed_timestamp=1_800_000_000,
            )
            assert type(result) is _VenueNoEntryDisposition
            assert result.reason == "CURRENT_INVALIDATED"
            assert state.disposition is result

        await rejected(1, False)
        await rejected(2, True)
        assert callbacks == []
        """
    )


def test_publication_cancelled_error_is_canonical_cancelled_with_spent_quota() -> None:
    _run_isolated(
        """
        fake.value = 300.0
        intent = support.make_intent(incarnation, ordinal=1)
        slot = support.issue_outcome_slot(support.outcome_marker(), intent)
        store_type = type(store)
        original_publish = store_type.publish

        def cancelled(owner, *args, **kwargs):
            original_publish(owner, *args, **kwargs)
            raise asyncio.CancelledError

        store_type.publish = cancelled
        try:
            await asyncio.wait_for(
                registry.acquire_mutation_once(intent=intent, outcome_slot=slot),
                timeout=1,
            )
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("publication cancellation returned a grant")
        finally:
            store_type.publish = original_publish
        state = store._outcomes[id(slot)]
        assert state.terminal_reason is Reason.CANCELLED
        assert state.disposition.reason == "CANCELLED"
        assert state.token_state == state.reservation_state == "INVALIDATED"
        assert sum(event.amount for event in limiter._rest_events) == 5
        assert len(limiter._event_index) == 2
        assert store._tokens[state.registration.token_id] is state
        assert store._reservations[state.registration.held_reservation_id] is state
        """
    )
