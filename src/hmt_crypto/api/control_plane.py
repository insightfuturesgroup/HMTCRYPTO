"""Exchange-neutral, secret-free contracts exposed by the operator control API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

HealthState = Literal["ready", "degraded", "failed", "disabled"]
CommandState = Literal["QUEUED", "CLAIMED", "SUBMITTING", "UNCERTAIN", "COMPLETED", "FAILED"]
ProtectionState = Literal[
    "UNKNOWN",
    "UNPROTECTED",
    "VERIFYING",
    "PAPER_SIMULATED",
    "NATIVE_VERIFIED",
    "SYNTHETIC_MONITORING",
    "STALE",
]
KillEvidenceState = Literal["UNKNOWN", "RECONCILED_CLAIM"]


class ControlPlaneUnavailable(RuntimeError):
    """Report that authoritative control-plane evidence is unavailable."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("control plane unavailable")


class ApiView(BaseModel):
    """Strict immutable base for allowlisted API views."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class ComponentHealth(ApiView):
    """One safe, bounded component-health observation."""

    component: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")
    state: HealthState
    reason: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_.-]+$")
    observed_at: datetime


class HealthSnapshot(ApiView):
    """Sequenced liveness/readiness evidence."""

    live: StrictBool
    ready: StrictBool
    sequence: StrictInt = Field(ge=0, lt=2**63)
    observed_at: datetime
    components: tuple[ComponentHealth, ...] = Field(max_length=64)


class PositionView(ApiView):
    """Secret-free, exchange-reconciled position projection."""

    product_id: StrictInt = Field(gt=0, lt=2**63)
    symbol: str = Field(min_length=1, max_length=64)
    side: Literal["long", "short", "flat"]
    contracts: str = Field(min_length=1, max_length=64)
    entry_price: str | None = Field(default=None, max_length=64)
    mark_price: str | None = Field(default=None, max_length=64)
    last_price: str | None = Field(default=None, max_length=64)
    leverage: str | None = Field(default=None, max_length=64)
    liquidation_price: str | None = Field(default=None, max_length=64)
    unrealised_pnl: str | None = Field(default=None, max_length=64)
    realised_pnl: str | None = Field(default=None, max_length=64)
    margin_used: str | None = Field(default=None, max_length=64)
    funding_rate: str | None = Field(default=None, max_length=64)
    protection_state: ProtectionState
    protection_observed_at: datetime | None
    protection_reconciliation_generation: StrictInt | None = Field(default=None, ge=1, lt=2**63)


class OrderView(ApiView):
    """Secret-free local/exchange order projection."""

    local_order_id: UUID
    exchange_order_id: str | None = Field(default=None, max_length=128)
    client_order_id: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=64)
    side: Literal["buy", "sell"]
    order_type: str = Field(min_length=1, max_length=32)
    contracts: str = Field(min_length=1, max_length=64)
    limit_price: str | None = Field(default=None, max_length=64)
    status: str = Field(min_length=1, max_length=32)
    reduce_only: StrictBool
    updated_at: datetime


class DashboardSnapshot(ApiView):
    """One authoritative, sequenced dashboard snapshot."""

    sequence: StrictInt = Field(ge=0, lt=2**63)
    generated_at: datetime
    evidence_observed_at: datetime | None
    region: Literal["india", "global"]
    environment: Literal["testnet", "prod"]
    mode: Literal["BACKTEST", "PAPER", "LIVE"]
    account_label: str = Field(min_length=1, max_length=128)
    catalogue_fresh: StrictBool
    stream_connected: StrictBool
    reconciliation_state: Literal["UNKNOWN", "BLOCKED", "READY_CLAIM"]
    risk_halted: StrictBool
    collateral_asset: str | None = Field(default=None, max_length=32)
    equity: str | None = Field(default=None, max_length=64)
    available_balance: str | None = Field(default=None, max_length=64)
    total_exposure: str | None = Field(default=None, max_length=64)
    daily_pnl: str | None = Field(default=None, max_length=64)
    margin_usage_percent: str | None = Field(default=None, max_length=64)
    positions: tuple[PositionView, ...] = Field(max_length=10_000)
    open_orders: tuple[OrderView, ...] = Field(max_length=10_000)


class KillImpact(ApiView):
    """Truth-labelled impact evidence used to mint a short-lived preview."""

    open_order_count: StrictInt | None = Field(default=None, ge=0, lt=2**31)
    position_count: StrictInt | None = Field(default=None, ge=0, lt=2**31)
    evidence_state: KillEvidenceState
    evidence_observed_at: datetime | None
    warning: str = Field(min_length=1, max_length=256)


class KillCommand(ApiView):
    """Idempotent durable kill command admitted by the API."""

    command_id: UUID
    request_id: UUID
    preview_id: UUID
    flatten: StrictBool
    accepted_at: datetime


class QueuedCommand(ApiView):
    """Durable command receipt; completion is observed separately."""

    command_id: UUID
    state: CommandState
    accepted_at: datetime


class ControlPlane(Protocol):
    """Secret-free control/query surface implemented by the durable backend."""

    async def health(self) -> HealthSnapshot: ...

    async def dashboard(self) -> DashboardSnapshot: ...

    async def preview_kill(self, *, flatten: bool) -> KillImpact: ...

    async def enqueue_kill(self, command: KillCommand) -> QueuedCommand: ...

    async def command_status(self, command_id: UUID) -> QueuedCommand | None: ...

    async def active_kill(self) -> QueuedCommand | None: ...


class UnavailableControlPlane:
    """Fail-closed composition used until a durable backend is supplied."""

    async def health(self) -> HealthSnapshot:
        """Return a truthful live-but-unready status."""
        now = datetime.now(UTC)
        return HealthSnapshot(
            live=True,
            ready=False,
            sequence=0,
            observed_at=now,
            components=(
                ComponentHealth(
                    component="control_plane",
                    state="disabled",
                    reason="durable_backend_unconfigured",
                    observed_at=now,
                ),
            ),
        )

    async def dashboard(self) -> DashboardSnapshot:
        """Reject invented dashboard state."""
        raise ControlPlaneUnavailable() from None

    async def preview_kill(self, *, flatten: bool) -> KillImpact:
        """Reject an emergency command without durable authority."""
        del flatten
        raise ControlPlaneUnavailable() from None

    async def enqueue_kill(self, command: KillCommand) -> QueuedCommand:
        """Reject an in-memory-only command acknowledgement."""
        del command
        raise ControlPlaneUnavailable() from None

    async def command_status(self, command_id: UUID) -> QueuedCommand | None:
        """Reject an invented command status."""
        del command_id
        raise ControlPlaneUnavailable() from None

    async def active_kill(self) -> QueuedCommand | None:
        """Reject an invented active-command observation."""
        raise ControlPlaneUnavailable() from None


__all__ = [
    "CommandState",
    "ComponentHealth",
    "ControlPlane",
    "ControlPlaneUnavailable",
    "DashboardSnapshot",
    "HealthSnapshot",
    "KillEvidenceState",
    "KillCommand",
    "KillImpact",
    "OrderView",
    "PositionView",
    "ProtectionState",
    "QueuedCommand",
    "UnavailableControlPlane",
]
