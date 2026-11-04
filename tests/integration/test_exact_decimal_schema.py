"""Every exact Decimal column must carry its PostgreSQL raw-SQL boundary."""

from schema_test_support import schema_api
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from hmt_crypto.persistence.types import ExactDecimal

EXPECTED_COLUMNS = {
    "orders": {"requested_limit_price", "requested_stop_price"},
    "fills": {"size", "price", "notional", "commission"},
    "exit_intents": {"requested_trigger_price"},
    "position_snapshots": {
        "entry_price",
        "mark_price",
        "liquidation_price",
        "margin",
        "unrealized_pnl",
    },
    "balance_snapshots": {"available_balance", "balance", "blocked_margin"},
    "wallet_transactions": {"amount", "post_balance"},
    "funding_snapshots": {"amount", "rate"},
    "pnl_snapshots": {
        "baseline_equity",
        "observed_equity",
        "reconciled_realized_pnl",
        "mark_to_market_unrealized_pnl",
        "equity_drawdown_loss",
        "unresolved_loss_reserve",
        "conservative_daily_loss",
    },
    "risk_state": {"baseline_equity", "conservative_daily_loss"},
    "risk_decisions": {
        "reserved_incremental_gross_exposure",
        "reserved_worst_case_loss",
        "reserved_collateral",
    },
}


def _decimal_columns():
    return {
        (table.name, column.name): column.type
        for table in schema_api().metadata.tables.values()
        for column in table.c
        if isinstance(column.type, ExactDecimal)
    }


def test_metadata_discovers_the_complete_exact_decimal_inventory():
    discovered = _decimal_columns()
    expected = {
        (table_name, column_name)
        for table_name, column_names in EXPECTED_COLUMNS.items()
        for column_name in column_names
    }
    assert len(discovered) == 31
    assert set(discovered) == expected
    assert {
        (decimal_type.precision, decimal_type.scale) for decimal_type in discovered.values()
    } == {(38, 18)}


def test_every_discovered_decimal_has_exact_postgres_checks_and_no_sqlite_leakage():
    api = schema_api()
    pg_dialect = postgresql.dialect()
    sqlite_dialect = sqlite.dialect()
    for (table_name, column_name), decimal_type in _decimal_columns().items():
        table = api.metadata.tables[f"hmt_crypto.{table_name}"]
        decimal_constraints = [
            constraint
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
            and str(constraint.name).startswith(f"ck_{table_name}_decimal_{column_name}_")
        ]
        constraints = {str(constraint.name): constraint for constraint in decimal_constraints}
        expected_names = {
            f"ck_{table_name}_decimal_{column_name}_{kind}"
            for kind in ("finite", "magnitude", "scale")
        }
        actual_names = {str(constraint.name) for constraint in decimal_constraints}
        assert len(decimal_constraints) == 3
        assert actual_names == expected_names, (
            f"{table_name}.{column_name} lacks its Decimal boundary"
        )

        pg = str(CreateTable(table).compile(dialect=pg_dialect))
        local = str(CreateTable(table).compile(dialect=sqlite_dialect))
        quoted = pg_dialect.identifier_preparer.quote(column_name)
        sqlite_quoted = sqlite_dialect.identifier_preparer.quote(column_name)
        bound = 10 ** (decimal_type.precision - decimal_type.scale)
        assert f"{quoted} NUMERIC" in pg
        assert f"{quoted} NUMERIC(" not in pg
        assert f"CAST({quoted} AS TEXT) NOT IN ('NaN', 'Infinity', '-Infinity')" in pg
        assert f"abs({quoted}) < {bound}" in pg
        assert f"{quoted} = trunc({quoted}, {decimal_type.scale})" in pg
        assert f"CAST({sqlite_quoted} AS TEXT) NOT IN" not in local
        assert f"abs({sqlite_quoted}) <" not in local
        assert f"{sqlite_quoted} = trunc({sqlite_quoted}," not in local
        for name in expected_names:
            compiled_name = pg_dialect.identifier_preparer.format_constraint(constraints[name])
            assert f"CONSTRAINT {compiled_name}" in pg
            assert compiled_name not in local


def test_existing_decimal_domain_constraints_and_nullability_remain_authoritative():
    api = schema_api()
    expected_checks = {
        "orders": {
            "ck_orders_basic_intent_prices",
            "ck_orders_limit_price_positive",
            "ck_orders_stop_price_positive",
        },
        "exit_intents": {"ck_exit_intents_price_applicability"},
        "pnl_snapshots": {
            "ck_pnl_snapshots_equity_drawdown_loss_nonnegative",
            "ck_pnl_snapshots_unresolved_loss_reserve_nonnegative",
            "ck_pnl_snapshots_conservative_daily_loss_nonnegative",
        },
        "risk_state": {"ck_risk_state_conservative_daily_loss_nonnegative"},
    }
    for table_name, wanted in expected_checks.items():
        table = api.metadata.tables[f"hmt_crypto.{table_name}"]
        assert wanted <= {str(constraint.name) for constraint in table.constraints}

    assert not api.Fill.__table__.c.size.nullable
    assert not api.BalanceSnapshot.__table__.c.available_balance.nullable
    assert not api.WalletTransaction.__table__.c.amount.nullable
    assert api.Order.__table__.c.requested_limit_price.nullable
    assert api.ExitIntent.__table__.c.requested_trigger_price.nullable
    assert api.RiskState.__table__.c.conservative_daily_loss.nullable
