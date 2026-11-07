"""Crash-resumable global kill-switch workflow contracts."""

from __future__ import annotations

from types import TracebackType
from uuid import UUID, uuid4

import pytest

from hmt_crypto.oms import _kill_switch_values as kill_values
from hmt_crypto.oms.kill_switch import (
    KillAction,
    KillEvent,
    KillEventEvidence,
    KillMode,
    KillRequest,
    KillState,
    KillSwitchError,
    KillTransitionDecision,
    apply_kill_event,
    next_kill_action,
    record_kill_event,
    rehydrate_kill_request,
)

REQUEST_ID = UUID("10000000-0000-0000-0000-000000000001")


def request(mode: KillMode = KillMode.CANCEL_ONLY) -> KillRequest:
    return KillRequest.create(
        request_id=REQUEST_ID,
        flatten=mode is KillMode.CANCEL_AND_FLATTEN,
        confirmation=("FLATTEN_ALL_POSITIONS" if mode is KillMode.CANCEL_AND_FLATTEN else None),
    )


def evidence(
    current: KillRequest,
    event: KillEvent,
    *,
    event_id: UUID | None = None,
) -> KillEventEvidence:
    return record_kill_event(
        request_id=current.request_id,
        event_id=event_id or uuid4(),
        expected_transition_count=current.transition_count,
        event=event,
    )


def advance(current: KillRequest, event: KillEvent) -> KillRequest:
    return apply_kill_event(current, evidence(current, event)).next_request


def _production_trace(error: BaseException) -> str:
    rendered: list[str] = []
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((str(current), repr(current), repr(current.args)))
        traceback: TracebackType | None = current.__traceback__
        while traceback is not None:
            module = str(traceback.tb_frame.f_globals.get("__name__", ""))
            if module.startswith("hmt_crypto"):
                rendered.extend(
                    f"{name}={value!r}" for name, value in traceback.tb_frame.f_locals.items()
                )
            traceback = traceback.tb_next
        pending.extend((current.__cause__, current.__context__))
    return "\n".join(rendered)


def test_cancel_only_halts_then_cancels_risk_and_remainder_before_reconcile() -> None:
    current = request()
    expected = (
        (KillAction.PERSIST_RISK_HALT, KillEvent.RISK_HALT_PERSISTED),
        (
            KillAction.CANCEL_RISK_INCREASING_ORDERS,
            KillEvent.RISK_INCREASING_CANCEL_COMPLETED,
        ),
        (
            KillAction.CANCEL_REMAINING_OPEN_ORDERS,
            KillEvent.REMAINDER_CANCEL_COMPLETED,
        ),
        (KillAction.RECONCILE_EXCHANGE_TRUTH, KillEvent.RECONCILED_RETAINED),
        (KillAction.REESTABLISH_PROTECTION, KillEvent.PROTECTION_SUBMITTED),
        (KillAction.VERIFY_PROTECTION, KillEvent.PROTECTION_VERIFIED),
        (KillAction.FINAL_RECONCILIATION, KillEvent.FINAL_RECONCILED_SAFE),
    )
    for action, event in expected:
        assert next_kill_action(current) is action
        current = advance(current, event)

    assert current.state is KillState.COMPLETED
    assert current.transition_count == 7
    assert next_kill_action(current) is KillAction.NONE


def test_flatten_cannot_begin_until_risk_increasing_orders_are_cancelled() -> None:
    current = request(KillMode.CANCEL_AND_FLATTEN)
    actions: list[KillAction] = []
    for event in (
        KillEvent.RISK_HALT_PERSISTED,
        KillEvent.RISK_INCREASING_CANCEL_COMPLETED,
        KillEvent.FLATTEN_COMPLETED,
        KillEvent.REMAINDER_CANCEL_COMPLETED,
        KillEvent.RECONCILED_FLAT,
        KillEvent.FINAL_RECONCILED_SAFE,
    ):
        actions.append(next_kill_action(current))
        current = advance(current, event)

    assert actions == [
        KillAction.PERSIST_RISK_HALT,
        KillAction.CANCEL_RISK_INCREASING_ORDERS,
        KillAction.FLATTEN_POSITIONS_REDUCE_ONLY,
        KillAction.CANCEL_REMAINING_OPEN_ORDERS,
        KillAction.RECONCILE_EXCHANGE_TRUTH,
        KillAction.FINAL_RECONCILIATION,
    ]
    assert current.state is KillState.COMPLETED


@pytest.mark.parametrize(
    ("mode", "state", "count", "action"),
    (
        (KillMode.CANCEL_ONLY, KillState.REQUESTED, 0, KillAction.PERSIST_RISK_HALT),
        (
            KillMode.CANCEL_ONLY,
            KillState.CANCELLING_RISK_INCREASING,
            1,
            KillAction.CANCEL_RISK_INCREASING_ORDERS,
        ),
        (
            KillMode.CANCEL_AND_FLATTEN,
            KillState.FLATTENING,
            2,
            KillAction.FLATTEN_POSITIONS_REDUCE_ONLY,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.CANCELLING_REMAINDER,
            2,
            KillAction.CANCEL_REMAINING_OPEN_ORDERS,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.RECONCILING,
            3,
            KillAction.RECONCILE_EXCHANGE_TRUTH,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.PROTECTION_REESTABLISHING,
            4,
            KillAction.REESTABLISH_PROTECTION,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.PROTECTION_VERIFYING,
            5,
            KillAction.VERIFY_PROTECTION,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.FINAL_RECONCILING,
            6,
            KillAction.FINAL_RECONCILIATION,
        ),
        (KillMode.CANCEL_ONLY, KillState.COMPLETED, 7, KillAction.NONE),
        (KillMode.CANCEL_ONLY, KillState.INCOMPLETE, 2, KillAction.NONE),
    ),
)
def test_only_reachable_persisted_state_selects_restart_action(
    mode: KillMode,
    state: KillState,
    count: int,
    action: KillAction,
) -> None:
    restored = rehydrate_kill_request(
        request_id=REQUEST_ID,
        mode=mode,
        state=state,
        transition_count=count,
    )
    assert next_kill_action(restored) is action
    assert next_kill_action(restored) is action


@pytest.mark.parametrize(
    ("mode", "state", "count"),
    (
        (KillMode.CANCEL_ONLY, KillState.REQUESTED, 1),
        (KillMode.CANCEL_ONLY, KillState.FLATTENING, 2),
        (KillMode.CANCEL_AND_FLATTEN, KillState.PROTECTION_VERIFYING, 5),
        (KillMode.CANCEL_AND_FLATTEN, KillState.COMPLETED, 5),
        (KillMode.CANCEL_ONLY, KillState.RECONCILING, 99),
    ),
)
def test_unreachable_persisted_states_are_rejected(
    mode: KillMode,
    state: KillState,
    count: int,
) -> None:
    with pytest.raises(KillSwitchError):
        rehydrate_kill_request(
            request_id=REQUEST_ID,
            mode=mode,
            state=state,
            transition_count=count,
        )


@pytest.mark.parametrize(
    ("mode", "state", "count", "event"),
    (
        (
            KillMode.CANCEL_ONLY,
            KillState.CANCELLING_RISK_INCREASING,
            1,
            KillEvent.RISK_INCREASING_CANCEL_UNRESOLVED,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.CANCELLING_REMAINDER,
            2,
            KillEvent.REMAINDER_CANCEL_UNRESOLVED,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.RECONCILING,
            3,
            KillEvent.RECONCILIATION_UNRESOLVED,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.PROTECTION_REESTABLISHING,
            4,
            KillEvent.PROTECTION_UNRESOLVED,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.PROTECTION_VERIFYING,
            5,
            KillEvent.PROTECTION_UNVERIFIED,
        ),
        (
            KillMode.CANCEL_ONLY,
            KillState.FINAL_RECONCILING,
            6,
            KillEvent.FINAL_RECONCILIATION_UNRESOLVED,
        ),
    ),
)
def test_unresolved_evidence_never_reports_success(
    mode: KillMode,
    state: KillState,
    count: int,
    event: KillEvent,
) -> None:
    current = rehydrate_kill_request(
        request_id=REQUEST_ID,
        mode=mode,
        state=state,
        transition_count=count,
    )
    result = advance(current, event)
    assert result.state is KillState.INCOMPLETE
    assert result.is_terminal is True
    assert result.succeeded is False


def test_evidence_is_request_bound_versioned_and_replay_safe() -> None:
    current = request()
    first_evidence = evidence(current, KillEvent.RISK_HALT_PERSISTED)
    first = apply_kill_event(current, first_evidence)

    assert first.request_id == current.request_id
    assert first.event_id == first_evidence.event_id
    assert first.expected_transition_count == 0
    assert first.next_request.transition_count == 1
    with pytest.raises(KillSwitchError):
        apply_kill_event(first.next_request, first_evidence)

    wrong_request = record_kill_event(
        request_id=uuid4(),
        event_id=first_evidence.event_id,
        expected_transition_count=first_evidence.expected_transition_count,
        event=first_evidence.event,
    )
    with pytest.raises(KillSwitchError):
        apply_kill_event(current, wrong_request)


@pytest.mark.asyncio
async def test_repository_contract_can_cas_only_one_competing_event() -> None:
    class MemoryCAS:
        def __init__(self, initial: KillRequest) -> None:
            self.current = initial
            self.seen: set[UUID] = set()

        async def compare_and_swap(self, decision: KillTransitionDecision) -> bool:
            if (
                decision.event_id in self.seen
                or decision.expected_mode is not self.current.mode
                or decision.expected_state is not self.current.state
                or decision.expected_transition_count != self.current.transition_count
                or decision.request_id != self.current.request_id
            ):
                return False
            self.seen.add(decision.event_id)
            self.current = decision.next_request
            return True

    current = request()
    first = apply_kill_event(current, evidence(current, KillEvent.RISK_HALT_PERSISTED))
    second = apply_kill_event(current, evidence(current, KillEvent.RISK_HALT_PERSISTED))
    repository = MemoryCAS(current)

    assert await repository.compare_and_swap(first) is True
    assert await repository.compare_and_swap(second) is False
    assert repository.current is first.next_request


def test_illegal_or_mode_incompatible_transition_fails_closed() -> None:
    cancel = request()
    with pytest.raises(KillSwitchError):
        advance(cancel, KillEvent.REMAINDER_CANCEL_COMPLETED)

    flatten_reconcile = rehydrate_kill_request(
        request_id=REQUEST_ID,
        mode=KillMode.CANCEL_AND_FLATTEN,
        state=KillState.RECONCILING,
        transition_count=4,
    )
    with pytest.raises(KillSwitchError):
        advance(flatten_reconcile, KillEvent.RECONCILED_RETAINED)


def test_flatten_confirmation_is_exact_and_absent_from_failure_tracebacks() -> None:
    cancel = KillRequest.create(request_id=REQUEST_ID, flatten=False, confirmation=None)
    assert cancel.mode is KillMode.CANCEL_ONLY
    flatten = KillRequest.create(
        request_id=REQUEST_ID,
        flatten=True,
        confirmation="FLATTEN_ALL_POSITIONS",
    )
    assert flatten.mode is KillMode.CANCEL_AND_FLATTEN

    canary = "FLATTEN-CANARY-MUST-NOT-SURVIVE"
    with pytest.raises(KillSwitchError) as captured:
        KillRequest.create(request_id=REQUEST_ID, flatten=True, confirmation=canary)
    assert canary not in _production_trace(captured.value)


def test_injected_confirmation_validation_failure_retains_no_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Fatal(BaseException):
        pass

    canary = "FLATTEN-CONFIRMATION-TRACE-CANARY"

    def fail(_flatten: object, _confirmation: object) -> KillMode:
        raise Fatal(canary)

    monkeypatch.setattr(kill_values, "_request_mode", fail)
    with pytest.raises(KillSwitchError) as captured:
        KillRequest.create(request_id=REQUEST_ID, flatten=True, confirmation=canary)
    assert canary not in _production_trace(captured.value)


def test_request_constructor_and_unowned_objects_cannot_bypass_issuance() -> None:
    with pytest.raises(TypeError):
        KillRequest(  # type: ignore[call-arg]
            request_id=REQUEST_ID,
            mode=KillMode.CANCEL_AND_FLATTEN,
            state=KillState.COMPLETED,
            transition_count=6,
        )

    forged = object.__new__(KillRequest)
    with pytest.raises(KillSwitchError):
        next_kill_action(forged)

    owned = request(KillMode.CANCEL_AND_FLATTEN)
    with pytest.raises(AttributeError):
        object.__setattr__(owned, "state", KillState.COMPLETED)
    assert owned.state is KillState.REQUESTED
    assert owned.succeeded is False


def test_decision_is_module_issued_and_binds_prior_state_mode_and_event() -> None:
    current = request()
    decision = apply_kill_event(current, evidence(current, KillEvent.RISK_HALT_PERSISTED))

    assert decision.expected_mode is KillMode.CANCEL_ONLY
    assert decision.expected_state is KillState.REQUESTED
    assert decision.expected_transition_count == 0
    assert decision.event is KillEvent.RISK_HALT_PERSISTED
    assert decision.next_request.mode is KillMode.CANCEL_ONLY
    with pytest.raises(TypeError):
        KillTransitionDecision()  # type: ignore[call-arg]
    with pytest.raises(AttributeError):
        object.__setattr__(decision, "expected_mode", KillMode.CANCEL_AND_FLATTEN)


def test_rehydrated_other_mode_cannot_construct_a_cross_mode_cas_decision() -> None:
    cancel = request()
    flatten = rehydrate_kill_request(
        request_id=cancel.request_id,
        mode=KillMode.CANCEL_AND_FLATTEN,
        state=KillState.FLATTENING,
        transition_count=2,
    )
    flatten_decision = apply_kill_event(
        flatten,
        evidence(flatten, KillEvent.FLATTEN_COMPLETED),
    )

    assert flatten_decision.expected_mode is KillMode.CANCEL_AND_FLATTEN
    assert flatten_decision.expected_state is KillState.FLATTENING
    assert cancel.mode is KillMode.CANCEL_ONLY
    assert cancel.state is KillState.REQUESTED


@pytest.mark.parametrize("field", ["request_id", "event_id"])
def test_nil_identity_is_rejected(field: str) -> None:
    values = {
        "request_id": REQUEST_ID,
        "event_id": uuid4(),
        "expected_transition_count": 0,
        "event": KillEvent.RISK_HALT_PERSISTED,
    }
    values[field] = UUID(int=0)
    with pytest.raises(KillSwitchError):
        record_kill_event(**values)  # type: ignore[arg-type]

    with pytest.raises(KillSwitchError):
        KillRequest.create(request_id=UUID(int=0), flatten=False, confirmation=None)


@pytest.mark.parametrize("count", [True, -1, 2**63])
def test_evidence_version_is_exact_and_bounded(count: object) -> None:
    with pytest.raises(KillSwitchError):
        record_kill_event(
            request_id=REQUEST_ID,
            event_id=uuid4(),
            expected_transition_count=count,  # type: ignore[arg-type]
            event=KillEvent.RISK_HALT_PERSISTED,
        )
