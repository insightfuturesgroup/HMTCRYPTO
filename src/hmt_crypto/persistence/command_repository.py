"""Immediate durable enqueue, queued-only claims, and unreserved terminal failures."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, literal, select, type_coerce
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from hmt_crypto.persistence.attempt_models import OrderAttempt
from hmt_crypto.persistence.command_models import CommandOperation
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_evidence import OPERATIONS, IntentEvidence, encode_evidence
from hmt_crypto.persistence.leadership_repository import (
    LeadershipRepository,
    _duration,
    _flush,
    _input,
    _lease_at,
    _receipt,
    _scope,
    _stored_value,
    _uuid,
)
from hmt_crypto.persistence.repository_clock import RepositoryClock
from hmt_crypto.persistence.repository_contracts import (
    AccountScope,
    ClaimResult,
    CommandClaim,
    CommandCorrelation,
    CommandReceipt,
    EnqueueDisposition,
    EnqueueResult,
    LeadershipFence,
    NulFreeBoundedString128,
    OperationKind,
    PreAttemptTerminalStatus,
)
from hmt_crypto.persistence.repository_errors import (
    FenceRejectedError,
    RepositoryConflictError,
    RepositoryInvariantError,
    RepositoryStateError,
)
from hmt_crypto.persistence.repository_locks import (
    LockedCommand,
    LockedCommandClaim,
    LockedCurrentLeadership,
    LockedDatabaseTime,
    LockedReconciliationRun,
    LockedRiskState,
    LockedUnreservedTerminal,
    MutationDatabaseTime,
    RepositoryLock,
    _admit,
    _before_lock,
    _clean,
    _lock_operation,
    _mint_lock,
    _predecessor,
    _validate,
    _validating_locked,
    consume_database_time,
)
from hmt_crypto.persistence.risk_reservation_models import RiskReservation
from hmt_crypto.persistence.types import UTCDateTime


def _clock_expression(session: AsyncSession) -> Any:
    if session.get_bind().dialect.name == "postgresql":
        return func.clock_timestamp()
    return type_coerce(func.strftime("%Y-%m-%dT%H:%M:%f", "now") + "000+00:00", UTCDateTime())


def _command(scope: AccountScope, row: CommandOperation) -> CommandReceipt:
    result = _receipt(CommandReceipt, scope, row, "command_id")
    if scope.mode != "LIVE" and any(
        getattr(result, key) is not None
        for key in ("leader_ref", "leader_generation", "recovery_ref", "recovery_generation")
    ):
        raise RepositoryInvariantError()
    if (
        scope.mode == "LIVE"
        and result.status == "CLAIMED"
        and (result.leader_ref is None or result.recovery_ref is None)
    ):
        raise RepositoryInvariantError()
    return result


def _claim(row: CommandOperation) -> CommandClaim:
    try:
        if row.claim_owner is None or row.claim_expires_at is None:
            raise RepositoryInvariantError()
        return CommandClaim(
            command_id=_stored_value(row.id),
            owner_id=_stored_value(row.claim_owner),
            generation=row.claim_generation,
            version=row.version,
            expires_at=row.claim_expires_at,
            leader_ref=_stored_value(row.leader_ref),
            leader_generation=row.leader_generation,
            recovery_ref=_stored_value(row.recovery_ref),
            recovery_generation=row.recovery_generation,
        )
    except ValidationError:
        raise RepositoryInvariantError() from None


def _claim_compare(command: LockedCommandClaim, leadership: LockedCurrentLeadership | None) -> None:
    row: CommandOperation = command._row
    expected: CommandClaim = command._data
    if row.status != "CLAIMED":
        raise RepositoryStateError()
    if _claim(row) != expected:
        raise FenceRejectedError()
    if command.scope.mode == "LIVE":
        if type(leadership) is not LockedCurrentLeadership:
            raise FenceRejectedError()
        _validate(command._session, leadership, scope=command.scope)
        fence: LeadershipFence = leadership._data
        if (
            expected.owner_id,
            expected.leader_ref,
            expected.leader_generation,
            expected.recovery_ref,
            expected.recovery_generation,
        ) != (
            fence.owner_id,
            fence.lease_id,
            fence.generation,
            fence.recovery_incarnation_id,
            fence.generation,
        ):
            raise FenceRejectedError()
    elif leadership is not None or any(
        getattr(expected, k) is not None
        for k in ("leader_ref", "leader_generation", "recovery_ref", "recovery_generation")
    ):
        raise FenceRejectedError()


def _claim_at(
    command: LockedCommandClaim,
    leadership: LockedCurrentLeadership | None,
    now: MutationDatabaseTime,
) -> None:
    _validate(command._session, command)
    _claim_compare(command, leadership)
    if command._row.version >= 2**63 - 1 or now.value < command._row.updated_at:
        raise RepositoryInvariantError()
    if now.value >= command._data.expires_at:
        raise FenceRejectedError()
    if leadership is not None:
        _lease_at(leadership, now)


class CommandRepository:
    async def enqueue_or_replay(
        self,
        session: AsyncSession,
        scope: AccountScope,
        *,
        request_id: NulFreeBoundedString128,
        operation_kind: OperationKind,
        correlation: CommandCorrelation,
        evidence: IntentEvidence | dict[str, object],
    ) -> EnqueueResult:
        await _admit(session)
        scope = _input(AccountScope, scope)
        correlation = _input(CommandCorrelation, correlation)
        try:
            TypeAdapter(NulFreeBoundedString128).validate_python(request_id)
            operation: object = operation_kind
            if type(operation) is not str or operation not in OPERATIONS:
                raise PersistenceValueError()
        except ValidationError:
            raise PersistenceValueError() from None
        payload, digest = encode_evidence("intent", operation_kind, 1, evidence)
        lookup = (
            select(CommandOperation)
            .where(*_scope(CommandOperation, scope), CommandOperation.request_id == request_id)
            .execution_options(populate_existing=True)
        )

        def replay(row: CommandOperation) -> EnqueueResult:
            receipt = _command(scope, row)
            if receipt.available_at != receipt.created_at:
                raise RepositoryInvariantError()
            if (
                row.operation_kind,
                row.correlation_id,
                row.evidence_schema_version,
                row.evidence_payload,
                row.evidence_sha256,
            ) != (operation_kind, correlation.correlation_id, 1, payload, digest):
                raise RepositoryConflictError()
            return EnqueueResult(disposition=EnqueueDisposition.EXISTING, command=receipt)

        with session.no_autoflush:
            row = (await session.execute(lookup)).scalar_one_or_none()
            if row is not None:
                return replay(row)
            stamp = select(_clock_expression(session).label("ts")).cte("repository_time")
            if session.get_bind().dialect.name == "postgresql":
                stamp = stamp.prefix_with("MATERIALIZED")
            values: dict[str, Any] = dict(
                id=uuid4(),
                **scope.model_dump(),
                request_id=request_id,
                operation_kind=operation_kind,
                correlation_id=correlation.correlation_id,
                status="QUEUED",
                claim_owner=None,
                claim_generation=0,
                claim_expires_at=None,
                attempt_count=0,
                leader_ref=None,
                leader_generation=None,
                recovery_ref=None,
                recovery_generation=None,
                version=1,
                completed_at=None,
                evidence_schema_version=1,
                evidence_payload=payload,
                evidence_sha256=digest,
            )
            table = CommandOperation.__table__
            names = [*values, "available_at", "created_at", "updated_at"]
            source = (
                select(
                    *[literal(value, type_=table.c[key].type) for key, value in values.items()],
                    stamp.c.ts,
                    stamp.c.ts,
                    stamp.c.ts,
                )
                .select_from(stamp)
                .where(literal(True))
            )
            insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
            statement = (
                insert(CommandOperation)
                .from_select(names, source)
                .on_conflict_do_nothing(
                    index_elements=["account_id", "region", "environment", "mode", "request_id"]
                )
                .returning(CommandOperation)
            )
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is None:
                winner = (await session.execute(lookup)).scalar_one_or_none()
                if winner is None:
                    raise RepositoryInvariantError()
                return replay(winner)
        await _flush(session)
        return EnqueueResult(disposition=EnqueueDisposition.CREATED, command=_command(scope, row))

    @_lock_operation
    async def claim_next(
        self,
        session: AsyncSession,
        scope: AccountScope,
        *,
        owner_id: UUID,
        duration: timedelta,
        leadership: LeadershipFence | None,
    ) -> ClaimResult | None:
        _clean(session)
        scope = _input(AccountScope, scope)
        _uuid(owner_id)
        _duration(duration)
        current = None
        if scope.mode == "LIVE":
            if leadership is None:
                raise PersistenceValueError()
            leadership = _input(LeadershipFence, leadership)
            if owner_id != leadership.owner_id:
                raise FenceRejectedError()
            repo = LeadershipRepository()
            neutral = await repo.lock_scope(
                session,
                scope,
                lease_id=leadership.lease_id,
                recovery_incarnation_id=leadership.recovery_incarnation_id,
            )
            current = repo.refine_current(neutral, leadership)
        elif leadership is not None:
            raise PersistenceValueError()
        _before_lock(session, scope, 30, "command", None)
        with session.no_autoflush:
            row = (
                await session.execute(
                    select(CommandOperation)
                    .where(
                        *_scope(CommandOperation, scope),
                        CommandOperation.status == "QUEUED",
                        CommandOperation.available_at <= _clock_expression(session),
                    )
                    .order_by(
                        CommandOperation.available_at,
                        CommandOperation.created_at,
                        CommandOperation.id,
                    )
                    .limit(1)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        _command(scope, row)
        locked = _mint_lock(LockedCommand, session, scope, 30, row.id, row)
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=locked)
        now = consume_database_time(session, sampled, locked)
        if current is not None:
            _lease_at(current, now)
        if row.available_at > now.value:
            raise FenceRejectedError()
        expiry = _duration(duration, now.value)
        if row.claim_generation >= 2**63 - 1 or row.version >= 2**63 - 1:
            raise RepositoryInvariantError()
        row.status, row.claim_owner, row.claim_expires_at = "CLAIMED", owner_id, expiry
        row.claim_generation += 1
        row.updated_at = now.value
        if leadership is not None:
            row.leader_ref, row.leader_generation = leadership.lease_id, leadership.generation
            row.recovery_ref, row.recovery_generation = (
                leadership.recovery_incarnation_id,
                leadership.generation,
            )
        await _flush(session)
        return ClaimResult(command=_command(scope, row), claim=_claim(row))

    @_lock_operation
    async def lock_by_identity(
        self,
        session: AsyncSession,
        scope: AccountScope,
        command_id: UUID,
        *,
        last_lock: RepositoryLock | None = None,
    ) -> LockedCommand:
        _clean(session)
        scope = _input(AccountScope, scope)
        _uuid(command_id)
        _predecessor(session, last_lock)
        if last_lock is not None:
            previous = _validate(session, last_lock, scope=scope, maximum=30)
            if (
                type(previous) is LockedRiskState
                and previous._row.reconciliation_run_id is not None
            ):
                raise RepositoryStateError()
            if type(previous) is LockedReconciliationRun:
                risk = previous._data
                if type(risk) is not LockedRiskState:
                    raise RepositoryStateError()
                _validate(session, risk, scope=scope)
                if (risk._row.reconciliation_run_id, risk._row.reconciliation_run_version) != (
                    previous._row.id,
                    previous._row.version,
                ):
                    raise RepositoryStateError()
        _before_lock(session, scope, 30, "command", command_id)
        with session.no_autoflush:
            row = (
                await session.execute(
                    select(CommandOperation)
                    .where(*_scope(CommandOperation, scope), CommandOperation.id == command_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
        if row is None:
            raise RepositoryStateError()
        _command(scope, row)
        return _mint_lock(LockedCommand, session, scope, 30, row.id, row)

    def refine_current_claim(
        self,
        command: LockedCommand,
        expected: CommandClaim,
        *,
        leadership: LockedCurrentLeadership | None,
    ) -> LockedCommandClaim:
        if type(command) is not LockedCommand:
            raise RepositoryStateError()
        _validate(command._session, command)
        expected = _input(CommandClaim, expected)
        refined = _mint_lock(
            LockedCommandClaim,
            command._session,
            command.scope,
            30,
            command.identity,
            command._row,
            data=expected,
            refinement=command,
        )
        with _validating_locked(command._session):
            _claim_compare(refined, leadership)
        return refined

    @_lock_operation
    async def lock_unreserved_terminalization(
        self, session: AsyncSession, command: LockedCommandClaim
    ) -> LockedUnreservedTerminal:
        _clean(session)
        if type(command) is not LockedCommandClaim:
            raise RepositoryStateError()
        _validate(session, command)
        _before_lock(session, command.scope, 50, "terminal", command.identity)
        with session.no_autoflush:
            reservations = (
                await session.execute(
                    select(RiskReservation.id)
                    .where(
                        *_scope(RiskReservation, command.scope),
                        RiskReservation.command_id == command.identity,
                    )
                    .order_by(RiskReservation.id)
                    .with_for_update()
                )
            ).all()
            if reservations:
                raise RepositoryStateError()
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(OrderAttempt)
                    .where(
                        *_scope(OrderAttempt, command.scope),
                        OrderAttempt.command_id == command.identity,
                    )
                )
            ).scalar_one()
        if count != command._row.attempt_count:
            raise RepositoryInvariantError()
        if count:
            raise RepositoryStateError()
        return _mint_lock(
            LockedUnreservedTerminal,
            session,
            command.scope,
            50,
            command.identity,
            command._row,
            data=command,
        )

    @_lock_operation
    async def terminalize_unreserved_at(
        self,
        session: AsyncSession,
        terminal: LockedUnreservedTerminal,
        *,
        leadership: LockedCurrentLeadership | None,
        sampled_at: LockedDatabaseTime,
        terminal_status: PreAttemptTerminalStatus,
    ) -> CommandReceipt:
        now = consume_database_time(session, sampled_at, terminal)
        if type(terminal) is not LockedUnreservedTerminal:
            raise RepositoryStateError()
        _validate(session, terminal)
        status: object = terminal_status
        if type(status) is not str or status not in ("FAILED", "CANCELLED"):
            raise PersistenceValueError()
        command: LockedCommandClaim = terminal._data
        _claim_at(command, leadership, now)
        row: CommandOperation = terminal._row
        row.status, row.completed_at, row.updated_at = terminal_status, now.value, now.value
        row.claim_owner = row.claim_expires_at = None
        await _flush(session)
        return _command(terminal.scope, row)
