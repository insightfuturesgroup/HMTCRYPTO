"""Kill-control adapter over the authoritative PAPER execution journal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from hmt_crypto.oms.kill_switch import KillAction, KillEvent, KillMode
from hmt_crypto.oms.kill_worker import KillActionInvocation, KillActionResult
from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.worker.paper_repository import JournalEvent, PaperJournalRepository


@dataclass(frozen=True, slots=True)
class PaperControlOrder:
    """One rebuilt active simulator order."""

    order_id: str
    product_id: int
    side: str
    remaining_contracts: int
    reduce_only: bool


@dataclass(frozen=True, slots=True)
class PaperControlPosition:
    """One rebuilt signed simulator position."""

    product_id: int
    signed_contracts: int


@dataclass(frozen=True, slots=True)
class PaperControlSnapshot:
    """Authoritative state rebuilt only from immutable execution/control events."""

    risk_halted: bool
    active_orders: tuple[PaperControlOrder, ...]
    positions: tuple[PaperControlPosition, ...]
    protection_established: bool
    protection_verified: bool


def _scope(value: AccountScope) -> AccountScope:
    if type(value) is not AccountScope or value.mode not in {"PAPER", "BACKTEST"}:
        raise ValueError("offline account scope required")
    return AccountScope.model_validate(value)


def _as_int(payload: dict[str, object], field: str) -> int:
    value = payload.get(field)
    if type(value) is not int or value < 0:
        raise RuntimeError("paper journal integer projection is invalid")
    return value


class PaperJournalKillPort:
    """Execute kill actions against rebuilt simulator truth, never counters."""

    def __init__(self) -> None:
        self._repository = PaperJournalRepository()

    async def snapshot(self, session: AsyncSession, scope: AccountScope) -> PaperControlSnapshot:
        """Rebuild current orders, positions, halt, and protection deterministically."""

        scope = _scope(scope)
        events = await self._repository.load_scope_events(session, scope)
        orders: dict[str, PaperControlOrder] = {}
        positions: dict[int, int] = {}
        risk_halted = False
        established = False
        verified = False
        for journal_event in events:
            payload = journal_event.payload
            if journal_event.event_kind == "COMMAND" and payload.get("operation") == "PLACE_ORDER":
                order_id = payload.get("order_id")
                side = payload.get("side")
                reduce_only = payload.get("reduce_only")
                if (
                    type(order_id) is not str
                    or side not in {"BUY", "SELL"}
                    or type(reduce_only) is not bool
                ):
                    raise RuntimeError("paper command projection is invalid")
                orders[order_id] = PaperControlOrder(
                    order_id,
                    _as_int(payload, "product_id"),
                    side,
                    _as_int(payload, "contracts"),
                    reduce_only,
                )
            elif journal_event.event_kind in {"FILL", "FLATTEN_FILL"}:
                product_id = _as_int(payload, "product_id")
                contracts = _as_int(payload, "contracts")
                side = payload.get("side")
                if product_id < 1 or contracts < 1 or side not in {"BUY", "SELL"}:
                    raise RuntimeError("paper fill projection is invalid")
                positions[product_id] = positions.get(product_id, 0) + (
                    contracts if side == "BUY" else -contracts
                )
                order_id = payload.get("order_id")
                if type(order_id) is str and order_id in orders:
                    current = orders[order_id]
                    remaining = current.remaining_contracts - contracts
                    if remaining < 0:
                        raise RuntimeError("paper fill exceeds the durable command")
                    if remaining == 0:
                        orders.pop(order_id)
                    else:
                        orders[order_id] = PaperControlOrder(
                            current.order_id,
                            current.product_id,
                            current.side,
                            remaining,
                            current.reduce_only,
                        )
            elif journal_event.event_kind == "ORDER_CANCELLED":
                order_id = payload.get("order_id")
                if type(order_id) is not str:
                    raise RuntimeError("paper cancellation projection is invalid")
                orders.pop(order_id, None)
            elif journal_event.event_kind == "RISK_HALT":
                risk_halted = True
            elif journal_event.event_kind == "PROTECTION":
                state = payload.get("state")
                if state == "ESTABLISHED":
                    established = True
                    verified = False
                elif state == "VERIFIED":
                    if not established:
                        raise RuntimeError("paper protection verification lacks establishment")
                    verified = True
                else:
                    raise RuntimeError("paper protection projection is invalid")
        active = tuple(sorted(orders.values(), key=lambda item: item.order_id))
        position_rows = tuple(
            PaperControlPosition(product_id, contracts)
            for product_id, contracts in sorted(positions.items())
            if contracts != 0
        )
        return PaperControlSnapshot(
            risk_halted,
            active,
            position_rows,
            established,
            verified,
        )

    async def execute_once(
        self,
        session: AsyncSession,
        scope: AccountScope,
        invocation: KillActionInvocation,
    ) -> KillActionResult:
        """Apply or replay one operation-keyed action against journal truth."""

        scope = _scope(scope)
        if (
            type(invocation) is not KillActionInvocation
            or invocation.account_id != scope.account_id
            or invocation.region != scope.region
            or invocation.environment != scope.environment
            or invocation.execution_mode != scope.mode
        ):
            raise ValueError("kill invocation is outside the paper scope")
        await self._repository.lock_scope(session, scope)
        operation_key = str(invocation.operation_key)
        all_events = await self._repository.load_scope_events(session, scope)
        replay = [
            item
            for item in all_events
            if item.event_kind == "KILL_ACTION"
            and item.payload.get("operation_key") == operation_key
        ]
        if replay:
            if len(replay) != 1:
                raise RuntimeError("paper kill action evidence is ambiguous")
            event_name = replay[0].payload.get("kill_event")
            if type(event_name) is not str:
                raise RuntimeError("paper kill result evidence is invalid")
            return KillActionResult(KillEvent(event_name))
        before = await self.snapshot(session, scope)
        observed_at = datetime.now(UTC)
        effects, result = self._plan(invocation, before, observed_at)
        effects.append(
            JournalEvent(
                "KILL_ACTION",
                operation_key,
                observed_at,
                {
                    "operation_key": operation_key,
                    "request_id": str(invocation.request_id),
                    "transition_count": invocation.transition_count,
                    "action": invocation.action.value,
                    "kill_event": result.value,
                },
            )
        )
        request_id = f"kill-{operation_key}"
        await self._repository.ensure_intent(
            session,
            scope,
            request_id=request_id,
            intent_payload={
                "paper_control_schema_version": 1,
                "operation_key": operation_key,
                "action": invocation.action.value,
            },
            occurred_at=observed_at,
        )
        claim = await self._repository.claim(
            session,
            scope,
            request_id,
            owner_id=invocation.operation_key,
            now=observed_at,
            duration=timedelta(minutes=1),
        )
        if claim is None:
            raise RuntimeError("paper kill action claim is unavailable")
        await self._repository.append(
            session,
            claim,
            events=tuple(effects),
            status="COMPLETE",
            recorded_at=observed_at,
        )
        return KillActionResult(result)

    @staticmethod
    def _plan(
        invocation: KillActionInvocation,
        before: PaperControlSnapshot,
        observed_at: datetime,
    ) -> tuple[list[JournalEvent], KillEvent]:
        key = str(invocation.operation_key)
        action = invocation.action
        effects: list[JournalEvent] = []
        if action is KillAction.PERSIST_RISK_HALT:
            effects.append(
                JournalEvent(
                    "RISK_HALT",
                    key,
                    observed_at,
                    {"operation_key": key, "cause": "GLOBAL_KILL"},
                )
            )
            return effects, KillEvent.RISK_HALT_PERSISTED
        if action is KillAction.CANCEL_RISK_INCREASING_ORDERS:
            if not before.risk_halted:
                return effects, KillEvent.RISK_INCREASING_CANCEL_UNRESOLVED
            for order in before.active_orders:
                if not order.reduce_only:
                    effects.append(_cancel_event(key, order, observed_at, "RISK_INCREASING"))
            return effects, KillEvent.RISK_INCREASING_CANCEL_COMPLETED
        if action is KillAction.FLATTEN_POSITIONS_REDUCE_ONLY:
            if not before.risk_halted:
                return effects, KillEvent.FLATTEN_UNRESOLVED
            for position in before.positions:
                effects.append(_flatten_event(key, position, observed_at))
            return effects, KillEvent.FLATTEN_COMPLETED
        if action is KillAction.CANCEL_REMAINING_OPEN_ORDERS:
            if not before.risk_halted:
                return effects, KillEvent.REMAINDER_CANCEL_UNRESOLVED
            for order in before.active_orders:
                effects.append(_cancel_event(key, order, observed_at, "REMAINDER"))
            return effects, KillEvent.REMAINDER_CANCEL_COMPLETED
        if action is KillAction.RECONCILE_EXCHANGE_TRUTH:
            if before.active_orders:
                result = KillEvent.RECONCILIATION_UNRESOLVED
            elif invocation.kill_mode is KillMode.CANCEL_AND_FLATTEN:
                result = (
                    KillEvent.RECONCILED_FLAT
                    if not before.positions
                    else KillEvent.RECONCILIATION_UNRESOLVED
                )
            else:
                result = KillEvent.RECONCILED_RETAINED
            return [_reconciliation_event(key, before, observed_at, result)], result
        if action is KillAction.REESTABLISH_PROTECTION:
            effects.append(
                JournalEvent(
                    "PROTECTION",
                    key,
                    observed_at,
                    {"operation_key": key, "state": "ESTABLISHED"},
                )
            )
            return effects, KillEvent.PROTECTION_SUBMITTED
        if action is KillAction.VERIFY_PROTECTION:
            if before.positions and not before.protection_established:
                return effects, KillEvent.PROTECTION_UNVERIFIED
            effects.append(
                JournalEvent(
                    "PROTECTION",
                    key,
                    observed_at,
                    {"operation_key": key, "state": "VERIFIED"},
                )
            )
            return effects, KillEvent.PROTECTION_VERIFIED
        if action is KillAction.FINAL_RECONCILIATION:
            safe = before.risk_halted and not before.active_orders
            if invocation.kill_mode is KillMode.CANCEL_AND_FLATTEN:
                safe = safe and not before.positions
            elif before.positions:
                safe = safe and before.protection_established and before.protection_verified
            result = (
                KillEvent.FINAL_RECONCILED_SAFE
                if safe
                else KillEvent.FINAL_RECONCILIATION_UNRESOLVED
            )
            return [_reconciliation_event(key, before, observed_at, result)], result
        raise ValueError("unsupported paper kill action")


def _cancel_event(
    operation_key: str,
    order: PaperControlOrder,
    observed_at: datetime,
    reason: str,
) -> JournalEvent:
    return JournalEvent(
        "ORDER_CANCELLED",
        f"{operation_key}:{order.order_id}",
        observed_at,
        {
            "operation_key": operation_key,
            "order_id": order.order_id,
            "reason": f"GLOBAL_KILL_{reason}",
        },
    )


def _flatten_event(
    operation_key: str,
    position: PaperControlPosition,
    observed_at: datetime,
) -> JournalEvent:
    return JournalEvent(
        "FLATTEN_FILL",
        f"{operation_key}:{position.product_id}",
        observed_at,
        {
            "operation_key": operation_key,
            "product_id": position.product_id,
            "side": "SELL" if position.signed_contracts > 0 else "BUY",
            "contracts": abs(position.signed_contracts),
            "reduce_only": True,
            "model": "PAPER_KILL_FLATTEN_V1",
        },
    )


def _reconciliation_event(
    operation_key: str,
    snapshot: PaperControlSnapshot,
    observed_at: datetime,
    result: KillEvent,
) -> JournalEvent:
    return JournalEvent(
        "RECONCILIATION",
        operation_key,
        observed_at,
        {
            "operation_key": operation_key,
            "active_orders": len(snapshot.active_orders),
            "open_positions": len(snapshot.positions),
            "result": result.value,
        },
    )


__all__ = [
    "PaperControlOrder",
    "PaperControlPosition",
    "PaperControlSnapshot",
    "PaperJournalKillPort",
]
