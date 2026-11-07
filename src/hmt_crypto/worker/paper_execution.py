"""Durable, offline-only PAPER/BACKTEST execution orchestration.

The worker deliberately has no exchange client, credential provider, private
HTTP transport, or LIVE construction path. Durable journal integration is
implemented below the immutable request contracts in this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel

from hmt_crypto.backtest.models import (
    BacktestFundingEvent,
    BacktestFundingSchedule,
    BacktestProductAssumption,
    BacktestRunStatus,
    LiquidityRole,
)
from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_accounting import (
    PaperReplayFrame,
    encode_versioned_report,
    run_paper_accounting,
    versioned_report_payload,
)
from hmt_crypto.oms.paper_models import (
    PaperCandle,
    PaperModelConfig,
    PaperOrderIntent,
    PaperSide,
    PaperTopOfBook,
)
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.paper_execution_models import encode_paper_event_payload
from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.risk.inputs import RiskIntent, RiskPolicy, RiskSnapshot
from hmt_crypto.risk.manager import RiskManager
from hmt_crypto.risk.models import DecisionOutcome, RiskEffect, Side
from hmt_crypto.worker.paper_repository import (
    JournalEvent,
    PaperJournalClaim,
    PaperJournalRepository,
    StoredJournalEvent,
)


class PaperIntentSource(StrEnum):
    """Operator provenance for one durable offline intent."""

    MANUAL = "MANUAL"
    STRATEGY = "STRATEGY"


class PaperFundingPolicy(StrEnum):
    """Closed funding-evidence behavior for an offline run."""

    REQUIRE_RECORDED_MARKS = "REQUIRE_RECORDED_MARKS"
    EXPLICIT_SYNTHETIC_ZERO_FUNDING_NOT_VENUE_DATA = (
        "EXPLICIT_SYNTHETIC_ZERO_FUNDING_NOT_VENUE_DATA"
    )


class PaperCrashPoint(StrEnum):
    """Test fault boundaries matching durable production checkpoints."""

    AFTER_INTENT = "AFTER_INTENT"
    AFTER_PREPARATION = "AFTER_PREPARATION"
    AFTER_FILL_PERSISTENCE = "AFTER_FILL_PERSISTENCE"


@dataclass(frozen=True, slots=True)
class RecordedPaperEvent:
    """One immutable recorded market frame with an explicit valuation mark."""

    sequence: int
    market_event: PaperTopOfBook | PaperCandle
    mark_price: Decimal

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or not 1 <= self.sequence < 2**63:
            raise ValueError("recorded event sequence must be a positive exact integer")
        if type(self.market_event) not in (PaperTopOfBook, PaperCandle):
            raise ValueError("recorded market event must be an exact PAPER event")
        if (
            type(self.mark_price) is not Decimal
            or not self.mark_price.is_finite()
            or self.mark_price <= 0
        ):
            raise ValueError("recorded mark must be a positive exact Decimal")


@dataclass(frozen=True, slots=True)
class PaperExecutionRequest:
    """Complete durable input for one risk-gated offline execution."""

    scope: AccountScope
    request_id: str
    source: PaperIntentSource
    intent: PaperOrderIntent
    risk_policy: RiskPolicy
    risk_intent: RiskIntent
    risk_snapshot: RiskSnapshot
    model_config: PaperModelConfig
    recorded_events: tuple[RecordedPaperEvent, ...]
    product: BacktestProductAssumption
    liquidity_role: LiquidityRole
    funding_schedule: BacktestFundingSchedule | None
    funding_events: tuple[BacktestFundingEvent, ...]
    funding_policy: PaperFundingPolicy
    initial_cash: Decimal
    assumptions_version: str
    signal: dict[str, object] | None

    def __post_init__(self) -> None:
        if type(self.scope) is not AccountScope:
            raise ValueError("paper execution requires an exact account scope")
        scope = AccountScope.model_validate(self.scope)
        if scope.mode not in {"PAPER", "BACKTEST"}:
            raise ValueError("offline PAPER/BACKTEST scope is required")
        if (
            type(self.request_id) is not str
            or not 1 <= len(self.request_id) <= 128
            or "\x00" in self.request_id
            or not self.request_id.strip()
        ):
            raise ValueError("request identity must be bounded nonblank text")
        if type(self.source) is not PaperIntentSource:
            raise ValueError("intent source must be a canonical enum member")
        if type(self.intent) is not PaperOrderIntent:
            raise ValueError("intent must be an exact PAPER simulator intent")
        if (
            type(self.risk_policy) is not RiskPolicy
            or type(self.risk_intent) is not RiskIntent
            or type(self.risk_snapshot) is not RiskSnapshot
            or type(self.model_config) is not PaperModelConfig
            or type(self.product) is not BacktestProductAssumption
            or type(self.liquidity_role) is not LiquidityRole
            or type(self.funding_policy) is not PaperFundingPolicy
        ):
            raise ValueError("paper execution inputs must use exact immutable contracts")
        expected_mode = (
            OmsExecutionMode.PAPER if scope.mode == "PAPER" else OmsExecutionMode.BACKTEST
        )
        if (
            self.intent.mode is not expected_mode
            or self.intent.paper_account_id != scope.account_id
            or self.intent.product_id != self.risk_intent.product_id
            or self.intent.product_id != self.product.product_id
            or self.intent.contracts != self.risk_intent.contracts
            or (self.intent.side is PaperSide.BUY) != (self.risk_intent.side is Side.BUY)
            or self.intent.reduce_only != (self.risk_intent.risk_effect.value == "REDUCE_ONLY")
        ):
            raise ValueError("scope, PAPER intent, product, and risk intent are inconsistent")
        if (
            type(self.recorded_events) is not tuple
            or len(self.recorded_events) < 2
            or any(type(item) is not RecordedPaperEvent for item in self.recorded_events)
            or tuple(item.sequence for item in self.recorded_events)
            != tuple(range(1, len(self.recorded_events) + 1))
            or any(
                item.market_event.product_id != self.intent.product_id
                for item in self.recorded_events
            )
        ):
            raise ValueError("recorded events must be an ordered product-specific tuple")
        observed = tuple(item.market_event.observed_at for item in self.recorded_events)
        if any(
            current <= previous for previous, current in zip(observed, observed[1:], strict=False)
        ):
            raise ValueError("recorded event times must increase strictly")
        if type(self.funding_events) is not tuple or any(
            type(item) is not BacktestFundingEvent for item in self.funding_events
        ):
            raise ValueError("funding events must be an exact immutable tuple")
        if (
            self.funding_schedule is not None
            and type(self.funding_schedule) is not BacktestFundingSchedule
        ):
            raise ValueError("funding schedule must be exact")
        if (
            self.funding_schedule is not None
            and self.funding_schedule.product_id != self.product.product_id
            or any(item.product_id != self.product.product_id for item in self.funding_events)
        ):
            raise ValueError("funding evidence is in the wrong product namespace")
        if (
            type(self.initial_cash) is not Decimal
            or not self.initial_cash.is_finite()
            or self.initial_cash < 0
            or type(self.assumptions_version) is not str
            or not 1 <= len(self.assumptions_version) <= 128
            or not self.assumptions_version.isascii()
            or not self.assumptions_version.isprintable()
            or self.signal is not None
            and type(self.signal) is not dict
            or self.source is PaperIntentSource.STRATEGY
            and self.signal is None
        ):
            raise ValueError("cash, assumptions, or signal provenance is invalid")
        object.__setattr__(self, "scope", scope)


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    """Versioned summary returned from one idempotent execution."""

    status: str
    request_id: str
    decision_sha256: str
    report_json: str | None
    event_count: int
    replayed: bool


def _json_value(value: object) -> Any:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is Decimal:
        return format(value, "f")
    if type(value) is datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("paper evidence timestamps must be UTC")
        return value.isoformat(timespec="microseconds")
    if type(value) is UUID:
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if type(value) is tuple:
        return [_json_value(item) for item in value]
    if type(value) is list:
        return [_json_value(item) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("paper evidence mappings require text keys")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    raise ValueError(f"unsupported paper evidence type: {type(value).__name__}")


def _intent_payload(request: PaperExecutionRequest) -> dict[str, object]:
    value = _json_value(
        {
            "paper_intent_schema_version": 1,
            "scope": request.scope,
            "request_id": request.request_id,
            "source": request.source,
            "intent": request.intent,
            "risk_policy": request.risk_policy,
            "risk_intent": request.risk_intent,
            "risk_snapshot": request.risk_snapshot,
            "model_config": request.model_config,
            "recorded_events": request.recorded_events,
            "product": request.product,
            "liquidity_role": request.liquidity_role,
            "funding_schedule": request.funding_schedule,
            "funding_events": request.funding_events,
            "funding_policy": request.funding_policy,
            "initial_cash": request.initial_cash,
            "assumptions_version": request.assumptions_version,
            "signal": request.signal,
        }
    )
    assert isinstance(value, dict)
    return value


def _event_payload(event: object) -> dict[str, object]:
    value = _json_value(event)
    if not isinstance(value, dict):
        raise ValueError("journal evidence must encode to an object")
    return value


def _last_scope_event(
    events: tuple[StoredJournalEvent, ...], event_kind: str
) -> StoredJournalEvent | None:
    return next((item for item in reversed(events) if item.event_kind == event_kind), None)


def _kill_halt_denial_payload(
    evaluation: object,
    halt: StoredJournalEvent,
) -> dict[str, object]:
    values: dict[str, object] = {
        "decision_schema_version": 1,
        "outcome": "DENIED",
        "reason_code": "DURABLE_PAPER_KILL_HALT",
        "base_risk_evaluation": _event_payload(evaluation),
        "halt_event_key": halt.event_key,
        "halt_payload_sha256": halt.payload_sha256,
        "halt_scope_sequence": halt.scope_sequence,
    }
    _, decision_sha256 = encode_paper_event_payload(
        {"kind": "paper_kill_halt_denial_v1", "payload": values}
    )
    values["decision_sha256"] = decision_sha256
    return values


def _control_abort_report(
    request: PaperExecutionRequest,
    evidence: StoredJournalEvent,
    reason: str,
) -> dict[str, object]:
    intent = _intent_payload(request)
    _, intent_sha256 = encode_paper_event_payload(intent)
    _, run_id = encode_paper_event_payload(
        {
            "kind": "paper_kill_abort_run_v1",
            "account_id": str(request.scope.account_id),
            "mode": request.scope.mode,
            "request_id": request.request_id,
            "control_event_key": evidence.event_key,
        }
    )
    return {
        "report_schema_version": 1,
        "report_kind": "OFFLINE_PAPER_BACKTEST_EXECUTION",
        "profitability_claim": False,
        "request_id": request.request_id,
        "account_id": str(request.scope.account_id),
        "mode": request.scope.mode,
        "status": "INCOMPLETE",
        "run_id_sha256": run_id,
        "config_sha256": intent_sha256,
        "data_sha256": encode_paper_event_payload(
            {
                "kind": "paper_kill_abort_data_v1",
                "recorded_events": intent["recorded_events"],
            }
        )[1],
        "funding_policy": request.funding_policy.value,
        "initial_cash": format(request.initial_cash, "f"),
        "final_equity": format(request.initial_cash, "f"),
        "net_pnl": "0",
        "gross_realized_pnl": "0",
        "realized_net_pnl": "0",
        "unrealized_pnl": "0",
        "total_fees": "0",
        "total_funding": "0",
        "incomplete_reasons": [reason],
        "assumptions": [
            f"assumptions_version={request.assumptions_version}",
            "no simulated fill was admitted after durable cancellation control",
        ],
        "execution_outcome": "ABORTED_BEFORE_FILL",
    }


def _result_from_events(
    request: PaperExecutionRequest,
    status: str,
    events: tuple[StoredJournalEvent, ...],
    *,
    replayed: bool,
) -> PaperExecutionResult:
    decision_events = [
        item for item in events if item.event_kind in {"RISK_APPROVED", "RISK_DENIED"}
    ]
    if len(decision_events) != 1:
        raise RuntimeError("durable paper decision is missing or ambiguous")
    decision = decision_events[0].payload.get("decision_sha256")
    if type(decision) is not str:
        raise RuntimeError("durable paper decision identity is invalid")
    run_events = [item for item in events if item.event_kind in {"RUN_COMPLETE", "RUN_INCOMPLETE"}]
    report_json: str | None = None
    if run_events:
        if len(run_events) != 1 or type(run_events[0].payload.get("report")) is not dict:
            raise RuntimeError("durable paper report is ambiguous")
        report_json = encode_versioned_report(run_events[0].payload["report"])  # type: ignore[arg-type]
    return PaperExecutionResult(
        status=status,
        request_id=request.request_id,
        decision_sha256=decision,
        report_json=report_json,
        event_count=len(events),
        replayed=replayed,
    )


class PaperExecutionService:
    """Risk-gate, simulate, account, and project one offline request durably."""

    def __init__(
        self,
        database: Database,
        *,
        worker_id: UUID,
        clock: Callable[[], datetime] | None = None,
        repository: PaperJournalRepository | None = None,
    ) -> None:
        if type(database) is not Database or type(worker_id) is not UUID:
            raise ValueError("paper worker requires exact offline dependencies")
        self._database = database
        self._worker_id = UUID(int=worker_id.int)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository = repository or PaperJournalRepository()
        self._risk = RiskManager()

    def _now(self) -> datetime:
        value = self._clock()
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise ValueError("paper worker clock must return exact UTC")
        return value

    async def _events(self, request: PaperExecutionRequest) -> tuple[StoredJournalEvent, ...]:
        async with self._database.transaction() as session:
            return await self._repository.load_events(session, request.scope, request.request_id)

    async def _claim(self, request: PaperExecutionRequest) -> PaperJournalClaim | None:
        async with self._database.transaction() as session:
            return await self._repository.claim(
                session,
                request.scope,
                request.request_id,
                owner_id=self._worker_id,
                now=self._now(),
                duration=timedelta(minutes=5),
            )

    async def execute(
        self,
        request: PaperExecutionRequest,
        *,
        crash_point: PaperCrashPoint | None = None,
    ) -> PaperExecutionResult:
        """Execute idempotently; injected faults occur only after committed checkpoints."""

        if type(request) is not PaperExecutionRequest or (
            crash_point is not None and type(crash_point) is not PaperCrashPoint
        ):
            raise ValueError("exact paper execution request required")
        occurred_at = request.risk_snapshot.evaluated_at
        async with self._database.transaction() as session:
            intent_receipt = await self._repository.ensure_intent(
                session,
                request.scope,
                request_id=request.request_id,
                intent_payload=_intent_payload(request),
                occurred_at=occurred_at,
            )
        if crash_point is PaperCrashPoint.AFTER_INTENT:
            raise RuntimeError("injected paper crash after intent")
        if intent_receipt.head.status in {"DENIED", "COMPLETE", "INCOMPLETE"}:
            return _result_from_events(
                request,
                intent_receipt.head.status,
                await self._events(request),
                replayed=True,
            )
        evaluation = self._risk.evaluate(
            request.risk_policy, request.risk_intent, request.risk_snapshot
        )
        existing = await self._events(request)
        existing_kinds = {item.event_kind for item in existing}
        if evaluation.outcome is DecisionOutcome.DENIED:
            claim = await self._claim(request)
            if claim is None:
                events = await self._events(request)
                return _result_from_events(request, "DENIED", events, replayed=True)
            async with self._database.transaction() as session:
                receipt = await self._repository.append(
                    session,
                    claim,
                    events=(
                        JournalEvent(
                            "RISK_DENIED",
                            evaluation.decision_sha256,
                            occurred_at,
                            _event_payload(evaluation),
                        ),
                    ),
                    status="DENIED",
                    recorded_at=self._now(),
                )
            events = await self._events(request)
            return _result_from_events(
                request, receipt.head.status, events, replayed=not intent_receipt.created
            )
        if "ATTEMPT" not in existing_kinds:
            reservation = evaluation.reservation
            if reservation is None:
                raise RuntimeError("approved risk decision lacks reservation")
            command_id = uuid5(
                NAMESPACE_URL,
                f"hmt-crypto:paper-command:{request.scope.account_id}:{request.scope.mode}:"
                f"{request.request_id}",
            )
            attempt_id = uuid5(NAMESPACE_URL, f"{command_id}:simulator-attempt:1")
            prepared: list[JournalEvent] = [
                JournalEvent(
                    "RISK_APPROVED",
                    evaluation.decision_sha256,
                    occurred_at,
                    _event_payload(evaluation),
                ),
                JournalEvent(
                    "RESERVATION",
                    reservation.reservation_plan_sha256,
                    occurred_at,
                    _event_payload(reservation),
                ),
                JournalEvent(
                    "COMMAND",
                    str(command_id),
                    occurred_at,
                    {
                        "command_schema_version": 1,
                        "command_id": str(command_id),
                        "order_id": str(request.intent.order_id),
                        "operation": "PLACE_ORDER",
                        "contracts": request.intent.contracts,
                        "product_id": request.intent.product_id,
                        "side": request.intent.side.value,
                        "reduce_only": request.intent.reduce_only,
                    },
                ),
                JournalEvent(
                    "ATTEMPT",
                    str(attempt_id),
                    occurred_at,
                    {
                        "attempt_schema_version": 1,
                        "attempt_id": str(attempt_id),
                        "command_id": str(command_id),
                        "backend": "SIMULATOR",
                        "simulator_kind": request.scope.mode,
                        "credential_version": None,
                    },
                ),
            ]
            if request.source is PaperIntentSource.STRATEGY:
                assert request.signal is not None
                prepared.append(
                    JournalEvent(
                        "SIGNAL",
                        f"signal:{request.request_id}",
                        occurred_at,
                        {
                            "signal_schema_version": 1,
                            "source": "STRATEGY",
                            "signal": _json_value(request.signal),
                        },
                    )
                )
            claim = await self._claim(request)
            if claim is None:
                raise RuntimeError("paper execution claim is unavailable")
            denied_by_halt = False
            async with self._database.transaction() as session:
                await self._repository.lock_scope(session, request.scope)
                scope_events = await self._repository.load_scope_events(session, request.scope)
                halt = _last_scope_event(scope_events, "RISK_HALT")
                if halt is not None and request.risk_intent.risk_effect is RiskEffect.RISK_INCREASE:
                    denial = _kill_halt_denial_payload(evaluation, halt)
                    await self._repository.append(
                        session,
                        claim,
                        events=(
                            JournalEvent(
                                "RISK_DENIED",
                                str(denial["decision_sha256"]),
                                halt.occurred_at,
                                denial,
                            ),
                        ),
                        status="DENIED",
                        recorded_at=self._now(),
                    )
                    denied_by_halt = True
                else:
                    await self._repository.append(
                        session,
                        claim,
                        events=tuple(prepared),
                        status="PREPARED",
                        recorded_at=self._now(),
                    )
            if denied_by_halt:
                return _result_from_events(
                    request,
                    "DENIED",
                    await self._events(request),
                    replayed=not intent_receipt.created,
                )
        if crash_point is PaperCrashPoint.AFTER_PREPARATION:
            raise RuntimeError("injected paper crash after preparation")
        frames = tuple(
            PaperReplayFrame(item.market_event, item.mark_price) for item in request.recorded_events
        )
        report = run_paper_accounting(
            intent=request.intent,
            model_config=request.model_config,
            frames=frames,
            product=request.product,
            liquidity_role=request.liquidity_role,
            funding_schedule=request.funding_schedule,
            funding_events=request.funding_events,
            funding_mode=request.funding_policy.value,
            initial_cash=request.initial_cash,
            assumptions_version=request.assumptions_version,
        )
        simulation_events: list[JournalEvent] = [
            JournalEvent(
                "SIMULATION",
                report.run_id_sha256,
                request.recorded_events[-1].market_event.observed_at,
                {
                    "simulation_schema_version": 1,
                    "run_id_sha256": report.run_id_sha256,
                    "status": report.status.value,
                    "fill_count": len(report.fills),
                    "model": "DETERMINISTIC_PAPER_PORTFOLIO_V1",
                },
            )
        ]
        for fill in report.fills:
            simulation_events.append(
                JournalEvent(
                    "FILL",
                    fill.fill_key_sha256,
                    fill.observed_at,
                    {
                        "fill_schema_version": 1,
                        "fill_key_sha256": fill.fill_key_sha256,
                        "order_id": str(fill.order_id),
                        "product_id": fill.product_id,
                        "side": fill.side.value,
                        "contracts": fill.contracts,
                        "price": format(fill.price, "f"),
                        "notional": format(fill.notional, "f"),
                        "liquidity_role": fill.liquidity_role.value,
                        "fee_schedule_version": request.product.fee_schedule_version,
                        "fee_rate": format(fill.fee_rate, "f"),
                        "fee": format(fill.fee, "f"),
                        "gross_realized_pnl": format(fill.gross_realized_pnl, "f"),
                    },
                )
            )
        claim = await self._claim(request)
        if claim is None:
            async with self._database.transaction() as session:
                head = await self._repository.get_head(session, request.scope, request.request_id)
            if head is not None and head.status in {"DENIED", "COMPLETE", "INCOMPLETE"}:
                return _result_from_events(
                    request,
                    head.status,
                    await self._events(request),
                    replayed=True,
                )
            raise RuntimeError("paper execution claim is unavailable")
        aborted: dict[str, object] | None = None
        async with self._database.transaction() as session:
            await self._repository.lock_scope(session, request.scope)
            scope_events = await self._repository.load_scope_events(session, request.scope)
            halt = _last_scope_event(scope_events, "RISK_HALT")
            cancellation = next(
                (
                    item
                    for item in reversed(scope_events)
                    if item.event_kind == "ORDER_CANCELLED"
                    and item.payload.get("order_id") == str(request.intent.order_id)
                ),
                None,
            )
            control = cancellation
            reason = "DURABLE_PAPER_ORDER_CANCELLED_BEFORE_FILL"
            if halt is not None and request.risk_intent.risk_effect is RiskEffect.RISK_INCREASE:
                control = halt
                reason = "DURABLE_PAPER_KILL_HALT_BEFORE_FILL"
            if control is not None:
                observed_at = self._now()
                aborted = _control_abort_report(request, control, reason)
                abort_run_id = str(aborted["run_id_sha256"])
                abort_events: list[JournalEvent] = []
                if cancellation is None:
                    abort_events.append(
                        JournalEvent(
                            "ORDER_CANCELLED",
                            f"execution-abort:{request.intent.order_id}",
                            observed_at,
                            {
                                "order_id": str(request.intent.order_id),
                                "reason": reason,
                            },
                        )
                    )
                abort_events.extend(
                    (
                        JournalEvent(
                            "SIMULATION",
                            abort_run_id,
                            observed_at,
                            {
                                "simulation_schema_version": 1,
                                "run_id_sha256": abort_run_id,
                                "status": "INCOMPLETE",
                                "fill_count": 0,
                                "model": "DETERMINISTIC_PAPER_PORTFOLIO_V1",
                                "reason": reason,
                            },
                        ),
                        JournalEvent(
                            "RUN_INCOMPLETE",
                            abort_run_id,
                            observed_at,
                            {"report": aborted, "reasons": [reason]},
                        ),
                    )
                )
                await self._repository.append(
                    session,
                    claim,
                    events=tuple(abort_events),
                    status="INCOMPLETE",
                    recorded_at=observed_at,
                )
            else:
                await self._repository.append(
                    session,
                    claim,
                    events=tuple(simulation_events),
                    status="SIMULATED",
                    recorded_at=self._now(),
                )
        if aborted is not None:
            return _result_from_events(
                request,
                "INCOMPLETE",
                await self._events(request),
                replayed=not intent_receipt.created,
            )
        if crash_point is PaperCrashPoint.AFTER_FILL_PERSISTENCE:
            raise RuntimeError("injected paper crash after fill persistence")
        projection_events: list[JournalEvent] = []
        for position in report.final_positions:
            projection_events.append(
                JournalEvent(
                    "POSITION",
                    f"{report.run_id_sha256}:{position.product_id}",
                    request.recorded_events[-1].market_event.observed_at,
                    {
                        "position_schema_version": 1,
                        "product_id": position.product_id,
                        "contracts": position.contracts,
                        "average_entry_price": format(position.average_entry_price, "f"),
                        "mark_price": format(position.mark_price, "f"),
                        "unrealized_pnl": format(position.unrealized_pnl, "f"),
                    },
                )
            )
        for funding in report.funding:
            projection_events.append(
                JournalEvent(
                    "FUNDING",
                    f"{funding.product_id}:{funding.observed_at.isoformat()}",
                    funding.observed_at,
                    {
                        "funding_schema_version": 1,
                        "product_id": funding.product_id,
                        "position_contracts": funding.position_contracts,
                        "reference_price": format(funding.reference_price, "f"),
                        "rate": format(funding.rate, "f"),
                        "position_value": format(funding.position_value, "f"),
                        "cashflow": format(funding.cashflow, "f"),
                        "schedule_version": funding.schedule_version,
                        "rate_source": funding.rate_source,
                    },
                )
            )
        last_at = max(
            [item.market_event.observed_at for item in request.recorded_events]
            + [item.observed_at for item in request.funding_events]
        )
        projection_events.append(
            JournalEvent(
                "PNL",
                report.run_id_sha256,
                last_at,
                {
                    "pnl_schema_version": 1,
                    "run_id_sha256": report.run_id_sha256,
                    "completeness": report.status.value,
                    "net_pnl": format(report.net_pnl, "f"),
                    "gross_realized_pnl": format(report.gross_realized_pnl, "f"),
                    "unrealized_pnl": format(report.unrealized_pnl, "f"),
                    "total_fees": format(report.total_fees, "f"),
                    "total_funding": format(report.total_funding, "f"),
                    "final_equity": format(report.final_equity, "f"),
                },
            )
        )
        report_payload = versioned_report_payload(
            report,
            request_id=request.request_id,
            account_id=request.scope.account_id,
            mode=request.scope.mode,
            funding_policy=request.funding_policy.value,
        )
        complete = report.status is BacktestRunStatus.COMPLETE
        projection_events.append(
            JournalEvent(
                "RUN_COMPLETE" if complete else "RUN_INCOMPLETE",
                report.run_id_sha256,
                last_at,
                {
                    "report": report_payload,
                    "reasons": list(report.incomplete_reasons),
                },
            )
        )
        claim = await self._claim(request)
        if claim is None:
            raise RuntimeError("paper projection claim is unavailable")
        async with self._database.transaction() as session:
            terminal = await self._repository.append(
                session,
                claim,
                events=tuple(projection_events),
                status="COMPLETE" if complete else "INCOMPLETE",
                recorded_at=self._now(),
            )
        events = await self._events(request)
        return _result_from_events(
            request,
            terminal.head.status,
            events,
            replayed=not intent_receipt.created,
        )


def export_paper_report(result: PaperExecutionResult) -> str:
    """Return the already canonical versioned report owned by a result."""

    if type(result) is not PaperExecutionResult or result.report_json is None:
        raise ValueError("a completed paper report is required")
    return str(result.report_json)


__all__ = [
    "PaperCrashPoint",
    "PaperExecutionRequest",
    "PaperExecutionResult",
    "PaperExecutionService",
    "PaperFundingPolicy",
    "PaperIntentSource",
    "RecordedPaperEvent",
    "export_paper_report",
]
