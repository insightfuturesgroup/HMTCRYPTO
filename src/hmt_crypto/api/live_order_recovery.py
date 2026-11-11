"""Translate reviewed private-read order lookups into recovery observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from hmt_crypto.domain import private_orders as orders


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    result: str
    exchange_order_id: int | None

    def __post_init__(self) -> None:
        if (
            self.result not in {"FOUND", "NOT_FOUND"}
            or (
                self.result == "FOUND"
                and (type(self.exchange_order_id) is not int or self.exchange_order_id < 1)
            )
            or (self.result != "FOUND" and self.exchange_order_id is not None)
        ):
            raise ValueError("invalid recovery observation")


def recovery_observation_from_lookup(
    observed: orders.ObservedOrderLookupResult,
) -> RecoveryObservation:
    """Use the selector-validated private-read result without inference."""
    value = cast(orders.OrderLookupFound | orders.LookupNotFound, observed.value)
    if value.kind == "FOUND":
        return RecoveryObservation("FOUND", value.value.id)
    if value.kind == "NOT_FOUND":
        return RecoveryObservation("NOT_FOUND", None)
    raise ValueError("invalid recovery lookup result")


__all__ = ["RecoveryObservation", "recovery_observation_from_lookup"]
