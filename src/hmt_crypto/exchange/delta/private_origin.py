"""Sealed canonical Delta India origins for inert private-read evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ConfigurationError


def _origin_error() -> ConfigurationError:
    return ConfigurationError(context={"operation": "canonical_private_origin"})


_ORIGIN_ISSUER = object()
_INDIA_PROD_VALUE = "https://api.india.delta.exchange"
_INDIA_TESTNET_VALUE = "https://cdn-ind.testnet.deltaex.org"


@dataclass(frozen=True, slots=True, eq=False, init=False, repr=False)
class CanonicalPrivateOrigin:
    """One module-issued India origin value without network authority."""

    _region: DeltaRegion = field(init=False, repr=False)
    _environment: DeltaEnvironment = field(init=False, repr=False)
    _value: str = field(init=False, repr=False)

    def __init__(
        self,
        *,
        _issuer: object,
        region: DeltaRegion,
        environment: DeltaEnvironment,
        value: str,
    ) -> None:
        if _issuer is not _ORIGIN_ISSUER:
            raise TypeError("canonical private origins are module-issued")
        object.__setattr__(self, "_region", region)
        object.__setattr__(self, "_environment", environment)
        object.__setattr__(self, "_value", value)

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        del cls, kwargs
        raise TypeError("canonical private origins cannot be subclassed")

    @property
    def region(self) -> DeltaRegion:
        """Return the exact source-captured region member."""
        return self._region

    @property
    def environment(self) -> DeltaEnvironment:
        """Return the exact source-captured environment member."""
        return self._environment

    @property
    def value(self) -> str:
        """Return the immutable canonical HTTPS origin."""
        return self._value

    def __repr__(self) -> str:
        return "CanonicalPrivateOrigin()"

    __str__ = __repr__

    def __copy__(self) -> NoReturn:
        raise TypeError("canonical private origins cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        del memo
        raise TypeError("canonical private origins cannot be copied")

    def __reduce__(self) -> NoReturn:
        raise TypeError("canonical private origins cannot be serialized")

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError("canonical private origins cannot be serialized")

    def __getstate__(self) -> NoReturn:
        raise TypeError("canonical private origins cannot be serialized")


def _issue_origin(
    region: DeltaRegion,
    environment: DeltaEnvironment,
    value: str,
) -> CanonicalPrivateOrigin:
    return CanonicalPrivateOrigin(
        _issuer=_ORIGIN_ISSUER,
        region=region,
        environment=environment,
        value=value,
    )


_INDIA_PROD_ORIGIN = _issue_origin(
    DeltaRegion.INDIA,
    DeltaEnvironment.PROD,
    _INDIA_PROD_VALUE,
)
_INDIA_TESTNET_ORIGIN = _issue_origin(
    DeltaRegion.INDIA,
    DeltaEnvironment.TESTNET,
    _INDIA_TESTNET_VALUE,
)


def _origin_is_intact(
    value: object,
    *,
    environment: DeltaEnvironment,
    expected_value: str,
) -> bool:
    if type(value) is not CanonicalPrivateOrigin:
        return False
    region_slot = object.__getattribute__(value, "_region")
    environment_slot = object.__getattribute__(value, "_environment")
    value_slot = object.__getattribute__(value, "_value")
    return (
        region_slot is DeltaRegion.INDIA
        and environment_slot is environment
        and type(value_slot) is str
        and value_slot == expected_value
    )


def _canonical_private_origin(
    region: DeltaRegion,
    environment: DeltaEnvironment,
) -> CanonicalPrivateOrigin:
    """Resolve only an exact India region/environment identity pair."""
    result: CanonicalPrivateOrigin | None = None
    try:
        if type(region) is DeltaRegion and region is DeltaRegion.INDIA:
            if type(environment) is DeltaEnvironment:
                if environment is DeltaEnvironment.PROD and _origin_is_intact(
                    _INDIA_PROD_ORIGIN,
                    environment=DeltaEnvironment.PROD,
                    expected_value=_INDIA_PROD_VALUE,
                ):
                    result = _INDIA_PROD_ORIGIN
                elif environment is DeltaEnvironment.TESTNET and _origin_is_intact(
                    _INDIA_TESTNET_ORIGIN,
                    environment=DeltaEnvironment.TESTNET,
                    expected_value=_INDIA_TESTNET_VALUE,
                ):
                    result = _INDIA_TESTNET_ORIGIN
    except Exception:
        pass
    if result is None:
        raise _origin_error()
    return result
