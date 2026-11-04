"""Typed protection admission, with no dispatch or evidence-qualification service.

Column adapters cover ORM/Core binds and results. Cross-column canonical/hash
identity and coverage sufficiency are ORM checks, not Core/raw-SQL guarantees.
Raw-SQL append-only/numeric and concurrent-writer enforcement belongs to 2.1f.
"""

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKeyConstraint, event
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import encode_evidence, validate_stored_evidence
from hmt_crypto.persistence.schema_support import SCOPE, NulFreeText
from hmt_crypto.persistence.types import ExactDecimal

MECHANISMS = ("NATIVE_REDUCE_ONLY", "SYNTHETIC_MONITOR")
AUTH_IDENTITY = (
    "exit_intent_id",
    "policy_version",
    "policy_sha256",
    "backend",
    "mechanism",
    "decision",
)


class StrictBoolean(TypeDecorator[bool]):
    """SQLAlchemy's Boolean accepts 0/1; requested policy requires actual bool."""

    impl = Boolean
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> bool | None:
        if value is not None and type(value) is not bool:
            raise PersistenceValueError()
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> bool | None:
        return self.process_bind_param(value, dialect)


def product_fk(table: str, field: str, parent: str, *extra: str) -> ForeignKeyConstraint:
    """Bind exactly to a product as well as the complete operational scope."""
    return ForeignKeyConstraint(
        [field, *SCOPE, "product_id", *extra],
        [f"hmt_crypto.{parent}.{c}" for c in ("id", *SCOPE, "product_id", *extra)],
        name=f"fk_{table}_{field}_scope_product",
        ondelete="RESTRICT",
        onupdate="RESTRICT",
    )


class PositivePrice(ExactDecimal):
    """Exact positive prices, including rejection on corrupted result reads."""

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


class ProtectionPayload(NulFreeText):
    """Own canonical allowlisted contracts on direct ORM/Core binds/results."""

    cache_ok = True

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__()

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        payload = super().process_bind_param(value, dialect)
        try:
            if payload is None or len(payload) > 2048:
                raise PersistenceValueError()
            operation = "STOP_LOSS" if self.kind == "exit_policy" else "UNVERIFIED"
            canonical, _ = encode_evidence(self.kind, operation, 1, json.loads(payload))
            if canonical != payload:
                raise PersistenceValueError()
            return payload
        except Exception:
            raise PersistenceValueError() from None


def validate_protection_model(model: type[Any], kind: str) -> None:
    """Reject inconsistent ORM claims, including legacy/corrupted ORM loads.

    PROTECTED remains an explicit caller claim needing later service qualification:
    necessary fields are not proof of freshness, completeness or applicability.
    """

    def validate(target: Any) -> None:
        if kind in {"exit_policy", "protection_authorization"}:
            operation = target.purpose if kind == "exit_policy" else target.decision
            validate_stored_evidence(
                kind,
                operation,
                target.evidence_schema_version,
                target.evidence_payload,
                target.evidence_sha256,
            )
            payload = json.loads(target.evidence_payload)
            if kind == "exit_policy":
                if (target.purpose == "CLOSE_POSITION") != (payload["trigger_basis"] == "NONE"):
                    raise PersistenceValueError()
            else:
                expected = {
                    "PAPER_AUTHORIZED": "SIMULATION",
                    "DENIED": "DENIED",
                    "UNVERIFIED": "UNVERIFIED",
                    "TESTNET_NATIVE_VERIFIED": "TESTNET_VERIFICATION",
                    "SYNTHETIC_AUTHORIZED": "EXPLICIT_SYNTHETIC_AUTHORIZATION",
                }
                if payload["basis"] != expected[target.decision]:
                    raise PersistenceValueError()
        else:
            observed = target.observed_position_contracts
            required, verified = target.required_contracts, target.verified_contracts
            if any(
                value is not None and type(value) is not int
                for value in (observed, required, verified)
            ):
                raise PersistenceValueError()
            if target.state not in {"UNKNOWN", "NO_REQUIRED_COVERAGE"}:
                if observed is None or observed == 0 or required is None or required <= 0:
                    raise PersistenceValueError()
            if target.state == "PROTECTED" and (
                verified is None or required is None or verified < required
            ):
                raise PersistenceValueError()

    def before_write(mapper: object, connection: object, target: Any) -> None:
        validate(target)

    def on_load(target: Any, context: object) -> None:
        validate(target)

    event.listen(model, "before_insert", before_write)
    event.listen(model, "before_update", before_write)
    event.listen(model, "load", on_load)
    if kind == "exit_policy":

        def reject_delete(mapper: object, connection: object, target: Any) -> None:
            raise PersistenceValueError()

        event.listen(model, "before_delete", reject_delete)
