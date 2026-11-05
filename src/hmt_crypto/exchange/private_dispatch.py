"""Exchange-neutral, inert private-dispatch evidence declarations."""

from __future__ import annotations

from typing import Literal, NoReturn, Protocol, Self, SupportsIndex, TypeAlias, cast
from uuid import UUID

from pydantic import field_validator, model_validator

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.domain.private_primitives import (
    ExactAwareUtcDatetime,
    ExactUuid,
    PrivateBoundaryModel,
    Sha256Hex,
    StrictEpoch,
    StrictNonNegativeInt,
    StrictPositiveInt,
    StrictPositivePid,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

TrustedDispatchExpirySource: TypeAlias = Literal[
    "RATE_RISK_WINDOW",
    "COMMAND_CLAIM",
    "LEADERSHIP",
    "CATALOGUE",
    "PRICE",
    "RECONCILIATION",
    "CLOCK_READINESS",
    "CREDENTIAL_PROBE",
    "TESTNET_ADMISSION",
]

VenueDispatchPreEntryPhase: TypeAlias = Literal[
    "ISSUED",
    "CLAIMED_FOR_UNSIGNED_PREPARATION",
    "AUDIT_RECORDED",
]

VenueNoEntryReason: TypeAlias = Literal[
    "SINK_BINDING_MISMATCH",
    "PREPARATION_FAILED",
    "AUDIT_FAILED",
    "CURRENT_INVALIDATED",
    "DEADLINE_EXPIRED",
    "SIGNING_FAILED",
    "SIGNATURE_EXPIRED",
    "LIMITER_INVALIDATED",
    "CANCELLED",
    "INTERNAL_EXCEPTION",
]

_EXPIRY_SOURCES = (
    "RATE_RISK_WINDOW",
    "COMMAND_CLAIM",
    "LEADERSHIP",
    "CATALOGUE",
    "PRICE",
    "RECONCILIATION",
    "CLOCK_READINESS",
    "CREDENTIAL_PROBE",
    "TESTNET_ADMISSION",
)
_POLICY_VERSION = "delta-docs-2026-11-04-private-v1"
_PRIVATE_DISPATCH_FIXTURE_MARKER = object()


def _is_exact_text(value: object, expected: tuple[str, ...]) -> bool:
    return type(value) is str and any(str.__eq__(value, candidate) for candidate in expected)


class TrustedDispatchInstant(PrivateBoundaryModel):
    """One prewarmed process-bound monotonic/UTC correlation observation."""

    process_id: StrictPositivePid
    process_incarnation_id: ExactUuid
    clock_epoch: StrictEpoch
    correlation_epoch: StrictEpoch
    monotonic_ns: StrictNonNegativeInt
    utc_epoch_seconds: StrictNonNegativeInt
    utc_upper_bound_ns: StrictNonNegativeInt

    @model_validator(mode="after")
    def require_conservative_utc_upper_bound(self) -> Self:
        lower_bound_ns = self.utc_epoch_seconds * 1_000_000_000
        if self.utc_upper_bound_ns < lower_bound_ns:
            raise type(self)._safe_error(
                "private_model_value", "invalid private model value"
            ) from None
        return self


class TrustedUtcExpiry(PrivateBoundaryModel):
    """One exact UTC expiry and its closed readiness source."""

    expires_at: ExactAwareUtcDatetime
    source: TrustedDispatchExpirySource

    @field_validator("source", mode="before")
    @classmethod
    def require_exact_source(cls, value: object) -> object:
        if not _is_exact_text(value, _EXPIRY_SOURCES):
            raise ValueError("invalid trusted expiry source")
        return value


class TrustedDispatchDeadline(PrivateBoundaryModel):
    """One process- and epoch-bound monotonic dispatch deadline."""

    process_id: StrictPositivePid
    process_incarnation_id: ExactUuid
    clock_epoch: StrictEpoch
    monotonic_ns: StrictNonNegativeInt
    correlation_epoch: StrictEpoch


class TrustedDispatchClock(Protocol):
    """Typing vocabulary only; Task 2.10 owns the exact runtime clock."""

    def derive_deadline_nonblocking(
        self,
        *,
        utc_expiries: tuple[TrustedUtcExpiry, ...],
        monotonic_deadlines_ns: tuple[StrictNonNegativeInt, ...],
        expected_process_incarnation_id: ExactUuid,
        expected_clock_epoch: StrictEpoch,
    ) -> TrustedDispatchDeadline: ...

    def observe_nonblocking(
        self,
        *,
        expected_process_incarnation_id: ExactUuid,
        expected_clock_epoch: StrictEpoch,
    ) -> TrustedDispatchInstant: ...


class VenueDispatchAuditIdentity(PrivateBoundaryModel):
    """Strict copied audit identity for one future place/modify entry."""

    account: AccountRef
    command_id: ExactUuid
    order_id: ExactUuid
    order_attempt_id: ExactUuid
    root_event_id: ExactUuid
    logical_call_id: ExactUuid
    correlation_id: ExactUuid
    order_attempt_number: StrictPositiveInt
    dispatch_event_id: ExactUuid
    consumption_id: ExactUuid
    operation: Literal["PLACE_ORDER", "MODIFY_ORDER"]
    route_id: Literal[PrivateRouteId.PLACE_ORDER, PrivateRouteId.MODIFY_ORDER]
    policy_version: Literal["delta-docs-2026-11-04-private-v1"]
    semantic_request_sha256: Sha256Hex
    command_evidence_sha256: Sha256Hex

    @field_validator("operation", mode="before")
    @classmethod
    def require_exact_operation(cls, value: object) -> object:
        if not _is_exact_text(value, ("PLACE_ORDER", "MODIFY_ORDER")):
            raise ValueError("invalid private dispatch operation")
        return value

    @field_validator("route_id", mode="before")
    @classmethod
    def require_exact_route(cls, value: object) -> object:
        if value is not PrivateRouteId.PLACE_ORDER and value is not PrivateRouteId.MODIFY_ORDER:
            raise ValueError("invalid private dispatch route")
        return value

    @field_validator("policy_version", mode="before")
    @classmethod
    def require_exact_policy(cls, value: object) -> object:
        if not _is_exact_text(value, (_POLICY_VERSION,)):
            raise ValueError("invalid private dispatch policy")
        return value

    @model_validator(mode="after")
    def require_owned_dispatch_identity(self) -> Self:
        account = self.account
        valid_target = (
            account.region is DeltaRegion.INDIA
            and account.environment is DeltaEnvironment.TESTNET
            and account.mode is ExecutionMode.LIVE
        )
        route_matches = (
            self.operation == "PLACE_ORDER" and self.route_id is PrivateRouteId.PLACE_ORDER
        ) or (self.operation == "MODIFY_ORDER" and self.route_id is PrivateRouteId.MODIFY_ORDER)
        if not valid_target or not route_matches or self.logical_call_id != self.order_attempt_id:
            raise type(self)._safe_error(
                "private_model_value", "invalid private model value"
            ) from None
        return self


_DECLARATIONS_CLOSED = False


class _OpaqueDispatchDeclaration:
    __slots__ = ()

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private dispatch declaration unavailable")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _DECLARATIONS_CLOSED:
            raise TypeError("private dispatch declarations cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("private dispatch declarations are immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("private dispatch declarations are immutable")

    def __copy__(self) -> NoReturn:
        raise TypeError("private dispatch declarations cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("private dispatch declarations cannot be copied")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private dispatch declarations cannot be serialized")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private dispatch declarations cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("private dispatch declarations cannot be serialized")

    def __hash__(self) -> NoReturn:
        raise TypeError("private dispatch declarations are equality-free")

    def __eq__(self, _other: object) -> NoReturn:
        raise TypeError("private dispatch declarations are equality-free")

    def __repr__(self) -> str:
        return cast(str, type.__getattribute__(type(self), "__name__"))

    __str__ = __repr__


class _VenueDispatchOutcomeSlot(_OpaqueDispatchDeclaration):
    __slots__ = ("_audit_identity", "_entry_latch")


class _VenueDispatchEntryLatch(_OpaqueDispatchDeclaration):
    __slots__ = ("_owner",)


class _EnteredVenueDispatch(_OpaqueDispatchDeclaration):
    __slots__ = ()


def _disposition_value(owner: object, slot: str) -> object:
    try:
        return object.__getattribute__(owner, slot)
    except AttributeError:
        raise TypeError("private dispatch declaration unavailable") from None


class _VenueNoEntryDisposition(_OpaqueDispatchDeclaration):
    __slots__ = (
        "_audit_identity",
        "_last_phase",
        "_pre_dispatch_audit_receipt_id",
        "_reason",
        "_wire_request_evidence_sha256",
    )

    @property
    def audit_identity(self) -> VenueDispatchAuditIdentity:
        return cast(VenueDispatchAuditIdentity, _disposition_value(self, "_audit_identity"))

    @property
    def last_phase(self) -> VenueDispatchPreEntryPhase:
        return cast(VenueDispatchPreEntryPhase, _disposition_value(self, "_last_phase"))

    @property
    def reason(self) -> VenueNoEntryReason:
        return cast(VenueNoEntryReason, _disposition_value(self, "_reason"))

    @property
    def wire_request_evidence_sha256(self) -> Sha256Hex | None:
        return cast(
            Sha256Hex | None,
            _disposition_value(self, "_wire_request_evidence_sha256"),
        )

    @property
    def pre_dispatch_audit_receipt_id(self) -> UUID | None:
        return cast(UUID | None, _disposition_value(self, "_pre_dispatch_audit_receipt_id"))


_DECLARATIONS_CLOSED = True


def _private_dispatch_fixture_marker_for_tests() -> object:
    return _PRIVATE_DISPATCH_FIXTURE_MARKER


def _issue_venue_dispatch_outcome_slot_for_tests(
    marker: object,
    **audit_values: object,
) -> _VenueDispatchOutcomeSlot:
    if marker is not _PRIVATE_DISPATCH_FIXTURE_MARKER:
        raise TypeError("private dispatch fixture authority unavailable")
    audit = VenueDispatchAuditIdentity.model_validate(audit_values, strict=True)
    slot = object.__new__(_VenueDispatchOutcomeSlot)
    latch = object.__new__(_VenueDispatchEntryLatch)
    object.__setattr__(slot, "_audit_identity", audit)
    object.__setattr__(slot, "_entry_latch", latch)
    object.__setattr__(latch, "_owner", slot)
    return slot


def _venue_dispatch_outcome_audit_identity(
    slot: _VenueDispatchOutcomeSlot,
) -> VenueDispatchAuditIdentity:
    if type(slot) is not _VenueDispatchOutcomeSlot:
        raise TypeError("private dispatch outcome slot unavailable")
    try:
        audit = object.__getattribute__(slot, "_audit_identity")
    except AttributeError:
        raise TypeError("private dispatch outcome slot unavailable") from None
    if type(audit) is not VenueDispatchAuditIdentity:
        raise TypeError("private dispatch outcome slot unavailable")
    return audit


def _venue_dispatch_outcome_entry_latch(
    slot: _VenueDispatchOutcomeSlot,
) -> _VenueDispatchEntryLatch:
    if type(slot) is not _VenueDispatchOutcomeSlot:
        raise TypeError("private dispatch outcome slot unavailable")
    try:
        latch = object.__getattribute__(slot, "_entry_latch")
    except AttributeError:
        raise TypeError("private dispatch outcome slot unavailable") from None
    if type(latch) is not _VenueDispatchEntryLatch:
        raise TypeError("private dispatch outcome slot unavailable")
    if _venue_dispatch_entry_latch_owner(latch) is not slot:
        raise TypeError("private dispatch outcome slot unavailable")
    return latch


def _venue_dispatch_entry_latch_owner(
    latch: _VenueDispatchEntryLatch,
) -> _VenueDispatchOutcomeSlot:
    if type(latch) is not _VenueDispatchEntryLatch:
        raise TypeError("private dispatch entry latch unavailable")
    try:
        owner = object.__getattribute__(latch, "_owner")
    except AttributeError:
        raise TypeError("private dispatch entry latch unavailable") from None
    if type(owner) is not _VenueDispatchOutcomeSlot:
        raise TypeError("private dispatch entry latch unavailable")
    try:
        linked = object.__getattribute__(owner, "_entry_latch")
    except AttributeError:
        raise TypeError("private dispatch entry latch unavailable") from None
    if linked is not latch:
        raise TypeError("private dispatch entry latch unavailable")
    return owner
