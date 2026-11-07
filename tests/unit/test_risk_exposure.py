"""Side-aware position and exposure calculations."""

from decimal import Decimal

import pytest

from hmt_crypto.risk.exposure import gross_exposure, strict_reduction, worst_position_contracts
from hmt_crypto.risk.models import ExposureLeg, RiskInputError, Side


@pytest.mark.parametrize(
    "position,side,contracts,expected_post",
    [
        (5, Side.SELL, 2, 3),
        (-5, Side.BUY, 2, -3),
        (5, Side.SELL, 5, 0),
        (-5, Side.BUY, 5, 0),
    ],
)
def test_strict_reduction_allows_only_smaller_same_sign_or_flat_positions(
    position: int, side: Side, contracts: int, expected_post: int
) -> None:
    assert strict_reduction(position, side, contracts) == expected_post


@pytest.mark.parametrize(
    "position,side,contracts",
    [
        (0, Side.BUY, 1),
        (5, Side.BUY, 1),
        (-5, Side.SELL, 1),
        (5, Side.SELL, 6),
        (-5, Side.BUY, 6),
    ],
)
def test_strict_reduction_rejects_missing_position_increase_and_reversal(
    position: int, side: Side, contracts: int
) -> None:
    with pytest.raises(RiskInputError):
        strict_reduction(position, side, contracts)


def test_worst_position_counts_each_one_sided_fill_extreme_without_netting() -> None:
    legs = (
        ExposureLeg(side=Side.BUY, contracts=4, per_contract_notional=Decimal("10")),
        ExposureLeg(side=Side.SELL, contracts=8, per_contract_notional=Decimal("10")),
        ExposureLeg(
            side=Side.SELL,
            contracts=99,
            per_contract_notional=Decimal("10"),
            reduce_only=True,
        ),
    )
    assert worst_position_contracts(3, legs) == 7


def test_gross_exposure_never_nets_positions_orders_or_reservations() -> None:
    positions = (
        ExposureLeg(side=Side.BUY, contracts=3, per_contract_notional=Decimal("10")),
        ExposureLeg(side=Side.SELL, contracts=2, per_contract_notional=Decimal("20")),
    )
    pending = (
        ExposureLeg(side=Side.SELL, contracts=4, per_contract_notional=Decimal("5")),
        ExposureLeg(
            side=Side.BUY,
            contracts=100,
            per_contract_notional=Decimal("5"),
            reduce_only=True,
        ),
    )
    assert gross_exposure(positions, pending) == Decimal("90")


def test_exposure_helpers_reject_reconstructed_side_members_before_hash_or_equality() -> None:
    forged = str.__new__(Side, "buy")
    with pytest.raises(RiskInputError):
        strict_reduction(-2, forged, 1)
