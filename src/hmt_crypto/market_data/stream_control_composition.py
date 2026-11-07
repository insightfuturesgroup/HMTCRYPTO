"""Exact production composition for one public stream-control owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import UUID

from hmt_crypto.exchange.delta.websocket_contracts import WsPlane
from hmt_crypto.exchange.delta.websocket_ownership import _claim_owned_ws_connection
from hmt_crypto.exchange.delta.websocket_transport import WsConnection
from hmt_crypto.market_data.stream_control_owner import (
    _OWNER_TOKEN,
    StreamControlOwner,
)
from hmt_crypto.market_data.stream_evidence_mailbox import StreamEvidenceMailbox

_PRODUCTION_OWNER_MARKER = object()


def issue_stream_control_owner(
    *,
    connection: object,
    sink: object,
    monotonic_ns: object,
    plane: object,
    connection_generation: object,
) -> StreamControlOwner:
    """Issue the production owner only over exact module-owned capabilities."""
    try:
        valid = (
            type(sink) is StreamEvidenceMailbox
            and callable(monotonic_ns)
            and type(plane) is WsPlane
            and type(connection_generation) is UUID
            and connection_generation.int != 0
            and _claim_owned_ws_connection(connection)
        )
    except BaseException:
        valid = False
    if not valid:
        del connection, sink, monotonic_ns, plane, connection_generation
        raise TypeError("production stream control dependencies are invalid") from None
    owner = StreamControlOwner(
        _token=_OWNER_TOKEN,
        _ownership_marker=_PRODUCTION_OWNER_MARKER,
        connection=cast(WsConnection, connection),
        sink=cast(StreamEvidenceMailbox, sink),
        monotonic_ns=cast(Callable[[], int], monotonic_ns),
        plane=cast(WsPlane, plane),
        connection_generation=cast(UUID, connection_generation),
    )
    return owner


def is_owned_stream_control_owner(value: object) -> bool:
    """Return whether production composition issued this exact owner."""
    try:
        return (
            type(value) is StreamControlOwner
            and value._ownership_marker is _PRODUCTION_OWNER_MARKER
        )
    except BaseException:
        return False


__all__ = ["issue_stream_control_owner", "is_owned_stream_control_owner"]
