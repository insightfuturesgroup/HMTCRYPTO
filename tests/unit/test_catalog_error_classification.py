"""Only exact known error classes may publish a trusted refresh error code."""

import pytest
from product_catalog_test_support import FetchQueue, new_catalog

from hmt_crypto import errors

pytestmark = pytest.mark.unit

_KNOWN = [
    (errors.AuthError, "auth_error"),
    (errors.CatalogUnavailableError, "catalog_unavailable"),
    (errors.StaleCatalogError, "catalog_stale"),
    (errors.ConfigurationError, "configuration_error"),
    (errors.ExchangeDownError, "exchange_down"),
    (errors.ExchangeError, "exchange_error"),
    (errors.ExchangeProtocolError, "exchange_protocol_error"),
    (errors.ExchangeTimeoutError, "exchange_timeout"),
    (errors.PaginationLimitError, "pagination_limit"),
    (errors.RateLimitError, "rate_limit"),
    (errors.SignatureExpiredError, "signature_expired"),
    (errors.UnauthorizedApiAccessError, "unauthorized_api_access"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("error_type", "code"), _KNOWN)
async def test_exact_known_error_keeps_code(
    error_type: type[errors.HmtCryptoError], code: str
) -> None:
    outcome = await new_catalog(FetchQueue(error_type())).refresh()
    assert outcome.safe_error_code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(("error_type", "code"), _KNOWN)
@pytest.mark.parametrize("override", [False, True])
async def test_custom_subclasses_cannot_inherit_or_override_trusted_codes(
    error_type: type[errors.HmtCryptoError],
    code: str,
    override: bool,
) -> None:
    custom = type("CustomFailure", (error_type,), {"_code": "rate_limit"} if override else {})
    outcome = await new_catalog(FetchQueue(custom())).refresh()
    assert outcome.safe_error_code == "catalog_refresh_failed"


@pytest.mark.asyncio
async def test_unknown_error_properties_are_never_evaluated() -> None:
    class HostileError(errors.ExchangeDownError):
        def __init__(self) -> None:
            Exception.__init__(self)

        @property
        def code(self) -> str:
            pytest.fail("hostile code evaluated")

        @property
        def _code(self) -> str:
            pytest.fail("hostile private code evaluated")

        @property
        def context(self) -> dict[str, object]:
            pytest.fail("hostile context evaluated")

        def __str__(self) -> str:
            pytest.fail("hostile string evaluated")

    outcome = await new_catalog(FetchQueue(HostileError())).refresh()
    assert outcome.safe_error_code == "catalog_refresh_failed"
