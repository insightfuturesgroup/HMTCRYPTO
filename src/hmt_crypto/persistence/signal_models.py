"""Account-scoped immutable intent evidence, without strategy or order authority."""

import json
from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, String, Text, UniqueConstraint, Uuid, event
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.payloads import MAX_PAYLOAD, canonical_intent
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    StrictBigInteger,
    account_fk,
    bounded,
    mode_check,
    sha_check,
    venue_checks,
)
from hmt_crypto.persistence.types import UTCDateTime


class Signal(Base):
    """Sanitized durable signal intent; never authorization to submit an order."""

    __tablename__ = "signals"
    __table_args__ = (
        *venue_checks(),
        mode_check(),
        account_fk("signals"),
        bounded("signal_key"),
        bounded("source"),
        bounded("intent_payload", MAX_PAYLOAD),
        sha_check("intent_sha256"),
        CheckConstraint("product_id >= 1", name="product_id_positive"),
        CheckConstraint("generated_at <= recorded_at", name="time_order"),
        UniqueConstraint(*SCOPE, "signal_key", name="uq_signals_scope_key"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid)
    region: Mapped[str] = mapped_column(String(16))
    environment: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))
    signal_key: Mapped[str] = mapped_column(String(128))
    product_id: Mapped[int] = mapped_column(StrictBigInteger())
    source: Mapped[str] = mapped_column(String(128))
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    intent_payload: Mapped[str] = mapped_column(Text)
    intent_sha256: Mapped[str] = mapped_column(String(64))

    @classmethod
    def from_intent(
        cls,
        *,
        id: UUID,
        account_id: UUID,
        region: str,
        environment: str,
        mode: str,
        signal_key: str,
        product_id: int,
        source: str,
        generated_at: datetime,
        recorded_at: datetime,
        intent: dict[str, object],
    ) -> "Signal":
        """Own and sanitize a bounded exact intent, deriving its canonical hash."""
        payload = canonical_intent(intent)
        return cls(
            id=id,
            account_id=account_id,
            region=region,
            environment=environment,
            mode=mode,
            signal_key=signal_key,
            product_id=product_id,
            source=source,
            generated_at=generated_at,
            recorded_at=recorded_at,
            intent_payload=payload,
            intent_sha256=sha256(payload.encode("utf-8")).hexdigest(),
        )


@event.listens_for(Signal, "before_insert")
@event.listens_for(Signal, "before_update")
def _validate_intent(mapper: object, connection: object, target: Signal) -> None:
    try:
        if canonical_intent(json.loads(target.intent_payload)) != target.intent_payload:
            raise PersistenceValueError()
        if sha256(target.intent_payload.encode("utf-8")).hexdigest() != target.intent_sha256:
            raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None
