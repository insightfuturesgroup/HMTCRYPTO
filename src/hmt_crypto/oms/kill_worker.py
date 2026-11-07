"""One-action-at-a-time PAPER/BACKTEST global-kill worker."""

from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID, uuid5

from hmt_crypto.oms.kill_switch import (
    KillAction,
    KillEvent,
    KillMode,
    KillRequest,
    KillSwitchError,
    apply_kill_event,
    next_kill_action,
    record_kill_event,
)
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.kill_workflow_repository import (
    KillWorkflowRepository,
    KillWorkflowRepositoryError,
)
from hmt_crypto.persistence.repository_contracts import AccountScope

if TYPE_CHECKING:
    from hmt_crypto.oms.paper_kill_backend import DurablePaperKillActionBackend

_OPERATION_NAMESPACE = UUID("9bb9f9aa-175b-4af6-8b45-9dc2125e4af8")
_AUTHORITY_ISSUER = object()
KillStepDisposition = Literal["IDLE", "ADVANCED", "COMPLETED", "INCOMPLETE", "STALE"]


class KillWorkerError(RuntimeError):
    """Report a safe worker/action failure without retrying the side effect."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("kill worker step failed")


@dataclass(frozen=True, slots=True)
class KillActionInvocation:
    """Deterministic idempotency identity for exactly one workflow action."""

    operation_key: UUID
    account_id: UUID
    region: Literal["india", "global"]
    environment: Literal["testnet", "prod"]
    execution_mode: Literal["PAPER", "BACKTEST"]
    request_id: UUID
    kill_mode: KillMode
    transition_count: int
    action: KillAction

    @classmethod
    def create(
        cls,
        scope: AccountScope,
        request: KillRequest,
        action: KillAction,
    ) -> KillActionInvocation:
        """Bind the sole valid next action to an exact non-LIVE account scope."""
        try:
            if (
                type(scope) is not AccountScope
                or scope.mode not in {"PAPER", "BACKTEST"}
                or type(request) is not KillRequest
                or type(action) is not KillAction
                or action is KillAction.NONE
                or next_kill_action(request) is not action
            ):
                raise ValueError
            operation_key = uuid5(
                _OPERATION_NAMESPACE,
                (
                    f"{scope.account_id}:{scope.region}:{scope.environment}:{scope.mode}:"
                    f"{request.request_id}:{request.mode.value}:{request.transition_count}:"
                    f"{action.value}"
                ),
            )
            return cls(
                operation_key=operation_key,
                account_id=scope.account_id,
                region=scope.region,
                environment=scope.environment,
                execution_mode=cast(Literal["PAPER", "BACKTEST"], scope.mode),
                request_id=request.request_id,
                kill_mode=request.mode,
                transition_count=request.transition_count,
                action=action,
            )
        except Exception:
            raise KillWorkerError() from None

    def __post_init__(self) -> None:
        expected = uuid5(
            _OPERATION_NAMESPACE,
            (
                f"{self.account_id}:{self.region}:{self.environment}:{self.execution_mode}:"
                f"{self.request_id}:{self.kill_mode.value}:{self.transition_count}:"
                f"{self.action.value}"
            ),
        )
        if (
            type(self.operation_key) is not UUID
            or self.operation_key != expected
            or type(self.account_id) is not UUID
            or self.account_id.int == 0
            or self.region not in {"india", "global"}
            or self.environment not in {"testnet", "prod"}
            or self.execution_mode not in {"PAPER", "BACKTEST"}
            or type(self.request_id) is not UUID
            or self.request_id.int == 0
            or type(self.kill_mode) is not KillMode
            or type(self.transition_count) is not int
            or self.transition_count < 0
            or type(self.action) is not KillAction
            or self.action is KillAction.NONE
        ):
            raise KillWorkerError() from None


@dataclass(frozen=True, slots=True)
class KillActionResult:
    """One classified action outcome; raw exchange/simulator material is excluded."""

    event: KillEvent

    def __post_init__(self) -> None:
        if type(self.event) is not KillEvent:
            raise KillWorkerError() from None


class KillActionPending(KillWorkerError):
    """The authority still owns an action beyond this bounded worker step."""

    __slots__ = ()


_AUTHORITY_LOCK = threading.RLock()
_AUTHORITIES: dict[tuple[bytes, int, str, str, str], PaperKillActionAuthority] = {}


class PaperKillActionAuthority:
    """Exact scope-bound owner of in-flight and completed idempotent PAPER actions."""

    __slots__ = ("__weakref__", "_backend", "_lock", "_results", "_scope", "_tasks")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("paper kill authority cannot be subclassed")

    def __init__(
        self,
        issuer: object,
        scope: AccountScope,
        backend: DurablePaperKillActionBackend,
    ) -> None:
        if (
            issuer is not _AUTHORITY_ISSUER
            or type(scope) is not AccountScope
            or scope.mode not in {"PAPER", "BACKTEST"}
        ):
            raise KillWorkerError() from None
        self._scope = AccountScope.model_validate(scope)
        self._backend = backend
        self._lock = asyncio.Lock()
        self._tasks: dict[UUID, asyncio.Task[KillActionResult]] = {}
        self._results: OrderedDict[UUID, KillActionResult] = OrderedDict()

    @classmethod
    def issue(
        cls,
        scope: AccountScope,
        backend: DurablePaperKillActionBackend,
    ) -> PaperKillActionAuthority:
        """Issue at most one in-process authority for an exact account scope/backend."""
        from hmt_crypto.oms.paper_kill_backend import DurablePaperKillActionBackend

        if (
            type(scope) is not AccountScope
            or type(backend) is not DurablePaperKillActionBackend
            or backend.scope != scope
        ):
            raise KillWorkerError() from None
        key = backend.binding_key
        with _AUTHORITY_LOCK:
            current = _AUTHORITIES.get(key)
            if current is not None:
                return current
            authority = cls(_AUTHORITY_ISSUER, scope, backend)
            _AUTHORITIES[key] = authority
            return authority

    @property
    def scope(self) -> AccountScope:
        """Return the exact non-LIVE account binding."""
        return AccountScope.model_validate(self._scope)

    def _matches(self, invocation: KillActionInvocation) -> bool:
        return (
            invocation.account_id == self._scope.account_id
            and invocation.region == self._scope.region
            and invocation.environment == self._scope.environment
            and invocation.execution_mode == self._scope.mode
        )

    @staticmethod
    def _observe(task: asyncio.Task[KillActionResult]) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    async def execute(
        self,
        invocation: KillActionInvocation,
        *,
        budget: timedelta,
    ) -> KillActionResult:
        """Start once or observe an existing action for at most ``budget``."""
        if (
            type(invocation) is not KillActionInvocation
            or not self._matches(invocation)
            or type(budget) is not timedelta
            or not timedelta(milliseconds=10) <= budget <= timedelta(minutes=4)
        ):
            raise KillWorkerError() from None
        async with self._lock:
            if invocation.operation_key in self._results:
                result = self._results[invocation.operation_key]
                self._results.move_to_end(invocation.operation_key)
                return result
            task = self._tasks.get(invocation.operation_key)
            if task is None:
                task = asyncio.create_task(self._backend.execute(invocation))
                task.add_done_callback(self._observe)
                self._tasks[invocation.operation_key] = task
        done, _ = await asyncio.wait({task}, timeout=budget.total_seconds())
        if not done:
            raise KillActionPending() from None
        try:
            result = task.result()
            if type(result) is not KillActionResult:
                raise KillWorkerError()
        except (asyncio.CancelledError, Exception):
            async with self._lock:
                if self._tasks.get(invocation.operation_key) is task:
                    self._tasks.pop(invocation.operation_key, None)
            raise KillWorkerError() from None
        async with self._lock:
            self._tasks.pop(invocation.operation_key, None)
            self._results[invocation.operation_key] = result
            self._results.move_to_end(invocation.operation_key)
            while len(self._results) > 4096:
                self._results.popitem(last=False)
        return result


@dataclass(frozen=True, slots=True)
class KillWorkerStep:
    """Safe result of at most one external action attempt."""

    disposition: KillStepDisposition
    command_id: UUID | None
    action: KillAction | None
    state: str | None
    transition_count: int | None


class KillWorkflowWorker:
    """Claim and advance a global kill with no internal side-effect retry loop."""

    def __init__(
        self,
        database: Database,
        scope: AccountScope,
        *,
        owner_id: UUID,
        authority: PaperKillActionAuthority,
        claim_duration: timedelta = timedelta(seconds=30),
        action_timeout: timedelta = timedelta(seconds=10),
    ) -> None:
        if (
            type(database) is not Database
            or type(scope) is not AccountScope
            or scope.mode == "LIVE"
            or type(owner_id) is not UUID
            or owner_id.int == 0
            or type(claim_duration) is not timedelta
            or not timedelta(seconds=5) <= claim_duration <= timedelta(minutes=5)
            or type(action_timeout) is not timedelta
            or not timedelta(milliseconds=10) <= action_timeout < claim_duration
            or type(authority) is not PaperKillActionAuthority
            or authority.scope != scope
        ):
            raise KillWorkerError() from None
        self._database = database
        self._scope = AccountScope.model_validate(scope)
        self._owner_id = UUID(int=owner_id.int)
        self._authority = authority
        self._claim_duration = claim_duration
        self._action_timeout = action_timeout
        self._repository = KillWorkflowRepository()

    def _invocation(self, request: KillRequest, action: KillAction) -> KillActionInvocation:
        return KillActionInvocation.create(self._scope, request, action)

    async def run_once(self) -> KillWorkerStep:
        """Perform zero or one idempotently-keyed action and persist its outcome."""
        try:
            async with self._database.transaction() as session:
                claim = await self._repository.claim_active(
                    session,
                    self._scope,
                    owner_id=self._owner_id,
                    duration=self._claim_duration,
                )
                if claim is None:
                    return KillWorkerStep("IDLE", None, None, None, None)
                request = await self._repository.start_or_load(
                    session,
                    self._scope,
                    claim,
                )
                if request.is_terminal:
                    raise KillWorkerError()
                action = next_kill_action(request)
                invocation = self._invocation(request, action)

            try:
                result = await self._authority.execute(
                    invocation,
                    budget=self._action_timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                raise KillWorkerError() from None
            if type(result) is not KillActionResult:
                raise KillWorkerError() from None
            event_id = uuid5(invocation.operation_key, result.event.value)
            evidence = record_kill_event(
                request_id=request.request_id,
                event_id=event_id,
                expected_transition_count=request.transition_count,
                event=result.event,
            )
            decision = apply_kill_event(request, evidence)
            async with self._database.transaction() as session:
                advanced = await self._repository.apply_and_finalize(
                    session,
                    self._scope,
                    claim,
                    decision,
                )
            if advanced is None:
                return KillWorkerStep(
                    "STALE",
                    request.request_id,
                    action,
                    request.state.value,
                    request.transition_count,
                )
            disposition: KillStepDisposition = "ADVANCED"
            if advanced.is_terminal:
                disposition = "COMPLETED" if advanced.succeeded else "INCOMPLETE"
            return KillWorkerStep(
                disposition,
                advanced.request_id,
                action,
                advanced.state.value,
                advanced.transition_count,
            )
        except asyncio.CancelledError:
            raise
        except (KillWorkflowRepositoryError, KillSwitchError, KillWorkerError):
            raise KillWorkerError() from None
        except Exception:
            raise KillWorkerError() from None


__all__ = [
    "KillActionInvocation",
    "KillActionPending",
    "KillActionResult",
    "KillStepDisposition",
    "KillWorkerError",
    "KillWorkerStep",
    "KillWorkflowWorker",
    "PaperKillActionAuthority",
]
