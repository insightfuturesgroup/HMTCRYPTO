"""Wallet-domain contracts for reviewed Delta private responses."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from delta_private_model_test_support import (
    account_api,
    reachable_exceptions,
    valid_account,
)
from pydantic import TypeAdapter, ValidationError

pytestmark = pytest.mark.unit


def _wallet() -> object:
    from hmt_crypto.domain import private_wallet

    return private_wallet


def _balance(wallet: object) -> object:
    values = {
        "asset_id": 11,
        "id": 12,
        "user_id": "12345",
        "asset_symbol": "USDT",
        "available_balance": Decimal("100.25"),
        "available_balance_for_robo": Decimal("10"),
        "balance": Decimal("120.25"),
        "blocked_margin": Decimal("1"),
        "commission": Decimal("-0.5"),
        "cross_asset_liability": Decimal("-2"),
        "cross_commission": Decimal("0.1"),
        "cross_locked_collateral": Decimal("3"),
        "cross_order_margin": Decimal("4"),
        "cross_position_margin": Decimal("5"),
        "interest_credit": Decimal("0.25"),
        "order_margin": Decimal("6"),
        "pending_referral_bonus": Decimal("0"),
        "pending_trading_fee_credit": Decimal("0.75"),
        "portfolio_margin": Decimal("7"),
        "position_margin": Decimal("8"),
        "trading_fee_credit": Decimal("1.25"),
        "unvested_amount": Decimal("9"),
    }
    return wallet.Balance(**values)


def _context() -> object:
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    api = account_api()
    observed_at = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    return api.ObservationContext(
        account=valid_account(api),
        endpoint_id=PrivateRouteId.BALANCES,
        correlation_id=UUID("00000000-0000-0000-0000-000000000101"),
        requested_at=observed_at,
        received_at=observed_at,
        source="delta_rest",
    )


def test_wallet_transaction_vocabulary_and_observation_are_exact() -> None:
    """Catch missing, aliased, or coercively parsed official transaction tokens."""
    wallet = _wallet()
    expected = (
        "cashflow",
        "deposit",
        "withdrawal",
        "commission",
        "conversion",
        "funding",
        "settlement",
        "liquidation_fee",
        "spot_trade",
        "withdrawal_cancellation",
        "referral_bonus",
        "sub_account_transfer",
        "commission_rebate",
        "promo_credit",
        "trading_credits",
        "trading_credits_forfeited",
        "trading_credits_paid",
        "trading_fee_credits_paid_liquidation_fee",
        "trading_credits_reverted",
        "interest_credit",
        "external_deposit",
        "credit_line",
        "trading_competition",
        "fund_deposit",
        "fund_withdrawal",
        "fund_wallet_deposit",
        "fund_wallet_withdrawal",
        "fund_reward",
        "trade_farming_reward",
        "revert",
        "raf_bonus",
        "fill_appropriation",
        "incident_compensation",
    )
    assert tuple(member.value for member in wallet.WalletTransactionType) == expected
    assert len(wallet.WalletTransactionType.__members__) == 33
    known = wallet.ObservedWalletTransactionType(
        raw="deposit", known=wallet.WalletTransactionType.DEPOSIT
    )
    unknown = wallet.ObservedWalletTransactionType(raw="future_credit", known=None)
    assert known.known is wallet.WalletTransactionType.DEPOSIT
    assert unknown.known is None
    for raw, member in (("deposit", None), ("future_credit", wallet.WalletTransactionType.DEPOSIT)):
        with pytest.raises(ValidationError):
            wallet.ObservedWalletTransactionType(raw=raw, known=member)
    with pytest.raises(ValidationError):
        TypeAdapter(wallet.WalletTransactionType).validate_python("deposit")


def test_wallet_metadata_preserves_presence_and_only_reviewed_empty_profile() -> None:
    """Catch metadata presence collapse or an unreviewed profile crossing the boundary."""
    wallet = _wallet()
    api = account_api()
    adapter = TypeAdapter(wallet.WalletTransactionMetadataPresence)
    missing = adapter.validate_python({"kind": "MISSING"})
    null = adapter.validate_python({"kind": "NULL"})
    assert type(missing) is api.MissingField
    assert type(null) is api.NullField
    assert missing.model_dump() == {"kind": "MISSING"}
    assert null.model_dump() == {"kind": "NULL"}
    value = adapter.validate_python(
        {
            "kind": "VALUE",
            "value": {"profile": wallet.WalletTransactionMetadataProfile.EMPTY_OBJECT},
        }
    )
    assert value.value.profile is wallet.WalletTransactionMetadataProfile.EMPTY_OBJECT
    for invalid in (
        {"kind": "VALUE", "value": {"profile": "UNAVAILABLE"}},
        {"kind": "VALUE", "value": None},
        {"kind": "MISSING", "value": {}},
        {},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
    assert tuple(wallet.WalletTransactionMetadataProfile) == (
        wallet.WalletTransactionMetadataProfile.EMPTY_OBJECT,
    )
    with pytest.raises(ValidationError):
        wallet.WalletTransactionMetadataValue.model_validate({"profile": "anything"})


def test_balance_snapshot_owns_exact_signed_decimal_rows() -> None:
    """Catch tuple normalization, numeric coercion, or inferred wallet equations."""
    wallet = _wallet()
    balance = _balance(wallet)
    snapshot = wallet.BalanceSnapshot(
        balances=(balance,),
        net_equity=Decimal("-1.5"),
        robo_trading_equity=Decimal("0"),
    )
    assert type(snapshot.balances) is tuple
    assert snapshot.balances[0] is not balance
    assert snapshot.net_equity == Decimal("-1.5")
    observed = wallet.ObservedBalanceSnapshot(context=_context(), value=snapshot)
    assert observed.value == snapshot
    assert observed.model_copy() == observed
    assert wallet.Observed[wallet.BalanceSnapshot] is wallet.ObservedBalanceSnapshot

    class TupleSubclass(tuple[object, ...]):
        pass

    for invalid in ([balance], TupleSubclass((balance,))):
        with pytest.raises(ValidationError) as captured:
            wallet.BalanceSnapshot(
                balances=invalid,
                net_equity=Decimal("1"),
                robo_trading_equity=Decimal("1"),
            )
        assert all(detail.get("input") is None for detail in captured.value.errors())
    with pytest.raises(ValidationError):
        wallet.Balance(**(_balance(wallet).model_dump() | {"available_balance": "100.25"}))


@pytest.mark.parametrize("operation", ["copy", "python", "json"])
def test_corrupt_balance_collection_cannot_cross_owned_boundaries(operation: str) -> None:
    """Catch corrupted collection state reaching copy or serialization callbacks."""
    wallet = _wallet()
    snapshot = wallet.BalanceSnapshot(
        balances=(_balance(wallet),),
        net_equity=Decimal("1"),
        robo_trading_equity=Decimal("1"),
    )
    object.__getattribute__(snapshot, "__dict__")["balances"] = []
    actions = {
        "copy": lambda: snapshot.model_copy(),
        "python": lambda: snapshot.model_dump(),
        "json": lambda: snapshot.model_dump_json(),
    }
    with pytest.raises(ValidationError):
        actions[operation]()


def test_wallet_transaction_requires_explicit_nullable_and_presence_fields() -> None:
    """Catch an omitted product ID or metadata field being silently defaulted."""
    wallet = _wallet()
    transaction = wallet.WalletTransaction(
        id=21,
        amount=Decimal("-2.5"),
        balance=Decimal("100"),
        transaction_type=wallet.ObservedWalletTransactionType(
            raw="funding", known=wallet.WalletTransactionType.FUNDING
        ),
        meta_data={"kind": "NULL"},
        product_id=None,
        asset_id=11,
        asset_symbol="USDT",
        created_at=datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
    )
    assert transaction.product_id is None
    assert transaction.meta_data.kind == "NULL"
    payload = transaction.model_dump()
    payload.pop("product_id")
    with pytest.raises(ValidationError):
        wallet.WalletTransaction.model_validate(payload)
    payload = transaction.model_dump()
    payload.pop("meta_data")
    with pytest.raises(ValidationError):
        wallet.WalletTransaction.model_validate(payload)


def test_wallet_generic_table_has_exact_canonical_identity() -> None:
    """Catch duplicate candidates or reordered owner rows."""
    wallet = _wallet()
    api = account_api()
    rows = wallet._WALLET_REVIEWED_GENERIC_SPECIALIZATIONS
    assert tuple((row.family, row.payload, row.specialization) for row in rows) == (
        (api.ObservedEnum, wallet.WalletTransactionType, wallet.ObservedWalletTransactionType),
        (
            api.ValueField,
            wallet.WalletTransactionMetadataValue,
            wallet.WalletTransactionMetadataValueField,
        ),
        (
            api.FieldPresence,
            wallet.WalletTransactionMetadataValue,
            wallet.WalletTransactionMetadataPresence,
        ),
        (api.Observed, wallet.BalanceSnapshot, wallet.ObservedBalanceSnapshot),
    )
    assert api.ObservedEnum[wallet.WalletTransactionType] is wallet.ObservedWalletTransactionType
    assert (
        api.ValueField[wallet.WalletTransactionMetadataValue]
        is wallet.WalletTransactionMetadataValueField
    )
    assert (
        api.FieldPresence[wallet.WalletTransactionMetadataValue]
        is wallet.WalletTransactionMetadataPresence
    )


@pytest.mark.parametrize(
    "field",
    (
        "available_balance",
        "available_balance_for_robo",
        "balance",
        "blocked_margin",
        "commission",
        "cross_asset_liability",
        "cross_commission",
        "cross_locked_collateral",
        "cross_order_margin",
        "cross_position_margin",
        "interest_credit",
        "order_margin",
        "pending_referral_bonus",
        "pending_trading_fee_credit",
        "portfolio_margin",
        "position_margin",
        "trading_fee_credit",
        "unvested_amount",
    ),
)
@pytest.mark.parametrize("value", [Decimal("-1"), Decimal("0"), Decimal("1")])
def test_every_balance_component_remains_signed(field: str, value: Decimal) -> None:
    """Catch an inferred sign rule being added to any wallet component."""
    wallet = _wallet()
    payload = _balance(wallet).model_dump()
    payload[field] = value
    assert wallet.Balance.model_validate(payload).model_dump()[field] == value


def test_wallet_boundary_rejects_hostile_root_key_scalar_and_model_subclass() -> None:
    """Catch caller-owned callbacks or subclasses crossing the wallet boundary."""
    wallet = _wallet()
    calls: list[str] = []

    class HostileKey(str):
        def __hash__(self) -> int:
            calls.append("hash")
            return str.__hash__(self)

        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("equality called")

    class Root(dict[object, object]):
        pass

    class BalanceSubclass(wallet.Balance):
        pass

    with pytest.raises(ValidationError):
        wallet.Balance.model_validate(Root(_balance(wallet).model_dump()))
    hostile_mapping = {HostileKey("asset_id"): 11}
    calls.clear()
    with pytest.raises(ValidationError):
        wallet.Balance.model_validate(hostile_mapping)
    with pytest.raises(ValidationError):
        wallet.BalanceSnapshot(
            balances=(BalanceSubclass(**_balance(wallet).model_dump()),),
            net_equity=Decimal("1"),
            robo_trading_equity=Decimal("1"),
        )
    assert calls == []

    class Canary:
        def __repr__(self) -> str:
            return "wallet-secret-canary"

    canary = Canary()
    payload = _balance(wallet).model_dump() | {"asset_symbol": canary}
    try:
        wallet.Balance.model_validate(payload)
    except ValidationError as error:
        for linked in reachable_exceptions(error):
            assert "wallet-secret-canary" not in repr(linked.args)
            if isinstance(linked, ValidationError):
                assert all(detail.get("input") is None for detail in linked.errors())
    else:
        raise AssertionError("hostile scalar accepted")
