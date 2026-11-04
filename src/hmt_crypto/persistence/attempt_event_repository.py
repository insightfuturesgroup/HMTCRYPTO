"""Bounded preparation and classified append-only evidence, never dispatch."""

import asyncio
import json
from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Text, cast, event, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from hmt_crypto.persistence.attempt_models import OrderAttempt
from hmt_crypto.persistence.command_repository import _claim, _claim_at, _command
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_event_models import OrderAttemptEvent
from hmt_crypto.persistence.execution_evidence import encode_evidence
from hmt_crypto.persistence.execution_support import STATES
from hmt_crypto.persistence.identity_models import Account, CredentialVersion
from hmt_crypto.persistence.leadership_repository import _flush, _input, _receipt, _scope, _uuid
from hmt_crypto.persistence.order_models import Order
from hmt_crypto.persistence.repository_contracts import (
    AccountScope,
    AttemptAllocationResult,
    AttemptEventProposal,
    AttemptEventReceipt,
    AttemptReceipt,
    EventAppendResult,
    EventDisposition,
    PositiveStrictInt,
)
from hmt_crypto.persistence.repository_errors import (
    RepositoryConflictError,
    RepositoryInvariantError,
    RepositoryLimitError,
    RepositoryStateError,
)
from hmt_crypto.persistence.repository_locks import (
    LockedAttempt,
    LockedAttemptSet,
    LockedCommand,
    LockedCommandClaim,
    LockedCurrentLeadership,
    LockedDatabaseTime,
    LockedEventKey,
    LockedEventStream,
    LockedOrderParent,
    RepositoryLock,
    _before_lock,
    _clean,
    _lock_operation,
    _mint_lock,
    _predecessor,
    _remember_row,
    _validate,
    consume_database_time,
)

_SQLITE_MUTEXES: WeakKeyDictionary[Any, dict[tuple[object, ...], asyncio.Lock]] = (
    WeakKeyDictionary()
)


def _neutral(session: AsyncSession, command: LockedCommand) -> None:
    if type(command) is not LockedCommand:
        raise RepositoryStateError()
    _validate(session, command)


def _attempt_receipt(
    command: LockedCommand | LockedCommandClaim, row: OrderAttempt
) -> AttemptReceipt:
    receipt = _receipt(AttemptReceipt, command.scope, row, "attempt_id")
    cmd = command._row
    if (
        row.command_id,
        row.operation_kind,
        row.correlation_id,
        row.evidence_payload,
        row.evidence_sha256,
    ) != (
        cmd.id,
        cmd.operation_kind,
        cmd.correlation_id,
        cmd.evidence_payload,
        cmd.evidence_sha256,
    ):
        raise RepositoryInvariantError()
    order_operation = cmd.operation_kind in ("PLACE_ORDER", "MODIFY_ORDER", "CANCEL_ORDER")
    if (row.order_id is not None) != order_operation:
        raise RepositoryInvariantError()
    if cmd.operation_kind in ("MODIFY_ORDER", "CANCEL_ORDER"):
        if str(row.order_id) != json.loads(cmd.evidence_payload)["target_order_id"]:
            raise RepositoryInvariantError()
    return receipt


async def _place_lineage(
    session: AsyncSession, command: LockedCommand, rows: Sequence[OrderAttempt]
) -> None:
    if command._row.operation_kind != "PLACE_ORDER":
        return
    order_ids = {row.order_id for row in rows}
    with session.no_autoflush:
        matched = (
            await session.scalars(
                select(Order.id).where(
                    *_scope(Order, command.scope),
                    Order.command_id == command.identity,
                    Order.id.in_(order_ids),
                )
            )
        ).all()
    if set(matched) != order_ids:
        raise RepositoryInvariantError()


def _parent(
    session: AsyncSession, command: LockedCommand | LockedCommandClaim, handle: RepositoryLock
) -> None:
    locked = _validate(session, handle, scope=command.scope)
    if locked._data is not command and (
        not isinstance(locked._data, dict) or locked._data.get("command") is not command
    ):
        # A current refinement refers to the same neutral row, never a substituted row.
        data = locked._data
        if not isinstance(data, LockedCommand) or data is not command._source:
            raise RepositoryStateError()


def _order_shape(row: Order) -> None:
    if (
        type(row.product_id) is not int
        or row.product_id < 1
        or type(row.version) is not int
        or row.version < 1
        or row.state not in STATES
        or row.updated_at < row.created_at
    ):
        raise RepositoryInvariantError()
    terminal = row.state in ("FILLED", "CANCELLED", "REJECTED", "EXPIRED")
    if (row.terminal_at is not None) != terminal or (
        row.terminal_at is not None and not row.created_at <= row.terminal_at <= row.updated_at
    ):
        raise RepositoryInvariantError()
    if row.origin in ("LOCAL_INTENT", "SIMULATOR_INTENT"):
        if any(
            getattr(row, key) is None
            for key in (
                "command_id",
                "client_order_id",
                "catalogue_snapshot_id",
                "requested_contracts",
                "requested_side",
                "requested_order_type",
                "requested_reduce_only",
            )
        ):
            raise RepositoryInvariantError()
        if (
            row.requested_contracts is None
            or row.requested_contracts < 1
            or row.requested_side not in ("buy", "sell")
            or row.requested_order_type not in ("market", "limit")
        ):
            raise RepositoryInvariantError()
        if (row.origin == "LOCAL_INTENT") != (row.mode == "LIVE"):
            raise RepositoryInvariantError()
        if (
            row.requested_stop_price is not None
            or (row.requested_order_type == "market" and row.requested_limit_price is not None)
            or (
                row.requested_order_type == "limit"
                and (row.requested_limit_price is None or row.requested_limit_price <= 0)
            )
        ):
            raise RepositoryInvariantError()
    elif (
        row.origin != "ADOPTED"
        or row.mode != "LIVE"
        or row.exchange_order_id is None
        or any(
            getattr(row, key) is not None
            for key in (
                "command_id",
                "signal_id",
                "client_order_id",
                "catalogue_snapshot_id",
                "requested_contracts",
                "requested_side",
                "requested_order_type",
                "requested_limit_price",
                "requested_stop_price",
                "requested_reduce_only",
            )
        )
    ):
        raise RepositoryInvariantError()


def _backend(attempt: OrderAttempt, proposal: AttemptEventProposal) -> None:
    if (
        proposal.event_kind in ("RESPONSE_ACCEPTED", "RESPONSE_REJECTED")
        and attempt.backend != "VENUE"
    ):
        raise RepositoryStateError()
    if (
        proposal.event_kind in ("SIMULATED_ACCEPTED", "SIMULATED_REJECTED")
        and attempt.backend != "SIMULATOR"
    ):
        raise RepositoryStateError()


def _advisory_statement(scope: AccountScope, event_key: str) -> Any:
    namespace = func.jsonb_build_array(
        cast(literal(scope.account_id), Text),
        scope.region,
        scope.environment,
        scope.mode,
        event_key,
    )
    return select(func.pg_advisory_xact_lock(func.hashtextextended(cast(namespace, Text), 0)))


async def _event_key(
    session: AsyncSession, command: LockedCommand, proposal: AttemptEventProposal
) -> LockedEventKey:
    scope = command.scope
    key = (*scope.model_dump().values(), proposal.event_key)
    _before_lock(session, scope, 65, "event_key", key)
    held = session.info.setdefault("_repository_event_key", {})
    transaction = session.sync_session.get_transaction()
    if held.get("transaction") is transaction:
        if held["key"] != key:
            raise RepositoryStateError()
    else:
        if session.get_bind().dialect.name == "postgresql":
            with session.no_autoflush:
                await session.execute(_advisory_statement(scope, proposal.event_key))
        else:
            bind = session.get_bind()
            mutexes = _SQLITE_MUTEXES.setdefault(bind, {})
            mutex = mutexes.setdefault(key, asyncio.Lock())
            await mutex.acquire()

            def release(sync_session: Any, ended: Any) -> None:
                if ended is transaction and mutex.locked():
                    mutex.release()

            event.listen(session.sync_session, "after_transaction_end", release)
        session.info["_repository_event_key"] = dict(transaction=transaction, key=key)
    return _mint_lock(LockedEventKey, session, scope, 65, key, None, data=command)


async def _lock_account_fk(session: AsyncSession, scope: AccountScope, rank: int) -> None:
    """Satisfy the INSERT's scoped account FK before sampling mutation time."""
    _before_lock(session, scope, rank, "0_account", scope.account_id)
    with session.no_autoflush:
        parent = await session.scalar(
            select(Account)
            .where(
                Account.id == scope.account_id,
                Account.region == scope.region,
                Account.environment == scope.environment,
                Account.mode == scope.mode,
            )
            .with_for_update(read=True, key_share=True)
            .execution_options(populate_existing=True)
        )
    if parent is None:
        raise RepositoryInvariantError()
    _remember_row(session, parent)


def _credential_input(scope: AccountScope, version: int | None) -> None:
    if scope.mode == "LIVE":
        try:
            TypeAdapter(PositiveStrictInt).validate_python(version)
        except ValidationError:
            raise PersistenceValueError() from None
    elif version is not None:
        raise PersistenceValueError()


async def _lock_credential_fk(session: AsyncSession, scope: AccountScope, version: int) -> None:
    _before_lock(session, scope, 60, "1_credential", version)
    with session.no_autoflush:
        parent = await session.scalar(
            select(CredentialVersion)
            .where(
                *_scope(CredentialVersion, scope), CredentialVersion.credential_version == version
            )
            .with_for_update(read=True, key_share=True)
            .execution_options(populate_existing=True)
        )
    if parent is None:
        raise RepositoryStateError()
    _remember_row(session, parent)


class AttemptEventRepository:
    @_lock_operation
    async def lock_order_parent(
        self, session: AsyncSession, command: LockedCommand, *, order_id: UUID | None
    ) -> LockedOrderParent:
        _clean(session)
        _neutral(session, command)
        cmd = command._row
        row = None
        order_operation = cmd.operation_kind in ("PLACE_ORDER", "MODIFY_ORDER", "CANCEL_ORDER")
        if order_operation:
            if order_id is None:
                raise RepositoryStateError()
            _uuid(order_id)
            _before_lock(session, command.scope, 40, "order", order_id)
            with session.no_autoflush:
                row = (
                    await session.execute(
                        select(Order)
                        .where(*_scope(Order, command.scope), Order.id == order_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
            if row is None:
                raise RepositoryStateError()
            _order_shape(row)
            intent = json.loads(cmd.evidence_payload)
            if row.product_id != intent["product_id"]:
                raise RepositoryInvariantError()
            if cmd.operation_kind == "PLACE_ORDER":
                expected_price = Decimal(intent["limit_price"]) if "limit_price" in intent else None
                if (
                    row.command_id,
                    row.requested_contracts,
                    row.requested_side,
                    row.requested_order_type,
                    row.requested_reduce_only,
                    row.requested_limit_price,
                ) != (
                    cmd.id,
                    intent["requested_contracts"],
                    intent["side"],
                    intent["order_type"],
                    intent["reduce_only"],
                    expected_price,
                ):
                    raise RepositoryInvariantError()
            elif str(row.id) != intent["target_order_id"]:
                raise RepositoryStateError()
        elif order_id is not None:
            raise PersistenceValueError()
        return _mint_lock(
            LockedOrderParent, session, command.scope, 40, order_id, row, data=command
        )

    @_lock_operation
    async def lock_attempt_set(
        self,
        session: AsyncSession,
        command: LockedCommandClaim,
        order: LockedOrderParent,
        *,
        last_lock: RepositoryLock,
        credential_version: PositiveStrictInt | None = None,
    ) -> LockedAttemptSet:
        _clean(session)
        if type(command) is not LockedCommandClaim or type(order) is not LockedOrderParent:
            raise RepositoryStateError()
        _validate(session, command)
        _parent(session, command, order)
        _predecessor(session, last_lock)
        previous = _validate(session, last_lock, scope=command.scope, maximum=60)
        if command._row.operation_kind in ("PLACE_ORDER", "MODIFY_ORDER"):
            if (
                previous.rank != 50
                or previous._row is None
                or getattr(previous._row, "command_id", None) != command.identity
                or getattr(previous._row, "order_id", None) != order.identity
                or getattr(previous._row, "id", None) != previous.identity
            ):
                raise RepositoryStateError()
        elif last_lock is not order:
            raise RepositoryStateError()
        _credential_input(command.scope, credential_version)
        await _lock_account_fk(session, command.scope, 60)
        if credential_version is not None:
            await _lock_credential_fk(session, command.scope, credential_version)
        _before_lock(session, command.scope, 60, "2_attempt_set", command.identity)
        predicates = [
            *_scope(OrderAttempt, command.scope),
            OrderAttempt.command_id == command.identity,
        ]
        with session.no_autoflush:
            count = (
                await session.execute(
                    select(func.count()).select_from(OrderAttempt).where(*predicates)
                )
            ).scalar_one()
            if count > 64:
                raise RepositoryLimitError()
            rows = (
                (
                    await session.execute(
                        select(OrderAttempt)
                        .where(*predicates)
                        .order_by(OrderAttempt.attempt_number, OrderAttempt.id)
                        .limit(64)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
        if len(rows) != count or count != command._row.attempt_count:
            raise RepositoryInvariantError()
        for number, row in enumerate(rows, 1):
            _attempt_receipt(command, row)
            if row.attempt_number != number or row.order_id != order.identity:
                raise RepositoryInvariantError()
        if count == 64:
            raise RepositoryLimitError()
        return _mint_lock(
            LockedAttemptSet,
            session,
            command.scope,
            60,
            command.identity,
            None,
            data=dict(
                command=command,
                order=order,
                rows=tuple(rows),
                credential_version=credential_version,
            ),
        )

    @_lock_operation
    async def allocate_attempt_at(
        self,
        session: AsyncSession,
        command: LockedCommandClaim,
        order: LockedOrderParent,
        attempt_set: LockedAttemptSet,
        *,
        leadership: LockedCurrentLeadership | None,
        sampled_at: LockedDatabaseTime,
        credential_version: PositiveStrictInt | None,
    ) -> AttemptAllocationResult:
        now = consume_database_time(session, sampled_at, attempt_set)
        if (
            type(command) is not LockedCommandClaim
            or type(order) is not LockedOrderParent
            or type(attempt_set) is not LockedAttemptSet
        ):
            raise RepositoryStateError()
        _validate(session, command)
        _validate(session, order, scope=command.scope)
        _parent(session, command, attempt_set)
        if attempt_set._data["order"] is not order:
            raise RepositoryStateError()
        _credential_input(command.scope, credential_version)
        if credential_version != attempt_set._data["credential_version"]:
            raise PersistenceValueError()
        count = len(attempt_set._data["rows"])
        if count >= 64:
            raise RepositoryLimitError()
        if count != command._row.attempt_count:
            raise RepositoryInvariantError()
        _claim_at(command, leadership, now)
        cmd = command._row
        live = command.scope.mode == "LIVE"
        values = dict(
            **command.scope.model_dump(),
            command_id=cmd.id,
            order_id=order.identity,
            attempt_number=count + 1,
            operation_kind=cmd.operation_kind,
            backend="VENUE" if live else "SIMULATOR",
            simulator_kind=None if live else command.scope.mode,
            credential_version=credential_version,
            correlation_id=cmd.correlation_id,
            prepared_at=now.value,
            recorded_at=now.value,
            original_claim_owner=cmd.claim_owner if live else None,
            original_claim_generation=cmd.claim_generation if live else None,
            original_claim_expires_at=cmd.claim_expires_at if live else None,
            original_leader_ref=cmd.leader_ref if live else None,
            original_leader_generation=cmd.leader_generation if live else None,
            original_recovery_ref=cmd.recovery_ref if live else None,
            original_recovery_generation=cmd.recovery_generation if live else None,
            evidence_schema_version=cmd.evidence_schema_version,
            evidence_payload=cmd.evidence_payload,
            evidence_sha256=cmd.evidence_sha256,
        )
        row = OrderAttempt(id=uuid4(), **values)
        _attempt_receipt(command, row)
        session.add(row)
        cmd.attempt_count += 1
        cmd.updated_at = now.value
        await _flush(session)
        return AttemptAllocationResult(
            command=_command(command.scope, cmd),
            claim=_claim(cmd),
            attempt=_attempt_receipt(command, row),
        )

    @_lock_operation
    async def lock_attempt(
        self, session: AsyncSession, command: LockedCommand, attempt_id: UUID
    ) -> LockedAttempt:
        _clean(session)
        _neutral(session, command)
        _uuid(attempt_id)
        _before_lock(session, command.scope, 60, "3_attempt", attempt_id)
        with session.no_autoflush:
            row = (
                await session.execute(
                    select(OrderAttempt)
                    .where(
                        *_scope(OrderAttempt, command.scope),
                        OrderAttempt.id == attempt_id,
                        OrderAttempt.command_id == command.identity,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
        if row is None:
            raise RepositoryStateError()
        _attempt_receipt(command, row)
        await _place_lineage(session, command, (row,))
        return _mint_lock(LockedAttempt, session, command.scope, 60, row.id, row, data=command)

    @_lock_operation
    async def lock_event_stream(
        self,
        session: AsyncSession,
        command: LockedCommand,
        attempt: LockedAttempt,
        *,
        proposal: AttemptEventProposal,
    ) -> EventAppendResult | LockedEventStream:
        _clean(session)
        _neutral(session, command)
        if type(attempt) is not LockedAttempt:
            raise RepositoryStateError()
        _parent(session, command, attempt)
        proposal = _input(AttemptEventProposal, proposal)
        _backend(attempt._row, proposal)
        await _event_key(session, command, proposal)
        payload, digest = encode_evidence("event", proposal.event_kind, 1, proposal.evidence)
        with session.no_autoflush:
            existing = (
                await session.execute(
                    select(OrderAttemptEvent).where(
                        *_scope(OrderAttemptEvent, command.scope),
                        OrderAttemptEvent.event_key == proposal.event_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                receipt = _receipt(AttemptEventReceipt, command.scope, existing, "event_id")
                fields = proposal.model_dump(exclude={"evidence"})
                if (
                    existing.attempt_id != attempt.identity
                    or existing.backend != attempt._row.backend
                    or (existing.evidence_payload, existing.evidence_sha256) != (payload, digest)
                    or any(getattr(existing, key) != value for key, value in fields.items())
                ):
                    raise RepositoryConflictError()
                return EventAppendResult(disposition=EventDisposition.EXISTING, event=receipt)
            parent_ids = select(OrderAttempt.id).where(
                *_scope(OrderAttempt, command.scope), OrderAttempt.command_id == command.identity
            )
            predicates = [
                *_scope(OrderAttemptEvent, command.scope),
                OrderAttemptEvent.attempt_id.in_(parent_ids),
            ]
            count = (
                await session.execute(
                    select(func.count()).select_from(OrderAttemptEvent).where(*predicates)
                )
            ).scalar_one()
            if count >= 64:
                raise RepositoryLimitError()
            await _lock_account_fk(session, command.scope, 70)
            _before_lock(session, command.scope, 70, "events", attempt.identity)
            rows = (
                (
                    await session.execute(
                        select(OrderAttemptEvent)
                        .where(*predicates)
                        .order_by(
                            OrderAttemptEvent.attempt_id,
                            OrderAttemptEvent.sequence,
                            OrderAttemptEvent.id,
                        )
                        .limit(64)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            parents = (
                (
                    await session.execute(
                        select(OrderAttempt)
                        .where(
                            *_scope(OrderAttempt, command.scope),
                            OrderAttempt.command_id == command.identity,
                        )
                        .order_by(OrderAttempt.attempt_number, OrderAttempt.id)
                        .limit(65)
                    )
                )
                .scalars()
                .all()
            )
        if len(rows) != count or len(parents) > 64 or len(parents) != command._row.attempt_count:
            raise RepositoryInvariantError()
        await _place_lineage(session, command, parents)
        parent_map = {}
        for number, parent in enumerate(parents, 1):
            _attempt_receipt(command, parent)
            if parent.attempt_number != number:
                raise RepositoryInvariantError()
            parent_map[parent.id] = parent
        sequences: dict[object, int] = {}
        for row in rows:
            _receipt(AttemptEventReceipt, command.scope, row, "event_id")
            previous = sequences.get(row.attempt_id, 0)
            if (
                row.sequence != previous + 1
                or row.attempt_id not in parent_map
                or row.backend != parent_map[row.attempt_id].backend
            ):
                raise RepositoryInvariantError()
            sequences[row.attempt_id] = row.sequence
        return _mint_lock(
            LockedEventStream,
            session,
            command.scope,
            70,
            attempt.identity,
            None,
            data=dict(
                command=command,
                attempt=attempt,
                proposal=proposal,
                payload=payload,
                digest=digest,
                sequence=sequences.get(attempt.identity, 0) + 1,
            ),
        )

    @_lock_operation
    async def append_event_at(
        self,
        session: AsyncSession,
        command: LockedCommand,
        attempt: LockedAttempt,
        stream: LockedEventStream,
        *,
        sampled_at: LockedDatabaseTime,
    ) -> EventAppendResult:
        now = consume_database_time(session, sampled_at, stream)
        _neutral(session, command)
        if type(attempt) is not LockedAttempt or type(stream) is not LockedEventStream:
            raise RepositoryStateError()
        _parent(session, command, attempt)
        _parent(session, command, stream)
        if stream._data["attempt"] is not attempt:
            raise RepositoryStateError()
        proposal = _input(AttemptEventProposal, stream._data["proposal"])
        _backend(attempt._row, proposal)
        if command._row.status in ("FAILED", "CANCELLED", "COMPLETED"):
            raise RepositoryStateError()
        if proposal.observed_at is not None and proposal.observed_at > now.value:
            raise PersistenceValueError()
        values = proposal.model_dump(exclude={"evidence"})
        row = OrderAttemptEvent(
            id=uuid4(),
            **command.scope.model_dump(),
            attempt_id=attempt.identity,
            backend=attempt._row.backend,
            sequence=stream._data["sequence"],
            recorded_at=now.value,
            evidence_schema_version=1,
            evidence_payload=stream._data["payload"],
            evidence_sha256=stream._data["digest"],
            **values,
        )
        receipt = _receipt(AttemptEventReceipt, command.scope, row, "event_id")
        session.add(row)
        await _flush(session)
        return EventAppendResult(disposition=EventDisposition.APPENDED, event=receipt)
