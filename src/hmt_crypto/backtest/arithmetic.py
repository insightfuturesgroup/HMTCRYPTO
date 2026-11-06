"""Module-owned Decimal arithmetic policy for deterministic replay."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    DivisionByZero,
    FloatOperation,
    InvalidOperation,
    Overflow,
    localcontext,
)

# Four admitted 38-digit Decimal factors plus a 19-digit contract count and
# bounded aggregation still fit comfortably without inheriting caller policy.
_ARITHMETIC_CONTEXT = Context(
    prec=256,
    rounding=ROUND_HALF_EVEN,
    Emin=-999_999,
    Emax=999_999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[DivisionByZero, FloatOperation, InvalidOperation, Overflow],
)

# Average-cost allocation can be non-terminating. Keeping its deterministic
# approximation below the aggregate precision leaves enough guard digits for
# exact cancellation against residual cost and mark-to-market values.
_ALLOCATION_CONTEXT = Context(
    prec=192,
    rounding=ROUND_HALF_EVEN,
    Emin=-999_999,
    Emax=999_999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[DivisionByZero, FloatOperation, InvalidOperation, Overflow],
)


@contextmanager
def arithmetic_context() -> Iterator[None]:
    """Run Decimal operations under the immutable backtest policy."""

    with localcontext(_ARITHMETIC_CONTEXT):
        yield


@contextmanager
def allocation_context() -> Iterator[None]:
    """Approximate non-terminating allocations with aggregate guard digits."""

    with localcontext(_ALLOCATION_CONTEXT):
        yield


__all__: list[str] = []
