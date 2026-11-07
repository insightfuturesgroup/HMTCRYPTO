"""Shared deterministic maker/taker fee and funding accounting for PAPER."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from hmt_crypto.backtest.engine import run_backtest
from hmt_crypto.backtest.models import (
    BacktestConfig,
    BacktestEvent,
    BacktestFundingEvent,
    BacktestFundingSchedule,
    BacktestMarketEvent,
    BacktestOrderDecision,
    BacktestProductAssumption,
    LiquidityRole,
)
from hmt_crypto.backtest.reports import BacktestReport
from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_models import (
    PaperCandle,
    PaperModelConfig,
    PaperOrderIntent,
    PaperTopOfBook,
)

FundingMode = Literal[
    "REQUIRE_RECORDED_MARKS",
    "EXPLICIT_SYNTHETIC_ZERO_FUNDING_NOT_VENUE_DATA",
]


@dataclass(frozen=True, slots=True)
class PaperReplayFrame:
    """One recorded market frame admitted to the shared backtest engine."""

    market_event: PaperTopOfBook | PaperCandle
    mark_price: Decimal


def _backtest_intent(intent: PaperOrderIntent) -> PaperOrderIntent:
    return PaperOrderIntent(
        order_id=intent.order_id,
        paper_account_id=intent.paper_account_id,
        mode=OmsExecutionMode.BACKTEST,
        product_id=intent.product_id,
        side=intent.side,
        contracts=intent.contracts,
        kind=intent.kind,
        tick_size=intent.tick_size,
        limit_price=intent.limit_price,
        stop_price=intent.stop_price,
        trail_amount=intent.trail_amount,
        trigger_direction=intent.trigger_direction,
        reduce_only=intent.reduce_only,
        bracket=intent.bracket,
    )


def _synthetic_funding(
    frames: tuple[PaperReplayFrame, ...],
    schedule: BacktestFundingSchedule | None,
    existing: tuple[BacktestFundingEvent, ...],
) -> tuple[BacktestFundingEvent, ...]:
    if schedule is None:
        return existing
    known = {item.observed_at for item in existing}
    start = frames[0].market_event.observed_at
    end = frames[-1].market_event.observed_at
    generated = list(existing)
    for observed_at in schedule.realization_times:
        if observed_at in known or observed_at < start or observed_at > end:
            continue
        prior_marks = [
            item.mark_price for item in frames if item.market_event.observed_at <= observed_at
        ]
        if not prior_marks:
            raise ValueError("synthetic funding lacks a prior explicit mark")
        generated.append(
            BacktestFundingEvent(
                sequence=1,
                observed_at=observed_at,
                product_id=schedule.product_id,
                rate=Decimal(0),
                reference_price=prior_marks[-1],
                schedule_version=schedule.version,
                rate_source="EXPLICIT_SYNTHETIC_ZERO_FUNDING_NOT_VENUE_DATA",
            )
        )
    return tuple(generated)


def run_paper_accounting(
    *,
    intent: PaperOrderIntent,
    model_config: PaperModelConfig,
    frames: tuple[PaperReplayFrame, ...],
    product: BacktestProductAssumption,
    liquidity_role: LiquidityRole,
    funding_schedule: BacktestFundingSchedule | None,
    funding_events: tuple[BacktestFundingEvent, ...],
    funding_mode: FundingMode,
    initial_cash: Decimal,
    assumptions_version: str,
) -> BacktestReport:
    """Run the shared exact ledger with an explicit versioned fee/funding policy."""

    if len(frames) < 2:
        raise ValueError("at least two recorded market frames are required")
    decision = BacktestOrderDecision(
        intent=_backtest_intent(intent),
        model_config=model_config,
        liquidity_role=liquidity_role,
        stop_loss_liquidity_role=None,
        take_profit_liquidity_role=None,
    )
    owned_funding = funding_events
    if funding_mode == "EXPLICIT_SYNTHETIC_ZERO_FUNDING_NOT_VENUE_DATA":
        owned_funding = _synthetic_funding(frames, funding_schedule, funding_events)
    staged: list[tuple[datetime, int, BacktestFundingEvent | tuple[int, PaperReplayFrame]]] = []
    for index, frame in enumerate(frames):
        staged.append((frame.market_event.observed_at, 1, (index, frame)))
    for event in owned_funding:
        staged.append((event.observed_at, 0, event))
    staged.sort(key=lambda item: (item[0], item[1]))
    if any(left[0] == right[0] for left, right in zip(staged, staged[1:], strict=False)):
        raise ValueError("market and funding timestamps must be globally unambiguous")
    events: list[BacktestEvent] = []
    for sequence, (_, _, value) in enumerate(staged, start=1):
        if isinstance(value, BacktestFundingEvent):
            events.append(
                BacktestFundingEvent(
                    sequence=sequence,
                    observed_at=value.observed_at,
                    product_id=value.product_id,
                    rate=value.rate,
                    reference_price=value.reference_price,
                    schedule_version=value.schedule_version,
                    rate_source=value.rate_source,
                )
            )
        else:
            assert type(value) is tuple
            frame_index, frame = value
            events.append(
                BacktestMarketEvent(
                    sequence=sequence,
                    market_event=frame.market_event,
                    mark_price=frame.mark_price,
                    decisions=(decision,) if frame_index == 0 else (),
                    long_liquidation_price=None,
                    short_liquidation_price=None,
                )
            )
    config = BacktestConfig(
        paper_account_id=intent.paper_account_id,
        initial_cash=initial_cash,
        products=(product,),
        funding_schedules=() if funding_schedule is None else (funding_schedule,),
        strict_funding=False,
        assumptions_version=assumptions_version,
        max_events=len(events),
    )
    return run_backtest(config, tuple(events))


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def versioned_report_payload(
    report: BacktestReport,
    *,
    request_id: str,
    account_id: UUID,
    mode: str,
    funding_policy: str,
) -> dict[str, object]:
    """Create an export-safe report that makes no profitability claim."""

    return {
        "report_schema_version": 1,
        "report_kind": "OFFLINE_PAPER_BACKTEST_EXECUTION",
        "profitability_claim": False,
        "request_id": request_id,
        "account_id": str(account_id),
        "mode": mode,
        "status": report.status.value,
        "run_id_sha256": report.run_id_sha256,
        "config_sha256": report.config_sha256,
        "data_sha256": report.data_sha256,
        "funding_policy": funding_policy,
        "initial_cash": _decimal(report.initial_cash),
        "final_equity": _decimal(report.final_equity),
        "net_pnl": _decimal(report.net_pnl),
        "gross_realized_pnl": _decimal(report.gross_realized_pnl),
        "realized_net_pnl": _decimal(report.realized_net_pnl),
        "unrealized_pnl": _decimal(report.unrealized_pnl),
        "total_fees": _decimal(report.total_fees),
        "total_funding": _decimal(report.total_funding),
        "incomplete_reasons": list(report.incomplete_reasons),
        "assumptions": list(report.assumptions),
    }


def encode_versioned_report(payload: dict[str, object]) -> str:
    """Encode the bounded export deterministically."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = [
    "PaperReplayFrame",
    "encode_versioned_report",
    "run_paper_accounting",
    "versioned_report_payload",
]
