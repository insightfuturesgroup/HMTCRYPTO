"""Exact, dependency-light scalar and context contracts for API audit rows."""

import re
import sys
from typing import Annotated
from uuid import UUID

from pydantic import BeforeValidator

from hmt_crypto._private_enums import ExactStrEnum
from hmt_crypto.domain._private_model import PrivateBoundaryModel
from hmt_crypto.domain.private_primitives import ExactAwareUtcDatetime

_PUBLIC_MODULE = "hmt_crypto.persistence.api_audit_contracts"
_I63_MAX = 2**63 - 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)
_EXCHANGE_CODES = frozenset(
    {
        "SignatureExpired",
        "UnauthorizedApiAccess",
        "Forbidden",
        "invalid_api_key",
        "ip_not_whitelisted_for_api_key",
        "Signature Mismatch",
        "insufficient_margin",
        "close_position_insufficient_margin",
        "insufficient_commission",
        "order_size_exceed_available",
        "order_size_not_available_in_orderbook",
        "close_order_position_unavailable",
        "risk_limits_breached",
        "invalid_contract",
        "immediate_liquidation",
        "out_of_bankruptcy",
        "self_matching_disrupted_post_only",
        "overlapping_buy_sell_orders",
        "immediate_execution_post_only",
        "naked_short_restricted",
        "portfolio_risk_limits_breached",
        "liquidation_risk_limits_breached",
        "greeks_limits_breached",
        "no_position_for_reduce_only",
        "no_position_left_for_reduce_only",
        "max_leverage_exceeded",
        "min_leverage_exceeded",
        "leverage_limit_exceeded",
        "order_already_filled",
        "less_than_order_min_size",
        "order_max_size_exceeded",
        "reduce_only_orders_allowed_sell_side",
        "reduce_only_orders_allowed",
        "trading_blocked",
        "kyc_pending",
        "trading_credits_breached",
        "derivatives_trading_blocked",
        "spot_trading_blocked",
        "spot_blocked_country",
        "account_risk_limits_breached",
        "position_under_liquidation",
        "position_bracket_exist_or_invalid_data",
        "no_open_position",
        "stale_order",
        "offset_by_reduce_only",
        "unsupported",
        "no_orders",
        "no_liquidity_for_market_order",
        "family_position_limit_exceeded",
        "trading_not_allowed_on_current_margin_mode",
        "max_orders_count_exceeded",
        "limit_order_not_allowed_for_binary_options",
        "stop_orders_not_allowed_for_binary_options",
        "not_a_binary_options_product",
        "invalid_index_symbol",
        "open_order_not_found",
    }
)


def _nonzero_uuid(value: object) -> UUID:
    if type(value) is not UUID or value.int == 0:
        raise ValueError("value must be a nonzero exact UUID")
    return UUID(int=value.int)


def _bounded_int(value: object, minimum: int, maximum: int = _I63_MAX) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("value must be an exact bounded integer")
    return value


def _positive_int(value: object) -> int:
    return _bounded_int(value, 1)


def _nonnegative_int(value: object) -> int:
    return _bounded_int(value, 0)


def _schema_one(value: object) -> int:
    if type(value) is not int or value != 1:
        raise ValueError("schema version must be one")
    return value


def _sha256(value: object) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError("value must be a lowercase SHA-256 digest")
    return value


def _ascii(value: object, *, maximum: int, label: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or not value.isascii()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise ValueError(f"value must be bounded visible ASCII {label}")
    return value


def _token_128(value: object) -> str:
    return _ascii(value, maximum=128, label="text")


def _token_256(value: object) -> str:
    return _ascii(value, maximum=256, label="text")


def _method(value: object) -> str:
    result = _ascii(value, maximum=8, label="method")
    if result not in ("GET", "POST", "PUT", "DELETE"):
        raise ValueError("method is not registered")
    return result


def _exchange_code(value: object) -> str:
    if type(value) is not str or value not in _EXCHANGE_CODES:
        raise ValueError("exchange code is not registered")
    return value


def _request_id(value: object) -> str:
    if type(value) is not str or _REQUEST_ID.fullmatch(value) is None:
        raise ValueError("exchange request ID is not allowlisted")
    return value


def _config_enum(value: object, class_name: str, literals: tuple[str, ...]) -> object:
    module = sys.modules.get("hmt_crypto.config")
    if module is None:
        raise ValueError("configuration enum module is unavailable")
    enum_type = getattr(module, class_name, None)
    if enum_type is None or type(value) is not enum_type or not isinstance(value, str):
        raise ValueError("value must be a canonical configuration enum")
    literal = str.__str__(value)
    if literal not in literals or not any(value is member for member in enum_type):
        raise ValueError("value must be a canonical configuration enum")
    return value


def _region(value: object) -> object:
    return _config_enum(value, "DeltaRegion", ("india", "global"))


def _environment(value: object) -> object:
    return _config_enum(value, "DeltaEnvironment", ("testnet", "prod"))


def _execution_mode(value: object) -> object:
    return _config_enum(value, "ExecutionMode", ("BACKTEST", "PAPER", "LIVE"))


def _execution_mode_literal(value: object) -> str:
    admitted = _execution_mode(value)
    return str.__str__(admitted)


def _public_kind(value: object) -> "AuditContextKind":
    if value is not AuditContextKind.PUBLIC:
        raise ValueError("context discriminator must be canonical PUBLIC")
    return AuditContextKind.PUBLIC


def _account_kind(value: object) -> "AuditContextKind":
    if value is not AuditContextKind.ACCOUNT:
        raise ValueError("context discriminator must be canonical ACCOUNT")
    return AuditContextKind.ACCOUNT


NonzeroUuid = Annotated[UUID, BeforeValidator(_nonzero_uuid)]
PositiveBigInt = Annotated[int, BeforeValidator(_positive_int)]
NonnegativeBigInt = Annotated[int, BeforeValidator(_nonnegative_int)]
SchemaVersionOne = Annotated[int, BeforeValidator(_schema_one)]
Sha256Hex = Annotated[str, BeforeValidator(_sha256)]
Token128 = Annotated[str, BeforeValidator(_token_128)]
Token256 = Annotated[str, BeforeValidator(_token_256)]
HttpMethod = Annotated[str, BeforeValidator(_method)]
ExchangeCode = Annotated[str, BeforeValidator(_exchange_code)]
ExchangeRequestId = Annotated[str, BeforeValidator(_request_id)]
AuditUtc = ExactAwareUtcDatetime
ExactDeltaRegion = Annotated[object, BeforeValidator(_region)]
ExactDeltaEnvironment = Annotated[object, BeforeValidator(_environment)]
ExactExecutionMode = Annotated[object, BeforeValidator(_execution_mode)]


class AuditContextKind(ExactStrEnum):
    """Closed persistence context discriminator."""

    __module__ = _PUBLIC_MODULE
    PUBLIC = "PUBLIC"
    ACCOUNT = "ACCOUNT"


class AuditTarget(ExactStrEnum):
    """Closed producer target discriminator."""

    __module__ = _PUBLIC_MODULE
    VENUE = "VENUE"
    SIMULATOR = "SIMULATOR"


class AuditCallKind(ExactStrEnum):
    """Closed API-call ownership kind."""

    __module__ = _PUBLIC_MODULE
    READ = "READ"
    DISPATCH = "DISPATCH"
    LOCAL_REJECTION = "LOCAL_REJECTION"
    RECEIVER_REJECTION = "RECEIVER_REJECTION"


class AuditEventKind(ExactStrEnum):
    """Every immutable metadata event admitted by D13a."""

    __module__ = _PUBLIC_MODULE
    CALL_STARTED = "CALL_STARTED"
    CALL_FINISHED = "CALL_FINISHED"
    ATTEMPT_STARTED = "ATTEMPT_STARTED"
    ATTEMPT_FINISHED = "ATTEMPT_FINISHED"
    LOCAL_REJECTED = "LOCAL_REJECTED"
    VENUE_PRE_DISPATCH = "VENUE_PRE_DISPATCH"
    SIMULATOR_PRE_DISPATCH = "SIMULATOR_PRE_DISPATCH"
    VENUE_NO_ENTRY = "VENUE_NO_ENTRY"
    VENUE_ENTERED = "VENUE_ENTERED"
    VENUE_TRANSFER_REJECTED = "VENUE_TRANSFER_REJECTED"
    SIMULATOR_OUTCOME = "SIMULATOR_OUTCOME"
    MAINTENANCE_ROOT = "MAINTENANCE_ROOT"
    PAYLOAD_PURGED = "PAYLOAD_PURGED"


_API_ID_FIELDS = (
    "target",
    "call_kind",
    "logical_call_id",
    "correlation_id",
    "policy_version",
    "root_event_id",
    "traversal_id",
    "page_number",
    "route_family",
    "route_id",
    "method",
    "path_template",
    "weight",
    "command_id",
    "operation",
    "order_id",
    "order_attempt_id",
    "order_attempt_number",
    "attempt_backend",
    "command_evidence_sha256",
    "simulator_backend_id",
    "simulator_profile_id",
    "simulator_profile_version",
)
_READ_ID_FIELDS = _API_ID_FIELDS + ("transport_attempt_number", "start_event_id")
_DISPATCH_ID_FIELDS = _API_ID_FIELDS + (
    "transport_attempt_number",
    "dispatch_event_id",
    "consumption_id",
    "dispatch_recorded_at",
    "semantic_request_sha256",
    "wire_request_evidence_sha256",
    "simulator_request_evidence_sha256",
    "pre_dispatch_audit_receipt_id",
)
_SOURCE_ID_FIELDS = (
    "source_event_id",
    "source_payload_slot",
    "source_payload_schema_version",
    "source_payload_sha256",
    "source_payload_byte_length",
    "source_payload_expires_at",
    "source_payload_ttl_seconds",
    "source_retention_policy_version",
)
_EVENT_ID_FIELDS = {
    AuditEventKind.CALL_STARTED: _API_ID_FIELDS,
    AuditEventKind.CALL_FINISHED: _API_ID_FIELDS,
    AuditEventKind.ATTEMPT_STARTED: _READ_ID_FIELDS,
    AuditEventKind.ATTEMPT_FINISHED: _READ_ID_FIELDS,
    AuditEventKind.LOCAL_REJECTED: _API_ID_FIELDS,
    AuditEventKind.VENUE_PRE_DISPATCH: _DISPATCH_ID_FIELDS,
    AuditEventKind.SIMULATOR_PRE_DISPATCH: _DISPATCH_ID_FIELDS,
    AuditEventKind.VENUE_NO_ENTRY: _DISPATCH_ID_FIELDS,
    AuditEventKind.VENUE_ENTERED: _DISPATCH_ID_FIELDS,
    AuditEventKind.VENUE_TRANSFER_REJECTED: _API_ID_FIELDS,
    AuditEventKind.SIMULATOR_OUTCOME: _DISPATCH_ID_FIELDS,
    AuditEventKind.MAINTENANCE_ROOT: _SOURCE_ID_FIELDS,
    AuditEventKind.PAYLOAD_PURGED: _SOURCE_ID_FIELDS,
}
_EVENT_FACT_FIELDS: dict[AuditEventKind, tuple[str, ...]] = {
    kind: ("occurred_at",) for kind in AuditEventKind
}
_EVENT_FACT_FIELDS.update(
    {
        AuditEventKind.CALL_FINISHED: ("occurred_at", "outcome_kind"),
        AuditEventKind.LOCAL_REJECTED: ("occurred_at", "entry_possible", "reconciliation_required"),
        AuditEventKind.VENUE_NO_ENTRY: (
            "occurred_at",
            "last_phase",
            "entry_possible",
            "reconciliation_required",
        ),
        AuditEventKind.VENUE_ENTERED: ("occurred_at", "entry_possible", "reconciliation_required"),
        AuditEventKind.SIMULATOR_OUTCOME: (
            "occurred_at",
            "entry_possible",
            "reconciliation_required",
        ),
        AuditEventKind.MAINTENANCE_ROOT: (),
        AuditEventKind.PAYLOAD_PURGED: (),
    }
)
_SUMMARY_ID_FIELDS = (
    "root_event_id",
    "call_kind",
    "logical_call_id",
    "correlation_id",
    "command_id",
    "operation",
    "order_id",
    "order_attempt_id",
    "order_attempt_number",
    "transport_attempt_number",
    "dispatch_event_id",
    "consumption_id",
    "policy_version",
)
_TERMINAL_KINDS = (
    AuditEventKind.LOCAL_REJECTED,
    AuditEventKind.VENUE_NO_ENTRY,
    AuditEventKind.VENUE_ENTERED,
    AuditEventKind.VENUE_TRANSFER_REJECTED,
    AuditEventKind.SIMULATOR_OUTCOME,
)


class AuditSummaryKind(ExactStrEnum):
    """Closed canonical summary discriminator."""

    __module__ = _PUBLIC_MODULE
    VENUE_REQUEST = "VENUE_REQUEST"
    VENUE_RESPONSE = "VENUE_RESPONSE"
    SIMULATOR_REQUEST = "SIMULATOR_REQUEST"
    SIMULATOR_RESPONSE = "SIMULATOR_RESPONSE"
    LOCAL_REJECTION = "LOCAL_REJECTION"
    TRANSFER_REJECTION = "TRANSFER_REJECTION"


class VenueOutcomeKind(ExactStrEnum):
    """Safe venue outcome classifications."""

    __module__ = _PUBLIC_MODULE
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    AUTH_REJECTED = "AUTH_REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    TRANSPORT_ERROR = "TRANSPORT_ERROR"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    CANCELLED = "CANCELLED"
    AMBIGUOUS = "AMBIGUOUS"
    LOCAL_REJECTED = "LOCAL_REJECTED"


class SimulatorOutcomeKind(ExactStrEnum):
    """Safe synthetic outcome classifications."""

    __module__ = _PUBLIC_MODULE
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_SUBMITTED = "NOT_SUBMITTED"


class LocalRejectionReason(ExactStrEnum):
    """Closed local and no-entry reasons; no diagnostic text is retained."""

    __module__ = _PUBLIC_MODULE
    INVALID_REQUEST = "INVALID_REQUEST"
    ROUTE_DENIED = "ROUTE_DENIED"
    ADMISSION_DENIED = "ADMISSION_DENIED"
    LIMITER_DENIED = "LIMITER_DENIED"
    CANCELLED = "CANCELLED"
    SINK_BINDING_MISMATCH = "SINK_BINDING_MISMATCH"
    PREPARATION_FAILED = "PREPARATION_FAILED"
    AUDIT_FAILED = "AUDIT_FAILED"
    CURRENT_INVALIDATED = "CURRENT_INVALIDATED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    SIGNING_FAILED = "SIGNING_FAILED"
    SIGNATURE_EXPIRED = "SIGNATURE_EXPIRED"
    LIMITER_INVALIDATED = "LIMITER_INVALIDATED"
    INTERNAL_EXCEPTION = "INTERNAL_EXCEPTION"
    ATTEMPT_PREPARATION_UNCERTAIN = "ATTEMPT_PREPARATION_UNCERTAIN"
    DISPATCH_COMMIT_UNCERTAIN = "DISPATCH_COMMIT_UNCERTAIN"


class TransferRejectionReason(ExactStrEnum):
    """Closed receiver-side transfer rejection reasons."""

    __module__ = _PUBLIC_MODULE
    UNOWNED_OR_WRONG_TARGET_RESULT = "UNOWNED_OR_WRONG_TARGET_RESULT"
    ALREADY_ENTERED = "ALREADY_ENTERED"


class AuditRetentionMode(ExactStrEnum):
    """Whether a bounded sanitized copy is retained later by D13b."""

    __module__ = _PUBLIC_MODULE
    SUMMARY_ONLY = "SUMMARY_ONLY"
    RETAIN_SANITIZED_SNAPSHOT = "RETAIN_SANITIZED_SNAPSHOT"


class PayloadSlot(ExactStrEnum):
    """Single bounded sanitized payload-copy slot."""

    __module__ = _PUBLIC_MODULE
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"


class DispatchLastPhase(ExactStrEnum):
    """Last durable venue dispatch phase for a no-entry claim."""

    __module__ = _PUBLIC_MODULE
    ISSUED = "ISSUED"
    CLAIMED_FOR_UNSIGNED_PREPARATION = "CLAIMED_FOR_UNSIGNED_PREPARATION"
    AUDIT_RECORDED = "AUDIT_RECORDED"


class RouteFamily(ExactStrEnum):
    """Closed inert route-registry family."""

    __module__ = _PUBLIC_MODULE
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    UNRESOLVED = "UNRESOLVED"


class PublicAuditContext(PrivateBoundaryModel):
    """Exact account-free context for a public venue read."""

    __module__ = _PUBLIC_MODULE
    context_kind: Annotated[AuditContextKind, BeforeValidator(_public_kind)]
    region: ExactDeltaRegion
    environment: ExactDeltaEnvironment


class AccountAuditContext(PrivateBoundaryModel):
    """Exact account-scoped context for private and synthetic calls."""

    __module__ = _PUBLIC_MODULE
    context_kind: Annotated[AuditContextKind, BeforeValidator(_account_kind)]
    account_id: NonzeroUuid
    region: ExactDeltaRegion
    environment: ExactDeltaEnvironment
    mode: ExactExecutionMode


AuditContext = PublicAuditContext | AccountAuditContext


__all__ = [name for name in tuple(globals()) if not name.startswith("_")]
