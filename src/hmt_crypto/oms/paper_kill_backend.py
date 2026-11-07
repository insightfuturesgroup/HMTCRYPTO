"""Concrete database-idempotent PAPER/BACKTEST kill-action backend."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from hmt_crypto.oms.kill_worker import KillActionInvocation, KillActionResult, KillWorkerError
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.worker.paper_control import PaperJournalKillPort

if TYPE_CHECKING:
    from hmt_crypto.oms.kill_worker import PaperKillActionAuthority


class DurablePaperKillActionBackend:
    """Execute only against the durable local simulator projection, never Delta."""

    __slots__ = ("_binding_key", "_database", "_repository", "_scope")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("durable paper kill backend cannot be subclassed")

    def __init__(self, database: Database, scope: AccountScope) -> None:
        if (
            type(database) is not Database
            or type(scope) is not AccountScope
            or scope.mode not in {"PAPER", "BACKTEST"}
        ):
            raise KillWorkerError() from None
        try:
            target = database.engine.url.render_as_string(hide_password=False).encode("utf-8")
        except Exception:
            raise KillWorkerError() from None
        self._database = database
        self._scope = AccountScope.model_validate(scope)
        self._repository = PaperJournalKillPort()
        self._binding_key = (
            sha256(target).digest(),
            scope.account_id.int,
            scope.region,
            scope.environment,
            scope.mode,
        )

    @property
    def scope(self) -> AccountScope:
        """Return the exact non-LIVE account binding."""
        return AccountScope.model_validate(self._scope)

    @property
    def binding_key(self) -> tuple[bytes, int, str, str, str]:
        """Return a secret-free stable in-process database/scope identity."""
        return self._binding_key

    def action_authority(self) -> PaperKillActionAuthority:
        """Return the sole strongly-owned action authority for this binding."""
        from hmt_crypto.oms.kill_worker import PaperKillActionAuthority

        return PaperKillActionAuthority.issue(self._scope, self)

    async def execute(self, invocation: KillActionInvocation) -> KillActionResult:
        """Commit or replay one locally simulated operation-keyed action."""
        if type(invocation) is not KillActionInvocation:
            raise KillWorkerError() from None
        async with self._database.transaction() as session:
            return await self._repository.execute_once(session, self._scope, invocation)


__all__ = ["DurablePaperKillActionBackend"]
