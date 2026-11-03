"""Exchange-neutral market-data enumerations used by Phase 1."""

from enum import StrEnum


class ContractType(StrEnum):
    """Documented Delta contract families plus explicit unsupported sentinels."""

    PERPETUAL_FUTURES = "perpetual_futures"
    FUTURES = "futures"
    CALL_OPTIONS = "call_options"
    PUT_OPTIONS = "put_options"
    SPOT = "spot"
    UNKNOWN = "unknown"


class NotionalType(StrEnum):
    """Known product notional models; only VANILLA is initially convertible."""

    VANILLA = "vanilla"
    INVERSE = "inverse"
    UNKNOWN = "unknown"


class ProductState(StrEnum):
    """Catalogue lifecycle states retained independently from tradability."""

    LIVE = "live"
    UNKNOWN = "unknown"


class TradingStatus(StrEnum):
    """Operational status needed by fail-closed market-data consumers."""

    OPERATIONAL = "operational"
    UNKNOWN = "unknown"
