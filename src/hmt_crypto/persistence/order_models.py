"""Local immutable intent beside a mutable, explicitly unknown venue projection.

Client IDs are correlation handles only. Stored catalogue membership is neither
freshness evidence nor a sizing/publication capability. No legal OMS graph here.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.execution_support import (
    STATES,
    ScopedRow,
    checked,
    guard_model,
    scoped_constraints,
    scoped_fk,
)
from hmt_crypto.persistence.schema_support import SCOPE, NulFreeString, StrictBigInteger, bounded
from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime, exact_decimal_checks


def _client_charset() -> tuple[CheckConstraint, CheckConstraint]:
    return (
        CheckConstraint(
            "client_order_id NOT GLOB '*[^a-zA-Z0-9_-]*'", name="client_order_id_charset_sqlite"
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(
            "client_order_id ~ '^[a-zA-Z0-9_-]+$'", name="client_order_id_charset_postgresql"
        ).ddl_if(dialect="postgresql"),
    )


class Order(ScopedRow, Base):
    """Adoption never fabricates original local intent or catalogue evidence."""

    __tablename__ = "orders"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        scoped_fk(__tablename__, "command_id", "command_operations"),
        scoped_fk(__tablename__, "signal_id", "signals"),
        ForeignKeyConstraint(
            ["catalogue_snapshot_id", "region", "environment"],
            [f"hmt_crypto.catalogue_snapshots.{c}" for c in ("id", "region", "environment")],
            name="fk_orders_snapshot_venue",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["catalogue_snapshot_id", "product_id"],
            [
                "hmt_crypto.catalogue_products.snapshot_id",
                "hmt_crypto.catalogue_products.product_id",
            ],
            name="fk_orders_snapshot_product",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint("id", *SCOPE, "product_id", name="uq_orders_id_scope_product"),
        UniqueConstraint(
            "id",
            *SCOPE,
            "product_id",
            "command_id",
            "requested_contracts",
            "requested_side",
            "requested_reduce_only",
            name="uq_orders_risk_decision_parent",
        ),
        UniqueConstraint(*SCOPE, "client_order_id", name="uq_orders_scope_client_id"),
        UniqueConstraint(*SCOPE, "exchange_order_id", name="uq_orders_scope_exchange_id"),
        checked("origin", ("LOCAL_INTENT", "SIMULATOR_INTENT", "ADOPTED")),
        checked("state", STATES),
        checked("requested_side", ("buy", "sell")),
        checked("requested_order_type", ("limit", "market")),
        checked("observed_side", ("buy", "sell", "UNKNOWN")),
        checked("observed_order_type", ("limit", "market", "UNKNOWN")),
        checked("observed_state", ("pending", "open", "closed", "cancelled", "UNKNOWN")),
        bounded("client_order_id", 32),
        *_client_charset(),
        bounded("exchange_order_id"),
        *exact_decimal_checks("requested_limit_price", ExactDecimal(38, 18)),
        *exact_decimal_checks("requested_stop_price", ExactDecimal(38, 18)),
        CheckConstraint("product_id >= 1 AND requested_contracts >= 1", name="positive_contracts"),
        CheckConstraint("version >= 1 AND last_transition_sequence >= 0", name="counters"),
        CheckConstraint("observed_filled_contracts >= 0", name="observed_filled_nonnegative"),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint(
            "(state IN ('FILLED','CANCELLED','REJECTED','EXPIRED') AND terminal_at IS NOT NULL "
            "AND terminal_at >= created_at AND terminal_at <= updated_at) OR "
            "(state NOT IN ('FILLED','CANCELLED','REJECTED','EXPIRED') AND terminal_at IS NULL)",
            name="terminal_time",
        ),
        CheckConstraint(
            "(origin IN ('LOCAL_INTENT','SIMULATOR_INTENT') AND command_id IS NOT NULL "
            "AND client_order_id IS NOT NULL AND catalogue_snapshot_id IS NOT NULL "
            "AND requested_contracts IS NOT NULL AND requested_side IS NOT NULL "
            "AND requested_order_type IS NOT NULL AND requested_reduce_only IS NOT NULL "
            "AND ((origin = 'LOCAL_INTENT' AND mode = 'LIVE') OR "
            "(origin = 'SIMULATOR_INTENT' AND mode IN ('PAPER','BACKTEST')))) OR "
            "(origin = 'ADOPTED' AND mode = 'LIVE' AND exchange_order_id IS NOT NULL "
            "AND command_id IS NULL AND signal_id IS NULL AND client_order_id IS NULL "
            "AND catalogue_snapshot_id IS NULL AND requested_contracts IS NULL "
            "AND requested_side IS NULL AND requested_order_type IS NULL "
            "AND requested_limit_price IS NULL AND requested_stop_price IS NULL "
            "AND requested_reduce_only IS NULL)",
            name="intent_provenance",
        ),
        CheckConstraint(
            "origin = 'ADOPTED' OR (requested_stop_price IS NULL AND "
            "((requested_order_type = 'market' AND requested_limit_price IS NULL) OR "
            "(requested_order_type = 'limit' AND requested_limit_price IS NOT NULL "
            "AND requested_limit_price > 0)))",
            name="basic_intent_prices",
        ),
        CheckConstraint(
            "requested_limit_price IS NULL OR requested_limit_price > 0",
            name="limit_price_positive",
        ),
        CheckConstraint(
            "requested_stop_price IS NULL OR requested_stop_price > 0", name="stop_price_positive"
        ),
        Index("ix_orders_command", "command_id", *SCOPE),
        Index("ix_orders_signal", "signal_id", *SCOPE),
        Index("ix_orders_snapshot_product", "catalogue_snapshot_id", "product_id"),
        Index("ix_orders_snapshot_venue", "catalogue_snapshot_id", "region", "environment"),
    )

    origin: Mapped[str] = mapped_column(NulFreeString(32))
    command_id: Mapped[UUID | None] = mapped_column(Uuid)
    signal_id: Mapped[UUID | None] = mapped_column(Uuid)
    client_order_id: Mapped[str | None] = mapped_column(NulFreeString(32))
    exchange_order_id: Mapped[str | None] = mapped_column(NulFreeString(128))
    catalogue_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    requested_contracts: Mapped[int | None] = mapped_column(StrictBigInteger())
    requested_side: Mapped[str | None] = mapped_column(NulFreeString(16))
    requested_order_type: Mapped[str | None] = mapped_column(NulFreeString(16))
    requested_limit_price: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    requested_stop_price: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    requested_reduce_only: Mapped[bool | None] = mapped_column(
        Boolean(create_constraint=True, name="requested_reduce_only_boolean")
    )
    observed_side: Mapped[str | None] = mapped_column(NulFreeString(16))
    observed_order_type: Mapped[str | None] = mapped_column(NulFreeString(16))
    observed_state: Mapped[str | None] = mapped_column(NulFreeString(16))
    # Unknown remains NULL. Original requested size is immutable; a later modify
    # can change venue size, so no unverified cap against original intent exists.
    observed_filled_contracts: Mapped[int | None] = mapped_column(StrictBigInteger())
    state: Mapped[str] = mapped_column(NulFreeString(32))
    version: Mapped[int] = mapped_column(StrictBigInteger())
    last_transition_sequence: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    terminal_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


guard_model(
    Order,
    protected=(
        "origin",
        "command_id",
        "signal_id",
        "client_order_id",
        "catalogue_snapshot_id",
        "product_id",
        "requested_contracts",
        "requested_side",
        "requested_order_type",
        "requested_limit_price",
        "requested_stop_price",
        "requested_reduce_only",
        "created_at",
    ),
)
