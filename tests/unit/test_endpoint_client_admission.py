"""Client admission binds endpoint origins to immutable source evidence."""

from dataclasses import asdict, replace

import httpx
import pytest

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.client import DeltaPublicClient
from hmt_crypto.exchange.delta.endpoints import (
    DeltaEndpointSet,
    StreamingCapability,
    get_delta_endpoints,
)
from hmt_crypto.exchange.delta.rate_limiter import RateLimitScope, ScopeKind, WeightedRateLimiter

pytestmark = pytest.mark.unit


def _limiter(region: str = "india", environment: str = "testnet") -> WeightedRateLimiter:
    return WeightedRateLimiter(RateLimitScope(region, environment, ScopeKind.IP, "admission-test"))


@pytest.mark.parametrize("construction", ["direct", "replace"])
@pytest.mark.parametrize(
    "base",
    [
        "https://api.delta.exchange",
        "HTTPS://API.DELTA.EXCHANGE:443/",
        "https://api.india.delta.exchange/",
        "https://testnet-api.delta.exchange",
        "http://cdn-ind.testnet.deltaex.org",
        "https://cdn-ind.testnet.deltaex.org:444",
        "https://unapproved-marker.example",
        "https://secret@unapproved-marker.example",
    ],
)
def test_mismatched_origin_fails_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    construction: str,
    base: str,
) -> None:
    endpoint = get_delta_endpoints("india", "testnet")
    if construction == "direct":
        endpoint = DeltaEndpointSet(
            region="india",
            environment="testnet",
            rest_base_url=base,
            public_ws_url=endpoint.public_ws_url,
            private_ws_url=endpoint.private_ws_url,
            streaming_capability=endpoint.streaming_capability,
        )
    else:
        endpoint = replace(endpoint, rest_base_url=base)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid endpoint reached transport construction")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", forbidden)
    with pytest.raises(ConfigurationError) as raised:
        DeltaPublicClient(endpoints=endpoint, limiter=_limiter(), user_agent="tests/1")
    assert base not in repr(raised.value.to_dict())
    assert "unapproved-marker" not in str(raised.value)


@pytest.mark.parametrize(
    "change",
    [
        {"is_development_override": True},
        {"private_rest_allowed": True},
        {"public_ws_url": "wss://unapproved.example"},
        {"environment": "prod"},
    ],
)
def test_conflicting_metadata_fails_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
) -> None:
    endpoint = replace(get_delta_endpoints("india", "testnet"), **change)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: pytest.fail("transport created"))
    with pytest.raises(ConfigurationError):
        DeltaPublicClient(endpoints=endpoint, limiter=_limiter(), user_agent="tests/1")


def test_endpoint_subclass_cannot_override_dispatch() -> None:
    class RedirectingEndpoint(DeltaEndpointSet):
        def rest_url(self, path: str) -> str:
            return "https://api.delta.exchange" + path

    endpoint = get_delta_endpoints("india", "testnet")
    values = {
        name: getattr(endpoint, name)
        for name in (
            "region",
            "environment",
            "rest_base_url",
            "public_ws_url",
            "private_ws_url",
            "streaming_capability",
        )
    }
    with pytest.raises(ConfigurationError):
        DeltaPublicClient(
            endpoints=RedirectingEndpoint(**values),
            limiter=_limiter(),
            user_agent="tests/1",
            transport=httpx.MockTransport(lambda request: pytest.fail("dispatch")),
        )


def test_custom_origin_requires_factory_authorization_not_just_marker() -> None:
    endpoint = DeltaEndpointSet(
        region="india",
        environment="testnet",
        rest_base_url="http://localhost:9010",
        public_ws_url=None,
        private_ws_url=None,
        streaming_capability=StreamingCapability.DISABLED_DEVELOPMENT_OVERRIDE,
        is_development_override=True,
    )
    with pytest.raises(ConfigurationError):
        DeltaPublicClient(
            endpoints=endpoint,
            limiter=_limiter(),
            user_agent="tests/1",
            transport=httpx.MockTransport(lambda request: pytest.fail("dispatch")),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["india", "global"])
@pytest.mark.parametrize("custom", [False, True])
async def test_valid_endpoint_is_snapshotted_before_caller_mutation(
    region: str, custom: bool
) -> None:
    endpoint = get_delta_endpoints(
        region,
        "testnet",
        **(
            {
                "development_rest_base_url": "http://localhost:9010",
                "allow_development_override": True,
            }
            if custom
            else {}
        ),
    )
    expected = endpoint.rest_base_url + "/v2/tickers"
    if not custom:
        # A directly constructed, equivalent canonical origin remains usable.
        endpoint = DeltaEndpointSet(
            **{key: value for key, value in asdict(endpoint).items() if not key.startswith("_")}
        )
        endpoint = replace(endpoint, rest_base_url=endpoint.rest_base_url.upper() + ":443/")
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"success": True, "result": []})

    async with DeltaPublicClient(
        endpoints=endpoint,
        limiter=_limiter(region),
        user_agent="tests/1",
        transport=httpx.MockTransport(handler),
    ) as client:
        object.__setattr__(endpoint, "rest_base_url", "https://api.delta.exchange")
        object.__setattr__(endpoint, "region", "global")
        assert client._transport._endpoints is not endpoint
        assert client._transport._endpoints.region == region
        assert client._transport._endpoints.is_development_override is custom
        assert await client.get_tickers() == ()
    assert urls == [expected]


def test_replaced_authorized_custom_origin_must_be_reauthorized() -> None:
    endpoint = get_delta_endpoints(
        "india",
        "testnet",
        development_rest_base_url="http://localhost:9010",
        allow_development_override=True,
    )
    with pytest.raises(ConfigurationError):
        DeltaPublicClient(
            endpoints=replace(endpoint, rest_base_url="https://arbitrary-marker.example"),
            limiter=_limiter(),
            user_agent="tests/1",
            transport=httpx.MockTransport(lambda request: pytest.fail("dispatch")),
        )


@pytest.mark.parametrize(
    ("region", "environment", "candidate"),
    [
        ("india", "testnet", "HTTPS://API.DELTA.EXCHANGE:443/"),
        ("india", "testnet", "https://api.india.delta.exchange/"),
        ("india", "testnet", "https://testnet-api.delta.exchange/"),
        ("india", "prod", "HTTPS://API.DELTA.EXCHANGE:443/"),
        ("india", "prod", "HTTPS://CDN-IND.TESTNET.DELTAEX.ORG:443/"),
        ("india", "prod", "https://testnet-api.delta.exchange/"),
        ("global", "testnet", "HTTPS://API.DELTA.EXCHANGE:443/"),
        ("global", "testnet", "https://api.india.delta.exchange/"),
        ("global", "testnet", "HTTPS://CDN-IND.TESTNET.DELTAEX.ORG:443/"),
        ("global", "prod", "https://api.india.delta.exchange/"),
        ("global", "prod", "HTTPS://CDN-IND.TESTNET.DELTAEX.ORG:443/"),
        ("global", "prod", "https://testnet-api.delta.exchange/"),
    ],
)
def test_admission_keeps_all_four_canonical_source_labels_consistent(
    monkeypatch: pytest.MonkeyPatch,
    region: str,
    environment: str,
    candidate: str,
) -> None:
    endpoint = get_delta_endpoints(region, environment)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: pytest.fail("transport"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: pytest.fail("transport"))
    with pytest.raises(ConfigurationError):
        DeltaPublicClient(
            endpoints=replace(endpoint, rest_base_url=candidate),
            limiter=_limiter(region, environment),
            user_agent="tests/1",
        )


def test_factory_returns_owned_metadata_without_exposing_canonical_registry() -> None:
    endpoint = get_delta_endpoints("india", "testnet")
    object.__setattr__(endpoint, "rest_base_url", "https://arbitrary-marker.example")
    assert get_delta_endpoints("india", "testnet").rest_base_url == (
        "https://cdn-ind.testnet.deltaex.org"
    )
