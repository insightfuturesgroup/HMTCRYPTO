"""Caller-transaction-owned leadership evidence mutations; no runtime authority."""

from datetime import datetime, timedelta
from typing import Any, TypeVar
from uuid import UUID

from asyncpg.pgproto.pgproto import UUID as AsyncpgUUID  # type: ignore[import-untyped]
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import StaleDataError

from hmt_crypto.persistence.coordination_models import LeadershipLease, RecoveryIncarnation
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.repository_clock import RepositoryClock
from hmt_crypto.persistence.repository_contracts import (
    AccountScope,
    DomainModel,
    LeadershipFence,
    LeaseSnapshot,
)
from hmt_crypto.persistence.repository_errors import (
    FenceRejectedError,
    LeaseUnavailableError,
    RepositoryInvariantError,
)
from hmt_crypto.persistence.repository_locks import (
    LockedCurrentLeadership,
    LockedLeadership,
    MutationDatabaseTime,
    _before_lock,
    _clean,
    _lock_operation,
    _mint_lock,
    _validate,
    _validating_locked,
    consume_database_time,
)

D = TypeVar("D", bound=DomainModel)


def _input(cls: type[D], value: D) -> D:
    try:
        if type(value) is not cls:
            raise PersistenceValueError()
        checked = cls.model_validate(value)
        return cls.model_validate(checked.model_dump(warnings=False))
    except (ValueError, ValidationError):
        raise PersistenceValueError() from None


def _uuid(value: UUID) -> None:
    if type(value) is not UUID:
        raise PersistenceValueError()


def _scope(model: Any, scope: AccountScope) -> list[Any]:
    return [getattr(model, key) == value for key, value in scope.model_dump().items()]


def _stored_value(value: Any) -> Any:
    # asyncpg's binary UUID passes through SQLAlchemy's native UUID result path.
    return UUID(bytes=value.bytes) if type(value) is AsyncpgUUID else value


def _receipt(cls: type[D], scope: AccountScope, row: Any, identity: str) -> D:
    try:
        values = {
            key: _stored_value(getattr(row, "id" if key == identity else key))
            for key in cls.model_fields
            if key != "scope"
        }
        return cls.model_validate({"scope": scope, **values})
    except (ValueError, ValidationError, PersistenceValueError, AttributeError, TypeError):
        raise RepositoryInvariantError() from None


def _duration(duration: timedelta, now: datetime | None = None) -> datetime | None:
    if type(duration) is not timedelta or duration <= timedelta(0):
        raise PersistenceValueError()
    if now is None:
        return None
    try:
        return now + duration
    except OverflowError:
        raise PersistenceValueError() from None


async def _flush(session: AsyncSession) -> None:
    try:
        await session.flush()
    except StaleDataError:
        raise FenceRejectedError() from None
    except SQLAlchemyError:
        raise RepositoryInvariantError() from None


def _lease_at(locked: LockedCurrentLeadership, now: MutationDatabaseTime) -> None:
    _validate(locked._session, locked)
    expected: LeadershipFence = locked._data
    _compare(locked._row, expected)
    if now.value < locked._row.created_at:
        raise RepositoryInvariantError()
    if now.value >= expected.expires_at:
        raise FenceRejectedError()


def _compare(row: LeadershipLease, expected: LeadershipFence) -> None:
    if row.state != "HELD" or any(
        getattr(row, "id" if key == "lease_id" else key) != value
        for key, value in expected.model_dump().items()
    ):
        raise FenceRejectedError()


class LeadershipRepository:
    @_lock_operation
    async def lock_scope(
        self,
        session: AsyncSession,
        scope: AccountScope,
        *,
        lease_id: UUID,
        recovery_incarnation_id: UUID,
    ) -> LockedLeadership:
        _clean(session)
        scope = _input(AccountScope, scope)
        _uuid(lease_id)
        _uuid(recovery_incarnation_id)
        _before_lock(session, scope, 10, "lease", lease_id)
        with session.no_autoflush:
            rows = (
                (
                    await session.execute(
                        select(LeadershipLease)
                        .where(*_scope(LeadershipLease, scope))
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) != 1:
                raise RepositoryInvariantError()
            row = rows[0]
            _receipt(LeaseSnapshot, scope, row, "lease_id")
            if row.id != lease_id or row.recovery_incarnation_id != recovery_incarnation_id:
                raise FenceRejectedError()
            parent = (
                await session.execute(
                    select(RecoveryIncarnation.id).where(
                        *_scope(RecoveryIncarnation, scope),
                        RecoveryIncarnation.id == recovery_incarnation_id,
                    )
                )
            ).scalar_one_or_none()
            if parent is None:
                raise RepositoryInvariantError()
        return _mint_lock(LockedLeadership, session, scope, 10, row.id, row)

    def refine_current(
        self, locked: LockedLeadership, expected: LeadershipFence
    ) -> LockedCurrentLeadership:
        if type(locked) is not LockedLeadership:
            raise FenceRejectedError()
        _validate(locked._session, locked)
        expected = _input(LeadershipFence, expected)
        with _validating_locked(locked._session):
            _compare(locked._row, expected)
        return _mint_lock(
            LockedCurrentLeadership,
            locked._session,
            locked.scope,
            10,
            locked.identity,
            locked._row,
            data=expected,
            refinement=locked,
        )

    @_lock_operation
    async def acquire(
        self,
        session: AsyncSession,
        scope: AccountScope,
        *,
        lease_id: UUID,
        recovery_incarnation_id: UUID,
        owner_id: UUID,
        duration: timedelta,
    ) -> LeaseSnapshot:
        _clean(session)
        _uuid(owner_id)
        _duration(duration)
        locked = await self.lock_scope(
            session, scope, lease_id=lease_id, recovery_incarnation_id=recovery_incarnation_id
        )
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=locked)
        now = consume_database_time(session, sampled, locked)
        expiry = _duration(duration, now.value)
        row: LeadershipLease = locked._row
        if row.state == "HELD" and row.expires_at is not None and row.expires_at > now.value:
            raise LeaseUnavailableError()
        if row.generation >= 2**63 - 1 or row.version >= 2**63 - 1:
            raise RepositoryInvariantError()
        row.state, row.owner_id = "HELD", owner_id
        row.generation += 1
        row.heartbeat_at = row.updated_at = now.value
        row.expires_at = expiry
        await _flush(session)
        return _receipt(LeaseSnapshot, scope, row, "lease_id")

    @_lock_operation
    async def renew(
        self,
        session: AsyncSession,
        scope: AccountScope,
        expected: LeadershipFence,
        *,
        duration: timedelta,
    ) -> LeaseSnapshot:
        _clean(session)
        expected = _input(LeadershipFence, expected)
        _duration(duration)
        locked = await self.lock_scope(
            session,
            scope,
            lease_id=expected.lease_id,
            recovery_incarnation_id=expected.recovery_incarnation_id,
        )
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=locked)
        now = consume_database_time(session, sampled, locked)
        current = self.refine_current(locked, expected)
        _lease_at(current, now)
        if locked._row.version >= 2**63 - 1:
            raise RepositoryInvariantError()
        expiry = _duration(duration, now.value)
        row: LeadershipLease = locked._row
        row.heartbeat_at = row.updated_at = now.value
        row.expires_at = expiry
        # Identical wall-clock ticks can leave every materialized timestamp equal.
        # A successful renewal must still consume its fence through an optimistic UPDATE.
        flag_modified(row, "updated_at")
        await _flush(session)
        return _receipt(LeaseSnapshot, scope, row, "lease_id")

    @_lock_operation
    async def release(
        self, session: AsyncSession, scope: AccountScope, expected: LeadershipFence
    ) -> LeaseSnapshot:
        _clean(session)
        expected = _input(LeadershipFence, expected)
        locked = await self.lock_scope(
            session,
            scope,
            lease_id=expected.lease_id,
            recovery_incarnation_id=expected.recovery_incarnation_id,
        )
        sampled = await RepositoryClock().sample_after_locks(session, last_lock=locked)
        now = consume_database_time(session, sampled, locked)
        current = self.refine_current(locked, expected)
        _lease_at(current, now)
        if locked._row.version >= 2**63 - 1:
            raise RepositoryInvariantError()
        row: LeadershipLease = locked._row
        row.state, row.owner_id, row.heartbeat_at, row.expires_at = "UNOWNED", None, None, None
        row.updated_at = now.value
        await _flush(session)
        return _receipt(LeaseSnapshot, scope, row, "lease_id")
