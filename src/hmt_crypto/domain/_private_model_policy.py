"""Non-overridable policy helpers for private exchange boundary models."""

from __future__ import annotations

from types import MappingProxyType

from pydantic import ConfigDict

INTEGRITY_GUARDED_ATTRIBUTES = (
    "__copy__",
    "__deepcopy__",
    "model_copy",
    "model_dump",
    "model_dump_json",
)

_CRITICAL_CONFIG = (
    ("extra", "forbid"),
    ("frozen", True),
    ("hide_input_in_errors", True),
    ("revalidate_instances", "always"),
    ("strict", True),
    ("validate_default", True),
)


def private_model_config() -> ConfigDict:
    """Return a fresh fail-closed configuration for one private model class."""
    return ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
        validate_default=True,
    )


def require_private_model_config(model_type: type[object], policy_owner: type[object]) -> None:
    """Reject any completed model class that weakens the private boundary."""
    try:
        namespace = type.__getattribute__(model_type, "__dict__")
        config = type.__getattribute__(model_type, "model_config")
    except Exception:
        raise TypeError("invalid private model configuration") from None
    if type(namespace) is not MappingProxyType:
        raise TypeError("invalid private model configuration") from None
    if model_type is not policy_owner and "__pydantic_init_subclass__" in namespace:
        raise TypeError("invalid private model configuration") from None
    if type(config) is not dict:
        raise TypeError("invalid private model configuration") from None
    for key, expected in _CRITICAL_CONFIG:
        if key not in config:
            raise TypeError("invalid private model configuration") from None
        actual = dict.__getitem__(config, key)
        if type(expected) is bool:
            valid = actual is expected
        else:
            valid = type(actual) is str and actual == expected
        if not valid:
            raise TypeError("invalid private model configuration") from None
