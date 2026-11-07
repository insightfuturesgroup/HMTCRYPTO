"""Database wall time sampled only after the exact final lock."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hmt_crypto.persistence.repository_locks import (
    LockedDatabaseTime,
    RepositoryLock,
    _admit,
    _sample,
    _state,
    _validate,
    consume_database_time,
)


class RepositoryClock:
    def discard_sample(
        self, session: AsyncSession, *, last_lock: RepositoryLock, sampled_at: LockedDatabaseTime
    ) -> None:
        """Close an unused sample before proceeding with another repository operation."""
        consume_database_time(session, sampled_at, last_lock)

    async def sample_after_locks(
        self, session: AsyncSession, *, last_lock: RepositoryLock
    ) -> LockedDatabaseTime:
        await _admit(session)
        locked = _validate(session, last_lock)
        if _state(session)["last"] is not locked:
            from hmt_crypto.persistence.repository_errors import RepositoryStateError

            raise RepositoryStateError()
        postgres = session.get_bind().dialect.name == "postgresql"
        with session.no_autoflush:
            if postgres:
                value = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            else:
                raw = (
                    await session.execute(select(func.strftime("%Y-%m-%dT%H:%M:%f", "now")))
                ).scalar_one()
                value = datetime.fromisoformat(raw).replace(tzinfo=UTC)
        return _sample(session, locked, value, postgres)
