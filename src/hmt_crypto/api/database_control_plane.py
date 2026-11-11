"""Durable operator control plane backed by the normalized persistence model."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from hmt_crypto.api.control_plane import (
    ComponentHealth,
    ControlPlaneUnavailable,
    DashboardSnapshot,
    HealthSnapshot,
    KillCommand,
    KillImpact,
    OrderView,
    PositionView,
    ProtectionState,
    QueuedCommand,
)
from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.config import Settings
from hmt_crypto.persistence.command_repository import CommandRepository
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.errors import PersistenceError
from hmt_crypto.persistence.models import (
    Account,
    AccountLiveControl,
    BalanceSnapshot,
    CatalogueProduct,
    CatalogueSnapshot,
    Order,
    PnlSnapshot,
    PositionProtection,
    PositionSnapshot,
    PrivateStreamEvidence,
    ReconciliationResource,
    RiskHaltEvent,
    RiskState,
)
from hmt_crypto.persistence.repository_contracts import (
    AccountScope,
    CommandCorrelation,
    CommandReceipt,
)

_OPEN_ORDER_STATES = (
    "UNKNOWN",
    "PREPARED",
    "SUBMITTING",
    "OPEN",
    "PARTIALLY_FILLED",
)
_DASHBOARD_ROW_LIMIT = 10_000

Region = Literal["india", "global"]
Environment = Literal["testnet", "prod"]
Mode = Literal["BACKTEST", "PAPER", "LIVE"]
ReconciliationState = Literal["UNKNOWN", "BLOCKED", "READY_CLAIM"]


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _command(receipt: CommandReceipt) -> QueuedCommand:
    state = receipt.status
    if state == "CANCELLED":
        state = "FAILED"
    return QueuedCommand(
        command_id=receipt.command_id,
        state=state,
        accepted_at=receipt.created_at,
    )


def _literal_scope(account: Account) -> AccountScope:
    if account.region not in {"india", "global"}:
        raise ControlPlaneUnavailable() from None
    if account.environment not in {"testnet", "prod"}:
        raise ControlPlaneUnavailable() from None
    if account.mode not in {"BACKTEST", "PAPER", "LIVE"}:
        raise ControlPlaneUnavailable() from None
    try:
        # asyncpg exposes UUID values through its own UUID subtype. Normalize
        # that trusted database value before entering the exact-type domain
        # model; all other scope fields remain fail-closed literals.
        account_id = UUID(str(account.id))
    except (TypeError, ValueError, AttributeError):
        raise ControlPlaneUnavailable() from None
    return AccountScope(
        account_id=account_id,
        region=cast(Region, account.region),
        environment=cast(Environment, account.environment),
        mode=cast(Mode, account.mode),
    )


def _position_side(contracts: int) -> Literal["long", "short", "flat"]:
    if contracts > 0:
        return "long"
    if contracts < 0:
        return "short"
    return "flat"


def _order_view(row: Order, symbol: str) -> OrderView:
    if (
        row.client_order_id is None
        or row.requested_side not in {"buy", "sell"}
        or row.requested_order_type not in {"limit", "market"}
        or type(row.requested_contracts) is not int
        or row.requested_contracts < 1
        or type(row.requested_reduce_only) is not bool
    ):
        raise ControlPlaneUnavailable() from None
    return OrderView(
        local_order_id=row.id,
        exchange_order_id=row.exchange_order_id,
        client_order_id=row.client_order_id,
        symbol=symbol,
        side=cast(Literal["buy", "sell"], row.requested_side),
        order_type=row.requested_order_type,
        contracts=str(row.requested_contracts),
        limit_price=_decimal(row.requested_limit_price),
        status=row.state,
        reduce_only=row.requested_reduce_only,
        updated_at=row.updated_at,
    )


def _protection_evidence(
    row: PositionProtection | None,
    risk: RiskState | None,
) -> tuple[ProtectionState, datetime | None, int | None]:
    """Translate a projection without upgrading stale/synthetic evidence to verified."""
    if row is None:
        return "UNKNOWN", None, None
    if row.state == "UNPROTECTED":
        return "UNPROTECTED", row.updated_at, row.reconciliation_generation
    if row.state == "VERIFYING":
        return "VERIFYING", row.updated_at, row.reconciliation_generation
    current = (
        risk is not None
        and risk.reconciliation_state == "READY_CLAIM"
        and risk.reconciliation_run_id is not None
        and row.reconciliation_ref == risk.reconciliation_run_id
        and row.reconciliation_generation is not None
    )
    if row.state == "PROTECTED":
        complete = (
            current
            and row.mechanism == "NATIVE_REDUCE_ONLY"
            and row.reason == "VERIFIED"
            and row.verified_at is not None
            and row.required_contracts is not None
            and row.verified_contracts is not None
            and row.verified_contracts >= row.required_contracts > 0
        )
        if complete and row.authorization_decision == "PAPER_AUTHORIZED":
            return "PAPER_SIMULATED", row.verified_at, row.reconciliation_generation
        if complete and row.authorization_decision == "TESTNET_NATIVE_VERIFIED":
            return "NATIVE_VERIFIED", row.verified_at, row.reconciliation_generation
        return "STALE", row.updated_at, row.reconciliation_generation
    if row.state == "SYNTHETIC_MONITORING":
        if (
            current
            and row.mechanism == "SYNTHETIC_MONITOR"
            and row.authorization_decision == "SYNTHETIC_AUTHORIZED"
        ):
            return "SYNTHETIC_MONITORING", row.updated_at, row.reconciliation_generation
        return "STALE", row.updated_at, row.reconciliation_generation
    if row.state in {"UNKNOWN", "NO_REQUIRED_COVERAGE"}:
        return "UNKNOWN", row.updated_at, row.reconciliation_generation
    raise ControlPlaneUnavailable() from None


class DatabaseControlPlane:
    """Expose durable commands and conservative database projections only."""

    def __init__(
        self,
        database: Database,
        settings: Settings | ControlRuntimeSettings,
        account_id: UUID,
    ) -> None:
        if (
            type(database) is not Database
            or type(settings) not in {Settings, ControlRuntimeSettings}
            or type(account_id) is not UUID
            or account_id.int == 0
        ):
            raise TypeError("invalid database control-plane configuration")
        self._database = database
        self._region: Region = settings.delta_region.value
        self._environment: Environment = settings.delta_env.value
        self._mode: Mode = settings.execution_mode.value
        self._account_id = UUID(int=account_id.int)
        self._commands = CommandRepository()

    async def _scope(self, session: AsyncSession) -> tuple[Account, AccountScope]:
        account = (
            await session.execute(
                select(Account).where(
                    Account.id == self._account_id,
                    Account.region == self._region,
                    Account.environment == self._environment,
                    Account.mode == self._mode,
                )
            )
        ).scalar_one_or_none()
        if account is None:
            raise ControlPlaneUnavailable() from None
        return account, _literal_scope(account)

    @staticmethod
    def _scope_predicates(model: Any, scope: AccountScope) -> tuple[ColumnElement[bool], ...]:
        return (
            model.account_id == scope.account_id,
            model.region == scope.region,
            model.environment == scope.environment,
            model.mode == scope.mode,
        )

    async def _risk_state(self, session: AsyncSession, scope: AccountScope) -> RiskState | None:
        return (
            await session.execute(
                select(RiskState).where(*self._scope_predicates(RiskState, scope))
            )
        ).scalar_one_or_none()

    async def _has_active_halt(self, session: AsyncSession, scope: AccountScope) -> bool:
        latest = (
            select(
                RiskHaltEvent.halt_id.label("halt_id"),
                func.max(RiskHaltEvent.sequence).label("sequence"),
            )
            .where(*self._scope_predicates(RiskHaltEvent, scope))
            .group_by(RiskHaltEvent.halt_id)
            .subquery()
        )
        active = (
            await session.execute(
                select(RiskHaltEvent.id)
                .join(
                    latest,
                    and_(
                        RiskHaltEvent.halt_id == latest.c.halt_id,
                        RiskHaltEvent.sequence == latest.c.sequence,
                    ),
                )
                .where(
                    *self._scope_predicates(RiskHaltEvent, scope),
                    RiskHaltEvent.to_state == "ACTIVE",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        return active is not None

    async def health(self) -> HealthSnapshot:
        """Report database/account/reconciliation readiness without guessing stream state."""
        now = datetime.now(UTC)
        try:
            async with self._database.transaction() as session:
                _, scope = await self._scope(session)
                risk = await self._risk_state(session, scope)
                live_control = await session.scalar(
                    select(AccountLiveControl).where(
                        AccountLiveControl.account_id == scope.account_id
                    )
                )
                stream_rows = list(
                    await session.scalars(
                        select(PrivateStreamEvidence).where(
                            PrivateStreamEvidence.account_id == scope.account_id,
                            PrivateStreamEvidence.observed_at >= now - timedelta(seconds=45),
                            PrivateStreamEvidence.state.in_(("snapshot", "live")),
                        )
                    )
                )
            reconciliation_ready = bool(
                (risk is not None and risk.reconciliation_state == "READY_CLAIM")
                or (live_control is not None and live_control.reconciliation_state == "ready")
            )
            private_stream_ready = {row.channel for row in stream_rows} == {
                "orders",
                "positions",
                "margins",
                "v2/user_trades",
            }
            components = (
                ComponentHealth(
                    component="database", state="ready", reason="reachable", observed_at=now
                ),
                ComponentHealth(
                    component="reconciliation",
                    state="ready" if reconciliation_ready else "degraded",
                    reason="ready_claim" if reconciliation_ready else "not_ready",
                    observed_at=now,
                ),
                ComponentHealth(
                    component="private_stream",
                    state="ready" if private_stream_ready else "degraded",
                    reason="fresh_channel_evidence"
                    if private_stream_ready
                    else "stream_evidence_stale",
                    observed_at=now,
                ),
            )
            return HealthSnapshot(
                live=True,
                ready=reconciliation_ready and private_stream_ready,
                sequence=0 if risk is None else risk.version,
                observed_at=now,
                components=components,
            )
        except (PersistenceError, ControlPlaneUnavailable, ValueError, TypeError):
            return HealthSnapshot(
                live=True,
                ready=False,
                sequence=0,
                observed_at=now,
                components=(
                    ComponentHealth(
                        component="database",
                        state="failed",
                        reason="unavailable",
                        observed_at=now,
                    ),
                ),
            )

    async def _symbols(
        self, session: AsyncSession, product_ids: set[int]
    ) -> tuple[dict[int, str], bool]:
        now = datetime.now(UTC)
        snapshot = (
            await session.execute(
                select(CatalogueSnapshot)
                .where(
                    CatalogueSnapshot.region == self._region,
                    CatalogueSnapshot.environment == self._environment,
                )
                .order_by(CatalogueSnapshot.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if snapshot is None:
            return {}, False
        if not product_ids:
            return {}, snapshot.fresh_until > now
        rows = (
            (
                await session.execute(
                    select(CatalogueProduct).where(
                        CatalogueProduct.snapshot_id == snapshot.id,
                        CatalogueProduct.product_id.in_(product_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        symbols = {row.product_id: row.symbol for row in rows}
        return symbols, snapshot.fresh_until > now and set(symbols) == product_ids

    async def dashboard(self) -> DashboardSnapshot:
        """Build one conservative snapshot from a single database transaction."""
        try:
            async with self._database.transaction() as session:
                account, scope = await self._scope(session)
                risk = await self._risk_state(session, scope)
                active_halt = await self._has_active_halt(session, scope)
                positions: list[PositionSnapshot] = []
                balances: list[BalanceSnapshot] = []
                pnl: PnlSnapshot | None = None
                if risk is not None and risk.reconciliation_run_id is not None:
                    positions = list(
                        (
                            await session.execute(
                                select(PositionSnapshot)
                                .where(
                                    *self._scope_predicates(PositionSnapshot, scope),
                                    PositionSnapshot.run_id == risk.reconciliation_run_id,
                                    PositionSnapshot.resource_kind == "MARGINED_POSITIONS",
                                )
                                .order_by(PositionSnapshot.product_id)
                                .limit(_DASHBOARD_ROW_LIMIT + 1)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    balances = list(
                        (
                            await session.execute(
                                select(BalanceSnapshot)
                                .where(
                                    *self._scope_predicates(BalanceSnapshot, scope),
                                    BalanceSnapshot.run_id == risk.reconciliation_run_id,
                                )
                                .order_by(BalanceSnapshot.asset_id)
                                .limit(_DASHBOARD_ROW_LIMIT + 1)
                            )
                        )
                        .scalars()
                        .all()
                    )
                if risk is not None and risk.pnl_snapshot_id is not None:
                    pnl = await session.get(PnlSnapshot, risk.pnl_snapshot_id)
                orders = list(
                    (
                        await session.execute(
                            select(Order)
                            .where(
                                *self._scope_predicates(Order, scope),
                                Order.state.in_(_OPEN_ORDER_STATES),
                            )
                            .order_by(Order.updated_at, Order.id)
                            .limit(_DASHBOARD_ROW_LIMIT + 1)
                        )
                    )
                    .scalars()
                    .all()
                )
                protections = list(
                    (
                        await session.execute(
                            select(PositionProtection)
                            .where(*self._scope_predicates(PositionProtection, scope))
                            .limit(_DASHBOARD_ROW_LIMIT + 1)
                        )
                    )
                    .scalars()
                    .all()
                )
                if any(
                    len(rows) > _DASHBOARD_ROW_LIMIT
                    for rows in (positions, balances, orders, protections)
                ):
                    raise ControlPlaneUnavailable() from None
                product_ids = {row.product_id for row in positions} | {
                    row.product_id for row in orders
                }
                symbols, catalogue_fresh = await self._symbols(session, product_ids)

            protection_by_product = {row.product_id: row for row in protections}
            position_views = tuple(
                PositionView(
                    product_id=row.product_id,
                    symbol=symbols.get(row.product_id, f"PRODUCT-{row.product_id}"),
                    side=_position_side(row.signed_contracts),
                    contracts=str(abs(row.signed_contracts)),
                    entry_price=_decimal(row.entry_price),
                    mark_price=_decimal(row.mark_price),
                    last_price=None,
                    leverage=None,
                    liquidation_price=_decimal(row.liquidation_price),
                    unrealised_pnl=_decimal(row.unrealized_pnl),
                    realised_pnl=None,
                    margin_used=_decimal(row.margin),
                    funding_rate=None,
                    protection_state=_protection_evidence(
                        protection_by_product.get(row.product_id), risk
                    )[0],
                    protection_observed_at=_protection_evidence(
                        protection_by_product.get(row.product_id), risk
                    )[1],
                    protection_reconciliation_generation=_protection_evidence(
                        protection_by_product.get(row.product_id), risk
                    )[2],
                )
                for row in positions
            )
            order_views = tuple(
                _order_view(
                    row,
                    symbols.get(row.product_id, f"PRODUCT-{row.product_id}"),
                )
                for row in orders
            )
            reference_balance = next(
                (
                    row
                    for row in balances
                    if risk is not None and row.asset_id == risk.reference_asset_id
                ),
                balances[0] if len(balances) == 1 else None,
            )
            equity = None if pnl is None else pnl.observed_equity
            margin = sum(
                (row.margin for row in positions if row.margin is not None),
                Decimal(0),
            )
            margin_percent = (
                None if equity is None or equity <= 0 else (margin / equity) * Decimal(100)
            )
            daily_pnl = None
            if (
                pnl is not None
                and pnl.reconciled_realized_pnl is not None
                and pnl.mark_to_market_unrealized_pnl is not None
            ):
                daily_pnl = pnl.reconciled_realized_pnl + pnl.mark_to_market_unrealized_pnl
            return DashboardSnapshot(
                sequence=0 if risk is None else risk.version,
                generated_at=datetime.now(UTC),
                evidence_observed_at=(None if risk is None else risk.reconciliation_observed_at),
                region=self._region,
                environment=self._environment,
                mode=self._mode,
                account_label=account.profile_name,
                catalogue_fresh=catalogue_fresh,
                stream_connected=False,
                reconciliation_state=cast(
                    ReconciliationState,
                    "UNKNOWN" if risk is None else risk.reconciliation_state,
                ),
                risk_halted=(risk is None or risk.halt_set_state != "INITIALIZED" or active_halt),
                collateral_asset=(
                    None if reference_balance is None else reference_balance.asset_symbol
                ),
                equity=_decimal(equity),
                available_balance=(
                    None
                    if reference_balance is None
                    else _decimal(reference_balance.available_balance)
                ),
                total_exposure=None,
                daily_pnl=_decimal(daily_pnl),
                margin_usage_percent=_decimal(margin_percent),
                positions=position_views,
                open_orders=order_views,
            )
        except (PersistenceError, ValueError, TypeError):
            raise ControlPlaneUnavailable() from None

    async def preview_kill(self, *, flatten: bool) -> KillImpact:
        """Return counts only when both exchange collection resources are complete."""
        if type(flatten) is not bool:
            raise ControlPlaneUnavailable() from None
        try:
            async with self._database.transaction() as session:
                _, scope = await self._scope(session)
                risk = await self._risk_state(session, scope)
                order_count: int | None = None
                position_count: int | None = None
                evidence_observed_at: datetime | None = None
                if (
                    risk is not None
                    and risk.reconciliation_state == "READY_CLAIM"
                    and risk.reconciliation_run_id is not None
                    and risk.reconciliation_observed_at is not None
                ):
                    resources = list(
                        (
                            await session.execute(
                                select(ReconciliationResource).where(
                                    *self._scope_predicates(ReconciliationResource, scope),
                                    ReconciliationResource.run_id == risk.reconciliation_run_id,
                                    ReconciliationResource.resource_kind.in_(
                                        ("OPEN_ORDERS", "MARGINED_POSITIONS")
                                    ),
                                    ReconciliationResource.state == "COLLECTED",
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    by_kind = {row.resource_kind: row for row in resources}
                    if len(resources) == 2 and set(by_kind) == {
                        "OPEN_ORDERS",
                        "MARGINED_POSITIONS",
                    }:
                        order_count = by_kind["OPEN_ORDERS"].items_received
                        evidence_observed_at = risk.reconciliation_observed_at
                        position_count = (
                            await session.execute(
                                select(func.count(PositionSnapshot.id)).where(
                                    *self._scope_predicates(PositionSnapshot, scope),
                                    PositionSnapshot.run_id == risk.reconciliation_run_id,
                                    PositionSnapshot.resource_kind == "MARGINED_POSITIONS",
                                    PositionSnapshot.signed_contracts != 0,
                                )
                            )
                        ).scalar_one()
            return KillImpact(
                open_order_count=order_count,
                position_count=position_count,
                evidence_state=(
                    "RECONCILED_CLAIM"
                    if order_count is not None and position_count is not None
                    else "UNKNOWN"
                ),
                evidence_observed_at=evidence_observed_at,
                warning=(
                    "Cancel open orders, halt new risk, flatten positions reduce-only, then "
                    "reconcile."
                    if flatten
                    else (
                        "Cancel open orders, halt new risk, retain positions, then reconcile "
                        "protection."
                    )
                ),
            )
        except (PersistenceError, ValueError, TypeError):
            raise ControlPlaneUnavailable() from None

    async def enqueue_kill(self, command: KillCommand) -> QueuedCommand:
        """Durably enqueue or recover the sole active global kill command."""
        try:
            async with self._database.transaction() as session:
                _, scope = await self._scope(session)
                result = await self._commands.enqueue_or_replay(
                    session,
                    scope,
                    request_id=str(command.request_id),
                    operation_kind="GLOBAL_KILL",
                    correlation=CommandCorrelation(correlation_id=command.command_id),
                    evidence={"flatten": command.flatten, "preview_id": command.preview_id},
                )
            return _command(result.command)
        except (PersistenceError, ValueError, TypeError):
            raise ControlPlaneUnavailable() from None

    async def command_status(self, command_id: UUID) -> QueuedCommand | None:
        """Read one exact durable command receipt or prove scoped absence."""
        try:
            async with self._database.transaction() as session:
                _, scope = await self._scope(session)
                receipt = await self._commands.get_by_identity(session, scope, command_id)
        except (PersistenceError, ValueError, TypeError):
            raise ControlPlaneUnavailable() from None
        if receipt is None or receipt.operation_kind != "GLOBAL_KILL":
            return None
        return _command(receipt)

    async def active_kill(self) -> QueuedCommand | None:
        """Return the server-authoritative active kill identity, if any."""
        try:
            async with self._database.transaction() as session:
                _, scope = await self._scope(session)
                receipt = await self._commands.get_active_global_kill(session, scope)
            return None if receipt is None else _command(receipt)
        except (PersistenceError, ValueError, TypeError):
            raise ControlPlaneUnavailable() from None

    async def aclose(self) -> None:
        """Close the owned database engine after API shutdown."""
        await self._database.aclose()


__all__ = ["DatabaseControlPlane"]
