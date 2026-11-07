"""Transaction-local lock composition and single-use time; not security capabilities."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any, Protocol, TypeVar, cast
from uuid import UUID

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection, RootTransaction
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from hmt_crypto.persistence.repository_contracts import AccountScope
from hmt_crypto.persistence.repository_errors import RepositoryStateError

LEADERSHIP, RISK_STATE, RECONCILIATION, COMMAND = 10, 20, 25, 30
ORDER, RESERVATION, ATTEMPT, EVENT_KEY, EVENT = 40, 50, 60, 65, 70
_SENTINEL = object()


class RepositoryLock(Protocol):
    @property
    def scope(self) -> AccountScope: ...
    @property
    def rank(self) -> int: ...


@dataclass(frozen=True, init=False, eq=False)
class _Handle:
    _sentinel: object
    _session: AsyncSession
    _transaction: object
    scope: AccountScope
    rank: int
    identity: object
    _row: Any
    _nonce: object
    _data: Any
    _source: object

    def __init__(self) -> None:
        raise RepositoryStateError()


@dataclass(frozen=True, init=False, eq=False)
class LockedLeadership(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedCurrentLeadership(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedRiskState(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedReconciliationRun(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedCommand(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedCommandClaim(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedUnreservedTerminal(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedOrderParent(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedAttemptSet(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedAttempt(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedEventKey(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedEventStream(_Handle):
    pass


@dataclass(frozen=True, init=False, eq=False)
class LockedDatabaseTime:
    value: datetime
    authoritative: bool
    _lock: _Handle
    _nonce: object

    def __init__(self) -> None:
        raise RepositoryStateError()


@dataclass(frozen=True, init=False, eq=False)
class MutationDatabaseTime:
    value: datetime

    def __init__(self) -> None:
        raise RepositoryStateError()


T = TypeVar("T", bound=_Handle)


def _state(session: AsyncSession) -> dict[str, Any]:
    tx = session.sync_session.get_transaction()
    state = session.info.get("_repository_locks")
    if state is None or state["transaction"] is not tx:
        state = dict(
            transaction=tx,
            connection=None,
            connection_transaction=None,
            handles={},
            samples={},
            rows={},
            last=None,
            last_key=None,
            scope=None,
            progress=0,
            poisoned=False,
        )
        session.info["_repository_locks"] = state
    return state


def _require_clean_unit(session: AsyncSession) -> None:
    if (
        not session.in_transaction()
        or session.in_nested_transaction()
        or not session.is_active
        or session.new
        or session.dirty
        or session.deleted
    ):
        raise RepositoryStateError()


def _require_outer_connection(connection: Connection, root: RootTransaction | None) -> None:
    if (
        connection.closed
        or connection.invalidated
        or connection.in_nested_transaction()
        or root is None
        or not root.is_active
        or connection.get_transaction() is not root
    ):
        raise RepositoryStateError()


async def _admit(session: AsyncSession, *, sampled_at: LockedDatabaseTime | None = None) -> None:
    """Bind the caller's connection before the first repository operation or lock mint."""
    _require_clean_unit(session)
    state = _state(session)
    if state["connection"] is None:
        if isinstance(session.bind, AsyncConnection) and session.bind.in_nested_transaction():
            raise RepositoryStateError()
        connection = (await session.connection()).sync_connection
        if connection is None:
            raise RepositoryStateError()
        root = connection.get_transaction()
        _require_outer_connection(connection, root)
        if state["transaction"] is not session.sync_session.get_transaction():
            raise RepositoryStateError()
        state["connection"] = connection
        state["connection_transaction"] = root
    _clean(session, sampled_at=sampled_at)


def _clean(session: AsyncSession, *, sampled_at: LockedDatabaseTime | None = None) -> None:
    """Check the current physical transaction without I/O, including during pure refinements."""
    _require_clean_unit(session)
    state = _state(session)
    connection = state["connection"]
    if connection is None:
        raise RepositoryStateError()
    _require_outer_connection(connection, state["connection_transaction"])
    if state["poisoned"] or (
        state["samples"]
        and (sampled_at is None or state["samples"].get(id(sampled_at)) is not sampled_at)
    ):
        raise RepositoryStateError()
    for row in state["rows"].values():
        inspected = sa_inspect(row)
        if (
            not inspected.persistent
            or inspected.deleted
            or inspected.session is not session.sync_session
            or session.identity_map.get(inspected.key) is not row
            or inspected.expired_attributes
            or inspected.unloaded.intersection(inspected.mapper.column_attrs.keys())
        ):
            state["poisoned"] = True
            raise RepositoryStateError()


def _remember_row(session: AsyncSession, row: Any) -> None:
    if sa_inspect(row, raiseerr=False) is not None:
        _state(session)["rows"][id(row)] = row


Method = TypeVar("Method", bound=Callable[..., Awaitable[Any]])


@contextmanager
def _validating_locked(session: AsyncSession) -> Iterator[None]:
    state = _state(session)
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        if not succeeded:
            state["poisoned"] = True


def _lock_operation(method: Method) -> Method:
    """Fail closed after a lock acquisition or consumption of its final sample."""

    @wraps(method)
    async def invoke(owner: Any, session: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        await _admit(session, sampled_at=kwargs.get("sampled_at"))
        state = _state(session)
        before = state["progress"]
        succeeded = False
        try:
            result = await method(owner, session, *args, **kwargs)
            succeeded = True
            return result
        finally:
            if not succeeded and state["progress"] != before:
                state["poisoned"] = True

    return cast(Method, invoke)


def _identity_key(value: object) -> tuple[str, ...]:
    if value is None:
        return ("",)
    if isinstance(value, UUID):
        return ("uuid", value.hex)
    if type(value) is int:
        return ("integer", f"{value:020d}")
    if type(value) is str:
        return ("text", value)
    if type(value) is tuple:
        return ("tuple", str(len(value)), *(part for item in value for part in _identity_key(item)))
    raise RepositoryStateError()


_NAMESPACES = {
    LockedLeadership: "lease",
    LockedRiskState: "risk",
    LockedReconciliationRun: "run",
    LockedCommand: "command",
    LockedOrderParent: "order",
    LockedUnreservedTerminal: "terminal",
    LockedAttemptSet: "2_attempt_set",
    LockedAttempt: "3_attempt",
    LockedEventKey: "event_key",
    LockedEventStream: "events",
}


def _before_lock(
    session: AsyncSession, scope: AccountScope, rank: int, namespace: str, identity: object
) -> None:
    _clean(session)
    state = _state(session)
    key = (rank, namespace, _identity_key(identity))
    if state["last_key"] is not None and (state["scope"] != scope or key < state["last_key"]):
        raise RepositoryStateError()
    state["scope"], state["last_key"] = scope, key
    state["progress"] += 1


def _mint_lock(
    cls: type[T],
    session: AsyncSession,
    scope: AccountScope,
    rank: int,
    identity: object,
    row: Any,
    *,
    data: Any = None,
    refinement: _Handle | None = None,
) -> T:
    _clean(session)
    if refinement is None:
        _before_lock(session, scope, rank, _NAMESPACES.get(cls, "reservation"), identity)
    _remember_row(session, row)
    state = _state(session)
    handle = object.__new__(cls)
    values = dict(
        _sentinel=_SENTINEL,
        _session=session,
        _transaction=state["transaction"],
        scope=scope,
        rank=rank,
        identity=identity,
        _row=row,
        _nonce=object(),
        _data=data,
        _source=refinement,
    )
    for key, value in values.items():
        object.__setattr__(handle, key, value)
    state["handles"][id(handle)] = handle
    if refinement is None:
        state["last"] = handle
    return handle


def _validate(
    session: AsyncSession,
    lock: RepositoryLock,
    *,
    scope: AccountScope | None = None,
    maximum: int | None = None,
    sampled_at: LockedDatabaseTime | None = None,
) -> _Handle:
    _clean(session, sampled_at=sampled_at)
    if not isinstance(lock, _Handle) or lock._sentinel is not _SENTINEL:
        raise RepositoryStateError()
    state = _state(session)
    if (
        lock._session is not session
        or lock._transaction is not state["transaction"]
        or state["handles"].get(id(lock)) is not lock
    ):
        raise RepositoryStateError()
    if (scope is not None and lock.scope != scope) or (maximum is not None and lock.rank > maximum):
        raise RepositoryStateError()
    return cast(_Handle, lock)


def _predecessor(session: AsyncSession, supplied: RepositoryLock | None) -> None:
    """A composition cannot omit or substitute the caller's most recent acquired lock."""
    _clean(session)
    last = _state(session)["last"]
    if supplied is None:
        if last is not None:
            raise RepositoryStateError()
        return
    actual = _validate(session, supplied)
    if actual is not last and actual._source is not last:
        raise RepositoryStateError()


def _sample(
    session: AsyncSession, lock: _Handle, value: datetime, authoritative: bool
) -> LockedDatabaseTime:
    _validate(session, lock)
    state = _state(session)
    if state["last"] is not lock:
        raise RepositoryStateError()
    token = object.__new__(LockedDatabaseTime)
    for key, item in dict(
        value=value, authoritative=authoritative, _lock=lock, _nonce=object()
    ).items():
        object.__setattr__(token, key, item)
    state["samples"].clear()
    state["samples"][id(token)] = token
    return token


def consume_database_time(
    session: AsyncSession, sampled_at: LockedDatabaseTime, final_lock: RepositoryLock
) -> MutationDatabaseTime:
    lock = _validate(session, final_lock, sampled_at=sampled_at)
    state = _state(session)
    if (
        type(sampled_at) is not LockedDatabaseTime
        or state["samples"].get(id(sampled_at)) is not sampled_at
        or sampled_at._lock is not lock
        or state["last"] is not lock
    ):
        raise RepositoryStateError()
    del state["samples"][id(sampled_at)]
    state["progress"] += 1
    result = object.__new__(MutationDatabaseTime)
    object.__setattr__(result, "value", sampled_at.value)
    return result
