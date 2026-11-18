"""Bounded retention for high-volume, reconstructable operational evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, select, text

from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    LiveOrderPreview,
    MarketCandle,
    PrivateReconciliationSnapshot,
    PrivateStreamEvidence,
    ProtectionGroup,
)

_RETENTION_LOCK_ID = 4_846_687_403_772_601


@dataclass(frozen=True, slots=True)
class RetentionResult:
    stream_evidence: int = 0
    reconciliation_snapshots: int = 0
    market_candles: int = 0
    lock_acquired: bool = True


async def prune_operational_history(
    database: Database,
    *,
    retention_days: int = 7,
    batch_size: int = 10_000,
    now: datetime | None = None,
) -> RetentionResult:
    """Delete old reconstructable rows; trading and audit records remain immutable."""
    if not 1 <= retention_days <= 90 or not 1 <= batch_size <= 10_000:
        raise ValueError("invalid operational retention policy")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    async with database.transaction() as session:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            acquired = bool(
                await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:lock_id)").bindparams(
                        lock_id=_RETENTION_LOCK_ID
                    )
                )
            )
            if not acquired:
                return RetentionResult(lock_acquired=False)

        stream_ids = list(
            await session.scalars(
                select(PrivateStreamEvidence.id)
                .where(PrivateStreamEvidence.observed_at < cutoff)
                .order_by(PrivateStreamEvidence.observed_at)
                .limit(batch_size)
            )
        )
        candle_ids = list(
            await session.scalars(
                select(MarketCandle.id)
                .where(MarketCandle.timestamp < cutoff)
                .order_by(MarketCandle.timestamp)
                .limit(batch_size)
            )
        )
        snapshot_ids = list(
            await session.scalars(
                select(PrivateReconciliationSnapshot.id)
                .where(
                    PrivateReconciliationSnapshot.observed_at < cutoff,
                    ~exists().where(
                        LiveOrderPreview.reconciliation_snapshot_id
                        == PrivateReconciliationSnapshot.id
                    ),
                    ~exists().where(
                        ProtectionGroup.reconciliation_snapshot_id
                        == PrivateReconciliationSnapshot.id
                    ),
                )
                .order_by(PrivateReconciliationSnapshot.observed_at)
                .limit(batch_size)
            )
        )
        if stream_ids:
            await session.execute(
                delete(PrivateStreamEvidence).where(PrivateStreamEvidence.id.in_(stream_ids))
            )
        if candle_ids:
            await session.execute(delete(MarketCandle).where(MarketCandle.id.in_(candle_ids)))
        if snapshot_ids:
            await session.execute(
                delete(PrivateReconciliationSnapshot).where(
                    PrivateReconciliationSnapshot.id.in_(snapshot_ids)
                )
            )
        return RetentionResult(
            stream_evidence=len(stream_ids),
            reconciliation_snapshots=len(snapshot_ids),
            market_candles=len(candle_ids),
        )
