"""End-to-end offline risk, simulation, accounting, and replay behavior."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
import pytest_asyncio
from schema_test_support import account
from schema_test_support import schema_db as schema_db
from sqlalchemy import delete

from hmt_crypto.backtest.models import BacktestFundingEvent, BacktestFundingSchedule
from hmt_crypto.oms.paper_models import PaperTopOfBook
from hmt_crypto.persistence.paper_execution_models import (
    PaperExecutionEvent,
    PaperExecutionHead,
)
from hmt_crypto.strategy.evaluator_models import StrategyDiagnostics, StrategyEvaluation
from hmt_crypto.worker.paper_execution import (
    PaperCrashPoint,
    PaperExecutionRequest,
    PaperExecutionService,
    PaperFundingPolicy,
    export_paper_report,
)
from hmt_crypto.worker.paper_repository import JournalEvent, PaperJournalRepository
from hmt_crypto.worker.paper_runner import RecordedStrategyRunner, StrategyPaperSizing
from tests.unit.test_paper_worker_contract import _request_values
from tests.unit.test_risk_manager import policy

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 6, 8, tzinfo=UTC)


class _StepClock:
    def __init__(self) -> None:
        self._step = 0

    def __call__(self) -> datetime:
        self._step += 1
        return NOW + timedelta(minutes=1, microseconds=self._step)


@pytest_asyncio.fixture
async def paper_db(schema_db):
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(PaperExecutionHead.__table__.create, checkfirst=True)
        await connection.run_sync(PaperExecutionEvent.__table__.create, checkfirst=True)
    try:
        yield schema_db
    finally:
        async with schema_db.engine.begin() as connection:
            await connection.execute(delete(PaperExecutionEvent))
            await connection.execute(delete(PaperExecutionHead))
            await connection.run_sync(PaperExecutionEvent.__table__.drop)
            await connection.run_sync(PaperExecutionHead.__table__.drop)


async def _seed_account(database: object, request: PaperExecutionRequest) -> None:
    owner = account(
        id=request.scope.account_id,
        region=request.scope.region,
        environment=request.scope.environment,
        mode=request.scope.mode,
        profile_name=f"worker-{request.scope.mode.lower()}",
        created_at=NOW,
        updated_at=NOW,
    )
    async with database.transaction() as session:  # type: ignore[attr-defined]
        session.add(owner)


def _request(**changes: object) -> PaperExecutionRequest:
    values = _request_values()
    values.update(changes)
    return PaperExecutionRequest(**values)  # type: ignore[arg-type]


async def _events(database: object, request: PaperExecutionRequest):
    async with database.transaction() as session:  # type: ignore[attr-defined]
        return await PaperJournalRepository().load_events(
            session, request.scope, request.request_id
        )


async def _persist_kill_halt(database: object, request: PaperExecutionRequest) -> None:
    repository = PaperJournalRepository()
    observed_at = NOW + timedelta(seconds=30)
    request_id = f"kill-halt-{request.request_id}"
    async with database.transaction() as session:  # type: ignore[attr-defined]
        await repository.ensure_intent(
            session,
            request.scope,
            request_id=request_id,
            intent_payload={"paper_control_schema_version": 1},
            occurred_at=observed_at,
        )
    async with database.transaction() as session:  # type: ignore[attr-defined]
        claim = await repository.claim(
            session,
            request.scope,
            request_id,
            owner_id=UUID("00000000-0000-0000-0000-0000000000f1"),
            now=observed_at + timedelta(microseconds=1),
            duration=timedelta(minutes=1),
        )
        assert claim is not None
        await repository.append(
            session,
            claim,
            events=(
                JournalEvent(
                    "RISK_HALT",
                    "global-kill",
                    observed_at,
                    {"cause": "GLOBAL_KILL", "operation_key": "test-kill"},
                ),
            ),
            status="COMPLETE",
            recorded_at=observed_at + timedelta(microseconds=1),
        )


@pytest.mark.asyncio
async def test_denial_persists_risk_evidence_without_order_attempt_or_fill(paper_db) -> None:
    """Bypassing denial or creating execution side effects on denial is a safety bug."""

    request = _request(risk_policy=policy(max_position_contracts=1))
    await _seed_account(paper_db, request)
    service = PaperExecutionService(
        paper_db,
        worker_id=UUID("00000000-0000-0000-0000-0000000000a1"),
        clock=_StepClock(),
    )

    result = await service.execute(request)
    events = await _events(paper_db, request)

    assert result.status == "DENIED"
    assert [item.event_kind for item in events] == ["INTENT", "RISK_DENIED"]
    assert not {"COMMAND", "ATTEMPT", "FILL"}.intersection(item.event_kind for item in events)


@pytest.mark.asyncio
async def test_durable_kill_halt_denies_new_risk_increase_without_execution_side_effects(
    paper_db,
) -> None:
    """The journal kill halt must override a stale caller snapshot before preparation."""

    request = _request(request_id="halted-before-paper-intent")
    await _seed_account(paper_db, request)
    await _persist_kill_halt(paper_db, request)

    result = await PaperExecutionService(
        paper_db,
        worker_id=UUID("00000000-0000-0000-0000-0000000000a1"),
        clock=_StepClock(),
    ).execute(request)
    events = await _events(paper_db, request)

    assert result.status == "DENIED"
    assert [item.event_kind for item in events] == ["INTENT", "RISK_DENIED"]
    assert events[-1].payload["reason_code"] == "DURABLE_PAPER_KILL_HALT"
    assert not {"COMMAND", "ATTEMPT", "FILL"}.intersection(item.event_kind for item in events)


@pytest.mark.asyncio
async def test_halt_after_preparation_prevents_late_simulated_fill(paper_db) -> None:
    """A fill may not appear after the shared journal has committed the kill halt."""

    request = _request(request_id="halted-after-preparation")
    await _seed_account(paper_db, request)
    clock = _StepClock()
    service = PaperExecutionService(
        paper_db,
        worker_id=UUID("00000000-0000-0000-0000-0000000000a1"),
        clock=clock,
    )
    with pytest.raises(RuntimeError, match="injected paper crash"):
        await service.execute(request, crash_point=PaperCrashPoint.AFTER_PREPARATION)
    await _persist_kill_halt(paper_db, request)

    result = await PaperExecutionService(
        paper_db,
        worker_id=UUID("00000000-0000-0000-0000-0000000000a1"),
        clock=clock,
    ).execute(request)
    events = await _events(paper_db, request)

    assert result.status == "INCOMPLETE"
    assert not any(item.event_kind == "FILL" for item in events)
    incomplete = next(item for item in events if item.event_kind == "RUN_INCOMPLETE")
    assert incomplete.payload["reasons"] == ["DURABLE_PAPER_KILL_HALT_BEFORE_FILL"]


@pytest.mark.asyncio
async def test_approval_prepares_before_simulation_and_projects_exact_accounting(paper_db) -> None:
    """Skipping preparation ordering or exact fee/position/PnL projection must fail."""

    request = _request()
    await _seed_account(paper_db, request)
    service = PaperExecutionService(
        paper_db,
        worker_id=UUID("00000000-0000-0000-0000-0000000000a1"),
        clock=_StepClock(),
    )

    result = await service.execute(request)
    events = await _events(paper_db, request)
    kinds = [item.event_kind for item in events]
    fill = next(item.payload for item in events if item.event_kind == "FILL")
    position = next(item.payload for item in events if item.event_kind == "POSITION")
    pnl = next(item.payload for item in events if item.event_kind == "PNL")

    assert result.status == "COMPLETE"
    assert kinds.index("RISK_APPROVED") < kinds.index("RESERVATION")
    assert kinds.index("RESERVATION") < kinds.index("COMMAND")
    assert kinds.index("COMMAND") < kinds.index("ATTEMPT") < kinds.index("SIMULATION")
    assert Decimal(fill["fee"]) == Decimal("0.202")
    assert Decimal(fill["notional"]) == Decimal("202")
    assert position["contracts"] == 2
    assert position["mark_price"] == "101"
    assert Decimal(pnl["total_fees"]) == Decimal("0.202")
    assert Decimal(pnl["net_pnl"]) == Decimal("-0.202")
    exported = export_paper_report(result)
    assert '"profitability_claim":false' in exported
    assert '"report_schema_version":1' in exported


@pytest.mark.asyncio
async def test_duplicate_replay_and_restart_do_not_duplicate_fill(paper_db) -> None:
    """Re-executing a completed request must return evidence without a second fill."""

    request = _request()
    await _seed_account(paper_db, request)
    worker = UUID("00000000-0000-0000-0000-0000000000a1")
    clock = _StepClock()
    first_service = PaperExecutionService(paper_db, worker_id=worker, clock=clock)
    first = await first_service.execute(request)
    before = await _events(paper_db, request)
    restarted = PaperExecutionService(paper_db, worker_id=worker, clock=clock)

    replay = await restarted.execute(request)
    after = await _events(paper_db, request)

    assert first.status == replay.status == "COMPLETE"
    assert replay.replayed is True
    assert len(before) == len(after)
    assert sum(item.event_kind == "FILL" for item in after) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "crash_point",
    (PaperCrashPoint.AFTER_INTENT, PaperCrashPoint.AFTER_FILL_PERSISTENCE),
)
async def test_crash_checkpoint_restarts_without_duplicate_economic_effect(
    paper_db, crash_point
) -> None:
    """A process death at either durable boundary must replay exactly once."""

    request = _request(request_id=f"crash-{crash_point.value.lower()}")
    await _seed_account(paper_db, request)
    worker = UUID("00000000-0000-0000-0000-0000000000a1")
    clock = _StepClock()
    service = PaperExecutionService(paper_db, worker_id=worker, clock=clock)

    with pytest.raises(RuntimeError, match="injected paper crash"):
        await service.execute(request, crash_point=crash_point)
    restarted = PaperExecutionService(paper_db, worker_id=worker, clock=clock)
    result = await restarted.execute(request)
    events = await _events(paper_db, request)

    assert result.status == "COMPLETE"
    assert sum(item.event_kind == "INTENT" for item in events) == 1
    assert sum(item.event_kind == "FILL" for item in events) == 1
    assert sum(item.event_kind == "PNL" for item in events) == 1


def _perpetual_request(*, synthetic_zero: bool) -> PaperExecutionRequest:
    values = _request_values()
    product = values["product"]
    values["product"] = replace(product, is_perpetual=True)  # type: ignore[arg-type]
    events = values["recorded_events"]
    assert isinstance(events, tuple)
    first_at = events[0].market_event.observed_at
    second = events[-1]
    second_at = second.market_event.observed_at
    third = type(second)(
        sequence=3,
        market_event=PaperTopOfBook(
            stream_id="recorded-btc",
            product_id=1101,
            event_index=3,
            observed_at=second_at + timedelta(seconds=1),
            bid_price=Decimal("100"),
            ask_price=Decimal("101"),
            bid_contracts=10,
            ask_contracts=10,
            trigger_reference=Decimal("101"),
        ),
        mark_price=Decimal("101"),
    )
    values["recorded_events"] = (*events, third)
    realization = second_at + timedelta(microseconds=500000)
    values["funding_schedule"] = BacktestFundingSchedule(
        product_id=1101,
        version="delta-funding-v1",
        source_sha256="8" * 64,
        coverage_start=first_at,
        coverage_end=second_at + timedelta(seconds=1),
        realization_times=(realization,),
    )
    values["funding_policy"] = (
        PaperFundingPolicy.EXPLICIT_SYNTHETIC_ZERO_FUNDING_NOT_VENUE_DATA
        if synthetic_zero
        else PaperFundingPolicy.REQUIRE_RECORDED_MARKS
    )
    return PaperExecutionRequest(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_missing_required_funding_mark_is_persisted_incomplete(paper_db) -> None:
    """A held perpetual without a rate must never be silently treated as zero."""

    request = _perpetual_request(synthetic_zero=False)
    await _seed_account(paper_db, request)
    result = await PaperExecutionService(
        paper_db,
        worker_id=UUID("00000000-0000-0000-0000-0000000000a1"),
        clock=_StepClock(),
    ).execute(request)
    events = await _events(paper_db, request)

    assert result.status == "INCOMPLETE"
    incomplete = next(item.payload for item in events if item.event_kind == "RUN_INCOMPLETE")
    assert "missing funding rate" in " ".join(incomplete["reasons"])
    assert not any(item.event_kind == "FUNDING" for item in events)


@pytest.mark.asyncio
async def test_explicit_synthetic_zero_funding_is_conspicuous_and_complete(paper_db) -> None:
    """Zero funding is legal only under the explicitly named synthetic policy."""

    request = _perpetual_request(synthetic_zero=True)
    await _seed_account(paper_db, request)
    result = await PaperExecutionService(
        paper_db,
        worker_id=UUID("00000000-0000-0000-0000-0000000000a1"),
        clock=_StepClock(),
    ).execute(request)
    events = await _events(paper_db, request)
    funding = next(item.payload for item in events if item.event_kind == "FUNDING")

    assert result.status == "COMPLETE"
    assert funding["rate"] == "0"
    assert funding["cashflow"] == "0"
    assert funding["rate_source"] == ("EXPLICIT_SYNTHETIC_ZERO_FUNDING_NOT_VENUE_DATA")


@pytest.mark.asyncio
async def test_recorded_funding_cashflow_uses_shared_backtest_assumptions(paper_db) -> None:
    """Funding sign or notional drift between PAPER and BACKTEST must be caught."""

    request = _perpetual_request(synthetic_zero=False)
    realization = request.funding_schedule.realization_times[0]  # type: ignore[union-attr]
    request = replace(
        request,
        funding_events=(
            BacktestFundingEvent(
                sequence=1,
                observed_at=realization,
                product_id=1101,
                rate=Decimal("0.01"),
                reference_price=Decimal("100"),
                schedule_version="delta-funding-v1",
                rate_source="recorded-delta-fixture",
            ),
        ),
    )
    await _seed_account(paper_db, request)
    result = await PaperExecutionService(
        paper_db,
        worker_id=UUID("00000000-0000-0000-0000-0000000000a1"),
        clock=_StepClock(),
    ).execute(request)
    events = await _events(paper_db, request)
    funding = next(item.payload for item in events if item.event_kind == "FUNDING")
    pnl = next(item.payload for item in events if item.event_kind == "PNL")

    assert result.status == "COMPLETE"
    assert funding["position_contracts"] == 2
    assert Decimal(funding["cashflow"]) == Decimal("-2")
    assert Decimal(pnl["total_funding"]) == Decimal("-2")
    assert Decimal(pnl["net_pnl"]) == Decimal("-2.202")


@pytest.mark.asyncio
async def test_recorded_strategy_output_is_sized_risk_checked_and_paper_executed(
    paper_db,
) -> None:
    """A strategy decision must not bypass explicit sizing or mandatory risk evaluation."""

    request = _request(request_id="strategy-recorded-1")
    await _seed_account(paper_db, request)
    evaluation = StrategyEvaluation(
        passed=True,
        status="matched",
        reason="recorded fixture matched",
        direction="BUY",
        matched_engine_id="engine-1",
        matched_accelerator_id=None,
        lots=1,
        atr_multiplier=1,
        sl_atr_multiplier=1,
        target_atr_multiplier=2,
        atr_multiplier_source="engine",
        sl_multiplier_source="engine",
        target_multiplier_source="engine",
        stock_target_override_symbol=None,
        diagnostics=StrategyDiagnostics(
            engine_rules=(),
            failed_rule=None,
            accelerator_condition_mode=None,
            accelerator_group_passes=(),
            accelerator_evaluations=(),
            final_action=None,
            trace_statuses=(("engine_selection", "passed"),),
        ),
    )
    service = PaperExecutionService(
        paper_db,
        worker_id=UUID("00000000-0000-0000-0000-0000000000a1"),
        clock=_StepClock(),
    )

    result = await RecordedStrategyRunner(service).run(
        request,
        evaluation,
        StrategyPaperSizing(contracts_per_lot=2, max_contracts=4),
    )
    events = await _events(paper_db, request)
    signal = next(item.payload for item in events if item.event_kind == "SIGNAL")
    fill = next(item.payload for item in events if item.event_kind == "FILL")

    assert result.status == "COMPLETE"
    assert signal["signal"]["matched_engine_id"] == "engine-1"
    assert fill["contracts"] == 2
    assert '"profitability_claim":false' in export_paper_report(result)
