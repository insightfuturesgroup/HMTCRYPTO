"""Append-only catalogue evidence; stored products confer no sizing authority."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.domain import Product
from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.payloads import MAX_PAYLOAD, decode_product, encode_product
from hmt_crypto.persistence.schema_support import (
    NulFreeString,
    NulFreeText,
    StrictBigInteger,
    bounded,
    sha_check,
    venue_checks,
)
from hmt_crypto.persistence.types import UTCDateTime


class CatalogueSnapshot(Base):
    """Venue publication evidence; unchanged content may have a fresh revision."""

    __tablename__ = "catalogue_snapshots"
    __table_args__ = (
        *venue_checks(),
        bounded("source"),
        sha_check("content_sha256"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("product_count >= 1", name="product_count_positive"),
        CheckConstraint(
            "fetched_at <= validated_at AND validated_at < fresh_until", name="time_order"
        ),
        UniqueConstraint(
            "region", "environment", "revision", name="uq_catalogue_snapshots_venue_revision"
        ),
        UniqueConstraint("id", "region", "environment", name="uq_catalogue_snapshots_id_venue"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    region: Mapped[str] = mapped_column(NulFreeString(16))
    environment: Mapped[str] = mapped_column(NulFreeString(16))
    revision: Mapped[int] = mapped_column(StrictBigInteger())
    source: Mapped[str] = mapped_column(NulFreeString(128))
    content_sha256: Mapped[str] = mapped_column(NulFreeString(64))
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime())
    validated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    fresh_until: Mapped[datetime] = mapped_column(UTCDateTime())
    product_count: Mapped[int] = mapped_column(StrictBigInteger())


class CatalogueProduct(Base):
    """One snapshot's exact product identity and canonical domain payload."""

    __tablename__ = "catalogue_products"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id"],
            ["hmt_crypto.catalogue_snapshots.id"],
            name="fk_catalogue_products_snapshot",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        UniqueConstraint(
            "snapshot_id", "product_id", name="uq_catalogue_products_snapshot_product"
        ),
        UniqueConstraint("snapshot_id", "symbol", name="uq_catalogue_products_snapshot_symbol"),
        CheckConstraint("product_id >= 1", name="product_id_positive"),
        bounded("symbol"),
        bounded("product_payload", MAX_PAYLOAD),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    symbol: Mapped[str] = mapped_column(NulFreeString(128))
    product_payload: Mapped[str] = mapped_column(NulFreeText)

    @classmethod
    def from_product(cls, *, snapshot_id: UUID, product: Product) -> "CatalogueProduct":
        """Copy validated metadata without granting catalogue publication capability."""
        payload = encode_product(product)
        owned = decode_product(payload)
        return cls(
            snapshot_id=snapshot_id,
            product_id=owned.id,
            symbol=owned.symbol,
            product_payload=payload,
        )

    def to_product(self) -> Product:
        """Return an independent revalidated Product or reject inconsistent storage."""
        product = decode_product(self.product_payload)
        if product.id != self.product_id or product.symbol != self.symbol:
            raise PersistenceValueError()
        return product


@event.listens_for(CatalogueProduct, "before_insert")
@event.listens_for(CatalogueProduct, "before_update")
def _validate_product(mapper: object, connection: object, target: CatalogueProduct) -> None:
    target.to_product()
