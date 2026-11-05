"""Exact Task 2.10 prewarmed trusted-dispatch clock contracts."""

from __future__ import annotations

import ast
import asyncio
import builtins
import copy
import importlib
import inspect
import logging
import os
import pickle
import socket
import subprocess
import sys
import textwrap
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.private_dispatch import (
    TrustedDispatchDeadline,
    TrustedDispatchInstant,
    TrustedUtcExpiry,
)
from hmt_crypto.worker import clock as task210_clock

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.integration.support.task210_worker_clock_issuer",)

_PROCESS_INCARNATION = UUID("00000000-0000-0000-0000-000000000303")
_UTC_SECONDS = 1_800_000_000
_UTC_UPPER_NS = _UTC_SECONDS * 1_000_000_000 + 250_000_000


def _issuer_support() -> ModuleType:
    return importlib.import_module("tests.integration.support.task210_worker_clock_issuer")


def _issue(marker: object) -> tuple[object, object]:
    return _issuer_support().issue_trusted_dispatch_clock(
        marker,
        clock_owner_id=UUID("00000000-0000-0000-0000-000000000301"),
        process_incarnation_id=_PROCESS_INCARNATION,
        clock_epoch=4,
        correlation_epoch=5,
        sample_monotonic_ns=10_000_000_000,
        observed_monotonic_ns=11_000_000_000,
        utc_epoch_seconds=_UTC_SECONDS,
        utc_upper_bound_ns=_UTC_UPPER_NS,
        nonblocking_execution_guard_ns=2_000_000,
    )


def _assert_safe_clock_error(call: object) -> None:
    assert callable(call)
    with pytest.raises(ConfigurationError) as captured:
        call()  # type: ignore[operator]
    assert captured.value.context == {"operation": "task210_trusted_clock"}
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "fixture-secret" not in f"{captured.value!s} {captured.value!r}"


def _utc_from_ns(value: int) -> datetime:
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=nanoseconds // 1_000)


def test_trusted_clock_exposes_only_exact_owner_identity(
    task210_worker_clock_marker: object,
) -> None:
    clock, currentness = _issue(task210_worker_clock_marker)

    assert type(clock) is task210_clock.Task210TrustedDispatchClock
    assert type(currentness) is task210_clock.Task210TrustedClockCurrentnessPort
    assert clock.clock_owner_id == UUID("00000000-0000-0000-0000-000000000301")
    assert clock.process_id == os.getpid()
    assert clock.process_incarnation_id == _PROCESS_INCARNATION
    assert clock.clock_epoch == 4
    assert clock.correlation_epoch == 5
    assert repr(clock) == "Task210TrustedDispatchClock(<redacted>)"
    assert repr(currentness) == "Task210TrustedClockCurrentnessPort(<redacted>)"
    assert {name for name in dir(clock) if not name.startswith("_")} == {
        "clock_epoch",
        "clock_owner_id",
        "correlation_epoch",
        "derive_deadline_nonblocking",
        "observe_nonblocking",
        "process_id",
        "process_incarnation_id",
    }


def test_clock_observation_uses_b2_exact_type_and_prewarmed_scalars(
    task210_worker_clock_marker: object,
) -> None:
    clock, currentness = _issue(task210_worker_clock_marker)
    assert (
        currentness.revalidate_dispatch_clock_nonblocking(
            clock,
            expected_process_id=os.getpid(),
            expected_process_incarnation_id=_PROCESS_INCARNATION,
        )
        is clock
    )

    observed = clock.observe_nonblocking(
        expected_process_incarnation_id=_PROCESS_INCARNATION,
        expected_clock_epoch=4,
    )

    assert type(observed) is TrustedDispatchInstant
    assert observed.model_dump() == {
        "process_id": os.getpid(),
        "process_incarnation_id": _PROCESS_INCARNATION,
        "clock_epoch": 4,
        "correlation_epoch": 5,
        "monotonic_ns": 11_000_000_000,
        "utc_epoch_seconds": _UTC_SECONDS,
        "utc_upper_bound_ns": _UTC_UPPER_NS,
    }


def test_clock_uuid_properties_never_expose_mutable_owner_state(
    task210_worker_clock_marker: object,
) -> None:
    clock, _currentness = _issue(task210_worker_clock_marker)
    expected = {
        "clock_owner_id": UUID("00000000-0000-0000-0000-000000000301"),
        "process_incarnation_id": _PROCESS_INCARNATION,
    }

    for name, owner_value in expected.items():
        exposed = getattr(clock, name)
        object.__setattr__(exposed, "int", 999)
        assert getattr(clock, name) == owner_value


def test_deadline_mapping_preserves_microseconds_and_takes_strict_minimum(
    task210_worker_clock_marker: object,
) -> None:
    clock, _currentness = _issue(task210_worker_clock_marker)
    utc_expiry_ns = _UTC_UPPER_NS + 8_250_123_000
    expected_utc_candidate = 10_000_000_000 + 8_250_123_000 - 2_000_000
    monotonic_candidate = expected_utc_candidate - 7

    deadline = clock.derive_deadline_nonblocking(
        utc_expiries=(
            TrustedUtcExpiry(
                expires_at=_utc_from_ns(utc_expiry_ns),
                source="LEADERSHIP",
            ),
        ),
        monotonic_deadlines_ns=(monotonic_candidate,),
        expected_process_incarnation_id=_PROCESS_INCARNATION,
        expected_clock_epoch=4,
    )

    assert type(deadline) is TrustedDispatchDeadline
    assert deadline.model_dump() == {
        "process_id": os.getpid(),
        "process_incarnation_id": _PROCESS_INCARNATION,
        "clock_epoch": 4,
        "monotonic_ns": monotonic_candidate,
        "correlation_epoch": 5,
    }


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("clock_owner_id", UUID(int=0)),
        ("process_incarnation_id", UUID(int=0)),
        ("clock_epoch", 0),
        ("clock_epoch", True),
        ("correlation_epoch", 0),
        ("sample_monotonic_ns", -1),
        ("observed_monotonic_ns", -1),
        ("observed_monotonic_ns", 9_999_999_999),
        ("utc_epoch_seconds", -1),
        ("utc_upper_bound_ns", _UTC_SECONDS * 1_000_000_000 - 1),
        ("nonblocking_execution_guard_ns", -1),
    ),
)
def test_clock_issuer_rejects_invalid_or_coercive_publication_values(
    task210_worker_clock_marker: object,
    name: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "clock_owner_id": UUID(int=301),
        "process_incarnation_id": _PROCESS_INCARNATION,
        "clock_epoch": 4,
        "correlation_epoch": 5,
        "sample_monotonic_ns": 10_000_000_000,
        "observed_monotonic_ns": 11_000_000_000,
        "utc_epoch_seconds": _UTC_SECONDS,
        "utc_upper_bound_ns": _UTC_UPPER_NS,
        "nonblocking_execution_guard_ns": 2_000_000,
    }
    payload[name] = value
    _assert_safe_clock_error(
        lambda: _issuer_support().issue_trusted_dispatch_clock(
            task210_worker_clock_marker, **payload
        )
    )


def test_clock_issuer_requires_the_exact_fixture_marker() -> None:
    _assert_safe_clock_error(lambda: _issue(object()))


def test_clock_and_currentness_are_sealed_process_local_capabilities(
    task210_worker_clock_marker: object,
) -> None:
    clock, currentness = _issue(task210_worker_clock_marker)
    for value in (clock, currentness):
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
        assert not hasattr(value, "__dict__")

    with pytest.raises(TypeError):
        task210_clock.Task210TrustedDispatchClock()
    with pytest.raises(TypeError):
        task210_clock.Task210TrustedClockCurrentnessPort()
    with pytest.raises(TypeError):
        type("ForgedClock", (task210_clock.Task210TrustedDispatchClock,), {})
    with pytest.raises(TypeError):
        type("ForgedPort", (task210_clock.Task210TrustedClockCurrentnessPort,), {})

    forged = object.__new__(task210_clock.Task210TrustedDispatchClock)
    _assert_safe_clock_error(
        lambda: currentness.revalidate_dispatch_clock_nonblocking(
            forged,
            expected_process_id=os.getpid(),
            expected_process_incarnation_id=_PROCESS_INCARNATION,
        )
    )


def test_deadline_rejects_empty_expired_wrong_epoch_and_nonexact_inputs(
    task210_worker_clock_marker: object,
) -> None:
    clock, _currentness = _issue(task210_worker_clock_marker)
    valid_expiry = TrustedUtcExpiry(
        expires_at=_utc_from_ns(_UTC_UPPER_NS + 5_000_000_000),
        source="CLOCK_READINESS",
    )
    base = {
        "utc_expiries": (valid_expiry,),
        "monotonic_deadlines_ns": (),
        "expected_process_incarnation_id": _PROCESS_INCARNATION,
        "expected_clock_epoch": 4,
    }
    changes = (
        {"utc_expiries": (), "monotonic_deadlines_ns": ()},
        {
            "utc_expiries": (
                TrustedUtcExpiry(
                    expires_at=_utc_from_ns(_UTC_UPPER_NS + 1_000_000),
                    source="LEADERSHIP",
                ),
            )
        },
        {"monotonic_deadlines_ns": (11_000_000_000,)},
        {"expected_process_incarnation_id": UUID(int=999)},
        {"expected_process_incarnation_id": str(_PROCESS_INCARNATION)},
        {"expected_clock_epoch": 5},
        {"expected_clock_epoch": True},
        {"utc_expiries": [valid_expiry]},
        {"monotonic_deadlines_ns": [20_000_000_000]},
        {"monotonic_deadlines_ns": (True,)},
    )
    for change in changes:
        _assert_safe_clock_error(
            lambda change=change: clock.derive_deadline_nonblocking(**(base | change))
        )


def test_deadline_revalidates_and_copies_every_exact_b2_expiry_before_access(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, _currentness = _issue(task210_worker_clock_marker)
    valid = TrustedUtcExpiry(
        expires_at=_utc_from_ns(_UTC_UPPER_NS + 8_000_000_000),
        source="CLOCK_READINESS",
    )
    invalid_source = valid.model_copy()
    object.__getattribute__(invalid_source, "__dict__")["source"] = "fixture-secret-invalid-source"
    missing_expiry = valid.model_copy()
    object.__getattribute__(missing_expiry, "__dict__").pop("expires_at")
    mutated_expiry = valid.model_copy()
    object.__getattribute__(mutated_expiry, "__dict__")["expires_at"] = (
        "fixture-secret-invalid-expiry"
    )
    empty_expiry = object.__new__(TrustedUtcExpiry)
    hostile_calls: list[str] = []
    side_effect_calls: list[str] = []

    class HostileExpiry:
        def __getattribute__(self, name: str) -> object:
            hostile_calls.append(name)
            raise AssertionError("fixture-secret-hostile-attribute")

        def __repr__(self) -> str:
            hostile_calls.append("repr")
            raise AssertionError("fixture-secret-hostile-repr")

    hostile_expiry = valid.model_copy()
    object.__getattribute__(hostile_expiry, "__dict__")["expires_at"] = HostileExpiry()

    def blocked(*_args: object, **_kwargs: object) -> None:
        side_effect_calls.append("called")
        raise AssertionError("fixture-secret-side-effect")

    monkeypatch.setattr(builtins, "open", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(time, "time", blocked)
    monkeypatch.setattr(time, "monotonic", blocked)
    monkeypatch.setattr(time, "sleep", blocked)
    monkeypatch.setattr(logging.Logger, "_log", blocked)

    for malformed in (
        invalid_source,
        missing_expiry,
        mutated_expiry,
        empty_expiry,
        hostile_expiry,
    ):
        _assert_safe_clock_error(
            lambda malformed=malformed: clock.derive_deadline_nonblocking(
                utc_expiries=(valid, malformed),
                monotonic_deadlines_ns=(),
                expected_process_incarnation_id=_PROCESS_INCARNATION,
                expected_clock_epoch=4,
            )
        )

    assert hostile_calls == []
    assert side_effect_calls == []


def test_clock_rejects_hostile_tuple_and_element_subclasses_without_callbacks(
    task210_worker_clock_marker: object,
) -> None:
    clock, _currentness = _issue(task210_worker_clock_marker)
    calls: list[str] = []

    class HostileTuple(tuple[object, ...]):
        def __iter__(self):
            calls.append("iter")
            raise AssertionError("fixture-secret-iteration")

    class HostileInt(int):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("fixture-secret-equality")

    for utc_values, monotonic_values in (
        (HostileTuple(), (20_000_000_000,)),
        ((), HostileTuple()),
        ((), (HostileInt(20_000_000_000),)),
    ):
        _assert_safe_clock_error(
            lambda utc_values=utc_values, monotonic_values=monotonic_values: (
                clock.derive_deadline_nonblocking(
                    utc_expiries=utc_values,  # type: ignore[arg-type]
                    monotonic_deadlines_ns=monotonic_values,  # type: ignore[arg-type]
                    expected_process_incarnation_id=_PROCESS_INCARNATION,
                    expected_clock_epoch=4,
                )
            )
        )
    assert calls == []


def test_signature_expiry_invalidation_is_terminal_and_correlation_bound(
    task210_worker_clock_marker: object,
) -> None:
    clock, currentness = _issue(task210_worker_clock_marker)
    correlation = UUID("00000000-0000-0000-0000-000000000399")

    currentness.invalidate_signature_expired_nonblocking(
        clock,
        correlation_id=correlation,
    )

    _assert_safe_clock_error(
        lambda: currentness.revalidate_dispatch_clock_nonblocking(
            clock,
            expected_process_id=os.getpid(),
            expected_process_incarnation_id=_PROCESS_INCARNATION,
        )
    )
    _assert_safe_clock_error(
        lambda: clock.observe_nonblocking(
            expected_process_incarnation_id=_PROCESS_INCARNATION,
            expected_clock_epoch=4,
        )
    )
    _assert_safe_clock_error(
        lambda: currentness.invalidate_signature_expired_nonblocking(
            clock,
            correlation_id=UUID(int=0),
        )
    )


def test_clock_currentness_rejects_wrong_bindings_before_hostile_callbacks(
    task210_worker_clock_marker: object,
) -> None:
    clock, currentness = _issue(task210_worker_clock_marker)
    calls: list[str] = []

    class HostileInt(int):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("fixture-secret-equality")

    for process_id, incarnation in (
        (os.getpid() + 1, _PROCESS_INCARNATION),
        (True, _PROCESS_INCARNATION),
        (HostileInt(os.getpid()), _PROCESS_INCARNATION),
        (os.getpid(), UUID(int=999)),
        (os.getpid(), str(_PROCESS_INCARNATION)),
    ):
        _assert_safe_clock_error(
            lambda process_id=process_id, incarnation=incarnation: (
                currentness.revalidate_dispatch_clock_nonblocking(
                    clock,
                    expected_process_id=process_id,  # type: ignore[arg-type]
                    expected_process_incarnation_id=incarnation,  # type: ignore[arg-type]
                )
            )
        )
    assert calls == []


def test_clock_is_thread_confined(task210_worker_clock_marker: object) -> None:
    clock, _currentness = _issue(task210_worker_clock_marker)
    outcomes: list[str] = []

    def run() -> None:
        try:
            clock.observe_nonblocking(
                expected_process_incarnation_id=_PROCESS_INCARNATION,
                expected_clock_epoch=4,
            )
        except ConfigurationError:
            outcomes.append("rejected")

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=2)
    assert outcomes == ["rejected"]


def test_clock_is_event_loop_confined(task210_worker_clock_marker: object) -> None:
    clock, _currentness = _issue(task210_worker_clock_marker)

    async def observe_from_another_loop() -> None:
        _assert_safe_clock_error(
            lambda: clock.observe_nonblocking(
                expected_process_incarnation_id=_PROCESS_INCARNATION,
                expected_clock_epoch=4,
            )
        )

    asyncio.run(observe_from_another_loop())


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork inheritance")
def test_clock_rejects_an_inherited_child_process(
    task210_worker_clock_marker: object,
) -> None:
    clock, _currentness = _issue(task210_worker_clock_marker)
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        outcome = b"accepted"
        try:
            clock.observe_nonblocking(
                expected_process_incarnation_id=_PROCESS_INCARNATION,
                expected_clock_epoch=4,
            )
        except ConfigurationError:
            outcome = b"rejected"
        os.write(write_fd, outcome)
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    outcome = os.read(read_fd, 32)
    os.close(read_fd)
    waited_pid, status = os.waitpid(child_pid, 0)
    assert waited_pid == child_pid
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    assert outcome == b"rejected"


def test_clock_entry_paths_never_call_io_time_sleep_or_logging(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, currentness = _issue(task210_worker_clock_marker)
    expiry = TrustedUtcExpiry(
        expires_at=_utc_from_ns(_UTC_UPPER_NS + 5_000_000_000),
        source="CLOCK_READINESS",
    )
    calls: list[str] = []

    def blocked(*_args: object, **_kwargs: object) -> None:
        calls.append("called")
        raise AssertionError("fixture-secret-side-effect")

    monkeypatch.setattr(builtins, "open", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(time, "time", blocked)
    monkeypatch.setattr(time, "monotonic", blocked)
    monkeypatch.setattr(time, "sleep", blocked)
    monkeypatch.setattr(logging.Logger, "_log", blocked)

    assert (
        currentness.revalidate_dispatch_clock_nonblocking(
            clock,
            expected_process_id=os.getpid(),
            expected_process_incarnation_id=_PROCESS_INCARNATION,
        )
        is clock
    )
    clock.observe_nonblocking(
        expected_process_incarnation_id=_PROCESS_INCARNATION,
        expected_clock_epoch=4,
    )
    clock.derive_deadline_nonblocking(
        utc_expiries=(expiry,),
        monotonic_deadlines_ns=(),
        expected_process_incarnation_id=_PROCESS_INCARNATION,
        expected_clock_epoch=4,
    )
    assert calls == []


def test_clock_import_orders_are_acyclic_and_define_no_duplicate_time_models() -> None:
    project = Path(__file__).resolve().parents[2]
    for imports in (
        ("hmt_crypto.exchange.private_dispatch", "hmt_crypto.worker.clock"),
        ("hmt_crypto.worker.clock", "hmt_crypto.exchange.private_dispatch"),
    ):
        source = textwrap.dedent(
            f"""
            import importlib
            import sys
            sys.path.insert(0, "src")
            for name in {imports!r}:
                importlib.import_module(name)
            clock = sys.modules["hmt_crypto.worker.clock"]
            dispatch = sys.modules["hmt_crypto.exchange.private_dispatch"]
            assert "TrustedDispatchInstant" not in vars(clock)
            assert "TrustedDispatchDeadline" not in vars(clock)
            assert clock._DispatchInstant is dispatch.TrustedDispatchInstant
            assert clock._DispatchDeadline is dispatch.TrustedDispatchDeadline
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


def test_clock_module_is_inert_dependency_light_and_dispatch_leaf_stays_neutral() -> None:
    project = Path(__file__).resolve().parents[2]
    clock_tree = ast.parse((project / "src/hmt_crypto/worker/clock.py").read_text())
    dispatch_tree = ast.parse((project / "src/hmt_crypto/exchange/private_dispatch.py").read_text())
    imports = {
        node.module or "" for node in ast.walk(clock_tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(clock_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    denied = (
        "httpx",
        "socket",
        "sqlalchemy",
        "logging",
        "persistence",
        "credential",
        "signer",
        "transport",
        "limiter",
        "registry",
    )
    assert not any(fragment in name for name in imports for fragment in denied)
    assert not any(
        isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(clock_tree)
    )
    dispatch_imports = {
        node.module or "" for node in ast.walk(dispatch_tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(dispatch_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any("worker" in name for name in dispatch_imports)


def test_clock_public_methods_are_synchronous_and_exactly_shaped() -> None:
    clock_type = task210_clock.Task210TrustedDispatchClock
    currentness_type = task210_clock.Task210TrustedClockCurrentnessPort
    assert tuple(inspect.signature(clock_type.derive_deadline_nonblocking).parameters) == (
        "self",
        "utc_expiries",
        "monotonic_deadlines_ns",
        "expected_process_incarnation_id",
        "expected_clock_epoch",
    )
    assert tuple(inspect.signature(clock_type.observe_nonblocking).parameters) == (
        "self",
        "expected_process_incarnation_id",
        "expected_clock_epoch",
    )
    assert tuple(
        inspect.signature(currentness_type.revalidate_dispatch_clock_nonblocking).parameters
    ) == (
        "self",
        "clock",
        "expected_process_id",
        "expected_process_incarnation_id",
    )
    assert tuple(
        inspect.signature(currentness_type.invalidate_signature_expired_nonblocking).parameters
    ) == ("self", "clock", "correlation_id")
    for method in (
        clock_type.derive_deadline_nonblocking,
        clock_type.observe_nonblocking,
        currentness_type.revalidate_dispatch_clock_nonblocking,
        currentness_type.invalidate_signature_expired_nonblocking,
    ):
        assert not inspect.iscoroutinefunction(method)


def test_clock_public_authority_members_have_docstrings() -> None:
    members = {
        task210_clock.Task210TrustedDispatchClock: (
            "clock_owner_id",
            "process_id",
            "process_incarnation_id",
            "clock_epoch",
            "correlation_epoch",
            "derive_deadline_nonblocking",
            "observe_nonblocking",
        ),
        task210_clock.Task210TrustedClockCurrentnessPort: (
            "revalidate_dispatch_clock_nonblocking",
            "invalidate_signature_expired_nonblocking",
        ),
    }
    for owner, names in members.items():
        for name in names:
            member = inspect.getattr_static(owner, name)
            target = member.fget if isinstance(member, property) else member
            assert inspect.getdoc(target), f"missing docstring: {owner.__name__}.{name}"
