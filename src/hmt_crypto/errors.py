"""Safe, stable application and exchange error hierarchy."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from urllib.parse import urlsplit
from uuid import UUID

from hmt_crypto.persistence.redaction import JsonValue, redact

_SAFE_CONTEXT_KEYS = frozenset(
    {
        "correlation_id",
        "environment",
        "exchange_code",
        "exchange_request_id",
        "http_status",
        "method",
        "operation",
        "path",
        "product_id",
        "product_symbol",
        "region",
        "request_id",
        "scope",
        "state",
        "status_code",
        "symbol",
    }
)
_SAFE_CONTEXT_VALUE_TYPES = (type(None), bool, int, str, Decimal, UUID, datetime, date)


def _sanitize_context(context: Mapping[str, object] | None) -> dict[str, JsonValue]:
    """Retain only explicitly safe scalar correlation and exchange metadata."""
    if context is None:
        return {}
    selected: dict[str, object] = {}
    for raw_name, value in context.items():
        name = raw_name.casefold()
        if name not in _SAFE_CONTEXT_KEYS or not isinstance(value, _SAFE_CONTEXT_VALUE_TYPES):
            continue
        if name == "path" and isinstance(value, str):
            parsed = urlsplit(value)
            value = parsed.path
        selected[name] = value
    sanitized = redact(selected)
    return sanitized if isinstance(sanitized, dict) else {}


class HmtCryptoError(Exception):
    """Base error retaining only pre-redacted, JSON-safe diagnostic context."""

    _code: ClassVar[str] = "hmt_crypto_error"
    _message: ClassVar[str] = "HMT Crypto operation failed"

    def __init__(
        self,
        *,
        context: Mapping[str, object] | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        if retry_after_ms is not None and (
            isinstance(retry_after_ms, bool)
            or not isinstance(retry_after_ms, int)
            or retry_after_ms < 0
        ):
            raise ValueError("retry_after_ms must be a nonnegative integer")
        self._context = _sanitize_context(context)
        self._retry_after_ms = retry_after_ms
        super().__init__(f"{self.code}: {self.message}")

    @property
    def code(self) -> str:
        """Return the immutable stable machine-readable error code."""
        return self._code

    @property
    def message(self) -> str:
        """Return the immutable operator-safe public message."""
        return self._message

    @property
    def context(self) -> dict[str, JsonValue]:
        """Return an isolated copy of already-redacted diagnostic context."""
        return deepcopy(self._context)

    @property
    def retry_after_ms(self) -> int | None:
        """Return an immutable retry delay supplied by trusted protocol parsing."""
        return self._retry_after_ms

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize stable public fields without raw exchange or exception objects."""
        payload: dict[str, JsonValue] = {
            "code": self.code,
            "message": self.message,
            "context": self.context,
        }
        if self.retry_after_ms is not None:
            payload["retry_after_ms"] = self.retry_after_ms
        return payload

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class ExchangeError(HmtCryptoError):
    """Base class for normalized exchange failures."""

    _code = "exchange_error"
    _message = "Exchange request failed"


class ConfigurationError(HmtCryptoError):
    """Typed configuration failed a fail-closed validation or policy check."""

    _code = "configuration_error"
    _message = "Configuration is invalid"


class AuthError(ExchangeError):
    """Credentials or a signature were rejected by the exchange."""

    _code = "auth_error"
    _message = "Exchange authentication failed"


class SignatureExpiredError(AuthError):
    """A signed request arrived outside the exchange timestamp window."""

    _code = "signature_expired"
    _message = "Exchange signature expired"


class UnauthorizedApiAccessError(AuthError):
    """The API key lacks access to the requested exchange operation."""

    _code = "unauthorized_api_access"
    _message = "Exchange API access is unauthorized"


class RateLimitError(ExchangeError):
    """The exchange rejected work because a request budget was exhausted."""

    _code = "rate_limit"
    _message = "Exchange rate limit exceeded"


class ExchangeDownError(ExchangeError):
    """The exchange or its transport is unavailable."""

    _code = "exchange_down"
    _message = "Exchange is unavailable"


class ExchangeTimeoutError(ExchangeError):
    """An exchange request exceeded its bounded transport timeout."""

    _code = "exchange_timeout"
    _message = "Exchange request timed out"


class OrderRejected(ExchangeError):
    """The exchange definitively rejected an order request."""

    _code = "order_rejected"
    _message = "Exchange rejected the order"


class ExchangeProtocolError(ExchangeError):
    """The exchange response violated the documented protocol contract."""

    _code = "exchange_protocol_error"
    _message = "Exchange protocol error"


class PaginationLimitError(HmtCryptoError):
    """A local pagination bound stopped a traversal before completion."""

    _code = "pagination_limit"
    _message = "Pagination safety limit reached"


class CatalogUnavailableError(HmtCryptoError):
    """No complete validated product-catalogue snapshot is available."""

    _code = "catalog_unavailable"
    _message = "Product catalogue is unavailable"


class StaleCatalogError(HmtCryptoError):
    """The last complete product-catalogue snapshot has expired."""

    _code = "catalog_stale"
    _message = "Product catalogue is stale"


class ProductNotFoundError(HmtCryptoError):
    """A product identifier is absent from the active catalogue snapshot."""

    _code = "product_not_found"
    _message = "Product was not found in the catalogue"


class AmbiguousExchangeResult(ExchangeError):
    """A write outcome cannot be established without reconciliation."""

    _code = "ambiguous_exchange_result"
    _message = "Exchange result is ambiguous"


class UnsupportedProductError(HmtCryptoError):
    """The requested operation has no verified adapter for this product model."""

    _code = "unsupported_product"
    _message = "Product model is unsupported"


class ContractSizingError(HmtCryptoError):
    """A contract conversion or price quantization failed closed."""

    _code = "contract_sizing"
    _message = "Contract sizing failed"


__all__ = [
    "AmbiguousExchangeResult",
    "AuthError",
    "CatalogUnavailableError",
    "ConfigurationError",
    "ContractSizingError",
    "ExchangeDownError",
    "ExchangeError",
    "ExchangeProtocolError",
    "ExchangeTimeoutError",
    "HmtCryptoError",
    "OrderRejected",
    "PaginationLimitError",
    "ProductNotFoundError",
    "RateLimitError",
    "SignatureExpiredError",
    "StaleCatalogError",
    "UnauthorizedApiAccessError",
    "UnsupportedProductError",
]
