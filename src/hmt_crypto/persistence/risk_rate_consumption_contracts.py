"""Strict frozen contracts for immutable local risk-rate consumption evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_reservation_contracts import (
    _enum,
    _hash,
    _integer,
    _text,
    _time,
    _uuid,
)

EVIDENCE_SCHEMA_VERSION = 1
MAX_EVIDENCE_BYTES = 8_192
NEUTRAL_EVENT_PAYLOAD = '{"outcome":"UNKNOWN"}'
NEUTRAL_EVENT_SHA256 = "35cc047ec1e3ebea7d316d457be73a445edbe1aec2fe8eae7965a346e172c5b6"

CONSUMPTION_PAYLOAD_FIELDS = frozenset(
    {
        "account_id",
        "attempt_backend",
        "attempt_evidence_payload",
        "attempt_evidence_schema_version",
        "attempt_evidence_sha256",
        "attempt_number",
        "attempt_prepared_at",
        "attempt_recorded_at",
        "command_id",
        "command_operation_kind",
        "consumed_units",
        "consumption_id",
        "consumption_key",
        "decision_evaluated_at",
        "decision_id",
        "decision_outcome",
        "decision_recorded_at",
        "dispatch_event_evidence_payload",
        "dispatch_event_evidence_schema_version",
        "dispatch_event_evidence_sha256",
        "dispatch_event_id",
        "dispatch_event_key",
        "dispatch_event_kind",
        "dispatch_event_sequence",
        "environment",
        "mode",
        "order_attempt_id",
        "order_id",
        "product_id",
        "rate_policy_sha256",
        "rate_policy_version",
        "rate_window_end_at",
        "rate_window_start_at",
        "recorded_at",
        "region",
        "reservation_created_at",
        "reservation_id",
        "reservation_plan_sha256",
        "risk_effect",
        "risk_state_id",
    }
)
DERIVED_FIELDS = frozenset({"consumption_key", "dispatch_event_key"})
CONSUMPTION_INPUT_FIELDS = CONSUMPTION_PAYLOAD_FIELDS - DERIVED_FIELDS
KEY_INPUT_FIELDS = frozenset(
    {
        "account_id",
        "decision_recorded_at",
        "environment",
        "mode",
        "order_attempt_id",
        "region",
        "reservation_created_at",
        "reservation_id",
    }
)


def _fail() -> NoReturn:
    raise PersistenceValueError()


class _OwnedModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except (ValidationError, PersistenceValueError):
            raise PersistenceValueError() from None


class ConsumptionKeyV1(_OwnedModel):
    """Exact semantic identity inputs; generated row UUIDs are excluded."""

    account_id: UUID
    region: str
    environment: str
    mode: str
    reservation_id: UUID
    decision_recorded_at: datetime
    reservation_created_at: datetime
    order_attempt_id: UUID

    @field_validator("account_id", "reservation_id", "order_attempt_id", mode="before")
    @classmethod
    def _required_uuid(cls, value: object) -> UUID:
        result = _uuid(value)
        assert result is not None
        return result

    @field_validator("region", mode="before")
    @classmethod
    def _region(cls, value: object) -> str:
        return _enum(value, ("india", "global"))

    @field_validator("environment", mode="before")
    @classmethod
    def _environment(cls, value: object) -> str:
        return _enum(value, ("testnet", "prod"))

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, value: object) -> str:
        return _enum(value, ("BACKTEST", "PAPER", "LIVE"))

    @field_validator("decision_recorded_at", "reservation_created_at", mode="before")
    @classmethod
    def _utc_time(cls, value: object) -> datetime:
        result = _time(value)
        assert result is not None
        return result


class RiskRateConsumptionInputV1(ConsumptionKeyV1):
    """Complete caller inputs before derived keys and canonical evidence are owned."""

    consumption_id: UUID
    decision_id: UUID
    risk_state_id: UUID
    command_id: UUID
    command_operation_kind: str
    order_id: UUID
    product_id: int
    decision_outcome: str
    risk_effect: str
    reservation_plan_sha256: str
    decision_evaluated_at: datetime
    attempt_number: int
    attempt_backend: str
    attempt_prepared_at: datetime
    attempt_recorded_at: datetime
    attempt_evidence_schema_version: int
    attempt_evidence_payload: str
    attempt_evidence_sha256: str
    dispatch_event_id: UUID
    dispatch_event_sequence: int
    dispatch_event_kind: str
    dispatch_event_evidence_schema_version: int
    dispatch_event_evidence_payload: str
    dispatch_event_evidence_sha256: str
    rate_policy_version: int
    rate_policy_sha256: str
    rate_window_start_at: datetime
    rate_window_end_at: datetime
    consumed_units: int
    recorded_at: datetime

    @field_validator(
        "consumption_id",
        "decision_id",
        "risk_state_id",
        "command_id",
        "order_id",
        "dispatch_event_id",
        mode="before",
    )
    @classmethod
    def _more_uuids(cls, value: object) -> UUID:
        result = _uuid(value)
        assert result is not None
        return result

    @field_validator("command_operation_kind", mode="before")
    @classmethod
    def _operation(cls, value: object) -> str:
        return _enum(value, ("PLACE_ORDER", "MODIFY_ORDER"))

    @field_validator("decision_outcome", mode="before")
    @classmethod
    def _outcome(cls, value: object) -> str:
        return _enum(value, ("APPROVED",))

    @field_validator("risk_effect", mode="before")
    @classmethod
    def _effect(cls, value: object) -> str:
        return _enum(value, ("RISK_INCREASE", "REDUCE_ONLY"))

    @field_validator("attempt_backend", mode="before")
    @classmethod
    def _backend(cls, value: object) -> str:
        return _enum(value, ("VENUE", "SIMULATOR"))

    @field_validator("dispatch_event_kind", mode="before")
    @classmethod
    def _event_kind(cls, value: object) -> str:
        return _enum(value, ("DISPATCH_STARTED",))

    @field_validator(
        "product_id",
        "attempt_number",
        "attempt_evidence_schema_version",
        "dispatch_event_sequence",
        "dispatch_event_evidence_schema_version",
        "rate_policy_version",
        "consumed_units",
        mode="before",
    )
    @classmethod
    def _positive_integer(cls, value: object) -> int:
        result = _integer(value)
        assert result is not None
        return result

    @field_validator(
        "reservation_plan_sha256",
        "attempt_evidence_sha256",
        "dispatch_event_evidence_sha256",
        "rate_policy_sha256",
        mode="before",
    )
    @classmethod
    def _digest(cls, value: object) -> str:
        return _hash(value)

    @field_validator("attempt_evidence_payload", mode="before")
    @classmethod
    def _attempt_payload(cls, value: object) -> str:
        return _text(value, maximum=2048)

    @field_validator("dispatch_event_evidence_payload", mode="before")
    @classmethod
    def _event_payload(cls, value: object) -> str:
        return _text(value, maximum=2048)

    @field_validator(
        "decision_evaluated_at",
        "attempt_prepared_at",
        "attempt_recorded_at",
        "rate_window_start_at",
        "rate_window_end_at",
        "recorded_at",
        mode="before",
    )
    @classmethod
    def _more_times(cls, value: object) -> datetime:
        result = _time(value)
        assert result is not None
        return result


@dataclass(frozen=True)
class DispatchStartedEvidenceEncoding:
    """Fixed neutral first-event semantic evidence."""

    payload: str
    sha256: str


@dataclass(frozen=True)
class RiskRateConsumptionEncoding:
    """Owned canonical payload and semantic identities."""

    consumption_key: str
    dispatch_event_key: str
    payload: str
    sha256: str


def require_exact_fields(value: object, fields: frozenset[str]) -> dict[str, object]:
    """Own an exact plain dictionary without accepting mapping subclasses."""
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != fields
    ):
        _fail()
    return dict(value)
