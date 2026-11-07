"""Transactional claim, recovery, CAS and terminalization for global kills."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from hmt_crypto.oms.kill_switch import (
    KillMode,
    KillRequest,
    KillState,
    KillTransitionDecision,
    rehydrate_kill_request,
)
from hmt_crypto.persistence.command_models import CommandOperation
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import validate_stored_evidence
from hmt_crypto.persistence.kill_workflow_models import KillWorkflow, KillWorkflowEvent
from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.persistence.types import UTCDateTime


class KillWorkflowRepositoryError(RuntimeError):
    """Collapse persistence and invariant failures at the worker boundary."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("kill workflow persistence failed")


@dataclass(frozen=True, slots=True)
class KillCommandClaim:
    """One bounded ownership generation for a claimed global-kill command."""

    command_id: UUID
    owner_id: UUID
    generation: int
    expires_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.command_id) is not UUID
            or self.command_id.int == 0
            or type(self.owner_id) is not UUID
            or self.owner_id.int == 0
            or type(self.generation) is not int
            or not 1 <= self.generation < 2**63
            or type(self.expires_at) is not datetime
            or self.expires_at.tzinfo is not UTC
            or self.expires_at.fold != 0
        ):
            raise KillWorkflowRepositoryError() from None


def _predicates(model: Any, scope: AccountScope) -> tuple[ColumnElement[bool], ...]:
    return (
        model.account_id == scope.account_id,
        model.region == scope.region,
        model.environment == scope.environment,
        model.mode == scope.mode,
    )


def _scope(value: AccountScope) -> AccountScope:
    try:
        if type(value) is not AccountScope or value.mode == "LIVE":
            raise ValueError
        return AccountScope.model_validate(value)
    except Exception:
        raise KillWorkflowRepositoryError() from None


def _exact_time(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is not UTC or value.fold != 0:
        raise KillWorkflowRepositoryError() from None
    return value


def _duration(value: timedelta) -> timedelta:
    if type(value) is not timedelta or not timedelta(seconds=5) <= value <= timedelta(minutes=5):
        raise KillWorkflowRepositoryError() from None
    return value


def _clock_expression(session: AsyncSession) -> Any:
    if session.get_bind().dialect.name == "postgresql":
        return func.clock_timestamp()
    return type_coerce(func.strftime("%Y-%m-%dT%H:%M:%f", "now") + "000+00:00", UTCDateTime())


async def _database_now(session: AsyncSession) -> datetime:
    try:
        value = await session.scalar(select(_clock_expression(session)))
        if type(value) is not datetime:
            raise ValueError
        return _exact_time(value)
    except Exception:
        raise KillWorkflowRepositoryError() from None


def _command_mode(command: CommandOperation) -> KillMode:
    try:
        if command.operation_kind != "GLOBAL_KILL" or command.attempt_count != 0:
            raise PersistenceValueError()
        validate_stored_evidence(
            "intent",
            command.operation_kind,
            command.evidence_schema_version,
            command.evidence_payload,
            command.evidence_sha256,
        )
        payload = json.loads(command.evidence_payload)
        if type(payload) is not dict or set(payload) != {"flatten", "preview_id"}:
            raise PersistenceValueError()
        flatten = payload["flatten"]
        preview = payload["preview_id"]
        if type(flatten) is not bool or type(preview) is not str or str(UUID(preview)) != preview:
            raise PersistenceValueError()
        return KillMode.CANCEL_AND_FLATTEN if flatten else KillMode.CANCEL_ONLY
    except Exception:
        raise KillWorkflowRepositoryError() from None


def _request(row: KillWorkflow) -> KillRequest:
    try:
        return rehydrate_kill_request(
            request_id=row.command_id,
            mode=KillMode(row.kill_mode),
            state=KillState(row.state),
            transition_count=row.transition_count,
        )
    except Exception:
        raise KillWorkflowRepositoryError() from None


class KillWorkflowRepository:
    """Own global-kill command claims and their atomic workflow transition graph."""

    async def claim_active(
        self,
        session: AsyncSession,
        scope: AccountScope,
        *,
        owner_id: UUID,
        duration: timedelta,
    ) -> KillCommandClaim | None:
        """Claim a queued command or recover one only after its database lease expires."""
        owned_scope = _scope(scope)
        lease = _duration(duration)
        if type(owner_id) is not UUID or owner_id.int == 0:
            raise KillWorkflowRepositoryError() from None
        try:
            row = (
                await session.execute(
                    select(CommandOperation)
                    .where(
                        *_predicates(CommandOperation, owned_scope),
                        CommandOperation.operation_kind == "GLOBAL_KILL",
                        or_(
                            CommandOperation.status == "QUEUED",
                            and_(
                                CommandOperation.status == "CLAIMED",
                                CommandOperation.claim_expires_at <= _clock_expression(session),
                            ),
                        ),
                        CommandOperation.available_at <= _clock_expression(session),
                    )
                    .order_by(CommandOperation.created_at, CommandOperation.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            observed = await _database_now(session)
            _command_mode(row)
            if (
                row.claim_generation >= 2**63 - 1
                or row.version >= 2**63 - 1
                or any(
                    value is not None
                    for value in (
                        row.leader_ref,
                        row.leader_generation,
                        row.recovery_ref,
                        row.recovery_generation,
                    )
                )
                or row.updated_at > observed
                or (
                    row.status == "CLAIMED"
                    and (row.claim_expires_at is None or row.claim_expires_at > observed)
                )
            ):
                raise KillWorkflowRepositoryError()
            row.status = "CLAIMED"
            row.claim_owner = owner_id
            row.claim_generation += 1
            row.claim_expires_at = observed + lease
            row.updated_at = observed
            await session.flush()
            assert row.claim_expires_at is not None
            return KillCommandClaim(
                command_id=row.id,
                owner_id=owner_id,
                generation=row.claim_generation,
                expires_at=row.claim_expires_at,
            )
        except KillWorkflowRepositoryError:
            raise
        except Exception:
            raise KillWorkflowRepositoryError() from None

    async def start_or_load(
        self,
        session: AsyncSession,
        scope: AccountScope,
        claim: KillCommandClaim,
    ) -> KillRequest:
        """Initialize from immutable command evidence or recover an existing workflow."""
        owned_scope = _scope(scope)
        try:
            command = await self._lock_claimed_command(session, owned_scope, claim)
            row = (
                await session.execute(
                    select(KillWorkflow)
                    .where(
                        *_predicates(KillWorkflow, owned_scope),
                        KillWorkflow.command_id == claim.command_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            observed = await _database_now(session)
            self._validate_claim(command, claim, observed)
            mode = _command_mode(command)
            if row is None:
                row = KillWorkflow(
                    id=claim.command_id,
                    **owned_scope.model_dump(),
                    command_id=claim.command_id,
                    kill_mode=mode.value,
                    state=KillState.REQUESTED.value,
                    transition_count=0,
                    version=1,
                    created_at=observed,
                    updated_at=observed,
                    completed_at=None,
                )
                session.add(row)
                await session.flush()
            elif row.kill_mode != mode.value or row.id != claim.command_id:
                raise KillWorkflowRepositoryError()
            return _request(row)
        except KillWorkflowRepositoryError:
            raise
        except Exception:
            raise KillWorkflowRepositoryError() from None

    async def load(
        self,
        session: AsyncSession,
        scope: AccountScope,
        command_id: UUID,
    ) -> KillRequest | None:
        """Load one scoped workflow without mutation."""
        owned_scope = _scope(scope)
        if type(command_id) is not UUID or command_id.int == 0:
            raise KillWorkflowRepositoryError() from None
        try:
            row = (
                await session.execute(
                    select(KillWorkflow).where(
                        *_predicates(KillWorkflow, owned_scope),
                        KillWorkflow.command_id == command_id,
                    )
                )
            ).scalar_one_or_none()
            return None if row is None else _request(row)
        except KillWorkflowRepositoryError:
            raise
        except Exception:
            raise KillWorkflowRepositoryError() from None

    async def apply_and_finalize(
        self,
        session: AsyncSession,
        scope: AccountScope,
        claim: KillCommandClaim,
        decision: KillTransitionDecision,
    ) -> KillRequest | None:
        """CAS one transition and atomically finalize a terminal command."""
        owned_scope = _scope(scope)
        if type(decision) is not KillTransitionDecision:
            raise KillWorkflowRepositoryError() from None
        try:
            command = await self._lock_claimed_command(session, owned_scope, claim)
            row = (
                await session.execute(
                    select(KillWorkflow)
                    .where(
                        *_predicates(KillWorkflow, owned_scope),
                        KillWorkflow.command_id == claim.command_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if row is None:
                raise KillWorkflowRepositoryError()
            observed = await _database_now(session)
            self._validate_claim(command, claim, observed)
            current = _request(row)
            if row.updated_at > observed:
                raise KillWorkflowRepositoryError()
            if (
                decision.request_id != current.request_id
                or decision.expected_mode is not current.mode
                or decision.expected_state is not current.state
                or decision.expected_transition_count != current.transition_count
            ):
                self._requeue(command, observed)
                await session.flush()
                return None
            next_request = decision.next_request
            if next_request.request_id != current.request_id:
                raise KillWorkflowRepositoryError()
            row.state = next_request.state.value
            row.transition_count = next_request.transition_count
            row.version = next_request.transition_count + 1
            row.updated_at = observed
            row.completed_at = observed if next_request.is_terminal else None
            session.add(
                KillWorkflowEvent(
                    id=decision.event_id,
                    **owned_scope.model_dump(),
                    workflow_id=row.id,
                    sequence=next_request.transition_count,
                    event_kind=decision.event.value,
                    recorded_at=observed,
                )
            )
            if next_request.is_terminal:
                self._terminalize(command, claim, next_request, observed)
            else:
                self._requeue(command, observed)
            await session.flush()
            return next_request
        except KillWorkflowRepositoryError:
            raise
        except Exception:
            raise KillWorkflowRepositoryError() from None

    async def _lock_claimed_command(
        self,
        session: AsyncSession,
        scope: AccountScope,
        claim: KillCommandClaim,
    ) -> CommandOperation:
        if type(claim) is not KillCommandClaim:
            raise KillWorkflowRepositoryError() from None
        row = (
            await session.execute(
                select(CommandOperation)
                .where(
                    *_predicates(CommandOperation, scope),
                    CommandOperation.id == claim.command_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if (
            row is None
            or row.operation_kind != "GLOBAL_KILL"
            or row.status != "CLAIMED"
            or row.claim_owner != claim.owner_id
            or row.claim_generation != claim.generation
            or row.claim_expires_at != claim.expires_at
            or row.claim_expires_at is None
        ):
            raise KillWorkflowRepositoryError() from None
        return row

    @staticmethod
    def _validate_claim(
        command: CommandOperation,
        claim: KillCommandClaim,
        now: datetime,
    ) -> None:
        if (
            command.id != claim.command_id
            or command.claim_owner != claim.owner_id
            or command.claim_generation != claim.generation
            or command.claim_expires_at != claim.expires_at
            or now >= claim.expires_at
            or command.updated_at > now
        ):
            raise KillWorkflowRepositoryError() from None

    @staticmethod
    def _requeue(command: CommandOperation, now: datetime) -> None:
        if command.status != "CLAIMED" or command.completed_at is not None:
            raise KillWorkflowRepositoryError() from None
        command.status = "QUEUED"
        command.available_at = now
        command.updated_at = now
        command.claim_owner = None
        command.claim_expires_at = None

    @staticmethod
    def _terminalize(
        command: CommandOperation,
        claim: KillCommandClaim,
        request: KillRequest,
        now: datetime,
    ) -> None:
        if (
            not request.is_terminal
            or command.id != request.request_id
            or command.id != claim.command_id
            or command.attempt_count != 0
        ):
            raise KillWorkflowRepositoryError() from None
        command.status = "COMPLETED" if request.succeeded else "FAILED"
        command.completed_at = now
        command.updated_at = now
        command.claim_owner = None
        command.claim_expires_at = None


__all__ = [
    "KillCommandClaim",
    "KillWorkflowRepository",
    "KillWorkflowRepositoryError",
]
