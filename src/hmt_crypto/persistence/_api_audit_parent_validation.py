"""Read-only semantic parity with immutable dispatch and consumption parents."""

from __future__ import annotations

from typing import Any, Never

from sqlalchemy import select
from sqlalchemy.orm import object_session

from hmt_crypto.persistence.errors import PersistenceValueError


def _fail() -> Never:
    raise PersistenceValueError() from None


def _connection(target: Any, connection: Any | None) -> Any:
    if connection is not None:
        return connection
    session = object_session(target)
    if session is None:
        _fail()
    return session.connection()


def _parent_rows(target: Any, connection: Any) -> tuple[dict[str, object], dict[str, object]]:
    from hmt_crypto.persistence.execution_event_models import OrderAttemptEvent
    from hmt_crypto.persistence.risk_rate_consumption_models import RiskRateConsumption

    event_table = OrderAttemptEvent.__table__
    consumption_table = RiskRateConsumption.__table__
    event_id = target.dispatch_event_id or target.prior_dispatch_event_id
    consumption_id = target.consumption_id or target.prior_consumption_id
    if event_id is None or consumption_id is None:
        _fail()
    event_row = (
        connection.execute(
            select(
                event_table.c.id,
                event_table.c.account_id,
                event_table.c.region,
                event_table.c.environment,
                event_table.c.mode,
                event_table.c.attempt_id,
                event_table.c.backend,
                event_table.c.sequence,
                event_table.c.event_kind,
                event_table.c.recorded_at,
            ).where(event_table.c.id == event_id)
        )
        .mappings()
        .one()
    )
    consumption_row = (
        connection.execute(
            select(
                consumption_table.c.id,
                consumption_table.c.account_id,
                consumption_table.c.region,
                consumption_table.c.environment,
                consumption_table.c.mode,
                consumption_table.c.command_id,
                consumption_table.c.command_operation_kind,
                consumption_table.c.order_id,
                consumption_table.c.order_attempt_id,
                consumption_table.c.attempt_backend,
                consumption_table.c.dispatch_event_id,
                consumption_table.c.recorded_at,
            ).where(consumption_table.c.id == consumption_id)
        )
        .mappings()
        .one()
    )
    return dict(event_row), dict(consumption_row)


def _scope(target: Any) -> tuple[object, ...]:
    return (target.account_id, target.region, target.environment, target.mode)


def _validate_dispatch(
    target: Any,
    event: dict[str, object],
    consumption: dict[str, object],
) -> None:
    event_scope = tuple(event[name] for name in ("account_id", "region", "environment", "mode"))
    consumption_scope = tuple(
        consumption[name] for name in ("account_id", "region", "environment", "mode")
    )
    if event_scope != _scope(target) or consumption_scope != _scope(target):
        _fail()
    if (
        event["attempt_id"] != target.order_attempt_id
        or event["backend"] != target.attempt_backend
        or event["sequence"] != target.dispatch_event_sequence
        or event["event_kind"] != target.dispatch_event_kind
        or event["recorded_at"] != target.dispatch_recorded_at
        or consumption["command_id"] != target.command_id
        or consumption["command_operation_kind"] != target.operation
        or consumption["order_id"] != target.order_id
        or consumption["order_attempt_id"] != target.order_attempt_id
        or consumption["attempt_backend"] != target.attempt_backend
        or consumption["dispatch_event_id"] != event["id"]
        or consumption["recorded_at"] != target.dispatch_recorded_at
    ):
        _fail()


def _validate_prior(event: dict[str, object], consumption: dict[str, object]) -> None:
    if (
        event["sequence"] != 1
        or event["event_kind"] != "DISPATCH_STARTED"
        or consumption["dispatch_event_id"] != event["id"]
        or consumption["order_attempt_id"] != event["attempt_id"]
        or consumption["attempt_backend"] != event["backend"]
        or consumption["recorded_at"] != event["recorded_at"]
    ):
        _fail()


def validate_api_audit_parent_semantics(target: Any, connection: Any | None = None) -> None:
    """Compare copied audit facts with immutable parents using plain reads only."""
    dispatch = target.dispatch_event_id is not None or target.consumption_id is not None
    prior = target.prior_dispatch_event_id is not None or target.prior_consumption_id is not None
    if not dispatch and not prior:
        return
    if dispatch == prior:
        _fail()
    try:
        event, consumption = _parent_rows(target, _connection(target, connection))
        if dispatch:
            _validate_dispatch(target, event, consumption)
        else:
            if tuple(
                event[name] for name in ("account_id", "region", "environment", "mode")
            ) != _scope(target) or tuple(
                consumption[name] for name in ("account_id", "region", "environment", "mode")
            ) != _scope(target):
                _fail()
            _validate_prior(event, consumption)
    except PersistenceValueError:
        raise
    except Exception:
        _fail()


__all__: list[str] = []
