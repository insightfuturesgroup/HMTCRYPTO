"""Allowlisted Delta REST/WebSocket endpoints and safe URL construction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from urllib.parse import unquote, urlsplit

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

    def rest_url(self, path: str) -> str:
        """Join a literal absolute API path without permitting host/path confusion."""
        if not isinstance(path, str) or not path.startswith("/") or path in {"", "/"}:
            raise ConfigurationError(context={"operation": "join_rest_path"})
        parsed = urlsplit(path)
        decoded = unquote(path).replace("\\", "/")
        segments = decoded.split("/")
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or path.startswith("//")
            or "\\" in path
            or "//" in decoded
            or any(segment in {".", ".."} for segment in segments)
        ):
            raise ConfigurationError(context={"operation": "join_rest_path"})
        return f"{self.rest_base_url}{path}"


# Official docs verified 2026-11-03. Global public hosts are India-named in the
# Global reference; retain those exact values but keep Global streaming disabled.
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


def _selection_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise ConfigurationError(context={"operation": "resolve_delta_endpoints"})
    return raw


def _development_base_url(value: str) -> str:
    if not isinstance(value, str) or not value or any(character.isspace() for character in value):
        raise ConfigurationError(context={"operation": "validate_development_endpoint"})
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise ConfigurationError(context={"operation": "validate_development_endpoint"}) from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(context={"operation": "validate_development_endpoint"})
    return value.rstrip("/")


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
        return canonical
    if not allow_development_override or live_requested or canonical.environment != "testnet":
        raise ConfigurationError(context={"operation": "resolve_delta_development_override"})
    base_url = _development_base_url(development_rest_base_url)
    return replace(
        canonical,
        rest_base_url=base_url,
        public_ws_url=None,
        private_ws_url=None,
        streaming_capability=StreamingCapability.DISABLED_DEVELOPMENT_OVERRIDE,
        is_development_override=True,
        private_rest_allowed=False,
    )


__all__ = ["DeltaEndpointSet", "StreamingCapability", "get_delta_endpoints"]
