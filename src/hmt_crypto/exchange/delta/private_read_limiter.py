"""Trusted central-limiter admission for reviewed Delta private reads."""

from __future__ import annotations

from hmt_crypto.errors import ConfigurationError, HmtCryptoError
from hmt_crypto.exchange.delta.private_admission import (
    PrivateReadAdmission,
    _attest_private_read_admission,
)
from hmt_crypto.exchange.delta.private_admission_policy import _require_admissible_private_read
from hmt_crypto.exchange.delta.private_limiter_registry import (
    PrivateLimiterLease,
    PrivateLimiterRegistry,
    _revalidate_private_limiter_lease_nonblocking,
)
from hmt_crypto.exchange.delta.private_read_wire import _FrozenPrivateReadRequestV1
from hmt_crypto.exchange.delta.private_routes import (
    PrivateDispatchCapability,
    PrivateProfileState,
    get_private_route_spec,
)
from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitRoute, RateRequest


def _fail() -> ConfigurationError:
    return ConfigurationError(context={"operation": "private_read_limiter"})


def _rate_route(name: str) -> RateLimitRoute:
    for candidate in RateLimitRoute:
        if candidate.value == name:
            return candidate
    raise _fail()


async def _acquire_private_read_permit(
    *,
    lease: PrivateLimiterLease,
    admission: PrivateReadAdmission,
    request: _FrozenPrivateReadRequestV1,
) -> None:
    """Charge one reviewed read through its sole external-user limiter owner."""
    try:
        frozen = _require_admissible_private_read(request)
        admitted = _attest_private_read_admission(admission, request=frozen)
        if type(lease) is not PrivateLimiterLease:
            raise _fail()
        registry = object.__getattribute__(lease, "_registry")
        if type(registry) is not PrivateLimiterRegistry:
            raise _fail()
        returned = _revalidate_private_limiter_lease_nonblocking(
            lease,
            expected_scope=lease.scope,
            expected_process_id=admitted.process_id,
            expected_process_incarnation_id=admitted.process_incarnation_id,
        )
        spec = get_private_route_spec(frozen.route_id)
        if (
            returned is not lease
            or admitted.limiter_lease_id != lease.lease_id
            or admitted.registry_owner_id != lease.registry_owner_id
            or admitted.limiter_instance_id != lease.limiter_instance_id
            or admitted.external_user_id != lease.scope.external_id
            or spec.dispatch_capability is not PrivateDispatchCapability.READ_ONLY
            or spec.profile_state is not PrivateProfileState.REVIEWED
        ):
            raise _fail()
        route = _rate_route(spec.rate_route_name)
        permit = await object.__getattribute__(registry, "_limiter").acquire(
            RateRequest(route=route)
        )
        accounting = permit.accounting
        if (
            permit.rest_weight != spec.rest_weight
            or accounting is None
            or accounting.limiter_instance_id != lease.limiter_instance_id
        ):
            raise _fail()
    except HmtCryptoError:
        raise
    except Exception:
        raise _fail() from None


async def _observe_private_read_429(
    lease: PrivateLimiterLease,
    *,
    reset_header: str,
) -> None:
    """Publish one strict 429 cooldown to the same central limiter owner."""
    try:
        if type(lease) is not PrivateLimiterLease:
            raise _fail()
        registry = object.__getattribute__(lease, "_registry")
        if type(registry) is not PrivateLimiterRegistry:
            raise _fail()
        await object.__getattribute__(registry, "_limiter").observe_429(reset_header)
    except HmtCryptoError:
        raise
    except Exception:
        raise _fail() from None


__all__: list[str] = []
