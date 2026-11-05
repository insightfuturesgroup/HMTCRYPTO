"""Closure-private composition authority for one process limiter registry."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, NoReturn, Protocol
from uuid import UUID, uuid4

from hmt_crypto.config import DeltaRegion
from hmt_crypto.domain.private_primitives import ExactUuid
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta import private_limiter_owner as own
from hmt_crypto.exchange.delta import private_limiter_registry_records as records
from hmt_crypto.exchange.delta import rate_limit_policy as limits
from hmt_crypto.exchange.delta.private_limiter_lifecycle_store import (
    _PrivateLimiterLifecycleStore as _Store,
)
from hmt_crypto.exchange.delta.weighted_limiter import WeightedRateLimiter
from hmt_crypto.worker.leadership import (
    Task210PrivateWorkerCurrentnessPort as _WorkerCurrentness,
)
from hmt_crypto.worker.leadership import Task210SinglePrivateWorkerProof as _WorkerProof

if TYPE_CHECKING:
    from hmt_crypto.exchange.delta.private_limiter_registry import PrivateLimiterRegistry


def _fail() -> NoReturn:
    raise ConfigurationError(context={"operation": "private_limiter_registry"}) from None


class _RegistryFactory(Protocol):
    def _create(
        self,
        *,
        ownership: _WorkerProof,
        ownership_currentness: _WorkerCurrentness,
        process_incarnation_id: ExactUuid,
        policy: limits.RateLimitPolicy,
    ) -> PrivateLimiterRegistry: ...


def _build_authority_installer() -> Callable[[type[PrivateLimiterRegistry]], _RegistryFactory]:
    used = False

    def install(registry_type: type[PrivateLimiterRegistry]) -> _RegistryFactory:
        nonlocal used
        from hmt_crypto.exchange.delta import private_limiter_registry as registry_module

        if used or registry_type is not registry_module.PrivateLimiterRegistry:
            _fail()
        used = True
        compositions: dict[int, tuple[str, object, object, object, object, object, UUID]] = {}
        process_owners: dict[int, PrivateLimiterRegistry] = {}
        pending: dict[
            int,
            tuple[
                object,
                PrivateLimiterRegistry,
                own._PrivateLimiterOwnerCapability,
                WeightedRateLimiter,
                _Store,
                dict[str, object],
                UUID,
            ],
        ] = {}

        def read_composition(
            registry: PrivateLimiterRegistry,
            capability: object,
            *,
            store: object,
            phase: str,
        ) -> tuple[object, object, UUID] | None:
            record = compositions.get(id(registry))
            if (
                type(registry) is not registry_type
                or type(capability) is not own._PrivateLimiterOwnerCapability
                or type(store) is not _Store
                or type(phase) is not str
                or record is None
                or record[0] != phase
                or record[1] is not registry
                or record[2] is not capability
                or record[5] is not store
                or process_owners.get(registry._process_id) is not registry
            ):
                return None
            return record[3], record[4], record[6]

        def initialize(
            registry: PrivateLimiterRegistry,
            *,
            _constructor_nonce: object,
        ) -> None:
            pending_record = pending.pop(id(_constructor_nonce), None)
            if (
                type(registry) is not registry_type
                or pending_record is None
                or pending_record[0] is not _constructor_nonce
                or pending_record[1] is not registry
            ):
                _fail()
            _, _, capability, limiter, store, values, registry_owner_id = pending_record
            for name, value in values.items():
                object.__setattr__(registry, name, value)
            object.__setattr__(capability, "_registry", registry)
            object.__setattr__(registry, "_owner_capability", capability)
            limiter._private_lifecycle_capability = capability
            gate = limiter._accounting_gate
            process_id = registry._process_id
            claiming = (
                "CLAIMING",
                registry,
                capability,
                limiter,
                gate,
                store,
                registry_owner_id,
            )
            compositions[id(registry)] = claiming
            process_owners[process_id] = registry
            try:
                _Store.__init__(store, owner_capability=capability)
                object.__setattr__(registry, "_lifecycle", store)
                limiter._private_lifecycle_store = store
                object.__setattr__(registry, "_open_epoch", 1)
                object.__setattr__(registry, "_closed", False)
                compositions[id(registry)] = ("BOUND", *claiming[1:])
                sample = limiter._sample_monotonic()
                with gate.hold():
                    limiter._accept_clock_sample_locked(*sample)
                    limiter._blocked_until_monotonic_ns = sample[1] + 300_000_000_000
            except BaseException:
                compositions.pop(id(registry), None)
                if process_owners.get(process_id) is registry:
                    process_owners.pop(process_id)
                raise

        class _PrivateLimiterRegistryFactory(records._OpaqueRegistryObject):
            __slots__ = ()

            def _create(
                self,
                *,
                ownership: _WorkerProof,
                ownership_currentness: _WorkerCurrentness,
                process_incarnation_id: ExactUuid,
                policy: limits.RateLimitPolicy,
            ) -> PrivateLimiterRegistry:
                if policy is not limits.DELTA_RATE_LIMIT_POLICY:
                    _fail()
                if type(ownership) is not _WorkerProof:
                    _fail()
                if type(ownership_currentness) is not _WorkerCurrentness:
                    _fail()
                if type(process_incarnation_id) is not UUID or process_incarnation_id.int == 0:
                    _fail()
                incarnation = UUID(int=process_incarnation_id.int)
                process_id = os.getpid()
                if (
                    ownership.region is not DeltaRegion.INDIA
                    or ownership.process_id != process_id
                    or ownership.process_incarnation_id != incarnation
                    or process_id in process_owners
                ):
                    _fail()
                returned = ownership_currentness.revalidate_single_private_worker_nonblocking(
                    ownership,
                    expected_external_user_id=ownership.external_user_id,
                    expected_region=DeltaRegion.INDIA,
                    expected_environment=ownership.environment,
                    expected_process_id=process_id,
                    expected_process_incarnation_id=incarnation,
                )
                if returned is not ownership:
                    _fail()
                scope = limits.RateLimitScope(
                    "india",
                    ownership.environment.value,
                    limits.ScopeKind.EXTERNAL_USER,
                    ownership.external_user_id,
                )
                limiter = WeightedRateLimiter(scope, policy=policy)
                registry = object.__new__(registry_type)
                capability = object.__new__(own._PrivateLimiterOwnerCapability)
                store = object.__new__(_Store)
                registry_owner_id = uuid4()
                values: dict[str, object] = {
                    "_closed": True,
                    "_leases": {},
                    "_lifecycle": None,
                    "_limiter": limiter,
                    "_loop_id": registry_module._loop_identity(),
                    "_open_epoch": 0,
                    "_ownership": ownership,
                    "_ownership_currentness": ownership_currentness,
                    "_process_id": process_id,
                    "_process_incarnation_id": incarnation,
                    "_registry_owner_id": registry_owner_id,
                    "_scope": scope,
                    "_thread_id": threading.get_ident(),
                }
                nonce = object()
                pending[id(nonce)] = (
                    nonce,
                    registry,
                    capability,
                    limiter,
                    store,
                    values,
                    registry_owner_id,
                )
                try:
                    registry_type.__init__(registry, _constructor_nonce=nonce)
                finally:
                    pending.pop(id(nonce), None)
                registry_module._PROCESS_REGISTRIES[process_id] = registry
                return registry

        type.__setattr__(registry_type, "__init__", initialize)
        type.__setattr__(registry_type, "_read_owner_composition", read_composition)
        return object.__new__(_PrivateLimiterRegistryFactory)

    return install


_take_private_registry_authority = _build_authority_installer()
del _build_authority_installer

__all__: list[str] = []
