"""Shared strict validation for Delta HTTP header values."""

from __future__ import annotations

from hmt_crypto.errors import ConfigurationError

MAX_USER_AGENT_LENGTH = 256


def validate_user_agent(value: object) -> str:
    """Return one bounded visible-ASCII User-Agent or fail with a safe error."""
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or len(value) > MAX_USER_AGENT_LENGTH
        or not value.isascii()
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value)
    ):
        raise ConfigurationError(context={"operation": "validate_delta_user_agent"})
    return value


__all__ = ["MAX_USER_AGENT_LENGTH", "validate_user_agent"]
