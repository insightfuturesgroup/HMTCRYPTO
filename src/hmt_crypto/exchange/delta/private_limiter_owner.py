"""Attestation helpers for one composition-local private limiter owner."""

from __future__ import annotations

from typing import NoReturn, Self, cast
from uuid import UUID

from hmt_crypto.errors import ConfigurationError


def _error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "private_limiter_owner"})


def _deny_capability_operation(*_args: object, **_kwargs: object) -> NoReturn:
    raise TypeError("private limiter owner capability operation unavailable")


class _PrivateLimiterOwnerCapability:
    """Uncopyable instance issued only by one registry-composition closure."""

    __slots__ = ("_registry",)

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        raise TypeError("private limiter owner capability unavailable")

    def __setattr__(self, _name: str, _value: object) -> NoReturn:
        raise AttributeError("private limiter owner capability is immutable")

    def __delattr__(self, _name: str) -> NoReturn:
        raise AttributeError("private limiter owner capability is immutable")

    __copy__ = _deny_capability_operation
    __deepcopy__ = _deny_capability_operation
    __reduce_ex__ = _deny_capability_operation
    __reduce__ = _deny_capability_operation
    __getstate__ = _deny_capability_operation
    __hash__ = _deny_capability_operation
    __eq__ = _deny_capability_operation


def _installed_parts(
    capability: object,
    *,
    store: object,
    phase: str,
) -> tuple[object, object, object, UUID] | None:
    if type(capability) is not _PrivateLimiterOwnerCapability:
        return None
    try:
        registry = object.__getattribute__(capability, "_registry")
        from hmt_crypto.exchange.delta import private_limiter_registry as registry_module

        if type(registry) is not registry_module.PrivateLimiterRegistry:
            return None
        parts = registry._read_owner_composition(capability, store=store, phase=phase)
    except (AttributeError, TypeError):
        return None
    if parts is None:
        return None
    limiter, gate, owner_id = parts
    return registry, limiter, gate, owner_id


def _bind_private_limiter_store(capability: object, store: object) -> object:
    parts = _installed_parts(capability, store=store, phase="CLAIMING")
    if parts is None or store is None:
        raise _error()
    registry, limiter, gate, _ = parts
    try:
        valid = (
            object.__getattribute__(registry, "_limiter") is limiter
            and object.__getattribute__(registry, "_owner_capability") is capability
            and object.__getattribute__(registry, "_lifecycle") is None
            and object.__getattribute__(registry, "_open_epoch") == 0
            and object.__getattribute__(registry, "_closed") is True
            and object.__getattribute__(limiter, "_accounting_gate") is gate
            and object.__getattribute__(limiter, "_private_lifecycle_capability") is capability
            and object.__getattribute__(limiter, "_private_lifecycle_store") is None
        )
    except Exception:
        raise _error() from None
    if not valid:
        raise _error()
    return gate


def _private_limiter_owner_is_bound(
    capability: object,
    *,
    limiter: object,
    gate: object,
    store: object,
    registry_owner_id: object,
) -> bool:
    parts = _installed_parts(capability, store=store, phase="BOUND")
    if parts is None:
        return False
    registry, owned_limiter, owned_gate, owner_id = parts
    try:
        valid = (
            owned_limiter is limiter
            and owned_gate is gate
            and owner_id is registry_owner_id
            and object.__getattribute__(registry, "_owner_capability") is capability
            and object.__getattribute__(registry, "_limiter") is limiter
            and object.__getattribute__(registry, "_lifecycle") is store
            and object.__getattribute__(registry, "_registry_owner_id") is registry_owner_id
            and object.__getattribute__(registry, "_open_epoch") > 0
            and object.__getattribute__(limiter, "_accounting_gate") is gate
            and object.__getattribute__(limiter, "_private_lifecycle_capability") is capability
            and object.__getattribute__(limiter, "_private_lifecycle_store") is store
            and object.__getattribute__(store, "_owner_capability") is capability
        )
        return valid
    except Exception:
        return False


def _private_limiter_owner_for_store(
    candidates: tuple[object, ...],
    *,
    store: object,
) -> _PrivateLimiterOwnerCapability | None:
    if type(candidates) is not tuple or store is None:
        return None
    selected: _PrivateLimiterOwnerCapability | None = None
    for candidate in candidates:
        parts = _installed_parts(candidate, store=store, phase="BOUND")
        if parts is None:
            continue
        registry, limiter, _, _ = parts
        try:
            references = (
                object.__getattribute__(registry, "_owner_capability"),
                object.__getattribute__(limiter, "_private_lifecycle_capability"),
                object.__getattribute__(store, "_owner_capability"),
            )
            valid = sum(reference is candidate for reference in references) >= 2
        except Exception:
            valid = False
        if valid:
            if selected is not None and selected is not candidate:
                return None
            selected = cast(_PrivateLimiterOwnerCapability, candidate)
    return selected


def _private_limiter_owner_parts(
    capability: object,
    *,
    store: object,
) -> tuple[object, object, object, UUID]:
    """Return closure-attested canonical owner parts without changing composition."""
    parts = _installed_parts(capability, store=store, phase="BOUND")
    if parts is None:
        raise _error()
    return parts


def _private_limiter_state_objects_match(
    capability: _PrivateLimiterOwnerCapability,
    *,
    limiter: object,
    store: object,
    state: object,
    identity_keys: tuple[UUID, ...],
    object_keys: tuple[int, int],
) -> bool:
    try:
        registration = object.__getattribute__(state, "registration")
        binding = object.__getattribute__(state, "binding")
        token = object.__getattribute__(state, "token")
        reservation = object.__getattribute__(state, "held_reservation")
        acquisition = object.__getattribute__(state, "acquisition")
        evidence = object.__getattribute__(state, "evidence")
        handle = object.__getattribute__(state, "handle")
        gate = object.__getattribute__(limiter, "_accounting_gate")
        registration_id, reservation_id, token_id = identity_keys[:3]
        identities = object.__getattribute__(store, "_identities")
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(
        _private_limiter_owner_is_bound(
            capability,
            limiter=limiter,
            gate=gate,
            store=store,
            registry_owner_id=registration.registry_owner_id,
        )
        and registration._owner_capability is capability
        and registration._limiter is limiter
        and registration._gate is gate
        and registration._store is store
        and object.__getattribute__(store, "_grants").get(registration_id) is state
        and object.__getattribute__(store, "_tokens").get(token_id) is state
        and object.__getattribute__(store, "_reservations").get(reservation_id) is state
        and object.__getattribute__(store, "_outcomes").get(object_keys[0]) is state
        and object.__getattribute__(store, "_latches").get(object_keys[1]) is state
        and all(identities.get(identity) is state for identity in identity_keys)
        and object.__getattribute__(token, "_grant") is state
        and object.__getattribute__(token, "_token_id") is token_id
        and object.__getattribute__(reservation, "_grant") is state
        and object.__getattribute__(reservation, "_reservation_id") is reservation_id
        and object.__getattribute__(acquisition, "_token") is token
        and object.__getattribute__(acquisition, "_binding") is binding
        and (
            evidence is None
            or object.__getattribute__(evidence, "_grant") is state
            and object.__getattribute__(evidence, "_binding") is binding
        )
        and (
            handle is None
            or object.__getattribute__(handle, "_grant") is state
            and object.__getattribute__(handle, "_binding") is binding
            and object.__getattribute__(store, "_handles").get(id(handle)) is state
        )
    )


def _require_private_limiter_owner(
    capability: object,
    *,
    limiter: object,
    gate: object,
    store: object,
    registry_owner_id: object,
) -> _PrivateLimiterOwnerCapability:
    if not _private_limiter_owner_is_bound(
        capability,
        limiter=limiter,
        gate=gate,
        store=store,
        registry_owner_id=registry_owner_id,
    ):
        raise _error()
    return cast(_PrivateLimiterOwnerCapability, capability)


__all__: list[str] = []
