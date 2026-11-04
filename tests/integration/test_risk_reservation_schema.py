"""Task 2.1d9b adds one pre-dispatch capacity projection, never dispatch authority."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from execution_test_support import attempt as source_attempt
from execution_test_support import attempt_event as source_event
from risk_decision_schema_support import (
    check_results,
    decision,
    decision_graph,
    reservation_plan,
)
from risk_reservation_schema_support import (
    apply_test_only_non_authoritative_release,
    reservation_graph,
    run_test_only_non_authoritative_atomic_terminalize_and_release,
)
from schema_test_support import NOW, schema_api
from schema_test_support import schema_db as schema_db
from sqlalchemy import (
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import load_only
from sqlalchemy.schema import CreateIndex, CreateTable
from test_coordination_schema import save

from hmt_crypto.persistence.errors import PersistenceError, PersistenceValueError
from hmt_crypto.persistence.types import ExactDecimal

SCOPE = ("account_id", "region", "environment", "mode")
DECIMALS = (
    "planned_incremental_gross_exposure",
    "planned_worst_case_loss",
    "planned_collateral",
    "remaining_incremental_gross_exposure",
    "remaining_worst_case_loss",
    "remaining_collateral",
)
RELEASE_FIELDS = (
    "release_key",
    "release_reason_code",
    "release_proof_version",
    "proof_observed_at",
    "observed_terminal_command_status",
    "observed_command_version",
    "observed_command_attempt_count",
    "observed_order_version",
    "order_attempt_event_evidence_count",
    "order_attempt_evidence_count",
    "attempt_evidence_set_sha256",
    "release_proof_payload",
    "release_proof_sha256",
)
DECISION_PARENT = (
    "decision_id",
    *SCOPE,
    "risk_state_id",
    "command_id",
    "command_operation_kind",
    "order_id",
    "product_id",
    "decision_outcome",
    "risk_effect",
    "reservation_plan_sha256",
    "decision_evaluated_at",
    "decision_recorded_at",
)
RATE_PARENT = (
    "id",
    *SCOPE,
    "decision_id",
    "risk_state_id",
    "command_id",
    "command_operation_kind",
    "order_id",
    "product_id",
    "decision_outcome",
    "risk_effect",
    "reservation_plan_sha256",
    "decision_evaluated_at",
    "decision_recorded_at",
    "created_at",
    "rate_policy_version",
    "rate_policy_sha256",
    "rate_window_start_at",
    "rate_window_end_at",
    "planned_pending_rate_units",
)


def _uniques(table):
    return {
        tuple(column.name for column in constraint.columns): constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_exact_table_export_columns_candidates_fks_indexes_checks_and_offline_ddl():
    api = schema_api()
    table = api.RiskReservation.__table__
    assert table.name == "risk_reservations"
    assert set(table.c.keys()) == {
        "id",
        *SCOPE,
        "reservation_key",
        "decision_id",
        "risk_state_id",
        "command_id",
        "command_operation_kind",
        "order_id",
        "product_id",
        "decision_outcome",
        "risk_effect",
        "reservation_plan_sha256",
        "decision_evaluated_at",
        "decision_recorded_at",
        "approved_contracts",
        "approved_side",
        "reference_asset_id",
        "reference_asset_symbol",
        "collateral_asset_id",
        "collateral_asset_symbol",
        "planned_incremental_gross_exposure",
        "planned_worst_case_loss",
        "planned_collateral",
        "planned_open_order_slots",
        "planned_pending_rate_units",
        "rate_policy_version",
        "rate_policy_sha256",
        "rate_window_start_at",
        "rate_window_end_at",
        "remaining_contracts",
        "remaining_incremental_gross_exposure",
        "remaining_worst_case_loss",
        "remaining_collateral",
        "remaining_open_order_slots",
        "pending_rate_units",
        "status",
        "version",
        "created_at",
        "updated_at",
        *RELEASE_FIELDS,
    }
    assert _uniques(table) == {
        ("id", *SCOPE): "uq_risk_reservations_id_scope",
        (*SCOPE, "reservation_key"): "uq_risk_reservations_scope_reservation_key",
        ("decision_id", *SCOPE): "uq_risk_reservations_decision_scope",
        ("command_id", *SCOPE): "uq_risk_reservations_command_scope",
        (*SCOPE, "release_key"): "uq_risk_reservations_scope_release_key",
        RATE_PARENT: "uq_risk_reservations_rate_consumption_parent",
    }
    assert (
        _uniques(api.RiskDecision.__table__)[
            (
                "id",
                *SCOPE,
                "risk_state_id",
                "command_id",
                "command_operation_kind",
                "order_id",
                "product_id",
                "outcome",
                "risk_effect",
                "reservation_plan_sha256",
                "evaluated_at",
                "recorded_at",
            )
        ]
        == "uq_risk_decisions_reservation_parent"
    )
    expected_fks = {
        "accounts": SCOPE,
        "risk_decisions": DECISION_PARENT,
        "risk_state": ("risk_state_id", *SCOPE),
        "command_operations": ("command_id", *SCOPE),
        "orders": ("order_id", *SCOPE, "product_id"),
    }
    assert {
        fk.referred_table.name: tuple(fk.column_keys) for fk in table.foreign_key_constraints
    } == expected_fks
    for fk in table.foreign_key_constraints:
        assert isinstance(fk, ForeignKeyConstraint)
        assert fk.ondelete == fk.onupdate == "RESTRICT"
    assert {
        index.name: tuple(column.name for column in index.columns) for index in table.indexes
    } == {
        "ix_risk_reservations_scope": SCOPE,
        "ix_risk_reservations_decision_parent": DECISION_PARENT,
        "ix_risk_reservations_risk_state_parent": ("risk_state_id", *SCOPE),
        "ix_risk_reservations_command_parent": ("command_id", *SCOPE),
        "ix_risk_reservations_order_parent": ("order_id", *SCOPE, "product_id"),
        "ix_risk_reservations_scope_status_product": (*SCOPE, "status", "product_id"),
    }
    pg = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    local = str(CreateTable(table).compile(dialect=sqlite.dialect()))
    assert pg.count(" NUMERIC") == 6
    assert "NUMERIC(" not in pg
    assert "DISPATCH_COMMITTED" not in pg + local
    assert "PENDING_DISPATCH" in pg and "RELEASED" in pg
    names = {constraint.name for constraint in table.constraints}
    assert None not in names
    check_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    assert check_names == {
        "ck_risk_reservations_approved_side_enum",
        "ck_risk_reservations_attempt_evidence_set_sha256_sha256",
        "ck_risk_reservations_collateral_asset_symbol_bounded",
        "ck_risk_reservations_command_operation_kind_enum",
        "ck_risk_reservations_decision_outcome_enum",
        "ck_risk_reservations_environment",
        "ck_risk_reservations_lifecycle_shape",
        "ck_risk_reservations_mode",
        "ck_risk_reservations_observed_terminal_command_status_enum",
        "ck_risk_reservations_operation_slot_shape",
        "ck_risk_reservations_planned_collateral_nonnegative",
        "ck_risk_reservations_planned_incremental_gross_exposure_nonnegative",
        "ck_risk_reservations_planned_integer_values",
        "ck_risk_reservations_planned_worst_case_loss_nonnegative",
        "ck_risk_reservations_rate_policy_sha256_sha256",
        "ck_risk_reservations_reference_asset_symbol_bounded",
        "ck_risk_reservations_region",
        "ck_risk_reservations_release_key_sha256",
        "ck_risk_reservations_release_proof_payload_bounded",
        "ck_risk_reservations_release_proof_sha256_sha256",
        "ck_risk_reservations_release_reason_code_enum",
        "ck_risk_reservations_remaining_collateral_nonnegative",
        "ck_risk_reservations_remaining_incremental_gross_exposure_nonnegative",
        "ck_risk_reservations_remaining_integer_values",
        "ck_risk_reservations_remaining_worst_case_loss_nonnegative",
        "ck_risk_reservations_reservation_key_sha256",
        "ck_risk_reservations_reservation_plan_sha256_sha256",
        "ck_risk_reservations_risk_effect_enum",
        "ck_risk_reservations_risk_effect_plan_shape",
        "ck_risk_reservations_status_enum",
        "ck_risk_reservations_time_order",
        *{
            f"ck_risk_reservations_decimal_{field}_{suffix}"
            for field in DECIMALS
            for suffix in ("finite", "magnitude", "scale")
        },
    }
    assert {column.name for column in table.c if column.nullable} == set(RELEASE_FIELDS)
    for field in DECIMALS:
        assert isinstance(table.c[field].type, ExactDecimal)
        for suffix in ("finite", "magnitude", "scale"):
            assert f"ck_risk_reservations_decimal_{field}_{suffix}" in names
        assert f"decimal_{field}_" not in local
    for index in table.indexes:
        assert isinstance(index, Index)
        assert index.name in str(CreateIndex(index).compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_owned_factory_creates_only_complete_pending_version_one_shape(schema_db):
    *_, approved, reservation = await reservation_graph(schema_db)
    assert reservation.account_id == approved.account_id
    assert reservation.decision_id == approved.id
    assert reservation.command_operation_kind == approved.command_operation_kind
    assert reservation.status == "PENDING_DISPATCH"
    assert reservation.version == 1
    assert reservation.created_at == reservation.updated_at == NOW + timedelta(seconds=12)
    assert reservation.remaining_contracts == reservation.approved_contracts == 2
    assert reservation.remaining_incremental_gross_exposure == Decimal("100")
    assert reservation.remaining_worst_case_loss == Decimal("10")
    assert reservation.remaining_collateral == Decimal("10")
    assert reservation.remaining_open_order_slots == 1
    assert reservation.pending_rate_units == reservation.planned_pending_rate_units == 1
    assert all(getattr(reservation, field) is None for field in RELEASE_FIELDS)
    async with schema_db.transaction() as session:
        session.add(reservation)
    async with schema_db.transaction() as session:
        model = type(reservation)
        loaded = await session.scalar(select(model).where(model.id == reservation.id))
        assert loaded is not None
        assert loaded.reservation_key == reservation.reservation_key
        assert loaded.status == "PENDING_DISPATCH"


@pytest.mark.asyncio
async def test_typed_core_insert_round_trips_the_complete_owned_initial_row(schema_db):
    *_, reservation = await reservation_graph(schema_db)
    values = {column.name: getattr(reservation, column.name) for column in reservation.__table__.c}
    async with schema_db.transaction() as session:
        await session.execute(insert(reservation.__table__).values(**values))
    async with schema_db.transaction() as session:
        loaded = await session.get(type(reservation), reservation.id)
        assert loaded is not None
        assert loaded.status == "PENDING_DISPATCH"
        assert loaded.reservation_key == reservation.reservation_key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0.000000000000000001"),
        Decimal("99999999999999999999.999999999999999999"),
    ],
)
async def test_all_six_reservation_decimals_round_trip_exact_storage_boundaries(schema_db, amount):
    graph = await decision_graph(schema_db)
    checks = check_results()
    for kind in ("TOTAL_EXPOSURE", "MAX_LOSS_PER_TRADE", "COLLATERAL_MARGIN"):
        checks[kind]["observed_decimal"] = amount
        checks[kind]["limit_decimal"] = amount
    approved = decision(
        *[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)],
        check_results_override=checks,
        reservation_plan_override=reservation_plan(
            reserved_incremental_gross_exposure=amount,
            reserved_worst_case_loss=amount,
            reserved_collateral=amount,
        ),
    )
    await save(schema_db, approved)
    reservation = schema_api().RiskReservation.from_decision(
        decision=approved, created_at=NOW + timedelta(seconds=12)
    )
    async with schema_db.transaction() as session:
        session.add(reservation)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(reservation), reservation.id)
        assert loaded is not None
        assert {getattr(loaded, field) for field in DECIMALS} == {amount}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [Decimal("0.0000000000000000001"), Decimal("100000000000000000000")],
)
async def test_all_six_reservation_decimals_reject_beyond_scale_or_magnitude(schema_db, invalid):
    *_, reservation = await reservation_graph(schema_db)
    values = {column.name: getattr(reservation, column.name) for column in reservation.__table__.c}
    for field in DECIMALS:
        values[field] = invalid
    with pytest.raises((PersistenceError, PersistenceValueError)):
        async with schema_db.transaction() as session:
            await session.execute(insert(reservation.__table__).values(**values))


def test_factory_rejects_caller_owned_derived_lifecycle_capacity_and_proof_values():
    forbidden = (
        "reservation_key",
        "status",
        "version",
        "remaining_contracts",
        "pending_rate_units",
        "release_key",
        "release_proof_payload",
        "api_secret",
    )
    for field in forbidden:
        with pytest.raises(PersistenceValueError) as error:
            schema_api().RiskReservation.from_decision(
                decision=None,
                created_at=NOW,
                **{field: "SECRET-RESERVATION-MARKER"},
            )
        assert "SECRET-RESERVATION-MARKER" not in str(error.value) + repr(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "created_at",
    [
        (NOW + timedelta(seconds=12)).replace(tzinfo=None),
        NOW + timedelta(minutes=2),
        NOW + timedelta(seconds=10),
    ],
)
async def test_factory_rejects_non_utc_expired_or_pre_recorded_creation_time(schema_db, created_at):
    graph = await decision_graph(schema_db)
    approved = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    with pytest.raises((PersistenceError, PersistenceValueError)):
        schema_api().RiskReservation.from_decision(decision=approved, created_at=created_at)


@pytest.mark.asyncio
async def test_factory_rejects_hostile_decision_outcome_without_invoking_comparison_callback(
    schema_db,
):
    graph = await decision_graph(schema_db)
    approved = decision(*[graph[index] for index in (0, 2, 3, 4, 5, 6, 7)])
    touched = False

    class HostileOutcome(str):
        def __ne__(self, other):
            nonlocal touched
            touched = True
            raise RuntimeError("private-outcome-marker")

    approved.outcome = HostileOutcome("APPROVED")
    with pytest.raises(PersistenceValueError) as error:
        schema_api().RiskReservation.from_decision(
            decision=approved,
            created_at=NOW + timedelta(seconds=12),
        )
    assert not touched
    assert "private-outcome-marker" not in str(error.value) + repr(error.value)


async def _released_graph(schema_db):
    graph = await reservation_graph(schema_db)
    owner, command, order, reservation = graph[0], graph[6], graph[7], graph[10]
    async with schema_db.transaction() as session:
        session.add(reservation)
    prepared = source_attempt(
        owner,
        command,
        order,
        prepared_at=NOW + timedelta(seconds=13),
        recorded_at=NOW + timedelta(seconds=13),
    )
    observed = source_event(
        owner,
        prepared,
        event_kind="DEFINITE_NOT_SENT",
        recorded_at=NOW + timedelta(seconds=14),
        evidence={"outcome": "NOT_SENT"},
    )
    async with schema_db.transaction() as session:
        session.add_all([prepared, observed])
    proof = apply_test_only_non_authoritative_release(
        reservation, graph[9], command, order, [prepared], [observed]
    )
    return graph, prepared, observed, proof


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["proof_observed_at", "release_reason_code", "release_proof_version"]
)
async def test_complete_row_validator_rejects_hostile_terminal_scalars_before_comparison(
    schema_db, field
):
    import hmt_crypto.persistence.risk_reservation_validation as validation

    graph, _, _, _ = await _released_graph(schema_db)
    values = {column.name: getattr(graph[10], column.name) for column in graph[10].__table__.c}
    touched = False

    class HostileDateTime(datetime):
        def __eq__(self, other):
            nonlocal touched
            touched = True
            raise RuntimeError("private-terminal-marker")

        def __lt__(self, other):
            nonlocal touched
            touched = True
            raise RuntimeError("private-terminal-marker")

    class HostileReason(str):
        def __eq__(self, other):
            nonlocal touched
            touched = True
            raise RuntimeError("private-terminal-marker")

    class HostileVersion(int):
        def __ne__(self, other):
            nonlocal touched
            touched = True
            raise RuntimeError("private-terminal-marker")

    updated_at = values["updated_at"]
    assert type(updated_at) is datetime
    if field == "proof_observed_at":
        values[field] = HostileDateTime(
            updated_at.year,
            updated_at.month,
            updated_at.day,
            updated_at.hour,
            updated_at.minute,
            updated_at.second,
            updated_at.microsecond,
            tzinfo=UTC,
        )
    elif field == "release_reason_code":
        values[field] = HostileReason("DEFINITE_NOT_DISPATCHED")
    else:
        values[field] = HostileVersion(1)
    with pytest.raises(PersistenceValueError) as error:
        validation._validate_lifecycle(values, RELEASE_FIELDS)
    assert not touched
    assert "private-terminal-marker" not in str(error.value) + repr(error.value)


@pytest.mark.asyncio
async def test_complete_reviewed_release_transition_round_trips_and_then_freezes(schema_db):
    graph, _, _, proof = await _released_graph(schema_db)
    reservation = graph[10]
    async with schema_db.transaction() as session:
        session.add(reservation)
    async with schema_db.transaction() as session:
        loaded = await session.get(type(reservation), reservation.id)
        assert loaded is not None
        assert loaded.status == "RELEASED"
        assert loaded.version == 2
        assert loaded.remaining_contracts == 0
        assert loaded.pending_rate_units == 0
        assert loaded.release_key == proof.release_key
        assert loaded.release_proof_sha256 == proof.sha256
        loaded.updated_at += timedelta(microseconds=1)
        with pytest.raises(PersistenceValueError):
            await session.flush()
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(reservation), reservation.id)
            assert loaded is not None
            await session.delete(loaded)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["normal", "deferred", "expired", "full_refresh", "partial_refresh"]
)
async def test_terminal_freeze_is_enforced_after_every_load_and_refresh_path(schema_db, path):
    graph, _, _, _ = await _released_graph(schema_db)
    reservation = graph[10]
    async with schema_db.transaction() as session:
        session.add(reservation)
    with pytest.raises((PersistenceError, PersistenceValueError)):
        async with schema_db.transaction() as session:
            if path == "deferred":
                loaded = await session.scalar(
                    select(type(reservation))
                    .where(type(reservation).id == reservation.id)
                    .options(load_only(type(reservation).id, type(reservation).status))
                )
                assert loaded is not None
                await session.refresh(loaded)
            else:
                loaded = await session.get(type(reservation), reservation.id)
                assert loaded is not None
                if path == "expired":
                    session.expire(loaded)
                    await session.refresh(loaded)
                elif path == "full_refresh":
                    await session.refresh(loaded)
                elif path == "partial_refresh":
                    await session.refresh(loaded, attribute_names=["updated_at"])
            loaded.updated_at += timedelta(microseconds=1)
            await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", RELEASE_FIELDS)
async def test_sql_null_matrix_rejects_every_partial_released_proof_bundle(schema_db, field):
    graph, _, _, _ = await _released_graph(schema_db)
    reservation = graph[10]
    async with schema_db.transaction() as session:
        session.add(reservation)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(
                update(type(reservation))
                .where(type(reservation).id == reservation.id)
                .values(**{field: None})
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", RELEASE_FIELDS)
async def test_sql_null_matrix_rejects_every_partial_pending_proof_bundle(schema_db, field):
    *_, reservation = await reservation_graph(schema_db)
    async with schema_db.transaction() as session:
        session.add(reservation)
    sample = {
        "release_key": "e" * 64,
        "release_reason_code": "DEFINITE_NOT_DISPATCHED",
        "release_proof_version": 1,
        "proof_observed_at": NOW + timedelta(seconds=20),
        "observed_terminal_command_status": "FAILED",
        "observed_command_version": 2,
        "observed_command_attempt_count": 0,
        "observed_order_version": 1,
        "order_attempt_event_evidence_count": 0,
        "order_attempt_evidence_count": 0,
        "attempt_evidence_set_sha256": "e" * 64,
        "release_proof_payload": json.loads(
            (Path(__file__).parents[1] / "fixtures/risk_reservation_v1.json").read_text()
        )["release_proof"]["payload"],
        "release_proof_sha256": "e" * 64,
    }[field]
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(
                update(type(reservation))
                .where(type(reservation).id == reservation.id)
                .values(**{field: sample})
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "DISPATCH_COMMITTED"},
        {"version": 2},
        {"remaining_contracts": 0},
        {"remaining_collateral": Decimal("0")},
        {"remaining_open_order_slots": 0},
        {"pending_rate_units": 0},
        {"updated_at": NOW + timedelta(seconds=13)},
    ],
)
async def test_typed_core_rejects_every_partial_pending_lifecycle_shape(schema_db, changes):
    *_, reservation = await reservation_graph(schema_db)
    async with schema_db.transaction() as session:
        session.add(reservation)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(
                update(type(reservation))
                .where(type(reservation).id == reservation.id)
                .values(**changes)
            )


@pytest.mark.asyncio
async def test_invalid_pending_adjustment_rolls_back_without_changing_stored_capacity(schema_db):
    *_, reservation = await reservation_graph(schema_db)
    reservation_id = reservation.id
    async with schema_db.transaction() as session:
        session.add(reservation)
    reservation.remaining_contracts = 1
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            session.add(reservation)
            await session.flush()
    async with schema_db.transaction() as session:
        stored = await session.get(type(reservation), reservation_id)
        assert stored is not None
        assert stored.status == "PENDING_DISPATCH"
        assert stored.remaining_contracts == stored.approved_contracts == 2


@pytest.mark.asyncio
async def test_orm_delete_is_forbidden_for_pending_reservation(schema_db):
    *_, reservation = await reservation_graph(schema_db)
    async with schema_db.transaction() as session:
        session.add(reservation)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            stored = await session.get(type(reservation), reservation.id)
            assert stored is not None
            await session.delete(stored)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["normal", "deferred", "expired", "full_refresh", "partial_refresh"]
)
async def test_corrupt_release_proof_is_rejected_on_every_result_path(schema_db, path):
    graph, _, _, _ = await _released_graph(schema_db)
    reservation = graph[10]
    async with schema_db.transaction() as session:
        session.add(reservation)
    if path in {"normal", "deferred"}:
        async with schema_db.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE risk_reservations SET release_proof_sha256 = ? WHERE id = ?",
                ("e" * 64, reservation.id.hex),
            )
        with pytest.raises((PersistenceError, PersistenceValueError)):
            async with schema_db.transaction() as session:
                statement = select(type(reservation)).where(type(reservation).id == reservation.id)
                if path == "deferred":
                    statement = statement.options(
                        load_only(type(reservation).id, type(reservation).status)
                    )
                await session.scalar(statement)
        return
    with pytest.raises((PersistenceError, PersistenceValueError)):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(reservation), reservation.id)
            assert loaded is not None
            await session.execute(
                text("UPDATE risk_reservations SET release_proof_sha256 = :digest WHERE id = :id"),
                {"digest": "e" * 64, "id": reservation.id.hex},
            )
            if path == "expired":
                session.expire(loaded)
                await session.refresh(loaded)
            elif path == "full_refresh":
                await session.refresh(loaded)
            else:
                await session.refresh(loaded, attribute_names=["updated_at"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["normal", "deferred", "expired", "full_refresh", "partial_refresh"]
)
async def test_impossible_stored_attempt_event_counts_reject_on_every_result_path(schema_db, path):
    graph, _, _, _ = await _released_graph(schema_db)
    reservation = graph[10]
    raw = json.loads(reservation.release_proof_payload)
    raw["order_attempt_event_evidence_count"] = 0
    corrupt_payload = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    corrupt_digest = hashlib.sha256(
        json.dumps(
            {
                "kind": "risk_reservation_release_proof",
                "payload": raw,
                "schema_version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()
    async with schema_db.transaction() as session:
        session.add(reservation)

    async def corrupt(session):
        await session.execute(text("PRAGMA ignore_check_constraints=ON"))
        await session.execute(
            text(
                "UPDATE risk_reservations "
                "SET order_attempt_event_evidence_count = 0, "
                "release_proof_payload = :payload, release_proof_sha256 = :digest "
                "WHERE id = :id"
            ),
            {
                "payload": corrupt_payload,
                "digest": corrupt_digest,
                "id": reservation.id.hex,
            },
        )
        await session.execute(text("PRAGMA ignore_check_constraints=OFF"))

    if path in {"normal", "deferred"}:
        async with schema_db.transaction() as session:
            await corrupt(session)
        with pytest.raises((PersistenceError, PersistenceValueError)):
            async with schema_db.transaction() as session:
                statement = select(type(reservation)).where(type(reservation).id == reservation.id)
                if path == "deferred":
                    statement = statement.options(
                        load_only(type(reservation).id, type(reservation).status)
                    )
                await session.scalar(statement)
        return
    with pytest.raises((PersistenceError, PersistenceValueError)):
        async with schema_db.transaction() as session:
            loaded = await session.get(type(reservation), reservation.id)
            assert loaded is not None
            await corrupt(session)
            if path == "expired":
                session.expire(loaded)
                await session.refresh(loaded)
            elif path == "full_refresh":
                await session.refresh(loaded)
            else:
                await session.refresh(loaded, attribute_names=["updated_at"])


async def _alternate_reservation_graphs(schema_db, dimension):
    first = {"profile_name": "reservation-scope-a", "catalogue_revision": 1}
    second = {"profile_name": "reservation-scope-b", "catalogue_revision": 2}
    if dimension == "region":
        first["region"] = "global"
        second["catalogue_revision"] = 1
    elif dimension == "environment":
        first["environment"] = "prod"
        second["catalogue_revision"] = 1
    elif dimension == "mode":
        second["mode"] = "BACKTEST"
    return await reservation_graph(schema_db, **first), await reservation_graph(schema_db, **second)


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", SCOPE)
@pytest.mark.parametrize("edge", ["decision_id", "risk_state_id", "command_id", "order_id"])
async def test_each_composite_parent_scope_edge_rejects_cross_graph_splicing(
    schema_db, dimension, edge
):
    alternate, child = await _alternate_reservation_graphs(schema_db, dimension)
    reservation = child[10]
    values = {column.name: getattr(reservation, column.name) for column in reservation.__table__.c}
    source = {
        "decision_id": alternate[9].id,
        "risk_state_id": alternate[4].id,
        "command_id": alternate[6].id,
        "order_id": alternate[7].id,
    }[edge]
    values[edge] = source
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            await session.execute(insert(reservation.__table__).values(**values))


@pytest.mark.asyncio
async def test_second_scope_is_independent_and_same_decision_cannot_reserve_twice(schema_db):
    first, second = await _alternate_reservation_graphs(schema_db, "account_id")
    async with schema_db.transaction() as session:
        session.add_all([first[10], second[10]])
    assert first[10].reservation_key != second[10].reservation_key
    replay_shape = type(first[10]).from_decision(decision=first[9], created_at=first[10].created_at)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            session.add(replay_shape)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("planned_collateral", Decimal("11")),
        ("approved_contracts", 3),
        ("reference_asset_symbol", "USD"),
        ("decision_recorded_at", NOW + timedelta(seconds=10)),
    ],
)
async def test_copied_parent_plan_or_time_mutation_is_rejected_on_insert(schema_db, field, value):
    *_, reservation = await reservation_graph(schema_db)
    setattr(reservation, field, value)
    with pytest.raises((PersistenceError, PersistenceValueError)):
        async with schema_db.transaction() as session:
            session.add(reservation)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["normal", "deferred", "expired", "full_refresh", "partial_refresh"]
)
async def test_parent_only_plan_corruption_is_rejected_on_every_result_path(schema_db, path):
    *_, reservation = await reservation_graph(schema_db)
    reservation_id = reservation.id
    model = type(reservation)
    async with schema_db.transaction() as session:
        session.add(reservation)
    if path in {"normal", "deferred"}:
        async with schema_db.engine.begin() as connection:
            await connection.exec_driver_sql(
                "UPDATE risk_reservations SET reference_asset_symbol = ? WHERE id = ?",
                ("USD", reservation_id.hex),
            )
        with pytest.raises((PersistenceError, PersistenceValueError)):
            async with schema_db.transaction() as session:
                statement = select(model).where(model.id == reservation_id)
                if path == "deferred":
                    statement = statement.options(load_only(model.id, model.status))
                await session.scalar(statement)
        return
    with pytest.raises((PersistenceError, PersistenceValueError)):
        async with schema_db.transaction() as session:
            loaded = await session.get(model, reservation_id)
            assert loaded is not None
            await session.execute(
                text(
                    "UPDATE risk_reservations SET reference_asset_symbol = :symbol WHERE id = :id"
                ),
                {"symbol": "USD", "id": reservation_id.hex},
            )
            if path == "expired":
                session.expire(loaded)
                await session.refresh(loaded)
            elif path == "full_refresh":
                await session.refresh(loaded)
            else:
                await session.refresh(loaded, attribute_names=["updated_at"])


@pytest.mark.asyncio
async def test_full_partial_expired_refresh_validates_complete_row_and_preserves_changes(schema_db):
    *_, reservation = await reservation_graph(schema_db)
    reservation_id = reservation.id
    model = type(reservation)
    async with schema_db.transaction() as session:
        session.add(reservation)
    async with schema_db.transaction() as session:
        loaded = await session.get(model, reservation_id)
        assert loaded is not None
        session.expire(loaded)
        await session.refresh(loaded)
        assert loaded.status == "PENDING_DISPATCH"
        loaded.remaining_contracts = 1
        with session.no_autoflush:
            await session.refresh(loaded, attribute_names=["updated_at"])
        assert loaded.remaining_contracts == 1
        session.expunge(loaded)
    async with schema_db.transaction() as session:
        loaded = await session.get(model, reservation_id)
        assert loaded is not None
        await session.refresh(loaded)
        assert loaded.rate_window_end_at < NOW + timedelta(days=1)
        assert loaded.status == "PENDING_DISPATCH"


@pytest.mark.asyncio
async def test_test_only_atomic_fixture_terminalizes_and_releases_as_one_portable_unit(
    schema_db,
):
    graph, prepared, observed, _ = await _released_graph(schema_db)
    command_id, reservation_id = graph[6].id, graph[10].id
    preserved = {
        field: getattr(graph[6], field)
        for field in (
            "claim_generation",
            "leader_ref",
            "leader_generation",
            "recovery_ref",
            "recovery_generation",
            "attempt_count",
            "evidence_schema_version",
            "evidence_payload",
            "evidence_sha256",
        )
    }
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), command_id)
        reservation = await session.get(type(graph[10]), reservation_id)
        assert command is not None and reservation is not None
        await run_test_only_non_authoritative_atomic_terminalize_and_release(
            session,
            reservation_id=reservation.id,
        )
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), command_id)
        reservation = await session.get(type(graph[10]), reservation_id)
        assert command is not None and reservation is not None
        assert command.status == "FAILED"
        assert command.claim_owner is command.claim_expires_at is None
        assert command.completed_at == command.updated_at == reservation.proof_observed_at
        assert reservation.status == "RELEASED"
        assert {field: getattr(command, field) for field in preserved} == preserved


@pytest.mark.asyncio
async def test_test_only_release_exact_replay_returns_stored_proof_without_writing(
    schema_db,
):
    graph, _, _, _ = await _released_graph(schema_db)
    reservation_id = graph[10].id
    async with schema_db.transaction() as session:
        first = await run_test_only_non_authoritative_atomic_terminalize_and_release(
            session, reservation_id=reservation_id
        )
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), graph[6].id)
        reservation = await session.get(type(graph[10]), reservation_id)
        assert command is not None and reservation is not None
        before_command = {
            column.name: getattr(command, column.name) for column in command.__table__.c
        }
        before_reservation = {
            column.name: getattr(reservation, column.name) for column in reservation.__table__.c
        }
        replay = await run_test_only_non_authoritative_atomic_terminalize_and_release(
            session, reservation_id=reservation_id
        )
        assert replay.sha256 == first.sha256
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), graph[6].id)
        reservation = await session.get(type(graph[10]), reservation_id)
        assert command is not None and reservation is not None
        assert {
            column.name: getattr(command, column.name) for column in command.__table__.c
        } == before_command
        assert {
            column.name: getattr(reservation, column.name) for column in reservation.__table__.c
        } == before_reservation


@pytest.mark.asyncio
async def test_test_only_atomic_fixture_failure_rolls_back_command_and_reservation_together(
    schema_db,
):
    graph, prepared, observed, _ = await _released_graph(schema_db)
    command_id, reservation_id = graph[6].id, graph[10].id
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            command = await session.get(type(graph[6]), command_id)
            reservation = await session.get(type(graph[10]), reservation_id)
            assert command is not None and reservation is not None
            await run_test_only_non_authoritative_atomic_terminalize_and_release(
                session,
                reservation_id=reservation.id,
            )
            reservation.release_proof_sha256 = "e" * 64
            await session.flush()
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), command_id)
        reservation = await session.get(type(graph[10]), reservation_id)
        assert command is not None and reservation is not None
        assert command.status == "CLAIMED"
        assert command.claim_owner == graph[5].id
        assert command.completed_at is None
        assert reservation.status == "PENDING_DISPATCH"
        assert reservation.remaining_contracts == 2


@pytest.mark.asyncio
async def test_pending_reservation_with_preexisting_terminal_command_is_not_adopted(
    schema_db,
):
    graph, prepared, observed, _ = await _released_graph(schema_db)
    command_id, reservation_id = graph[6].id, graph[10].id
    async with schema_db.transaction() as session:
        command = await session.get(type(graph[6]), command_id)
        assert command is not None
        command.status = "FAILED"
        command.claim_owner = None
        command.claim_expires_at = None
        command.version += 1
        command.completed_at = NOW + timedelta(seconds=20)
        command.updated_at = NOW + timedelta(seconds=20)
    with pytest.raises(PersistenceValueError):
        async with schema_db.transaction() as session:
            command = await session.get(type(graph[6]), command_id)
            reservation = await session.get(type(graph[10]), reservation_id)
            assert command is not None and reservation is not None
            await run_test_only_non_authoritative_atomic_terminalize_and_release(
                session,
                reservation_id=reservation.id,
            )
    async with schema_db.transaction() as session:
        reservation = await session.get(type(graph[10]), reservation_id)
        assert reservation is not None and reservation.status == "PENDING_DISPATCH"


@pytest.mark.asyncio
async def test_populated_schema_can_drop_and_recreate_the_single_reservation_table(schema_db):
    api = schema_api()
    *_, reservation = await reservation_graph(schema_db)
    async with schema_db.transaction() as session:
        session.add(reservation)
    async with schema_db.engine.begin() as connection:
        await connection.run_sync(api.metadata.drop_all)
        await connection.run_sync(api.metadata.create_all)
        names = {
            row[0]
            for row in (
                await connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            ).all()
        }
    assert "risk_reservations" in names
    assert len(api.metadata.tables) == 34
