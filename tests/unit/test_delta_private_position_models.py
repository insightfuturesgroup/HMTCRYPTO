"""Position-domain contracts for reviewed Delta private responses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from delta_private_model_test_support import (
    account_api,
    reachable_exceptions,
    valid_account,
)
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def _positions() -> object:
    from hmt_crypto.domain import private_positions

    return private_positions


def _context() -> object:
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    api = account_api()
    observed_at = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    return api.ObservationContext(
        account=valid_account(api),
        endpoint_id=PrivateRouteId.MARGINED_POSITIONS,
        correlation_id=UUID("00000000-0000-0000-0000-000000000201"),
        requested_at=observed_at,
        received_at=observed_at,
        source="delta_rest",
    )


def _margined(positions: object, **changes: object) -> object:
    created = datetime(2026, 11, 5, 11, 0, tzinfo=UTC)
    values = {
        "user_id": "12345",
        "size": -3,
        "entry_price": Decimal("100.5"),
        "margin": Decimal("50"),
        "liquidation_price": Decimal("80"),
        "bankruptcy_price": None,
        "adl_level": 2,
        "product_id": 101,
        "product_symbol": "BTCUSDT",
        "product": {"id": 101, "symbol": "BTCUSDT"},
        "commission": Decimal("-0.25"),
        "realized_pnl": Decimal("1.5"),
        "realized_funding": Decimal("-0.1"),
        "realized_cashflow": Decimal("0"),
        "realized_holding_cost": Decimal("0.05"),
        "unrealized_pnl": Decimal("-2"),
        "unrealized_cashflow": Decimal("0"),
        "mark_price": Decimal("99"),
        "margin_mode": positions.ObservedMarginMode(
            raw="portfolio", known=positions.MarginMode.PORTFOLIO
        ),
        "auto_topup": False,
        "created_at": created,
        "updated_at": created + timedelta(seconds=1),
        "potentially_delayed": True,
    }
    return positions.MarginedPosition(**(values | changes))


def test_margin_mode_preserves_known_and_unknown_response_tokens() -> None:
    """Catch coercion or loss of a future margin-mode token."""
    positions = _positions()
    assert tuple(member.value for member in positions.MarginMode) == (
        "isolated",
        "portfolio",
        "cross",
    )
    known = positions.ObservedMarginMode(raw="isolated", known=positions.MarginMode.ISOLATED)
    unknown = positions.ObservedMarginMode(raw="future_margin", known=None)
    assert known.known is positions.MarginMode.ISOLATED
    assert unknown.known is None
    with pytest.raises(ValidationError):
        positions.ObservedMarginMode(raw="isolated", known=None)
    with pytest.raises(ValidationError):
        positions.ObservedMarginMode(raw="future_margin", known=positions.MarginMode.CROSS)


def test_latest_position_keeps_signed_contracts_and_nonnegative_entry() -> None:
    """Catch an absent position becoming flat or a negative entry price being accepted."""
    positions = _positions()
    value = positions.LatestPosition(product_id=101, signed_contracts=-3, entry_price=Decimal("0"))
    assert value.signed_contracts == -3
    assert value.entry_price == Decimal("0")
    with pytest.raises(ValidationError):
        positions.LatestPosition(product_id=101, signed_contracts=-3, entry_price=Decimal("-0.1"))
    with pytest.raises(ValidationError):
        positions.LatestPosition(product_id=101, signed_contracts="-3", entry_price=Decimal("1"))


def test_margined_position_enforces_nested_identity_time_and_exact_flags() -> None:
    """Catch contradictory product identity, reversed time, or delayed-state coercion."""
    positions = _positions()
    value = _margined(positions)
    assert value.product.id == value.product_id
    assert value.product.symbol == value.product_symbol
    assert value.potentially_delayed is True
    assert value.model_copy() == value
    for change in (
        {"product": {"id": 102, "symbol": "BTCUSDT"}},
        {"product": {"id": 101, "symbol": "ETHUSDT"}},
        {"updated_at": value.created_at - timedelta(microseconds=1)},
        {"potentially_delayed": False},
        {"auto_topup": 0},
        {"margin": Decimal("-0.1")},
        {"mark_price": Decimal("-0.1")},
        {"adl_level": -1},
    ):
        with pytest.raises(ValidationError) as captured:
            _margined(positions, **change)
        assert all(detail.get("input") is None for detail in captured.value.errors())


def test_observed_margined_batch_requires_exact_tuple_before_normalization() -> None:
    """Catch a tuple subclass or mutable list being normalized into trusted state."""
    positions = _positions()
    position = _margined(positions)
    observed = positions.ObservedMarginedPositions(context=_context(), value=(position,))
    assert type(observed.value) is tuple
    assert observed.value[0] is not position
    assert (
        positions.Observed[positions.MarginedPositionBatch] is positions.ObservedMarginedPositions
    )

    class HostileTuple(tuple[object, ...]):
        calls = 0

        def __iter__(self):  # type: ignore[no-untyped-def]
            type(self).calls += 1
            raise AssertionError("iterated")

    for invalid in ([position], HostileTuple((position,))):
        with pytest.raises(ValidationError):
            positions.ObservedMarginedPositions(context=_context(), value=invalid)
    assert HostileTuple.calls == 0


@pytest.mark.parametrize("operation", ["copy", "python", "json"])
def test_corrupt_margined_batch_cannot_cross_owned_boundaries(operation: str) -> None:
    """Catch corrupted tuple state reaching copy or serialization."""
    positions = _positions()
    observed = positions.ObservedMarginedPositions(
        context=_context(), value=(_margined(positions),)
    )
    object.__getattribute__(observed, "__dict__")["value"] = []
    actions = {
        "copy": lambda: observed.model_copy(),
        "python": lambda: observed.model_dump(),
        "json": lambda: observed.model_dump_json(),
    }
    with pytest.raises(ValidationError):
        actions[operation]()


def test_positions_generic_table_has_exact_canonical_identity() -> None:
    """Catch reconstructed batch aliases or duplicate specialization candidates."""
    positions = _positions()
    api = account_api()
    rows = positions._POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS
    assert tuple((row.family, row.payload, row.specialization) for row in rows) == (
        (api.ObservedEnum, positions.MarginMode, positions.ObservedMarginMode),
        (api.Observed, positions.MarginedPositionBatch, positions.ObservedMarginedPositions),
    )
    reconstructed = tuple[positions.MarginedPosition, ...]
    assert reconstructed == positions.MarginedPositionBatch
    assert reconstructed is not positions.MarginedPositionBatch
    assert api.Observed[reconstructed] is api.module._RejectedPrivateGenericSpecialization


def test_every_margined_position_field_is_required() -> None:
    """Catch a rich position field gaining an implicit default."""
    positions = _positions()
    payload = _margined(positions).model_dump()
    for field in tuple(payload):
        missing = dict(payload)
        missing.pop(field)
        with pytest.raises(ValidationError):
            positions.MarginedPosition.model_validate(missing)


def test_position_boundary_rejects_root_and_nested_model_subclasses() -> None:
    """Catch structural model substitution for trusted position identity."""
    positions = _positions()

    class Root(dict[str, object]):
        pass

    class ProductSubclass(positions.PositionProductIdentity):
        pass

    with pytest.raises(ValidationError):
        positions.MarginedPosition.model_validate(Root(_margined(positions).model_dump()))
    product = ProductSubclass(id=101, symbol="BTCUSDT")
    with pytest.raises(ValidationError) as captured:
        _margined(positions, product=product)
    assert all(detail.get("input") is None for detail in captured.value.errors())


@pytest.mark.parametrize("owner", ["wallet", "positions"])
@pytest.mark.parametrize("entrypoint", ["constructor", "model_validate"])
def test_observed_enum_mismatch_is_input_free(owner: str, entrypoint: str) -> None:
    """Catch an invalid known/unknown pair retaining the raw private token."""
    if owner == "wallet":
        from hmt_crypto.domain import private_wallet as module

        model = module.ObservedWalletTransactionType
        known = module.WalletTransactionType.DEPOSIT
        raw = "wallet_canary"
    else:
        module = _positions()
        model = module.ObservedMarginMode
        known = module.MarginMode.CROSS
        raw = "margin_canary"
    payload = {"raw": raw, "known": known}
    with pytest.raises(ValidationError) as captured:
        if entrypoint == "constructor":
            model(**payload)
        else:
            model.model_validate(payload)
    expected = (
        ("private_observed_enum_value", "invalid observed enum")
        if entrypoint == "constructor"
        else ("private_model_value", "invalid private model value")
    )
    assert captured.value.errors(include_url=False) == [
        {
            "type": expected[0],
            "loc": (),
            "msg": expected[1],
            "input": None,
        }
    ]
    for reachable in reachable_exceptions(captured.value):
        rendered = f"{reachable!s} {reachable!r} {reachable.args!r}"
        if isinstance(reachable, ValidationError):
            details = reachable.errors(include_url=False)
            rendered += repr(details)
            assert all(detail.get("input") is None for detail in details)
        assert raw not in rendered
