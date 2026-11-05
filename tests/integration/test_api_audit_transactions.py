"""Atomic append, replay, conflict and rollback acceptance for D13c."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from api_audit_service_support import (
    append_root,
    attempt_record,
    clean_session,
    retained,
    root_record,
    row_counts,
    seed_stream_end,
    summary_only,
)
from api_audit_service_support import audit_service as audit_service
from api_audit_service_support import audit_sessions as audit_sessions
from execution_test_support import schema_db as schema_db
from sqlalchemy import event, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from hmt_crypto.persistence.api_audit_models import ApiAuditEvent
from hmt_crypto.persistence.api_audit_payload_models import ApiAuditPayload
from hmt_crypto.persistence.api_audit_repository import ApiAuditRepository
from hmt_crypto.persistence.api_audit_service import ApiAuditTransactionService
from hmt_crypto.persistence.api_audit_service_errors import (
    AuditCommitUncertain,
    AuditConflict,
    AuditEvidenceRejected,
    AuditInvariantViolation,
    AuditTransactionStateError,
    AuditUnavailable,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_root_commit_and_exact_replay_return_identical_receipt(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = summary_only(root_record())
    async with clean_session(audit_sessions) as session:
        original = await audit_service.append(session=session, request=request)
    assert original.event_id == original.root_event_id
    assert original.sequence == 1
    assert await row_counts(audit_sessions) == (1, 0)

    def allocation_forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("replay allocated fresh storage outputs")

    monkeypatch.setattr("hmt_crypto.persistence.api_audit_repository.uuid4", allocation_forbidden)
    monkeypatch.setattr(
        "hmt_crypto.persistence.api_audit_repository._database_utc",
        allocation_forbidden,
    )
    monkeypatch.setattr(
        "hmt_crypto.persistence.api_audit_repository._new_descriptor",
        allocation_forbidden,
    )
    monkeypatch.setattr(
        "hmt_crypto.persistence.api_audit_repository._sequence",
        allocation_forbidden,
    )
    async with clean_session(audit_sessions) as session:
        replayed = await audit_service.append(session=session, request=request)
    assert replayed == original
    assert await row_counts(audit_sessions) == (1, 0)


@pytest.mark.asyncio
async def test_service_reconstructs_owned_input_before_the_first_database_read(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supplied = summary_only(root_record())
    original = ApiAuditRepository._append_or_replay

    async def inspect_owned(self, session, request):
        assert request is not supplied
        assert request.record is not supplied.record
        assert request.retention is not supplied.retention
        return await original(self, session, request)

    monkeypatch.setattr(ApiAuditRepository, "_append_or_replay", inspect_owned)
    async with clean_session(audit_sessions) as session:
        receipt = await audit_service.append(session=session, request=supplied)
    assert receipt.sequence == 1


@pytest.mark.asyncio
async def test_retained_copy_commits_atomically_and_exact_replay_is_stable(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    root = await append_root(audit_service, audit_sessions)
    request = retained(attempt_record(root.root_event_id))
    async with clean_session(audit_sessions) as session:
        original = await audit_service.append(session=session, request=request)

    assert original.sequence == 2
    assert original.payload_slot is not None
    assert original.payload_copy_key is not None
    assert await row_counts(audit_sessions) == (2, 1)
    async with audit_sessions() as session:
        payload = await session.get(
            ApiAuditPayload, (original.event_id, str.__str__(original.payload_slot))
        )
        assert payload is not None
        assert payload.copy_key == original.payload_copy_key
        assert payload.payload_sha256 == original.payload_sha256

    async with clean_session(audit_sessions) as session:
        replayed = await audit_service.append(session=session, request=request)
    assert replayed == original
    assert await row_counts(audit_sessions) == (2, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ("evidence", "retention"))
async def test_same_event_key_with_divergent_canonical_state_conflicts(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    change: str,
) -> None:
    root = await append_root(audit_service, audit_sessions)
    record = attempt_record(root.root_event_id)
    original = retained(record)
    async with clean_session(audit_sessions) as session:
        await audit_service.append(session=session, request=original)

    if change == "evidence":
        divergent = retained(attempt_record(root.root_event_id, evidence="b" * 64))
    else:
        divergent = summary_only(record)
    async with clean_session(audit_sessions) as session:
        with pytest.raises(AuditConflict):
            await audit_service.append(session=session, request=divergent)
    assert await row_counts(audit_sessions) == (2, 1)


@pytest.mark.asyncio
async def test_missing_parent_is_rejected_without_partial_rows(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    request = retained(attempt_record(UUID(int=999)))
    async with clean_session(audit_sessions) as session:
        with pytest.raises(AuditEvidenceRejected):
            await audit_service.append(session=session, request=request)
    assert await row_counts(audit_sessions) == (0, 0)


@pytest.mark.asyncio
async def test_semantically_mismatched_parent_is_rejected_atomically(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    root = await append_root(audit_service, audit_sessions)
    mismatched_call = UUID(int=701)
    request = retained(attempt_record(root.root_event_id, call_id=mismatched_call))
    async with clean_session(audit_sessions) as session:
        with pytest.raises(AuditEvidenceRejected) as captured:
            await audit_service.append(session=session, request=request)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert await row_counts(audit_sessions) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ("paired", "standalone_finish"))
async def test_preseeded_completed_stream_rejects_every_fresh_append(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    state: str,
) -> None:
    root = await append_root(audit_service, audit_sessions)
    await seed_stream_end(audit_sessions, root, state)  # type: ignore[arg-type]
    async with clean_session(audit_sessions) as session:
        with pytest.raises(AuditConflict):
            await audit_service.append(
                session=session,
                request=retained(attempt_record(root.root_event_id)),
            )
    expected = 3 if state == "paired" else 2
    assert await row_counts(audit_sessions) == (expected, 0)


@pytest.mark.asyncio
async def test_preseeded_terminal_without_finish_is_corruption_before_allocation(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = await append_root(audit_service, audit_sessions)
    await seed_stream_end(audit_sessions, root, "terminal_only")

    def allocation_forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("corrupt stream reached allocation")

    monkeypatch.setattr("hmt_crypto.persistence.api_audit_repository.uuid4", allocation_forbidden)
    monkeypatch.setattr(
        "hmt_crypto.persistence.api_audit_repository._database_utc",
        allocation_forbidden,
    )
    monkeypatch.setattr(
        "hmt_crypto.persistence.api_audit_repository._new_descriptor",
        allocation_forbidden,
    )
    monkeypatch.setattr(
        "hmt_crypto.persistence.api_audit_repository._sequence",
        allocation_forbidden,
    )
    async with clean_session(audit_sessions) as session:
        with pytest.raises(AuditInvariantViolation):
            await audit_service.append(
                session=session,
                request=retained(attempt_record(root.root_event_id)),
            )
    assert await row_counts(audit_sessions) == (2, 0)


def test_stream_inspection_uses_the_complete_terminal_domain() -> None:
    import hmt_crypto.persistence.api_audit_repository as repository

    terminals = {
        "LOCAL_REJECTED",
        "VENUE_NO_ENTRY",
        "VENUE_ENTERED",
        "VENUE_TRANSFER_REJECTED",
        "SIMULATOR_OUTCOME",
    }
    assert frozenset(repository._TERMINAL_EVENT_KINDS) == terminals
    assert frozenset(repository._STREAM_END_KINDS) == terminals | {"CALL_FINISHED"}


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_metadata_and_payload_and_returns_no_receipt(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    root = await append_root(audit_service, audit_sessions)
    request = retained(attempt_record(root.root_event_id))

    async with audit_sessions() as session:

        def reject_commit(sync_session: object) -> None:
            del sync_session
            raise RuntimeError("secret commit canary")

        event.listen(session.sync_session, "before_commit", reject_commit, once=True)
        with pytest.raises(AuditCommitUncertain) as captured:
            await audit_service.append(session=session, request=request)
        assert "secret commit canary" not in str(captured.value)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
    assert await row_counts(audit_sessions) == (1, 0)


@pytest.mark.asyncio
async def test_cancellation_during_context_exit_rolls_back_and_escapes_unchanged(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    root = await append_root(audit_service, audit_sessions)
    request = retained(attempt_record(root.root_event_id))

    async with audit_sessions() as session:

        def cancel_context_exit(sync_session: object) -> None:
            del sync_session
            raise asyncio.CancelledError

        event.listen(session.sync_session, "before_commit", cancel_context_exit, once=True)
        with pytest.raises(asyncio.CancelledError):
            await audit_service.append(session=session, request=request)
    assert await row_counts(audit_sessions) == (1, 0)


@pytest.mark.asyncio
async def test_receipt_is_constructed_only_after_one_positive_context_exit(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hmt_crypto.persistence.api_audit_service as service_module

    request = summary_only(root_record())
    transaction_started = 0
    transaction_committed = False
    original_receipt = service_module._receipt

    async with audit_sessions() as session:

        def began(sync_session: object, transaction: object, connection: object) -> None:
            nonlocal transaction_started
            del sync_session, transaction, connection
            transaction_started += 1

        def committed(sync_session: object) -> None:
            nonlocal transaction_committed
            del sync_session
            transaction_committed = True

        def receipt_after_exit(provisional):
            assert transaction_committed is True
            return original_receipt(provisional)

        event.listen(session.sync_session, "after_begin", began)
        event.listen(session.sync_session, "after_commit", committed)
        monkeypatch.setattr(service_module, "_receipt", receipt_after_exit)
        receipt = await audit_service.append(session=session, request=request)
    assert receipt.sequence == 1
    assert transaction_started == 1


@pytest.mark.asyncio
async def test_active_or_pending_session_is_rejected_before_any_sql(
    schema_db,
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    statements = 0

    def counted(*args: object, **kwargs: object) -> None:
        nonlocal statements
        del args, kwargs
        statements += 1

    event.listen(schema_db.engine.sync_engine, "before_cursor_execute", counted)
    try:
        async with audit_sessions() as session:
            async with session.begin():
                before = statements
                with pytest.raises(AuditTransactionStateError):
                    await audit_service.append(session=session, request=summary_only(root_record()))
                assert statements == before

        async with audit_sessions() as session:
            session.add(ApiAuditEvent())
            before = statements
            with pytest.raises(AuditTransactionStateError):
                await audit_service.append(session=session, request=summary_only(root_record()))
            assert statements == before
    finally:
        event.remove(schema_db.engine.sync_engine, "before_cursor_execute", counted)


@pytest.mark.asyncio
async def test_autoflush_session_is_rejected_before_any_sql(
    schema_db,
    audit_service: ApiAuditTransactionService,
) -> None:
    statements = 0

    def counted(*args: object, **kwargs: object) -> None:
        nonlocal statements
        del args, kwargs
        statements += 1

    sessions = async_sessionmaker(schema_db.engine, expire_on_commit=False, autoflush=True)
    event.listen(schema_db.engine.sync_engine, "before_cursor_execute", counted)
    try:
        async with sessions() as session:
            with pytest.raises(AuditTransactionStateError):
                await audit_service.append(session=session, request=summary_only(root_record()))
            assert statements == 0
    finally:
        event.remove(schema_db.engine.sync_engine, "before_cursor_execute", counted)


@pytest.mark.asyncio
async def test_nonempty_identity_map_is_rejected_before_replay_sql(
    schema_db,
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    root = await append_root(audit_service, audit_sessions)
    statements = 0

    def counted(*args: object, **kwargs: object) -> None:
        nonlocal statements
        del args, kwargs
        statements += 1

    event.listen(schema_db.engine.sync_engine, "before_cursor_execute", counted)
    try:
        async with audit_sessions() as session:
            cached = await session.get(ApiAuditEvent, root.event_id)
            assert cached is not None
            await session.commit()
            assert len(session.identity_map) == 1
            before = statements
            with pytest.raises(AuditTransactionStateError):
                await audit_service.append(session=session, request=summary_only(root_record()))
            assert statements == before
    finally:
        event.remove(schema_db.engine.sync_engine, "before_cursor_execute", counted)


@pytest.mark.asyncio
async def test_hostile_reconstructed_session_state_has_fixed_safe_error(
    audit_service: ApiAuditTransactionService,
) -> None:
    class HostileState:
        def in_transaction(self) -> bool:
            raise RuntimeError("secret session canary")

    reconstructed = object.__new__(AsyncSession)
    object.__setattr__(reconstructed, "sync_session", HostileState())
    with pytest.raises(AuditTransactionStateError) as captured:
        await audit_service.append(
            session=reconstructed,
            request=summary_only(root_record()),
        )
    assert "secret session canary" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_hostile_exact_session_inspection_has_fixed_safe_error(
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hostile_state(sync_session: Session) -> bool:
        del sync_session
        raise RuntimeError("secret exact-session canary")

    async with audit_sessions() as session:
        monkeypatch.setattr(Session, "in_transaction", hostile_state)
        with pytest.raises(AuditTransactionStateError) as captured:
            await audit_service.append(
                session=session,
                request=summary_only(root_record()),
            )
    assert "secret exact-session canary" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_nonexact_request_is_rejected_without_sql_or_exception_chain(
    schema_db,
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    statements = 0

    def counted(*args: object, **kwargs: object) -> None:
        nonlocal statements
        del args, kwargs
        statements += 1

    event.listen(schema_db.engine.sync_engine, "before_cursor_execute", counted)
    try:
        async with audit_sessions() as session:
            with pytest.raises(AuditEvidenceRejected) as captured:
                await audit_service.append(session=session, request=object())  # type: ignore[arg-type]
            assert statements == 0
            assert captured.value.__cause__ is None
            assert captured.value.__context__ is None
    finally:
        event.remove(schema_db.engine.sync_engine, "before_cursor_execute", counted)


@pytest.mark.asyncio
async def test_lookup_outage_is_normalized_without_raw_driver_context(
    schema_db,
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
) -> None:
    def fail_lookup(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise SQLAlchemyError("secret query canary")

    event.listen(schema_db.engine.sync_engine, "before_cursor_execute", fail_lookup, once=True)
    async with audit_sessions() as session:
        with pytest.raises(AuditUnavailable) as captured:
            await audit_service.append(session=session, request=summary_only(root_record()))
    assert "secret query canary" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert await row_counts(audit_sessions) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ("dirty", "deleted"))
async def test_dirty_or_deleted_session_is_rejected_before_additional_sql(
    schema_db,
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    state: str,
) -> None:
    root = await append_root(audit_service, audit_sessions)
    statements = 0

    def counted(*args: object, **kwargs: object) -> None:
        nonlocal statements
        del args, kwargs
        statements += 1

    event.listen(schema_db.engine.sync_engine, "before_cursor_execute", counted)
    try:
        async with audit_sessions() as session:
            row = await session.get(ApiAuditEvent, root.event_id)
            assert row is not None
            await session.commit()
            if state == "dirty":
                row.sequence = 2
            else:
                await session.delete(row)
            before = statements
            with pytest.raises(AuditTransactionStateError):
                await audit_service.append(session=session, request=summary_only(root_record()))
            assert statements == before
    finally:
        event.remove(schema_db.engine.sync_engine, "before_cursor_execute", counted)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ("metadata", "payload"))
async def test_replay_rejects_corrupt_stored_metadata_or_copy(
    schema_db,
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    corruption: str,
) -> None:
    root = await append_root(audit_service, audit_sessions)
    request = retained(attempt_record(root.root_event_id))
    async with clean_session(audit_sessions) as session:
        receipt = await audit_service.append(session=session, request=request)

    async with schema_db.engine.begin() as connection:
        if corruption == "metadata":
            await connection.execute(
                update(ApiAuditEvent)
                .where(ApiAuditEvent.id == receipt.event_id)
                .values(summary_sha256="f" * 64)
            )
        else:
            row = (
                await connection.execute(
                    select(ApiAuditPayload.payload_text).where(
                        ApiAuditPayload.owning_event_id == receipt.event_id
                    )
                )
            ).scalar_one()
            corrupted = row.replace('"GET"', '"PUT"', 1)
            assert corrupted != row and len(corrupted) == len(row)
            await connection.execute(
                update(ApiAuditPayload)
                .where(ApiAuditPayload.owning_event_id == receipt.event_id)
                .values(payload_text=corrupted)
            )

    async with clean_session(audit_sessions) as session:
        with pytest.raises(AuditInvariantViolation) as captured:
            await audit_service.append(session=session, request=request)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ("metadata", "payload"))
async def test_repository_refreshes_expire_false_cached_rows_before_replay(
    schema_db,
    audit_service: ApiAuditTransactionService,
    audit_sessions: async_sessionmaker[AsyncSession],
    corruption: str,
) -> None:
    root = await append_root(audit_service, audit_sessions)
    request = retained(attempt_record(root.root_event_id))
    async with clean_session(audit_sessions) as session:
        receipt = await audit_service.append(session=session, request=request)

    async with audit_sessions() as stale:
        cached_event = await stale.get(ApiAuditEvent, receipt.event_id)
        cached_payload = await stale.get(ApiAuditPayload, (receipt.event_id, "REQUEST"))
        assert cached_event is not None and cached_payload is not None
        await stale.commit()
        async with schema_db.engine.begin() as connection:
            if corruption == "metadata":
                await connection.execute(
                    update(ApiAuditEvent)
                    .where(ApiAuditEvent.id == receipt.event_id)
                    .values(summary_sha256="e" * 64)
                )
            else:
                corrupted = cached_payload.payload_text.replace('"GET"', '"PUT"', 1)
                assert corrupted != cached_payload.payload_text
                await connection.execute(
                    update(ApiAuditPayload)
                    .where(ApiAuditPayload.owning_event_id == receipt.event_id)
                    .values(payload_text=corrupted)
                )
        with pytest.raises(AuditInvariantViolation):
            async with stale.begin():
                await ApiAuditRepository()._append_or_replay(stale, request)


def test_repository_and_service_expose_no_generic_mutation_authority() -> None:
    from hmt_crypto.persistence.api_audit_repository import ApiAuditRepository

    forbidden = {"add", "upsert", "update", "delete", "commit", "rollback", "execute"}
    assert forbidden.isdisjoint(vars(ApiAuditRepository))
    assert forbidden.isdisjoint(vars(ApiAuditTransactionService))
