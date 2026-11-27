"""Lease-fenced Delta private account supervisor with gated Live dispatch."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import desc, select
from sqlalchemy.orm import load_only

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
from hmt_crypto.fast_path import ACCOUNT_CHANNEL, ORDER_CHANNEL, publish_json, wait_for_message
from hmt_crypto.live_qualification_policy import (
    LIVE_QUALIFICATION_MIN_MINUTES,
    LIVE_RECONCILIATION_EVIDENCE_INTERVAL_SECONDS,
)
from hmt_crypto.logging import get_logger
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    Account,
    AccountLiveControl,
    AccountRiskPolicy,
    CatalogueProduct,
    CatalogueSnapshot,
    CredentialSecretReference,
    LiveOrderAction,
    LiveOrderIntent,
    LiveOrderSubmission,
    LiveQualificationEvidence,
    MarketQuote,
    PlatformAuditEvent,
    PlatformLiveControl,
    PrivateReconciliationSnapshot,
    PrivateStreamEvidence,
    PrivateWorkerLease,
    TradingViewSignal,
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
from hmt_crypto.worker.operational_retention import prune_operational_history
from hmt_crypto.worker.private_stream import run_private_stream
from hmt_crypto.worker.protection_sync import sync_one_protection_group
from hmt_crypto.worker.scope import normalize_worker_uuid
from hmt_crypto.worker.tradingview_runner import admit_one_tradingview_signal


class PrivateWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, case_sensitive=True, hide_input_in_errors=True
    )

    owner_id: UUID = Field(default_factory=uuid4, alias="HMT_PRIVATE_WORKER_ID")
    aws_region: str = Field(min_length=3, max_length=64, alias="HMT_AWS_REGION")
    secret_prefix: str = Field(default="hmt-crypto/delta", alias="HMT_DELTA_SECRET_PREFIX")
    interval_seconds: int = Field(default=30, ge=5, le=300, alias="HMT_PRIVATE_INTERVAL_SECONDS")
    lease_seconds: int = Field(default=90, ge=15, le=900, alias="HMT_PRIVATE_LEASE_SECONDS")
    read_only: bool = Field(default=True, alias="HMT_PRIVATE_READ_ONLY")
    operational_retention_days: int = Field(
        default=7, ge=1, le=90, alias="HMT_OPERATIONAL_RETENTION_DAYS"
    )

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


async def _auto_activate_healthy_account(
    database: Database,
    process: PrivateWorkerSettings,
    claim: _Claim,
    execution: LiveExecutionSettings,
) -> str:
    """Qualify and open a healthy account without weakening emergency controls."""
    if process.read_only or not execution.enabled:
        return "deployment_locked"
    now = datetime.now(UTC)
    binding_payload: str | None = None
    async with database.transaction() as session:
        account = await session.get(Account, claim.account_id)
        lease = await session.scalar(
            select(PrivateWorkerLease).where(
                PrivateWorkerLease.account_id == claim.account_id,
                PrivateWorkerLease.owner_id == process.owner_id,
                PrivateWorkerLease.generation == claim.generation,
                PrivateWorkerLease.credential_version == claim.credential_version,
                PrivateWorkerLease.lease_until > now,
            )
        )
        control = await session.scalar(
            select(AccountLiveControl)
            .where(AccountLiveControl.account_id == claim.account_id)
            .with_for_update()
        )
        global_control = await session.get(PlatformLiveControl, "global", with_for_update=True)
        if account is None or control is None or global_control is None:
            return "live_control_unavailable"
        if account.environment != "prod" or account.mode != "LIVE":
            return "account_not_live"
        if control.credential_state != "verified" or lease is None:
            return "worker_or_credential_not_ready"
        snapshot = await session.scalar(
            select(PrivateReconciliationSnapshot)
            .options(
                load_only(
                    PrivateReconciliationSnapshot.id,
                    PrivateReconciliationSnapshot.observed_at,
                    PrivateReconciliationSnapshot.credential_version,
                    PrivateReconciliationSnapshot.outcome,
                    PrivateReconciliationSnapshot.complete,
                    PrivateReconciliationSnapshot.worker_owner_id,
                    PrivateReconciliationSnapshot.worker_generation,
                )
            )
            .where(
                PrivateReconciliationSnapshot.account_id == claim.account_id,
                PrivateReconciliationSnapshot.outcome == "ready",
                PrivateReconciliationSnapshot.complete.is_(True),
                PrivateReconciliationSnapshot.worker_owner_id == process.owner_id,
                PrivateReconciliationSnapshot.worker_generation == claim.generation,
                PrivateReconciliationSnapshot.credential_version == claim.credential_version,
                PrivateReconciliationSnapshot.observed_at >= now - timedelta(seconds=60),
            )
            .order_by(desc(PrivateReconciliationSnapshot.observed_at))
            .limit(1)
        )
        if snapshot is None:
            return "reconciliation_stale"
        streams = list(
            await session.scalars(
                select(PrivateStreamEvidence).where(
                    PrivateStreamEvidence.account_id == claim.account_id,
                    PrivateStreamEvidence.worker_owner_id == process.owner_id,
                    PrivateStreamEvidence.worker_generation == claim.generation,
                    PrivateStreamEvidence.credential_version == claim.credential_version,
                    PrivateStreamEvidence.observed_at >= now - timedelta(seconds=45),
                    PrivateStreamEvidence.state.in_(("snapshot", "live")),
                )
            )
        )
        if {row.channel for row in streams} != {
            "orders",
            "positions",
            "margins",
            "v2/user_trades",
        }:
            return "private_stream_stale"
        risk = await session.scalar(
            select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == claim.account_id)
        )
        if risk is None:
            return "risk_policy_missing"
        if not risk.auto_activation_enabled:
            return "automatic_activation_disabled"
        if control.emergency_disabled or global_control.emergency_disabled:
            return "emergency_disabled"
        if risk.product_scope_mode == "ALL_SUPPORTED":
            catalogue = await session.scalar(
                select(CatalogueSnapshot)
                .where(
                    CatalogueSnapshot.region == account.region,
                    CatalogueSnapshot.environment == account.environment,
                    CatalogueSnapshot.fresh_until > now,
                )
                .order_by(desc(CatalogueSnapshot.validated_at))
                .limit(1)
            )
            if catalogue is None:
                return "catalogue_not_ready"
            rows = list(
                await session.scalars(
                    select(CatalogueProduct).where(CatalogueProduct.snapshot_id == catalogue.id)
                )
            )
            product_ids = {
                row.product_id
                for row in rows
                if row.to_product().contract_type.value in ("perpetual_futures", "futures")
                and row.to_product().notional_type.value == "vanilla"
                and not row.to_product().is_quanto
                and row.to_product().quoting_asset.symbol == "USD"
                and row.to_product().contract_unit_currency
                == row.to_product().underlying_asset.symbol
                and row.to_product().state.value == "live"
                and row.to_product().trading_status.value == "operational"
                and not row.to_product().only_reduce_only_orders_allowed
            }
            rendered_scope = json.dumps(sorted(product_ids))
            if risk.allowed_product_ids != rendered_scope:
                risk.allowed_product_ids = rendered_scope
                risk.version += 1
                risk.updated_at = now
        else:
            try:
                product_ids = {int(value) for value in json.loads(risk.allowed_product_ids)}
            except (TypeError, ValueError):
                return "risk_product_scope_invalid"
        if not product_ids:
            return "risk_product_scope_empty"
        quotes = list(
            await session.scalars(
                select(MarketQuote).where(
                    MarketQuote.region == account.region,
                    MarketQuote.environment == account.environment,
                    MarketQuote.product_id.in_(product_ids),
                    MarketQuote.observed_at >= now - timedelta(seconds=30),
                )
            )
        )
        if {row.product_id for row in quotes} != product_ids:
            return "quote_stale"
        uncertain_submission = await session.scalar(
            select(LiveOrderSubmission.id)
            .join(LiveOrderIntent, LiveOrderIntent.id == LiveOrderSubmission.intent_id)
            .where(
                LiveOrderSubmission.account_id == claim.account_id,
                LiveOrderSubmission.status == "UNCERTAIN",
                LiveOrderIntent.hidden_at.is_(None),
            )
            .limit(1)
        )
        uncertain_action = await session.scalar(
            select(LiveOrderAction.id)
            .where(
                LiveOrderAction.account_id == claim.account_id,
                LiveOrderAction.status == "UNCERTAIN",
            )
            .limit(1)
        )
        if uncertain_submission is not None or uncertain_action is not None:
            return "uncertain_operations_unresolved"
        if LIVE_QUALIFICATION_MIN_MINUTES:
            override_details = json.dumps(
                {"qualificationMinMinutes": LIVE_QUALIFICATION_MIN_MINUTES},
                separators=(",", ":"),
                sort_keys=True,
            )
            override_audit_id = await session.scalar(
                select(PlatformAuditEvent.id)
                .where(
                    PlatformAuditEvent.action == "QUALIFICATION_POLICY_OVERRIDE_APPLIED",
                    PlatformAuditEvent.target_type == "account",
                    PlatformAuditEvent.target_id == str(claim.account_id),
                    PlatformAuditEvent.details == override_details,
                )
                .limit(1)
            )
            if override_audit_id is None:
                session.add(
                    PlatformAuditEvent(
                        id=uuid4(),
                        actor_user_id=None,
                        action="QUALIFICATION_POLICY_OVERRIDE_APPLIED",
                        target_type="account",
                        target_id=str(claim.account_id),
                        outcome="SUCCEEDED",
                        request_id=str(uuid4()),
                        details=override_details,
                        occurred_at=now,
                    )
                )
            window_start = now - timedelta(minutes=LIVE_QUALIFICATION_MIN_MINUTES)
            old_snapshot = await session.scalar(
                select(PrivateReconciliationSnapshot.id)
                .where(
                    PrivateReconciliationSnapshot.account_id == claim.account_id,
                    PrivateReconciliationSnapshot.worker_generation == claim.generation,
                    PrivateReconciliationSnapshot.credential_version == claim.credential_version,
                    PrivateReconciliationSnapshot.outcome == "ready",
                    PrivateReconciliationSnapshot.complete.is_(True),
                    PrivateReconciliationSnapshot.observed_at <= window_start,
                )
                .limit(1)
            )
            if old_snapshot is None:
                return "qualification_window_incomplete"
        binding_payload = json.dumps(
            {
                "accountId": str(account.id),
                "credentialVersion": claim.credential_version,
                "environment": account.environment,
                "region": account.region,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(binding_payload.encode("utf-8")).hexdigest()
        evidence = await session.scalar(
            select(LiveQualificationEvidence).where(
                LiveQualificationEvidence.evidence_sha256 == digest,
                LiveQualificationEvidence.status == "qualified",
            )
        )
        if evidence is None:
            session.add(
                LiveQualificationEvidence(
                    id=uuid4(),
                    account_id=claim.account_id,
                    release_commit="0" * 40,
                    evidence_sha256=digest,
                    evidence_payload=binding_payload,
                    status="qualified",
                    window_started_at=now - timedelta(minutes=LIVE_QUALIFICATION_MIN_MINUTES),
                    window_completed_at=now,
                    approved_by_user_id=None,
                    approved_at=now,
                    created_at=now,
                )
            )
        emergency_lock = control.emergency_disabled or global_control.emergency_disabled
        changed = not control.exits_enabled
        control.exits_enabled = True
        if not emergency_lock:
            changed = changed or not control.entries_enabled or not global_control.entries_enabled
            control.entries_enabled = True
            global_control.entries_enabled = True
            global_control.reason = "automatic healthy activation"
            global_control.updated_at = now
        control.updated_at = now
        if changed or evidence is None:
            session.add(
                PlatformAuditEvent(
                    id=uuid4(),
                    actor_user_id=None,
                    action="AUTOMATIC_LIVE_ACTIVATION",
                    target_type="account",
                    target_id=str(claim.account_id),
                    outcome="SUCCEEDED",
                    request_id=str(uuid4()),
                    details=json.dumps(
                        {
                            "emergencyEntryLockPreserved": emergency_lock,
                            "qualificationMinMinutes": LIVE_QUALIFICATION_MIN_MINUTES,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    occurred_at=now,
                )
            )
    if not changed and evidence is not None:
        return "already_active"
    return "exits_opened" if emergency_lock else "gates_opened"


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
            .options(
                load_only(
                    PrivateReconciliationSnapshot.id,
                    PrivateReconciliationSnapshot.observed_at,
                    PrivateReconciliationSnapshot.credential_version,
                    PrivateReconciliationSnapshot.outcome,
                    PrivateReconciliationSnapshot.content_sha256,
                    PrivateReconciliationSnapshot.worker_owner_id,
                    PrivateReconciliationSnapshot.worker_generation,
                )
            )
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
            or latest.observed_at
            <= now - timedelta(seconds=LIVE_RECONCILIATION_EVIDENCE_INTERVAL_SECONDS)
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
    account_id = normalize_worker_uuid(
        claim.account_id, message="invalid Live submission worker scope"
    )
    owner_id = normalize_worker_uuid(
        process.owner_id, message="invalid Live submission worker scope"
    )
    entries_allowed = await _dispatch_qualification_is_current(database, claim, execution)
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
            account_id=account_id,
            owner_id=owner_id,
            external_user_id=claim.external_user_id,
            adapter=adapter,
            entries_allowed=entries_allowed,
        )
    finally:
        await adapter.aclose()


async def _dispatch_qualification_is_current(
    database: Database,
    claim: _Claim,
    execution: LiveExecutionSettings,
) -> bool:
    now = datetime.now(UTC)
    async with database.transaction() as session:
        account = await session.get(Account, claim.account_id)
        if account is None:
            return False
        binding = json.dumps(
            {
                "accountId": str(account.id),
                "credentialVersion": claim.credential_version,
                "environment": account.environment,
                "region": account.region,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        expected_digest = hashlib.sha256(binding.encode("utf-8")).hexdigest()
        evidence = await session.scalar(
            select(LiveQualificationEvidence).where(
                LiveQualificationEvidence.account_id == claim.account_id,
                LiveQualificationEvidence.evidence_sha256 == expected_digest,
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
    if evidence is None or evidence.window_completed_at is None:
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
    account_id = normalize_worker_uuid(claim.account_id, message="invalid Live action worker scope")
    owner_id = normalize_worker_uuid(process.owner_id, message="invalid Live action worker scope")
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
            account_id=account_id,
            owner_id=owner_id,
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
    account_id = normalize_worker_uuid(
        claim.account_id, message="invalid Live recovery worker scope"
    )
    owner_id = normalize_worker_uuid(process.owner_id, message="invalid Live recovery worker scope")
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
            account_id=account_id,
            owner_id=owner_id,
            external_user_id=claim.external_user_id,
            adapter=adapter,
        )
    finally:
        await adapter.aclose()


async def _publish_private_stream_update(account_id: UUID, update: dict[str, object]) -> None:
    """Wake recovery and push a compact venue-confirmed account delta."""
    payload = {
        "accountId": str(account_id),
        "reason": "private_stream_update",
        "observedAt": datetime.now(UTC).isoformat(),
        **update,
    }
    await publish_json(ACCOUNT_CHANNEL, payload)
    if update.get("kind") in {"order", "fill"}:
        await publish_json(ORDER_CHANNEL, {"accountId": str(account_id)})


async def _run_immediate_execution_loop(
    database: Database,
    process: PrivateWorkerSettings,
    execution: LiveExecutionSettings,
    secrets: AwsDeltaSecretStore,
    logger: Any,
    *,
    stop: asyncio.Event,
) -> None:
    """Dispatch durable order commands independently of reconciliation latency."""
    while not stop.is_set():
        try:
            async with database.transaction() as session:
                pending_accounts = (
                    select(LiveOrderSubmission.account_id)
                    .where(
                        LiveOrderSubmission.status == "PENDING",
                        LiveOrderSubmission.intent_id.in_(
                            select(LiveOrderIntent.id).where(LiveOrderIntent.hidden_at.is_(None))
                        ),
                    )
                    .union(
                        select(LiveOrderSubmission.account_id)
                        .join(LiveOrderIntent, LiveOrderIntent.id == LiveOrderSubmission.intent_id)
                        .where(
                            LiveOrderSubmission.status == "UNCERTAIN",
                            LiveOrderIntent.hidden_at.is_(None),
                            LiveOrderSubmission.updated_at
                            >= datetime.now(UTC) - timedelta(seconds=4),
                        ),
                        select(LiveOrderAction.account_id).where(
                            LiveOrderAction.status == "PENDING"
                        ),
                        select(TradingViewSignal.account_id).where(
                            TradingViewSignal.status == "received"
                        ),
                    )
                )
                candidate_accounts = set(await session.scalars(pending_accounts))
            for account_id in candidate_accounts:
                claim = await _claim(database, process, account_id)
                if claim is None:
                    continue
                tradingview = await admit_one_tradingview_signal(
                    database,
                    account_id=claim.account_id,
                    owner_id=process.owner_id,
                )
                if tradingview.kind not in {"IDLE", "DEFERRED"}:
                    logger.info(
                        "immediate_tradingview_signal",
                        account_id=str(account_id),
                        outcome=tradingview.kind,
                        signal_id=(
                            None if tradingview.signal_id is None else str(tradingview.signal_id)
                        ),
                    )
                api_key, api_secret = await secrets.read(claim.secret_arn)
                try:
                    action = await _execute_qualified_live_action(
                        database,
                        process,
                        claim,
                        execution,
                        api_key=api_key,
                        api_secret=api_secret,
                    )
                    if action.kind not in {"IDLE", "DISABLED"}:
                        logger.info(
                            "immediate_live_order_action",
                            account_id=str(account_id),
                            outcome=action.kind,
                        )
                    result = await _execute_qualified_live_submission(
                        database,
                        process,
                        claim,
                        execution,
                        api_key=api_key,
                        api_secret=api_secret,
                    )
                    if result.kind not in {"IDLE", "DISABLED"}:
                        logger.info(
                            "immediate_live_submission",
                            account_id=str(account_id),
                            outcome=result.kind,
                        )
                    recovery = LiveRecoveryResult("IDLE", None)
                    if result.kind in {"UNCERTAIN", "IDLE", "DISABLED"}:
                        recovery = await _recover_live_submission(
                            database,
                            process,
                            claim,
                            api_key=api_key,
                            api_secret=api_secret,
                        )
                        if recovery.kind != "IDLE":
                            logger.info(
                                "immediate_live_recovery",
                                account_id=str(account_id),
                                outcome=recovery.kind,
                                submission_id=(
                                    None
                                    if recovery.submission_id is None
                                    else str(recovery.submission_id)
                                ),
                            )
                    if (
                        action.kind not in {"IDLE", "DISABLED"}
                        or result.kind not in {"IDLE", "DISABLED"}
                        or recovery.kind != "IDLE"
                        or tradingview.kind not in {"IDLE", "DEFERRED"}
                    ):
                        await publish_json(
                            ACCOUNT_CHANNEL,
                            {"accountId": str(account_id), "reason": "order_state_changed"},
                        )
                finally:
                    api_key = ""
                    api_secret = ""
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("immediate_execution_cycle_failed", error_type=type(error).__name__)
        try:
            await wait_for_message(ORDER_CHANNEL, 1.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(1.0)


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
    execution_task: asyncio.Task[None] | None = None
    next_retention_at = 0.0
    logger.info("private_worker_started", owner_id=str(process.owner_id))
    from hmt_crypto.risk_operations import log_risk_configuration

    log_risk_configuration("worker", logger)
    try:
        if not process.read_only and execution.enabled:
            execution_task = asyncio.create_task(
                _run_immediate_execution_loop(
                    database, process, execution, secrets, logger, stop=stop
                )
            )
        while not stop.is_set():
            if time.monotonic() >= next_retention_at:
                try:
                    retention = await prune_operational_history(
                        database, retention_days=process.operational_retention_days
                    )
                    logger.info(
                        "operational_retention_completed",
                        lock_acquired=retention.lock_acquired,
                        stream_evidence=retention.stream_evidence,
                        reconciliation_snapshots=retention.reconciliation_snapshots,
                        market_candles=retention.market_candles,
                    )
                except Exception as error:
                    logger.error("operational_retention_failed", error_type=type(error).__name__)
                next_retention_at = time.monotonic() + 86_400
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
                    stage = "secret_read"
                    try:
                        api_key, api_secret = await secrets.read(claim.secret_arn)
                        try:
                            stage = "private_reconciliation"
                            evidence = await read_private_reconciliation(
                                api_key=api_key,
                                api_secret=api_secret,
                                expected_user_id=claim.external_user_id,
                                allowed_product_ids=claim.product_ids,
                            )
                            if await _publish(database, process, claim, evidence):
                                try:
                                    await publish_json(
                                        ACCOUNT_CHANNEL,
                                        {
                                            "accountId": str(claim.account_id),
                                            "observedAt": datetime.now(UTC).isoformat(),
                                        },
                                    )
                                except Exception:
                                    logger.warning(
                                        "account_update_publish_failed",
                                        account_id=str(account_id),
                                    )
                                stage = "private_stream"
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
                                            publish_update=partial(
                                                _publish_private_stream_update, claim.account_id
                                            ),
                                        )
                                    )
                                    stream_tasks[account_id] = (*identity, task)
                                stage = "protection_sync"
                                try:
                                    protection_created = await sync_one_protection_group(
                                        database,
                                        account_id=claim.account_id,
                                        owner_id=process.owner_id,
                                    )
                                except asyncio.CancelledError:
                                    raise
                                except Exception as error:
                                    protection_created = False
                                    logger.error(
                                        "protection_sync_failed",
                                        account_id=str(account_id),
                                        error_type=type(error).__name__,
                                    )
                                if protection_created:
                                    logger.info(
                                        "protection_group_armed",
                                        account_id=str(account_id),
                                    )
                                stage = "reconciliation_published"
                                logger.info(
                                    "private_reconciliation_published",
                                    account_id=str(account_id),
                                    generation=claim.generation,
                                )
                                activation = await _auto_activate_healthy_account(
                                    database,
                                    process,
                                    claim,
                                    execution,
                                )
                                if activation in {"gates_opened", "exits_opened"}:
                                    logger.info(
                                        "automatic_live_activation",
                                        account_id=str(account_id),
                                        outcome=activation,
                                    )
                                if process.read_only:
                                    continue
                                stage = "gtt"
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
                                stage = "tradingview"
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
                                stage = "live_order_action"
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
                                stage = "live_order_recovery"
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
                                stage = "live_submission"
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
                    except Exception as error:
                        await _degrade(database, process, claim)
                        logger.error(
                            "private_account_cycle_failed",
                            account_id=str(account_id),
                            stage=stage,
                            error_type=type(error).__name__,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("private_supervision_cycle_failed")
            try:
                wake = asyncio.create_task(
                    wait_for_message(ORDER_CHANNEL, float(process.interval_seconds))
                )
                stopped = asyncio.create_task(stop.wait())
                done, pending = await asyncio.wait(
                    {wake, stopped}, return_when=asyncio.FIRST_COMPLETED
                )
                for pending_task in pending:
                    pending_task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for completed_task in done:
                    await completed_task
            except asyncio.CancelledError:
                raise
            except Exception:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=process.interval_seconds)
                except TimeoutError:
                    pass
    finally:
        if execution_task is not None:
            execution_task.cancel()
            await asyncio.gather(execution_task, return_exceptions=True)
        for _, _, task in stream_tasks.values():
            task.cancel()
        await asyncio.gather(
            *(task for _, _, task in stream_tasks.values()),
            return_exceptions=True,
        )
        await database.aclose()
        logger.info("private_worker_stopped")


__all__ = ["PrivateWorkerSettings", "run_private_worker"]
