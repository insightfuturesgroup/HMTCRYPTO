"""Strict frozen DTOs and scalar validators for reservation evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, NoReturn, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.types import _decimal_text, _utc

_HASH_CHARS = frozenset("0123456789abcdef")


def _fail() -> NoReturn:
    raise PersistenceValueError()


def _uuid(value: object, *, optional: bool = False) -> UUID | None:
    if value is None and optional:
        return None
    if type(value) is not UUID or value.int == 0:
        _fail()
    return value


def _integer(value: object, *, positive: bool = True, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value < 2**63:
        _fail()
    return value


def _text(value: object, *, maximum: int = 128) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or not value.strip()
        or "\x00" in value
    ):
        _fail()
    return value


def _enum(value: object, allowed: frozenset[str] | tuple[str, ...]) -> str:
    if type(value) is not str or value not in allowed:
        _fail()
    return value


def _hash(value: object) -> str:
    if type(value) is not str or len(value) != 64 or not set(value) <= _HASH_CHARS:
        _fail()
    return value


def _time(value: object, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    return _utc(value)


def _decimal(value: object, *, nonnegative: bool = True) -> Decimal:
    result = Decimal(_decimal_text(value, 38, 18))
    if nonnegative and result < 0:
        _fail()
    return result


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat(timespec="microseconds")


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


class AttemptEventEvidenceV1(_OwnedModel):
    """One complete immutable attempt-event source row."""

    id: UUID
    account_id: UUID
    region: str
    environment: str
    mode: str
    attempt_id: UUID
    backend: str
    sequence: int
    event_key: str
    event_kind: str
    observed_at: datetime | None
    recorded_at: datetime
    ingester_id: UUID | None
    ingester_generation: int | None
    reconciliation_ref: UUID | None
    ingester_leader_ref: UUID | None
    ingester_leader_generation: int | None
    ingester_recovery_ref: UUID | None
    ingester_recovery_generation: int | None
    evidence_schema_version: int
    evidence_payload: str
    evidence_sha256: str

    @field_validator("id", "account_id", "attempt_id", mode="before")
    @classmethod
    def _required_uuid(cls, value: object) -> UUID:
        result = _uuid(value)
        assert result is not None
        return result

    @field_validator(
        "ingester_id",
        "reconciliation_ref",
        "ingester_leader_ref",
        "ingester_recovery_ref",
        mode="before",
    )
    @classmethod
    def _optional_uuid(cls, value: object) -> UUID | None:
        return _uuid(value, optional=True)

    @field_validator("sequence", "evidence_schema_version", mode="before")
    @classmethod
    def _positive(cls, value: object) -> int:
        result = _integer(value)
        assert result is not None
        return result

    @field_validator(
        "ingester_generation",
        "ingester_leader_generation",
        "ingester_recovery_generation",
        mode="before",
    )
    @classmethod
    def _optional_positive(cls, value: object) -> int | None:
        return _integer(value, optional=True)

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

    @field_validator("backend", mode="before")
    @classmethod
    def _backend(cls, value: object) -> str:
        return _enum(value, ("VENUE", "SIMULATOR"))

    @field_validator("event_kind", mode="before")
    @classmethod
    def _event_kind(cls, value: object) -> str:
        return _text(value, maximum=32)

    @field_validator("event_key", mode="before")
    @classmethod
    def _event_key(cls, value: object) -> str:
        return _text(value)

    @field_validator("evidence_payload", mode="before")
    @classmethod
    def _payload(cls, value: object) -> str:
        return _text(value, maximum=2048)

    @field_validator("evidence_sha256", mode="before")
    @classmethod
    def _evidence_hash(cls, value: object) -> str:
        return _hash(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _optional_time(cls, value: object) -> datetime | None:
        return _time(value, optional=True)

    @field_validator("recorded_at", mode="before")
    @classmethod
    def _recorded(cls, value: object) -> datetime:
        result = _time(value)
        assert result is not None
        return result


class AttemptEvidenceV1(_OwnedModel):
    """One complete immutable attempt plus its complete event stream."""

    id: UUID
    account_id: UUID
    region: str
    environment: str
    mode: str
    command_id: UUID
    order_id: UUID
    attempt_number: int
    operation_kind: str
    backend: str
    simulator_kind: str | None
    credential_version: int | None
    correlation_id: UUID
    prepared_at: datetime
    recorded_at: datetime
    original_claim_owner: UUID | None
    original_claim_generation: int | None
    original_claim_expires_at: datetime | None
    original_leader_ref: UUID | None
    original_leader_generation: int | None
    original_recovery_ref: UUID | None
    original_recovery_generation: int | None
    evidence_schema_version: int
    evidence_payload: str
    evidence_sha256: str
    events: tuple[AttemptEventEvidenceV1, ...]

    @field_validator("id", "account_id", "command_id", "order_id", "correlation_id", mode="before")
    @classmethod
    def _required_uuid(cls, value: object) -> UUID:
        result = _uuid(value)
        assert result is not None
        return result

    @field_validator(
        "original_claim_owner",
        "original_leader_ref",
        "original_recovery_ref",
        mode="before",
    )
    @classmethod
    def _optional_uuid(cls, value: object) -> UUID | None:
        return _uuid(value, optional=True)

    @field_validator("attempt_number", "evidence_schema_version", mode="before")
    @classmethod
    def _positive(cls, value: object) -> int:
        result = _integer(value)
        assert result is not None
        return result

    @field_validator(
        "credential_version",
        "original_claim_generation",
        "original_leader_generation",
        "original_recovery_generation",
        mode="before",
    )
    @classmethod
    def _optional_positive(cls, value: object) -> int | None:
        return _integer(value, optional=True)

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

    @field_validator("operation_kind", mode="before")
    @classmethod
    def _operation(cls, value: object) -> str:
        return _enum(value, ("PLACE_ORDER", "MODIFY_ORDER"))

    @field_validator("backend", mode="before")
    @classmethod
    def _backend(cls, value: object) -> str:
        return _enum(value, ("VENUE", "SIMULATOR"))

    @field_validator("simulator_kind", mode="before")
    @classmethod
    def _simulator_kind(cls, value: object) -> str | None:
        if value is None:
            return None
        return _enum(value, ("PAPER", "BACKTEST"))

    @field_validator("prepared_at", "recorded_at", mode="before")
    @classmethod
    def _required_time(cls, value: object) -> datetime:
        result = _time(value)
        assert result is not None
        return result

    @field_validator("original_claim_expires_at", mode="before")
    @classmethod
    def _optional_time(cls, value: object) -> datetime | None:
        return _time(value, optional=True)

    @field_validator("evidence_payload", mode="before")
    @classmethod
    def _payload(cls, value: object) -> str:
        return _text(value, maximum=2048)

    @field_validator("evidence_sha256", mode="before")
    @classmethod
    def _evidence_hash(cls, value: object) -> str:
        return _hash(value)

    @field_validator("events", mode="before")
    @classmethod
    def _events(cls, value: object) -> tuple[object, ...]:
        if type(value) not in {list, tuple}:
            _fail()
        return tuple(cast(list[object] | tuple[object, ...], value))


@dataclass(frozen=True)
class AttemptEvidenceSetEncoding:
    """Bounded complete canonical attempt-set evidence."""

    canonical: str
    sha256: str
    observed_command_attempt_count: int
    attempt_count: int
    event_count: int
    _owned_token: object | None = field(default=None, init=False, repr=False, compare=False)


_ATTEMPT_EVIDENCE_SET_OWNED_TOKEN = object()


def _new_attempt_evidence_set_encoding(
    *,
    canonical: str,
    sha256: str,
    observed_command_attempt_count: int,
    attempt_count: int,
    event_count: int,
) -> AttemptEvidenceSetEncoding:
    """Construct an encoder-owned result without exposing an authority token."""
    result = AttemptEvidenceSetEncoding(
        canonical=canonical,
        sha256=sha256,
        observed_command_attempt_count=observed_command_attempt_count,
        attempt_count=attempt_count,
        event_count=event_count,
    )
    object.__setattr__(result, "_owned_token", _ATTEMPT_EVIDENCE_SET_OWNED_TOKEN)
    return result


def _is_owned_attempt_evidence_set_encoding(value: object) -> bool:
    return (
        type(value) is AttemptEvidenceSetEncoding
        and value._owned_token is _ATTEMPT_EVIDENCE_SET_OWNED_TOKEN
    )


@dataclass(frozen=True)
class RiskReservationReleaseEncoding:
    """Canonical stored release proof and persisted sibling values."""

    release_key: str
    payload: str
    sha256: str
    attempt_evidence_set_sha256: str
    observed_command_attempt_count: int
    order_attempt_evidence_count: int
    order_attempt_event_evidence_count: int
    proof_observed_at: datetime
    observed_terminal_command_status: str
    observed_command_version: int
    observed_order_version: int
