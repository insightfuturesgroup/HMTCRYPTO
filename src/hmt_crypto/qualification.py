"""Operator-controlled, evidence-derived Live qualification approval."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import desc, select

from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.live_qualification_policy import (
    LIVE_QUALIFICATION_WINDOW,
    LIVE_RECONCILIATION_MAX_GAP_SECONDS,
    canonical_risk_policy_scope,
    risk_scope_is_approved,
)
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    Account,
    AccountLiveControl,
    AccountRiskPolicy,
    CatalogueProduct,
    CatalogueSnapshot,
    LiveQualificationEvidence,
    PlatformUser,
    PrivateReconciliationSnapshot,
    PrivateStreamEvidence,
    PrivateWorkerLease,
)
from hmt_crypto.persistence.redaction import JsonValue

_CHANNELS = {"orders", "positions", "margins", "v2/user_trades"}


def _continuous(times: list[datetime], *, start: datetime, end: datetime, gap: int) -> bool:
    ordered = sorted(times)
    return bool(
        ordered
        and ordered[0] <= start + timedelta(seconds=gap)
        and ordered[-1] >= end - timedelta(seconds=gap)
        and all(
            right - left <= timedelta(seconds=gap)
            for left, right in zip(ordered, ordered[1:], strict=False)
        )
    )


async def qualify_live_account(
    *,
    profile_name: str,
    release_commit: str,
    approver_email: str,
    failure_drills_sha256: str,
) -> dict[str, JsonValue]:
    """Approve only a complete immutable one-hour evidence window."""
    if not re.fullmatch(r"[0-9a-f]{40}", release_commit):
        raise ValueError("release commit must be a full lowercase SHA")
    if not re.fullmatch(r"[0-9a-f]{64}", failure_drills_sha256):
        raise ValueError("failure-drill evidence must be a SHA-256")
    now = datetime.now(UTC)
    qualification_start = now - LIVE_QUALIFICATION_WINDOW
    # A zero-minute owner-approved policy still requires current, healthy
    # evidence; use the normal freshness windows to collect it.
    start = qualification_start if LIVE_QUALIFICATION_WINDOW else now - timedelta(seconds=60)
    database = Database(ControlRuntimeSettings().database_runtime_config())
    try:
        async with database.transaction() as session:
            account = await session.scalar(
                select(Account).where(
                    Account.profile_name == profile_name,
                    Account.region == "india",
                    Account.environment == "prod",
                    Account.mode == "LIVE",
                    Account.archived_at.is_(None),
                )
            )
            approver = await session.scalar(
                select(PlatformUser).where(
                    PlatformUser.email == approver_email.casefold(),
                    PlatformUser.role == "admin",
                    PlatformUser.status == "active",
                )
            )
            if account is None or approver is None:
                raise ValueError("approved account and active admin are required")
            control = await session.scalar(
                select(AccountLiveControl).where(AccountLiveControl.account_id == account.id)
            )
            lease = await session.scalar(
                select(PrivateWorkerLease).where(
                    PrivateWorkerLease.account_id == account.id,
                    PrivateWorkerLease.lease_until > now,
                )
            )
            risk = await session.scalar(
                select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == account.id)
            )
            catalogue = await session.scalar(
                select(CatalogueSnapshot)
                .where(
                    CatalogueSnapshot.region == "india",
                    CatalogueSnapshot.environment == "prod",
                )
                .order_by(desc(CatalogueSnapshot.revision))
                .limit(1)
            )
            streams = list(
                await session.scalars(
                    select(PrivateStreamEvidence).where(
                        PrivateStreamEvidence.account_id == account.id,
                        PrivateStreamEvidence.observed_at >= start,
                    )
                )
            )
            reconciliations = list(
                await session.scalars(
                    select(PrivateReconciliationSnapshot).where(
                        PrivateReconciliationSnapshot.account_id == account.id,
                        PrivateReconciliationSnapshot.observed_at >= start,
                    )
                )
            )
            if control is None or lease is None or risk is None or catalogue is None:
                raise ValueError("current Live readiness evidence is incomplete")
            if control.credential_state != "verified" or control.reconciliation_state != "ready":
                raise ValueError("account is not currently reconciled and verified")
            risk_scope = canonical_risk_policy_scope(
                # Owner override changes enforcement, not the approved baseline
                # values captured by qualification evidence.
                enabled=True,
                limit_currency=risk.limit_currency,
                usd_inr_rate=risk.usd_inr_rate,
                max_order_notional=risk.max_order_notional,
                max_position_notional=risk.max_position_notional,
                max_daily_loss=risk.max_daily_loss,
                max_open_orders=risk.max_open_orders,
                max_inflight_signals=risk.max_inflight_signals,
            )
            if not risk_scope_is_approved(risk_scope):
                raise ValueError("approved INR conversion policy is missing")
            allowed_ids = set(json.loads(risk.allowed_product_ids))
            allowed_products = list(
                await session.scalars(
                    select(CatalogueProduct).where(
                        CatalogueProduct.snapshot_id == catalogue.id,
                        CatalogueProduct.product_id.in_(allowed_ids),
                    )
                )
            )
            if (
                not allowed_ids
                or {row.product_id for row in allowed_products} != allowed_ids
                or any(
                    product.contract_type.value not in ("perpetual_futures", "futures")
                    or product.notional_type.value != "vanilla"
                    or product.is_quanto
                    or product.quoting_asset.symbol != "USD"
                    or product.contract_unit_currency != product.underlying_asset.symbol
                    or product.state.value != "live"
                    or product.trading_status.value != "operational"
                    or product.only_reduce_only_orders_allowed
                    for product in (row.to_product() for row in allowed_products)
                )
            ):
                raise ValueError("approved product scope is missing")
            by_channel = {
                channel: [
                    row.observed_at
                    for row in streams
                    if row.channel == channel
                    and row.state in {"snapshot", "live"}
                    and row.credential_version == lease.credential_version
                    and row.worker_generation == lease.generation
                ]
                for channel in _CHANNELS
            }
            if any(
                row.state in {"gap", "disconnected"}
                or row.credential_version != lease.credential_version
                or row.worker_generation != lease.generation
                for row in streams
            ) or not all(
                _continuous(values, start=start, end=now, gap=45) for values in by_channel.values()
            ):
                raise ValueError("private-stream qualification window is not continuous")
            ready_reconciliations = [
                row.observed_at
                for row in reconciliations
                if row.outcome == "ready"
                and row.complete
                and row.credential_version == lease.credential_version
                and row.worker_generation == lease.generation
            ]
            if len(ready_reconciliations) != len(reconciliations) or not _continuous(
                ready_reconciliations,
                start=start,
                end=now,
                gap=LIVE_RECONCILIATION_MAX_GAP_SECONDS,
            ):
                raise ValueError("reconciliation qualification window is not continuous")
            # Qualification is deliberately bound only to the durable exchange
            # identity. Release/risk/catalogue data remains audited metadata and
            # cannot invalidate a healthy account after deployment.
            binding: dict[str, object] = {
                "accountId": str(account.id),
                "environment": account.environment,
                "region": account.region,
                "credentialVersion": lease.credential_version,
            }
            encoded_binding = json.dumps(
                binding, ensure_ascii=True, separators=(",", ":"), sort_keys=True
            )
            digest = hashlib.sha256(encoded_binding.encode("utf-8")).hexdigest()
            payload: dict[str, object] = {
                "binding": binding,
                "failureDrillsSha256": failure_drills_sha256,
                "qualificationMinMinutes": int(LIVE_QUALIFICATION_WINDOW.total_seconds() // 60),
                "reconciliationCount": len(reconciliations),
                "releaseCommit": release_commit,
                "streamCounts": {key: len(value) for key, value in sorted(by_channel.items())},
                "windowCompletedAt": now.isoformat(),
                "windowStartedAt": qualification_start.isoformat(),
            }
            encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
            existing = await session.scalar(
                select(LiveQualificationEvidence).where(
                    LiveQualificationEvidence.account_id == account.id,
                    LiveQualificationEvidence.evidence_sha256 == digest,
                    LiveQualificationEvidence.status == "qualified",
                )
            )
            if existing is not None:
                return {
                    "accountId": str(account.id),
                    "evidenceSha256": digest,
                    "releaseCommit": release_commit,
                    "status": "qualified",
                    "windowCompletedAt": existing.window_completed_at.isoformat()
                    if existing.window_completed_at
                    else now.isoformat(),
                    "windowStartedAt": existing.window_started_at.isoformat(),
                }
            session.add(
                LiveQualificationEvidence(
                    id=uuid4(),
                    account_id=account.id,
                    release_commit=release_commit,
                    evidence_sha256=digest,
                    evidence_payload=encoded,
                    status="qualified",
                    window_started_at=qualification_start,
                    window_completed_at=now,
                    approved_by_user_id=approver.id,
                    approved_at=now,
                    created_at=now,
                )
            )
            return {
                "accountId": str(account.id),
                "evidenceSha256": digest,
                "releaseCommit": release_commit,
                "status": "qualified",
                "windowCompletedAt": now.isoformat(),
                "windowStartedAt": qualification_start.isoformat(),
            }
    finally:
        await database.aclose()


__all__ = ["qualify_live_account"]
