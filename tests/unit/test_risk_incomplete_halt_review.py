"""Second review regressions for incomplete truth and halt freshness."""

import copy
import pickle
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from test_risk_manager import accounting, checks_by_kind, intent, policy, snapshot

import hmt_crypto.risk.models as risk_models
from hmt_crypto.risk.inputs import HaltSnapshot
from hmt_crypto.risk.manager import RiskManager
from hmt_crypto.risk.models import (
    CheckKind,
    CheckOutcome,
    Completeness,
    RiskInputError,
)

_INCOMPLETE_COMPONENT_FIELDS = (
    ("accounting", "baseline_equity"),
    ("price", "mark_price"),
    ("rate_window", "rate_policy_version"),
    ("reconciliation", "observed_at"),
    ("halt", "revision"),
)


def _state_with_incomplete(component: str) -> tuple[object, object]:
    state = snapshot()
    incomplete = replace(getattr(state, component), completeness=Completeness.MISSING)
    object.__setattr__(state, component, incomplete)
    return state, incomplete


@pytest.mark.parametrize(
    "changes",
    [
        {"baseline_equity": True},
        {"current_equity": Decimal("1e-19")},
        {"realized_pnl": "1"},
        {"unrealized_pnl": object()},
        {"unresolved_loss_reserve": -1},
        {"ledger_cursor_start": 1},
        {"ledger_cursor_end": ""},
        {"ledger_evidence_sha256": "not-a-digest"},
    ],
)
def test_incomplete_accounting_rejects_invalid_populated_optional_fields(
    changes: dict[str, object],
) -> None:
    with pytest.raises(RiskInputError):
        accounting(completeness=Completeness.MISSING, **changes)


def test_incomplete_accounting_canonicalizes_populated_decimal_fields() -> None:
    partial = accounting(
        completeness=Completeness.PARTIAL,
        baseline_equity=Decimal("1000.000000000000000000"),
        current_equity=Decimal("995.000000000000000000"),
    )

    assert partial.baseline_equity is not None
    assert partial.current_equity is not None
    assert partial.baseline_equity.as_tuple().exponent == 0
    assert partial.current_equity.as_tuple().exponent == 0


def test_incomplete_accounting_rejects_hostile_optional_value_without_callbacks() -> None:
    callbacks = 0

    class Explosive:
        def _explode(self, *_args: object) -> object:
            nonlocal callbacks
            callbacks += 1
            raise AssertionError("hostile callback ran")

        __bool__ = _explode
        __eq__ = _explode
        __hash__ = _explode
        __iter__ = _explode
        __le__ = _explode
        __lt__ = _explode
        __str__ = _explode

    with pytest.raises(RiskInputError):
        accounting(completeness=Completeness.AMBIGUOUS, realized_pnl=Explosive())
    assert callbacks == 0


@pytest.mark.parametrize(
    "round_trip",
    [copy.copy, lambda value: pickle.loads(pickle.dumps(value))],
    ids=["copy", "pickle"],
)
def test_manager_revalidates_copied_incomplete_accounting(
    round_trip: object,
) -> None:
    raw = accounting(completeness=Completeness.MISSING)
    object.__setattr__(raw, "baseline_equity", True)
    assert callable(round_trip)
    reconstructed = round_trip(raw)

    with pytest.raises(RiskInputError):
        RiskManager().evaluate(policy(), intent(), snapshot(accounting=reconstructed))


def test_incomplete_halt_rejects_invalid_populated_revision() -> None:
    with pytest.raises(RiskInputError):
        replace(snapshot().halt, completeness=Completeness.MISSING, revision=True)


def test_thirty_day_old_empty_halt_is_unknown_not_safe() -> None:
    state = snapshot()
    old = state.evaluated_at - timedelta(days=30)
    stale_halt = replace(state.halt, observed_at=old, read_at=old)

    result = RiskManager().evaluate(policy(), intent(), replace(state, halt=stale_halt))

    halt_check = checks_by_kind(result)[CheckKind.KILL_HALT]
    assert halt_check.outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert halt_check.reason_code == "STALE_EVIDENCE"  # type: ignore[attr-defined]


def test_halt_freshness_boundary_is_inclusive_and_next_microsecond_is_stale() -> None:
    state = snapshot()
    boundary = state.evaluated_at - timedelta(seconds=30)
    at_boundary = replace(state.halt, observed_at=boundary, read_at=boundary)
    outside = replace(
        state.halt,
        observed_at=boundary - timedelta(microseconds=1),
        read_at=boundary - timedelta(microseconds=1),
    )

    passing = RiskManager().evaluate(policy(), intent(), replace(state, halt=at_boundary))
    denied = RiskManager().evaluate(policy(), intent(), replace(state, halt=outside))

    passing_check = checks_by_kind(passing)[CheckKind.KILL_HALT]
    denied_check = checks_by_kind(denied)[CheckKind.KILL_HALT]
    assert passing_check.outcome is CheckOutcome.PASS  # type: ignore[attr-defined]
    assert passing_check.observed_integer == 30  # type: ignore[attr-defined]
    assert passing_check.limit_integer == 30  # type: ignore[attr-defined]
    assert denied_check.outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert denied_check.reason_code == "STALE_EVIDENCE"  # type: ignore[attr-defined]


def test_unproven_empty_halt_snapshot_is_unknown() -> None:
    unproven = HaltSnapshot(
        completeness=Completeness.MISSING,
        source=None,
        observed_at=None,
        read_at=None,
        revision=None,
        evidence_sha256=None,
        active_causes=(),
    )

    result = RiskManager().evaluate(policy(), intent(), snapshot(halt=unproven))

    halt_check = checks_by_kind(result)[CheckKind.KILL_HALT]
    assert halt_check.outcome is CheckOutcome.UNKNOWN  # type: ignore[attr-defined]
    assert halt_check.reason_code == "MISSING_EVIDENCE"  # type: ignore[attr-defined]


def test_incomplete_halt_validates_source_read_revision_and_evidence() -> None:
    reconstructed = str.__new__(
        risk_models.HaltSource,
        risk_models.HaltSource.DURABLE_HALT_LEDGER.value,
    )
    invalid_changes = (
        {"source": reconstructed},
        {"read_at": "not-a-time"},
        {"revision": True},
        {"evidence_sha256": "not-a-digest"},
    )

    for changes in invalid_changes:
        values: dict[str, object] = {
            "completeness": Completeness.PARTIAL,
            "source": None,
            "observed_at": None,
            "read_at": None,
            "revision": None,
            "evidence_sha256": None,
            "active_causes": (),
        }
        values.update(changes)
        with pytest.raises(RiskInputError):
            HaltSnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("component,field", _INCOMPLETE_COMPONENT_FIELDS)
@pytest.mark.parametrize(
    "round_trip",
    [copy.copy, lambda value: pickle.loads(pickle.dumps(value))],
    ids=["copy", "pickle"],
)
def test_manager_revalidates_copied_and_pickled_incomplete_snapshots(
    component: str,
    field: str,
    round_trip: object,
) -> None:
    state, incomplete = _state_with_incomplete(component)
    object.__setattr__(incomplete, field, True)
    assert callable(round_trip)
    reconstructed = round_trip(incomplete)
    object.__setattr__(state, component, reconstructed)

    with pytest.raises(RiskInputError):
        RiskManager().evaluate(policy(), intent(), state)  # type: ignore[arg-type]


@pytest.mark.parametrize("component,_field", _INCOMPLETE_COMPONENT_FIELDS)
def test_manager_rejects_reconstructed_incomplete_enums(
    component: str,
    _field: str,
) -> None:
    state, incomplete = _state_with_incomplete(component)
    reconstructed = str.__new__(Completeness, Completeness.MISSING.value)
    object.__setattr__(incomplete, "completeness", reconstructed)
    object.__setattr__(state, component, copy.copy(incomplete))

    with pytest.raises(RiskInputError):
        RiskManager().evaluate(policy(), intent(), state)  # type: ignore[arg-type]


@pytest.mark.parametrize("component,field", _INCOMPLETE_COMPONENT_FIELDS)
def test_manager_rejects_hostile_incomplete_fields_without_callbacks(
    component: str,
    field: str,
) -> None:
    callbacks = 0

    class Explosive:
        def _explode(self, *_args: object) -> object:
            nonlocal callbacks
            callbacks += 1
            raise AssertionError("hostile callback ran")

        __bool__ = _explode
        __eq__ = _explode
        __hash__ = _explode
        __iter__ = _explode
        __le__ = _explode
        __lt__ = _explode
        __str__ = _explode

    state, incomplete = _state_with_incomplete(component)
    object.__setattr__(incomplete, field, Explosive())
    object.__setattr__(state, component, copy.copy(incomplete))

    with pytest.raises(RiskInputError):
        RiskManager().evaluate(policy(), intent(), state)  # type: ignore[arg-type]
    assert callbacks == 0


@pytest.mark.parametrize("component,field", _INCOMPLETE_COMPONENT_FIELDS)
def test_manager_normalizes_missing_incomplete_slots_to_risk_input_error(
    component: str,
    field: str,
) -> None:
    state, incomplete = _state_with_incomplete(component)
    reconstructed = copy.copy(incomplete)
    object.__delattr__(reconstructed, field)
    object.__setattr__(state, component, reconstructed)

    with pytest.raises(RiskInputError):
        RiskManager().evaluate(policy(), intent(), state)  # type: ignore[arg-type]
