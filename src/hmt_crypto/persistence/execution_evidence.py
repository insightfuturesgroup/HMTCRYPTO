"""Small storage evidence contracts, deliberately independent of private wire DTOs.

No free-form messages, headers, signatures, prehash or arbitrary metadata are
accepted. These values record intent/observations; none confer write authority.
"""

import json
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StrictBool, field_validator

from hmt_crypto.domain.models import DomainModel
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.types import _decimal_text

PositiveInteger = Annotated[int, Field(strict=True, ge=1, lt=2**63)]
OPERATIONS = (
    "PLACE_ORDER",
    "MODIFY_ORDER",
    "CANCEL_ORDER",
    "CANCEL_ALL",
    "SET_LEVERAGE",
    "SET_MARGIN_MODE",
    "CHANGE_POSITION_MARGIN",
    "CREATE_BRACKET",
    "EDIT_BRACKET",
)
ATTEMPT_EVENT_KINDS = (
    "DEFINITE_NOT_SENT",
    "DISPATCH_STARTED",
    "RESPONSE_ACCEPTED",
    "RESPONSE_REJECTED",
    "UNCERTAIN",
    "RECONCILED",
    "SIMULATED_ACCEPTED",
    "SIMULATED_REJECTED",
)
TRANSITION_KINDS = ("LOCAL_INTENT", "OBSERVATION", "RESPONSE", "RECONCILIATION")


class IntentEvidence(DomainModel):
    """Minimal semantic storage intent, not an executable exchange request."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    product_id: PositiveInteger | None = None
    requested_contracts: PositiveInteger | None = None
    side: Literal["buy", "sell"] | None = None
    order_type: Literal["limit", "market"] | None = None
    reduce_only: StrictBool | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    target_order_id: UUID | None = None
    margin_mode: Literal["isolated", "portfolio"] | None = None

    @field_validator("limit_price", "stop_price", mode="before")
    @classmethod
    def _price(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        _decimal_text(value, 38, 18)
        if not isinstance(value, Decimal) or value <= 0:
            raise PersistenceValueError()
        return value


class EventEvidence(DomainModel):
    """Classified outcome only; raw venue messages have no storage field."""

    outcome: Literal["UNKNOWN", "NOT_SENT", "ACCEPTED", "REJECTED", "RESOLVED"] | None = None


class TransitionEvidence(DomainModel):
    """Bounded classifications without invented exchange/state-machine semantics."""

    reason: Literal["OBSERVED", "LOCAL_INTENT", "RESPONSE", "RECONCILIATION", "UNKNOWN"] | None = (
        None
    )


_CONTRACTS: dict[str, type[DomainModel]] = {
    "intent": IntentEvidence,
    "event": EventEvidence,
    "transition": TransitionEvidence,
}
_OPERATIONS = {"intent": OPERATIONS, "event": ATTEMPT_EVENT_KINDS, "transition": TRANSITION_KINDS}


def encode_evidence(
    kind: str,
    operation: str,
    schema_version: int,
    value: object,
) -> tuple[str, str]:
    """Own, validate and hash allowlisted semantic evidence before serialization."""
    try:
        if type(schema_version) is not int or schema_version != 1:
            raise PersistenceValueError()
        if type(operation) is not str or operation not in _OPERATIONS[kind]:
            raise PersistenceValueError()
        contract = _CONTRACTS[kind]
        if isinstance(value, DomainModel) and type(value) is contract:
            value = value.model_dump(mode="python", exclude_none=True)
        if type(value) is not dict:
            raise PersistenceValueError()
        owned = contract.model_validate(value, strict=True)
        raw = owned.model_dump(mode="python", exclude_none=True)
        for key, item in raw.items():
            if type(item) is Decimal:
                raw[key] = _decimal_text(item, 38, 18)
            elif type(item) is UUID:
                raw[key] = str(item)
        payload = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        semantic = json.dumps(
            {"operation": operation, "payload": raw, "schema_version": schema_version},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        if len(payload) > 2048:
            raise PersistenceValueError()
        return payload, sha256(semantic.encode("ascii")).hexdigest()
    except Exception:
        raise PersistenceValueError() from None


def validate_stored_evidence(
    kind: str,
    operation: str,
    schema_version: int,
    payload: str,
    digest: str,
) -> None:
    """Revalidate serialized evidence, restoring only explicitly typed values."""
    try:
        raw = json.loads(payload)
        if type(raw) is not dict:
            raise PersistenceValueError()
        if kind == "intent":
            for key in ("limit_price", "stop_price"):
                if key in raw:
                    raw[key] = Decimal(raw[key])
            if "target_order_id" in raw:
                raw["target_order_id"] = UUID(raw["target_order_id"])
        if encode_evidence(kind, operation, schema_version, raw) != (payload, digest):
            raise PersistenceValueError()
    except Exception:
        raise PersistenceValueError() from None
