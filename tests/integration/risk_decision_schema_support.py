"""Persisted parent graph and canonical Task 2.1d9a decision fixtures."""

from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from execution_test_support import command, order
from schema_test_support import HASH, NOW, product, schema_api, scope, snapshot
from test_coordination_schema import graph, save
from test_pnl_snapshot_schema import pnl
from test_reconciliation_run_schema import outcome, run
from test_risk_state_schema import claimed_state

CHECK_KINDS = (
    "PRODUCT_CATALOGUE",
    "MARKET_TRADABILITY",
    "MAX_POSITION",
    "TOTAL_EXPOSURE",
    "MAX_LEVERAGE",
    "MAX_LOSS_PER_TRADE",
    "MAX_DAILY_LOSS",
    "MAX_OPEN_ORDERS",
    "MAX_ORDER_RATE",
    "PRICE_COLLAR",
    "DUPLICATE_ORDER",
    "COLLATERAL_MARGIN",
    "KILL_HALT",
    "FRESHNESS_RECONCILIATION",
    "PROTECTION",
    "REDUCE_ONLY_NO_REVERSAL",
)

NUMERIC = {
    "MAX_POSITION": ("INTEGER", "CONTRACTS", None, None, 2, 10),
    "TOTAL_EXPOSURE": (
        "DECIMAL",
        "REFERENCE_ASSET",
        Decimal("100"),
        Decimal("1000"),
        None,
        None,
    ),
    "MAX_LEVERAGE": ("DECIMAL", "LEVERAGE", Decimal("2"), Decimal("5"), None, None),
    "MAX_LOSS_PER_TRADE": (
        "DECIMAL",
        "REFERENCE_ASSET",
        Decimal("10"),
        Decimal("50"),
        None,
        None,
    ),
    "MAX_DAILY_LOSS": (
        "DECIMAL",
        "REFERENCE_ASSET",
        Decimal("20"),
        Decimal("100"),
        None,
        None,
    ),
    "MAX_OPEN_ORDERS": ("INTEGER", "ORDERS", None, None, 1, 10),
    "MAX_ORDER_RATE": ("INTEGER", "REQUESTS", None, None, 1, 20),
    "PRICE_COLLAR": (
        "DECIMAL",
        "BASIS_POINTS",
        Decimal("50"),
        Decimal("100"),
        None,
        None,
    ),
    "COLLATERAL_MARGIN": (
        "DECIMAL",
        "COLLATERAL_ASSET",
        Decimal("10"),
        Decimal("100"),
        None,
        None,
    ),
    "FRESHNESS_RECONCILIATION": ("INTEGER", "SECONDS", None, None, 5, 30),
    "PROTECTION": ("INTEGER", "CONTRACTS", None, None, 2, 2),
}


def check_results():
    results = OrderedDict()
    for index, kind in enumerate(CHECK_KINDS, start=1):
        if kind == "REDUCE_ONLY_NO_REVERSAL":
            results[kind] = dict(
                check_kind=kind,
                outcome="NOT_APPLICABLE",
                reason_code="NOT_APPLICABLE",
                metric_kind="NONE",
                observed_decimal=None,
                limit_decimal=None,
                observed_integer=None,
                limit_integer=None,
                unit="NONE",
                evidence_observed_at=None,
                input_set_sha256=f"{index:064x}",
            )
            continue
        metric = NUMERIC.get(kind, ("NONE", "NONE", None, None, None, None))
        results[kind] = dict(
            check_kind=kind,
            outcome="PASS",
            reason_code="SATISFIED",
            metric_kind=metric[0],
            unit=metric[1],
            observed_decimal=metric[2],
            limit_decimal=metric[3],
            observed_integer=metric[4],
            limit_integer=metric[5],
            evidence_observed_at=NOW,
            input_set_sha256=f"{index:064x}",
        )
    return results


def reservation_plan(**changes):
    values = dict(
        approved_contracts=2,
        approved_side="buy",
        reference_asset_id=1,
        reference_asset_symbol="USDT",
        collateral_asset_id=2,
        collateral_asset_symbol="USDT",
        reserved_incremental_gross_exposure=Decimal("100"),
        reserved_worst_case_loss=Decimal("10"),
        reserved_collateral=Decimal("10"),
        reserved_open_order_slots=1,
        reserved_pending_rate_units=1,
        rate_policy_version=1,
        rate_policy_sha256="d" * 64,
        rate_window_start_at=NOW,
        rate_window_end_at=NOW + timedelta(minutes=2),
    )
    values.update(changes)
    return values


async def decision_graph(db):
    api = schema_api()
    owner, recovery = await graph(db)
    publication = snapshot()
    await save(db, publication)
    await save(
        db,
        api.CatalogueProduct.from_product(snapshot_id=publication.id, product=product()),
    )
    reconciliation = run(
        owner,
        recovery,
        **outcome(completeness="COMPLETE"),
    )
    await save(db, reconciliation)
    calculation = pnl(owner, reconciliation)
    await save(db, calculation)
    state = claimed_state(owner, reconciliation, calculation)
    await save(db, state)
    claim_owner = api.WorkerHeartbeat(
        id=uuid4(),
        **scope(owner),
        started_at=NOW,
        last_seen_at=NOW,
        health_state="HEALTHY",
        observed_recovery_incarnation_id=None,
        observed_lease_id=None,
        observed_leadership_generation=None,
        version=1,
    )
    await save(db, claim_owner)
    cmd = command(
        owner,
        status="CLAIMED",
        claim_owner=claim_owner.id,
        claim_generation=1,
        claim_expires_at=NOW + timedelta(minutes=3),
    )
    await save(db, cmd)
    row = order(owner, cmd, publication)
    await save(db, row)
    return owner, recovery, reconciliation, calculation, state, claim_owner, cmd, row


def decision(owner, reconciliation, calculation, state, claim_owner, cmd, row, **changes):
    values = dict(
        id=uuid4(),
        **scope(owner),
        decision_key="decision-1",
        risk_state_id=state.id,
        order_id=row.id,
        product_id=row.product_id,
        order_origin_command_id=row.command_id,
        order_requested_contracts=row.requested_contracts,
        order_requested_side=row.requested_side,
        order_requested_reduce_only=row.requested_reduce_only,
        outcome="APPROVED",
        risk_effect="RISK_INCREASE",
        policy_version=1,
        policy_sha256=HASH,
        evaluation_reference_asset_id=1,
        evaluation_reference_asset_symbol="USDT",
        evaluation_collateral_asset_id=2,
        evaluation_collateral_asset_symbol="USDT",
        observed_risk_state_version=state.version,
        observed_halt_set_state=state.halt_set_state,
        observed_halt_revision=state.halt_revision,
        observed_reconciliation_state=state.reconciliation_state,
        observed_accounting_state=state.accounting_state,
        observed_command_version=cmd.version,
        observed_claim_owner_id=claim_owner.id,
        observed_claim_generation=cmd.claim_generation,
        observed_claim_expires_at=cmd.claim_expires_at,
        observed_order_version=row.version,
        observed_order_transition_sequence=row.last_transition_sequence,
        observed_command_leader_ref=None,
        observed_command_leader_generation=None,
        observed_command_recovery_ref=None,
        observed_command_recovery_generation=None,
        leadership_lease_id=None,
        recovery_incarnation_id=None,
        observed_leadership_owner_id=None,
        observed_leadership_generation=None,
        observed_leadership_expires_at=None,
        observed_leadership_version=None,
        reconciliation_run_id=reconciliation.id,
        reconciliation_run_version=reconciliation.version,
        reconciliation_requirements_version=reconciliation.requirements_version,
        reconciliation_requirements_sha256=reconciliation.requirements_sha256,
        reconciliation_observed_at=state.reconciliation_observed_at,
        reconciliation_completeness=reconciliation.completeness,
        pnl_snapshot_id=calculation.id,
        pnl_completeness=calculation.completeness,
        pnl_calculation_version=calculation.calculation_version,
        pnl_reference_asset_id=calculation.reference_asset_id,
        pnl_reference_asset_symbol=calculation.reference_asset_symbol,
        pnl_evidence_sha256=calculation.evidence_sha256,
        pnl_input_set_sha256=calculation.input_set_sha256,
        evaluated_at=NOW + timedelta(seconds=10),
        recorded_at=NOW + timedelta(seconds=11),
    )
    values.update(changes)
    return schema_api().RiskDecision.from_evidence(
        command=cmd,
        check_results=check_results(),
        reservation_plan=reservation_plan(),
        **values,
    )
