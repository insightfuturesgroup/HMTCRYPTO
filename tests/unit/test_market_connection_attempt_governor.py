"""Serialized per-egress WebSocket connection-attempt admission."""

from __future__ import annotations

import asyncio
import importlib
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import ModuleType, TracebackType
from uuid import uuid4

import pytest

import hmt_crypto.market_data.connection_attempts as attempts_module
from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitScope, ScopeKind
from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor
from hmt_crypto.market_data.reconnect_policy import ReconnectPolicy

pytest_plugins = ("tests.integration.support.task210_worker_clock_issuer",)

SECOND = 1_000_000_000
WINDOW = 300 * SECOND
DEPLOYMENT = "phase3-test-deployment"
_ISSUED = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)


class AuthorityFactory:
    def __init__(self, marker: object) -> None:
        self.marker = marker
        self.pair: tuple[object, object] | None = None

    def get(self) -> tuple[object, object]:
        if self.pair is None:
            support = importlib.import_module(
                "tests.integration.support.task210_worker_clock_issuer"
            )
            self.pair = support.issue_single_private_worker(
                self.marker,
                proof_id=uuid4(),
                deployment_revision=DEPLOYMENT,
                external_user_id="12345",
                worker_incarnation_id=uuid4(),
                process_incarnation_id=uuid4(),
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
                ownership_epoch=1,
                issued_at=_ISSUED,
                expires_at=_ISSUED + timedelta(hours=1),
                observed_at=_ISSUED + timedelta(seconds=1),
                process_started_monotonic_ns=0,
            )
        return self.pair

    def invalidate(self) -> None:
        support: ModuleType = importlib.import_module(
            "tests.integration.support.task210_worker_clock_issuer"
        )
        _proof, currentness = self.get()
        support.invalidate_worker_ownership(self.marker, currentness)


_ACTIVE_AUTHORITY: AuthorityFactory | None = None
_ACTIVE_CLOCK: Callable[[], int] | None = None
_ACTIVE_SLEEP: Callable[[int], Awaitable[None]] | None = None


def _test_monotonic_ns() -> int:
    if _ACTIVE_CLOCK is None:
        raise RuntimeError("test clock is not installed")
    return _ACTIVE_CLOCK()


async def _test_sleep_ns(delay_ns: int) -> None:
    if _ACTIVE_SLEEP is None:
        raise RuntimeError("test sleeper is not installed")
    await _ACTIVE_SLEEP(delay_ns)


@pytest.fixture(autouse=True)
def _owned_process_scope(
    task210_worker_clock_marker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global _ACTIVE_AUTHORITY, _ACTIVE_CLOCK, _ACTIVE_SLEEP
    _ACTIVE_AUTHORITY = AuthorityFactory(task210_worker_clock_marker)
    _ACTIVE_CLOCK = None
    _ACTIVE_SLEEP = None
    monkeypatch.setattr(attempts_module, "_PROCESS_OWNER_REF", None, raising=False)
    monkeypatch.setattr(attempts_module, "_system_monotonic_ns", _test_monotonic_ns)
    monkeypatch.setattr(attempts_module, "_system_sleep_ns", _test_sleep_ns)
    states = getattr(attempts_module, "_GOVERNOR_STATES", None)
    if states is not None:
        states.clear()


def governor_factory(
    egress_identity: str,
    *,
    monotonic_ns: object,
    sleep_ns: object,
    selected_policy: object = None,
) -> ConnectionAttemptGovernor:
    global _ACTIVE_CLOCK, _ACTIVE_SLEEP
    assert _ACTIVE_AUTHORITY is not None
    assert callable(monotonic_ns)
    assert callable(sleep_ns)
    _ACTIVE_CLOCK = monotonic_ns
    _ACTIVE_SLEEP = sleep_ns
    proof, currentness = _ACTIVE_AUTHORITY.get()
    parameters = inspect.signature(ConnectionAttemptGovernor.for_single_process_egress).parameters
    assert "ownership_proof" in parameters
    return ConnectionAttemptGovernor.for_single_process_egress(
        RateLimitScope(
            region="india",
            environment="testnet",
            kind=ScopeKind.IP,
            external_id=egress_identity,
        ),
        ownership_proof=proof,
        ownership_currentness=currentness,
        policy=selected_policy or policy(),
    )


class AutoTime:
    def __init__(self) -> None:
        self.now = 0
        self.sleeps: list[int] = []
        self._lock = asyncio.Lock()

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep_ns(self, delay_ns: int) -> None:
        self.sleeps.append(delay_ns)
        async with self._lock:
            self.now += delay_ns
        await asyncio.sleep(0)


class SwitchTime(AutoTime):
    def __init__(self) -> None:
        super().__init__()
        self.block = False
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def sleep_ns(self, delay_ns: int) -> None:
        self.sleeps.append(delay_ns)
        if not self.block:
            self.now += delay_ns
            await asyncio.sleep(0)
            return
        self.started.set()
        await self.release.wait()


class FatalDependency(BaseException):
    """A non-cancellation dependency failure outside ``Exception``."""


class ReprBombClock:
    def __call__(self) -> int:
        raise FatalDependency("clock-call-secret")

    def __repr__(self) -> str:
        return "<clock-dependency-repr-secret>"


class IdentityBombClock:
    def __call__(self) -> int:
        return 0

    def __getattribute__(self, name: str) -> object:
        if name in {"__self__", "__func__"}:
            raise FatalDependency("clock-identity-secret")
        return object.__getattribute__(self, name)


def policy(*, attempts: int = 120, spacing: int = 3 * SECOND) -> ReconnectPolicy:
    return ReconnectPolicy(
        max_attempts_per_window=attempts,
        min_spacing_ns=spacing,
    )


def governor(
    time: AutoTime,
    *,
    selected_policy: ReconnectPolicy | None = None,
    egress: str | None = None,
) -> ConnectionAttemptGovernor:
    return governor_factory(
        egress or f"test-{uuid4()}",
        monotonic_ns=time.monotonic_ns,
        sleep_ns=time.sleep_ns,
        selected_policy=selected_policy or policy(),
    )


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


@pytest.mark.asyncio
async def test_cold_start_waits_a_complete_window_before_first_attempt() -> None:
    time = AutoTime()
    owner = governor(time)

    result = await owner.consume_physical_open_attempt()

    assert result is None
    assert time.sleeps == [WINDOW + 1]
    assert await owner.snapshot() == (WINDOW + 1,)
    assert owner.consumed_attempt_count == 1


@pytest.mark.asyncio
async def test_same_egress_factory_returns_one_shared_owner_and_budget() -> None:
    time = AutoTime()
    egress = f"shared-{uuid4()}"
    first = governor(time, egress=egress)
    second = governor(time, egress=egress)

    assert second is first
    await first.consume_physical_open_attempt()
    await second.consume_physical_open_attempt()
    assert first.consumed_attempt_count == 2
    assert time.sleeps == [WINDOW + 1, 3 * SECOND]


@pytest.mark.asyncio
async def test_all_callers_share_spacing_and_consumption_is_atomic() -> None:
    time = AutoTime()
    owner = governor(time)
    await owner.consume_physical_open_attempt()

    await asyncio.gather(
        owner.consume_physical_open_attempt(),
        owner.consume_physical_open_attempt(),
    )

    attempted = await owner.snapshot()
    assert len(attempted) == 3
    assert attempted[1] - attempted[0] >= 3 * SECOND
    assert attempted[2] - attempted[1] >= 3 * SECOND


@pytest.mark.asyncio
async def test_window_cap_is_replanned_after_every_wake() -> None:
    time = AutoTime()
    owner = governor(time, selected_policy=policy(attempts=2, spacing=SECOND))

    await owner.consume_physical_open_attempt()
    await owner.consume_physical_open_attempt()
    await owner.consume_physical_open_attempt()

    attempted = await owner.snapshot()
    assert attempted == (WINDOW + SECOND + 1, 2 * WINDOW + 2)
    assert time.sleeps == [WINDOW + 1, SECOND, 299 * SECOND + 1]


@pytest.mark.asyncio
async def test_cancelled_wait_does_not_consume_attempt() -> None:
    time = SwitchTime()
    owner = governor(time)
    await owner.consume_physical_open_attempt()
    time.block = True

    waiting = asyncio.create_task(owner.consume_physical_open_attempt())
    await time.started.wait()
    waiting.cancel("cancellation-canary")
    with pytest.raises(asyncio.CancelledError):
        await waiting

    assert owner.consumed_attempt_count == 1
    assert await owner.snapshot() == (WINDOW + 1,)


@pytest.mark.asyncio
async def test_failed_physical_open_still_has_no_release_or_forgeable_token() -> None:
    time = AutoTime()
    owner = governor(time)

    assert await owner.consume_physical_open_attempt() is None
    assert owner.consumed_attempt_count == 1
    assert not hasattr(attempts_module, "ConnectionAttemptReservation")


@pytest.mark.asyncio
async def test_recovered_constructor_token_cannot_create_a_second_consuming_owner() -> None:
    time = AutoTime()
    owner = governor(time)
    state = attempts_module._GOVERNOR_STATES[owner]
    forged = ConnectionAttemptGovernor(
        _token=attempts_module._FACTORY_TOKEN,
        state=state,
    )

    with pytest.raises(RuntimeError, match="ownership is unavailable"):
        await forged.consume_physical_open_attempt()
    assert forged.consumed_attempt_count == 0
    assert owner.consumed_attempt_count == 0


def test_recovered_constructor_token_cannot_reinitialize_the_active_owner() -> None:
    owner = governor(AutoTime())
    state = attempts_module._GOVERNOR_STATES[owner]

    with pytest.raises(TypeError, match="require the owned factory"):
        owner.__init__(_token=attempts_module._FACTORY_TOKEN, state=state)

    assert attempts_module._GOVERNOR_STATES[owner] is state


@pytest.mark.asyncio
async def test_partial_timer_wake_fails_without_retry_spin() -> None:
    time = AutoTime()

    async def partial_sleep(delay_ns: int) -> None:
        time.sleeps.append(delay_ns)
        time.now += 1

    owner = governor_factory(
        f"partial-{uuid4()}",
        monotonic_ns=time.monotonic_ns,
        sleep_ns=partial_sleep,
    )

    with pytest.raises(RuntimeError, match="timer deadline was not reached"):
        await owner.consume_physical_open_attempt()
    assert time.sleeps == [WINDOW + 1]
    assert owner.consumed_attempt_count == 0


@pytest.mark.asyncio
async def test_monotonic_regression_fails_closed_without_consuming_attempt() -> None:
    time = AutoTime()
    owner = governor(time, selected_policy=policy(spacing=1))
    await owner.consume_physical_open_attempt()
    time.now -= 1

    with pytest.raises(RuntimeError, match="monotonic clock regressed"):
        await owner.consume_physical_open_attempt()
    time.now += 1
    assert owner.consumed_attempt_count == 1


@pytest.mark.asyncio
async def test_clock_and_sleep_dependency_failures_are_recursively_secret_safe() -> None:
    clock_canary = "CLOCK-SECRET-DO-NOT-RETAIN"

    def failed_clock() -> int:
        raise KeyboardInterrupt(clock_canary)

    with pytest.raises(RuntimeError) as clock_failure:
        governor_factory(
            f"clock-{uuid4()}",
            monotonic_ns=failed_clock,
            sleep_ns=asyncio.sleep,
        )
    assert clock_canary not in _production_trace(clock_failure.value)

    sleep_canary = "SLEEP-SECRET-DO-NOT-RETAIN"
    time = AutoTime()

    async def failed_sleep(_delay_ns: int) -> None:
        raise RuntimeError(sleep_canary)

    owner = governor_factory(
        f"sleep-{uuid4()}",
        monotonic_ns=time.monotonic_ns,
        sleep_ns=failed_sleep,
    )
    with pytest.raises(RuntimeError, match="timer is unavailable") as sleep_failure:
        await owner.consume_physical_open_attempt()
    assert sleep_canary not in _production_trace(sleep_failure.value)


def test_hostile_dependency_repr_is_absent_from_public_clock_failure_trace() -> None:
    with pytest.raises(RuntimeError) as captured:
        governor_factory(
            f"hostile-repr-{uuid4()}",
            monotonic_ns=ReprBombClock(),
            sleep_ns=asyncio.sleep,
        )

    rendered = _production_trace(captured.value)
    assert "clock-call-secret" not in rendered
    assert "clock-dependency-repr-secret" not in rendered


def test_callable_identity_never_executes_hostile_dependency_attributes() -> None:
    owner = governor_factory(
        f"hostile-identity-{uuid4()}",
        monotonic_ns=IdentityBombClock(),
        sleep_ns=asyncio.sleep,
    )

    assert type(owner) is ConnectionAttemptGovernor


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
async def test_invalid_clock_values_fail_closed(value: object) -> None:
    with pytest.raises(RuntimeError, match="monotonic clock is unavailable"):
        governor_factory(
            f"invalid-{uuid4()}",
            monotonic_ns=lambda: value,  # type: ignore[return-value]
            sleep_ns=lambda _delay: asyncio.sleep(0),
        )


def test_factory_is_mandatory_and_runtime_dependencies_are_not_injectable() -> None:
    parameters = inspect.signature(ConnectionAttemptGovernor).parameters
    assert "_token" in parameters
    factory_parameters = inspect.signature(
        ConnectionAttemptGovernor.for_single_process_egress
    ).parameters
    assert "monotonic_ns" not in factory_parameters
    assert "sleep_ns" not in factory_parameters
    assert "deployment_revision" not in factory_parameters
    time = AutoTime()
    egress = f"mismatch-{uuid4()}"
    first = governor(time, egress=egress)

    second = governor(time, egress=egress)
    assert second is first
    assert first.consumed_attempt_count == 0


def test_dependencies_policy_and_egress_must_be_exact() -> None:
    async def sleep_ns(_delay: int) -> None:
        return None

    with pytest.raises(ValueError):
        governor_factory(" bad ", monotonic_ns=lambda: 0, sleep_ns=sleep_ns)
    with pytest.raises(TypeError):
        governor_factory(
            f"bad-{uuid4()}",
            monotonic_ns=lambda: 0,
            sleep_ns=sleep_ns,
            selected_policy=object(),
        )
    assert _ACTIVE_AUTHORITY is not None
    proof, currentness = _ACTIVE_AUTHORITY.get()
    with pytest.raises(ValueError, match="egress scope"):
        ConnectionAttemptGovernor.for_single_process_egress(
            "untyped-egress",
            ownership_proof=proof,
            ownership_currentness=currentness,
        )
    with pytest.raises(ValueError, match="egress scope"):
        ConnectionAttemptGovernor.for_single_process_egress(
            RateLimitScope(
                region="india",
                environment="testnet",
                kind=ScopeKind.EXTERNAL_USER,
                external_id="not-an-ip-scope",
            ),
            ownership_proof=proof,
            ownership_currentness=currentness,
        )


def test_second_egress_alias_cannot_create_an_independent_process_budget() -> None:
    time = AutoTime()
    first = governor(time, egress="configured-egress")

    with pytest.raises(RuntimeError, match="scope mismatch"):
        governor(time, egress="alias-for-the-same-egress")

    assert first.consumed_attempt_count == 0


@pytest.mark.asyncio
async def test_lost_process_leadership_fails_before_attempt_consumption() -> None:
    time = AutoTime()
    owner = governor(time, egress="configured-egress")
    assert _ACTIVE_AUTHORITY is not None
    _ACTIVE_AUTHORITY.invalidate()

    with pytest.raises(RuntimeError, match="ownership is unavailable"):
        await owner.consume_physical_open_attempt()
    assert owner.consumed_attempt_count == 0
