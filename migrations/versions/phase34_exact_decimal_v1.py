"""Protect remaining money, price and quantity columns as exact decimals.

Upgrade validation and type changes take ACCESS EXCLUSIVE table locks. Widening
from a NUMERIC typmod to typmod-free NUMERIC is normally metadata-only, but the
operator must still schedule for lock acquisition. Downgrade reintroduces
typmods and can scan/rewrite rows; it fails closed if later values do not fit.
"""

from alembic import op

revision = "phase34_exact_decimal_v1"
down_revision = "phase33b_retention_purge_v1"
branch_labels = None
depends_on = None

EXACT_COLUMNS = (
    ("market_candles", "open", "NUMERIC(38, 18)"),
    ("market_candles", "high", "NUMERIC(38, 18)"),
    ("market_candles", "low", "NUMERIC(38, 18)"),
    ("market_candles", "close", "NUMERIC(38, 18)"),
    ("market_candles", "volume", "NUMERIC(38, 18)"),
    ("protection_groups", "average_fill_price", "NUMERIC(38, 18)"),
    ("gtt_orders", "trail_anchor", "NUMERIC(38, 18)"),
    ("tradingview_signals", "atr_value", "NUMERIC(38, 18)"),
    ("account_risk_policies", "max_order_notional", "NUMERIC(38, 18)"),
    ("account_risk_policies", "max_position_notional", "NUMERIC(38, 18)"),
    ("account_risk_policies", "max_daily_loss", "NUMERIC(38, 18)"),
    ("account_risk_policies", "usd_inr_rate", "NUMERIC(18, 6)"),
    ("account_risk_policies", "max_order_limit_inr", "NUMERIC(38, 18)"),
    ("account_risk_policies", "max_position_limit_inr", "NUMERIC(38, 18)"),
    ("account_risk_policies", "max_daily_loss_limit_inr", "NUMERIC(38, 18)"),
    ("live_order_previews", "limit_price", "NUMERIC(38, 18)"),
    ("live_order_previews", "price", "NUMERIC(38, 18)"),
    ("live_order_previews", "notional", "NUMERIC(38, 18)"),
    ("live_order_actions", "target_limit_price", "NUMERIC(38, 18)"),
)


def _constraint(table: str, column: str, kind: str) -> str:
    return f"ck_{table}_decimal_{column}_{kind}"


def upgrade() -> None:
    for table, column, _old_type in EXACT_COLUMNS:
        qualified = f'hmt_crypto."{table}"'
        quoted = f'"{column}"'
        op.execute(
            f"ALTER TABLE {qualified} ALTER COLUMN {quoted} TYPE NUMERIC USING {quoted}::NUMERIC"
        )
        checks = (
            ("finite", f"CAST({quoted} AS TEXT) NOT IN ('NaN', 'Infinity', '-Infinity')"),
            ("magnitude", f"abs({quoted}) < 100000000000000000000"),
            ("scale", f"{quoted} = trunc({quoted}, 18)"),
        )
        for kind, expression in checks:
            name = _constraint(table, column, kind)
            op.execute(
                f'ALTER TABLE {qualified} ADD CONSTRAINT "{name}" CHECK ({expression}) NOT VALID'
            )
            op.execute(f'ALTER TABLE {qualified} VALIDATE CONSTRAINT "{name}"')


def downgrade() -> None:
    for table, column, old_type in reversed(EXACT_COLUMNS):
        qualified = f'hmt_crypto."{table}"'
        quoted = f'"{column}"'
        op.execute(
            f"ALTER TABLE {qualified} ALTER COLUMN {quoted} TYPE {old_type} "
            f"USING {quoted}::{old_type}"
        )
        for kind in ("scale", "magnitude", "finite"):
            name = _constraint(table, column, kind)
            op.execute(f'ALTER TABLE {qualified} DROP CONSTRAINT "{name}"')
