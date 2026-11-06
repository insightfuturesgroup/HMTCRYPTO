"""Private ownership checks for module-issued WebSocket capabilities."""

from __future__ import annotations

import os

from hmt_crypto.exchange.delta import websocket_transport as _transport

_ABORT_INVARIANT_EXIT_CODE = 70


def _is_owned_ws_connection(value: object) -> bool:
    """Return whether an entered module-owned connector issued this connection."""
    try:
        return (
            type(value) is _transport._DeltaWsConnection
            and value._ownership_marker is _transport._PRODUCTION_CONNECTION_MARKER
            and type(value._raw) is _transport._OFFICIAL_CONNECTION_TYPE
            and callable(value._raw.transport.abort)
        )
    except BaseException:
        return False


def _is_owned_receive_capability(value: object) -> bool:
    """Return whether the exact entered connection issued this receiver."""
    try:
        return (
            type(value) is _transport._ReceiveCapability
            and type(value._connection) is _transport._DeltaWsConnection
            and value._issuance_marker is _transport._RECEIVER_CAPABILITY_MARKER
            and _is_owned_ws_connection(value._connection)
            and value._connection._issued_receiver is value
        )
    except BaseException:
        return False


def _claim_owned_receive_capability(value: object) -> bool:
    """Claim the sole actor session permitted to own this receiver."""
    try:
        if type(value) is not _transport._ReceiveCapability or not _is_owned_receive_capability(
            value
        ):
            return False
        if value._session_claimed:
            return False
        value._session_claimed = True
        return True
    except BaseException:
        return False


def _claim_owned_ws_connection(value: object) -> bool:
    """Atomically claim the one production control owner for this connection."""
    try:
        if type(value) is not _transport._DeltaWsConnection or not _is_owned_ws_connection(value):
            return False
        if value._owner_issued:
            return False
        value._owner_issued = True
        return True
    except BaseException:
        return False


def _abort_owned_ws_connection(value: object) -> bool:
    """Abort owned transport or terminate the unsafe worker invariant."""
    if type(value) is not _transport._DeltaWsConnection or not _is_owned_ws_connection(value):
        return False
    try:
        value._raw.transport.abort()
        return True
    except BaseException:
        os._exit(_ABORT_INVARIANT_EXIT_CODE)


__all__: list[str] = []
