"""Hostile B3 lifecycle, latch, and registration construction proofs."""

from __future__ import annotations

import ast
import copy
import inspect
import os
import pickle
import subprocess
import sys
import textwrap
from dataclasses import fields
from uuid import UUID

import pytest

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange import private_dispatch
from hmt_crypto.exchange.delta import private_limiter_completion as completion
from hmt_crypto.exchange.delta import registered_limiter_grant as bridge
from hmt_crypto.exchange.delta.rate_limit_policy import (
    DELTA_RATE_LIMIT_POLICY,
    RateLimitScope,
    ScopeKind,
)
from tests.unit import delta_private_limiter_test_support as support

pytestmark = pytest.mark.unit


def _run_isolated(source: str) -> None:
    project = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    result = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(source)],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_latch_is_issued_with_slot_and_reconstruction_has_no_authority() -> None:
    intent = support.make_intent(UUID(int=710), ordinal=1)
    slot = support.issue_outcome_slot(support.outcome_marker(), intent)
    latch = support.entry_latch(slot)

    assert type(latch) is private_dispatch._VenueDispatchEntryLatch
    assert support.entry_latch(slot) is latch
    forged = object.__new__(private_dispatch._VenueDispatchEntryLatch)
    with pytest.raises(TypeError):
        private_dispatch._venue_dispatch_entry_latch_owner(forged)
    owner = private_dispatch._venue_dispatch_entry_latch_owner(latch)
    assert owner is slot


def test_terminal_result_minting_is_not_an_unconstrained_leaf_helper() -> None:
    from hmt_crypto.exchange.delta import private_limiter_entry as entry

    namespace = vars(private_dispatch)
    assert "_issue_venue_no_entry_disposition" not in namespace
    assert "_issue_entered_venue_dispatch" not in namespace
    assert "_bind_owned_entry_latch" not in vars(entry)
    assert "_mint_owned_no_entry_disposition" not in vars(entry)
    assert "_mint_owned_entered_dispatch" not in vars(entry)
    for result_type in (
        private_dispatch._VenueNoEntryDisposition,
        private_dispatch._EnteredVenueDispatch,
    ):
        forged = object.__new__(result_type)
        with pytest.raises(TypeError):
            copy.copy(forged)
        with pytest.raises(TypeError):
            pickle.dumps(forged)


def test_lifecycle_store_and_entry_splits_have_single_owner_bound_responsibility() -> None:
    from hmt_crypto.exchange.delta import private_limiter_entry as entry
    from hmt_crypto.exchange.delta import private_limiter_lifecycle_store as store

    assert not hasattr(completion, "_GrantState")
    assert not hasattr(completion, "_PrivateLimiterLifecycleStore")
    assert store.__all__ == entry.__all__ == []
    assert len(inspect.getsource(store).splitlines()) <= 600
    assert len(inspect.getsource(entry).splitlines()) <= 600

    store_tree = ast.parse(inspect.getsource(store))
    entry_tree = ast.parse(inspect.getsource(entry))
    forbidden = ("httpx", "client", "signer", "transport", "persistence", "audit")
    for tree in (store_tree, entry_tree):
        imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any(fragment in name for name in imports for fragment in forbidden)
        assert not any(
            isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree)
        )
    classes = {node.name for node in entry_tree.body if isinstance(node, ast.ClassDef)}
    assert classes == {"_OwnedVenueEntryLatch"}


def test_lifecycle_store_rejects_missing_fake_and_reused_owner_capability() -> None:
    from hmt_crypto.exchange.delta.private_limiter_lifecycle_store import (
        _PrivateLimiterLifecycleStore,
    )
    from hmt_crypto.exchange.delta.private_limiter_owner import (
        _PrivateLimiterOwnerCapability,
    )
    from hmt_crypto.exchange.delta.weighted_limiter import WeightedRateLimiter

    scope = RateLimitScope("india", "testnet", ScopeKind.EXTERNAL_USER, "12345")
    limiter = WeightedRateLimiter(scope, policy=DELTA_RATE_LIMIT_POLICY)
    fake = object.__new__(_PrivateLimiterOwnerCapability)
    with pytest.raises((ConfigurationError, TypeError)):
        _PrivateLimiterLifecycleStore(limiter._accounting_gate)
    with pytest.raises(ConfigurationError):
        _PrivateLimiterLifecycleStore(owner_capability=fake)
    for operation in (copy.copy, copy.deepcopy, pickle.dumps, hash):
        with pytest.raises((TypeError, AttributeError)):
            operation(fake)


def test_owner_capability_is_bound_once_and_fake_stage_has_no_side_effect() -> None:
    _run_isolated(
        """
        import os
        import sys
        from uuid import UUID

        sys.path.insert(0, ".")
        sys.path.insert(0, "src")

        from hmt_crypto.errors import ConfigurationError
        from hmt_crypto.exchange import private_dispatch
        from hmt_crypto.exchange.delta import private_limiter_registry as registry_module
        from hmt_crypto.exchange.delta import registered_limiter_grant as bridge
        from hmt_crypto.exchange.delta.private_limiter_lifecycle_store import (
            _PrivateLimiterLifecycleStore,
        )
        from hmt_crypto.exchange.delta.private_limiter_owner import (
            _PrivateLimiterOwnerCapability,
        )
        from hmt_crypto.exchange.delta.rate_limit_policy import DELTA_RATE_LIMIT_POLICY
        from tests.integration.support import task210_worker_clock_issuer as issuer
        from tests.unit import delta_private_limiter_test_support as support

        incarnation = UUID(int=760)
        proof, currentness = support.issue_worker(issuer._FIXTURE_MARKER, incarnation)
        registry = registry_module._compose_private_limiter_registry(
            ownership=proof,
            ownership_currentness=currentness,
            process_incarnation_id=incarnation,
            policy=DELTA_RATE_LIMIT_POLICY,
        )
        limiter, store = registry._limiter, registry._lifecycle
        capability = registry._owner_capability
        assert limiter._private_lifecycle_capability is capability
        assert store._owner_capability is capability
        try:
            _PrivateLimiterLifecycleStore(owner_capability=capability)
        except ConfigurationError:
            pass
        else:
            raise AssertionError("owner capability rebound to a second store")

        intent = support.make_intent(incarnation, ordinal=1)
        slot = support.issue_outcome_slot(support.outcome_marker(), intent)
        audit = private_dispatch._venue_dispatch_outcome_audit_identity(slot)
        fake = object.__new__(_PrivateLimiterOwnerCapability)
        before = (len(store._grants), len(store._outcomes), len(limiter._event_index))
        try:
            bridge._stage_registered_mutation(
                owner_capability=fake,
                limiter=limiter,
                store=store,
                intent=intent,
                outcome_slot=slot,
                audit_identity=audit,
                registry_owner_id=registry._registry_owner_id,
                registry_open_epoch=registry._open_epoch,
                ownership_proof_id=proof.proof_id,
                process_id=os.getpid(),
                process_incarnation_id=incarnation,
                limiter_clock_epoch=limiter._limiter_clock_epoch,
                accounting_generation=limiter._limiter_accounting_generation,
                cooldown_epoch=limiter._cooldown_epoch,
                identities=tuple(UUID(int=value) for value in range(761, 766)),
            )
        except ConfigurationError:
            pass
        else:
            raise AssertionError("fake owner capability staged a registration")
        assert (len(store._grants), len(store._outcomes), len(limiter._event_index)) == before
        """
    )


def test_reflected_module_globals_cannot_compose_a_fake_registry_owner() -> None:
    from types import SimpleNamespace

    from hmt_crypto.exchange.delta import private_limiter_owner as owner
    from hmt_crypto.exchange.delta import private_limiter_registry as registry_module
    from hmt_crypto.exchange.delta import private_limiter_registry_authority as authority
    from hmt_crypto.exchange.delta.private_limiter_lifecycle_store import (
        _PrivateLimiterLifecycleStore,
    )
    from hmt_crypto.exchange.delta.weighted_limiter import WeightedRateLimiter

    namespace = vars(owner)
    forbidden = {
        "_CAPABILITY_ISSUER_SENTINEL",
        "_CAPABILITY_SEAL",
        "_issue_private_limiter_owner_capability",
        "_restore_private_limiter_owner",
    }
    assert not hasattr(authority, "_take_private_registry_authority")
    assert not hasattr(authority, "_build_authority_installer")
    assert owner._PrivateLimiterOwnerCapability.__slots__ == ("_registry",)
    limiter = WeightedRateLimiter(
        RateLimitScope("india", "testnet", ScopeKind.EXTERNAL_USER, "12345"),
        policy=DELTA_RATE_LIMIT_POLICY,
    )
    fake_registry = SimpleNamespace()
    owner_id = UUID(int=766)
    fake_registry._limiter = limiter
    fake_registry._registry_owner_id = owner_id
    store = None
    try:
        capability = namespace["_issue_private_limiter_owner_capability"](
            sentinel=namespace["_CAPABILITY_ISSUER_SENTINEL"],
            registry=fake_registry,
            limiter=limiter,
            gate=limiter._accounting_gate,
            registry_owner_id=owner_id,
        )
        fake_registry._owner_capability = capability
        limiter._private_lifecycle_capability = capability
        store = _PrivateLimiterLifecycleStore(owner_capability=capability)
        fake_registry._lifecycle = store
        limiter._private_lifecycle_store = store
        intent = support.make_intent(UUID(int=767), ordinal=1)
        slot = support.issue_outcome_slot(support.outcome_marker(), intent)
        audit = private_dispatch._venue_dispatch_outcome_audit_identity(slot)
        registration, _ = bridge._stage_registered_mutation(
            owner_capability=capability,
            limiter=limiter,
            store=store,
            intent=intent,
            outcome_slot=slot,
            audit_identity=audit,
            registry_owner_id=owner_id,
            registry_open_epoch=1,
            ownership_proof_id=UUID(int=768),
            process_id=intent.process_id,
            process_incarnation_id=intent.process_incarnation_id,
            limiter_clock_epoch=1,
            accounting_generation=1,
            cooldown_epoch=0,
            identities=tuple(UUID(int=value) for value in range(769, 774)),
        )
        with limiter._accounting_gate.hold():
            store.prepare(registration, audit, support.entry_latch(slot))
            store.invalidate(
                registration,
                completion.LimiterReservationInvalidationReason.INTERNAL_EXCEPTION,
            )
    except (AttributeError, ConfigurationError, KeyError, TypeError):
        pass
    else:
        raise AssertionError("reflected module authority minted a terminal result")
    assert forbidden.isdisjoint(namespace)
    assert not limiter._event_index
    if store is not None:
        assert not store._grants
        assert not store._outcomes
        assert not store._latches

    exact_fake = object.__new__(registry_module.PrivateLimiterRegistry)
    with pytest.raises(ConfigurationError):
        registry_module.PrivateLimiterRegistry.__init__(
            exact_fake,
            _constructor_nonce=object(),
        )
    fake_process_id = 999_999_937
    forged = object.__new__(owner._PrivateLimiterOwnerCapability)
    object.__setattr__(forged, "_registry", exact_fake)
    exact_store = None

    def forged_claim(operation: object, _candidate: object, candidate_store: object) -> object:
        if operation == "claim_store":
            return limiter._accounting_gate
        if operation == "restore":
            return exact_fake, limiter, limiter._accounting_gate, owner_id
        if operation in {"finish_store", "owns_store", "attest"}:
            return forged
        raise AssertionError((operation, candidate_store))

    try:
        object.__setattr__(forged, "_claim", forged_claim)
    except AttributeError:
        pass
    fake_values = {
        "_closed": True,
        "_lifecycle": None,
        "_limiter": limiter,
        "_open_epoch": 0,
        "_owner_capability": forged,
        "_process_id": fake_process_id,
        "_registry_owner_id": owner_id,
    }
    for name, value in fake_values.items():
        object.__setattr__(exact_fake, name, value)
    limiter._private_lifecycle_capability = forged
    limiter._private_lifecycle_store = None
    registry_module._PROCESS_REGISTRIES[fake_process_id] = exact_fake
    try:
        with pytest.raises(ConfigurationError):
            exact_store = _PrivateLimiterLifecycleStore(owner_capability=forged)
            object.__setattr__(exact_fake, "_lifecycle", exact_store)
            object.__setattr__(exact_fake, "_open_epoch", 1)
            object.__setattr__(exact_fake, "_closed", False)
            limiter._private_lifecycle_store = exact_store
            intent = support.make_intent(UUID(int=774), ordinal=1)
            slot = support.issue_outcome_slot(support.outcome_marker(), intent)
            audit = private_dispatch._venue_dispatch_outcome_audit_identity(slot)
            registration, _ = bridge._stage_registered_mutation(
                owner_capability=forged,
                limiter=limiter,
                store=exact_store,
                intent=intent,
                outcome_slot=slot,
                audit_identity=audit,
                registry_owner_id=owner_id,
                registry_open_epoch=1,
                ownership_proof_id=UUID(int=775),
                process_id=intent.process_id,
                process_incarnation_id=intent.process_incarnation_id,
                limiter_clock_epoch=limiter._limiter_clock_epoch,
                accounting_generation=limiter._limiter_accounting_generation,
                cooldown_epoch=limiter._cooldown_epoch,
                identities=tuple(UUID(int=value) for value in range(776, 781)),
            )
            with limiter._accounting_gate.hold():
                exact_store.prepare(registration, audit, support.entry_latch(slot))
                exact_store.invalidate(
                    registration,
                    completion.LimiterReservationInvalidationReason.INTERNAL_EXCEPTION,
                )
    finally:
        registry_module._PROCESS_REGISTRIES.pop(fake_process_id, None)
    assert fake_process_id not in registry_module._PROCESS_REGISTRIES
    assert not limiter._event_index
    if exact_store is not None:
        assert not exact_store._grants
        assert not exact_store._outcomes
        assert not exact_store._latches


_OWNER_PARITY_CASES = (
    ("consume", "store._grants", "registration_id", "object()"),
    ("consume", "store._tokens", "token_id", "object()"),
    ("consume", "store._reservations", "reservation_id", "object()"),
    ("consume", "store._outcomes", "slot_id", "object()"),
    ("consume", "store._latches", "latch_id", "object()"),
    ("consume", "state.registration", "'_limiter'", "object()"),
    ("consume", "state.registration", "'_gate'", "object()"),
    ("consume", "state.registration", "'_store'", "object()"),
    ("consume", "state.registration", "'_owner_capability'", "object()"),
    ("consume", "registry", "'_owner_capability'", "object()"),
    ("consume", "limiter", "'_private_lifecycle_capability'", "object()"),
    ("consume", "store", "'_owner_capability'", "object()"),
    ("consume", "state", "'token'", "object()"),
    ("consume", "token", "'_token_id'", "UUID(int=791)"),
    ("consume", "state", "'held_reservation'", "object()"),
    ("consume", "reservation", "'_reservation_id'", "UUID(int=792)"),
    ("consume", "state", "'acquisition'", "object()"),
    ("consume", "acquisition", "'_token'", "object()"),
    ("consume", "acquisition", "'_binding'", "expected.model_copy(deep=True)"),
    ("consume", "state.registration", "'registration_id'", "UUID(int=793)"),
    ("consume", "state.registration", "'token_id'", "UUID(int=794)"),
    ("consume", "state.registration", "'outcome_slot'", "object()"),
    ("entry", "state", "'audit_identity'", "object()"),
    ("entry", "slot", "'_audit_identity'", "object()"),
    ("entry", "state", "'pre_dispatch_audit_receipt_id'", "UUID(int=795)"),
)


@pytest.mark.parametrize(("stage", "owner", "key", "replacement"), _OWNER_PARITY_CASES)
def test_every_owner_object_and_index_mismatch_terminalizes_once(
    stage: str,
    owner: str,
    key: str,
    replacement: str,
) -> None:
    from tests.unit.test_delta_private_limiter_registry_races_atomic import _run_isolated

    _run_isolated(
        f"""
        acquisition, latch, state = await acquire(1, 300.0)
        slot = state.registration.outcome_slot
        token, expected = acquisition.token, acquisition.binding
        reservation = state.held_reservation
        registration_id = state.registration.registration_id
        token_id = state.registration.token_id
        reservation_id = state.registration.held_reservation_id
        slot_id, latch_id = id(slot), id(latch)
        handle = None
        if {stage!r} == "entry":
            evidence = consume(acquisition)
            handle = registry.attach_to_venue_handoff_once(evidence, entry_latch=latch)
            audit(handle, latch)
            assert registry.transfer_attached_once(
                handle, entry_latch=latch, rejection_reason=None
            ) == "TRANSFERRED"
        target = {owner}
        if type(target) is dict:
            target[{key}] = {replacement}
        else:
            object.__setattr__(target, {key}, {replacement})
        try:
            if {stage!r} == "consume":
                registry.consume_or_invalidate_once(
                    token,
                    expected=expected,
                    actual_region=DeltaRegion.INDIA,
                    actual_environment=DeltaEnvironment.TESTNET,
                    actual_mode=ExecutionMode.LIVE,
                )
            else:
                finalize(acquisition, handle, latch, 300_100_000_000, expected)
        except ConfigurationError:
            pass
        terminal = state.disposition
        assert type(terminal) is _VenueNoEntryDisposition, (
            terminal, state.terminal_reason, state.registration_state
        )
        assert state.entered is None and state.reservation_state == "INVALIDATED"
        assert store._grants[registration_id] is state
        assert store._tokens[token_id] is state
        assert store._reservations[reservation_id] is state
        assert store._outcomes[slot_id] is state
        assert store._latches[latch_id] is state
        assert sum(event.amount for event in limiter._rest_events) == 5
        assert len(limiter._event_index) == 2 and not limiter._waiters
        try:
            await asyncio.wait_for(
                registry.acquire_mutation_once(
                    intent=support.make_intent(incarnation, ordinal=1),
                    outcome_slot=slot,
                ),
                timeout=1,
            )
        except ConfigurationError:
            pass
        else:
            raise AssertionError("terminal outcome slot was reissued")
        assert state.disposition is terminal
        """
    )


def test_registration_manifest_remains_exact_and_rejects_reconstruction() -> None:
    registration_type = completion._PrivateMutationGrantRegistration
    assert tuple(field.name for field in fields(registration_type)) == (
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
    forged = object.__new__(registration_type)
    for operation in (copy.copy, copy.deepcopy, pickle.dumps, hash):
        with pytest.raises((TypeError, AttributeError)):
            operation(forged)


@pytest.mark.parametrize("collision_index", range(5))
def test_stage_rejects_every_preallocated_identity_collision_before_gate(
    collision_index: int,
) -> None:
    from tests.unit.test_delta_private_limiter_registry_races_atomic import (
        _run_isolated as run_registry_case,
    )

    run_registry_case(
        f"""
        from hmt_crypto.exchange import private_dispatch as dispatch
        from hmt_crypto.exchange.delta import registered_limiter_grant as bridge

        intent = support.make_intent(incarnation, ordinal=2)
        slot = support.issue_outcome_slot(support.outcome_marker(), intent)
        identities = [UUID(int=value) for value in range(720, 725)]
        if {collision_index} == 0:
            identities[0] = registry._registry_owner_id
        else:
            identities[{collision_index}] = identities[0]
        before = (len(store._grants), len(store._outcomes), len(limiter._event_index))
        try:
            bridge._stage_registered_mutation(
                owner_capability=registry._owner_capability,
                limiter=limiter,
                store=store,
                intent=intent,
                outcome_slot=slot,
                audit_identity=dispatch._venue_dispatch_outcome_audit_identity(slot),
                registry_owner_id=registry._registry_owner_id,
                registry_open_epoch=registry._open_epoch,
                ownership_proof_id=proof.proof_id,
                process_id=registry._process_id,
                process_incarnation_id=incarnation,
                limiter_clock_epoch=limiter._limiter_clock_epoch,
                accounting_generation=limiter._limiter_accounting_generation,
                cooldown_epoch=limiter._cooldown_epoch,
                identities=tuple(identities),
            )
        except ConfigurationError:
            pass
        else:
            raise AssertionError("identity collision staged")
        assert (len(store._grants), len(store._outcomes), len(limiter._event_index)) == before
        """
    )


def test_stage_rejects_hostile_audit_identity_before_scalar_callbacks() -> None:
    from tests.unit.test_delta_private_limiter_registry_races_atomic import (
        _run_isolated as run_registry_case,
    )

    run_registry_case(
        """
        from hmt_crypto.exchange.delta import registered_limiter_grant as bridge

        calls = []
        class HostileAuditIdentity:
            @property
            def dispatch_event_id(self):
                calls.append("dispatch_event_id")
                raise AssertionError("hostile callback reached")

        intent = support.make_intent(incarnation, ordinal=3)
        slot = support.issue_outcome_slot(support.outcome_marker(), intent)
        try:
            bridge._stage_registered_mutation(
                owner_capability=registry._owner_capability,
                limiter=limiter,
                store=store,
                intent=intent,
                outcome_slot=slot,
                audit_identity=HostileAuditIdentity(),
                registry_owner_id=registry._registry_owner_id,
                registry_open_epoch=registry._open_epoch,
                ownership_proof_id=proof.proof_id,
                process_id=registry._process_id,
                process_incarnation_id=incarnation,
                limiter_clock_epoch=limiter._limiter_clock_epoch,
                accounting_generation=limiter._limiter_accounting_generation,
                cooldown_epoch=limiter._cooldown_epoch,
                identities=tuple(UUID(int=value) for value in range(736, 741)),
            )
        except ConfigurationError:
            pass
        else:
            raise AssertionError("hostile audit identity staged")
        assert calls == []
        """
    )


def test_bound_ids_slot_and_event_reject_before_any_new_gate_entry() -> None:
    from tests.unit.test_delta_private_limiter_registry_races_atomic import (
        _run_isolated as run_registry_case,
    )

    run_registry_case(
        """
        from hmt_crypto.exchange import private_dispatch as dispatch
        from hmt_crypto.exchange.delta import registered_limiter_grant as bridge

        intent = support.make_intent(incarnation, ordinal=4)
        slot = support.issue_outcome_slot(support.outcome_marker(), intent)
        audit_identity = dispatch._venue_dispatch_outcome_audit_identity(slot)
        values = {
            "owner_capability": registry._owner_capability,
            "limiter": limiter,
            "store": store,
            "intent": intent,
            "outcome_slot": slot,
            "audit_identity": audit_identity,
            "registry_owner_id": registry._registry_owner_id,
            "registry_open_epoch": registry._open_epoch,
            "ownership_proof_id": proof.proof_id,
            "process_id": registry._process_id,
            "process_incarnation_id": incarnation,
            "limiter_clock_epoch": limiter._limiter_clock_epoch,
            "accounting_generation": limiter._limiter_accounting_generation,
            "cooldown_epoch": limiter._cooldown_epoch,
            "identities": tuple(UUID(int=value) for value in range(742, 747)),
        }
        registration, _ = bridge._stage_registered_mutation(**values)
        with limiter._accounting_gate.hold():
            store.prepare(registration, audit_identity, support.entry_latch(slot))

        gate_type = type(limiter._accounting_gate)
        original_hold = gate_type.hold
        def forbidden_hold(_owner):
            raise AssertionError("collision reached accounting gate")
        gate_type.hold = forbidden_hold
        try:
            try:
                bridge._stage_registered_mutation(**values)
            except ConfigurationError:
                pass
            else:
                raise AssertionError("bound identity or slot staged")
            event_intent = support.make_intent(incarnation, ordinal=5)
            event_slot = support.issue_outcome_slot(support.outcome_marker(), event_intent)
            event_audit = dispatch._venue_dispatch_outcome_audit_identity(event_slot)
            event_values = values | {
                "intent": event_intent,
                "outcome_slot": event_slot,
                "audit_identity": event_audit,
                "identities": tuple(UUID(int=value) for value in range(750, 755)),
            }
            candidate_ids = (*event_values["identities"], event_audit.dispatch_event_id,
                             event_audit.consumption_id)
            for identity in candidate_ids:
                limiter._event_index[identity] = object()
                try:
                    bridge._stage_registered_mutation(**event_values)
                except ConfigurationError:
                    pass
                else:
                    raise AssertionError("bound event identity staged")
                del limiter._event_index[identity]
        finally:
            gate_type.hold = original_hold
        """
    )


def test_transition_reason_allowlists_are_closed_and_include_transfer_reuse() -> None:
    from hmt_crypto.exchange.delta import private_limiter_lifecycle as lifecycle

    enum = completion.LimiterReservationInvalidationReason
    assert lifecycle._transfer_rejection_reason(None, transferred=False) is None
    assert (
        lifecycle._transfer_rejection_reason(None, transferred=True) is enum.VENUE_TRANSFER_REUSED
    )
    assert (
        lifecycle._transfer_rejection_reason(
            enum.VENUE_TRANSFER_TARGET_MISMATCH,
            transferred=False,
        )
        is enum.VENUE_TRANSFER_TARGET_MISMATCH
    )
    with pytest.raises(ConfigurationError):
        lifecycle._transfer_rejection_reason(enum.CANCELLED, transferred=False)


def test_registered_bridge_has_precharge_validation_and_no_public_entry() -> None:
    source = inspect.getsource(bridge)
    tree = ast.parse(source)
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_validate_registered_grant_for_charge_locked" in functions
    assert bridge.__all__ == []
    assert not any(isinstance(node, ast.AsyncFunctionDef) for node in tree.body)


@pytest.mark.asyncio
async def test_forged_registration_rejects_before_scheduler_or_gate() -> None:
    from hmt_crypto.exchange.delta.private_limiter_completion import (
        _PrivateMutationGrantRegistration,
    )
    from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitRoute, RateRequest
    from hmt_crypto.exchange.delta.weighted_limiter import WeightedRateLimiter

    limiter = WeightedRateLimiter(
        RateLimitScope("india", "testnet", ScopeKind.EXTERNAL_USER, "12345")
    )
    forged = object.__new__(_PrivateMutationGrantRegistration)
    original_hold = type(limiter._accounting_gate).hold

    def forbidden_hold(_owner: object) -> object:
        raise AssertionError("malformed registration reached accounting gate")

    type(limiter._accounting_gate).hold = forbidden_hold  # type: ignore[method-assign]
    try:
        with pytest.raises(ConfigurationError):
            await limiter._acquire_registered_mutation(
                RateRequest(RateLimitRoute.PLACE_ORDER, {27: 1}),
                registration=forged,
            )
    finally:
        type(limiter._accounting_gate).hold = original_hold
    assert not limiter._waiters
    assert not limiter._event_index
