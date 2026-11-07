"""Positive-exit transaction service for one immutable API-audit append."""

from __future__ import annotations

import asyncio

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from hmt_crypto.persistence.api_audit_repository import (
    ApiAuditRepository,
    _ProvisionalAppend,
)
from hmt_crypto.persistence.api_audit_service_contracts import (
    ApiAuditAppendReceipt,
    ApiAuditAppendRequest,
)
from hmt_crypto.persistence.api_audit_service_errors import (
    AuditCommitUncertain,
    AuditConflict,
    AuditEvidenceRejected,
    AuditInvariantViolation,
    AuditTransactionStateError,
    AuditUnavailable,
)

_SAFE_ERRORS = (
    AuditCommitUncertain,
    AuditConflict,
    AuditEvidenceRejected,
    AuditInvariantViolation,
    AuditTransactionStateError,
    AuditUnavailable,
)


def _exact_request(request: object) -> ApiAuditAppendRequest:
    try:
        if type(request) is not ApiAuditAppendRequest:
            raise ValueError
        exact = ApiAuditAppendRequest.model_validate(request)
    except (ValidationError, ValueError):
        error = AuditEvidenceRejected()
    else:
        return exact
    raise error from None


def _clean_inactive_session(session: object) -> AsyncSession:
    invalid = True
    try:
        if type(session) is AsyncSession:
            sync = object.__getattribute__(session, "sync_session")
            invalid = bool(
                type(sync) is not Session
                or sync.autoflush is not False
                or sync.in_transaction()
                or sync.new
                or sync.dirty
                or sync.deleted
                or len(sync.identity_map) != 0
            )
    except Exception:
        invalid = True
    if invalid:
        raise AuditTransactionStateError()
    assert type(session) is AsyncSession
    return session


def _receipt(provisional: _ProvisionalAppend) -> ApiAuditAppendReceipt:
    """Construct the public receipt only after positive transaction context exit."""
    try:
        receipt = ApiAuditAppendReceipt(
            context=provisional.context,
            event_kind=provisional.event_kind,
            event_id=provisional.event_id,
            root_event_id=provisional.root_event_id,
            sequence=provisional.sequence,
            event_key=provisional.event_key,
            audit_recorded_at=provisional.audit_recorded_at,
            summary_sha256=provisional.summary_sha256,
            retention_mode=provisional.retention_mode,
            payload_slot=provisional.payload_slot,
            payload_schema_version=provisional.payload_schema_version,
            payload_sha256=provisional.payload_sha256,
            payload_byte_length=provisional.payload_byte_length,
            payload_expires_at=provisional.payload_expires_at,
            payload_ttl_seconds=provisional.payload_ttl_seconds,
            retention_policy_version=provisional.retention_policy_version,
            payload_copy_key=provisional.payload_copy_key,
        )
    except Exception:
        error = AuditInvariantViolation()
    else:
        return receipt
    raise error from None


class ApiAuditTransactionService:
    """Own one transaction on an exact clean caller-supplied ``AsyncSession``."""

    __slots__ = ("_repository",)

    def __init__(self, *, repository: ApiAuditRepository) -> None:
        if type(repository) is not ApiAuditRepository:
            raise AuditTransactionStateError()
        self._repository = repository

    async def append(
        self, *, session: AsyncSession, request: ApiAuditAppendRequest
    ) -> ApiAuditAppendReceipt:
        """Append or replay atomically and publish only after successful context exit."""
        exact = _exact_request(request)
        owned = _clean_inactive_session(session)
        provisional: _ProvisionalAppend | None = None
        failure: Exception | None = None
        try:
            async with owned.begin():
                provisional = await self._repository._append_or_replay(owned, exact)
        except asyncio.CancelledError:
            raise
        except _SAFE_ERRORS as error:
            failure = error
        except Exception:
            failure = AuditCommitUncertain()
        if failure is not None:
            raise failure from None
        if provisional is None:
            raise AuditCommitUncertain()
        return _receipt(provisional)


__all__ = ["ApiAuditTransactionService"]
