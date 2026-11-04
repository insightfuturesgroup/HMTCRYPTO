"""Complete-row ORM validation for immutable local rate-consumption evidence."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from sqlalchemy import event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import object_session
from sqlalchemy.orm.state import InstanceState

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_rate_consumption_contracts import CONSUMPTION_INPUT_FIELDS
from hmt_crypto.persistence.risk_rate_consumption_evidence import (
    encode_risk_rate_consumption_evidence,
    validate_risk_rate_consumption_evidence,
)
from hmt_crypto.persistence.risk_reservation_contracts import _time
from hmt_crypto.persistence.risk_reservation_intent import validate_current_intent_snapshot
from hmt_crypto.persistence.schema_support import SCOPE

RESERVATION_CHILD = (
    "reservation_id",
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
    "reservation_created_at",
    "rate_policy_version",
    "rate_policy_sha256",
    "rate_window_start_at",
    "rate_window_end_at",
    "consumed_units",
)
RESERVATION_PARENT = (
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
ATTEMPT_CHILD = (
    "order_attempt_id",
    *SCOPE,
    "command_id",
    "command_operation_kind",
    "order_id",
    "attempt_number",
    "attempt_backend",
    "attempt_prepared_at",
    "attempt_recorded_at",
    "attempt_evidence_schema_version",
    "attempt_evidence_sha256",
)
ATTEMPT_PARENT = (
    "id",
    *SCOPE,
    "command_id",
    "operation_kind",
    "order_id",
    "attempt_number",
    "backend",
    "prepared_at",
    "recorded_at",
    "evidence_schema_version",
    "evidence_sha256",
)
EVENT_CHILD = (
    "dispatch_event_id",
    *SCOPE,
    "order_attempt_id",
    "attempt_backend",
    "dispatch_event_sequence",
    "dispatch_event_key",
    "dispatch_event_kind",
    "recorded_at",
    "dispatch_event_evidence_schema_version",
    "dispatch_event_evidence_sha256",
)
EVENT_PARENT = (
    "id",
    *SCOPE,
    "attempt_id",
    "backend",
    "sequence",
    "event_key",
    "event_kind",
    "recorded_at",
    "evidence_schema_version",
    "evidence_sha256",
)
DECISION_CHILD = (
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
DECISION_PARENT = (
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
EVENT_NULL_FIELDS = (
    "observed_at",
    "ingester_id",
    "ingester_generation",
    "reconciliation_ref",
    "ingester_leader_ref",
    "ingester_leader_generation",
    "ingester_recovery_ref",
    "ingester_recovery_generation",
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


def _same(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _connection(target: Any, connection: Connection | None) -> Connection:
    if connection is not None:
        return connection
    session = object_session(target)
    if session is None:
        raise PersistenceValueError()
    return session.connection()


def _load_values(target: Any, connection: Connection | None, *, stored: bool) -> dict[str, object]:
    fields = tuple(column.name for column in target.__table__.c)
    state = cast(InstanceState[Any], sa_inspect(target))
    if not stored:
        if any(field not in state.dict for field in fields):
            raise PersistenceValueError()
        return {field: state.dict[field] for field in fields}
    if state.identity is None:
        raise PersistenceValueError()
    connection = _connection(target, connection)
    table = target.__table__
    row = (
        connection.execute(
            select(*(table.c[field] for field in fields)).where(table.c.id == state.identity[0])
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise PersistenceValueError()
    return dict(row)


def _parent_row(
    target: Any,
    connection: Connection | None,
    values: dict[str, object],
    table_name: str,
    child: tuple[str, ...],
    parent: tuple[str, ...],
    extra: tuple[str, ...] = (),
) -> dict[str, object]:
    connection = _connection(target, connection)
    table = target.__table__.metadata.tables[f"hmt_crypto.{table_name}"]
    columns = tuple(dict.fromkeys((*parent, *extra)))
    row = (
        connection.execute(
            select(*(table.c[field] for field in columns)).where(
                *(
                    table.c[parent_field] == values[child_field]
                    for child_field, parent_field in zip(child, parent, strict=True)
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise PersistenceValueError()
    return dict(row)


def _validate_own(values: dict[str, object]) -> None:
    inputs = {
        field: values["id"] if field == "consumption_id" else values[field]
        for field in CONSUMPTION_INPUT_FIELDS
    }
    encoded = encode_risk_rate_consumption_evidence(1, inputs)
    stored = validate_risk_rate_consumption_evidence(
        values["evidence_schema_version"], values["evidence_payload"], values["evidence_sha256"]
    )
    if encoded != stored or any(
        not _same(values[field], expected)
        for field, expected in (
            ("consumption_key", encoded.consumption_key),
            ("dispatch_event_key", encoded.dispatch_event_key),
            ("evidence_payload", encoded.payload),
            ("evidence_sha256", encoded.sha256),
        )
    ):
        raise PersistenceValueError()


def _validate_parents(
    target: Any,
    values: dict[str, object],
    connection: Connection | None,
    *,
    stored: bool,
) -> None:
    reservation = _parent_row(
        target,
        connection,
        values,
        "risk_reservations",
        RESERVATION_CHILD,
        RESERVATION_PARENT,
        (
            "approved_contracts",
            "planned_incremental_gross_exposure",
            "planned_worst_case_loss",
            "planned_collateral",
            "planned_open_order_slots",
            "remaining_contracts",
            "remaining_incremental_gross_exposure",
            "remaining_worst_case_loss",
            "remaining_collateral",
            "remaining_open_order_slots",
            "pending_rate_units",
            "status",
            "version",
            "updated_at",
            *RELEASE_FIELDS,
        ),
    )
    attempt = _parent_row(
        target,
        connection,
        values,
        "order_attempts",
        ATTEMPT_CHILD,
        ATTEMPT_PARENT,
        ("evidence_payload",),
    )
    event_parent = _parent_row(
        target,
        connection,
        values,
        "order_attempt_events",
        EVENT_CHILD,
        EVENT_PARENT,
        ("evidence_payload", *EVENT_NULL_FIELDS),
    )
    decision = _parent_row(
        target,
        connection,
        values,
        "risk_decisions",
        DECISION_CHILD,
        DECISION_PARENT,
        (
            "command_evidence_schema_version",
            "command_evidence_payload",
            "command_evidence_sha256",
            "approved_contracts",
            "approved_side",
            "order_origin_command_id",
            "order_requested_contracts",
            "order_requested_side",
            "order_requested_reduce_only",
        ),
    )
    current_command = _parent_row(
        target,
        connection,
        values,
        "command_operations",
        ("command_id", *SCOPE),
        ("id", *SCOPE),
        ("operation_kind", "evidence_schema_version", "evidence_payload", "evidence_sha256"),
    )
    current_order = _parent_row(
        target,
        connection,
        values,
        "orders",
        ("order_id", *SCOPE),
        ("id", *SCOPE),
        (
            "product_id",
            "command_id",
            "requested_contracts",
            "requested_side",
            "requested_order_type",
            "requested_limit_price",
            "requested_stop_price",
            "requested_reduce_only",
        ),
    )
    if (
        not _same(attempt["evidence_payload"], values["attempt_evidence_payload"])
        or not _same(event_parent["evidence_payload"], values["dispatch_event_evidence_payload"])
        or any(event_parent[field] is not None for field in EVENT_NULL_FIELDS)
        or not _same(
            attempt["evidence_schema_version"], decision["command_evidence_schema_version"]
        )
        or not _same(attempt["evidence_payload"], decision["command_evidence_payload"])
        or not _same(attempt["evidence_sha256"], decision["command_evidence_sha256"])
        or not _same(current_command["operation_kind"], values["command_operation_kind"])
    ):
        raise PersistenceValueError()
    _validate_intent_parity(values, decision, current_command, current_order)
    if stored:
        _validate_committed_parent(values, reservation)


def _validate_intent_parity(
    values: dict[str, object],
    decision: dict[str, object],
    command: dict[str, object],
    order: dict[str, object],
) -> None:
    validate_current_intent_snapshot(
        {
            "decision_command_evidence_schema_version": decision["command_evidence_schema_version"],
            "decision_command_evidence_payload": decision["command_evidence_payload"],
            "decision_command_evidence_sha256": decision["command_evidence_sha256"],
            "current_command_evidence_schema_version": command["evidence_schema_version"],
            "current_command_evidence_payload": command["evidence_payload"],
            "current_command_evidence_sha256": command["evidence_sha256"],
            "decision_approved_contracts": decision["approved_contracts"],
            "decision_approved_side": decision["approved_side"],
            "decision_risk_effect": decision["risk_effect"],
            "decision_order_id": decision["order_id"],
            "decision_order_product_id": decision["product_id"],
            "decision_order_origin_command_id": decision["order_origin_command_id"],
            "decision_order_requested_contracts": decision["order_requested_contracts"],
            "decision_order_requested_side": decision["order_requested_side"],
            "decision_order_requested_reduce_only": decision["order_requested_reduce_only"],
            "current_order_id": order["id"],
            "current_order_product_id": order["product_id"],
            "current_order_origin_command_id": order["command_id"],
            "current_order_requested_contracts": order["requested_contracts"],
            "current_order_requested_side": order["requested_side"],
            "current_order_requested_order_type": order["requested_order_type"],
            "current_order_requested_limit_price": order["requested_limit_price"],
            "current_order_requested_stop_price": order["requested_stop_price"],
            "current_order_requested_reduce_only": order["requested_reduce_only"],
        },
        {
            "command_operation_kind": values["command_operation_kind"],
            "command_id": values["command_id"],
            "order_id": values["order_id"],
            "product_id": values["product_id"],
            "approved_contracts": decision["approved_contracts"],
            "approved_side": decision["approved_side"],
            "risk_effect": decision["risk_effect"],
        },
    )


def _validate_committed_parent(values: dict[str, object], reservation: dict[str, object]) -> None:
    updated_at = _time(reservation["updated_at"])
    created_at = _time(reservation["created_at"])
    window_end = _time(reservation["rate_window_end_at"])
    recorded_at = _time(values["recorded_at"])
    assert updated_at is not None and created_at is not None
    assert window_end is not None and recorded_at is not None
    if (
        reservation["status"] != "DISPATCH_COMMITTED"
        or reservation["version"] != 2
        or updated_at != recorded_at
        or updated_at < created_at
        or updated_at >= window_end
        or reservation["pending_rate_units"] != 0
        or reservation["remaining_contracts"] != reservation["approved_contracts"]
        or reservation["remaining_incremental_gross_exposure"]
        != reservation["planned_incremental_gross_exposure"]
        or reservation["remaining_worst_case_loss"] != reservation["planned_worst_case_loss"]
        or reservation["remaining_collateral"] != reservation["planned_collateral"]
        or reservation["remaining_open_order_slots"] != reservation["planned_open_order_slots"]
        or any(reservation[field] is not None for field in RELEASE_FIELDS)
    ):
        raise PersistenceValueError()


def _validate(
    target: Any, connection: Connection | None = None, *, stored: bool
) -> dict[str, object]:
    values = _load_values(target, connection, stored=stored)
    _validate_own(values)
    _validate_parents(target, values, connection, stored=stored)
    return values


def install_risk_rate_consumption_validation(model: type[Any]) -> None:
    """Install complete insert/load/refresh validation and append-only ORM guards."""

    def before_insert(mapper: object, connection: Connection, target: Any) -> None:
        _validate(target, connection, stored=False)

    def reject_update(mapper: object, connection: Connection, target: Any) -> None:
        _validate(target, connection, stored=True)
        raise PersistenceValueError()

    def reject_delete(mapper: object, connection: Connection, target: Any) -> None:
        _validate(target, connection, stored=True)
        raise PersistenceValueError()

    def on_load(target: Any, context: object) -> None:
        _validate(target, stored=True)

    def on_refresh(target: Any, context: object, attrs: Iterable[str] | None) -> None:
        _validate(target, stored=True)

    event.listen(model, "before_insert", before_insert)
    event.listen(model, "before_update", reject_update)
    event.listen(model, "before_delete", reject_delete)
    event.listen(model, "load", on_load, restore_load_context=True)
    event.listen(model, "refresh", on_refresh, restore_load_context=True)
