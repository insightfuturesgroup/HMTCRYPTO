"""Immutable normalized balance and position observations.

Content hashes cover the typed economic tuple, not request provenance.  ORM
loads validate sibling hash agreement; Core/raw SQL cannot, and database-level
append-only enforcement remains deferred to reviewed triggers and grants.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any, Self, cast
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    Uuid,
    event,
    select,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection, Dialect
from sqlalchemy.orm import Mapped, mapped_column, object_session
from sqlalchemy.orm.state import InstanceState

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    guard_model,
    scoped_constraints,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
    sha_check,
)
from hmt_crypto.persistence.snapshot_observation import (
    BALANCE_FIELDS,
    POSITION_FIELDS,
    POSITION_KINDS,
    encode_snapshot,
    validate_snapshot,
)
from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime, exact_decimal_checks

COMMON_FIELDS = (
    "resource_kind",
    "observation_schema_version",
    "observation_sha256",
)


class AssetSymbol(NulFreeString):
    """Require exact bounded nonblank asset text on bind and result paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str:
        result = super().process_bind_param(value, dialect)
        if result is None or not 1 <= len(result) <= 128 or not result.strip():
            raise PersistenceValueError()
        return result

    def process_result_value(self, value: object, dialect: Dialect) -> str:
        return self.process_bind_param(value, dialect)


def _resource_fk(table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["resource_id", *SCOPE, "run_id", "resource_kind"],
        [
            f"hmt_crypto.reconciliation_resources.{field}"
            for field in ("id", *SCOPE, "run_id", "resource_kind")
        ],
        name=f"fk_{table}_resource_scope",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


def _common_constraints(table: str) -> tuple[Any, ...]:
    return (
        *scoped_constraints(table),
        _resource_fk(table),
        Index(
            f"ix_{table}_resource_parent",
            "resource_id",
            *SCOPE,
            "run_id",
            "resource_kind",
        ),
        CheckConstraint("observation_schema_version = 1", name="observation_schema_version"),
        sha_check("observation_sha256"),
        CheckConstraint(
            "requested_at <= received_at AND received_at <= recorded_at", name="time_order"
        ),
    )


class PositionSnapshot(ScopedRow, Base):
    """A latest or delayed-margined signed-contract observation."""

    __tablename__ = "position_snapshots"
    __table_args__ = (
        *_common_constraints(__tablename__),
        UniqueConstraint("id", *SCOPE, "product_id", name="uq_position_snapshots_id_scope_product"),
        UniqueConstraint(
            "resource_id",
            *SCOPE,
            "run_id",
            "resource_kind",
            "product_id",
            name="uq_position_snapshots_resource_product",
        ),
        checked("resource_kind", POSITION_KINDS),
        checked("margin_mode", ("isolated", "portfolio", "UNKNOWN")),
        *(
            constraint
            for field in (
                "entry_price",
                "mark_price",
                "liquidation_price",
                "margin",
                "unrealized_pnl",
            )
            for constraint in exact_decimal_checks(field, ExactDecimal(38, 18))
        ),
        CheckConstraint("product_id >= 1", name="product_positive"),
        CheckConstraint(
            "resource_kind = 'MARGINED_POSITIONS' OR "
            "(resource_kind = 'LATEST_POSITION' AND mark_price IS NULL "
            "AND liquidation_price IS NULL AND margin IS NULL "
            "AND unrealized_pnl IS NULL AND margin_mode IS NULL "
            "AND exchange_updated_at IS NULL)",
            name="latest_shape",
        ),
    )

    resource_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    resource_kind: Mapped[str] = mapped_column(NulFreeString(32))
    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    signed_contracts: Mapped[int] = mapped_column(StrictBigInteger())
    entry_price: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    mark_price: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    liquidation_price: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    margin: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    margin_mode: Mapped[str | None] = mapped_column(NulFreeString(16))
    exchange_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    observation_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    observation_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    @classmethod
    def from_observation(cls, **values: Any) -> Self:
        """Own a normalized tuple and derive its immutable content identity."""
        return cast(Self, _from_observation(cls, POSITION_FIELDS, values))


class BalanceSnapshot(ScopedRow, Base):
    """A normalized per-asset wallet balance observation."""

    __tablename__ = "balance_snapshots"
    __table_args__ = (
        *_common_constraints(__tablename__),
        UniqueConstraint(
            "resource_id",
            *SCOPE,
            "run_id",
            "resource_kind",
            "asset_id",
            name="uq_balance_snapshots_resource_asset_id",
        ),
        UniqueConstraint(
            "resource_id",
            *SCOPE,
            "run_id",
            "resource_kind",
            "asset_symbol",
            name="uq_balance_snapshots_resource_asset_symbol",
        ),
        CheckConstraint("resource_kind = 'BALANCES'", name="resource_kind"),
        CheckConstraint("asset_id >= 1", name="asset_positive"),
        bounded("asset_symbol"),
        *(
            constraint
            for field in ("available_balance", "balance", "blocked_margin")
            for constraint in exact_decimal_checks(field, ExactDecimal(38, 18))
        ),
    )

    resource_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    resource_kind: Mapped[str] = mapped_column(NulFreeString(32))
    asset_id: Mapped[int] = mapped_column(StrictBigInteger())
    asset_symbol: Mapped[str] = mapped_column(AssetSymbol(128))
    available_balance: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    balance: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    blocked_margin: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    observation_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    observation_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    @classmethod
    def from_observation(cls, **values: Any) -> Self:
        """Own a normalized tuple and derive its immutable content identity."""
        return cast(Self, _from_observation(cls, BALANCE_FIELDS, values))


def _from_observation(
    model: type[PositionSnapshot] | type[BalanceSnapshot],
    semantic_fields: frozenset[str],
    values: dict[str, Any],
) -> PositionSnapshot | BalanceSnapshot:
    if {"observation_schema_version", "observation_sha256"} & values.keys():
        raise PersistenceValueError()
    allowed = set(model.__table__.c.keys()) - {
        "observation_schema_version",
        "observation_sha256",
    }
    if values.keys() - allowed:
        raise PersistenceValueError()
    owned = dict(values)
    try:
        semantic = {field: owned[field] for field in semantic_fields}
        digest = encode_snapshot(owned.get("resource_kind"), 1, semantic)
    except Exception:
        raise PersistenceValueError() from None
    return model(
        observation_schema_version=1,
        observation_sha256=digest,
        **owned,
    )


def _validate_tuple(
    target: PositionSnapshot | BalanceSnapshot,
    semantic_fields: frozenset[str],
    connection: Connection | None = None,
) -> None:
    fields = (*COMMON_FIELDS, *semantic_fields)
    state = cast(InstanceState[Any], sa_inspect(target))
    values = {field: state.dict[field] for field in fields if field in state.dict}
    missing = [field for field in fields if field not in values]
    if missing:
        session = object_session(target)
        if state.identity is None or (connection is None and session is None):
            raise PersistenceValueError()
        if connection is None:
            assert session is not None
            connection = session.connection()
        table = target.__table__
        values.update(
            connection.execute(
                select(*(table.c[field] for field in missing)).where(
                    table.c.id == state.identity[0]
                )
            )
            .mappings()
            .one()
        )
    validate_snapshot(
        values["resource_kind"],
        values["observation_schema_version"],
        {field: values[field] for field in semantic_fields},
        values["observation_sha256"],
    )


def _install_validation(
    model: type[PositionSnapshot] | type[BalanceSnapshot], semantic_fields: frozenset[str]
) -> None:
    def before_write(mapper: object, connection: Connection, target: Any) -> None:
        _validate_tuple(target, semantic_fields, connection)

    def on_load(target: Any, context: object) -> None:
        _validate_tuple(target, semantic_fields)

    def on_refresh(target: Any, context: object, attrs: Iterable[str] | None) -> None:
        _validate_tuple(target, semantic_fields)

    event.listen(model, "before_insert", before_write)
    event.listen(model, "before_update", before_write)
    event.listen(model, "load", on_load, restore_load_context=True)
    event.listen(model, "refresh", on_refresh, restore_load_context=True)
    guard_model(model, immutable=True)


_install_validation(PositionSnapshot, POSITION_FIELDS)
_install_validation(BalanceSnapshot, BALANCE_FIELDS)
