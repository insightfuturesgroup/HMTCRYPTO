"""Closed immutable metadata summaries for API audit evidence."""

from typing import Any, Literal, NamedTuple, Never, Self, cast

from pydantic import model_validator

from hmt_crypto.domain._private_model import PrivateBoundaryModel
from hmt_crypto.exchange.delta._private_route_rows import PRIVATE_ROUTE_ROWS
from hmt_crypto.persistence._api_audit_contexts import (
    AccountAuditContext,
    AuditCallKind,
    AuditEventKind,
    AuditSummaryKind,
    ExchangeCode,
    ExchangeRequestId,
    HttpMethod,
    LocalRejectionReason,
    NonnegativeBigInt,
    NonzeroUuid,
    PositiveBigInt,
    PublicAuditContext,
    SchemaVersionOne,
    Sha256Hex,
    SimulatorOutcomeKind,
    Token128,
    Token256,
    TransferRejectionReason,
    VenueOutcomeKind,
    _execution_mode_literal,
)

_PUBLIC_MODULE = "hmt_crypto.persistence.api_audit_contracts"


class _RouteProjection(NamedTuple):
    family: str
    route_id: str
    method: str
    path_template: str
    policy_version: str
    weight: int
    max_response_bytes: int


_PUBLIC_ROUTE_ROWS = (
    ("products", "GET", "/v2/products", 3, 8_388_608),
    ("product", "GET", "/v2/products/{symbol}", 3, 524_288),
    ("tickers", "GET", "/v2/tickers", 3, 4_194_304),
    ("ticker", "GET", "/v2/tickers/{symbols}", 3, 1_048_576),
    ("orderbook", "GET", "/v2/l2orderbook/{symbol}", 3, 4_194_304),
    ("candles", "GET", "/v2/history/candles", 3, 4_194_304),
)
_ROUTE_PROJECTIONS = tuple(
    _RouteProjection("PUBLIC", route, method, path, "delta-docs-2026-11-03", weight, bound)
    for route, method, path, weight, bound in _PUBLIC_ROUTE_ROWS
) + tuple(
    _RouteProjection(
        "PRIVATE",
        cast(str, row[0]),
        cast(str, row[2]),
        cast(str, row[3]),
        "delta-docs-2026-11-04-private-v1",
        cast(int, row[20]),
        cast(int, row[9]),
    )
    for row in PRIVATE_ROUTE_ROWS
)


def _route_projection(family: object, route_id: object) -> _RouteProjection | None:
    if type(family) is str and type(route_id) is str:
        for projection in _ROUTE_PROJECTIONS:
            if projection.family == family and projection.route_id == route_id:
                return projection
    return None


def _route_profile_sql() -> str:
    resolved = []
    for route in _ROUTE_PROJECTIONS:
        resolved.append(
            "("
            + " AND ".join(
                (
                    f"route_family = '{route.family}'",
                    f"route_id = '{route.route_id}'",
                    f"method = '{route.method}'",
                    f"path_template = '{route.path_template}'",
                    f"policy_version = '{route.policy_version}'",
                    f"weight = {route.weight}",
                )
            )
            + ")"
        )
    unresolved = (
        "(route_family = 'UNRESOLVED' AND route_id = 'UNRESOLVED' "
        "AND method IS NULL AND path_template IS NULL AND weight IS NULL)"
    )
    return (
        "target IS NULL OR (target = 'SIMULATOR' AND route_family IS NULL "
        "AND route_id IS NULL AND method IS NULL AND path_template IS NULL "
        "AND weight IS NULL) OR (target = 'VENUE' AND "
        "((context_kind = 'PUBLIC' AND route_family IN ('PUBLIC','UNRESOLVED')) OR "
        "(context_kind = 'ACCOUNT' AND route_family IN ('PRIVATE','UNRESOLVED') "
        "AND (route_family <> 'PRIVATE' OR region = 'india'))) "
        "AND (call_kind <> 'DISPATCH' OR "
        "((operation = 'PLACE_ORDER' AND route_id = 'place_order') OR "
        "(operation = 'MODIFY_ORDER' AND route_id = 'modify_order'))) AND ("
        + " OR ".join((*resolved, unresolved))
        + "))"
    )


def _response_route_bound_sql() -> str:
    return "completed_response_byte_length IS NULL OR " + " OR ".join(
        f"(route_id = '{route.route_id}' "
        f"AND completed_response_byte_length <= {route.max_response_bytes})"
        for route in _ROUTE_PROJECTIONS
    )


def _response_truth_sql() -> str:
    no_response = "'TIMEOUT','TRANSPORT_ERROR','CANCELLED','AMBIGUOUS','LOCAL_REJECTED'"
    return (
        f"(outcome_kind NOT IN ({no_response}) OR (http_status IS NULL "
        "AND exchange_code IS NULL AND exchange_request_id IS NULL "
        "AND completed_response_sha256 IS NULL AND completed_response_byte_length IS NULL)) "
        "AND (http_status IS NOT NULL OR (exchange_code IS NULL "
        "AND exchange_request_id IS NULL AND completed_response_sha256 IS NULL "
        "AND completed_response_byte_length IS NULL)) "
        "AND (outcome_kind <> 'SUCCESS' OR http_status IS NULL "
        "OR http_status BETWEEN 200 AND 299) "
        "AND (outcome_kind <> 'AUTH_REJECTED' OR http_status IN (401,403)) "
        "AND (outcome_kind <> 'RATE_LIMITED' OR http_status = 429)"
    )


def _validate_route_binding(record: Any) -> None:
    owner = cast(PrivateBoundaryModel, record)
    if record.route_family == "UNRESOLVED":
        if record.route_id != "UNRESOLVED" or any(
            value is not None for value in (record.method, record.path_template, record.weight)
        ):
            _invalid(owner)
        expected_policy = (
            "delta-docs-2026-11-03"
            if type(record.context) is PublicAuditContext
            else "delta-docs-2026-11-04-private-v1"
        )
        if record.policy_version != expected_policy:
            _invalid(owner)
    else:
        projection = _route_projection(record.route_family, record.route_id)
        if projection is None or (
            record.method,
            record.path_template,
            record.policy_version,
            record.weight,
        ) != (
            projection.method,
            projection.path_template,
            projection.policy_version,
            projection.weight,
        ):
            _invalid(owner)
    if any(
        value is not None
        for value in (
            record.simulator_backend_id,
            record.simulator_profile_id,
            record.simulator_profile_version,
            record.simulator_request_evidence_sha256,
        )
    ):
        _invalid(owner)
    if record.call_kind is AuditCallKind.DISPATCH:
        if type(record.context) is not AccountAuditContext:
            _invalid(owner)
        if (
            _execution_mode_literal(record.context.mode) != "LIVE"
            or record.attempt_backend != "VENUE"
            or record.route_family != "PRIVATE"
        ):
            _invalid(owner)
        expected_route = "place_order" if record.operation == "PLACE_ORDER" else "modify_order"
        if record.route_id != expected_route:
            _invalid(owner)
    elif type(record.context) is PublicAuditContext:
        if record.route_family not in ("PUBLIC", "UNRESOLVED"):
            _invalid(owner)
    elif record.route_family not in ("PRIVATE", "UNRESOLVED"):
        _invalid(owner)
    elif record.route_family == "PRIVATE" and str.__str__(record.context.region) != "india":
        _invalid(owner)


def _invalid(model: PrivateBoundaryModel) -> Never:
    raise type(model)._safe_error("api_audit_value", "invalid API audit value") from None


def _reject_explicit_null(model: PrivateBoundaryModel) -> None:
    state = object.__getattribute__(model, "__dict__")
    fields_set = object.__getattribute__(model, "__pydantic_fields_set__")
    if any(state.get(name) is None for name in fields_set):
        _invalid(model)


class VenueRequestSummaryV1(PrivateBoundaryModel):
    """Canonical bounded venue request evidence; never a raw request."""

    __module__ = _PUBLIC_MODULE
    schema_version: SchemaVersionOne
    summary_kind: Literal[AuditSummaryKind.VENUE_REQUEST]
    route_id: Token128
    method: HttpMethod | None = None
    path_template: Token256 | None = None
    route_policy: Token128 | None = None
    weight: PositiveBigInt | None = None
    request_evidence_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def validate_route_shape(self) -> Self:
        """Require all registry fields together, or none for UNRESOLVED."""
        _reject_explicit_null(self)
        route = (self.method, self.path_template, self.route_policy, self.weight)
        if self.route_id == "UNRESOLVED":
            if (
                any(value is not None for value in route)
                or self.request_evidence_sha256 is not None
            ):
                _invalid(self)
        else:
            projection = next(
                (item for item in _ROUTE_PROJECTIONS if item.route_id == self.route_id), None
            )
            if projection is None or route != (
                projection.method,
                projection.path_template,
                projection.policy_version,
                projection.weight,
            ):
                _invalid(self)
        return self


class VenueResponseSummaryV1(PrivateBoundaryModel):
    """Canonical safe venue response classification and bounded facts."""

    __module__ = _PUBLIC_MODULE
    schema_version: SchemaVersionOne
    summary_kind: Literal[AuditSummaryKind.VENUE_RESPONSE]
    outcome_kind: VenueOutcomeKind
    http_status: NonnegativeBigInt | None = None
    exchange_code: ExchangeCode | None = None
    exchange_request_id: ExchangeRequestId | None = None
    completed_response_sha256: Sha256Hex | None = None
    completed_response_byte_length: NonnegativeBigInt | None = None
    duration_ns: NonnegativeBigInt

    @model_validator(mode="after")
    def validate_response_shape(self) -> Self:
        """Keep received-response facts paired and strictly bounded."""
        _reject_explicit_null(self)
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            _invalid(self)
        if (self.completed_response_sha256 is None) != (
            self.completed_response_byte_length is None
        ):
            _invalid(self)
        if self.http_status is None and any(
            value is not None
            for value in (
                self.exchange_code,
                self.exchange_request_id,
                self.completed_response_sha256,
            )
        ):
            _invalid(self)
        no_response = {
            VenueOutcomeKind.TIMEOUT,
            VenueOutcomeKind.TRANSPORT_ERROR,
            VenueOutcomeKind.CANCELLED,
            VenueOutcomeKind.AMBIGUOUS,
            VenueOutcomeKind.LOCAL_REJECTED,
        }
        if self.outcome_kind in no_response and any(
            value is not None
            for value in (
                self.http_status,
                self.exchange_code,
                self.exchange_request_id,
                self.completed_response_sha256,
            )
        ):
            _invalid(self)
        if self.outcome_kind is VenueOutcomeKind.SUCCESS:
            if self.http_status is not None and not 200 <= self.http_status <= 299:
                _invalid(self)
            if self.exchange_code is not None:
                _invalid(self)
        elif self.outcome_kind is VenueOutcomeKind.AUTH_REJECTED:
            if self.http_status not in (401, 403):
                _invalid(self)
        elif self.outcome_kind is VenueOutcomeKind.RATE_LIMITED:
            if self.http_status != 429:
                _invalid(self)
        return self


class SimulatorRequestSummaryV1(PrivateBoundaryModel):
    """Exact synthetic profile and canonical simulator request evidence."""

    __module__ = _PUBLIC_MODULE
    schema_version: SchemaVersionOne
    summary_kind: Literal[AuditSummaryKind.SIMULATOR_REQUEST]
    simulator_backend_id: Token128
    simulator_profile_id: Token128
    simulator_profile_version: Token128
    simulator_request_evidence_sha256: Sha256Hex


class SimulatorResponseSummaryV1(PrivateBoundaryModel):
    """Closed synthetic response classification."""

    __module__ = _PUBLIC_MODULE
    schema_version: SchemaVersionOne
    summary_kind: Literal[AuditSummaryKind.SIMULATOR_RESPONSE]
    outcome_kind: SimulatorOutcomeKind
    duration_ns: NonnegativeBigInt


class LocalRejectionSummaryV1(PrivateBoundaryModel):
    """Closed local rejection classification with no diagnostic message."""

    __module__ = _PUBLIC_MODULE
    schema_version: SchemaVersionOne
    summary_kind: Literal[AuditSummaryKind.LOCAL_REJECTION]
    reason: LocalRejectionReason
    route_id: Token128

    @model_validator(mode="after")
    def validate_registered_route(self) -> Self:
        """Admit only a registered route identity or fixed UNRESOLVED."""
        if self.route_id != "UNRESOLVED" and not any(
            route.route_id == self.route_id for route in _ROUTE_PROJECTIONS
        ):
            _invalid(self)
        return self


class TransferRejectionSummaryV1(PrivateBoundaryModel):
    """Closed receiver rejection with conditional prior immutable identity."""

    __module__ = _PUBLIC_MODULE
    schema_version: SchemaVersionOne
    summary_kind: Literal[AuditSummaryKind.TRANSFER_REJECTION]
    reason: TransferRejectionReason
    send_started_by_this_call: Literal[False]
    prior_dispatch_event_id: NonzeroUuid | None = None
    prior_consumption_id: NonzeroUuid | None = None
    prior_bytes_sent_possible: Literal[True] | None = None

    @model_validator(mode="after")
    def validate_prior_identity(self) -> Self:
        """Admit prior identity only for the exact ALREADY_ENTERED case."""
        _reject_explicit_null(self)
        prior = (
            self.prior_dispatch_event_id,
            self.prior_consumption_id,
            self.prior_bytes_sent_possible,
        )
        if self.reason is TransferRejectionReason.ALREADY_ENTERED:
            if any(value is None for value in prior):
                _invalid(self)
        elif any(value is not None for value in prior):
            _invalid(self)
        return self


AuditSummaryV1 = (
    VenueRequestSummaryV1
    | VenueResponseSummaryV1
    | SimulatorRequestSummaryV1
    | SimulatorResponseSummaryV1
    | LocalRejectionSummaryV1
    | TransferRejectionSummaryV1
)
_ALL_SUMMARY_MODELS = (
    VenueRequestSummaryV1,
    VenueResponseSummaryV1,
    SimulatorRequestSummaryV1,
    SimulatorResponseSummaryV1,
    LocalRejectionSummaryV1,
    TransferRejectionSummaryV1,
)

_SUMMARY_TYPES = {
    AuditEventKind.ATTEMPT_STARTED: VenueRequestSummaryV1,
    AuditEventKind.ATTEMPT_FINISHED: VenueResponseSummaryV1,
    AuditEventKind.LOCAL_REJECTED: LocalRejectionSummaryV1,
    AuditEventKind.VENUE_PRE_DISPATCH: VenueRequestSummaryV1,
    AuditEventKind.SIMULATOR_PRE_DISPATCH: SimulatorRequestSummaryV1,
    AuditEventKind.VENUE_NO_ENTRY: LocalRejectionSummaryV1,
    AuditEventKind.VENUE_ENTERED: VenueResponseSummaryV1,
    AuditEventKind.VENUE_TRANSFER_REJECTED: TransferRejectionSummaryV1,
    AuditEventKind.SIMULATOR_OUTCOME: SimulatorResponseSummaryV1,
}
_NO_ENTRY_REASONS = frozenset(
    {
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
    }
)
_LOCAL_REJECTION_REASONS = frozenset(
    {
        "INVALID_REQUEST",
        "ROUTE_DENIED",
        "ADMISSION_DENIED",
        "LIMITER_DENIED",
        "CANCELLED",
        "ATTEMPT_PREPARATION_UNCERTAIN",
        "DISPATCH_COMMIT_UNCERTAIN",
    }
)


def _validate_summary_binding(record: Any, summary_type: type[PrivateBoundaryModel]) -> None:
    summary = object.__getattribute__(record, "summary")
    owner = cast(PrivateBoundaryModel, record)
    if type(summary) is not summary_type:
        _invalid(owner)
    if type(summary) is VenueRequestSummaryV1:
        actual = (
            summary.route_id,
            summary.method,
            summary.path_template,
            summary.route_policy,
            summary.weight,
        )
        expected = (
            record.route_id,
            record.method,
            record.path_template,
            record.policy_version,
            record.weight,
        )
        if actual != expected:
            _invalid(owner)
    elif type(summary) is SimulatorRequestSummaryV1:
        simulator_actual = (
            summary.simulator_backend_id,
            summary.simulator_profile_id,
            summary.simulator_profile_version,
            summary.simulator_request_evidence_sha256,
        )
        simulator_expected = (
            record.simulator_backend_id,
            record.simulator_profile_id,
            record.simulator_profile_version,
            record.simulator_request_evidence_sha256,
        )
        if simulator_actual != simulator_expected:
            _invalid(owner)
    elif type(summary) is LocalRejectionSummaryV1 and summary.route_id != record.route_id:
        _invalid(owner)
    elif type(summary) is VenueResponseSummaryV1:
        projection = _route_projection(record.route_family, record.route_id)
        if summary.completed_response_byte_length is not None and (
            projection is None
            or summary.completed_response_byte_length > projection.max_response_bytes
        ):
            _invalid(owner)


__all__ = [
    "VenueRequestSummaryV1",
    "VenueResponseSummaryV1",
    "SimulatorRequestSummaryV1",
    "SimulatorResponseSummaryV1",
    "LocalRejectionSummaryV1",
    "TransferRejectionSummaryV1",
    "AuditSummaryV1",
]
