"""Append-only wallet observations and explicitly non-additive funding evidence.

Typed payload columns validate standalone canonical shape for Core paths. ORM
loads additionally validate complete sibling agreement. Raw SQL append-only and
cross-column enforcement remains deferred to reviewed PostgreSQL grants/triggers.
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

from hmt_crypto.persistence.accounting_evidence import (
    ACCOUNTING_ROLE,
    FUNDING_FIELDS,
    FUNDING_KIND,
    SOURCE_KINDS,
    WALLET_FIELDS,
    WALLET_KIND,
    AccountingPayload,
    encode_accounting_evidence,
)
from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import (
    ScopedRow,
    checked,
    evidence_constraints,
    guard_model,
    scoped_constraints,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    bounded,
)
from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime, exact_decimal_checks

EVIDENCE_FIELDS = ("evidence_schema_version", "evidence_payload", "evidence_sha256")


class BoundedExactText(NulFreeString):
    """Require exact nonblank text of at most 128 characters on reads and writes."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        result = super().process_bind_param(value, dialect)
        if result is None or not 1 <= len(result) <= 128 or not result.strip():
            raise PersistenceValueError()
        return result

    def process_result_value(self, value: object, dialect: Dialect) -> str | None:
        return self.process_bind_param(value, dialect)


class NullableBoundedExactText(BoundedExactText):
    """Preserve SQL NULL while applying the exact text boundary when present."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return super().process_bind_param(value, dialect)

    def process_result_value(self, value: object, dialect: Dialect) -> str | None:
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


class WalletTransaction(ScopedRow, Base):
    """Deduplicated authoritative wallet transaction observation."""

    __tablename__ = "wallet_transactions"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        _resource_fk(__tablename__),
        UniqueConstraint(
            *SCOPE,
            "exchange_transaction_id",
            name="uq_wallet_transactions_scope_exchange_id",
        ),
        UniqueConstraint("id", *SCOPE, "asset_id", name="uq_wallet_transactions_id_scope_asset"),
        Index(
            "ix_wallet_transactions_resource_parent",
            "resource_id",
            *SCOPE,
            "run_id",
            "resource_kind",
        ),
        CheckConstraint("resource_kind = 'WALLET_TRANSACTIONS'", name="resource_kind"),
        CheckConstraint("product_id IS NULL OR product_id >= 1", name="product_positive"),
        CheckConstraint("asset_id >= 1", name="asset_positive"),
        bounded("exchange_transaction_id"),
        bounded("transaction_type"),
        bounded("asset_symbol"),
        *exact_decimal_checks("amount", ExactDecimal(38, 18)),
        *exact_decimal_checks("post_balance", ExactDecimal(38, 18)),
        CheckConstraint(
            "requested_at <= received_at AND received_at <= recorded_at", name="time_order"
        ),
    )

    resource_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    resource_kind: Mapped[str] = mapped_column(NulFreeString(32))
    exchange_transaction_id: Mapped[str] = mapped_column(BoundedExactText(128))
    transaction_type: Mapped[str] = mapped_column(BoundedExactText(128))
    product_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    asset_id: Mapped[int] = mapped_column(StrictBigInteger())
    asset_symbol: Mapped[str] = mapped_column(BoundedExactText(128))
    amount: Mapped[Decimal] = mapped_column(ExactDecimal(38, 18))
    post_balance: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    effective_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(AccountingPayload(WALLET_KIND))
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))

    @classmethod
    def from_evidence(cls, **values: Any) -> Self:
        """Own the normalized transaction tuple and derive canonical evidence."""
        return cast(Self, _from_evidence(cls, WALLET_KIND, WALLET_FIELDS, values))


class FundingSnapshot(ScopedRow, Base):
    """A non-additive funding classification or informational rate observation."""

    __tablename__ = "funding_snapshots"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        UniqueConstraint(
            *SCOPE, "observation_key", name="uq_funding_snapshots_scope_observation_key"
        ),
        UniqueConstraint(
            "wallet_transaction_id",
            *SCOPE,
            name="uq_funding_snapshots_wallet_parent",
        ),
        UniqueConstraint(
            "position_snapshot_id",
            *SCOPE,
            name="uq_funding_snapshots_position_parent",
        ),
        ForeignKeyConstraint(
            ["wallet_transaction_id", *SCOPE, "asset_id"],
            [f"hmt_crypto.wallet_transactions.{field}" for field in ("id", *SCOPE, "asset_id")],
            name="fk_funding_snapshots_wallet_scope_asset",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["position_snapshot_id", *SCOPE, "product_id"],
            [f"hmt_crypto.position_snapshots.{field}" for field in ("id", *SCOPE, "product_id")],
            name="fk_funding_snapshots_position_scope_product",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index(
            "ix_funding_snapshots_wallet_parent",
            "wallet_transaction_id",
            *SCOPE,
            "asset_id",
        ),
        Index(
            "ix_funding_snapshots_position_parent",
            "position_snapshot_id",
            *SCOPE,
            "product_id",
        ),
        checked("source_kind", SOURCE_KINDS),
        CheckConstraint(f"accounting_role = '{ACCOUNTING_ROLE}'", name="accounting_role"),
        CheckConstraint("product_id IS NULL OR product_id >= 1", name="product_positive"),
        CheckConstraint(
            "(asset_id IS NULL AND asset_symbol IS NULL) OR "
            "(asset_id IS NOT NULL AND asset_id >= 1 AND asset_symbol IS NOT NULL)",
            name="asset_pair",
        ),
        bounded("observation_key"),
        bounded("asset_symbol"),
        *exact_decimal_checks("amount", ExactDecimal(38, 18)),
        *exact_decimal_checks("rate", ExactDecimal(38, 18)),
        CheckConstraint(
            "(source_kind = 'WALLET_TRANSACTION' "
            "AND wallet_transaction_id IS NOT NULL AND position_snapshot_id IS NULL "
            "AND amount IS NOT NULL AND rate IS NULL AND asset_id IS NOT NULL) OR "
            "(source_kind = 'POSITION_AGGREGATE' "
            "AND wallet_transaction_id IS NULL AND position_snapshot_id IS NOT NULL "
            "AND product_id IS NOT NULL AND amount IS NOT NULL AND rate IS NULL) OR "
            "(source_kind = 'MARKET_RATE' "
            "AND wallet_transaction_id IS NULL AND position_snapshot_id IS NULL "
            "AND product_id IS NOT NULL AND amount IS NULL AND rate IS NOT NULL "
            "AND effective_at IS NOT NULL)",
            name="source_shape",
        ),
        CheckConstraint("observed_at <= recorded_at", name="time_order"),
    )

    observation_key: Mapped[str] = mapped_column(BoundedExactText(128))
    source_kind: Mapped[str] = mapped_column(NulFreeString(32))
    wallet_transaction_id: Mapped[UUID | None] = mapped_column(Uuid)
    position_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    product_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    asset_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    asset_symbol: Mapped[str | None] = mapped_column(NullableBoundedExactText(128))
    amount: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    rate: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    effective_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    accounting_role: Mapped[str] = mapped_column(NulFreeString(32))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    evidence_schema_version: Mapped[int] = mapped_column(StrictBigInteger())
    evidence_payload: Mapped[str] = mapped_column(AccountingPayload(FUNDING_KIND))
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64))

    @classmethod
    def from_evidence(cls, **values: Any) -> Self:
        """Own the classification tuple and derive canonical safe evidence."""
        return cast(Self, _from_evidence(cls, FUNDING_KIND, FUNDING_FIELDS, values))


def _from_evidence(
    model: type[WalletTransaction] | type[FundingSnapshot],
    kind: str,
    semantic_fields: frozenset[str],
    values: dict[str, Any],
) -> WalletTransaction | FundingSnapshot:
    if set(EVIDENCE_FIELDS) & values.keys():
        raise PersistenceValueError()
    allowed = set(model.__table__.c.keys()) - set(EVIDENCE_FIELDS)
    if values.keys() - allowed:
        raise PersistenceValueError()
    owned = dict(values)
    try:
        semantic = {field: owned[field] for field in semantic_fields}
        payload, digest = encode_accounting_evidence(kind, 1, semantic)
    except Exception:
        raise PersistenceValueError() from None
    return model(
        evidence_schema_version=1,
        evidence_payload=payload,
        evidence_sha256=digest,
        **owned,
    )


def _validate_tuple(
    target: WalletTransaction | FundingSnapshot,
    kind: str,
    semantic_fields: frozenset[str],
    connection: Connection | None = None,
) -> None:
    fields = (*EVIDENCE_FIELDS, *semantic_fields)
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
    semantic = {field: values[field] for field in semantic_fields}
    if encode_accounting_evidence(kind, values["evidence_schema_version"], semantic) != (
        values["evidence_payload"],
        values["evidence_sha256"],
    ):
        raise PersistenceValueError()


def _install_validation(
    model: type[WalletTransaction] | type[FundingSnapshot],
    kind: str,
    semantic_fields: frozenset[str],
) -> None:
    def before_write(mapper: object, connection: Connection, target: Any) -> None:
        _validate_tuple(target, kind, semantic_fields, connection)

    def on_load(target: Any, context: object) -> None:
        _validate_tuple(target, kind, semantic_fields)

    def on_refresh(target: Any, context: object, attrs: Iterable[str] | None) -> None:
        _validate_tuple(target, kind, semantic_fields)

    event.listen(model, "before_insert", before_write)
    event.listen(model, "before_update", before_write)
    event.listen(model, "load", on_load, restore_load_context=True)
    event.listen(model, "refresh", on_refresh, restore_load_context=True)
    guard_model(model, immutable=True)


_install_validation(WalletTransaction, WALLET_KIND, WALLET_FIELDS)
_install_validation(FundingSnapshot, FUNDING_KIND, FUNDING_FIELDS)
