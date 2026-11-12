"""Lease-fenced Delta private account supervisor with gated Live dispatch."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import desc, select

from hmt_crypto.api.delta_reconciliation import (
    PrivateReconciliationEvidence,
    read_private_reconciliation,
)
from hmt_crypto.api.live_order_http import DeltaIndiaLiveOrderHttpAdapter
from hmt_crypto.api.live_order_recovery_http import DeltaIndiaLiveOrderRecoveryAdapter
from hmt_crypto.api.secret_store import AwsDeltaSecretStore
from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.exchange.delta.signer import DeltaSigner
from hmt_crypto.live_qualification_policy import qualification_window_is_complete
from hmt_crypto.logging import get_logger
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    Account,
    AccountLiveControl,
    CatalogueProduct,
    CatalogueSnapshot,
    CredentialSecretReference,
    LiveQualificationEvidence,
    PrivateReconciliationSnapshot,
    PrivateStreamEvidence,
    PrivateWorkerLease,
)
from hmt_crypto.worker.gtt_runner import evaluate_one_gtt
from hmt_crypto.worker.live_execution import (
    LiveExecutionResult,
    LiveExecutionSettings,
    execute_one_live_submission,
)
from hmt_crypto.worker.live_order_actions import (
    DeltaIndiaLiveOrderActionAdapter,
    LiveOrderActionResult,
    execute_one_live_order_action,
)
from hmt_crypto.worker.live_recovery import LiveRecoveryResult, recover_one_live_submission
from hmt_crypto.worker.private_stream import run_private_stream
from hmt_crypto.worker.protection_sync import sync_one_protection_group
from hmt_crypto.worker.tradingview_runner import admit_one_tradingview_signal


class PrivateWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, case_sensitive=True, hide_input_in_errors=True
    )

    owner_id: UUID = Field(default_factory=uuid4, alias="HMT_PRIVATE_WORKER_ID")
    aws_region: str = Field(min_length=3, max_length=64, alias="HMT_AWS_REGION")
    secret_prefix: str = Field(default="hmt-crypto/delta", alias="HMT_DELTA_SECRET_PREFIX")
    interval_seconds: int = Field(default=15, ge=5, le=300, alias="HMT_PRIVATE_INTERVAL_SECONDS")
    lease_seconds: int = Field(default=45, ge=15, le=900, alias="HMT_PRIVATE_LEASE_SECONDS")
    read_only: bool = Field(default=True, alias="HMT_PRIVATE_READ_ONLY")

    @model_validator(mode="after")
    def _safe_identity(self) -> Self:
        if (
            self.owner_id.int == 0
            or not self.secret_prefix.startswith("hmt-crypto/")
            or self.lease_seconds < self.interval_seconds * 2
        ):
            raise ValueError("invalid private worker supervision policy")
        return self


@dataclass(frozen=True, slots=True)
class _Claim:
    account_id: UUID
    credential_version: int
    external_user_id: str
    secret_arn: str
    generation: int
    product_ids: frozenset[int]


async def _claim(
    database: Database,
    process: PrivateWorkerSettings,
    account_id: UUID,
) -> _Claim | None:
    now = datetime.now(UTC)
    async with database.transaction() as session:
        account = await session.get(Account, account_id)
        if (
            account is None
            or account.environment != "prod"
            or account.mode != "LIVE"
            or account.current_credential_version is None
            or account.external_user_id is None
        ):
            return None
        control = await session.scalar(
            select(AccountLiveControl).where(AccountLiveControl.account_id == account_id)
        )
        if control is None or control.credential_state != "verified":
            return None
        reference = await session.scalar(
            select(CredentialSecretReference).where(
                CredentialSecretReference.account_id == account_id,
                CredentialSecretReference.credential_version == account.current_credential_version,
                CredentialSecretReference.status == "verified",
            )
        )
        catalogue = await session.scalar(
            select(CatalogueSnapshot)
            .where(
                CatalogueSnapshot.region == "india",
                CatalogueSnapshot.environment == "prod",
                CatalogueSnapshot.fresh_until > now,
            )
            .order_by(desc(CatalogueSnapshot.validated_at))
            .limit(1)
        )
        if reference is None or catalogue is None:
            control.reconciliation_state = "degraded"
            control.entries_enabled = False
            control.exits_enabled = False
            control.last_error_code = "private_worker_prerequisite_missing"
            control.updated_at = now
            return None
        product_ids = frozenset(
            int(value)
            for value in (
                await session.scalars(
                    select(CatalogueProduct.product_id).where(
                        CatalogueProduct.snapshot_id == catalogue.id
                    )
                )
            ).all()
        )
        if not product_ids:
            control.reconciliation_state = "degraded"
            control.entries_enabled = False
            control.exits_enabled = False
            control.last_error_code = "private_worker_catalogue_empty"
            control.updated_at = now
            return None
        lease = await session.scalar(
            select(PrivateWorkerLease)
            .where(PrivateWorkerLease.account_id == account_id)
            .with_for_update()
        )
        if lease is None:
            lease = PrivateWorkerLease(
                id=uuid4(),
                account_id=account_id,
                owner_id=process.owner_id,
                credential_version=account.current_credential_version,
                generation=1,
                heartbeat_at=now,
                lease_until=now + timedelta(seconds=process.lease_seconds),
            )
            session.add(lease)
        elif lease.owner_id == process.owner_id:
            if lease.credential_version != account.current_credential_version:
                lease.generation += 1
            lease.credential_version = account.current_credential_version
            lease.heartbeat_at = now
            lease.lease_until = now + timedelta(seconds=process.lease_seconds)
        elif lease.lease_until <= now:
            lease.owner_id = process.owner_id
            lease.credential_version = account.current_credential_version
            lease.generation += 1
            lease.heartbeat_at = now
            lease.lease_until = now + timedelta(seconds=process.lease_seconds)
        else:
            return None
        if control.reconciliation_state != "ready":
            control.reconciliation_state = "running"
            control.entries_enabled = False
            control.exits_enabled = False
        control.updated_at = now
        return _Claim(
            account_id,
            account.current_credential_version,
            account.external_user_id,
            reference.secret_arn,
            lease.generation,
            product_ids,
        )


async def _publish(
    database: Database,
    process: PrivateWorkerSettings,
    claim: _Claim,
    evidence: PrivateReconciliationEvidence,
) -> bool:
    now = datetime.now(UTC)
    async with database.transaction() as session:
        lease = await session.scalar(
            select(PrivateWorkerLease)
            .where(PrivateWorkerLease.account_id == claim.account_id)
            .with_for_update()
        )
        account = await session.get(Account, claim.account_id)
        if (
            lease is None
            or lease.owner_id != process.owner_id
            or lease.generation != claim.generation
            or lease.credential_version != claim.credential_version
            or lease.lease_until <= now
            or account is None
            or account.current_credential_version != claim.credential_version
        ):
            return False
        if type(evidence) is not PrivateReconciliationEvidence:
            return False
        outcome = "ready" if evidence.complete and not evidence.foreign_product_ids else "blocked"
        latest = await session.scalar(
            select(PrivateReconciliationSnapshot)
            .where(PrivateReconciliationSnapshot.account_id == claim.account_id)
            .order_by(desc(PrivateReconciliationSnapshot.observed_at))
            .limit(1)
        )
        publish_snapshot = (
            latest is None
            or latest.content_sha256 != evidence.content_sha256
            or latest.outcome != outcome
            or latest.credential_version != claim.credential_version
            or latest.worker_owner_id != process.owner_id
            or latest.worker_generation != claim.generation
            or latest.observed_at <= now - timedelta(minutes=5)
        )
        if publish_snapshot:
            session.add(
                PrivateReconciliationSnapshot(
                    id=uuid4(),
                    account_id=claim.account_id,
                    credential_version=claim.credential_version,
                    request_id=uuid4(),
                    balance_count=evidence.balance_count,
                    position_count=evidence.position_count,
                    open_order_count=evidence.open_order_count,
                    fill_count=evidence.fill_count,
                    wallet_transaction_count=evidence.wallet_transaction_count,
                    foreign_product_ids=json.dumps(evidence.foreign_product_ids),
                    complete=evidence.complete,
                    content_sha256=evidence.content_sha256,
                    outcome=outcome,
                    balances_payload=evidence.balances_payload,
                    positions_payload=evidence.positions_payload,
                    open_orders_payload=evidence.open_orders_payload,
                    fills_payload=evidence.fills_payload,
                    wallet_transactions_payload=evidence.wallet_transactions_payload,
                    worker_owner_id=process.owner_id,
                    worker_generation=claim.generation,
                    observed_at=now,
                )
            )
        control = await session.scalar(
            select(AccountLiveControl).where(AccountLiveControl.account_id == claim.account_id)
        )
        if control is None:
            return False
        control.reconciliation_state = outcome
        if process.read_only or outcome != "ready":
            control.entries_enabled = False
            control.exits_enabled = False
        control.reconciled_at = now
        control.last_error_code = None if outcome == "ready" else "foreign_or_incomplete_state"
        control.updated_at = now
        return True


async def _degrade(database: Database, process: PrivateWorkerSettings, claim: _Claim) -> None:
    now = datetime.now(UTC)
    async with database.transaction() as session:
        lease = await session.scalar(
            select(PrivateWorkerLease).where(PrivateWorkerLease.account_id == claim.account_id)
        )
        if (
            lease is None
            or lease.owner_id != process.owner_id
            or lease.generation != claim.generation
            or lease.credential_version != claim.credential_version
        ):
            return
        control = await session.scalar(
            select(AccountLiveControl).where(AccountLiveControl.account_id == claim.account_id)
        )
        if control is not None:
            control.reconciliation_state = "degraded"
            control.entries_enabled = False
            control.exits_enabled = False
            control.last_error_code = "private_worker_read_failed"
            control.updated_at = now


async def _execute_qualified_live_submission(
    database: Database,
    process: PrivateWorkerSettings,
    claim: _Claim,
    execution: LiveExecutionSettings,
    *,
    api_key: str,
    api_secret: str,
) -> LiveExecutionResult:
    """Bind one secret version and one lease owner to the one-shot adapter."""
    if not execution.enabled:
        return LiveExecutionResult("DISABLED", None)
    if not await _dispatch_qualification_is_current(database, claim, execution):
        return LiveExecutionResult("QUALIFICATION_MISSING", None)
    signer = DeltaSigner(
        api_key=SecretStr(api_key),
        api_secret=SecretStr(api_secret),
        user_agent="hmt-crypto-live-worker/1.0",
        clock=lambda: int(time.time()),
    )
    adapter = DeltaIndiaLiveOrderHttpAdapter(signer=signer)
    try:
        return await execute_one_live_submission(
            database,
            settings=execution,
            account_id=claim.account_id,
            owner_id=process.owner_id,
            external_user_id=claim.external_user_id,
            adapter=adapter,
        )
    finally:
        await adapter.aclose()


async def _dispatch_qualification_is_current(
    database: Database,
    claim: _Claim,
    execution: LiveExecutionSettings,
) -> bool:
    digest = execution.qualification_sha256
    if digest is None:
        return False
    now = datetime.now(UTC)
    async with database.transaction() as session:
        evidence = await session.scalar(
            select(LiveQualificationEvidence).where(
                LiveQualificationEvidence.account_id == claim.account_id,
                LiveQualificationEvidence.evidence_sha256 == digest,
                LiveQualificationEvidence.status == "qualified",
            )
        )
        streams = list(
            await session.scalars(
                select(PrivateStreamEvidence).where(
                    PrivateStreamEvidence.account_id == claim.account_id,
                    PrivateStreamEvidence.credential_version == claim.credential_version,
                    PrivateStreamEvidence.worker_generation == claim.generation,
                    PrivateStreamEvidence.observed_at >= now - timedelta(seconds=45),
                    PrivateStreamEvidence.state.in_(("snapshot", "live")),
                )
            )
        )
    if (
        evidence is None
        or evidence.window_completed_at is None
        or not qualification_window_is_complete(
            started_at=evidence.window_started_at,
            completed_at=evidence.window_completed_at,
        )
    ):
        return False
    return {row.channel for row in streams} == {
        "orders",
        "positions",
        "margins",
        "v2/user_trades",
    }


async def _execute_qualified_live_action(
    database: Database,
    process: PrivateWorkerSettings,
    claim: _Claim,
    execution: LiveExecutionSettings,
    *,
    api_key: str,
    api_secret: str,
) -> LiveOrderActionResult:
    if not execution.enabled:
        return LiveOrderActionResult("DISABLED", None)
    signer = DeltaSigner(
        api_key=SecretStr(api_key),
        api_secret=SecretStr(api_secret),
        user_agent="hmt-crypto-live-action/1.0",
        clock=lambda: int(time.time()),
    )
    adapter = DeltaIndiaLiveOrderActionAdapter(signer=signer)
    try:
        return await execute_one_live_order_action(
            database,
            settings=execution,
            account_id=claim.account_id,
            owner_id=process.owner_id,
            adapter=adapter,
        )
    finally:
        await adapter.aclose()


async def _recover_live_submission(
    database: Database,
    process: PrivateWorkerSettings,
    claim: _Claim,
    *,
    api_key: str,
    api_secret: str,
) -> LiveRecoveryResult:
    signer = DeltaSigner(
        api_key=SecretStr(api_key),
        api_secret=SecretStr(api_secret),
        user_agent="hmt-crypto-live-recovery/1.0",
        clock=lambda: int(time.time()),
    )
    adapter = DeltaIndiaLiveOrderRecoveryAdapter(signer=signer)
    try:
        return await recover_one_live_submission(
            database,
            account_id=claim.account_id,
            owner_id=process.owner_id,
            external_user_id=claim.external_user_id,
            adapter=adapter,
        )
    finally:
        await adapter.aclose()


async def run_private_worker(
    runtime: ControlRuntimeSettings,
    process: PrivateWorkerSettings,
    *,
    stop: asyncio.Event,
) -> None:
    if (
        runtime.execution_mode is not ExecutionMode.LIVE
        or runtime.delta_region is not DeltaRegion.INDIA
        or runtime.delta_env is not DeltaEnvironment.PROD
    ):
        raise ValueError("private worker requires Delta India production Live scope")
    database = Database(runtime.database_runtime_config())
    secrets = AwsDeltaSecretStore(region=process.aws_region, prefix=process.secret_prefix)
    execution = LiveExecutionSettings()
    logger = get_logger("worker.private")
    stream_tasks: dict[UUID, tuple[int, int, asyncio.Task[None]]] = {}
    logger.info("private_worker_started", owner_id=str(process.owner_id))
    try:
        while not stop.is_set():
            try:
                async with database.transaction() as session:
                    account_ids = tuple(
                        await session.scalars(
                            select(AccountLiveControl.account_id)
                            .join(Account, Account.id == AccountLiveControl.account_id)
                            .where(
                                AccountLiveControl.credential_state == "verified",
                                Account.archived_at.is_(None),
                            )
                        )
                    )
                for account_id in account_ids:
                    claim = await _claim(database, process, account_id)
                    if claim is None:
                        continue
                    try:
                        api_key, api_secret = await secrets.read(claim.secret_arn)
                        try:
                            evidence = await read_private_reconciliation(
                                api_key=api_key,
                                api_secret=api_secret,
                                expected_user_id=claim.external_user_id,
                                allowed_product_ids=claim.product_ids,
                            )
                            if await _publish(database, process, claim, evidence):
                                stream = stream_tasks.get(account_id)
                                identity = (claim.credential_version, claim.generation)
                                if stream is None or stream[:2] != identity or stream[2].done():
                                    if stream is not None:
                                        stream[2].cancel()
                                    task = asyncio.create_task(
                                        run_private_stream(
                                            database,
                                            account_id=claim.account_id,
                                            owner_id=process.owner_id,
                                            generation=claim.generation,
                                            credential_version=claim.credential_version,
                                            api_key=api_key,
                                            api_secret=api_secret,
                                            stop=stop,
                                        )
                                    )
                                    stream_tasks[account_id] = (*identity, task)
                                protection_created = await sync_one_protection_group(
                                    database,
                                    account_id=claim.account_id,
                                    owner_id=process.owner_id,
                                )
                                if protection_created:
                                    logger.info(
                                        "protection_group_armed",
                                        account_id=str(account_id),
                                    )
                                logger.info(
                                    "private_reconciliation_published",
                                    account_id=str(account_id),
                                    generation=claim.generation,
                                )
                                if process.read_only:
                                    continue
                                gtt_result = await evaluate_one_gtt(
                                    database,
                                    account_id=claim.account_id,
                                    owner_id=process.owner_id,
                                )
                                if gtt_result.kind not in {"IDLE", "ARMED"}:
                                    logger.info(
                                        "gtt_cycle",
                                        account_id=str(account_id),
                                        outcome=gtt_result.kind,
                                        gtt_id=(
                                            None
                                            if gtt_result.gtt_id is None
                                            else str(gtt_result.gtt_id)
                                        ),
                                        submission_id=(
                                            None
                                            if gtt_result.submission_id is None
                                            else str(gtt_result.submission_id)
                                        ),
                                    )
                                tradingview_result = await admit_one_tradingview_signal(
                                    database,
                                    account_id=claim.account_id,
                                    owner_id=process.owner_id,
                                )
                                if tradingview_result.kind not in {"IDLE", "DEFERRED"}:
                                    logger.info(
                                        "tradingview_signal_cycle",
                                        account_id=str(account_id),
                                        outcome=tradingview_result.kind,
                                        signal_id=(
                                            None
                                            if tradingview_result.signal_id is None
                                            else str(tradingview_result.signal_id)
                                        ),
                                        submission_id=(
                                            None
                                            if tradingview_result.submission_id is None
                                            else str(tradingview_result.submission_id)
                                        ),
                                        reason=tradingview_result.reason,
                                    )
                                action_result = await _execute_qualified_live_action(
                                    database,
                                    process,
                                    claim,
                                    execution,
                                    api_key=api_key,
                                    api_secret=api_secret,
                                )
                                if action_result.kind not in {"IDLE", "DISABLED"}:
                                    logger.info(
                                        "live_order_action_cycle",
                                        account_id=str(account_id),
                                        outcome=action_result.kind,
                                        action_id=(
                                            None
                                            if action_result.action_id is None
                                            else str(action_result.action_id)
                                        ),
                                    )
                                recovery = await _recover_live_submission(
                                    database,
                                    process,
                                    claim,
                                    api_key=api_key,
                                    api_secret=api_secret,
                                )
                                if recovery.kind != "IDLE":
                                    logger.info(
                                        "live_recovery_cycle",
                                        account_id=str(account_id),
                                        outcome=recovery.kind,
                                        submission_id=(
                                            None
                                            if recovery.submission_id is None
                                            else str(recovery.submission_id)
                                        ),
                                    )
                                result = await _execute_qualified_live_submission(
                                    database,
                                    process,
                                    claim,
                                    execution,
                                    api_key=api_key,
                                    api_secret=api_secret,
                                )
                                if result.kind != "DISABLED":
                                    logger.info(
                                        "live_submission_cycle",
                                        account_id=str(account_id),
                                        outcome=result.kind,
                                        submission_id=(
                                            None
                                            if result.submission_id is None
                                            else str(result.submission_id)
                                        ),
                                    )
                        finally:
                            api_key = ""
                            api_secret = ""
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        await _degrade(database, process, claim)
                        logger.error("private_account_cycle_failed", account_id=str(account_id))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("private_supervision_cycle_failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=process.interval_seconds)
            except TimeoutError:
                pass
    finally:
        for _, _, task in stream_tasks.values():
            task.cancel()
        await asyncio.gather(
            *(task for _, _, task in stream_tasks.values()),
            return_exceptions=True,
        )
        await database.aclose()
        logger.info("private_worker_stopped")


__all__ = ["PrivateWorkerSettings", "run_private_worker"]
