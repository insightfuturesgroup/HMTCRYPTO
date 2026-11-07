"""Allowlisted Delta REST/WebSocket endpoints and safe URL construction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx

from hmt_crypto.errors import ConfigurationError


class StreamingCapability(StrEnum):
    """Operational status of WebSocket metadata for an endpoint set."""

    ENABLED = "enabled"
    DISABLED_PENDING_TESTNET_CONTRACT = "disabled_pending_testnet_contract"
    DISABLED_DEVELOPMENT_OVERRIDE = "disabled_development_override"


@dataclass(frozen=True, slots=True)
class DeltaEndpointSet:
    """Frozen endpoint metadata for exactly one Delta region/environment."""

    region: str
    environment: str
    rest_base_url: str
    public_ws_url: str | None
    private_ws_url: str | None
    streaming_capability: StreamingCapability
    is_development_override: bool = False
    # Phase 1 exposes public REST only. This remains false even for canonical hosts.
    private_rest_allowed: bool = False
    # Only the explicit testnet/non-LIVE factory path issues origin-bound evidence.
    # Ordinary construction and dataclasses.replace cannot carry authorization.
    _development_authorization: tuple[str, str, str] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def rest_url(self, path: str) -> str:
        """Join a literal absolute API path without permitting host/path confusion."""
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path in {"", "/"}
            or any(not character.isprintable() or character.isspace() for character in path)
            or "%" in path
        ):
            raise ConfigurationError(context={"operation": "join_rest_path"})
        parsed = urlsplit(path)
        normalized = path.replace("\\", "/")
        segments = normalized.split("/")
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or path.startswith("//")
            or "\\" in path
            or "//" in normalized
            or any(segment in {".", ".."} for segment in segments)
        ):
            raise ConfigurationError(context={"operation": "join_rest_path"})
        return f"{self.rest_base_url}{path}"


# India WebSocket hosts were reverified from official source manifest F01 on
# 2026-11-05. Global values remain inert metadata under the F22 disabled gate.
_CANONICAL_ENDPOINTS: dict[tuple[str, str], DeltaEndpointSet] = {
    ("india", "prod"): DeltaEndpointSet(
        region="india",
        environment="prod",
        rest_base_url="https://api.india.delta.exchange",
        public_ws_url="wss://public-socket.india.delta.exchange",
        private_ws_url="wss://socket.india.delta.exchange",
        streaming_capability=StreamingCapability.ENABLED,
    ),
    ("india", "testnet"): DeltaEndpointSet(
        region="india",
        environment="testnet",
        rest_base_url="https://cdn-ind.testnet.deltaex.org",
        public_ws_url="wss://socket-ind-pub.testnet.deltaex.org",
        private_ws_url="wss://socket-ind.testnet.deltaex.org",
        streaming_capability=StreamingCapability.ENABLED,
    ),
    ("global", "prod"): DeltaEndpointSet(
        region="global",
        environment="prod",
        rest_base_url="https://api.delta.exchange",
        public_ws_url="wss://public-socket.india.delta.exchange",
        private_ws_url="wss://socket.delta.exchange",
        streaming_capability=StreamingCapability.DISABLED_PENDING_TESTNET_CONTRACT,
    ),
    ("global", "testnet"): DeltaEndpointSet(
        region="global",
        environment="testnet",
        rest_base_url="https://testnet-api.delta.exchange",
        public_ws_url="wss://socket-ind-pub.testnet.deltaex.org",
        private_ws_url="wss://testnet-socket.delta.exchange",
        streaming_capability=StreamingCapability.DISABLED_PENDING_TESTNET_CONTRACT,
    ),
}
_CANONICAL_REST_HOSTS = frozenset(
    host
    for endpoint in _CANONICAL_ENDPOINTS.values()
    if (host := urlsplit(endpoint.rest_base_url).hostname) is not None
)


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return {"http": 80, "https": 443}.get(scheme)


def _same_origin(left: str, right: str) -> bool:
    left_url = urlsplit(left)
    right_url = urlsplit(right)
    return (
        left_url.scheme.casefold() == right_url.scheme.casefold()
        and left_url.hostname is not None
        and right_url.hostname is not None
        and left_url.hostname.casefold() == right_url.hostname.casefold()
        and _effective_port(left_url.scheme.casefold(), left_url.port)
        == _effective_port(right_url.scheme.casefold(), right_url.port)
    )


def _selection_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise ConfigurationError(context={"operation": "resolve_delta_endpoints"})
    return raw


def _development_base_url(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(not character.isprintable() or character.isspace() for character in value)
    ):
        raise ConfigurationError(context={"operation": "validate_development_endpoint"})
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except (UnicodeError, ValueError) as error:
        raise ConfigurationError(context={"operation": "validate_development_endpoint"}) from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or "%" in parsed.netloc
    ):
        raise ConfigurationError(context={"operation": "validate_development_endpoint"})
    _validate_development_hostname(parsed.hostname)
    try:
        normalized = httpx.URL(value)
    except (httpx.InvalidURL, UnicodeError, ValueError) as error:
        raise ConfigurationError(context={"operation": "validate_development_endpoint"}) from error
    if normalized.scheme not in {"http", "https"} or normalized.host is None:
        raise ConfigurationError(context={"operation": "validate_development_endpoint"})
    return str(normalized).rstrip("/")


_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _validate_development_hostname(hostname: str) -> None:
    """Require an IP, localhost, or a round-trippable IDNA DNS name."""
    try:
        ip_address(hostname)
        return
    except ValueError:
        pass
    if hostname.casefold() == "localhost":
        return
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise ConfigurationError(context={"operation": "validate_development_endpoint"}) from error
    if not ascii_hostname or len(ascii_hostname) > 253:
        raise ConfigurationError(context={"operation": "validate_development_endpoint"})
    labels = ascii_hostname.split(".")
    if any(not _DNS_LABEL.fullmatch(label) for label in labels):
        raise ConfigurationError(context={"operation": "validate_development_endpoint"})
    try:
        for label in labels:
            if label.startswith("xn--"):
                label.encode("ascii").decode("idna")
    except UnicodeError as error:
        raise ConfigurationError(context={"operation": "validate_development_endpoint"}) from error


def get_delta_endpoints(
    region: object,
    environment: object,
    *,
    development_rest_base_url: str | None = None,
    allow_development_override: bool = False,
    live_requested: bool = False,
) -> DeltaEndpointSet:
    """Resolve exact allowlisted endpoints, or an explicit public-only dev override."""
    if type(allow_development_override) is not bool or type(live_requested) is not bool:
        raise ConfigurationError(context={"operation": "resolve_delta_development_override"})
    selection = (_selection_value(region), _selection_value(environment))
    try:
        canonical = _CANONICAL_ENDPOINTS[selection]
    except KeyError as error:
        raise ConfigurationError(context={"operation": "resolve_delta_endpoints"}) from error
    if development_rest_base_url is None:
        if allow_development_override:
            raise ConfigurationError(context={"operation": "resolve_delta_development_override"})
        return replace(canonical)
    if not allow_development_override or live_requested or canonical.environment != "testnet":
        raise ConfigurationError(context={"operation": "resolve_delta_development_override"})
    base_url = _development_base_url(development_rest_base_url)
    candidate_host = urlsplit(base_url).hostname
    if candidate_host is not None and candidate_host.casefold() in _CANONICAL_REST_HOSTS:
        if _same_origin(base_url, canonical.rest_base_url):
            return replace(canonical)
        raise ConfigurationError(context={"operation": "resolve_delta_development_override"})
    endpoint = replace(
        canonical,
        rest_base_url=base_url,
        public_ws_url=None,
        private_ws_url=None,
        streaming_capability=StreamingCapability.DISABLED_DEVELOPMENT_OVERRIDE,
        is_development_override=True,
        private_rest_allowed=False,
    )
    object.__setattr__(
        endpoint, "_development_authorization", (canonical.region, canonical.environment, base_url)
    )
    return endpoint


def _validated_endpoint_snapshot(value: DeltaEndpointSet) -> DeltaEndpointSet:
    """Revalidate source/origin metadata and return an independently owned endpoint."""
    context = {"operation": "public_client_endpoints"}
    if type(value) is not DeltaEndpointSet:
        raise ConfigurationError(context=context)
    if (
        any(
            type(part) is not str for part in (value.region, value.environment, value.rest_base_url)
        )
        or type(value.is_development_override) is not bool
        or value.private_rest_allowed is not False
        or type(value.streaming_capability) is not StreamingCapability
        or any(
            type(url) is not str
            for url in (value.public_ws_url, value.private_ws_url)
            if url is not None
        )
    ):
        raise ConfigurationError(context=context)
    try:
        base_url = _development_base_url(value.rest_base_url)
        if value.is_development_override:
            authorization = value._development_authorization
            if (
                type(authorization) is not tuple
                or any(type(part) is not str for part in authorization)
                or authorization != (value.region, value.environment, base_url)
            ):
                raise ConfigurationError(context=context)
            expected = get_delta_endpoints(
                value.region,
                value.environment,
                development_rest_base_url=base_url,
                allow_development_override=True,
            )
        else:
            expected = get_delta_endpoints(value.region, value.environment)
        if (
            not _same_origin(base_url, expected.rest_base_url)
            or value.public_ws_url != expected.public_ws_url
            or value.private_ws_url != expected.private_ws_url
            or value.streaming_capability is not expected.streaming_capability
            or value.is_development_override is not expected.is_development_override
        ):
            raise ConfigurationError(context=context)
    except ConfigurationError:
        raise ConfigurationError(context=context) from None
    return expected


__all__ = ["DeltaEndpointSet", "StreamingCapability", "get_delta_endpoints"]
