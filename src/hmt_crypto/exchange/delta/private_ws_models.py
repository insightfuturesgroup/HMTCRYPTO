"""Conservative typed observations for documented Delta private channels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class PrivateWsAction(StrEnum):
    """Documented private-channel action vocabulary."""

    SNAPSHOT = "snapshot"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class MarginUpdate:
    """One documented margins-channel wallet update."""

    asset_id: int
    asset_symbol: str
    wallet_id: int
    user_id: int
    balance: Decimal
    available_balance: Decimal
    blocked_margin: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class PositionRow:
    """Position values shared by snapshot and incremental observations."""

    symbol: str
    product_id: int
    user_id: int | None
    size: int
    margin: Decimal
    entry_price: Decimal
    liquidation_price: Decimal
    bankruptcy_price: Decimal
    commission: Decimal


@dataclass(frozen=True, slots=True)
class PositionUpdate:
    """One documented position create/update/delete observation."""

    action: PrivateWsAction
    reason: str | None
    row: PositionRow


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """One channel snapshot; an empty tuple is a valid zero-position snapshot."""

    rows: tuple[PositionRow, ...]


@dataclass(frozen=True, slots=True)
class OrderRow:
    """Allowlisted common order state from a snapshot or incremental update."""

    symbol: str
    product_id: int
    order_id: int
    user_id: int | None
    client_order_id: str | None
    side: str
    size: int
    unfilled_size: int
    state: str
    limit_price: Decimal | None
    average_fill_price: Decimal | None
    reduce_only: bool | None


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    """One incremental order event carrying a symbol-local venue sequence."""

    action: PrivateWsAction
    reason: str | None
    sequence: int
    observed_at: datetime
    row: OrderRow


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    """One symbol-scoped open-order snapshot and its venue sequence baseline."""

    symbol: str
    sequence: int
    observed_at: datetime
    rows: tuple[OrderRow, ...]


@dataclass(frozen=True, slots=True)
class UserTradeUpdate:
    """One compact v2/user_trades fill; fee is intentionally unavailable."""

    symbol: str
    fill_id: str
    reason: str
    user_id: int
    order_id: int
    side: str
    size: int
    price: Decimal
    position_after: int
    role: str
    client_order_id: str
    observed_at: datetime
    sequence: int
    fee_provisional: bool = True


PrivateWsObservation = (
    MarginUpdate | PositionUpdate | PositionSnapshot | OrderUpdate | OrderSnapshot | UserTradeUpdate
)


__all__ = [
    "MarginUpdate",
    "OrderRow",
    "OrderSnapshot",
    "OrderUpdate",
    "PositionRow",
    "PositionSnapshot",
    "PositionUpdate",
    "PrivateWsAction",
    "PrivateWsObservation",
    "UserTradeUpdate",
]
