"""Recorded strategy-to-risk-to-PAPER runner."""

from __future__ import annotations

from dataclasses import dataclass, replace

from hmt_crypto.oms.paper_models import PaperOrderIntent, PaperSide
from hmt_crypto.risk.models import Side
from hmt_crypto.strategy.evaluator_models import StrategyEvaluation
from hmt_crypto.worker.paper_execution import (
    PaperExecutionRequest,
    PaperExecutionResult,
    PaperExecutionService,
    PaperIntentSource,
)


@dataclass(frozen=True, slots=True)
class StrategyPaperSizing:
    """Explicit contracts-per-lot conversion and absolute admission cap."""

    contracts_per_lot: int
    max_contracts: int

    def __post_init__(self) -> None:
        if (
            type(self.contracts_per_lot) is not int
            or type(self.max_contracts) is not int
            or not 1 <= self.contracts_per_lot <= self.max_contracts < 2**63
        ):
            raise ValueError("strategy sizing must use positive bounded exact integers")


class RecordedStrategyRunner:
    """Adapt a broker-neutral strategy result into the mandatory risk/PAPER path."""

    def __init__(self, service: PaperExecutionService) -> None:
        if type(service) is not PaperExecutionService:
            raise ValueError("exact paper execution service required")
        self._service = service

    async def run(
        self,
        request: PaperExecutionRequest,
        evaluation: StrategyEvaluation,
        sizing: StrategyPaperSizing,
    ) -> PaperExecutionResult | None:
        """Size one passed evaluation and delegate every order to risk-gated execution."""

        if (
            type(request) is not PaperExecutionRequest
            or type(evaluation) is not StrategyEvaluation
            or type(sizing) is not StrategyPaperSizing
        ):
            raise ValueError("recorded strategy runner requires exact immutable inputs")
        if not evaluation.passed:
            return None
        if evaluation.direction not in {"BUY", "SELL"}:
            raise ValueError("strategy direction is unsupported")
        contracts = evaluation.lots * sizing.contracts_per_lot
        if (
            type(evaluation.lots) is not int
            or evaluation.lots < 1
            or contracts > sizing.max_contracts
            or contracts >= 2**63
        ):
            raise ValueError("strategy sizing exceeds the explicit contract cap")
        paper_side = PaperSide.BUY if evaluation.direction == "BUY" else PaperSide.SELL
        risk_side = Side.BUY if evaluation.direction == "BUY" else Side.SELL
        original = request.intent
        intent = PaperOrderIntent(
            order_id=original.order_id,
            paper_account_id=original.paper_account_id,
            mode=original.mode,
            product_id=original.product_id,
            side=paper_side,
            contracts=contracts,
            kind=original.kind,
            tick_size=original.tick_size,
            limit_price=original.limit_price,
            stop_price=original.stop_price,
            trail_amount=original.trail_amount,
            trigger_direction=original.trigger_direction,
            reduce_only=original.reduce_only,
            bracket=original.bracket,
        )
        risk_intent = replace(
            request.risk_intent,
            side=risk_side,
            contracts=contracts,
            planned_protection_contracts=(
                contracts if request.risk_intent.planned_protection_contracts is not None else None
            ),
            intent_owner="strategy-v2-recorded-runner",
            intent_key=f"strategy-v2:{request.request_id}",
        )
        owned = replace(
            request,
            source=PaperIntentSource.STRATEGY,
            intent=intent,
            risk_intent=risk_intent,
            signal=evaluation.to_dict(),
        )
        return await self._service.execute(owned)


__all__ = ["RecordedStrategyRunner", "StrategyPaperSizing"]
