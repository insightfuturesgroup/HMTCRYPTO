"""Closed policy for static Delta India private-read admission."""

from __future__ import annotations

from typing import NoReturn

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.private_read_wire import (
    _attest_frozen_private_read,
    _FrozenPrivateReadRequestV1,
)
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

_ADMITTED_READ_ROUTES = (
    PrivateRouteId.BALANCES,
    PrivateRouteId.MARGINED_POSITIONS,
    PrivateRouteId.LATEST_POSITION,
    PrivateRouteId.OPEN_ORDERS,
    PrivateRouteId.ORDER_HISTORY,
    PrivateRouteId.ORDER_BY_ID,
    PrivateRouteId.ORDER_BY_CLIENT_ID,
    PrivateRouteId.GET_LEVERAGE,
)


def _fail() -> NoReturn:
    raise ConfigurationError(context={"operation": "private_read_admission"}) from None


def _require_admissible_private_read(
    value: object,
) -> _FrozenPrivateReadRequestV1:
    """Attest one frozen request and enforce the closed eight-route allowlist."""
    try:
        if type(value) is not _FrozenPrivateReadRequestV1:
            _fail()
        request = _attest_frozen_private_read(value)
        if request.method != "GET" or not any(
            request.route_id is candidate for candidate in _ADMITTED_READ_ROUTES
        ):
            _fail()
        return request
    except Exception:
        _fail()


__all__: list[str] = []
