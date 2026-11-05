"""Input-free after-validator regressions for private account evidence."""

from __future__ import annotations

import gc
import weakref
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from delta_private_model_test_support import account_api, reachable_exceptions
from pydantic import ValidationError

pytestmark = pytest.mark.unit

_CANARY = "8123456789012345678"
_SAFE_VALUE_ERROR = [
    {
        "type": "private_model_value",
        "loc": (),
        "msg": "invalid private model value",
        "input": None,
    }
]


def _account(api: object) -> object:
    from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode

    return api.AccountRef(  # type: ignore[attr-defined]
        account_id=UUID("00000000-0000-0000-0000-000000000301"),
        external_user_id=_CANARY,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=ExecutionMode.PAPER,
        credential_version=7,
    )


def _invalid_case(
    api: object, case: str, account: object
) -> tuple[type[object], dict[str, object]]:
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    first = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    if case == "observation-time":
        return api.ObservationContext, {  # type: ignore[attr-defined]
            "account": account,
            "endpoint_id": PrivateRouteId.BALANCES,
            "correlation_id": UUID("00000000-0000-0000-0000-000000000302"),
            "requested_at": first,
            "received_at": first - timedelta(microseconds=1),
            "source": "delta_rest",
        }
    values = {
        "account": account,
        "endpoint_id": PrivateRouteId.TRANSACTIONS,
        "first_requested_at": first,
        "last_received_at": first,
        "page_correlation_ids": (UUID("00000000-0000-0000-0000-000000000303"),),
        "credential_version": 7,
        "policy_version": "delta-docs-2026-11-04-private-v1",
        "mode": api.CollectionMode.STRICT,  # type: ignore[attr-defined]
    }
    if case == "pagination-time":
        values["last_received_at"] = first - timedelta(microseconds=1)
    else:
        values["credential_version"] = 8
    return api.PrivatePaginationProvenance, values  # type: ignore[attr-defined]


def _assert_safe_value_error(error: ValidationError) -> None:
    assert error.errors(include_url=False) == _SAFE_VALUE_ERROR
    for reachable in reachable_exceptions(error):
        rendered = f"{reachable!s} {reachable!r} {reachable.args!r}"
        if isinstance(reachable, ValidationError):
            details = reachable.errors(include_url=False)
            rendered += repr(details)
            assert all(detail.get("input") is None for detail in details)
        assert _CANARY not in rendered


@pytest.mark.parametrize(
    "case",
    ("observation-time", "pagination-time", "pagination-credential"),
)
@pytest.mark.parametrize("entrypoint", ("constructor", "model_validate"))
def test_account_after_validator_failures_are_input_free(case: str, entrypoint: str) -> None:
    """After-validator failures disclose neither their condition nor raw account state."""
    api = account_api()
    account = _account(api)
    account_ref = weakref.ref(account)
    model, payload = _invalid_case(api, case, account)
    with pytest.raises(ValidationError) as captured:
        if entrypoint == "constructor":
            model(**payload)
        else:
            model.model_validate(payload)  # type: ignore[attr-defined]
    error = captured.value
    _assert_safe_value_error(error)
    del account, payload, error, captured
    gc.collect()
    assert account_ref() is None
