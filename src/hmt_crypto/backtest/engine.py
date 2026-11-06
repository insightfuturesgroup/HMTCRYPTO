"""Pure event-driven replay over the accepted PAPER portfolio simulator."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from hmt_crypto.backtest.arithmetic import arithmetic_context
from hmt_crypto.backtest.budget import ReplayBudget
from hmt_crypto.backtest.errors import (
    BacktestDataError,
    BacktestIncompleteError,
    BacktestInputError,
)
from hmt_crypto.backtest.identity import identity_sha256
from hmt_crypto.backtest.ledger import BacktestLedger
from hmt_crypto.backtest.models import (
    BacktestConfig,
    BacktestEvent,
    BacktestFundingEvent,
    BacktestMarketEvent,
    BacktestOrderDecision,
    BacktestProductAssumption,
    BacktestRunStatus,
    LiquidityRole,
)
from hmt_crypto.backtest.reports import (
    BacktestFill,
    BacktestFundingRecord,
    BacktestReport,
    BacktestTrade,
)
from hmt_crypto.oms.models import OmsExecutionMode
from hmt_crypto.oms.paper_models import (
    PaperBracketRole,
    PaperFillProposal,
    PaperSimulationError,
)
from hmt_crypto.oms.paper_portfolio import (
    create_paper_simulation,
    evaluate_simulation_event,
    simulation_position,
    submit_simulation_order,
)
from hmt_crypto.oms.paper_portfolio_models import PaperSimulationState

_ZERO = Decimal(0)
_PROTOCOL = "hmt-crypto-backtest-v1"


def _copy_config(value: object) -> BacktestConfig:
    if type(value) is not BacktestConfig:
        raise BacktestInputError("config must be an exact BacktestConfig") from None
    return BacktestConfig(
        value.paper_account_id,
        value.initial_cash,
        value.products,
        value.funding_schedules,
        value.strict_funding,
        value.assumptions_version,
        value.max_events,
        value.limits,
    )


def _copy_events(value: object, config: BacktestConfig) -> tuple[BacktestEvent, ...]:
    if type(value) is not tuple or not 1 <= len(value) <= config.max_events:
        raise BacktestDataError("events must be a non-empty bounded immutable tuple") from None
    copied: list[BacktestEvent] = []
    total_decisions = 0
    input_work = len(config.products) + sum(
        len(item.realization_times) for item in config.funding_schedules
    )
    for item in value:
        input_work += 1
        if type(item) is BacktestMarketEvent:
            if len(item.decisions) > config.limits.max_decisions_per_event:
                raise BacktestDataError("decisions per event exceed the configured limit") from None
            total_decisions += len(item.decisions)
            if total_decisions > config.limits.max_total_decisions:
                raise BacktestDataError("total decision limit is exceeded") from None
            input_work += len(item.decisions)
        if input_work > config.limits.max_work_units:
            raise BacktestDataError("aggregate work budget is exceeded") from None
        if type(item) is BacktestMarketEvent:
            copied.append(
                BacktestMarketEvent(
                    item.sequence,
                    item.market_event,
                    item.mark_price,
                    item.decisions,
                    item.long_liquidation_price,
                    item.short_liquidation_price,
                )
            )
        elif type(item) is BacktestFundingEvent:
            copied.append(
                BacktestFundingEvent(
                    item.sequence,
                    item.observed_at,
                    item.product_id,
                    item.rate,
                    item.reference_price,
                    item.schedule_version,
                    item.rate_source,
                )
            )
        else:
            raise BacktestDataError(
                "event subclasses and unknown event types are rejected"
            ) from None
    return tuple(copied)


def _validate_events(
    config: BacktestConfig,
    events: tuple[BacktestEvent, ...],
) -> None:
    products = {item.product_id: item for item in config.products}
    schedules = {item.product_id: item for item in config.funding_schedules}
    schedule_times = {
        item.product_id: frozenset(item.realization_times) for item in config.funding_schedules
    }
    previous_at: datetime | None = None
    product_previous_at: dict[int, datetime] = {}
    market_indices: dict[int, int] = {}
    order_ids: set[UUID] = set()
    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence != expected_sequence:
            raise BacktestDataError(
                "global event sequence must start at one without gaps"
            ) from None
        if previous_at is not None and event.observed_at < previous_at:
            raise BacktestDataError("global event timestamps must be nondecreasing") from None
        previous_at = event.observed_at
        if event.product_id not in products:
            raise BacktestDataError("event references a product absent from assumptions") from None
        product_at = product_previous_at.get(event.product_id)
        if product_at is not None and event.observed_at <= product_at:
            raise BacktestDataError(
                "per-product event timestamps must be strictly increasing"
            ) from None
        product_previous_at[event.product_id] = event.observed_at
        if isinstance(event, BacktestMarketEvent):
            prior = market_indices.get(event.product_id, 0)
            if event.market_event.event_index != prior + 1:
                raise BacktestDataError("per-product market index must start at one without gaps")
            market_indices[event.product_id] = event.market_event.event_index
            for decision in event.decisions:
                intent = decision.intent
                if (
                    intent.paper_account_id != config.paper_account_id
                    or intent.mode is not OmsExecutionMode.BACKTEST
                    or intent.product_id != event.product_id
                    or intent.order_id in order_ids
                ):
                    raise BacktestDataError(
                        "decision account, mode, product, or order identity is inconsistent"
                    ) from None
                order_ids.add(intent.order_id)
        else:
            product = products[event.product_id]
            schedule = schedules.get(event.product_id)
            if (
                not product.is_perpetual
                or schedule is None
                or event.schedule_version != schedule.version
                or event.observed_at not in schedule_times[event.product_id]
            ):
                raise BacktestDataError(
                    "funding event lacks matching product-specific schedule evidence"
                ) from None


def _role_maps(
    state: PaperSimulationState,
    parent_roles: dict[UUID, tuple[LiquidityRole | None, LiquidityRole | None]],
) -> dict[UUID, LiquidityRole]:
    child_roles: dict[UUID, LiquidityRole] = {}
    for lifecycle in state.brackets:
        roles = parent_roles.get(lifecycle.parent_order_id)
        if roles is None:
            raise BacktestDataError("bracket child is missing explicit fee-role assumptions")
        for proposal, child_state in zip(
            lifecycle.current_children, lifecycle.child_states, strict=True
        ):
            role = roles[0] if proposal.role is PaperBracketRole.STOP_LOSS else roles[1]
            if role is None:
                raise BacktestDataError("bracket leg is missing an explicit fee role") from None
            child_roles[child_state.intent.order_id] = role
    return child_roles


def _fee_for_fill(
    fill: PaperFillProposal,
    role: LiquidityRole,
    product: BacktestProductAssumption,
) -> tuple[Decimal, Decimal, Decimal]:
    rate = product.maker_fee_rate if role is LiquidityRole.MAKER else product.taker_fee_rate
    with arithmetic_context():
        notional = fill.price * Decimal(fill.contracts) * product.contract_value
        fee = notional * rate
    return notional, rate, fee


def _guard_reason(event: BacktestMarketEvent, contracts: int) -> str | None:
    if (
        contracts > 0
        and event.long_liquidation_price is not None
        and event.mark_price <= event.long_liquidation_price
    ):
        return (
            f"product {event.product_id} long mark {event.mark_price} breached supplied "
            f"threshold {event.long_liquidation_price}; no liquidation fill was invented"
        )
    if (
        contracts < 0
        and event.short_liquidation_price is not None
        and event.mark_price >= event.short_liquidation_price
    ):
        return (
            f"product {event.product_id} short mark {event.mark_price} breached supplied "
            f"threshold {event.short_liquidation_price}; no liquidation fill was invented"
        )
    return None


def _funding_cashflow(
    contracts: int,
    reference_price: Decimal,
    rate: Decimal,
    product: BacktestProductAssumption,
) -> tuple[Decimal, Decimal]:
    with arithmetic_context():
        position_value = Decimal(abs(contracts)) * reference_price * product.contract_value
        direction = Decimal(1 if contracts > 0 else -1 if contracts < 0 else 0)
        cashflow = -(direction * position_value * rate)
    return position_value, cashflow


def _funding_gaps(
    config: BacktestConfig,
    ledger: BacktestLedger,
    seen: set[tuple[int, datetime]],
    run_end: datetime,
    budget: ReplayBudget,
) -> tuple[str, ...]:
    schedules = {item.product_id: item for item in config.funding_schedules}
    reasons: list[str] = []
    for product in config.products:
        if not product.is_perpetual:
            continue
        budget.consume_work(ledger.position_history_size)
        held_interval = ledger.held_interval(product.product_id, run_end)
        if held_interval is None:
            continue
        first_held, last_held = held_interval
        schedule = schedules.get(product.product_id)
        if schedule is None:
            budget.consume_output(1)
            reasons.append(
                f"product {product.product_id}: missing funding schedule for held perpetual"
            )
            continue
        if schedule.coverage_start > first_held or schedule.coverage_end < last_held:
            budget.consume_output(1)
            reasons.append(
                f"product {product.product_id}: funding schedule does not cover held run interval"
            )
        budget.consume_work(ledger.position_history_size + len(schedule.realization_times))
        for realization, contracts in ledger.positions_at(
            product.product_id, schedule.realization_times
        ):
            if (
                first_held <= realization <= last_held
                and contracts != 0
                and (product.product_id, realization) not in seen
            ):
                budget.consume_output(1)
                reasons.append(
                    f"product {product.product_id}: missing funding rate at "
                    f"{realization.isoformat()}"
                )
    return tuple(reasons)


def _drawdown(initial_cash: Decimal, equities: tuple[Decimal, ...]) -> tuple[Decimal, Decimal]:
    peak = initial_cash
    maximum = _ZERO
    maximum_fraction = _ZERO
    with arithmetic_context():
        for equity in equities:
            peak = max(peak, equity)
            drawdown = peak - equity
            fraction = drawdown / peak if peak > 0 else _ZERO
            maximum = max(maximum, drawdown)
            maximum_fraction = max(maximum_fraction, fraction)
    return maximum, maximum_fraction


def _assumptions(config: BacktestConfig) -> tuple[str, ...]:
    base = (
        f"assumptions_version={config.assumptions_version}",
        "decisions are submitted after their source market event",
        "linear notional = abs(contracts) * contract_value * supplied price",
        "positive funding rates debit longs and credit shorts at the explicit snapshot",
        "maker/taker commission is signed: negative maker commission is a cash rebate",
        "gross_realized_pnl excludes commission and funding; realized_net_pnl includes both",
        "PAPER slippage, latency, partial-fill, stop, trailing, and bracket semantics are reused",
        "liquidation guard halts without synthesizing venue liquidation fills",
    )
    products = tuple(
        f"product {item.product_id}: contract_value={item.contract_value}; "
        f"perpetual={item.is_perpetual}; maker_rate={item.maker_fee_rate}; "
        f"taker_rate={item.taker_fee_rate}; fees={item.fee_schedule_version}; "
        f"snapshot_sha256={item.product_snapshot_sha256}"
        for item in config.products
    )
    schedules = tuple(
        f"product {item.product_id}: funding_schedule={item.version}; "
        f"coverage={item.coverage_start.isoformat()}..{item.coverage_end.isoformat()}; "
        f"realizations={len(item.realization_times)}; source_sha256={item.source_sha256}"
        for item in config.funding_schedules
    )
    return base + products + schedules


def _record_fill(
    fill: PaperFillProposal,
    role: LiquidityRole,
    product: BacktestProductAssumption,
    ledger: BacktestLedger,
) -> tuple[BacktestFill, BacktestTrade | None]:
    notional, rate, fee = _fee_for_fill(fill, role, product)
    accounting = ledger.apply_fill(
        product_id=fill.product_id,
        side=fill.side,
        contracts=fill.contracts,
        price=fill.price,
        fee=fee,
        observed_at=fill.observed_at,
    )
    recorded = BacktestFill(
        fill.fill_key_sha256,
        fill.order_id,
        fill.product_id,
        fill.side,
        fill.contracts,
        fill.price,
        notional,
        role,
        rate,
        fee,
        accounting.gross_realized_pnl,
        fill.observed_at,
    )
    if accounting.closed_contracts == 0:
        return recorded, None
    with arithmetic_context():
        exit_fee = fee * Decimal(accounting.closed_contracts) / Decimal(fill.contracts)
    trade = BacktestTrade(
        fill.order_id,
        fill.product_id,
        accounting.closed_contracts,
        accounting.entry_price,
        fill.price,
        accounting.gross_realized_pnl,
        exit_fee,
        fill.observed_at,
    )
    return recorded, trade


def _run_backtest(config: BacktestConfig, events: tuple[BacktestEvent, ...]) -> BacktestReport:
    owned_config = _copy_config(config)
    owned_events = _copy_events(events, owned_config)
    _validate_events(owned_config, owned_events)
    config_digest = identity_sha256(f"{_PROTOCOL}:config", owned_config)
    data_digest = identity_sha256(f"{_PROTOCOL}:data", owned_events)
    run_digest = identity_sha256(f"{_PROTOCOL}:run", (config_digest, data_digest))
    products = {item.product_id: item for item in owned_config.products}
    schedules = {item.product_id: item for item in owned_config.funding_schedules}
    ledger = BacktestLedger(owned_config.initial_cash, products)
    budget = ReplayBudget(
        owned_config.limits.max_work_units,
        owned_config.limits.max_output_records,
    )
    budget.consume_work(
        len(owned_config.products)
        + sum(len(item.realization_times) for item in owned_config.funding_schedules)
        + len(owned_events)
        + sum(len(item.decisions) for item in owned_events if isinstance(item, BacktestMarketEvent))
    )
    state = create_paper_simulation()
    direct_roles: dict[UUID, LiquidityRole] = {}
    parent_roles: dict[UUID, tuple[LiquidityRole | None, LiquidityRole | None]] = {}
    fills: list[BacktestFill] = []
    trades: list[BacktestTrade] = []
    funding: list[BacktestFundingRecord] = []
    equity = []
    seen_funding: set[tuple[int, datetime]] = set()
    halt_reason: str | None = None
    processed_end = owned_events[0].observed_at
    for event in owned_events:
        processed_end = event.observed_at
        product = products[event.product_id]
        if isinstance(event, BacktestMarketEvent):
            evaluation_scale = len(state.orders) + len(state.brackets) + 1
            budget.consume_work(evaluation_scale * evaluation_scale)
            ledger.update_mark(event.product_id, event.mark_price)
            halt_reason = _guard_reason(event, ledger.contracts(event.product_id))
            if halt_reason is not None:
                budget.consume_output(1)
                equity.append(ledger.equity_point(event.sequence, event.observed_at))
                break
            try:
                evaluation = evaluate_simulation_event(state, event.market_event)
            except PaperSimulationError:
                raise BacktestDataError(
                    "PAPER rejected a gap, inconsistent state, or ambiguous OHLC path"
                ) from None
            state = evaluation.state
            child_roles = _role_maps(state, parent_roles)
            for proposed in evaluation.fills:
                role = direct_roles.get(proposed.order_id) or child_roles.get(proposed.order_id)
                if role is None:
                    raise BacktestDataError(
                        "fill is missing an explicit maker/taker role"
                    ) from None
                recorded, trade = _record_fill(proposed, role, product, ledger)
                budget.consume_output(1 + (trade is not None))
                fills.append(recorded)
                if trade is not None:
                    trades.append(trade)
            expected_position = simulation_position(
                state,
                owned_config.paper_account_id,
                OmsExecutionMode.BACKTEST,
                event.product_id,
            )
            if expected_position != ledger.contracts(event.product_id):
                raise BacktestDataError("PAPER and accounting positions diverged") from None
            halt_reason = _guard_reason(event, ledger.contracts(event.product_id))
            if halt_reason is None:
                for decision in event.decisions:
                    try:
                        state = submit_simulation_order(
                            state,
                            decision.intent,
                            config=decision.model_config,
                            stream_id=event.market_event.stream_id,
                            submitted_event_index=event.market_event.event_index,
                        )
                    except PaperSimulationError:
                        raise BacktestDataError(
                            "PAPER rejected an inconsistent order decision"
                        ) from None
                    _remember_fee_roles(decision, direct_roles, parent_roles)
            budget.consume_output(1)
            equity.append(ledger.equity_point(event.sequence, event.observed_at))
            if halt_reason is not None:
                break
        else:
            schedule = schedules[event.product_id]
            contracts = ledger.contracts(event.product_id)
            position_value, cashflow = _funding_cashflow(
                contracts, event.reference_price, event.rate, product
            )
            ledger.update_mark(event.product_id, event.reference_price)
            ledger.apply_funding(cashflow)
            budget.consume_output(2)
            funding.append(
                BacktestFundingRecord(
                    event.product_id,
                    event.observed_at,
                    contracts,
                    event.reference_price,
                    event.rate,
                    position_value,
                    cashflow,
                    schedule.version,
                    event.rate_source,
                )
            )
            seen_funding.add((event.product_id, event.observed_at))
            equity.append(ledger.equity_point(event.sequence, event.observed_at))
    incomplete = _funding_gaps(
        owned_config,
        ledger,
        seen_funding,
        processed_end,
        budget,
    )
    if incomplete and owned_config.strict_funding:
        raise BacktestIncompleteError(incomplete) from None
    status = (
        BacktestRunStatus.HALTED_LIQUIDATION_GUARD
        if halt_reason is not None
        else BacktestRunStatus.INCOMPLETE
        if incomplete
        else BacktestRunStatus.COMPLETE
    )
    final_positions = ledger.final_positions()
    assumptions = _assumptions(owned_config)
    budget.consume_output(len(final_positions) + len(assumptions))
    unrealized = ledger.unrealized_pnl()
    final_equity = ledger.cash + unrealized
    maximum, maximum_fraction = _drawdown(
        owned_config.initial_cash, tuple(item.equity for item in equity)
    )
    with arithmetic_context():
        net_pnl = final_equity - owned_config.initial_cash
        realized_net = ledger.gross_realized_pnl - ledger.total_fees + ledger.total_funding
        if net_pnl != realized_net + unrealized:
            raise BacktestDataError("accounting identity did not conserve equity") from None
    return BacktestReport(
        status=status,
        run_id_sha256=run_digest,
        config_sha256=config_digest,
        data_sha256=data_digest,
        initial_cash=owned_config.initial_cash,
        final_equity=final_equity,
        net_pnl=net_pnl,
        gross_realized_pnl=ledger.gross_realized_pnl,
        realized_net_pnl=realized_net,
        unrealized_pnl=unrealized,
        total_fees=ledger.total_fees,
        total_funding=ledger.total_funding,
        max_drawdown=maximum,
        max_drawdown_fraction=maximum_fraction,
        fills=tuple(fills),
        trades=tuple(trades),
        funding=tuple(funding),
        equity_curve=tuple(equity),
        final_positions=final_positions,
        assumptions=assumptions,
        incomplete_reasons=incomplete,
        halt_reason=halt_reason,
    )


def run_backtest(config: BacktestConfig, events: tuple[BacktestEvent, ...]) -> BacktestReport:
    """Replay exact UTC events under the module-owned Decimal policy."""

    with arithmetic_context():
        return _run_backtest(config, events)


def _remember_fee_roles(
    decision: BacktestOrderDecision,
    direct_roles: dict[UUID, LiquidityRole],
    parent_roles: dict[UUID, tuple[LiquidityRole | None, LiquidityRole | None]],
) -> None:
    order_id = decision.intent.order_id
    direct_roles[order_id] = decision.liquidity_role
    if decision.intent.bracket is not None:
        parent_roles[order_id] = (
            decision.stop_loss_liquidity_role,
            decision.take_profit_liquidity_role,
        )


__all__ = ["run_backtest"]
