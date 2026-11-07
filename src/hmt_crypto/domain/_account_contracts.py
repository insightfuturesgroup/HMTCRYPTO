"""Construction-time account contracts exposed by the public account facade."""

from dataclasses import dataclass
from typing import Annotated, Any, Literal, NoReturn, Self, cast

from pydantic import (
    BeforeValidator,
    GetCoreSchemaHandler,
    ValidationError,
    model_validator,
)
from pydantic_core import CoreSchema, InitErrorDetails, PydanticCustomError, core_schema

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.private_primitives import (
    CredentialVersion,
    ExactAwareUtcDatetime,
    ExactEnumField,
    ExactStrEnum,
    ExactUuid,
    ExternalUserText,
    PrivateBoundaryModel,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

_ACCOUNT_MODULE = "hmt_crypto.domain.account"


def _safe_validation_error(code: str, message: str) -> ValidationError:
    details = cast(
        list[InitErrorDetails],
        [{"type": PydanticCustomError(code, message), "loc": (), "input": None}],
    )
    return ValidationError.from_exception_data("PrivateAccount", details, hide_input=True)


@dataclass(frozen=True, slots=True, eq=False)
class _ExactStringLiteral:
    __module__ = _ACCOUNT_MODULE

    expected: str

    def __post_init__(self) -> None:
        if type(self.expected) is not str:
            raise TypeError("exact literal requires an exact string")

    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        del source_type, handler
        expected = self.expected

        def validate(value: object, info: core_schema.ValidationInfo) -> str:
            if info.mode != "python" or type(value) is not str or value != expected:
                raise _safe_validation_error("exact_string_literal", "invalid exact string literal")
            return expected

        def serialize(value: object, info: core_schema.SerializationInfo) -> str:
            del info
            if type(value) is not str or value != expected:
                raise ValueError("invalid exact string literal")
            return expected

        return core_schema.with_info_plain_validator_function(
            validate,
            json_schema_input_schema=core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                serialize, info_arg=True, return_schema=core_schema.str_schema()
            ),
        )


@dataclass(frozen=True, slots=True, eq=False)
class _ExactBoolean:
    __module__ = _ACCOUNT_MODULE

    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        del source_type, handler

        def validate(value: object, info: core_schema.ValidationInfo) -> bool:
            if info.mode != "python" or type(value) is not bool:
                raise _safe_validation_error("exact_boolean", "invalid exact boolean")
            return value

        def serialize(value: object) -> bool:
            if type(value) is not bool:
                raise ValueError("invalid exact boolean")
            return value

        return core_schema.with_info_plain_validator_function(
            validate,
            json_schema_input_schema=core_schema.bool_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                serialize, return_schema=core_schema.bool_schema()
            ),
        )


@dataclass(frozen=True, slots=True, eq=False)
class _ExactBooleanLiteral:
    __module__ = _ACCOUNT_MODULE

    expected: bool

    def __post_init__(self) -> None:
        if type(self.expected) is not bool:
            raise TypeError("exact boolean literal requires an exact bool")

    def __get_pydantic_core_schema__(
        self, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        del source_type, handler
        expected = self.expected

        def validate(value: object, info: core_schema.ValidationInfo) -> bool:
            if info.mode != "python" or type(value) is not bool or value is not expected:
                raise _safe_validation_error(
                    "exact_boolean_literal", "invalid exact boolean literal"
                )
            return expected

        def serialize(value: object) -> bool:
            if type(value) is not bool or value is not expected:
                raise ValueError("invalid exact boolean literal")
            return expected

        return core_schema.with_info_plain_validator_function(
            validate,
            json_schema_input_schema=core_schema.bool_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                serialize, return_schema=core_schema.bool_schema()
            ),
        )


ExactBool = Annotated[bool, _ExactBoolean()]
ExactTrue = Annotated[Literal[True], _ExactBooleanLiteral(True)]
MissingKind = Annotated[Literal["MISSING"], _ExactStringLiteral("MISSING")]
NullKind = Annotated[Literal["NULL"], _ExactStringLiteral("NULL")]
ValueKind = Annotated[Literal["VALUE"], _ExactStringLiteral("VALUE")]
DeltaRestSource = Annotated[Literal["delta_rest"], _ExactStringLiteral("delta_rest")]
PrivatePolicyVersion = Annotated[
    Literal["delta-docs-2026-11-04-private-v1"],
    _ExactStringLiteral("delta-docs-2026-11-04-private-v1"),
]
NumericFillOrderKind = Annotated[Literal["NUMERIC"], _ExactStringLiteral("NUMERIC")]
SettlementFillOrderKind = Annotated[Literal["SETTLEMENT"], _ExactStringLiteral("SETTLEMENT")]
LookupFoundKind = Annotated[Literal["FOUND"], _ExactStringLiteral("FOUND")]
LookupNotFoundKind = Annotated[Literal["NOT_FOUND"], _ExactStringLiteral("NOT_FOUND")]
StrictPaginationKind = Annotated[Literal["STRICT"], _ExactStringLiteral("STRICT")]
DiagnosticPaginationKind = Annotated[Literal["DIAGNOSTIC"], _ExactStringLiteral("DIAGNOSTIC")]
ExactDeltaRegion = Annotated[DeltaRegion, ExactEnumField(DeltaRegion)]
ExactDeltaEnvironment = Annotated[DeltaEnvironment, ExactEnumField(DeltaEnvironment)]
ExactExecutionMode = Annotated[ExecutionMode, ExactEnumField(ExecutionMode)]


class CollectionMode(ExactStrEnum):
    __module__ = _ACCOUNT_MODULE

    STRICT = "STRICT"
    DIAGNOSTIC = "DIAGNOSTIC"


class TruncationReason(ExactStrEnum):
    __module__ = _ACCOUNT_MODULE

    PAGE_LIMIT = "PAGE_LIMIT"
    ITEM_LIMIT = "ITEM_LIMIT"


class MissingField(PrivateBoundaryModel):
    __module__ = _ACCOUNT_MODULE

    kind: MissingKind


class NullField(PrivateBoundaryModel):
    __module__ = _ACCOUNT_MODULE

    kind: NullKind


class AccountRef(PrivateBoundaryModel):
    __module__ = _ACCOUNT_MODULE

    account_id: ExactUuid
    external_user_id: ExternalUserText
    region: ExactDeltaRegion
    environment: ExactDeltaEnvironment
    mode: ExactExecutionMode
    credential_version: CredentialVersion


class ObservationContext(PrivateBoundaryModel):
    __module__ = _ACCOUNT_MODULE

    account: AccountRef
    endpoint_id: PrivateRouteId
    correlation_id: ExactUuid
    requested_at: ExactAwareUtcDatetime
    received_at: ExactAwareUtcDatetime
    source: DeltaRestSource

    @model_validator(mode="after")
    def require_ordered_times(self) -> Self:
        if self.received_at < self.requested_at:
            raise type(self)._safe_error(
                "private_model_value", "invalid private model value"
            ) from None
        return self


def _exact_nonempty_uuid_tuple(value: object) -> object:
    if type(value) is not tuple or tuple.__len__(value) == 0:
        raise ValueError("page correlations require an exact nonempty tuple")
    return value


_ExactPageCorrelationIds = Annotated[
    tuple[ExactUuid, ...], BeforeValidator(_exact_nonempty_uuid_tuple)
]


class PrivatePaginationProvenance(PrivateBoundaryModel):
    __module__ = _ACCOUNT_MODULE

    account: AccountRef
    endpoint_id: PrivateRouteId
    first_requested_at: ExactAwareUtcDatetime
    last_received_at: ExactAwareUtcDatetime
    page_correlation_ids: _ExactPageCorrelationIds
    credential_version: CredentialVersion
    policy_version: PrivatePolicyVersion
    mode: CollectionMode

    @model_validator(mode="after")
    def require_consistent_evidence(self) -> Self:
        if self.last_received_at < self.first_requested_at:
            raise type(self)._safe_error(
                "private_model_value", "invalid private model value"
            ) from None
        if self.credential_version != self.account.credential_version:
            raise type(self)._safe_error(
                "private_model_value", "invalid private model value"
            ) from None
        return self


def _invalid_generic_declaration(kind: Literal["model", "alias"]) -> NoReturn:
    raise TypeError(f"invalid reviewed generic {kind} declaration")


def _rebuild_account_contracts() -> None:
    """Resolve helper-local annotations after the account facade is bound."""
    namespace = globals()
    for model in (
        MissingField,
        NullField,
        AccountRef,
        ObservationContext,
        PrivatePaginationProvenance,
    ):
        model.model_rebuild(force=True, raise_errors=True, _types_namespace=namespace)
