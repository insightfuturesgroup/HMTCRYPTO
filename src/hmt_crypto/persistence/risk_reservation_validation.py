"""Complete-row ORM validation for pre-dispatch risk reservations.

These guards validate portable model invariants.  They are not substitutes for
the deferred PostgreSQL locks, triggers, grants, or atomic repository.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import object_session
from sqlalchemy.orm.state import InstanceState

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_reservation_contracts import (
    _decimal,
    _enum,
    _hash,
    _integer,
    _text,
    _time,
)
from hmt_crypto.persistence.risk_reservation_evidence import (
    decode_risk_reservation_release_proof,
    derive_reservation_key,
    validate_risk_reservation_release_proof,
)
from hmt_crypto.persistence.risk_reservation_fields import (
    MAX_RELEASE_PROOF_ATTEMPTS,
    MAX_RELEASE_PROOF_EVENTS,
    RELEASE_PAYLOAD_FIELDS,
    RESERVATION_KEY_FIELDS,
)
from hmt_crypto.persistence.schema_support import SCOPE
from hmt_crypto.persistence.types import _decimal_text

_PARENT_COLUMNS = {
    "decision_id": "id",
    "account_id": "account_id",
    "region": "region",
    "environment": "environment",
    "mode": "mode",
    "risk_state_id": "risk_state_id",
    "command_id": "command_id",
    "command_operation_kind": "command_operation_kind",
    "order_id": "order_id",
    "product_id": "product_id",
    "decision_outcome": "outcome",
    "risk_effect": "risk_effect",
    "reservation_plan_sha256": "reservation_plan_sha256",
    "decision_evaluated_at": "evaluated_at",
    "decision_recorded_at": "recorded_at",
    "approved_contracts": "approved_contracts",
    "approved_side": "approved_side",
    "reference_asset_id": "reference_asset_id",
    "reference_asset_symbol": "reference_asset_symbol",
    "collateral_asset_id": "collateral_asset_id",
    "collateral_asset_symbol": "collateral_asset_symbol",
    "planned_incremental_gross_exposure": "reserved_incremental_gross_exposure",
    "planned_worst_case_loss": "reserved_worst_case_loss",
    "planned_collateral": "reserved_collateral",
    "planned_open_order_slots": "reserved_open_order_slots",
    "planned_pending_rate_units": "reserved_pending_rate_units",
    "rate_policy_version": "rate_policy_version",
    "rate_policy_sha256": "rate_policy_sha256",
    "rate_window_start_at": "rate_window_start_at",
    "rate_window_end_at": "rate_window_end_at",
}
_UUID_FIELDS = (
    "id",
    "account_id",
    "decision_id",
    "risk_state_id",
    "command_id",
    "order_id",
)
_HASH_FIELDS = (
    "reservation_key",
    "reservation_plan_sha256",
    "rate_policy_sha256",
)
_POSITIVE_INTEGER_FIELDS = (
    "product_id",
    "approved_contracts",
    "reference_asset_id",
    "collateral_asset_id",
    "planned_pending_rate_units",
    "rate_policy_version",
)
_NONNEGATIVE_INTEGER_FIELDS = (
    "planned_open_order_slots",
    "remaining_contracts",
    "remaining_open_order_slots",
    "pending_rate_units",
)
_DECIMAL_FIELDS = (
    "planned_incremental_gross_exposure",
    "planned_worst_case_loss",
    "planned_collateral",
    "remaining_incremental_gross_exposure",
    "remaining_worst_case_loss",
    "remaining_collateral",
)
_TIME_FIELDS = (
    "decision_evaluated_at",
    "decision_recorded_at",
    "rate_window_start_at",
    "rate_window_end_at",
    "created_at",
    "updated_at",
)


def _load_values(
    target: Any,
    connection: Connection | None,
    *,
    stored: bool,
) -> dict[str, object]:
    fields = tuple(column.name for column in target.__table__.c)
    state = cast(InstanceState[Any], sa_inspect(target))
    if not stored:
        if any(field not in state.dict for field in fields):
            raise PersistenceValueError()
        return {field: state.dict[field] for field in fields}
    session = object_session(target)
    if state.identity is None or (connection is None and session is None):
        raise PersistenceValueError()
    if connection is None:
        assert session is not None
        connection = session.connection()
    table = target.__table__
    return dict(
        connection.execute(
            select(*(table.c[field] for field in fields)).where(table.c.id == state.identity[0])
        )
        .mappings()
        .one()
    )


def _parent_values(
    target: Any, values: dict[str, object], connection: Connection | None
) -> dict[str, object]:
    if connection is None:
        session = object_session(target)
        if session is None:
            raise PersistenceValueError()
        connection = session.connection()
    table = target.metadata.tables["hmt_crypto.risk_decisions"]
    row = (
        connection.execute(
            select(*(table.c[parent] for parent in _PARENT_COLUMNS.values())).where(
                table.c.id == values["decision_id"],
                *(table.c[field] == values[field] for field in SCOPE),
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise PersistenceValueError()
    return dict(row)


def _same(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    return left == right


def _timestamp(value: object) -> str:
    result = _time(value)
    assert result is not None
    return result.astimezone(UTC).isoformat(timespec="microseconds")


def _decimal_value(value: object) -> str:
    return _decimal_text(value, 38, 18)


def _reservation_key_values(values: dict[str, object]) -> dict[str, object]:
    return {field: values[field] for field in RESERVATION_KEY_FIELDS}


def _validate_required_values(values: dict[str, object]) -> None:
    for field in _UUID_FIELDS:
        value = values[field]
        if type(value) is not UUID or value.int == 0:
            raise PersistenceValueError()
    _enum(values["region"], ("india", "global"))
    _enum(values["environment"], ("testnet", "prod"))
    _enum(values["mode"], ("BACKTEST", "PAPER", "LIVE"))
    _enum(values["command_operation_kind"], ("PLACE_ORDER", "MODIFY_ORDER"))
    _enum(values["decision_outcome"], ("APPROVED",))
    _enum(values["risk_effect"], ("RISK_INCREASE", "REDUCE_ONLY"))
    _enum(values["approved_side"], ("buy", "sell"))
    for field in ("reference_asset_symbol", "collateral_asset_symbol"):
        _text(values[field])
    for field in _HASH_FIELDS:
        _hash(values[field])
    for field in _POSITIVE_INTEGER_FIELDS:
        _integer(values[field])
    for field in _NONNEGATIVE_INTEGER_FIELDS:
        _integer(values[field], positive=False)
    for field in _DECIMAL_FIELDS:
        if _decimal(values[field]) != values[field]:
            raise PersistenceValueError()
    for field in _TIME_FIELDS:
        _time(values[field])

    if values["planned_pending_rate_units"] != 1:
        raise PersistenceValueError()
    expected_slots = 1 if values["command_operation_kind"] == "PLACE_ORDER" else 0
    if values["planned_open_order_slots"] != expected_slots:
        raise PersistenceValueError()
    planned = (
        values["planned_incremental_gross_exposure"],
        values["planned_worst_case_loss"],
        values["planned_collateral"],
    )
    if values["risk_effect"] == "RISK_INCREASE":
        if planned[0] <= 0 or planned[1] <= 0 or planned[2] < 0:  # type: ignore[operator]
            raise PersistenceValueError()
    elif any(item != 0 for item in planned):
        raise PersistenceValueError()
    start = cast(datetime, values["rate_window_start_at"])
    evaluated = cast(datetime, values["decision_evaluated_at"])
    recorded = cast(datetime, values["decision_recorded_at"])
    created = cast(datetime, values["created_at"])
    end = cast(datetime, values["rate_window_end_at"])
    if not (start <= evaluated <= recorded <= created < end):
        raise PersistenceValueError()
    if values["reservation_key"] != derive_reservation_key(1, _reservation_key_values(values)):
        raise PersistenceValueError()


def _expected_release_payload(values: dict[str, object]) -> dict[str, object]:
    return {
        "account_id": str(values["account_id"]),
        "approved_contracts": values["approved_contracts"],
        "approved_side": values["approved_side"],
        "attempt_evidence_set_sha256": values["attempt_evidence_set_sha256"],
        "collateral_asset_id": values["collateral_asset_id"],
        "collateral_asset_symbol": values["collateral_asset_symbol"],
        "command_id": str(values["command_id"]),
        "command_operation_kind": values["command_operation_kind"],
        "decision_evaluated_at": _timestamp(values["decision_evaluated_at"]),
        "decision_id": str(values["decision_id"]),
        "decision_outcome": values["decision_outcome"],
        "decision_recorded_at": _timestamp(values["decision_recorded_at"]),
        "environment": values["environment"],
        "mode": values["mode"],
        "observed_command_attempt_count": values["observed_command_attempt_count"],
        "observed_command_version": values["observed_command_version"],
        "observed_order_version": values["observed_order_version"],
        "observed_terminal_command_status": values["observed_terminal_command_status"],
        "order_attempt_event_evidence_count": values["order_attempt_event_evidence_count"],
        "order_attempt_evidence_count": values["order_attempt_evidence_count"],
        "order_id": str(values["order_id"]),
        "planned_collateral": _decimal_value(values["planned_collateral"]),
        "planned_incremental_gross_exposure": _decimal_value(
            values["planned_incremental_gross_exposure"]
        ),
        "planned_open_order_slots": values["planned_open_order_slots"],
        "planned_pending_rate_units": values["planned_pending_rate_units"],
        "planned_worst_case_loss": _decimal_value(values["planned_worst_case_loss"]),
        "prior_reservation_version": 1,
        "product_id": values["product_id"],
        "proof_observed_at": _timestamp(values["proof_observed_at"]),
        "rate_policy_sha256": values["rate_policy_sha256"],
        "rate_policy_version": values["rate_policy_version"],
        "rate_window_end_at": _timestamp(values["rate_window_end_at"]),
        "rate_window_start_at": _timestamp(values["rate_window_start_at"]),
        "reference_asset_id": values["reference_asset_id"],
        "reference_asset_symbol": values["reference_asset_symbol"],
        "region": values["region"],
        "release_key": values["release_key"],
        "release_reason_code": values["release_reason_code"],
        "released_collateral": _decimal_value(values["planned_collateral"]),
        "released_contracts": values["approved_contracts"],
        "released_incremental_gross_exposure": _decimal_value(
            values["planned_incremental_gross_exposure"]
        ),
        "released_open_order_slots": values["planned_open_order_slots"],
        "released_pending_rate_units": values["planned_pending_rate_units"],
        "released_worst_case_loss": _decimal_value(values["planned_worst_case_loss"]),
        "reservation_created_at": _timestamp(values["created_at"]),
        "reservation_id": str(values["id"]),
        "reservation_key": values["reservation_key"],
        "reservation_plan_sha256": values["reservation_plan_sha256"],
        "resulting_reservation_version": 2,
        "risk_effect": values["risk_effect"],
        "risk_state_id": str(values["risk_state_id"]),
    }


def _validate_lifecycle(values: dict[str, object], release_fields: tuple[str, ...]) -> None:
    status = _enum(values["status"], ("PENDING_DISPATCH", "RELEASED"))
    version = _integer(values["version"])
    if status == "PENDING_DISPATCH":
        if (
            version != 1
            or values["created_at"] != values["updated_at"]
            or values["remaining_contracts"] != values["approved_contracts"]
            or values["remaining_incremental_gross_exposure"]
            != values["planned_incremental_gross_exposure"]
            or values["remaining_worst_case_loss"] != values["planned_worst_case_loss"]
            or values["remaining_collateral"] != values["planned_collateral"]
            or values["remaining_open_order_slots"] != values["planned_open_order_slots"]
            or values["pending_rate_units"] != values["planned_pending_rate_units"]
            or any(values[field] is not None for field in release_fields)
        ):
            raise PersistenceValueError()
        return

    if version != 2 or any(values[field] is None for field in release_fields):
        raise PersistenceValueError()
    release_reason = _enum(values["release_reason_code"], ("DEFINITE_NOT_DISPATCHED",))
    release_proof_version = _integer(values["release_proof_version"])
    proof_observed_at = _time(values["proof_observed_at"])
    updated_at = _time(values["updated_at"])
    created_at = _time(values["created_at"])
    assert proof_observed_at is not None and updated_at is not None and created_at is not None
    if (
        values["remaining_contracts"] != 0
        or values["remaining_incremental_gross_exposure"] != 0
        or values["remaining_worst_case_loss"] != 0
        or values["remaining_collateral"] != 0
        or values["remaining_open_order_slots"] != 0
        or values["pending_rate_units"] != 0
        or release_reason != "DEFINITE_NOT_DISPATCHED"
        or release_proof_version != 1
        or updated_at != proof_observed_at
        or proof_observed_at < created_at
    ):
        raise PersistenceValueError()
    _hash(values["release_key"])
    _hash(values["attempt_evidence_set_sha256"])
    _hash(values["release_proof_sha256"])
    _enum(values["observed_terminal_command_status"], ("FAILED", "CANCELLED"))
    for field in ("observed_command_version", "observed_order_version"):
        _integer(values[field])
    for field in (
        "observed_command_attempt_count",
        "order_attempt_event_evidence_count",
        "order_attempt_evidence_count",
    ):
        _integer(values[field], positive=False)
    command_count = cast(int, values["observed_command_attempt_count"])
    attempt_count = cast(int, values["order_attempt_evidence_count"])
    event_count = cast(int, values["order_attempt_event_evidence_count"])
    if (
        command_count > MAX_RELEASE_PROOF_ATTEMPTS
        or attempt_count > MAX_RELEASE_PROOF_ATTEMPTS
        or event_count > MAX_RELEASE_PROOF_EVENTS
        or attempt_count != event_count
    ):
        raise PersistenceValueError()
    payload = values["release_proof_payload"]
    if type(payload) is not str:
        raise PersistenceValueError()
    validate_risk_reservation_release_proof(
        release_proof_version, payload, values["release_proof_sha256"]
    )
    decoded = decode_risk_reservation_release_proof(payload)
    if decoded.release_key != values["release_key"]:
        raise PersistenceValueError()
    raw = json.loads(payload)
    expected = _expected_release_payload(values)
    if set(raw) != RELEASE_PAYLOAD_FIELDS or raw != expected:
        raise PersistenceValueError()


def _validate_parent(values: dict[str, object], parent: dict[str, object]) -> None:
    if any(
        not _same(values[child], parent[parent_field])
        for child, parent_field in _PARENT_COLUMNS.items()
    ):
        raise PersistenceValueError()


def _validate_reservation(
    target: Any,
    immutable_fields: tuple[str, ...],
    release_fields: tuple[str, ...],
    connection: Connection | None = None,
    *,
    stored: bool,
) -> dict[str, object]:
    values = _load_values(target, connection, stored=stored)
    _validate_required_values(values)
    _validate_lifecycle(values, release_fields)
    _validate_parent(values, _parent_values(target, values, connection))
    return values


def install_risk_reservation_validation(
    model: type[Any], immutable_fields: tuple[str, ...], release_fields: tuple[str, ...]
) -> None:
    """Install complete insert/load/refresh and sole-transition ORM guards."""

    def before_insert(mapper: object, connection: Connection, target: Any) -> None:
        _validate_reservation(target, immutable_fields, release_fields, connection, stored=False)

    def before_update(mapper: object, connection: Connection, target: Any) -> None:
        stored_values = _validate_reservation(
            target, immutable_fields, release_fields, connection, stored=True
        )
        state = cast(InstanceState[Any], sa_inspect(target))
        proposed = dict(stored_values)
        for column in target.__table__.c:
            history = state.attrs[column.name].history
            if history.has_changes():
                if column.name not in state.dict:
                    raise PersistenceValueError()
                proposed[column.name] = state.dict[column.name]
        if stored_values["status"] != "PENDING_DISPATCH" or stored_values["version"] != 1:
            raise PersistenceValueError()
        if proposed["status"] != "RELEASED" or proposed["version"] != 2:
            raise PersistenceValueError()
        if any(not _same(proposed[field], stored_values[field]) for field in immutable_fields):
            raise PersistenceValueError()
        _validate_required_values(proposed)
        _validate_lifecycle(proposed, release_fields)
        _validate_parent(proposed, _parent_values(target, proposed, connection))

    def on_load(target: Any, context: object) -> None:
        _validate_reservation(target, immutable_fields, release_fields, stored=True)

    def on_refresh(target: Any, context: object, attrs: Iterable[str] | None) -> None:
        _validate_reservation(target, immutable_fields, release_fields, stored=True)

    def reject_delete(mapper: object, connection: Connection, target: Any) -> None:
        raise PersistenceValueError()

    event.listen(model, "before_insert", before_insert)
    event.listen(model, "before_update", before_update)
    event.listen(model, "before_delete", reject_delete)
    event.listen(model, "load", on_load, restore_load_context=True)
    event.listen(model, "refresh", on_refresh, restore_load_context=True)
