"""Opaque registry-backed values for the kill-switch workflow."""

from __future__ import annotations

import threading
import weakref
from typing import NamedTuple, NoReturn, Self, cast
from uuid import UUID

from hmt_crypto.oms._kill_switch_types import (
    EVENTS,
    FLATTEN_CONFIRMATION,
    MAX_I63,
    MODES,
    REACHABLE,
    STATES,
    TERMINAL_STATES,
    KillEvent,
    KillMode,
    KillState,
    KillSwitchError,
)

_REGISTRY_LOCK = threading.RLock()


def _canonical(value: object, members: tuple[object, ...]) -> bool:
    return any(value is member for member in members)


def _uuid(value: object) -> UUID | None:
    if type(value) is not UUID or value.int == 0:
        return None
    return UUID(int=value.int)


def _request_mode(flatten: object, confirmation: object) -> KillMode | None:
    if type(flatten) is not bool:
        return None
    if flatten:
        if type(confirmation) is not str or confirmation != FLATTEN_CONFIRMATION:
            return None
        return KillMode.CANCEL_AND_FLATTEN
    return KillMode.CANCEL_ONLY if confirmation is None else None


def _deny(*_args: object, **_kwargs: object) -> NoReturn:
    raise TypeError("kill-switch values are module-issued")


class _OpaqueValue:
    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("kill-switch values are module-issued")

    __setattr__ = _deny
    __delattr__ = _deny
    __copy__ = _deny
    __deepcopy__ = _deny
    __reduce__ = _deny
    __reduce_ex__ = _deny
    __getstate__ = _deny


class _RequestState(NamedTuple):
    request_id: UUID
    mode: KillMode
    state: KillState
    transition_count: int


class _EvidenceState(NamedTuple):
    request_id: UUID
    event_id: UUID
    expected_transition_count: int
    event: KillEvent


class KillRequest(_OpaqueValue):
    """Opaque workflow identity and its last committed transition version."""

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del cls, kwargs
        raise TypeError("kill requests cannot be subclassed")

    @classmethod
    def create(
        cls,
        *,
        request_id: UUID,
        flatten: bool,
        confirmation: str | None,
    ) -> KillRequest:
        """Issue an initial request; flattening requires the exact phrase."""
        try:
            mode = _request_mode(flatten, confirmation)
        except BaseException:
            mode = None
        finally:
            del confirmation
        try:
            checked_id = _uuid(request_id)
        except BaseException:
            checked_id = None
        if mode is None or checked_id is None:
            raise KillSwitchError() from None
        return _issue_request(_RequestState(checked_id, mode, KillState.REQUESTED, 0))

    @property
    def request_id(self) -> UUID:
        return UUID(int=_request_state(self).request_id.int)

    @property
    def mode(self) -> KillMode:
        return _request_state(self).mode

    @property
    def state(self) -> KillState:
        return _request_state(self).state

    @property
    def transition_count(self) -> int:
        return _request_state(self).transition_count

    @property
    def is_terminal(self) -> bool:
        """Whether no automatic workflow action remains."""
        return _request_state(self).state in TERMINAL_STATES

    @property
    def succeeded(self) -> bool:
        """Whether final exchange truth and protection were proven safe."""
        return _request_state(self).state is KillState.COMPLETED

    def __repr__(self) -> str:
        state = _request_state(self)
        return (
            f"KillRequest(mode={state.mode.value!r}, state={state.state.value!r}, "
            f"version={state.transition_count})"
        )


class KillEventEvidence(_OpaqueValue):
    """Opaque event identity bound to one request and expected CAS version."""

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del cls, kwargs
        raise TypeError("kill event evidence cannot be subclassed")

    @property
    def request_id(self) -> UUID:
        return UUID(int=_evidence_state(self).request_id.int)

    @property
    def event_id(self) -> UUID:
        return UUID(int=_evidence_state(self).event_id.int)

    @property
    def expected_transition_count(self) -> int:
        return _evidence_state(self).expected_transition_count

    @property
    def event(self) -> KillEvent:
        return _evidence_state(self).event


class _DecisionState(NamedTuple):
    request_id: UUID
    expected_mode: KillMode
    expected_state: KillState
    expected_transition_count: int
    event_id: UUID
    event: KillEvent
    next_request: KillRequest


class KillTransitionDecision(_OpaqueValue):
    """Opaque inputs for one unique-event insert plus versioned CAS update."""

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del cls, kwargs
        raise TypeError("kill decisions cannot be subclassed")

    @property
    def request_id(self) -> UUID:
        return UUID(int=_decision_state(self).request_id.int)

    @property
    def expected_mode(self) -> KillMode:
        return _decision_state(self).expected_mode

    @property
    def expected_state(self) -> KillState:
        return _decision_state(self).expected_state

    @property
    def expected_transition_count(self) -> int:
        return _decision_state(self).expected_transition_count

    @property
    def event_id(self) -> UUID:
        return UUID(int=_decision_state(self).event_id.int)

    @property
    def event(self) -> KillEvent:
        return _decision_state(self).event

    @property
    def next_request(self) -> KillRequest:
        return _decision_state(self).next_request


_REQUESTS: dict[int, object] = {}
_EVIDENCE: dict[int, object] = {}
_DECISIONS: dict[int, object] = {}


def _register(
    value: _OpaqueValue,
    state: _RequestState | _EvidenceState | _DecisionState,
    registry: dict[int, object],
) -> None:
    identity = id(value)

    def discard(reference: weakref.ReferenceType[_OpaqueValue]) -> None:
        with _REGISTRY_LOCK:
            current = registry.get(identity)
            if type(current) is tuple and current[0] is reference:
                registry.pop(identity, None)

    reference = weakref.ref(value, discard)
    registry[identity] = (reference, state)


def _lookup(value: object, registry: dict[int, object], state_type: type[object]) -> object:
    if not isinstance(value, _OpaqueValue):
        raise KillSwitchError() from None
    with _REGISTRY_LOCK:
        record = registry.get(id(value))
        if (
            type(record) is not tuple
            or len(record) != 2
            or record[0]() is not value
            or type(record[1]) is not state_type
        ):
            raise KillSwitchError() from None
        return record[1]


def _request_state(value: object) -> _RequestState:
    if type(value) is not KillRequest:
        raise KillSwitchError() from None
    return cast(_RequestState, _lookup(value, _REQUESTS, _RequestState))


def _evidence_state(value: object) -> _EvidenceState:
    if type(value) is not KillEventEvidence:
        raise KillSwitchError() from None
    return cast(_EvidenceState, _lookup(value, _EVIDENCE, _EvidenceState))


def _decision_state(value: object) -> _DecisionState:
    if type(value) is not KillTransitionDecision:
        raise KillSwitchError() from None
    state = cast(_DecisionState, _lookup(value, _DECISIONS, _DecisionState))
    _request_state(state.next_request)
    return state


def _issue_request(state: _RequestState) -> KillRequest:
    if state.transition_count not in REACHABLE.get(state.mode, {}).get(state.state, frozenset()):
        raise KillSwitchError() from None
    value = object.__new__(KillRequest)
    with _REGISTRY_LOCK:
        _register(value, state, _REQUESTS)
    return value


def _issue_decision(state: _DecisionState) -> KillTransitionDecision:
    _request_state(state.next_request)
    if state.expected_transition_count < 0:
        raise KillSwitchError() from None
    value = object.__new__(KillTransitionDecision)
    with _REGISTRY_LOCK:
        _register(value, state, _DECISIONS)
    return value


def rehydrate_kill_request(
    *,
    request_id: UUID,
    mode: KillMode,
    state: KillState,
    transition_count: int,
) -> KillRequest:
    """Rebuild only a workflow state reachable from committed transitions."""
    checked_id = _uuid(request_id)
    if (
        checked_id is None
        or not _canonical(mode, MODES)
        or not _canonical(state, STATES)
        or type(transition_count) is not int
        or not 0 <= transition_count < MAX_I63
    ):
        raise KillSwitchError() from None
    return _issue_request(_RequestState(checked_id, mode, state, transition_count))


def record_kill_event(
    *,
    request_id: UUID,
    event_id: UUID,
    expected_transition_count: int,
    event: KillEvent,
) -> KillEventEvidence:
    """Issue classified durable evidence for one expected workflow version."""
    checked_request = _uuid(request_id)
    checked_event = _uuid(event_id)
    if (
        checked_request is None
        or checked_event is None
        or type(expected_transition_count) is not int
        or not 0 <= expected_transition_count < MAX_I63
        or not _canonical(event, EVENTS)
    ):
        raise KillSwitchError() from None
    state = _EvidenceState(
        checked_request,
        checked_event,
        expected_transition_count,
        event,
    )
    value = object.__new__(KillEventEvidence)
    with _REGISTRY_LOCK:
        _register(value, state, _EVIDENCE)
    return value
