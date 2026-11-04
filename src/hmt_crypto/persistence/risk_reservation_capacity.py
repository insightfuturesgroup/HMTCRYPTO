"""Pure exact capacity aggregation for reviewed reservation snapshots.

The function has no storage or locking authority.  Callers of a future
repository must provide one already-locked transaction snapshot.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, NoReturn, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.risk_reservation_contracts import (
    _enum,
    _hash,
    _integer,
    _time,
    _uuid,
)
from hmt_crypto.persistence.types import _decimal_text


def _fail() -> NoReturn:
    raise PersistenceValueError()


def _amount(value: object) -> Decimal:
    result = Decimal(_decimal_text(value, 38, 18))
    if result < 0:
        _fail()
    return result


_DECIMAL_SCALE = 18


def _scaled(value: Decimal) -> int:
    """Convert a validated scale-18 Decimal without ambient-context arithmetic."""
    sign, digits, exponent = value.as_tuple()
    if type(exponent) is not int:
        _fail()
    coefficient = int("".join(str(digit) for digit in digits) or "0")
    shift = _DECIMAL_SCALE + exponent
    if shift < 0:
        _fail()
    result = coefficient * int("1" + ("0" * shift))
    return -result if sign else result


def _from_scaled(value: int) -> Decimal:
    """Restore one exact scale-18 Decimal without ambient-context arithmetic."""
    digits = tuple(int(character) for character in str(abs(value))) or (0,)
    return Decimal((1 if value < 0 else 0, digits, -_DECIMAL_SCALE))


class _CapacityModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> Self:
        try:
            return super().model_validate(obj, *args, **kwargs)
        except (ValidationError, PersistenceValueError):
            raise PersistenceValueError() from None


class _ScopedCapacityModel(_CapacityModel):
    account_id: UUID
    region: str
    environment: str
    mode: str
    product_id: int

    @field_validator("account_id", mode="before")
    @classmethod
    def _account(cls, value: object) -> UUID:
        result = _uuid(value)
        assert result is not None
        return result

    @field_validator("region", mode="before")
    @classmethod
    def _region(cls, value: object) -> str:
        return _enum(value, ("india", "global"))

    @field_validator("environment", mode="before")
    @classmethod
    def _environment(cls, value: object) -> str:
        return _enum(value, ("testnet", "prod"))

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, value: object) -> str:
        return _enum(value, ("BACKTEST", "PAPER", "LIVE"))

    @field_validator("product_id", mode="before")
    @classmethod
    def _product(cls, value: object) -> int:
        result = _integer(value)
        assert result is not None
        return result


class CapacityPlanV1(_ScopedCapacityModel):
    contracts: int
    incremental_gross_exposure: Decimal
    worst_case_loss: Decimal
    collateral: Decimal
    open_order_slots: int
    pending_rate_units: int
    rate_policy_version: int
    rate_policy_sha256: str
    rate_window_start_at: datetime
    rate_window_end_at: datetime

    @field_validator("contracts", "pending_rate_units", "rate_policy_version", mode="before")
    @classmethod
    def _positive(cls, value: object) -> int:
        result = _integer(value)
        assert result is not None
        return result

    @field_validator("open_order_slots", mode="before")
    @classmethod
    def _nonnegative(cls, value: object) -> int:
        result = _integer(value, positive=False)
        assert result is not None
        return result

    @field_validator("incremental_gross_exposure", "worst_case_loss", "collateral", mode="before")
    @classmethod
    def _decimal(cls, value: object) -> Decimal:
        return _amount(value)

    @field_validator("rate_policy_sha256", mode="before")
    @classmethod
    def _sha(cls, value: object) -> str:
        return _hash(value)

    @field_validator("rate_window_start_at", "rate_window_end_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        result = _time(value)
        assert result is not None
        return result


class CapacityReservationV1(_ScopedCapacityModel):
    status: str
    remaining_contracts: int
    remaining_incremental_gross_exposure: Decimal
    remaining_worst_case_loss: Decimal
    remaining_collateral: Decimal
    remaining_open_order_slots: int
    pending_rate_units: int
    rate_policy_version: int
    rate_policy_sha256: str
    rate_window_start_at: datetime
    rate_window_end_at: datetime

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> str:
        if type(value) is not str or value not in {
            "PENDING_DISPATCH",
            "DISPATCH_COMMITTED",
            "RELEASED",
        }:
            _fail()
        return value

    @field_validator(
        "remaining_contracts",
        "remaining_open_order_slots",
        "pending_rate_units",
        mode="before",
    )
    @classmethod
    def _nonnegative(cls, value: object) -> int:
        result = _integer(value, positive=False)
        assert result is not None
        return result

    @field_validator("rate_policy_version", mode="before")
    @classmethod
    def _positive(cls, value: object) -> int:
        result = _integer(value)
        assert result is not None
        return result

    @field_validator(
        "remaining_incremental_gross_exposure",
        "remaining_worst_case_loss",
        "remaining_collateral",
        mode="before",
    )
    @classmethod
    def _decimal(cls, value: object) -> Decimal:
        return _amount(value)

    @field_validator("rate_policy_sha256", mode="before")
    @classmethod
    def _sha(cls, value: object) -> str:
        return _hash(value)

    @field_validator("rate_window_start_at", "rate_window_end_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        result = _time(value)
        assert result is not None
        return result


class RateConsumptionV1(_ScopedCapacityModel):
    consumed_units: int
    rate_policy_version: int
    rate_policy_sha256: str
    rate_window_start_at: datetime
    rate_window_end_at: datetime

    @field_validator("consumed_units", "rate_policy_version", mode="before")
    @classmethod
    def _positive(cls, value: object) -> int:
        result = _integer(value)
        assert result is not None
        return result

    @field_validator("rate_policy_sha256", mode="before")
    @classmethod
    def _sha(cls, value: object) -> str:
        return _hash(value)

    @field_validator("rate_window_start_at", "rate_window_end_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        result = _time(value)
        assert result is not None
        return result


@dataclass(frozen=True)
class CapacityTotals:
    contracts: int
    incremental_gross_exposure: Decimal
    worst_case_loss: Decimal
    collateral: Decimal
    open_order_slots: int
    rate_units: int


_BucketValue = CapacityPlanV1 | CapacityReservationV1 | RateConsumptionV1


def _scope(value: _ScopedCapacityModel) -> tuple[UUID, str, str, str]:
    return value.account_id, value.region, value.environment, value.mode


def _bucket(
    value: _BucketValue,
) -> tuple[UUID, str, str, str, int, str, datetime, datetime]:
    return (
        *_scope(value),
        value.rate_policy_version,
        value.rate_policy_sha256,
        value.rate_window_start_at,
        value.rate_window_end_at,
    )


def _overlaps(left: _BucketValue, right: CapacityPlanV1) -> bool:
    return (
        left.rate_window_start_at < right.rate_window_end_at
        and right.rate_window_start_at < left.rate_window_end_at
    )


def calculate_capacity(
    proposed: object, reservations: object, consumptions: object
) -> CapacityTotals:
    """Aggregate one bounded snapshot, including the proposed plan exactly once."""
    try:
        plan = CapacityPlanV1.model_validate(proposed)
        if plan.rate_window_start_at >= plan.rate_window_end_at or plan.pending_rate_units != 1:
            _fail()
        if type(reservations) not in {list, tuple} or type(consumptions) not in {list, tuple}:
            _fail()
        reservation_values = cast(list[object] | tuple[object, ...], reservations)
        consumption_values = cast(list[object] | tuple[object, ...], consumptions)
        owned_reservations = tuple(
            CapacityReservationV1.model_validate(
                item.model_dump() if type(item) is CapacityReservationV1 else item
            )
            for item in reservation_values
        )
        owned_consumptions = tuple(
            RateConsumptionV1.model_validate(
                item.model_dump() if type(item) is RateConsumptionV1 else item
            )
            for item in consumption_values
        )
        target = _bucket(plan)
        contracts = plan.contracts
        exposure = _scaled(plan.incremental_gross_exposure)
        loss = _scaled(plan.worst_case_loss)
        collateral = _scaled(plan.collateral)
        slots = plan.open_order_slots
        rate = plan.pending_rate_units
        for reservation in owned_reservations:
            if (
                _scope(reservation) != _scope(plan)
                or reservation.rate_window_start_at >= reservation.rate_window_end_at
            ):
                _fail()
            if (
                reservation.status == "PENDING_DISPATCH" and reservation.pending_rate_units != 1
            ) or (reservation.status != "PENDING_DISPATCH" and reservation.pending_rate_units != 0):
                _fail()
            if reservation.status == "RELEASED" and any(
                value != 0
                for value in (
                    reservation.remaining_contracts,
                    reservation.remaining_incremental_gross_exposure,
                    reservation.remaining_worst_case_loss,
                    reservation.remaining_collateral,
                    reservation.remaining_open_order_slots,
                    reservation.pending_rate_units,
                )
            ):
                _fail()
            if reservation.status != "RELEASED":
                if reservation.product_id == plan.product_id:
                    contracts += reservation.remaining_contracts
                exposure += _scaled(reservation.remaining_incremental_gross_exposure)
                loss += _scaled(reservation.remaining_worst_case_loss)
                collateral += _scaled(reservation.remaining_collateral)
                slots += reservation.remaining_open_order_slots
            if reservation.pending_rate_units:
                if _overlaps(reservation, plan) and _bucket(reservation) != target:
                    _fail()
                if _bucket(reservation) == target:
                    rate += reservation.pending_rate_units
        for consumption in owned_consumptions:
            if (
                _scope(consumption) != _scope(plan)
                or consumption.rate_window_start_at >= consumption.rate_window_end_at
                or consumption.consumed_units != 1
            ):
                _fail()
            if (
                consumption.consumed_units
                and _overlaps(consumption, plan)
                and _bucket(consumption) != target
            ):
                _fail()
            if _bucket(consumption) == target:
                rate += consumption.consumed_units
        return CapacityTotals(
            contracts=contracts,
            incremental_gross_exposure=_from_scaled(exposure),
            worst_case_loss=_from_scaled(loss),
            collateral=_from_scaled(collateral),
            open_order_slots=slots,
            rate_units=rate,
        )
    except Exception:
        raise PersistenceValueError() from None
