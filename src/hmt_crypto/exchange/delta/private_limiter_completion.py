"""Sealed private limiter-completion records and registry-owned lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NoReturn, Protocol, Self, SupportsIndex, cast
from uuid import UUID

from pydantic import field_validator, model_validator

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.domain.private_primitives import (
    ExactStrEnum,
    ExactUuid,
    PrivateBoundaryModel,
    PrivateId,
    Sha256Hex,
    StrictNonNegativeInt,
    StrictPositiveInt,
    StrictPositivePid,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.private_dispatch import (
    TrustedDispatchDeadline,
    TrustedDispatchInstant,
    VenueDispatchAuditIdentity,
    VenueDispatchPreEntryPhase,
    VenueNoEntryReason,
    _EnteredVenueDispatch,
    _VenueDispatchEntryLatch,
    _VenueDispatchOutcomeSlot,
    _VenueNoEntryDisposition,
)

if TYPE_CHECKING:
    from hmt_crypto.exchange.delta.private_limiter_lifecycle_store import (
        _PrivateLimiterLifecycleStore,
    )
    from hmt_crypto.exchange.delta.private_limiter_owner import (
        _PrivateLimiterOwnerCapability,
    )
    from hmt_crypto.worker.clock import (
        Task210TrustedClockCurrentnessPort,
        Task210TrustedDispatchClock,
    )

_POLICY_VERSION = "delta-docs-2026-11-04-private-v1"


def _is_exact_text(value: object, expected: tuple[str, ...]) -> bool:
    return type(value) is str and any(str.__eq__(value, candidate) for candidate in expected)


def _require_exact_route(value: object) -> object:
    if value is not PrivateRouteId.PLACE_ORDER and value is not PrivateRouteId.MODIFY_ORDER:
        raise ValueError("invalid limiter wait route")
    return value


class LimiterWaitIntent(PrivateBoundaryModel):
    """Immutable non-authorizing intent for one future place/modify wait."""

    account: AccountRef
    command_id: ExactUuid
    order_id: ExactUuid
    order_attempt_id: ExactUuid
    order_attempt_number: StrictPositiveInt
    root_event_id: ExactUuid
    logical_call_id: ExactUuid
    correlation_id: ExactUuid
    operation: Literal["PLACE_ORDER", "MODIFY_ORDER"]
    route_id: Literal[PrivateRouteId.PLACE_ORDER, PrivateRouteId.MODIFY_ORDER]
    product_id: PrivateId
    rest_weight: Literal[5]
    product_operations: Literal[1]
    policy_version: Literal["delta-docs-2026-11-04-private-v1"]
    semantic_request_sha256: Sha256Hex
    command_evidence_sha256: Sha256Hex
    process_id: StrictPositivePid
    process_incarnation_id: ExactUuid

    @field_validator("operation", mode="before")
    @classmethod
    def require_exact_operation(cls, value: object) -> object:
        if not _is_exact_text(value, ("PLACE_ORDER", "MODIFY_ORDER")):
            raise ValueError("invalid limiter wait operation")
        return value

    @field_validator("route_id", mode="before")
    @classmethod
    def require_exact_route(cls, value: object) -> object:
        return _require_exact_route(value)

    @field_validator("rest_weight", mode="before")
    @classmethod
    def require_exact_rest_weight(cls, value: object) -> object:
        if type(value) is not int or value != 5:
            raise ValueError("invalid limiter wait REST weight")
        return value

    @field_validator("product_operations", mode="before")
    @classmethod
    def require_exact_product_operations(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("invalid limiter product operation count")
        return value

    @field_validator("policy_version", mode="before")
    @classmethod
    def require_exact_policy(cls, value: object) -> object:
        if not _is_exact_text(value, (_POLICY_VERSION,)):
            raise ValueError("invalid limiter wait policy")
        return value

    @model_validator(mode="after")
    def require_owned_wait_identity(self) -> Self:
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


def _audit_matches_intent(audit: object, intent: object) -> bool:
    if type(audit) is not VenueDispatchAuditIdentity or type(intent) is not LimiterWaitIntent:
        return False
    ids = (
        audit.command_id,
        audit.order_id,
        audit.order_attempt_id,
        audit.root_event_id,
        audit.logical_call_id,
        audit.correlation_id,
        audit.dispatch_event_id,
        audit.consumption_id,
    )
    if (
        any(type(identity) is not UUID or identity.int == 0 for identity in ids)
        or type(audit.account) is not AccountRef
        or type(audit.order_attempt_number) is not int
        or audit.order_attempt_number <= 0
        or not _is_exact_text(audit.operation, ("PLACE_ORDER", "MODIFY_ORDER"))
        or audit.route_id not in (PrivateRouteId.PLACE_ORDER, PrivateRouteId.MODIFY_ORDER)
        or not _is_exact_text(audit.policy_version, (_POLICY_VERSION,))
        or type(audit.semantic_request_sha256) is not str
        or len(audit.semantic_request_sha256) != 64
        or type(audit.command_evidence_sha256) is not str
        or len(audit.command_evidence_sha256) != 64
    ):
        return False
    matching = (
        "account",
        "command_id",
        "order_id",
        "order_attempt_id",
        "root_event_id",
        "logical_call_id",
        "correlation_id",
        "order_attempt_number",
        "operation",
        "route_id",
        "policy_version",
        "semantic_request_sha256",
        "command_evidence_sha256",
    )
    return all(
        object.__getattribute__(audit, name) == object.__getattribute__(intent, name)
        for name in matching
    )


class LimiterWaitBinding(LimiterWaitIntent):
    """Limiter-owned immutable accounting identities for a completed wait."""

    registry_owner_id: ExactUuid
    limiter_instance_id: ExactUuid
    held_reservation_id: ExactUuid
    limiter_clock_epoch: StrictPositiveInt
    limiter_accounting_generation: StrictPositiveInt
    rest_window_id: ExactUuid
    rest_window_end_monotonic_ns: StrictPositiveInt
    product_window_id: ExactUuid
    product_window_end_monotonic_ns: StrictPositiveInt
    cooldown_epoch: StrictNonNegativeInt

    @model_validator(mode="after")
    def require_distinct_owner_and_event_identities(self) -> Self:
        owner_ids = (
            self.registry_owner_id,
            self.limiter_instance_id,
            self.held_reservation_id,
        )
        if len(set(owner_ids)) != 3 or self.rest_window_id == self.product_window_id:
            raise type(self)._safe_error(
                "private_model_value", "invalid private model value"
            ) from None
        return self


class LimiterReservationInvalidationReason(ExactStrEnum):
    """Closed reasons for terminal invalidation of one limiter reservation."""

    WRONG_MODE_OR_TARGET = "WRONG_MODE_OR_TARGET"
    EXPECTED_BINDING_MISMATCH = "EXPECTED_BINDING_MISMATCH"
    VENUE_TRANSFER_TARGET_MISMATCH = "VENUE_TRANSFER_TARGET_MISMATCH"
    VENUE_TRANSFER_BINDING_MISMATCH = "VENUE_TRANSFER_BINDING_MISMATCH"
    VENUE_TRANSFER_REUSED = "VENUE_TRANSFER_REUSED"
    D10_PRECONSUME_REJECTION = "D10_PRECONSUME_REJECTION"
    DURABLE_REPLAY_NO_SEND = "DURABLE_REPLAY_NO_SEND"
    DATABASE_FAILURE = "DATABASE_FAILURE"
    COMMIT_UNCERTAIN = "COMMIT_UNCERTAIN"
    CANCELLED = "CANCELLED"
    POST_COMMIT_CURRENTNESS_FAILED = "POST_COMMIT_CURRENTNESS_FAILED"
    DEADLINE_DERIVATION_FAILED = "DEADLINE_DERIVATION_FAILED"
    HANDOFF_ATTACHMENT_FAILED = "HANDOFF_ATTACHMENT_FAILED"
    REGISTRY_CLOSED = "REGISTRY_CLOSED"
    FORK_OR_PROCESS_MISMATCH = "FORK_OR_PROCESS_MISMATCH"
    LIMITER_REPLACED = "LIMITER_REPLACED"
    ACCOUNTING_EVENT_REPLACED = "ACCOUNTING_EVENT_REPLACED"
    LIMITER_CLOCK_INVALIDATED = "LIMITER_CLOCK_INVALIDATED"
    ACCOUNTING_WINDOW_EXPIRED = "ACCOUNTING_WINDOW_EXPIRED"
    COOLDOWN_ADVANCED = "COOLDOWN_ADVANCED"
    INTERNAL_EXCEPTION = "INTERNAL_EXCEPTION"


_DECLARATIONS_CLOSED = False


class _OpaqueCompletionDeclaration:
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


class LimiterWaitCompleted(_OpaqueCompletionDeclaration):
    """Opaque single-use token for one registered limiter wait."""

    __slots__ = ("_grant", "_token_id")


class _HeldLimiterReservation(_OpaqueCompletionDeclaration):
    __slots__ = ("_grant", "_reservation_id")


class _CompletedPrivateMutationAcquisition(_OpaqueCompletionDeclaration):
    __slots__ = ()

    binding: LimiterWaitBinding
    held_reservation: _HeldLimiterReservation


class _RegisteredPrivateMutationAcquisition(_OpaqueCompletionDeclaration):
    __slots__ = ("_binding", "_token")

    @property
    def token(self) -> LimiterWaitCompleted:
        return cast(LimiterWaitCompleted, object.__getattribute__(self, "_token"))

    @property
    def binding(self) -> LimiterWaitBinding:
        return cast(LimiterWaitBinding, object.__getattribute__(self, "_binding"))


class _ConsumedLimiterWaitEvidence(_OpaqueCompletionDeclaration):
    __slots__ = ("_binding", "_grant")


class _VenueLimiterReservationHandle(_OpaqueCompletionDeclaration):
    __slots__ = ("_binding", "_grant")


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class _PrivateMutationGrantRegistration(_OpaqueCompletionDeclaration):
    registration_id: UUID
    registry_owner_id: UUID
    registry_open_epoch: int
    ownership_proof_id: UUID
    limiter_instance_id: UUID
    limiter_clock_epoch: int
    limiter_accounting_generation: int
    cooldown_epoch: int
    process_id: int
    process_incarnation_id: UUID
    intent: LimiterWaitIntent
    outcome_slot: _VenueDispatchOutcomeSlot
    held_reservation_id: UUID
    token_id: UUID
    rest_event_id: UUID
    product_event_id: UUID
    _limiter: object
    _gate: object
    _store: _PrivateLimiterLifecycleStore
    _owner_capability: _PrivateLimiterOwnerCapability


_DECLARATIONS_CLOSED = True


class _D10LimiterWaitConsumePort(Protocol):
    def consume_or_invalidate_once(
        self,
        token: LimiterWaitCompleted,
        *,
        expected: LimiterWaitBinding,
        actual_region: DeltaRegion,
        actual_environment: DeltaEnvironment,
        actual_mode: ExecutionMode,
    ) -> _ConsumedLimiterWaitEvidence: ...

    def invalidate_issued_once(
        self,
        token: LimiterWaitCompleted,
        *,
        reason: Literal[LimiterReservationInvalidationReason.D10_PRECONSUME_REJECTION],
    ) -> None: ...


class _D10LimiterReservationDispositionPort(Protocol):
    def invalidate_once(
        self,
        evidence: _ConsumedLimiterWaitEvidence,
        *,
        reason: LimiterReservationInvalidationReason,
    ) -> None: ...

    def attach_to_venue_handoff_once(
        self,
        evidence: _ConsumedLimiterWaitEvidence,
        *,
        entry_latch: _VenueDispatchEntryLatch,
    ) -> _VenueLimiterReservationHandle: ...


class _D10VenueHandoffTransferPort(Protocol):
    def transfer_attached_once(
        self,
        handle: _VenueLimiterReservationHandle,
        *,
        entry_latch: _VenueDispatchEntryLatch,
        rejection_reason: Literal[
            LimiterReservationInvalidationReason.VENUE_TRANSFER_TARGET_MISMATCH,
            LimiterReservationInvalidationReason.VENUE_TRANSFER_BINDING_MISMATCH,
        ]
        | None,
    ) -> Literal["TRANSFERRED", "ALREADY_ENTERED"] | _VenueNoEntryDisposition: ...


class _VenueLimiterEntryPort(Protocol):
    def advance_pre_entry_once(
        self,
        handle: _VenueLimiterReservationHandle,
        *,
        entry_latch: _VenueDispatchEntryLatch,
        phase: VenueDispatchPreEntryPhase,
        wire_request_evidence_sha256: Sha256Hex | None = None,
        pre_dispatch_audit_receipt_id: UUID | None = None,
    ) -> None: ...

    def invalidate_before_entry_once(
        self,
        handle: _VenueLimiterReservationHandle,
        *,
        entry_latch: _VenueDispatchEntryLatch,
        reason: VenueNoEntryReason,
    ) -> _VenueNoEntryDisposition: ...

    def finalize_and_enter_once(
        self,
        handle: _VenueLimiterReservationHandle,
        *,
        expected: LimiterWaitBinding,
        entry_latch: _VenueDispatchEntryLatch,
        clock: Task210TrustedDispatchClock,
        clock_currentness: Task210TrustedClockCurrentnessPort,
        signed_at: TrustedDispatchInstant,
        deadline: TrustedDispatchDeadline,
        signed_timestamp: StrictNonNegativeInt,
    ) -> _VenueNoEntryDisposition | _EnteredVenueDispatch: ...
