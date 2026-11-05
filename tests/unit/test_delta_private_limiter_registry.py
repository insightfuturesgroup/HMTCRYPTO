"""B3 PID registry, atomic mutation grant, and deterministic race proofs."""

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import os
import pickle
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from dataclasses import fields
from types import MethodType
from uuid import UUID

import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange import private_dispatch
from hmt_crypto.exchange.delta.private_limiter_completion import (
    LimiterReservationInvalidationReason,
)
from hmt_crypto.exchange.delta.rate_limit_policy import (
    DELTA_RATE_LIMIT_POLICY,
    RateLimitScope,
    ScopeKind,
)
from tests.unit import delta_private_limiter_test_support as support

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.integration.support.task210_worker_clock_issuer",)


class _FakeMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _scope() -> RateLimitScope:
    return RateLimitScope("india", "testnet", ScopeKind.EXTERNAL_USER, "12345")


def _assert_opaque(value: object) -> None:
    assert not hasattr(value, "__dict__")
    with pytest.raises(TypeError):
        copy.copy(value)
    with pytest.raises(TypeError):
        copy.deepcopy(value)
    with pytest.raises(TypeError):
        pickle.dumps(value)
    with pytest.raises(TypeError):
        hash(value)
    with pytest.raises(TypeError):
        _ = value == value


@pytest.mark.asyncio
async def test_registry_atomic_grant_and_race_winners(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hmt_crypto.exchange.delta import private_limiter_registry as registry_module
    from hmt_crypto.exchange.delta import weighted_limiter as limiter_module

    fake = _FakeMonotonic()
    monkeypatch.setattr(limiter_module.time, "monotonic", fake)
    incarnation = UUID(int=100)
    proof, currentness = support.issue_worker(task210_worker_clock_marker, incarnation)
    registry = registry_module._compose_private_limiter_registry(
        ownership=proof,
        ownership_currentness=currentness,
        process_incarnation_id=incarnation,
        policy=DELTA_RATE_LIMIT_POLICY,
    )
    _assert_opaque(registry)
    with pytest.raises(TypeError):
        registry_module.PrivateLimiterRegistry()
    with pytest.raises(ConfigurationError):
        registry_module._compose_private_limiter_registry(
            ownership=proof,
            ownership_currentness=currentness,
            process_incarnation_id=incarnation,
            policy=DELTA_RATE_LIMIT_POLICY,
        )
    with pytest.raises(ConfigurationError):
        registry.lease_external_user(
            scope=RateLimitScope("global", "testnet", ScopeKind.EXTERNAL_USER, "12345")
        )
    first = registry.lease_external_user(scope=_scope())
    second = registry.lease_external_user(scope=_scope())
    assert first.lease_id != second.lease_id
    assert first.limiter_instance_id == second.limiter_instance_id
    assert first.scope == _scope()
    _assert_opaque(first)
    assert (
        registry_module._revalidate_private_limiter_lease_nonblocking(
            first,
            expected_scope=_scope(),
            expected_process_id=os.getpid(),
            expected_process_incarnation_id=incarnation,
        )
        is first
    )
    with pytest.raises(ConfigurationError):
        registry_module._revalidate_private_limiter_lease_nonblocking(
            first,
            expected_scope=_scope(),
            expected_process_id=os.getpid() + 1,
            expected_process_incarnation_id=incarnation,
        )
    with pytest.raises(ConfigurationError):
        await asyncio.wait_for(
            asyncio.to_thread(registry.lease_external_user, scope=_scope()),
            timeout=1,
        )
    limiter = object.__getattribute__(registry, "_limiter")
    mismatched_intent = support.make_intent(incarnation, ordinal=97)
    mismatched_slot_intent = support.make_intent(incarnation, ordinal=98)
    with pytest.raises(ConfigurationError):
        await asyncio.wait_for(
            registry.acquire_mutation_once(
                intent=mismatched_intent,
                outcome_slot=support.issue_outcome_slot(
                    support.outcome_marker(), mismatched_slot_intent
                ),
            ),
            timeout=1,
        )
    reverse_intent = support.make_intent(incarnation, ordinal=99)
    reverse_slot = support.issue_outcome_slot(support.outcome_marker(), reverse_intent)
    with limiter._accounting_gate.hold():
        with pytest.raises(ConfigurationError):
            await asyncio.wait_for(
                registry.acquire_mutation_once(
                    intent=reverse_intent,
                    outcome_slot=reverse_slot,
                ),
                timeout=1,
            )
    assert not limiter._waiters

    sleep_entered = asyncio.Event()
    never_release = asyncio.Event()

    async def controlled_sleep(_owner: object, delay: float) -> None:
        assert delay == 1.0
        sleep_entered.set()
        await never_release.wait()

    object.__setattr__(limiter, "_sleeper", MethodType(controlled_sleep, limiter))
    fake.value = 299.0
    queued_intent = support.make_intent(incarnation, ordinal=1)
    queued_slot = support.issue_outcome_slot(support.outcome_marker(), queued_intent)
    queued = asyncio.create_task(
        registry.acquire_mutation_once(
            intent=queued_intent,
            outcome_slot=queued_slot,
        )
    )
    await asyncio.wait_for(sleep_entered.wait(), timeout=1)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(queued, timeout=1)
    snapshot = await asyncio.wait_for(limiter.snapshot(), timeout=1)
    assert snapshot.rest_used == 0
    assert snapshot.product_operations_used == {}
    queued_state = registry._lifecycle._outcomes[id(queued_slot)]
    assert queued_state.registration_state == "INVALIDATED"
    assert queued_state.token_state == queued_state.reservation_state == "UNISSUED"
    assert queued_state.disposition.reason == "CANCELLED"
    assert queued_state.terminal_reason is LimiterReservationInvalidationReason.CANCELLED
    assert queued_state.registration.rest_event_id not in limiter._event_index
    assert queued_state.registration.product_event_id not in limiter._event_index
    lifecycle = registry._lifecycle
    assert lifecycle._grants[queued_state.registration.registration_id] is queued_state
    assert lifecycle._outcomes[id(queued_slot)] is queued_state
    assert lifecycle._tokens[queued_state.registration.token_id] is queued_state
    assert lifecycle._reservations[queued_state.registration.held_reservation_id] is queued_state
    with pytest.raises(ConfigurationError):
        await asyncio.wait_for(
            registry.acquire_mutation_once(intent=queued_intent, outcome_slot=queued_slot),
            timeout=1,
        )

    async def forbidden_sleep(_delay: float) -> None:
        raise AssertionError("grant must not sleep at cold-start equality")

    object.__setattr__(limiter, "_sleeper", forbidden_sleep)
    fake.value = 300.0
    intent = support.make_intent(incarnation, ordinal=2)
    acquisition = await asyncio.wait_for(
        registry.acquire_mutation_once(
            intent=intent,
            outcome_slot=support.issue_outcome_slot(support.outcome_marker(), intent),
        ),
        timeout=1,
    )
    assert acquisition.binding.rest_weight == 5
    assert acquisition.binding.product_operations == 1
    assert acquisition.binding.rest_window_id != acquisition.binding.product_window_id
    snapshot = await asyncio.wait_for(limiter.snapshot(), timeout=1)
    assert snapshot.rest_used == 5
    assert snapshot.product_operations_used == {27: 1}
    _assert_opaque(acquisition)
    _assert_opaque(acquisition.token)

    delivery_entered = asyncio.Event()
    release_delivery = asyncio.Event()

    async def delivery_barrier(_owner: object) -> None:
        delivery_entered.set()
        await release_delivery.wait()

    original_barrier = limiter._after_grant_release_before_delivery_barrier
    object.__setattr__(
        limiter,
        "_after_grant_release_before_delivery_barrier",
        MethodType(delivery_barrier, limiter),
    )
    fake.value = 301.0
    cancelled_intent = support.make_intent(incarnation, ordinal=3)
    cancelled_slot = support.issue_outcome_slot(support.outcome_marker(), cancelled_intent)
    cancelled = asyncio.create_task(
        registry.acquire_mutation_once(
            intent=cancelled_intent,
            outcome_slot=cancelled_slot,
        )
    )
    await asyncio.wait_for(delivery_entered.wait(), timeout=1)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(cancelled, timeout=1)
    snapshot = await asyncio.wait_for(limiter.snapshot(), timeout=1)
    assert snapshot.rest_used == 10
    assert snapshot.product_operations_used == {27: 1}
    cancelled_state = registry._lifecycle._outcomes[id(cancelled_slot)]
    assert cancelled_state.registration_state == "INVALIDATED"
    assert cancelled_state.token_state == cancelled_state.reservation_state == "INVALIDATED"
    assert cancelled_state.disposition.reason == "CANCELLED"
    assert cancelled_state.terminal_reason is LimiterReservationInvalidationReason.CANCELLED
    assert limiter._event_index[cancelled_state.binding.rest_window_id].event is (
        cancelled_state.rest_event
    )
    assert limiter._event_index[cancelled_state.binding.product_window_id].event is (
        cancelled_state.product_event
    )
    assert lifecycle._grants[cancelled_state.registration.registration_id] is cancelled_state
    assert lifecycle._outcomes[id(cancelled_slot)] is cancelled_state
    assert lifecycle._tokens[cancelled_state.registration.token_id] is cancelled_state
    assert (
        lifecycle._reservations[cancelled_state.registration.held_reservation_id] is cancelled_state
    )
    with pytest.raises(ConfigurationError):
        await asyncio.wait_for(
            registry.acquire_mutation_once(
                intent=cancelled_intent,
                outcome_slot=cancelled_slot,
            ),
            timeout=1,
        )
    object.__setattr__(limiter, "_after_grant_release_before_delivery_barrier", original_barrier)

    delivery_entered = asyncio.Event()
    release_delivery = asyncio.Event()
    object.__setattr__(
        limiter,
        "_after_grant_release_before_delivery_barrier",
        MethodType(delivery_barrier, limiter),
    )
    fake.value = 302.0
    limited_intent = support.make_intent(incarnation, ordinal=4)
    limited_task = asyncio.create_task(
        registry.acquire_mutation_once(
            intent=limited_intent,
            outcome_slot=support.issue_outcome_slot(support.outcome_marker(), limited_intent),
        )
    )
    await asyncio.wait_for(delivery_entered.wait(), timeout=1)
    await asyncio.wait_for(limiter.observe_429("1"), timeout=1)
    release_delivery.set()
    limited = await asyncio.wait_for(limited_task, timeout=1)
    with pytest.raises(ConfigurationError):
        registry.consume_or_invalidate_once(
            limited.token,
            expected=limited.binding,
            actual_region=DeltaRegion.INDIA,
            actual_environment=DeltaEnvironment.TESTNET,
            actual_mode=ExecutionMode.LIVE,
        )
    snapshot = await asyncio.wait_for(limiter.snapshot(), timeout=1)
    assert snapshot.rest_used == 15
    object.__setattr__(limiter, "_after_grant_release_before_delivery_barrier", original_barrier)

    owned_latches: dict[int, object] = {}

    async def acquire_at(ordinal: int, moment: float) -> object:
        fake.value = moment
        owned_intent = support.make_intent(incarnation, ordinal=ordinal)
        slot = support.issue_outcome_slot(support.outcome_marker(), owned_intent)
        owned_latches[ordinal] = support.entry_latch(slot)
        return await asyncio.wait_for(
            registry.acquire_mutation_once(
                intent=owned_intent,
                outcome_slot=slot,
            ),
            timeout=1,
        )

    wrong_target = await acquire_at(5, 303.0)
    with pytest.raises(ConfigurationError):
        registry.consume_or_invalidate_once(
            wrong_target.token,
            expected=wrong_target.binding,
            actual_region=DeltaRegion.INDIA,
            actual_environment=DeltaEnvironment.TESTNET,
            actual_mode=ExecutionMode.PAPER,
        )
    with pytest.raises(ConfigurationError):
        registry.consume_or_invalidate_once(
            wrong_target.token,
            expected=wrong_target.binding,
            actual_region=DeltaRegion.INDIA,
            actual_environment=DeltaEnvironment.TESTNET,
            actual_mode=ExecutionMode.LIVE,
        )

    entered_acquisition = await acquire_at(6, 304.0)
    evidence = registry.consume_or_invalidate_once(
        entered_acquisition.token,
        expected=entered_acquisition.binding,
        actual_region=DeltaRegion.INDIA,
        actual_environment=DeltaEnvironment.TESTNET,
        actual_mode=ExecutionMode.LIVE,
    )
    with pytest.raises(ConfigurationError):
        registry.consume_or_invalidate_once(
            entered_acquisition.token,
            expected=entered_acquisition.binding,
            actual_region=DeltaRegion.INDIA,
            actual_environment=DeltaEnvironment.TESTNET,
            actual_mode=ExecutionMode.LIVE,
        )
    latch = owned_latches[6]
    handle = registry.attach_to_venue_handoff_once(evidence, entry_latch=latch)
    assert (
        registry.transfer_attached_once(
            handle,
            entry_latch=latch,
            rejection_reason=None,
        )
        == "TRANSFERRED"
    )
    support.advance_audited(registry, handle, latch, ordinal=6)
    clock, clock_currentness = support.issue_clock(
        task210_worker_clock_marker,
        incarnation,
        observed_monotonic_ns=304_100_000_000,
    )
    signed_at = clock.observe_nonblocking(
        expected_process_incarnation_id=incarnation,
        expected_clock_epoch=1,
    )
    deadline = clock.derive_deadline_nonblocking(
        utc_expiries=(),
        monotonic_deadlines_ns=(304_900_000_000,),
        expected_process_incarnation_id=incarnation,
        expected_clock_epoch=1,
    )
    entered = registry.finalize_and_enter_once(
        handle,
        expected=entered_acquisition.binding,
        entry_latch=latch,
        clock=clock,
        clock_currentness=clock_currentness,
        signed_at=signed_at,
        deadline=deadline,
        signed_timestamp=1_800_000_000,
    )
    assert type(entered) is private_dispatch._EnteredVenueDispatch
    assert (
        registry.transfer_attached_once(
            handle,
            entry_latch=latch,
            rejection_reason=None,
        )
        == "ALREADY_ENTERED"
    )
    with pytest.raises(ConfigurationError):
        registry.invalidate_before_entry_once(
            handle,
            entry_latch=latch,
            reason="CANCELLED",
        )

    invalidated_acquisition = await acquire_at(7, 305.0)
    invalidated_evidence = registry.consume_or_invalidate_once(
        invalidated_acquisition.token,
        expected=invalidated_acquisition.binding,
        actual_region=DeltaRegion.INDIA,
        actual_environment=DeltaEnvironment.TESTNET,
        actual_mode=ExecutionMode.LIVE,
    )
    invalidated_latch = owned_latches[7]
    invalidated_handle = registry.attach_to_venue_handoff_once(
        invalidated_evidence,
        entry_latch=invalidated_latch,
    )
    with pytest.raises(ConfigurationError):
        registry.invalidate_before_entry_once(
            invalidated_handle,
            entry_latch=invalidated_latch,
            reason="UNKNOWN",  # type: ignore[arg-type]
        )
    disposition = registry.invalidate_before_entry_once(
        invalidated_handle,
        entry_latch=invalidated_latch,
        reason="CANCELLED",
    )
    assert disposition.reason == "CANCELLED"
    assert (
        registry.invalidate_before_entry_once(
            invalidated_handle,
            entry_latch=invalidated_latch,
            reason="INTERNAL_EXCEPTION",
        )
        is disposition
    )
    assert (
        registry.transfer_attached_once(
            invalidated_handle,
            entry_latch=invalidated_latch,
            rejection_reason=None,
        )
        is disposition
    )

    pruned_acquisition = await acquire_at(8, 306.0)
    pruned_evidence = registry.consume_or_invalidate_once(
        pruned_acquisition.token,
        expected=pruned_acquisition.binding,
        actual_region=DeltaRegion.INDIA,
        actual_environment=DeltaEnvironment.TESTNET,
        actual_mode=ExecutionMode.LIVE,
    )
    fake.value = 307.0
    await asyncio.wait_for(limiter.snapshot(), timeout=1)
    with pytest.raises(ConfigurationError):
        registry.attach_to_venue_handoff_once(
            pruned_evidence,
            entry_latch=owned_latches[8],
        )

    lifecycle = object.__getattribute__(registry, "_lifecycle")
    gate = limiter._accounting_gate
    original_hold = type(gate).hold
    original_publish = type(lifecycle).publish
    gate_exit_states: list[str] = []
    watched: list[object] = []

    @contextmanager
    def observing_hold(owner: object) -> object:
        with original_hold(owner):
            try:
                yield
            finally:
                if watched:
                    gate_exit_states.append(watched[0].registration_state)

    def fail_after_publish(owner: object, *args: object, **kwargs: object) -> object:
        result = original_publish(owner, *args, **kwargs)
        watched.append(object.__getattribute__(result.token, "_grant"))
        raise RuntimeError("post-charge publication failure")

    monkeypatch.setattr(type(gate), "hold", observing_hold)
    monkeypatch.setattr(type(lifecycle), "publish", fail_after_publish)
    fake.value = 308.0
    failed_intent = support.make_intent(incarnation, ordinal=9)
    failed_slot = support.issue_outcome_slot(support.outcome_marker(), failed_intent)
    with pytest.raises(RuntimeError, match="post-charge"):
        await asyncio.wait_for(
            registry.acquire_mutation_once(intent=failed_intent, outcome_slot=failed_slot),
            timeout=1,
        )
    monkeypatch.setattr(type(lifecycle), "publish", original_publish)
    monkeypatch.setattr(type(gate), "hold", original_hold)
    assert gate_exit_states[0] == "INVALIDATED"
    assert watched[0].token_state == watched[0].reservation_state == "INVALIDATED"
    assert watched[0].disposition.reason == "INTERNAL_EXCEPTION"

    quota_before_replacement = (
        tuple(limiter._rest_events),
        {key: tuple(value) for key, value in limiter._product_events.items()},
        dict(limiter._event_index),
        limiter._limiter_clock_epoch,
        limiter._limiter_accounting_generation,
        limiter._cooldown_epoch,
    )

    replacement = support.replace_worker(task210_worker_clock_marker, currentness, proof)
    returned = registry_module._replace_private_limiter_worker_proof_nonblocking(
        registry,
        current=proof,
        replacement=replacement,
        currentness=currentness,
    )
    assert returned is registry
    renewed = registry.lease_external_user(scope=_scope())
    assert renewed.registry_open_epoch == first.registry_open_epoch + 1
    assert renewed.limiter_instance_id == first.limiter_instance_id
    assert quota_before_replacement == (
        tuple(limiter._rest_events),
        {key: tuple(value) for key, value in limiter._product_events.items()},
        dict(limiter._event_index),
        limiter._limiter_clock_epoch,
        limiter._limiter_accounting_generation,
        limiter._cooldown_epoch,
    )
    with pytest.raises(ConfigurationError):
        registry_module._revalidate_private_limiter_lease_nonblocking(
            first,
            expected_scope=_scope(),
            expected_process_id=os.getpid(),
            expected_process_incarnation_id=incarnation,
        )

    proof_loss_entered = asyncio.Event()
    release_proof_loss = asyncio.Event()

    async def proof_loss_barrier(_owner: object) -> None:
        proof_loss_entered.set()
        await release_proof_loss.wait()

    object.__setattr__(
        limiter,
        "_after_grant_release_before_delivery_barrier",
        MethodType(proof_loss_barrier, limiter),
    )
    fake.value = 309.0
    proof_loss_intent = support.make_intent(incarnation, ordinal=10)
    proof_loss_task = asyncio.create_task(
        registry.acquire_mutation_once(
            intent=proof_loss_intent,
            outcome_slot=support.issue_outcome_slot(support.outcome_marker(), proof_loss_intent),
        )
    )
    await asyncio.wait_for(proof_loss_entered.wait(), timeout=1)
    support.invalidate_worker(task210_worker_clock_marker, currentness)
    release_proof_loss.set()
    with pytest.raises(ConfigurationError):
        await asyncio.wait_for(proof_loss_task, timeout=1)

    registry.close()
    registry.close()
    assert limiter._closed is True
    assert sum(event.amount for event in limiter._rest_events) == 45
    with pytest.raises(ConfigurationError):
        registry_module._compose_private_limiter_registry(
            ownership=replacement,
            ownership_currentness=currentness,
            process_incarnation_id=incarnation,
            policy=DELTA_RATE_LIMIT_POLICY,
        )


def test_registered_grant_split_has_no_owner_or_standalone_authority() -> None:
    from hmt_crypto.exchange.delta import registered_limiter_grant as bridge
    from hmt_crypto.exchange.delta.private_limiter_completion import (
        _PrivateMutationGrantRegistration,
    )

    tree = ast.parse(inspect.getsource(bridge))
    imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(
        fragment in name
        for name in imports
        for fragment in ("worker", "client", "httpx", "transport", "private_limiter_registry")
    )
    assert not any(isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) for node in tree.body)
    assert not any(
        isinstance(node, ast.Assign) and isinstance(node.value, (ast.Dict, ast.List, ast.Set))
        for node in tree.body
    )
    assert bridge.__all__ == []
    assert tuple(field.name for field in fields(_PrivateMutationGrantRegistration)) == (
        "registration_id",
        "registry_owner_id",
        "registry_open_epoch",
        "ownership_proof_id",
        "limiter_instance_id",
        "limiter_clock_epoch",
        "limiter_accounting_generation",
        "cooldown_epoch",
        "process_id",
        "process_incarnation_id",
        "intent",
        "outcome_slot",
        "held_reservation_id",
        "token_id",
        "rest_event_id",
        "product_event_id",
        "_limiter",
        "_gate",
        "_store",
        "_owner_capability",
    )
    with pytest.raises(TypeError):
        _PrivateMutationGrantRegistration()
    forged = object.__new__(_PrivateMutationGrantRegistration)
    with pytest.raises(ConfigurationError):
        bridge._validate_registration(object(), forged)


def test_neutral_dispatch_remains_worker_free() -> None:
    path = inspect.getsourcefile(private_dispatch)
    assert path is not None
    with open(path, encoding="utf-8") as source:
        tree = ast.parse(source.read())
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(name.startswith("hmt_crypto.worker") for name in imported)
    with pytest.raises(TypeError):
        support.issue_outcome_slot(object(), support.make_intent(UUID(int=700)))
    with pytest.raises(ConfigurationError):
        support.issue_worker(object(), UUID(int=701))


def test_registry_splits_are_pure_acyclic_and_keep_public_lease_identity() -> None:
    from hmt_crypto.exchange.delta import private_limiter_lifecycle as lifecycle
    from hmt_crypto.exchange.delta import private_limiter_registry as registry
    from hmt_crypto.exchange.delta import private_limiter_registry_records as records

    assert registry.PrivateLimiterLease is records.PrivateLimiterLease
    assert records.PrivateLimiterLease.__module__ == registry.__name__
    assert records.__all__ == lifecycle.__all__ == []
    for module, ceiling in ((records, 220), (lifecycle, 600)):
        source = inspect.getsource(module)
        assert len(source.splitlines()) <= ceiling
        tree = ast.parse(source)
        imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any(
            fragment in name
            for name in imports
            for fragment in (
                "weighted_limiter",
                "private_limiter_registry",
                "leadership",
                "client",
                "httpx",
                "transport",
            )
        )
        assert not any(
            isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree)
        )
    record_tree = ast.parse(inspect.getsource(records))
    assert not any(
        isinstance(node, ast.Assign) and isinstance(node.value, (ast.Dict, ast.List, ast.Set))
        for node in record_tree.body
    )


def test_rate_limiter_registry_relocation_has_fresh_pickle_identity() -> None:
    from hmt_crypto.exchange.delta import rate_limiter, weighted_limiter

    assert not hasattr(weighted_limiter, "LimiterRegistry")
    assert rate_limiter.LimiterRegistry.__module__ == rate_limiter.__name__
    owner = rate_limiter.LimiterRegistry()
    restored = pickle.loads(pickle.dumps(owner))
    assert type(restored) is rate_limiter.LimiterRegistry
    assert restored.coordination_mode is owner.coordination_mode


@pytest.mark.parametrize(
    "order",
    (
        (
            "hmt_crypto.exchange.private_dispatch",
            "hmt_crypto.worker.clock",
            "hmt_crypto.exchange.delta.private_limiter_completion",
            "hmt_crypto.exchange.delta.private_limiter_registry",
        ),
        (
            "hmt_crypto.exchange.delta.private_limiter_registry",
            "hmt_crypto.exchange.delta.private_limiter_completion",
            "hmt_crypto.worker.clock",
            "hmt_crypto.exchange.private_dispatch",
        ),
    ),
)
def test_fresh_process_import_orders_are_inert(order: tuple[str, ...]) -> None:
    script = textwrap.dedent(
        f"""
        import importlib
        import sys

        sys.path.insert(0, "src")
        for name in {order!r}:
            importlib.import_module(name)
        registry = importlib.import_module(
            "hmt_crypto.exchange.delta.private_limiter_registry"
        )
        records = importlib.import_module(
            "hmt_crypto.exchange.delta.private_limiter_registry_records"
        )
        assert registry._PROCESS_REGISTRIES == {{}}
        assert registry.PrivateLimiterLease is records.PrivateLimiterLease
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_fork_and_foreign_loop_fail_before_inherited_registry_locks() -> None:
    if not hasattr(os, "fork"):
        pytest.skip("requires os.fork")
    script = textwrap.dedent(
        """
        import asyncio
        import os
        import sys
        from uuid import UUID

        sys.path.insert(0, ".")
        sys.path.insert(0, "src")
        from hmt_crypto.exchange.delta.private_limiter_registry import (
            _compose_private_limiter_registry,
        )
        from hmt_crypto.exchange.delta.rate_limit_policy import (
            DELTA_RATE_LIMIT_POLICY,
            RateLimitScope,
            ScopeKind,
        )
        from hmt_crypto.errors import ConfigurationError
        from tests.integration.support import task210_worker_clock_issuer as issuer
        from tests.unit import delta_private_limiter_test_support as support

        incarnation = UUID(int=900)
        proof, currentness = support.issue_worker(issuer._FIXTURE_MARKER, incarnation)
        registry = _compose_private_limiter_registry(
            ownership=proof,
            ownership_currentness=currentness,
            process_incarnation_id=incarnation,
            policy=DELTA_RATE_LIMIT_POLICY,
        )
        scope = RateLimitScope(
            "india", "testnet", ScopeKind.EXTERNAL_USER, "12345"
        )
        child = os.fork()
        if child == 0:
            try:
                registry.lease_external_user(scope=scope)
            except ConfigurationError:
                os._exit(0)
            os._exit(4)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0

        async def foreign_loop_attempt():
            registry.lease_external_user(scope=scope)

        try:
            asyncio.run(foreign_loop_attempt())
        except ConfigurationError:
            pass
        else:
            raise AssertionError("foreign loop admitted")
        registry.close()
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
