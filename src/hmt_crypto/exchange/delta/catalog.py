"""Atomic in-memory product catalogue with last-good retention."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from hmt_crypto.domain import Product
from hmt_crypto.errors import (
    CatalogUnavailableError,
    HmtCryptoError,
    StaleCatalogError,
)
from hmt_crypto.exchange.delta.catalog_models import (
    CatalogProductLoad,
    CatalogSnapshot,
    CatalogSource,
    _clone_source,
    _is_exact_source,
    venue_allows_new_orders,
)
from hmt_crypto.exchange.delta.paginator import MISSING_AFTER, PaginationResult

UtcClock = Callable[[], datetime]


class CatalogRefreshStatus(StrEnum):
    """Outcome of one complete catalogue refresh attempt."""

    PUBLISHED_CHANGED = "published_changed"
    PUBLISHED_UNCHANGED = "published_unchanged"
    RETAINED_LAST_GOOD = "retained_last_good"
    UNAVAILABLE = "unavailable"


ProductFetcher = Callable[[], Awaitable[CatalogProductLoad]]


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("catalogue clock must return aware datetimes")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CatalogRefreshResult:
    """Safe immutable outcome that never retains payloads or exceptions."""

    status: CatalogRefreshStatus
    attempted_at: datetime
    active_revision: int | None
    active_content_sha256: str | None
    product_count: int
    safe_error_code: str | None

    def __init__(
        self,
        *,
        status: CatalogRefreshStatus,
        attempted_at: datetime,
        active_revision: int | None,
        active_content_sha256: str | None,
        product_count: int,
        safe_error_code: str | None,
    ) -> None:
        if not isinstance(status, CatalogRefreshStatus):
            raise TypeError("refresh status is invalid")
        attempted = _utc(attempted_at)
        if active_revision is not None and (
            isinstance(active_revision, bool)
            or not isinstance(active_revision, int)
            or active_revision <= 0
        ):
            raise ValueError("active revision is invalid")
        if active_content_sha256 is not None and (
            type(active_content_sha256) is not str
            or len(active_content_sha256) != 64
            or any(character not in "0123456789abcdef" for character in active_content_sha256)
        ):
            raise ValueError("active content hash is invalid")
        if (
            isinstance(product_count, bool)
            or not isinstance(product_count, int)
            or product_count < 0
        ):
            raise ValueError("product count is invalid")
        if (active_revision is None) != (active_content_sha256 is None):
            raise ValueError("active snapshot evidence is inconsistent")
        if active_revision is None and product_count != 0:
            raise ValueError("unavailable results cannot report products")
        if safe_error_code is not None and (
            type(safe_error_code) is not str
            or not 1 <= len(safe_error_code) <= 64
            or not safe_error_code.replace("_", "a").isalnum()
            or not safe_error_code.isascii()
        ):
            raise ValueError("safe error code is invalid")
        published = status in {
            CatalogRefreshStatus.PUBLISHED_CHANGED,
            CatalogRefreshStatus.PUBLISHED_UNCHANGED,
        }
        if published and (
            active_revision is None or product_count == 0 or safe_error_code is not None
        ):
            raise ValueError("published refresh evidence is inconsistent")
        if status is CatalogRefreshStatus.RETAINED_LAST_GOOD and (
            active_revision is None or product_count == 0 or safe_error_code is None
        ):
            raise ValueError("retained refresh evidence is inconsistent")
        if status is CatalogRefreshStatus.UNAVAILABLE and (
            active_revision is not None or product_count != 0 or safe_error_code is None
        ):
            raise ValueError("unavailable refresh evidence is inconsistent")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "attempted_at", attempted)
        object.__setattr__(self, "active_revision", active_revision)
        object.__setattr__(self, "active_content_sha256", active_content_sha256)
        object.__setattr__(self, "product_count", product_count)
        object.__setattr__(self, "safe_error_code", safe_error_code)

    def __repr__(self) -> str:
        return (
            "CatalogRefreshResult("
            f"status={self.status.value!r}, active_revision={self.active_revision!r}, "
            f"product_count={self.product_count}, safe_error_code={self.safe_error_code!r})"
        )

    def to_dict(self) -> dict[str, object]:
        """Return safe scalar refresh evidence for logs and operator APIs."""
        return {
            "status": self.status.value,
            "attempted_at": self.attempted_at.isoformat(),
            "active_revision": self.active_revision,
            "active_content_sha256": self.active_content_sha256,
            "product_count": self.product_count,
            "safe_error_code": self.safe_error_code,
        }


class _CatalogValidationError(Exception):
    pass


class _CatalogClockError(Exception):
    pass


class _CatalogSourceError(Exception):
    pass


class ProductCatalog:
    """Refresh and atomically publish one source-scoped in-memory catalogue."""

    def __init__(
        self,
        *,
        source: CatalogSource,
        fetch_products: ProductFetcher,
        ttl: timedelta,
        clock: UtcClock,
    ) -> None:
        if not _is_exact_source(source):
            raise TypeError("source must be CatalogSource")
        if not callable(fetch_products) or not callable(clock):
            raise TypeError("catalogue fetcher and clock must be callable")
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise ValueError("catalogue TTL must be positive")
        self._source = _clone_source(source)
        self._fetch_products = fetch_products
        self._ttl = ttl
        self._clock = clock
        self._active: CatalogSnapshot | None = None
        self._refresh_lock = asyncio.Lock()
        self._inflight: asyncio.Task[CatalogRefreshResult] | None = None

    @property
    def source(self) -> CatalogSource:
        """Return the immutable source identity."""
        return self._source

    def peek(self) -> CatalogSnapshot | None:
        """Return the active snapshot even when stale, without mutation or I/O."""
        return self._active

    def require_snapshot(self, *, at: datetime) -> CatalogSnapshot:
        """Return a fresh active snapshot or fail closed with a typed error."""
        try:
            checked_at = _utc(at)
        except (TypeError, ValueError):
            raise ValueError("catalogue check time must be aware") from None
        snapshot = self._active
        if snapshot is None:
            raise CatalogUnavailableError(
                context={
                    "operation": "catalog_snapshot",
                    "region": self._source.region.value,
                    "environment": self._source.environment.value,
                }
            )
        if not snapshot.is_fresh(checked_at):
            raise StaleCatalogError(
                context={
                    "operation": "catalog_snapshot",
                    "region": self._source.region.value,
                    "environment": self._source.environment.value,
                }
            )
        return snapshot

    async def refresh(self) -> CatalogRefreshResult:
        """Coalesce callers onto one shielded fetch/validate/publication task."""
        async with self._refresh_lock:
            task = self._inflight
            if task is None or task.done():
                task = asyncio.create_task(
                    self._refresh_once(),
                    name=(
                        "hmt-crypto-catalog-refresh-"
                        f"{self._source.region.value}-{self._source.environment.value}"
                    ),
                )
                self._inflight = task
        return await asyncio.shield(task)

    def _now(self) -> datetime:
        try:
            return _utc(self._clock())
        except Exception:
            raise _CatalogClockError from None

    def _validate_load(self, result: object) -> tuple[Product, ...]:
        if type(result) is not CatalogProductLoad:
            raise _CatalogValidationError
        source = result.source
        if (
            not _is_exact_source(source)
            or source.region is not self._source.region
            or source.environment is not self._source.environment
        ):
            raise _CatalogSourceError
        page = result.pagination
        if type(page) is not PaginationResult:
            raise _CatalogValidationError
        try:
            page_count = page.page_count
            complete = page.complete
            truncated = page.truncated
            terminal = page.terminal_after
            items = tuple(page.items)
        except (AttributeError, TypeError):
            raise _CatalogValidationError from None
        if (
            isinstance(page_count, bool)
            or not isinstance(page_count, int)
            or page_count <= 0
            or complete is not True
            or truncated is not False
            or (terminal is not None and terminal is not MISSING_AFTER)
            or not items
            or any(not isinstance(item, Product) for item in items)
        ):
            raise _CatalogValidationError
        return items

    def _outcome(
        self,
        *,
        status: CatalogRefreshStatus,
        attempted_at: datetime,
        safe_error_code: str | None,
    ) -> CatalogRefreshResult:
        active = self._active
        return CatalogRefreshResult(
            status=status,
            attempted_at=attempted_at,
            active_revision=None if active is None else active.revision,
            active_content_sha256=None if active is None else active.content_sha256,
            product_count=0 if active is None else len(active.products),
            safe_error_code=safe_error_code,
        )

    def _failure(self, *, attempted_at: datetime, code: str) -> CatalogRefreshResult:
        status = (
            CatalogRefreshStatus.UNAVAILABLE
            if self._active is None
            else CatalogRefreshStatus.RETAINED_LAST_GOOD
        )
        return self._outcome(status=status, attempted_at=attempted_at, safe_error_code=code)

    async def _refresh_once(self) -> CatalogRefreshResult:
        try:
            attempted_at = self._now()
        except _CatalogClockError:
            return self._failure(attempted_at=datetime.now(UTC), code="catalog_clock_invalid")
        try:
            raw_result = await self._fetch_products()
            fetched_at = self._now()
            products = self._validate_load(raw_result)
            validated_at = self._now()
            active = self._active
            if (
                attempted_at > fetched_at
                or fetched_at > validated_at
                or (active is not None and attempted_at < active.validated_at)
            ):
                raise _CatalogClockError
            revision = 1 if self._active is None else self._active.revision + 1
            snapshot = CatalogSnapshot(
                source=self._source,
                revision=revision,
                products=products,
                fetched_at=fetched_at,
                validated_at=validated_at,
                ttl=self._ttl,
            )
        except asyncio.CancelledError:
            raise
        except _CatalogClockError:
            return self._failure(attempted_at=attempted_at, code="catalog_clock_invalid")
        except _CatalogSourceError:
            return self._failure(attempted_at=attempted_at, code="catalog_source_mismatch")
        except (_CatalogValidationError, TypeError, ValueError):
            return self._failure(attempted_at=attempted_at, code="catalog_validation_failed")
        except HmtCryptoError as error:
            return self._failure(attempted_at=attempted_at, code=error.code)
        except Exception:
            return self._failure(attempted_at=attempted_at, code="catalog_refresh_failed")

        prior_hash = None if self._active is None else self._active.content_sha256
        self._active = snapshot
        status = (
            CatalogRefreshStatus.PUBLISHED_UNCHANGED
            if prior_hash == snapshot.content_sha256
            else CatalogRefreshStatus.PUBLISHED_CHANGED
        )
        return self._outcome(status=status, attempted_at=attempted_at, safe_error_code=None)


__all__ = [
    "CatalogRefreshResult",
    "CatalogRefreshStatus",
    "CatalogProductLoad",
    "CatalogSnapshot",
    "CatalogSource",
    "ProductCatalog",
    "venue_allows_new_orders",
]
