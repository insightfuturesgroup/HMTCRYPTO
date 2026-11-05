"""Pure sealed records for the PID-owned private limiter registry."""

from __future__ import annotations

from typing import NoReturn, Self, cast
from uuid import UUID

from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitScope

_REGISTRY_MODULE = "hmt_crypto.exchange.delta.private_limiter_registry"
_AUTHORITY_MODULE = "hmt_crypto.exchange.delta.private_limiter_registry_authority"
_AUTHORIZED_OWNER_TYPES = frozenset(
    {
        (_REGISTRY_MODULE, "PrivateLimiterRegistry"),
        (_AUTHORITY_MODULE, "_PrivateLimiterRegistryFactory"),
    }
)


def _exact_uuid(value: object) -> UUID:
    if type(value) is not UUID or value.int == 0:
        raise TypeError("private limiter registry identity unavailable")
    return UUID(int=value.int)


def _deny_registry_operation(*_args: object, **_kwargs: object) -> NoReturn:
    raise TypeError("private limiter registry operation unavailable")


class _OpaqueRegistryObject:
    __slots__ = ()

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private limiter registry object unavailable")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        record_type = cls.__module__ == __name__ and cls.__name__ == "PrivateLimiterLease"
        owner_type = (cls.__module__, cls.__name__) in _AUTHORIZED_OWNER_TYPES
        if not record_type and not owner_type:
            raise TypeError("private limiter registry objects cannot be subclassed")

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("private limiter registry objects are immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("private limiter registry objects are immutable")

    __copy__ = _deny_registry_operation
    __deepcopy__ = _deny_registry_operation
    __reduce_ex__ = _deny_registry_operation
    __reduce__ = _deny_registry_operation
    __getstate__ = _deny_registry_operation
    __hash__ = _deny_registry_operation
    __eq__ = _deny_registry_operation


class PrivateLimiterLease(_OpaqueRegistryObject):
    """Sealed evidence that one exact external-user limiter is registry-owned."""

    __slots__ = (
        "_lease_id",
        "_limiter_instance_id",
        "_ownership_proof_id",
        "_policy_version",
        "_process_id",
        "_process_incarnation_id",
        "_registry",
        "_registry_open_epoch",
        "_registry_owner_id",
        "_scope",
    )

    @property
    def lease_id(self) -> UUID:
        return _exact_uuid(object.__getattribute__(self, "_lease_id"))

    @property
    def registry_owner_id(self) -> UUID:
        return _exact_uuid(object.__getattribute__(self, "_registry_owner_id"))

    @property
    def ownership_proof_id(self) -> UUID:
        return _exact_uuid(object.__getattribute__(self, "_ownership_proof_id"))

    @property
    def limiter_instance_id(self) -> UUID:
        return _exact_uuid(object.__getattribute__(self, "_limiter_instance_id"))

    @property
    def scope(self) -> RateLimitScope:
        value = object.__getattribute__(self, "_scope")
        return RateLimitScope(value.region, value.environment, value.kind, value.external_id)

    @property
    def policy_version(self) -> str:
        return cast(str, object.__getattribute__(self, "_policy_version"))

    @property
    def process_id(self) -> int:
        return cast(int, object.__getattribute__(self, "_process_id"))

    @property
    def process_incarnation_id(self) -> UUID:
        return _exact_uuid(object.__getattribute__(self, "_process_incarnation_id"))

    @property
    def registry_open_epoch(self) -> int:
        return cast(int, object.__getattribute__(self, "_registry_open_epoch"))


def _issue_private_limiter_lease(**values: object) -> PrivateLimiterLease:
    lease = object.__new__(PrivateLimiterLease)
    for name in PrivateLimiterLease.__slots__:
        if name not in values:
            raise TypeError("private limiter lease value unavailable")
        object.__setattr__(lease, name, values[name])
    if len(values) != len(PrivateLimiterLease.__slots__):
        raise TypeError("private limiter lease manifest mismatch")
    return lease


PrivateLimiterLease.__module__ = _REGISTRY_MODULE

__all__: list[str] = []
