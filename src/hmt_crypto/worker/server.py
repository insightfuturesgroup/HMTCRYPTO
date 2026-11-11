"""Long-running worker entry point selected by an explicit process kind.

The market worker is public-only, the private worker is signed-read-only, and
the legacy PAPER worker owns no Live execution authority.
"""

from __future__ import annotations

import asyncio
import os
import signal

from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.config import ExecutionMode, Settings
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.logging import configure_logging, get_logger
from hmt_crypto.oms.kill_worker import KillWorkerError, KillWorkflowWorker
from hmt_crypto.oms.paper_kill_backend import DurablePaperKillActionBackend
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.worker.market_runner import MarketWorkerSettings, run_market_worker
from hmt_crypto.worker.private_runner import PrivateWorkerSettings, run_private_worker
from hmt_crypto.worker.settings import PaperWorkerSettings


async def run_paper_worker(
    settings: Settings,
    process: PaperWorkerSettings,
    *,
    stop: asyncio.Event | None = None,
) -> None:
    """Drain durable PAPER/BACKTEST controls until an owned stop is requested."""

    if (
        type(settings) is not Settings
        or type(process) is not PaperWorkerSettings
        or settings.execution_mode not in {ExecutionMode.PAPER, ExecutionMode.BACKTEST}
    ):
        raise ConfigurationError(context={"operation": "paper_worker_mode"}) from None
    shutdown = stop or asyncio.Event()
    if type(shutdown) is not asyncio.Event:
        raise TypeError("stop must be an exact asyncio.Event")
    database = Database(settings)
    scope = AccountScope(
        account_id=process.account_id,
        region=settings.delta_region.value,
        environment=settings.delta_env.value,
        mode=settings.execution_mode.value,
    )
    backend = DurablePaperKillActionBackend(database, scope)
    worker = KillWorkflowWorker(
        database,
        scope,
        owner_id=process.worker_id,
        authority=backend.action_authority(),
    )
    logger = get_logger("worker.paper")
    logger.info(
        "paper_worker_started",
        account_id=str(scope.account_id),
        region=scope.region,
        environment=scope.environment,
        mode=scope.mode,
        worker_id=str(process.worker_id),
    )
    poll_seconds = process.poll_interval_milliseconds / 1_000
    failure_seconds = process.failure_backoff_milliseconds / 1_000
    try:
        while not shutdown.is_set():
            delay = poll_seconds
            try:
                result = await worker.run_once()
                if result.disposition != "IDLE":
                    logger.info(
                        "paper_kill_step",
                        disposition=result.disposition,
                        command_id=None if result.command_id is None else str(result.command_id),
                        action=None if result.action is None else result.action.value,
                        state=result.state,
                        transition_count=result.transition_count,
                    )
                    delay = 0.0
            except asyncio.CancelledError:
                raise
            except KillWorkerError:
                logger.error("paper_kill_step_failed")
                delay = failure_seconds
            if delay <= 0:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=delay)
            except TimeoutError:
                pass
    finally:
        await database.aclose()
        logger.info("paper_worker_stopped")


def main() -> None:
    """Load settings, install graceful signal handlers, and run one worker."""

    configure_logging(json_output=True)
    worker_kind = os.environ.get("HMT_WORKER_KIND", "paper")

    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
            except (NotImplementedError, RuntimeError):
                continue
        if worker_kind == "market":
            await run_market_worker(
                ControlRuntimeSettings(),
                MarketWorkerSettings(),
                stop=stop,
            )
            return
        if worker_kind == "private":
            await run_private_worker(
                ControlRuntimeSettings(),
                PrivateWorkerSettings(),  # type: ignore[call-arg]
                stop=stop,
            )
            return
        if worker_kind != "paper":
            raise RuntimeError("unsupported worker kind")
        await run_paper_worker(
            Settings(),
            PaperWorkerSettings(),  # type: ignore[call-arg]
            stop=stop,
        )

    asyncio.run(serve())


__all__ = ["main", "run_paper_worker"]
