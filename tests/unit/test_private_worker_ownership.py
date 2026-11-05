"""Exact Task 2.10 single-private-worker proof supplier contracts."""

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.worker import leadership

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.integration.support.task210_worker_clock_issuer",)

_ISSUED = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
_EXPIRES = _ISSUED + timedelta(seconds=30)
_PROCESS_INCARNATION = UUID("00000000-0000-0000-0000-000000000103")


def _issuer_support() -> ModuleType:
    return importlib.import_module("tests.integration.support.task210_worker_clock_issuer")


def _issue(marker: object) -> tuple[object, object]:
    return _issuer_support().issue_single_private_worker(
        marker,
        proof_id=UUID("00000000-0000-0000-0000-000000000101"),
        deployment_revision="build-2026-11-05.1",
        external_user_id="12345",
        worker_incarnation_id=UUID("00000000-0000-0000-0000-000000000102"),
        process_incarnation_id=_PROCESS_INCARNATION,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        ownership_epoch=7,
        issued_at=_ISSUED,
        expires_at=_EXPIRES,
        observed_at=_ISSUED + timedelta(seconds=1),
        process_started_monotonic_ns=10_000,
    )


def _assert_safe_worker_error(call: object) -> None:
    assert callable(call)
    with pytest.raises(ConfigurationError) as captured:
        call()  # type: ignore[operator]
    assert captured.value.context == {"operation": "task210_worker_ownership"}
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "fixture-secret" not in f"{captured.value!s} {captured.value!r}"


def test_worker_proof_exposes_only_exact_safe_owner_fields(
    task210_worker_clock_marker: object,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)

    assert type(proof) is leadership.Task210SinglePrivateWorkerProof
    assert type(currentness) is leadership.Task210PrivateWorkerCurrentnessPort
    assert proof.proof_id == UUID("00000000-0000-0000-0000-000000000101")
    assert proof.deployment_revision == "build-2026-11-05.1"
    assert proof.external_user_id == "12345"
    assert proof.worker_incarnation_id == UUID("00000000-0000-0000-0000-000000000102")
    assert proof.process_incarnation_id == _PROCESS_INCARNATION
    assert proof.process_id == os.getpid()
    assert proof.region is DeltaRegion.INDIA
    assert proof.environment is DeltaEnvironment.TESTNET
    assert proof.ownership_epoch == 7
    assert proof.issued_at == _ISSUED
    assert proof.expires_at == _EXPIRES
    assert repr(proof) == "Task210SinglePrivateWorkerProof(<redacted>)"
    assert repr(currentness) == "Task210PrivateWorkerCurrentnessPort(<redacted>)"

    public = {name for name in dir(proof) if not name.startswith("_")}
    assert public == {
        "deployment_revision",
        "environment",
        "expires_at",
        "external_user_id",
        "issued_at",
        "ownership_epoch",
        "process_id",
        "process_incarnation_id",
        "proof_id",
        "region",
        "worker_incarnation_id",
    }
    assert not any(
        fragment in name.casefold()
        for name in public
        for fragment in ("sign", "secret", "credential", "send", "transport", "client")
    )


def test_worker_currentness_returns_the_same_exact_proof(
    task210_worker_clock_marker: object,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)

    returned = currentness.revalidate_single_private_worker_nonblocking(
        proof,
        expected_external_user_id="12345",
        expected_region=DeltaRegion.INDIA,
        expected_environment=DeltaEnvironment.TESTNET,
        expected_process_id=os.getpid(),
        expected_process_incarnation_id=_PROCESS_INCARNATION,
    )

    assert returned is proof
    assert not inspect.isawaitable(returned)


def test_worker_uuid_properties_never_expose_mutable_owner_state(
    task210_worker_clock_marker: object,
) -> None:
    proof, _currentness = _issue(task210_worker_clock_marker)
    expected = {
        "proof_id": UUID("00000000-0000-0000-0000-000000000101"),
        "worker_incarnation_id": UUID("00000000-0000-0000-0000-000000000102"),
        "process_incarnation_id": _PROCESS_INCARNATION,
    }

    for name, owner_value in expected.items():
        exposed = getattr(proof, name)
        object.__setattr__(exposed, "int", 999)
        assert getattr(proof, name) == owner_value


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("proof_id", UUID(int=0)),
        ("deployment_revision", ""),
        ("deployment_revision", "contains space"),
        ("deployment_revision", "x" * 129),
        ("external_user_id", "0"),
        ("worker_incarnation_id", UUID(int=0)),
        ("process_incarnation_id", UUID(int=0)),
        ("region", DeltaRegion.GLOBAL),
        ("environment", "testnet"),
        ("ownership_epoch", 0),
        ("ownership_epoch", True),
        ("issued_at", datetime(2026, 11, 5, 10, 0)),
        ("expires_at", _ISSUED),
        ("observed_at", _EXPIRES),
        ("process_started_monotonic_ns", -1),
    ),
)
def test_worker_issuer_rejects_invalid_or_coercive_owner_values(
    task210_worker_clock_marker: object,
    name: str,
    value: object,
) -> None:
    payload: dict[str, object] = {
        "proof_id": UUID(int=101),
        "deployment_revision": "build-2026-11-05.1",
        "external_user_id": "12345",
        "worker_incarnation_id": UUID(int=102),
        "process_incarnation_id": _PROCESS_INCARNATION,
        "region": DeltaRegion.INDIA,
        "environment": DeltaEnvironment.TESTNET,
        "ownership_epoch": 7,
        "issued_at": _ISSUED,
        "expires_at": _EXPIRES,
        "observed_at": _ISSUED + timedelta(seconds=1),
        "process_started_monotonic_ns": 10_000,
    }
    payload[name] = value
    _assert_safe_worker_error(
        lambda: _issuer_support().issue_single_private_worker(
            task210_worker_clock_marker, **payload
        )
    )


def test_worker_issuer_requires_the_exact_fixture_marker() -> None:
    _assert_safe_worker_error(lambda: _issue(object()))


def test_worker_proof_and_currentness_are_sealed_process_local_capabilities(
    task210_worker_clock_marker: object,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)
    for value in (proof, currentness):
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
        leadership.Task210SinglePrivateWorkerProof()
    with pytest.raises(TypeError):
        leadership.Task210PrivateWorkerCurrentnessPort()
    with pytest.raises(TypeError):
        type("ForgedProof", (leadership.Task210SinglePrivateWorkerProof,), {})
    with pytest.raises(TypeError):
        type("ForgedPort", (leadership.Task210PrivateWorkerCurrentnessPort,), {})

    forged = object.__new__(leadership.Task210SinglePrivateWorkerProof)
    _assert_safe_worker_error(
        lambda: currentness.revalidate_single_private_worker_nonblocking(
            forged,
            expected_external_user_id="12345",
            expected_region=DeltaRegion.INDIA,
            expected_environment=DeltaEnvironment.TESTNET,
            expected_process_id=os.getpid(),
            expected_process_incarnation_id=_PROCESS_INCARNATION,
        )
    )


def test_worker_currentness_rejects_every_wrong_expected_binding_before_callbacks(
    task210_worker_clock_marker: object,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)
    calls: list[str] = []

    class HostileText(str):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("fixture-secret-equality")

        def __str__(self) -> str:
            calls.append("str")
            raise AssertionError("fixture-secret-string")

    changes = (
        {"expected_external_user_id": "54321"},
        {"expected_external_user_id": HostileText("12345")},
        {"expected_region": DeltaRegion.GLOBAL},
        {"expected_region": "india"},
        {"expected_environment": DeltaEnvironment.PROD},
        {"expected_environment": "testnet"},
        {"expected_process_id": os.getpid() + 1},
        {"expected_process_id": True},
        {"expected_process_incarnation_id": UUID(int=999)},
        {"expected_process_incarnation_id": str(_PROCESS_INCARNATION)},
    )
    base = {
        "expected_external_user_id": "12345",
        "expected_region": DeltaRegion.INDIA,
        "expected_environment": DeltaEnvironment.TESTNET,
        "expected_process_id": os.getpid(),
        "expected_process_incarnation_id": _PROCESS_INCARNATION,
    }
    for change in changes:
        _assert_safe_worker_error(
            lambda change=change: currentness.revalidate_single_private_worker_nonblocking(
                proof, **(base | change)
            )
        )
    assert calls == []


def test_worker_expiry_equality_and_owner_invalidation_are_terminal(
    task210_worker_clock_marker: object,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)
    kwargs = {
        "expected_external_user_id": "12345",
        "expected_region": DeltaRegion.INDIA,
        "expected_environment": DeltaEnvironment.TESTNET,
        "expected_process_id": os.getpid(),
        "expected_process_incarnation_id": _PROCESS_INCARNATION,
    }

    _issuer_support().advance_worker_observed_at(task210_worker_clock_marker, currentness, _EXPIRES)
    _assert_safe_worker_error(
        lambda: currentness.revalidate_single_private_worker_nonblocking(proof, **kwargs)
    )

    fresh, fresh_currentness = _issue(task210_worker_clock_marker)
    _issuer_support().invalidate_worker_ownership(task210_worker_clock_marker, fresh_currentness)
    _assert_safe_worker_error(
        lambda: fresh_currentness.revalidate_single_private_worker_nonblocking(fresh, **kwargs)
    )


def test_worker_proof_replacement_preserves_owner_and_cold_start_epoch(
    task210_worker_clock_marker: object,
) -> None:
    original, currentness = _issue(task210_worker_clock_marker)
    assert not leadership._authenticated_limiter_cold_start_allows_nonblocking(
        currentness,
        observed_monotonic_ns=300_000_009_999,
    )

    replacement = _issuer_support().replace_single_private_worker(
        task210_worker_clock_marker,
        currentness,
        current=original,
        proof_id=UUID(int=201),
        ownership_epoch=8,
        issued_at=_ISSUED + timedelta(seconds=2),
        expires_at=_EXPIRES + timedelta(seconds=10),
        observed_at=_ISSUED + timedelta(seconds=3),
    )

    assert replacement is not original
    assert replacement.ownership_epoch == 8
    assert leadership._authenticated_limiter_cold_start_allows_nonblocking(
        currentness,
        observed_monotonic_ns=300_000_010_000,
    )
    kwargs = {
        "expected_external_user_id": "12345",
        "expected_region": DeltaRegion.INDIA,
        "expected_environment": DeltaEnvironment.TESTNET,
        "expected_process_id": os.getpid(),
        "expected_process_incarnation_id": _PROCESS_INCARNATION,
    }
    _assert_safe_worker_error(
        lambda: currentness.revalidate_single_private_worker_nonblocking(original, **kwargs)
    )
    assert (
        currentness.revalidate_single_private_worker_nonblocking(replacement, **kwargs)
        is replacement
    )


def test_worker_replacement_rejects_owner_drift_or_nonincreasing_epoch(
    task210_worker_clock_marker: object,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)
    base = {
        "proof_id": UUID(int=201),
        "ownership_epoch": 8,
        "issued_at": _ISSUED + timedelta(seconds=2),
        "expires_at": _EXPIRES + timedelta(seconds=10),
        "observed_at": _ISSUED + timedelta(seconds=3),
    }
    for change in (
        {"ownership_epoch": 7},
        {"expires_at": _EXPIRES},
        {"proof_id": UUID(int=0)},
    ):
        _assert_safe_worker_error(
            lambda change=change: _issuer_support().replace_single_private_worker(
                task210_worker_clock_marker,
                currentness,
                current=proof,
                **(base | change),
            )
        )


def test_worker_replacement_never_rolls_back_published_trusted_time(
    task210_worker_clock_marker: object,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)
    published = _ISSUED + timedelta(seconds=5)
    _issuer_support().advance_worker_observed_at(
        task210_worker_clock_marker,
        currentness,
        published,
    )
    calls: list[str] = []

    class HostileTime:
        def __getattribute__(self, name: str) -> object:
            calls.append(name)
            raise AssertionError("fixture-secret-hostile-time")

    base = {
        "current": proof,
        "proof_id": UUID(int=301),
        "ownership_epoch": 8,
        "issued_at": published,
        "expires_at": _EXPIRES + timedelta(seconds=10),
        "observed_at": published,
    }
    for change in (
        {
            "issued_at": published - timedelta(microseconds=1),
            "observed_at": published + timedelta(seconds=1),
        },
        {"observed_at": published - timedelta(microseconds=1)},
        {"issued_at": HostileTime()},
        {"observed_at": HostileTime()},
    ):
        _assert_safe_worker_error(
            lambda change=change: _issuer_support().replace_single_private_worker(
                task210_worker_clock_marker,
                currentness,
                **(base | change),
            )
        )
    assert calls == []

    equal = _issuer_support().replace_single_private_worker(
        task210_worker_clock_marker,
        currentness,
        **base,
    )
    assert equal.issued_at == published
    assert not leadership._authenticated_limiter_cold_start_allows_nonblocking(
        currentness,
        observed_monotonic_ns=300_000_009_999,
    )
    later = _issuer_support().replace_single_private_worker(
        task210_worker_clock_marker,
        currentness,
        current=equal,
        proof_id=UUID(int=302),
        ownership_epoch=9,
        issued_at=published + timedelta(seconds=1),
        expires_at=_EXPIRES + timedelta(seconds=20),
        observed_at=published + timedelta(seconds=2),
    )
    assert later.issued_at > published
    assert leadership._authenticated_limiter_cold_start_allows_nonblocking(
        currentness,
        observed_monotonic_ns=300_000_010_000,
    )


def test_worker_currentness_is_thread_and_loop_confined(
    task210_worker_clock_marker: object,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)
    outcomes: list[str] = []

    def run_in_thread() -> None:
        try:
            currentness.revalidate_single_private_worker_nonblocking(
                proof,
                expected_external_user_id="12345",
                expected_region=DeltaRegion.INDIA,
                expected_environment=DeltaEnvironment.TESTNET,
                expected_process_id=os.getpid(),
                expected_process_incarnation_id=_PROCESS_INCARNATION,
            )
        except ConfigurationError:
            outcomes.append("rejected")

    thread = threading.Thread(target=run_in_thread)
    thread.start()
    thread.join(timeout=2)
    assert outcomes == ["rejected"]

    async def wrong_loop() -> None:
        run_in_thread()

    asyncio.run(wrong_loop())
    assert outcomes == ["rejected", "rejected"]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork inheritance")
def test_worker_currentness_rejects_an_inherited_child_process(
    task210_worker_clock_marker: object,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)
    parent_pid = os.getpid()
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        outcome = b"accepted"
        try:
            currentness.revalidate_single_private_worker_nonblocking(
                proof,
                expected_external_user_id="12345",
                expected_region=DeltaRegion.INDIA,
                expected_environment=DeltaEnvironment.TESTNET,
                expected_process_id=parent_pid,
                expected_process_incarnation_id=_PROCESS_INCARNATION,
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


def test_worker_entry_paths_never_call_io_time_sleep_or_logging(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof, currentness = _issue(task210_worker_clock_marker)
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
        currentness.revalidate_single_private_worker_nonblocking(
            proof,
            expected_external_user_id="12345",
            expected_region=DeltaRegion.INDIA,
            expected_environment=DeltaEnvironment.TESTNET,
            expected_process_id=os.getpid(),
            expected_process_incarnation_id=_PROCESS_INCARNATION,
        )
        is proof
    )
    assert not leadership._authenticated_limiter_cold_start_allows_nonblocking(
        currentness,
        observed_monotonic_ns=300_000_009_999,
    )
    assert calls == []


def test_worker_modules_are_inert_private_and_dependency_light() -> None:
    project = Path(__file__).resolve().parents[2]
    tree = ast.parse((project / "src/hmt_crypto/worker/leadership.py").read_text())
    imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
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
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))

    import hmt_crypto.worker as root

    assert root.__all__ == []
    assert not hasattr(root, "Task210SinglePrivateWorkerProof")
    assert not hasattr(root, "Task210PrivateWorkerCurrentnessPort")


def test_worker_owner_import_is_inert_in_a_fresh_process() -> None:
    project = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        """
        import importlib
        import sys
        sys.path.insert(0, "src")
        importlib.import_module("hmt_crypto.worker.leadership")
        forbidden = (
            "hmt_crypto.persistence.db",
            "hmt_crypto.exchange.delta.weighted_limiter",
            "hmt_crypto.exchange.delta.signer",
        )
        assert not any(name in sys.modules for name in forbidden)
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_worker_public_authority_members_have_docstrings() -> None:
    members = {
        leadership.Task210SinglePrivateWorkerProof: (
            "proof_id",
            "deployment_revision",
            "external_user_id",
            "worker_incarnation_id",
            "process_incarnation_id",
            "process_id",
            "region",
            "environment",
            "ownership_epoch",
            "issued_at",
            "expires_at",
        ),
        leadership.Task210PrivateWorkerCurrentnessPort: (
            "revalidate_single_private_worker_nonblocking",
        ),
    }
    for owner, names in members.items():
        for name in names:
            member = inspect.getattr_static(owner, name)
            target = member.fget if isinstance(member, property) else member
            assert inspect.getdoc(target), f"missing docstring: {owner.__name__}.{name}"
