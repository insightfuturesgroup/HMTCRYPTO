"""Immutable economic observations and their sole authoritative local association.

No catalogue product, accounting policy or order projection is inferred here.
Typed size admission works for ORM/Core binds and results; SQLite TEXT is not
numeric SQL ordering. Raw-SQL/DB append-only enforcement remains Task 2.1f.
"""

import json
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint, Uuid
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import FILL_TYPES, encode_evidence
from hmt_crypto.persistence.execution_support import (
    EvidenceRow,
    RecordedRow,
    ScopedRow,
    checked,
    evidence_constraints,
    guard_model,
    scoped_constraints,
)
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    NulFreeText,
    StrictBigInteger,
    bounded,
)
from hmt_crypto.persistence.types import ExactDecimal, UTCDateTime


class _PositiveFillSize(ExactDecimal):
    """Retain exact Decimal admission and reject nonpositive sizes on both paths."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> Decimal | str | None:
        result = super().process_bind_param(value, dialect)
        if result is not None and Decimal(result) <= 0:
            raise PersistenceValueError()
        return result

    def process_result_value(self, value: object, dialect: Dialect) -> Decimal | None:
        result = super().process_result_value(value, dialect)
        if result is not None and result <= 0:
            raise PersistenceValueError()
        return result


class _FillEvidencePayload(NulFreeText):
    """Reject arbitrary/noncanonical fill evidence on ORM and Core binds/results."""

    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        payload = super().process_bind_param(value, dialect)
        if payload is None or len(payload) > 2048:
            raise PersistenceValueError()
        try:
            canonical, _ = encode_evidence("fill", "UNKNOWN", 1, json.loads(payload))
        except Exception:
            raise PersistenceValueError() from None
        if canonical != payload:
            raise PersistenceValueError()
        return payload


class Fill(ScopedRow, EvidenceRow, RecordedRow, Base):
    """Account-scoped fill evidence; exchange order references remain opaque text."""

    __tablename__ = "fills"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        *evidence_constraints(),
        UniqueConstraint(*SCOPE, "exchange_fill_id", name="uq_fills_scope_exchange_id"),
        UniqueConstraint("id", *SCOPE, "product_id", name="uq_fills_id_scope_product"),
        bounded("exchange_fill_id"),
        bounded("exchange_order_reference"),
        bounded("settling_asset_symbol"),
        checked("side", ("buy", "sell", "UNKNOWN")),
        checked("fill_type", FILL_TYPES),
        checked("source", ("VENUE", "SIMULATOR", "RECONCILIATION")),
        CheckConstraint("product_id >= 1", name="product_positive"),
        CheckConstraint("settling_asset_id >= 1", name="settling_asset_positive"),
        CheckConstraint(
            "requested_at <= received_at AND received_at <= recorded_at", name="time_order"
        ),
        Index("ix_fills_exchange_order_reference", *SCOPE, "exchange_order_reference"),
    )

    exchange_fill_id: Mapped[str] = mapped_column(NulFreeString(128))
    exchange_order_reference: Mapped[str | None] = mapped_column(NulFreeString(128))
    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    size: Mapped[Decimal] = mapped_column(_PositiveFillSize(38, 18))
    # Price presence/sign is an observation, including settlement uncertainty.
    price: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    side: Mapped[str] = mapped_column(NulFreeString(16))
    fill_type: Mapped[str] = mapped_column(NulFreeString(16))
    notional: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    # NULL means unknown; a negative maker commission is valid evidence.
    commission: Mapped[Decimal | None] = mapped_column(ExactDecimal(38, 18))
    settling_asset_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    settling_asset_symbol: Mapped[str | None] = mapped_column(NulFreeString(128))
    source: Mapped[str] = mapped_column(NulFreeString(16))
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    evidence_payload: Mapped[str] = mapped_column(_FillEvidencePayload())


class FillOrderLink(ScopedRow, RecordedRow, Base):
    """One immutable local association per fill, checked against both products/scopes."""

    __tablename__ = "fill_order_links"
    __table_args__ = (
        *scoped_constraints(__tablename__),
        UniqueConstraint("fill_id", name="uq_fill_order_links_fill"),
        *(
            ForeignKeyConstraint(
                [field, *SCOPE, "product_id"],
                [f"hmt_crypto.{parent}.{c}" for c in ("id", *SCOPE, "product_id")],
                name=f"fk_fill_order_links_{field}_scope_product",
                ondelete="RESTRICT",
                onupdate="RESTRICT",
            )
            for field, parent in (("fill_id", "fills"), ("order_id", "orders"))
        ),
        CheckConstraint("product_id >= 1", name="product_positive"),
        checked("source", ("VENUE", "SIMULATOR", "RECONCILIATION")),
        Index("ix_fill_order_links_order", "order_id", *SCOPE, "product_id"),
    )

    fill_id: Mapped[UUID] = mapped_column(Uuid)
    order_id: Mapped[UUID] = mapped_column(Uuid)
    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    source: Mapped[str] = mapped_column(NulFreeString(16))


guard_model(Fill, evidence_kind="fill", operation_field="fill_type", immutable=True)
guard_model(FillOrderLink, immutable=True)
