"""ORM result-path and immutable-parent validation for risk decisions."""

import json
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import event, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import object_session
from sqlalchemy.orm.state import InstanceState

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.execution_support import guard_model
from hmt_crypto.persistence.risk_decision_contracts import CONTEXT_FIELDS
from hmt_crypto.persistence.risk_decision_evidence import (
    decode_risk_decision_evidence,
    validate_risk_decision_evidence,
)
from hmt_crypto.persistence.types import _decimal_text, _utc


def _load_values(
    target: Any, fields: tuple[str, ...], connection: Connection | None
) -> dict[str, object]:
    state = cast(InstanceState[Any], sa_inspect(target))
    values = {field: state.dict[field] for field in fields if field in state.dict}
    missing = [field for field in fields if field not in values]
    if missing:
        session = object_session(target)
        if state.identity is None or (connection is None and session is None):
            raise PersistenceValueError()
        if connection is None:
            assert session is not None
            connection = session.connection()
        table = target.__table__
        values.update(
            connection.execute(
                select(*(table.c[field] for field in missing)).where(
                    table.c.id == state.identity[0]
                )
            )
            .mappings()
            .one()
        )
    return values


def _json_value(value: object) -> object:
    if type(value) is UUID:
        return str(value)
    if type(value) is datetime:
        return _utc(value).isoformat(timespec="microseconds")
    if type(value) is Decimal:
        return _decimal_text(value, 38, 18)
    return value


def _validate_decision(
    target: Any, plan_fields: tuple[str, ...], connection: Connection | None = None
) -> None:
    fields = tuple(column.name for column in target.__table__.c)
    values = _load_values(target, fields, connection)
    validate_risk_decision_evidence(
        values["evidence_schema_version"],
        values["evidence_payload"],
        values["evidence_sha256"],
    )
    decoded = decode_risk_decision_evidence(values["evidence_payload"])
    payload = json.loads(decoded.payload)
    if (
        values["check_set_version"] != 1
        or values["check_set_sha256"] != decoded.check_set_sha256
        or values["reservation_plan_sha256"] != decoded.reservation_plan_sha256
    ):
        raise PersistenceValueError()
    for field in CONTEXT_FIELDS:
        sibling = values["id"] if field == "decision_id" else values[field]
        if payload[field] != _json_value(sibling):
            raise PersistenceValueError()
    plan_payload = payload["reservation_plan"]
    for field in plan_fields:
        if plan_payload[field] != _json_value(values[field]):
            raise PersistenceValueError()
    if connection is None:
        session = object_session(target)
        if session is None:
            raise PersistenceValueError()
        connection = session.connection()
    command_table = target.metadata.tables["hmt_crypto.command_operations"]
    parent = connection.execute(
        select(
            command_table.c.evidence_schema_version,
            command_table.c.evidence_payload,
            command_table.c.evidence_sha256,
        ).where(
            command_table.c.id == values["command_id"],
            command_table.c.account_id == values["account_id"],
            command_table.c.region == values["region"],
            command_table.c.environment == values["environment"],
            command_table.c.mode == values["mode"],
            command_table.c.operation_kind == values["command_operation_kind"],
            command_table.c.evidence_sha256 == values["command_evidence_sha256"],
        )
    ).one_or_none()
    if parent is None or tuple(parent) != (
        values["command_evidence_schema_version"],
        values["command_evidence_payload"],
        values["command_evidence_sha256"],
    ):
        raise PersistenceValueError()


def install_risk_decision_validation(model: type[Any], plan_fields: tuple[str, ...]) -> None:
    """Install append-only insert/load/full-or-partial-refresh guards."""

    def before_insert(mapper: object, connection: Connection, target: Any) -> None:
        _validate_decision(target, plan_fields, connection)

    def on_load(target: Any, context: object) -> None:
        _validate_decision(target, plan_fields)

    def on_refresh(target: Any, context: object, attrs: Iterable[str] | None) -> None:
        _validate_decision(target, plan_fields)

    guard_model(model, immutable=True)
    event.listen(model, "before_insert", before_insert)
    event.listen(model, "load", on_load, restore_load_context=True)
    event.listen(model, "refresh", on_refresh, restore_load_context=True)
