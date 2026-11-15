"""Translate reviewed private-read order lookups into recovery observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from hmt_crypto.domain import private_orders as orders


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    result: str
    exchange_order_id: int | None
    order_state: str | None = None
    size: int | None = None
    unfilled_size: int | None = None
    cancellation_reason: str | None = None

    def __post_init__(self) -> None:
        if (
            self.result not in {"FOUND", "NOT_FOUND"}
            or (
                self.result == "FOUND"
                and (type(self.exchange_order_id) is not int or self.exchange_order_id < 1)
            )
            or (self.result != "FOUND" and self.exchange_order_id is not None)
            or (
                self.result == "FOUND"
                and self.order_state is not None
                and self.order_state not in {"open", "pending", "closed", "cancelled"}
            )
            or (
                self.result == "FOUND"
                and not (
                    (self.order_state is None and self.size is None and self.unfilled_size is None)
                    or (
                        self.order_state is not None
                        and self.size is not None
                        and self.unfilled_size is not None
                    )
                )
            )
            or (
                self.result != "FOUND"
                and any(
                    value is not None
                    for value in (
                        self.order_state,
                        self.size,
                        self.unfilled_size,
                        self.cancellation_reason,
                    )
                )
            )
            or (self.size is not None and self.size < 0)
            or (
                self.unfilled_size is not None
                and (self.unfilled_size < 0 or self.size is None or self.unfilled_size > self.size)
            )
        ):
            raise ValueError("invalid recovery observation")


def recovery_observation_from_lookup(
    observed: orders.ObservedOrderLookupResult,
) -> RecoveryObservation:
    """Use the selector-validated private-read result without inference."""
    value = cast(orders.OrderLookupFound | orders.LookupNotFound, observed.value)
    if value.kind == "FOUND":
        order = value.value
        if order.state.known is None:
            raise ValueError("unknown recovered order state")
        return RecoveryObservation(
            "FOUND",
            order.id,
            order.state.known.value,
            order.size,
            order.unfilled_size,
            order.cancellation_reason,
        )
    if value.kind == "NOT_FOUND":
        return RecoveryObservation("NOT_FOUND", None)
    raise ValueError("invalid recovery lookup result")


__all__ = ["RecoveryObservation", "recovery_observation_from_lookup"]
