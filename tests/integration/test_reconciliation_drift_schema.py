"""Offline drift/review evidence; acknowledgements never clear safety state."""

import subprocess
import sys
from datetime import timedelta
from itertools import product
from uuid import uuid4

import pytest
from schema_test_support import HASH, NOW, account, schema_api, scope
from schema_test_support import schema_db as schema_db
from sqlalchemy import UniqueConstraint, delete, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import load_only
from sqlalchemy.schema import CreateIndex, CreateTable
from test_coordination_schema import SCOPE, graph, incarnation, save
from test_reconciliation_resource_schema import resource as collected_resource
from test_reconciliation_run_schema import run

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError

DRIFT_KINDS = (
    "RESOURCE_INCOMPLETE",
    "UNRESOLVED_OPERATION",
    "UNMANAGED_ORDER",
    "ORDER_MISSING_AT_EXCHANGE",
    "ORDER_STATE_MISMATCH",
    "FILL_MISMATCH",
    "UNMANAGED_POSITION",
    "POSITION_MISMATCH",
    "BALANCE_MISMATCH",
    "ACCOUNTING_MISMATCH",
    "UNKNOWN_EXPOSURE",
    "INADEQUATE_PROTECTION",
    "UNCLASSIFIED",
)
HARD_KINDS = {
    "RESOURCE_INCOMPLETE",
    "UNRESOLVED_OPERATION",
    "UNKNOWN_EXPOSURE",
    "INADEQUATE_PROTECTION",
    "UNCLASSIFIED",
}
SUBJECT_KINDS = (
    "ACCOUNT",
    "RESOURCE",
    "COMMAND",
    "ORDER",
    "FILL",
    "POSITION",
    "BALANCE",
    "WALLET_TRANSACTION",
    "PROTECTION",
    "ACCOUNTING",
)
SEVERITIES = ("INFO", "WARNING", "CRITICAL")
POLICIES = ("REVIEWABLE", "NON_OVERRIDABLE")
REASONS = (
    "EXPECTED_EXTERNAL_ACTIVITY",
    "ADOPTION_REVIEWED",
    "FALSE_POSITIVE",
    "OPERATOR_NOTED",
)


async def parents(db, *, owner=None, include_resource=True):
    if owner is None:
        owner, recovery = await graph(db)
    else:
        recovery = incarnation(owner)
        await save(db, owner)
        await save(db, recovery)
    reconciliation = run(owner, recovery)
    await save(db, reconciliation)
    resource = None
    if include_resource:
        resource = collected_resource(owner, reconciliation)
        await save(db, resource)
    return owner, recovery, reconciliation, resource


def drift(owner, reconciliation, resource=None, **changes):
    api = schema_api()
    assert hasattr(api, "ReconciliationDrift"), "Missing ReconciliationDrift export"
    values = dict(
        id=uuid4(),
        **scope(owner),
        run_id=reconciliation.id,
        resource_id=None if resource is None else resource.id,
        resource_kind=None if resource is None else resource.resource_kind,
        drift_kind="UNKNOWN_EXPOSURE",
        subject_kind="ACCOUNT",
        subject_key_sha256=HASH,
        product_id=84,
        asset_id=None,
        severity="CRITICAL",
        acknowledgement_policy="NON_OVERRIDABLE",
        detected_at=NOW,
        recorded_at=NOW + timedelta(seconds=1),
        comparison="UNCERTAINTY",
        expected_sha256="b" * 64,
        observed_sha256=None,
    )
    values.update(changes)
    return api.ReconciliationDrift.from_evidence(**values)


def acknowledgement(owner, discrepancy, **changes):
    api = schema_api()
    assert hasattr(api, "ReconciliationAcknowledgement"), (
        "Missing ReconciliationAcknowledgement export"
    )
    policy = changes.get("acknowledgement_policy", discrepancy.acknowledgement_policy)
    values = dict(
        id=uuid4(),
        **scope(owner),
        drift_id=discrepancy.id,
        run_id=discrepancy.run_id,
        drift_kind=discrepancy.drift_kind,
        acknowledgement_policy=policy,
        idempotency_key="ack-001",
        acknowledgement_kind=("REVIEW_EVIDENCE" if policy == "REVIEWABLE" else "NOTICE_EVIDENCE"),
        safety_effect="NONE",
        actor_kind="OPERATOR",
        actor_ref="ops@example.invalid",
        reason_code="OPERATOR_NOTED",
        acknowledged_at=NOW + timedelta(seconds=2),
        recorded_at=NOW + timedelta(seconds=3),
        supporting_evidence_sha256=None,
    )
    values.update(changes)
    return api.ReconciliationAcknowledgement.from_evidence(**values)


def test_factories_own_inputs_and_reject_evidence_or_secret_overrides():
    owner = account()
    reconciliation = type("Run", (), {"id": uuid4()})()
    values = dict(
        subject_key_sha256=HASH,
        comparison="UNCERTAINTY",
        expected_sha256=None,
        observed_sha256=None,
    )
    row = drift(owner, reconciliation, product_id=None, **values)
    values["subject_key_sha256"] = "c" * 64
    assert row.subject_key_sha256 == HASH
    for field, value in (
        ("evidence_schema_version", 1),
        ("evidence_payload", "{}"),
        ("evidence_sha256", HASH),
        ("api_secret", "SECRET-DRIFT-MARKER"),
    ):
        with pytest.raises(PersistenceValueError) as exc:
            drift(owner, reconciliation, **{field: value})
        assert "SECRET-DRIFT-MARKER" not in str(exc.value)
        assert "SECRET-DRIFT-MARKER" not in repr(exc.value)

    ack_values = dict(actor_ref="operator", supporting_evidence_sha256=HASH)
    review = acknowledgement(owner, row, **ack_values)
    ack_values["actor_ref"] = "changed"
    assert review.actor_ref == "operator"
    for field, value in (
        ("evidence_schema_version", 1),
        ("evidence_payload", "{}"),
        ("evidence_sha256", HASH),
        ("operator_note", "SECRET-ACK-MARKER"),
    ):
        with pytest.raises(PersistenceValueError) as exc:
            acknowledgement(owner, row, **{field: value})
        assert "SECRET-ACK-MARKER" not in str(exc.value)
        assert "SECRET-ACK-MARKER" not in repr(exc.value)


def test_exact_two_tables_exports_columns_constraints_indexes_and_postgres_ddl():
    api = schema_api()
    assert hasattr(api, "ReconciliationDrift")
    assert hasattr(api, "ReconciliationAcknowledgement")
    drift_table = api.ReconciliationDrift.__table__
    acknowledgement_table = api.ReconciliationAcknowledgement.__table__
    assert {drift_table.name, acknowledgement_table.name} == {
        "reconciliation_drifts",
        "reconciliation_acknowledgements",
    }
    assert set(drift_table.c.keys()) == {
        "id",
        *SCOPE,
        "run_id",
        "resource_id",
        "resource_kind",
        "drift_kind",
        "subject_kind",
        "subject_key_sha256",
        "product_id",
        "asset_id",
        "severity",
        "acknowledgement_policy",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
        "detected_at",
        "recorded_at",
    }
    assert set(acknowledgement_table.c.keys()) == {
        "id",
        *SCOPE,
        "drift_id",
        "run_id",
        "drift_kind",
        "acknowledgement_policy",
        "idempotency_key",
        "acknowledgement_kind",
        "safety_effect",
        "actor_kind",
        "actor_ref",
        "reason_code",
        "evidence_schema_version",
        "evidence_payload",
        "evidence_sha256",
        "acknowledged_at",
        "recorded_at",
    }
    assert {
        tuple(column.name for column in constraint.columns)
        for constraint in drift_table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {
        ("id", *SCOPE),
        (*SCOPE, "run_id", "drift_kind", "subject_kind", "subject_key_sha256"),
        ("id", *SCOPE, "run_id", "drift_kind", "acknowledgement_policy"),
    }
    assert {
        tuple(column.name for column in constraint.columns)
        for constraint in acknowledgement_table.constraints
        if isinstance(constraint, UniqueConstraint)
    } == {("id", *SCOPE), (*SCOPE, "idempotency_key")}
    expected_fks = {
        "reconciliation_drifts": {
            "accounts": (SCOPE, ("id", *SCOPE[1:])),
            "reconciliation_runs": (("run_id", *SCOPE), ("id", *SCOPE)),
            "reconciliation_resources": (
                ("resource_id", *SCOPE, "run_id", "resource_kind"),
                ("id", *SCOPE, "run_id", "resource_kind"),
            ),
        },
        "reconciliation_acknowledgements": {
            "accounts": (SCOPE, ("id", *SCOPE[1:])),
            "reconciliation_drifts": (
                ("drift_id", *SCOPE, "run_id", "drift_kind", "acknowledgement_policy"),
                ("id", *SCOPE, "run_id", "drift_kind", "acknowledgement_policy"),
            ),
        },
    }
    for table in (drift_table, acknowledgement_table):
        expected = expected_fks[table.name]
        assert {fk.referred_table.name for fk in table.foreign_key_constraints} == set(expected)
        indexes = {tuple(column.name for column in index.columns) for index in table.indexes}
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "UUID" in ddl and "BIGINT" in ddl and "TIMESTAMP WITH TIME ZONE" in ddl
        for constraint in table.constraints:
            assert constraint.name and constraint.name in ddl
        for fk in table.foreign_key_constraints:
            source, target = expected[fk.referred_table.name]
            assert tuple(fk.column_keys) == source
            assert tuple(element.column.name for element in fk.elements) == target
            assert fk.ondelete == fk.onupdate == "RESTRICT"
            assert source in indexes
        for index in table.indexes:
            assert index.name in str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    for table in api.metadata.tables.values():
        if table not in {drift_table, acknowledgement_table}:
            assert "reconciliation_acknowledgements" not in {
                fk.referred_table.name for fk in table.foreign_key_constraints
            }
            if table.name != "reconciliation_acknowledgements":
                assert "reconciliation_drifts" not in {
                    fk.referred_table.name for fk in table.foreign_key_constraints
                }


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("kind,severity,policy", list(product(DRIFT_KINDS, SEVERITIES, POLICIES)))
async def test_complete_drift_kind_severity_policy_matrix(
    schema_db, channel, kind, severity, policy
):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    valid = kind not in HARD_KINDS or (severity == "CRITICAL" and policy == "NON_OVERRIDABLE")
    if valid:
        await save(
            schema_db,
            drift(
                owner,
                reconciliation,
                drift_kind=kind,
                severity=severity,
                acknowledgement_policy=policy,
            ),
            channel,
        )
    else:
        with pytest.raises(PersistenceError):
            await save(
                schema_db,
                drift(
                    owner,
                    reconciliation,
                    drift_kind=kind,
                    severity=severity,
                    acknowledgement_policy=policy,
                ),
                channel,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("subject_kind", [*SUBJECT_KINDS, "READY", "", None, True])
async def test_subject_kind_is_closed(schema_db, channel, subject_kind):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    if subject_kind in SUBJECT_KINDS:
        await save(
            schema_db,
            drift(owner, reconciliation, subject_kind=subject_kind),
            channel,
        )
    else:
        with pytest.raises(PersistenceError):
            await save(
                schema_db,
                drift(owner, reconciliation, subject_kind=subject_kind),
                channel,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("resource_present,kind_present", list(product([False, True], repeat=2)))
async def test_optional_resource_is_exact_all_null_or_all_present(
    schema_db, channel, resource_present, kind_present
):
    owner, _, reconciliation, resource = await parents(schema_db)
    row = drift(owner, reconciliation)
    row.resource_id = resource.id if resource_present else None
    row.resource_kind = resource.resource_kind if kind_present else None
    if resource_present == kind_present:
        await save(schema_db, row, channel)
    else:
        with pytest.raises(PersistenceError):
            await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", [*SCOPE, "run_id", "resource_id", "resource_kind"])
async def test_every_run_resource_and_scope_dimension_is_enforced(schema_db, channel, field):
    owner, _, reconciliation, resource = await parents(schema_db)
    row = drift(owner, reconciliation, resource)
    setattr(
        row,
        field,
        {
            "account_id": uuid4(),
            "region": "global",
            "environment": "prod",
            "mode": "BACKTEST",
            "run_id": uuid4(),
            "resource_id": uuid4(),
            "resource_kind": "OPEN_ORDERS",
        }[field],
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["product_id", "asset_id"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", 2**63])
async def test_optional_ids_are_exact_positive_bigints(schema_db, channel, field, value):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    with pytest.raises(PersistenceError):
        await save(schema_db, drift(owner, reconciliation, **{field: value}), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("value", [None, True, "A" * 64, "a" * 63, "g" * 64])
async def test_subject_identity_is_required_lowercase_sha256(schema_db, channel, value):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    with pytest.raises(PersistenceError):
        await save(
            schema_db,
            drift(owner, reconciliation, subject_key_sha256=value),
            channel,
        )


@pytest.mark.asyncio
async def test_per_run_deduplication_and_successor_run_recurrence(schema_db):
    owner, recovery, reconciliation, _ = await parents(schema_db, include_resource=False)
    await save(schema_db, drift(owner, reconciliation))
    with pytest.raises(PersistenceError):
        await save(schema_db, drift(owner, reconciliation))
    successor = run(owner, recovery, trigger="PERIODIC")
    await save(schema_db, successor)
    await save(schema_db, drift(owner, successor))


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("detected_at", NOW + timedelta(seconds=2)),
        ("recorded_at", NOW - timedelta(microseconds=1)),
    ],
)
async def test_drift_time_order_is_enforced(schema_db, channel, field, value):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    with pytest.raises(PersistenceError):
        await save(schema_db, drift(owner, reconciliation, **{field: value}), channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["detected_at", "recorded_at"])
async def test_drift_times_require_exact_aware_datetimes(schema_db, field):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    with pytest.raises(PersistenceError):
        await save(
            schema_db,
            drift(owner, reconciliation, **{field: NOW.replace(tzinfo=None)}),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field", [*SCOPE, "run_id", "drift_id", "drift_kind", "acknowledgement_policy"]
)
async def test_acknowledgement_exact_parent_identity_is_enforced(schema_db, channel, field):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    row = acknowledgement(owner, discrepancy)
    setattr(
        row,
        field,
        {
            "account_id": uuid4(),
            "region": "global",
            "environment": "prod",
            "mode": "BACKTEST",
            "run_id": uuid4(),
            "drift_id": uuid4(),
            "drift_kind": "UNCLASSIFIED",
            "acknowledgement_policy": "REVIEWABLE",
        }[field],
    )
    with pytest.raises(PersistenceError):
        await save(schema_db, row, channel)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "policy,kind,reason",
    list(product(POLICIES, ("REVIEW_EVIDENCE", "NOTICE_EVIDENCE"), REASONS)),
)
async def test_complete_acknowledgement_policy_kind_reason_matrix(
    schema_db, channel, policy, kind, reason
):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(
        owner,
        reconciliation,
        drift_kind="BALANCE_MISMATCH",
        severity="WARNING",
        acknowledgement_policy=policy,
    )
    await save(schema_db, discrepancy)
    valid = (policy == "REVIEWABLE") == (kind == "REVIEW_EVIDENCE") and (
        policy != "NON_OVERRIDABLE" or reason == "OPERATOR_NOTED"
    )
    if valid:
        await save(
            schema_db,
            acknowledgement(
                owner,
                discrepancy,
                acknowledgement_kind=kind,
                reason_code=reason,
            ),
            channel,
        )
    else:
        with pytest.raises(PersistenceError):
            await save(
                schema_db,
                acknowledgement(
                    owner,
                    discrepancy,
                    acknowledgement_kind=kind,
                    reason_code=reason,
                ),
                channel,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("drift_kind", ["UNKNOWN_EXPOSURE", "INADEQUATE_PROTECTION"])
async def test_critical_unknowns_accept_notice_with_no_safety_effect_only(schema_db, drift_kind):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation, drift_kind=drift_kind)
    await save(schema_db, discrepancy)
    notice = acknowledgement(owner, discrepancy)
    await save(schema_db, notice)
    assert notice.acknowledgement_kind == "NOTICE_EVIDENCE"
    assert notice.safety_effect == "NONE"
    for changes in (
        {"acknowledgement_kind": "REVIEW_EVIDENCE"},
        {"safety_effect": "SAFE"},
        {"reason_code": "FALSE_POSITIVE"},
    ):
        with pytest.raises(PersistenceError):
            await save(
                schema_db,
                acknowledgement(
                    owner,
                    discrepancy,
                    idempotency_key=str(uuid4()),
                    **changes,
                ),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize("field", ["idempotency_key", "actor_ref"])
@pytest.mark.parametrize("value", [None, "", " ", "\t", "x" * 129, "bad\x00text", 1, True])
async def test_acknowledgement_text_boundaries(schema_db, channel, field, value):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    with pytest.raises(PersistenceError):
        await save(
            schema_db,
            acknowledgement(owner, discrepancy, **{field: value}),
            channel,
        )


@pytest.mark.asyncio
async def test_scoped_idempotent_replay_conflicts_but_distinct_events_are_allowed(schema_db):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    await save(schema_db, acknowledgement(owner, discrepancy))
    with pytest.raises(PersistenceError):
        await save(schema_db, acknowledgement(owner, discrepancy))
    await save(
        schema_db,
        acknowledgement(owner, discrepancy, idempotency_key="ack-002"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("acknowledged_at", NOW + timedelta(seconds=4)),
        ("recorded_at", NOW + timedelta(seconds=1)),
    ],
)
async def test_acknowledgement_time_order(schema_db, channel, field, value):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    with pytest.raises(PersistenceError):
        await save(
            schema_db,
            acknowledgement(owner, discrepancy, **{field: value}),
            channel,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["acknowledged_at", "recorded_at"])
async def test_acknowledgement_times_require_exact_aware_datetimes(schema_db, field):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    with pytest.raises(PersistenceError):
        await save(
            schema_db,
            acknowledgement(
                owner,
                discrepancy,
                **{field: (NOW + timedelta(seconds=2)).replace(tzinfo=None)},
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name", ["drift", "acknowledgement"])
@pytest.mark.parametrize("change", ["payload", "digest", "version", "semantic"])
async def test_direct_orm_insert_validates_complete_evidence_tuple(schema_db, model_name, change):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    if model_name == "acknowledgement":
        await save(schema_db, discrepancy)
        row = acknowledgement(owner, discrepancy)
    else:
        row = discrepancy
    if change == "payload":
        row.evidence_payload = "{}"
    elif change == "digest":
        row.evidence_sha256 = "c" * 64
    elif change == "version":
        row.evidence_schema_version = 2
    elif model_name == "drift":
        row.subject_key_sha256 = "d" * 64
    else:
        row.actor_ref = "changed"
    with pytest.raises(PersistenceError):
        await save(schema_db, row)


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name", ["drift", "acknowledgement"])
@pytest.mark.parametrize("operation", ["load", "deferred", "refresh", "partial", "expired"])
async def test_corrupt_digest_fails_closed_on_every_orm_load_path(schema_db, model_name, operation):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    row = discrepancy
    if model_name == "acknowledgement":
        row = acknowledgement(owner, discrepancy)
        await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = None
            if operation in {"refresh", "partial", "expired"}:
                loaded = await session.get(type(row), row.id)
                session.expire(loaded, ["evidence_sha256"])
            await session.execute(
                text(f"UPDATE {row.__table__.name} SET evidence_sha256=:v WHERE id=:id"),
                {"v": "c" * 64, "id": row.id.hex},
            )
            if operation == "load":
                await session.get(type(row), row.id)
            elif operation == "deferred":
                (await session.scalars(select(type(row)).options(load_only(type(row).id)))).all()
            elif operation == "expired":
                await session.run_sync(lambda _: loaded.evidence_sha256)
            else:
                await session.refresh(
                    loaded,
                    attribute_names=["recorded_at"] if operation == "partial" else None,
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name", ["drift", "acknowledgement"])
@pytest.mark.parametrize("operation", ["update", "delete", "expired_update", "deferred_delete"])
async def test_every_orm_update_and_delete_is_denied(schema_db, model_name, operation):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    row = discrepancy
    if model_name == "acknowledgement":
        row = acknowledgement(owner, discrepancy)
        await save(schema_db, row)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            loaded = (
                (await session.scalars(select(type(row)).options(load_only(type(row).id)))).one()
                if operation == "deferred_delete"
                else await session.get(type(row), row.id)
            )
            if operation in {"delete", "deferred_delete"}:
                await session.delete(loaded)
            else:
                if operation == "expired_update":
                    session.expire(loaded)
                loaded.recorded_at = loaded.recorded_at + timedelta(seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("model_name", ["drift", "acknowledgement"])
async def test_core_cross_column_and_append_only_limitations_are_explicit(schema_db, model_name):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    row = discrepancy
    if model_name == "acknowledgement":
        row = acknowledgement(owner, discrepancy)
        await save(schema_db, row)
    async with schema_db.transaction() as session:
        await session.execute(
            row.__table__.update()
            .where(row.__table__.c.id == row.id)
            .values(evidence_sha256="c" * 64)
        )
        assert (
            await session.execute(
                select(row.__table__.c.evidence_sha256).where(row.__table__.c.id == row.id)
            )
        ).scalar_one() == "c" * 64
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.get(type(row), row.id)
    async with schema_db.transaction() as session:
        await session.execute(delete(row.__table__).where(row.__table__.c.id == row.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["orm", "core"])
@pytest.mark.parametrize(
    "model_name,field,value",
    [
        ("drift", "product_id", 1.5),
        ("drift", "detected_at", "2026-11-04T00:00:00.000000+01:00"),
        ("acknowledgement", "actor_ref", "\t"),
        (
            "acknowledgement",
            "acknowledged_at",
            "2026-11-04T00:00:02.000000+01:00",
        ),
    ],
)
async def test_corrupt_typed_stored_values_fail_closed(
    schema_db, channel, model_name, field, value
):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    row = discrepancy
    if model_name == "acknowledgement":
        row = acknowledgement(owner, discrepancy)
        await save(schema_db, row)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            await session.execute(text("PRAGMA ignore_check_constraints=ON"))
            await session.execute(
                text(f"UPDATE {row.__table__.name} SET {field}=:v WHERE id=:id"),
                {"v": value, "id": row.id.hex},
            )
            if channel == "orm":
                await session.get(type(row), row.id)
            else:
                (await session.execute(select(row.__table__))).all()


@pytest.mark.asyncio
async def test_success_and_rollback_leave_all_prior_truth_unchanged(schema_db):
    owner, _, reconciliation, resource = await parents(schema_db)
    api = schema_api()
    prior = [
        table
        for table in api.metadata.tables.values()
        if table.name not in {"reconciliation_drifts", "reconciliation_acknowledgements"}
    ]
    async with schema_db.transaction() as session:
        before = {table.name: (await session.execute(select(table))).all() for table in prior}
    discrepancy = drift(owner, reconciliation, resource)
    await save(schema_db, discrepancy)
    await save(schema_db, acknowledgement(owner, discrepancy))
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(acknowledgement(owner, discrepancy, idempotency_key="collision"))
            session.add(acknowledgement(owner, discrepancy, idempotency_key="collision"))
    async with schema_db.transaction() as session:
        assert before == {
            table.name: (await session.execute(select(table))).all() for table in prior
        }


def test_schema_has_no_readiness_adoption_correction_or_mutable_safety_capability():
    api = schema_api()
    forbidden = {
        "resolved",
        "safe",
        "ready",
        "clears_block",
        "status",
        "adopted",
        "corrected",
        "halt_cleared",
    }
    for model in (api.ReconciliationDrift, api.ReconciliationAcknowledgement):
        assert forbidden.isdisjoint(model.__table__.c.keys())
        assert not any(
            name.startswith(("resolve", "adopt", "correct", "clear", "place", "submit"))
            for name in vars(model)
        )


@pytest.mark.asyncio
async def test_populated_tables_drop_and_recreate_children_first(schema_db):
    owner, _, reconciliation, _ = await parents(schema_db, include_resource=False)
    discrepancy = drift(owner, reconciliation)
    await save(schema_db, discrepancy)
    await save(schema_db, acknowledgement(owner, discrepancy))
    api = schema_api()
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(api.ReconciliationAcknowledgement.__table__.drop)
        await connection.run_sync(api.ReconciliationDrift.__table__.drop)
        await connection.run_sync(api.ReconciliationDrift.__table__.create)
        await connection.run_sync(api.ReconciliationAcknowledgement.__table__.create)
    async with schema_db.transaction() as session:
        assert (await session.execute(select(api.ReconciliationDrift))).all() == []
        assert (await session.execute(select(api.ReconciliationAcknowledgement))).all() == []


def test_import_has_no_engine_tasks_files_network_orders_or_production_writes(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, socket, sqlalchemy, sqlalchemy.ext.asyncio
def forbidden(*a, **kw):
    raise AssertionError('import side effect')
socket.socket = socket.create_connection = forbidden
sqlalchemy.create_engine = sqlalchemy.ext.asyncio.create_async_engine = forbidden
asyncio.create_task = asyncio.run = forbidden
from hmt_crypto.persistence.models import (
    ReconciliationAcknowledgement, ReconciliationDrift
)
assert ReconciliationDrift.__tablename__ == 'reconciliation_drifts'
assert ReconciliationAcknowledgement.__tablename__ == 'reconciliation_acknowledgements'
""",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
