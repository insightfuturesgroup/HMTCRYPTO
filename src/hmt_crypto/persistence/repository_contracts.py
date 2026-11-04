"""Frozen copied persistence evidence. None of these DTOs grants dispatch authority."""

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BeforeValidator, ConfigDict, Field, model_validator

from hmt_crypto.domain.models import DomainModel as BaseDomainModel
from hmt_crypto.persistence.execution_evidence import EventEvidence, validate_stored_evidence
from hmt_crypto.persistence.risk_rate_consumption_contracts import (
    NEUTRAL_EVENT_PAYLOAD,
    NEUTRAL_EVENT_SHA256,
)
from hmt_crypto.persistence.risk_rate_consumption_evidence import derive_dispatch_event_key


def _integer(value: object) -> object:
    if type(value) is not int:
        raise ValueError("Expected exact integer")
    return value


def _text(value: object) -> object:
    if type(value) is not str or "\x00" in value:
        raise ValueError("Expected exact text")
    return value


def _utc(value: object) -> object:
    if (
        type(value) is not datetime
        or type(value.tzinfo) not in (timezone, ZoneInfo)
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError("Expected exact UTC datetime")
    return value


PositiveStrictInt = Annotated[int, Field(strict=True, ge=1, lt=2**63), BeforeValidator(_integer)]
NonNegativeStrictInt = Annotated[int, Field(strict=True, ge=0, lt=2**63), BeforeValidator(_integer)]
NulFreeBoundedString128 = Annotated[
    str, Field(strict=True, min_length=1, max_length=128), BeforeValidator(_text)
]
Sha256Hex = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$"), BeforeValidator(_text)]
UtcDateTime = Annotated[datetime, BeforeValidator(_utc)]

EVENT_OUTCOMES = {
    "DEFINITE_NOT_SENT": "NOT_SENT",
    "RESPONSE_ACCEPTED": "ACCEPTED",
    "RESPONSE_REJECTED": "REJECTED",
    "UNCERTAIN": "UNKNOWN",
    "RECONCILED": "RESOLVED",
    "SIMULATED_ACCEPTED": "ACCEPTED",
    "SIMULATED_REJECTED": "REJECTED",
}


class DomainModel(BaseDomainModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    @model_validator(mode="before")
    @classmethod
    def exact_types(cls, values: object) -> object:
        if type(values) is dict:
            for key, value in values.items():
                if type(key) is not str:
                    raise ValueError("Invalid repository field name")
                if key == "evidence":
                    if type(value) is EventEvidence:
                        raw_evidence = vars(value)
                    elif type(value) is dict:
                        raw_evidence = value
                    else:
                        raise ValueError("Non-exact event evidence")
                    if (
                        len(raw_evidence) != 1
                        or any(type(k) is not str or k != "outcome" for k in raw_evidence)
                        or type(raw_evidence.get("outcome")) is not str
                    ):
                        raise ValueError("Invalid nested event outcome")
                    values = {**values, "evidence": {"outcome": raw_evidence["outcome"]}}

                if isinstance(value, (int, str, UUID, datetime)) and type(value) not in (
                    int,
                    str,
                    UUID,
                    datetime,
                    EnqueueDisposition,
                    EventDisposition,
                ):
                    raise ValueError("Non-exact field")
                if key == "evidence_schema_version" and type(value) is not int:
                    raise ValueError("Non-exact version")
        return values

    @model_validator(mode="after")
    def stored_shapes(self) -> Self:
        v = self.__dict__
        for ref, gen in (
            ("leader_ref", "leader_generation"),
            ("recovery_ref", "recovery_generation"),
            ("original_leader_ref", "original_leader_generation"),
            ("original_recovery_ref", "original_recovery_generation"),
            ("ingester_id", "ingester_generation"),
            ("ingester_leader_ref", "ingester_leader_generation"),
            ("ingester_recovery_ref", "ingester_recovery_generation"),
        ):
            if ref in v and (v[ref] is None) != (v[gen] is None):
                raise ValueError("Incomplete pair")
        if "evidence_payload" in v:
            kind = "event" if "event_kind" in v else "intent"
            operation = v["event_kind"] if "event_kind" in v else v["operation_kind"]
            validate_stored_evidence(
                kind,
                operation,
                v["evidence_schema_version"],
                v["evidence_payload"],
                v["evidence_sha256"],
            )
        if "event_kind" in v:
            import json

            outcome = (
                v["evidence"].outcome
                if "evidence" in v
                else json.loads(v["evidence_payload"]).get("outcome")
            )
            if v["event_kind"] == "DISPATCH_STARTED" and (
                v["sequence"] != 1
                or v["event_key"] != derive_dispatch_event_key(v["attempt_id"])
                or v["evidence_payload"] != NEUTRAL_EVENT_PAYLOAD
                or v["evidence_sha256"] != NEUTRAL_EVENT_SHA256
                or any(
                    value is not None
                    for key, value in v.items()
                    if key.startswith("ingester_") or key in ("observed_at", "reconciliation_ref")
                )
            ):
                raise ValueError("Invalid stored dispatch start")
            if v["event_kind"] != "DISPATCH_STARTED" and outcome != EVENT_OUTCOMES[v["event_kind"]]:
                raise ValueError("Event outcome mismatch")
            if v["event_kind"] == "RECONCILED" and (
                v["ingester_id"] is None or v["reconciliation_ref"] is None
            ):
                raise ValueError("Missing reconciliation evidence")
            if v.get("backend") is not None:
                if (
                    v["event_kind"] in ("RESPONSE_ACCEPTED", "RESPONSE_REJECTED")
                    and v["backend"] != "VENUE"
                ):
                    raise ValueError("Backend mismatch")
                if (
                    v["event_kind"] in ("SIMULATED_ACCEPTED", "SIMULATED_REJECTED")
                    and v["backend"] != "SIMULATOR"
                ):
                    raise ValueError("Backend mismatch")
            if (
                "recorded_at" in v
                and v["observed_at"] is not None
                and v["observed_at"] > v["recorded_at"]
            ):
                raise ValueError("Observation is later than recording")
        if "created_at" in v and v["updated_at"] < v["created_at"]:
            raise ValueError("Invalid time order")
        if "state" in v:
            held = v["state"] == "HELD"
            if any((v[k] is not None) != held for k in ("owner_id", "heartbeat_at", "expires_at")):
                raise ValueError("Invalid ownership shape")
            if held and (
                v["generation"] < 1
                or not v["created_at"] <= v["heartbeat_at"] <= v["updated_at"]
                or v["expires_at"] <= v["heartbeat_at"]
            ):
                raise ValueError("Invalid lease times")
        if "status" in v:
            terminal = v["status"] in ("FAILED", "CANCELLED", "COMPLETED")
            if (v["completed_at"] is not None) != terminal:
                raise ValueError("Invalid completion shape")
            if terminal and not v["created_at"] <= v["completed_at"] <= v["updated_at"]:
                raise ValueError("Invalid completion time")
            claimed = v["status"] == "CLAIMED"
            if any((v[k] is not None) != claimed for k in ("claim_owner", "claim_expires_at")):
                raise ValueError("Invalid claim shape")
            if claimed and (v["claim_generation"] < 1 or v["claim_expires_at"] <= v["updated_at"]):
                raise ValueError("Invalid claim times")
            if v["available_at"] < v["created_at"]:
                raise ValueError("Invalid availability")
        if "prepared_at" in v:
            if v["recorded_at"] < v["prepared_at"]:
                raise ValueError("Invalid preparation times")
            live = v["scope"].mode == "LIVE"
            if live:
                if (
                    v["backend"] != "VENUE"
                    or v["simulator_kind"] is not None
                    or any(
                        v[k] is None
                        for k in (
                            "credential_version",
                            "original_claim_owner",
                            "original_claim_generation",
                            "original_claim_expires_at",
                            "original_leader_ref",
                            "original_recovery_ref",
                        )
                    )
                ):
                    raise ValueError("Invalid venue provenance")
                if v["original_claim_expires_at"] <= v["prepared_at"]:
                    raise ValueError("Invalid original claim expiry")
            elif (
                v["backend"] != "SIMULATOR"
                or v["simulator_kind"] != v["scope"].mode
                or any(
                    value is not None
                    for key, value in v.items()
                    if key.startswith("original_") or key == "credential_version"
                )
            ):
                raise ValueError("Invalid simulator provenance")
        return self


OperationKind = Literal[
    "PLACE_ORDER",
    "MODIFY_ORDER",
    "CANCEL_ORDER",
    "SET_LEVERAGE",
    "SET_MARGIN_MODE",
    "CHANGE_POSITION_MARGIN",
]
PreAttemptTerminalStatus = Literal["FAILED", "CANCELLED"]
StoredAttemptEventKind = Literal[
    "DEFINITE_NOT_SENT",
    "DISPATCH_STARTED",
    "RESPONSE_ACCEPTED",
    "RESPONSE_REJECTED",
    "UNCERTAIN",
    "RECONCILED",
    "SIMULATED_ACCEPTED",
    "SIMULATED_REJECTED",
]
E1AppendEventKind = Literal[
    "DEFINITE_NOT_SENT",
    "RESPONSE_ACCEPTED",
    "RESPONSE_REJECTED",
    "UNCERTAIN",
    "RECONCILED",
    "SIMULATED_ACCEPTED",
    "SIMULATED_REJECTED",
]


class AccountScope(DomainModel):
    account_id: UUID
    region: Literal["india", "global"]
    environment: Literal["testnet", "prod"]
    mode: Literal["BACKTEST", "PAPER", "LIVE"]


class LeadershipFence(DomainModel):
    lease_id: UUID
    recovery_incarnation_id: UUID
    owner_id: UUID
    generation: PositiveStrictInt
    version: PositiveStrictInt
    expires_at: UtcDateTime


class CommandClaim(DomainModel):
    command_id: UUID
    owner_id: UUID
    generation: PositiveStrictInt
    version: PositiveStrictInt
    expires_at: UtcDateTime
    leader_ref: UUID | None
    leader_generation: PositiveStrictInt | None
    recovery_ref: UUID | None
    recovery_generation: PositiveStrictInt | None


class CommandCorrelation(DomainModel):
    correlation_id: UUID


class LeaseSnapshot(DomainModel):
    scope: AccountScope
    lease_id: UUID
    recovery_incarnation_id: UUID
    state: Literal["UNOWNED", "HELD"]
    owner_id: UUID | None
    generation: NonNegativeStrictInt
    heartbeat_at: UtcDateTime | None
    expires_at: UtcDateTime | None
    version: PositiveStrictInt
    created_at: UtcDateTime
    updated_at: UtcDateTime


class CommandReceipt(DomainModel):
    scope: AccountScope
    command_id: UUID
    request_id: NulFreeBoundedString128
    operation_kind: OperationKind
    correlation_id: UUID
    status: Literal["QUEUED", "CLAIMED", "COMPLETED", "FAILED", "CANCELLED"]
    available_at: UtcDateTime
    claim_owner: UUID | None
    claim_generation: NonNegativeStrictInt
    claim_expires_at: UtcDateTime | None
    attempt_count: NonNegativeStrictInt
    leader_ref: UUID | None
    leader_generation: PositiveStrictInt | None
    recovery_ref: UUID | None
    recovery_generation: PositiveStrictInt | None
    version: PositiveStrictInt
    created_at: UtcDateTime
    updated_at: UtcDateTime
    completed_at: UtcDateTime | None
    evidence_schema_version: Literal[1]
    evidence_payload: str
    evidence_sha256: Sha256Hex


class AttemptReceipt(DomainModel):
    scope: AccountScope
    attempt_id: UUID
    command_id: UUID
    order_id: UUID | None
    attempt_number: PositiveStrictInt
    operation_kind: OperationKind
    backend: Literal["VENUE", "SIMULATOR"]
    simulator_kind: Literal["PAPER", "BACKTEST"] | None
    credential_version: PositiveStrictInt | None
    correlation_id: UUID
    prepared_at: UtcDateTime
    original_claim_owner: UUID | None
    original_claim_generation: PositiveStrictInt | None
    original_claim_expires_at: UtcDateTime | None
    original_leader_ref: UUID | None
    original_leader_generation: PositiveStrictInt | None
    original_recovery_ref: UUID | None
    original_recovery_generation: PositiveStrictInt | None
    recorded_at: UtcDateTime
    evidence_schema_version: Literal[1]
    evidence_payload: str
    evidence_sha256: Sha256Hex


class AttemptEventReceipt(DomainModel):
    scope: AccountScope
    event_id: UUID
    attempt_id: UUID
    backend: Literal["VENUE", "SIMULATOR"]
    sequence: PositiveStrictInt
    event_key: NulFreeBoundedString128
    event_kind: StoredAttemptEventKind
    observed_at: UtcDateTime | None
    ingester_id: UUID | None
    ingester_generation: PositiveStrictInt | None
    reconciliation_ref: UUID | None
    ingester_leader_ref: UUID | None
    ingester_leader_generation: PositiveStrictInt | None
    ingester_recovery_ref: UUID | None
    ingester_recovery_generation: PositiveStrictInt | None
    recorded_at: UtcDateTime
    evidence_schema_version: Literal[1]
    evidence_payload: str
    evidence_sha256: Sha256Hex


class AttemptEventProposal(DomainModel):
    event_key: NulFreeBoundedString128
    event_kind: E1AppendEventKind
    evidence: EventEvidence
    observed_at: UtcDateTime | None = None
    ingester_id: UUID | None = None
    ingester_generation: PositiveStrictInt | None = None
    reconciliation_ref: UUID | None = None
    ingester_leader_ref: UUID | None = None
    ingester_leader_generation: PositiveStrictInt | None = None
    ingester_recovery_ref: UUID | None = None
    ingester_recovery_generation: PositiveStrictInt | None = None


class EnqueueDisposition(StrEnum):
    CREATED = "CREATED"
    EXISTING = "EXISTING"


class EventDisposition(StrEnum):
    APPENDED = "APPENDED"
    EXISTING = "EXISTING"


class EnqueueResult(DomainModel):
    disposition: EnqueueDisposition
    command: CommandReceipt


class ClaimResult(DomainModel):
    command: CommandReceipt
    claim: CommandClaim


class AttemptAllocationResult(DomainModel):
    command: CommandReceipt
    claim: CommandClaim
    attempt: AttemptReceipt


class EventAppendResult(DomainModel):
    disposition: EventDisposition
    event: AttemptEventReceipt
