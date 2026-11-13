"""Normalization for database-backed worker identity values."""

from __future__ import annotations

from uuid import UUID


def normalize_worker_uuid(value: object, *, message: str) -> UUID:
    """Return an exact stdlib UUID, including for asyncpg's UUID subtype."""
    try:
        normalized = UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        raise ValueError(message) from None
    if normalized.int == 0:
        raise ValueError(message)
    return normalized
