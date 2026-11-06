"""Redacted WebSocket handshake and admission failures."""

from __future__ import annotations

from enum import StrEnum

from websockets.exceptions import InvalidStatus

from hmt_crypto.errors import ExchangeDownError


class WebSocketHandshakeCategory(StrEnum):
    """Safe reconnect classification for a failed physical open."""

    RATE_LIMIT = "rate_limit"
    CLIENT_REJECTION = "client_rejection"
    SERVER_REJECTION = "server_rejection"
    NETWORK = "network"
    PROTOCOL = "protocol"


class WebSocketHandshakeError(ExchangeDownError):
    """A redacted handshake failure retaining only status and category."""

    _code = "websocket_handshake_rejected"
    _message = "WebSocket handshake failed"

    def __init__(
        self,
        *,
        category: WebSocketHandshakeCategory,
        status_code: int | None,
    ) -> None:
        if type(category) is not WebSocketHandshakeCategory or (
            status_code is not None
            and (type(status_code) is not int or not 100 <= status_code <= 599)
        ):
            del category, status_code
            raise ValueError("invalid WebSocket handshake classification") from None
        self._category = category
        self._status_code = status_code
        context: dict[str, object] = {
            "operation": "websocket_open",
            "state": category.value,
        }
        if status_code is not None:
            context["http_status"] = status_code
        super().__init__(context=context)

    @property
    def category(self) -> WebSocketHandshakeCategory:
        """Return the bounded reconnect category."""
        return self._category

    @property
    def status_code(self) -> int | None:
        """Return the bounded HTTP status when the handshake produced one."""
        return self._status_code

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """Reconstruct copies and pickles from bounded classification only."""
        return _reconstruct_handshake_error, (self.category, self.status_code)


class WebSocketAttemptAdmissionError(ExchangeDownError):
    """The exact shared physical-open governor denied this attempt."""

    _code = "websocket_attempt_admission_failed"
    _message = "WebSocket connection attempt was not admitted"

    def __init__(self) -> None:
        super().__init__(context={"operation": "websocket_attempt_admission"})


def _reconstruct_handshake_error(
    category: WebSocketHandshakeCategory,
    status_code: int | None,
) -> WebSocketHandshakeError:
    return WebSocketHandshakeError(category=category, status_code=status_code)


# Source-manifest F02: excess connection attempts are reported as HTTP 429.
def classify_open_error(error: BaseException) -> WebSocketHandshakeError:
    """Collapse one dependency failure to bounded reconnect evidence."""
    status: int | None = None
    try:
        if type(error) is InvalidStatus:
            candidate = error.response.status_code
            if type(candidate) is int and 100 <= candidate <= 599:
                status = candidate
        is_network = isinstance(error, (OSError, TimeoutError))
    except BaseException:
        is_network = False
    if status == 429:
        category = WebSocketHandshakeCategory.RATE_LIMIT
    elif status is not None and 400 <= status <= 499:
        category = WebSocketHandshakeCategory.CLIENT_REJECTION
    elif status is not None and 500 <= status <= 599:
        category = WebSocketHandshakeCategory.SERVER_REJECTION
    elif is_network:
        category = WebSocketHandshakeCategory.NETWORK
    else:
        category = WebSocketHandshakeCategory.PROTOCOL
    return WebSocketHandshakeError(category=category, status_code=status)


__all__ = [
    "WebSocketAttemptAdmissionError",
    "WebSocketHandshakeCategory",
    "WebSocketHandshakeError",
    "classify_open_error",
]
