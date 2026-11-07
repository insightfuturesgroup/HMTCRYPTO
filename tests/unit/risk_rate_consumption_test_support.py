"""Literal broker-free inputs for Task 2.1d9c consumption evidence."""

from datetime import UTC, datetime
from uuid import UUID

ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000002")
DECISION_ID = UUID("10000000-0000-4000-8000-000000000001")
RISK_STATE_ID = UUID("30000000-0000-4000-8000-000000000003")
COMMAND_ID = UUID("40000000-0000-4000-8000-000000000004")
ORDER_ID = UUID("50000000-0000-4000-8000-000000000005")
RESERVATION_ID = UUID("90000000-0000-4000-8000-000000000009")
ATTEMPT_ID = UUID("a0000000-0000-4000-8000-00000000000a")
EVENT_ID = UUID("b0000000-0000-4000-8000-00000000000b")
CONSUMPTION_ID = UUID("d0000000-0000-4000-8000-00000000000d")

WINDOW_START = datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
DECISION_EVALUATED = datetime(2026, 11, 4, 12, 0, 10, tzinfo=UTC)
DECISION_RECORDED = datetime(2026, 11, 4, 12, 0, 11, tzinfo=UTC)
RESERVATION_CREATED = datetime(2026, 11, 4, 12, 0, 12, tzinfo=UTC)
ATTEMPT_PREPARED = datetime(2026, 11, 4, 12, 0, 13, tzinfo=UTC)
ATTEMPT_RECORDED = datetime(2026, 11, 4, 12, 0, 14, tzinfo=UTC)
DISPATCH_RECORDED = datetime(2026, 11, 4, 12, 0, 15, tzinfo=UTC)
WINDOW_END = datetime(2026, 11, 4, 12, 1, 0, tzinfo=UTC)

ATTEMPT_PAYLOAD = (
    '{"order_type":"market","product_id":1101,"reduce_only":false,'
    '"requested_contracts":2,"side":"buy"}'
)
ATTEMPT_SHA = "7d551ad745bfadd1489333b70885de0c7f60a0447f094adc93219ad1d5c1bf00"
EVENT_PAYLOAD = '{"outcome":"UNKNOWN"}'
EVENT_SHA = "35cc047ec1e3ebea7d316d457be73a445edbe1aec2fe8eae7965a346e172c5b6"
PLAN_SHA = "c" * 64
RATE_SHA = "d" * 64
CONSUMPTION_KEY = "aeec50bf9cdda24ee76ec119c3b1ec40b400f41f080c8cc0e1c3bd3d2e5d1dfd"


def key_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "account_id": ACCOUNT_ID,
        "region": "india",
        "environment": "testnet",
        "mode": "PAPER",
        "reservation_id": RESERVATION_ID,
        "decision_recorded_at": DECISION_RECORDED,
        "reservation_created_at": RESERVATION_CREATED,
        "order_attempt_id": ATTEMPT_ID,
    }
    values.update(changes)
    return values


def evidence_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "consumption_id": CONSUMPTION_ID,
        "account_id": ACCOUNT_ID,
        "region": "india",
        "environment": "testnet",
        "mode": "PAPER",
        "reservation_id": RESERVATION_ID,
        "decision_id": DECISION_ID,
        "risk_state_id": RISK_STATE_ID,
        "command_id": COMMAND_ID,
        "command_operation_kind": "PLACE_ORDER",
        "order_id": ORDER_ID,
        "product_id": 1101,
        "decision_outcome": "APPROVED",
        "risk_effect": "RISK_INCREASE",
        "reservation_plan_sha256": PLAN_SHA,
        "decision_evaluated_at": DECISION_EVALUATED,
        "decision_recorded_at": DECISION_RECORDED,
        "reservation_created_at": RESERVATION_CREATED,
        "order_attempt_id": ATTEMPT_ID,
        "attempt_number": 1,
        "attempt_backend": "SIMULATOR",
        "attempt_prepared_at": ATTEMPT_PREPARED,
        "attempt_recorded_at": ATTEMPT_RECORDED,
        "attempt_evidence_schema_version": 1,
        "attempt_evidence_payload": ATTEMPT_PAYLOAD,
        "attempt_evidence_sha256": ATTEMPT_SHA,
        "dispatch_event_id": EVENT_ID,
        "dispatch_event_sequence": 1,
        "dispatch_event_kind": "DISPATCH_STARTED",
        "dispatch_event_evidence_schema_version": 1,
        "dispatch_event_evidence_payload": EVENT_PAYLOAD,
        "dispatch_event_evidence_sha256": EVENT_SHA,
        "rate_policy_version": 1,
        "rate_policy_sha256": RATE_SHA,
        "rate_window_start_at": WINDOW_START,
        "rate_window_end_at": WINDOW_END,
        "consumed_units": 1,
        "recorded_at": DISPATCH_RECORDED,
    }
    values.update(changes)
    return values
