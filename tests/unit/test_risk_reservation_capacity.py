"""Pure capacity selection uses exact Decimal and half-open rate buckets."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

import pytest

from hmt_crypto.persistence.errors import PersistenceValueError


def capacity():
    import hmt_crypto.persistence.risk_reservation_capacity as module

    return module


START = datetime(2026, 11, 4, 12, tzinfo=UTC)
END = START + timedelta(minutes=5)
ACCOUNT_ID = UUID("20000000-0000-4000-8000-000000000002")


def plan(**changes):
    values = dict(
        account_id=ACCOUNT_ID,
        region="india",
        environment="testnet",
        mode="PAPER",
        product_id=1101,
        contracts=2,
        incremental_gross_exposure=Decimal("100"),
        worst_case_loss=Decimal("10"),
        collateral=Decimal("5"),
        open_order_slots=1,
        pending_rate_units=1,
        rate_policy_version=1,
        rate_policy_sha256="a" * 64,
        rate_window_start_at=START,
        rate_window_end_at=END,
    )
    values.update(changes)
    return values


def reservation(status="PENDING_DISPATCH", **changes):
    values = dict(
        account_id=ACCOUNT_ID,
        region="india",
        environment="testnet",
        mode="PAPER",
        product_id=1101,
        status=status,
        remaining_contracts=3,
        remaining_incremental_gross_exposure=Decimal("30"),
        remaining_worst_case_loss=Decimal("3"),
        remaining_collateral=Decimal("2"),
        remaining_open_order_slots=1,
        pending_rate_units=1 if status == "PENDING_DISPATCH" else 0,
        rate_policy_version=1,
        rate_policy_sha256="a" * 64,
        rate_window_start_at=START,
        rate_window_end_at=END,
    )
    values.update(changes)
    return values


def consumption(**changes):
    values = dict(
        account_id=ACCOUNT_ID,
        region="india",
        environment="testnet",
        mode="PAPER",
        product_id=1101,
        consumed_units=1,
        rate_policy_version=1,
        rate_policy_sha256="a" * 64,
        rate_window_start_at=START,
        rate_window_end_at=END,
    )
    values.update(changes)
    return values


def test_capacity_counts_pending_and_committed_non_rate_and_excludes_released():
    totals = capacity().calculate_capacity(
        plan(),
        [
            reservation(),
            reservation("DISPATCH_COMMITTED", remaining_contracts=4),
            reservation(
                "RELEASED",
                remaining_contracts=0,
                remaining_incremental_gross_exposure=Decimal("0"),
                remaining_worst_case_loss=Decimal("0"),
                remaining_collateral=Decimal("0"),
                remaining_open_order_slots=0,
            ),
        ],
        [],
    )
    assert totals.contracts == 9
    assert totals.incremental_gross_exposure == Decimal("160")
    assert totals.worst_case_loss == Decimal("16")
    assert totals.collateral == Decimal("9")
    assert totals.open_order_slots == 3
    assert totals.rate_units == 2


def test_pending_to_consumption_transfer_is_rate_neutral():
    before = capacity().calculate_capacity(plan(), [reservation()], [])
    after = capacity().calculate_capacity(
        plan(), [reservation("DISPATCH_COMMITTED")], [consumption()]
    )
    assert before.rate_units == after.rate_units == 2
    assert before.contracts == after.contracts


@pytest.mark.parametrize(
    "change",
    [
        {"rate_policy_version": 2},
        {"rate_policy_sha256": "b" * 64},
        {"rate_window_start_at": START + timedelta(seconds=1)},
        {"rate_window_end_at": END - timedelta(seconds=1)},
    ],
)
def test_nonzero_overlapping_incompatible_bucket_fails_closed(change):
    with pytest.raises(PersistenceValueError):
        capacity().calculate_capacity(plan(), [reservation(**change)], [])
    with pytest.raises(PersistenceValueError):
        capacity().calculate_capacity(plan(), [], [consumption(**change)])


def test_touching_buckets_and_zero_contributors_do_not_conflict():
    touching = reservation(rate_window_start_at=END, rate_window_end_at=END + timedelta(minutes=5))
    zero = reservation("DISPATCH_COMMITTED", rate_policy_version=2, pending_rate_units=0)
    assert capacity().calculate_capacity(plan(), [touching, zero], []).rate_units == 1


@pytest.mark.parametrize(
    "invalid",
    [
        plan(pending_rate_units=2),
        reservation("PENDING_DISPATCH", pending_rate_units=0),
        reservation("DISPATCH_COMMITTED", pending_rate_units=1),
        reservation("RELEASED", pending_rate_units=1),
    ],
)
def test_capacity_rejects_impossible_pending_rate_unit_shapes(invalid):
    if "contracts" in invalid:
        with pytest.raises(PersistenceValueError):
            capacity().calculate_capacity(invalid, [], [])
    else:
        with pytest.raises(PersistenceValueError):
            capacity().calculate_capacity(plan(), [invalid], [])


@pytest.mark.parametrize(
    "invalid",
    [True, 1.5, "1", Decimal("NaN"), Decimal("1e-19"), Decimal("1e20")],
)
def test_capacity_rejects_coercion_and_loss(invalid):
    with pytest.raises(PersistenceValueError):
        capacity().calculate_capacity(plan(incremental_gross_exposure=invalid), [], [])


def test_capacity_strictly_revalidates_unsafe_exact_model_instances():
    module = capacity()
    owned = module.CapacityPlanV1.model_validate(plan())
    unsafe = owned.model_copy(update={"contracts": "2"})
    with pytest.raises(PersistenceValueError):
        module.calculate_capacity(unsafe, [], [])


def test_capacity_sums_maximum_exact_decimals_without_rounding():
    maximum = Decimal("99999999999999999999.999999999999999999")
    expected = Decimal("199999999999999999999.999999999999999998")
    totals = capacity().calculate_capacity(
        plan(
            incremental_gross_exposure=maximum,
            worst_case_loss=maximum,
            collateral=maximum,
        ),
        [
            reservation(
                remaining_incremental_gross_exposure=maximum,
                remaining_worst_case_loss=maximum,
                remaining_collateral=maximum,
            )
        ],
        [],
    )
    assert totals.incremental_gross_exposure == expected
    assert totals.worst_case_loss == expected
    assert totals.collateral == expected


def test_capacity_decimal_sum_is_independent_of_low_ambient_context():
    with localcontext() as context:
        context.prec = 3
        totals = capacity().calculate_capacity(
            plan(incremental_gross_exposure=Decimal("1.000000000000000001")),
            [reservation(remaining_incremental_gross_exposure=Decimal("2.000000000000000002"))],
            [],
        )
    assert totals.incremental_gross_exposure == Decimal("3.000000000000000003")


def test_product_position_isolated_while_account_capacity_aggregates_cross_product():
    totals = capacity().calculate_capacity(
        plan(),
        [reservation(product_id=2202, remaining_contracts=99)],
        [],
    )
    assert totals.contracts == 2
    assert totals.incremental_gross_exposure == Decimal("130")
    assert totals.open_order_slots == 2


@pytest.mark.parametrize("source", ["reservation", "consumption"])
def test_capacity_rejects_cross_scope_snapshot_rows(source):
    row = (
        reservation(account_id=uuid4())
        if source == "reservation"
        else consumption(account_id=uuid4())
    )
    with pytest.raises(PersistenceValueError):
        capacity().calculate_capacity(
            plan(),
            [row] if source == "reservation" else [],
            [row] if source == "consumption" else [],
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("remaining_contracts", 1),
        ("remaining_incremental_gross_exposure", Decimal("1")),
        ("remaining_worst_case_loss", Decimal("1")),
        ("remaining_collateral", Decimal("1")),
        ("remaining_open_order_slots", 1),
        ("pending_rate_units", 1),
    ],
)
def test_capacity_rejects_released_rows_with_any_held_capacity(field, value):
    released = reservation(
        "RELEASED",
        remaining_contracts=0,
        remaining_incremental_gross_exposure=Decimal("0"),
        remaining_worst_case_loss=Decimal("0"),
        remaining_collateral=Decimal("0"),
        remaining_open_order_slots=0,
    )
    released[field] = value
    with pytest.raises(PersistenceValueError):
        capacity().calculate_capacity(plan(), [released], [])


def test_capacity_requires_one_unit_per_future_rate_consumption_parent():
    with pytest.raises(PersistenceValueError):
        capacity().calculate_capacity(plan(), [], [consumption(consumed_units=2)])
