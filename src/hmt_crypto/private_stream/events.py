"""Receive-only private-stream event port and closed event attestation."""

from __future__ import annotations

import asyncio
from typing import NoReturn, Self, SupportsIndex, TypeAlias
from weakref import WeakKeyDictionary

from hmt_crypto.errors import ConfigurationError, ExchangeProtocolError
from hmt_crypto.exchange.delta.private_ws_protocol import (
    PrivateWsDelivery,
    PrivateWsGenerationInvalidated,
    attest_private_ws_delivery,
    attest_private_ws_invalidation,
)
from hmt_crypto.private_stream.reconciliation import (
    PrivateWsReconciliationReset,
    attest_private_ws_reconciliation_reset,
)

PrivateStreamEvent: TypeAlias = (
    PrivateWsReconciliationReset | PrivateWsDelivery | PrivateWsGenerationInvalidated
)

_PORTS: WeakKeyDictionary[PrivateStreamReceivePort, asyncio.Queue[PrivateStreamEvent]]
_CLASSES_CLOSED = False


class PrivateStreamReceivePort:
    """Opaque receive-only view over the supervisor's bounded internal queue."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private stream receive ports cannot be constructed")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if _CLASSES_CLOSED:
            raise TypeError("private stream receive ports cannot be subclassed")

    def __copy__(self) -> NoReturn:
        raise TypeError("private stream receive ports cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> NoReturn:
        raise TypeError("private stream receive ports cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("private stream receive ports cannot be serialized")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError("private stream receive ports cannot be serialized")

    async def get(self) -> PrivateStreamEvent:
        """Wait for and attest the next sealed event."""
        return attest_private_stream_event(await _queue(self).get())

    def get_nowait(self) -> PrivateStreamEvent:
        """Return and attest the next sealed event without waiting."""
        return attest_private_stream_event(_queue(self).get_nowait())

    def empty(self) -> bool:
        """Return whether no event is currently queued."""
        return _queue(self).empty()

    def qsize(self) -> int:
        """Return the bounded approximate event count."""
        return _queue(self).qsize()


_CLASSES_CLOSED = True
_PORTS = WeakKeyDictionary()


def _queue(value: object) -> asyncio.Queue[PrivateStreamEvent]:
    if type(value) is not PrivateStreamReceivePort:
        raise ConfigurationError(context={"operation": "private_stream_receive"})
    queue = _PORTS.get(value)
    if queue is None:
        raise ConfigurationError(context={"operation": "private_stream_receive"})
    return queue


def _issue_private_stream_receive_port(
    queue: asyncio.Queue[PrivateStreamEvent],
) -> PrivateStreamReceivePort:
    if type(queue) is not asyncio.Queue or queue.maxsize <= 0:
        raise ConfigurationError(context={"operation": "configure_private_stream_receive"})
    port = object.__new__(PrivateStreamReceivePort)
    _PORTS[port] = queue
    return port


def attest_private_stream_event(value: object) -> PrivateStreamEvent:
    """Attest one closed event and its generation currentness where applicable."""
    if type(value) is PrivateWsReconciliationReset:
        return attest_private_ws_reconciliation_reset(value)
    if type(value) is PrivateWsDelivery:
        return attest_private_ws_delivery(value)
    if type(value) is PrivateWsGenerationInvalidated:
        return attest_private_ws_invalidation(value)
    raise ExchangeProtocolError(
        context={"operation": "attest_delta_private_stream_event", "scope": "private"}
    )


__all__ = ["PrivateStreamEvent", "PrivateStreamReceivePort", "attest_private_stream_event"]
