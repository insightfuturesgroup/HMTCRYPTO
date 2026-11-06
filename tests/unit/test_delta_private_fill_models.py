"""Fill response contracts and pure fill grammar helpers."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module", autouse=True)
def _release_later_owner_modules() -> Iterator[None]:
    """Keep this later-owner contract module neutral to earlier-owner suites."""
    yield
    import sys

    domain = sys.modules.get("hmt_crypto.domain")
    for name in (
        "hmt_crypto.domain.private_orders",
        "hmt_crypto.domain._private_order_contracts",
        "hmt_crypto.domain._private_fill_contracts",
        "hmt_crypto.domain._private_result_contracts",
        "hmt_crypto.domain.private_wallet",
        "hmt_crypto.domain._private_wallet_contracts",
        "hmt_crypto.domain.private_positions",
        "hmt_crypto.domain._private_positions_contracts",
    ):
        sys.modules.pop(name, None)
        if domain is not None:
            domain.__dict__.pop(name.rsplit(".", 1)[1], None)


def _orders() -> object:
    from hmt_crypto.domain import private_orders

    return private_orders


def _observed(model: object, raw: str, member: object | None) -> object:
    return model(raw=raw, known=member)


def _product() -> dict[str, object]:
    orders = _orders()
    asset = {"symbol": "USDT", "precision": 8, "minimum_precision": 2}
    return {
        "id": 27,
        "symbol": "BTCUSD",
        "contract_type": _observed(
            orders.ObservedFillContractType,
            "perpetual_futures",
            orders.FillContractType.PERPETUAL_FUTURES,
        ),
        "tick_size": Decimal("0.5"),
        "contract_value": Decimal("1"),
        "contract_unit_currency": "BTC",
        "notional_type": _observed(
            orders.ObservedFillNotionalType,
            "vanilla",
            orders.FillNotionalType.VANILLA,
        ),
        "quoting_asset": asset,
        "underlying_asset": {"symbol": "BTC", "precision": 8, "minimum_precision": 1},
        "settling_asset": asset,
        "spot_index": {"symbol": "BTCUSD"},
    }


def _payload(**changes: object) -> dict[str, object]:
    orders = _orders()
    values: dict[str, object] = {
        "id": UUID("071b5513-6557-4d0a-ad99-557931137dbd"),
        "size": Decimal("0"),
        "notional": Decimal("1.5"),
        "product": _product(),
        "fill_type": _observed(orders.ObservedFillType, "normal", orders.FillType.NORMAL),
        "side": _observed(orders.ObservedOrderSide, "sell", orders.OrderSide.SELL),
        "price": Decimal("0"),
        "role": _observed(orders.ObservedFillRole, "maker", orders.FillRole.MAKER),
        "commission": Decimal("-0.25"),
        "created_at": datetime(2026, 11, 5, 12, 0, tzinfo=UTC),
        "product_id": 27,
        "product_symbol": "BTCUSD",
        "order_id": {"kind": "NUMERIC", "raw": "101", "order_id": 101},
        "settling_asset_id": 5,
        "settling_asset_symbol": "USDT",
        "meta_data": {"kind": "NULL"},
    }
    values.update(changes)
    return values


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("071b551365574d0aad99557931137dbd", "071b551365574d0aad99557931137dbd"),
        ("00000000000000000000000000000000", "00000000000000000000000000000000"),
    ),
)
def test_compact_fill_uuid_accepts_only_exact_lowercase_32_hex(raw: str, expected: str) -> None:
    """Catch compact fill IDs widening to another UUID spelling."""
    assert _orders().parse_compact_fill_uuid(raw).hex == expected


@pytest.mark.parametrize(
    "value",
    (
        "071B551365574D0AAD99557931137DBD",
        "071b5513-6557-4d0a-ad99-557931137dbd",
        "{071b551365574d0aad99557931137dbd}",
        " 071b551365574d0aad99557931137dbd",
        "071b551365574d0aad99557931137db",
        "071b551365574d0aad99557931137dbdd",
        "z71b551365574d0aad99557931137dbd",
        1,
        True,
    ),
)
def test_compact_fill_uuid_rejects_every_unreviewed_spelling(value: object) -> None:
    """Catch UUID constructors silently normalizing an unreviewed input."""
    with pytest.raises(ValueError, match="^invalid compact fill UUID$") as captured:
        _orders().parse_compact_fill_uuid(value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    (("1", 1), ("9223372036854775807", 9223372036854775807)),
)
def test_numeric_fill_order_reference_has_one_canonical_64_bit_grammar(
    raw: str, expected: int
) -> None:
    """Catch accepted numeric references losing their exact raw spelling."""
    value = _orders().parse_numeric_fill_order_ref(raw)
    assert value.kind == "NUMERIC"
    assert value.raw == raw
    assert value.order_id == expected


@pytest.mark.parametrize(
    "value",
    (
        "0",
        "01",
        "+1",
        "-1",
        " 1",
        "1 ",
        "9223372036854775808",
        1,
        True,
        "071b551365574d0aad99557931137dbd",
    ),
)
def test_numeric_fill_order_reference_rejects_noncanonical_values(value: object) -> None:
    """Catch integer, overflow, UUID, sign, whitespace, or leading-zero widening."""
    with pytest.raises(ValueError, match="^invalid numeric fill order reference$"):
        _orders().parse_numeric_fill_order_ref(value)


def test_fill_enum_vocabulary_and_product_summary_are_exact() -> None:
    """Catch fill/product enum drift or catalogue-like permissive product data."""
    orders = _orders()
    assert tuple(member.value for member in orders.FillType) == (
        "normal",
        "adl",
        "liquidation",
        "settlement",
        "otc",
    )
    assert tuple(member.value for member in orders.FillRole) == ("maker", "taker")
    assert tuple(member.value for member in orders.FillContractType) == (
        "futures",
        "perpetual_futures",
        "call_options",
        "put_options",
    )
    assert tuple(member.value for member in orders.FillNotionalType) == (
        "vanilla",
        "inverse",
    )
    product = orders.FillProductSummary(**_product())
    assert product.id == 27
    with pytest.raises(ValidationError):
        orders.FillProductSummary(**(_product() | {"tick_size": Decimal("0")}))


def test_fill_enforces_identity_nonnegative_values_and_signed_commission() -> None:
    """Catch contradictory nested identity or negative size/notional/price."""
    orders = _orders()
    fill = orders.Fill(**_payload())
    assert fill.commission == Decimal("-0.25")
    assert fill.size == fill.price == Decimal("0")
    assert fill.model_copy() == fill
    for change in (
        {"product_id": 28},
        {"product_symbol": "ETHUSD"},
        {"settling_asset_symbol": "USDC"},
        {"size": Decimal("-0.1")},
        {"notional": Decimal("-0.1")},
        {"price": Decimal("-0.1")},
    ):
        with pytest.raises(ValidationError):
            orders.Fill(**_payload(**change))


def test_fill_order_reference_is_exactly_tagged_and_numeric_raw_matches() -> None:
    """Catch union-discriminator coercion or raw/numeric disagreement."""
    orders = _orders()
    adapter = TypeAdapter(orders.FillOrderRef)
    numeric = adapter.validate_python({"kind": "NUMERIC", "raw": "1", "order_id": 1})
    assert type(numeric) is orders.NumericFillOrderRef
    for invalid in (
        {"kind": "NUMERIC", "raw": "2", "order_id": 1},
        {"kind": "numeric", "raw": "1", "order_id": 1},
        {"raw": "1", "order_id": 1},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_settlement_and_metadata_value_branches_are_closed_to_reviewed_profiles() -> None:
    """Catch invented settlement UUID or metadata profiles opening the branches."""
    orders = _orders()
    assert tuple(orders.SettlementFillOrderProfile) == (
        orders.SettlementFillOrderProfile.COMPACT_UUID_V1,
    )
    assert tuple(orders.FillMetadataProfile) == (
        orders.FillMetadataProfile.NORMAL_V1,
        orders.FillMetadataProfile.ADL_V1,
        orders.FillMetadataProfile.LIQUIDATION_V1,
        orders.FillMetadataProfile.SETTLEMENT_V1,
        orders.FillMetadataProfile.OTC_V1,
    )
    settlement = orders.SettlementFillOrderRef(
        kind="SETTLEMENT",
        profile=orders.SettlementFillOrderProfile.COMPACT_UUID_V1,
        order_uuid=UUID("071b5513-6557-4d0a-ad99-557931137dbd"),
    )
    assert settlement.profile is orders.SettlementFillOrderProfile.COMPACT_UUID_V1
    with pytest.raises(ValidationError):
        orders.FillMetadataValue(profile="UNAVAILABLE")
    presence = TypeAdapter(orders.FillMetadataPresence)
    assert presence.validate_python({"kind": "MISSING"}).kind == "MISSING"
    assert presence.validate_python({"kind": "NULL"}).kind == "NULL"
    with pytest.raises(ValidationError):
        presence.validate_python({"kind": "VALUE", "value": {"profile": "UNAVAILABLE"}})


def test_known_settlement_fill_cannot_fall_back_to_numeric_reference() -> None:
    """Catch a settlement fill being accepted under ordinary numeric order grammar."""
    orders = _orders()
    settlement = orders.ObservedFillType(raw="settlement", known=orders.FillType.SETTLEMENT)
    with pytest.raises(ValidationError):
        orders.Fill(**_payload(fill_type=settlement))
    unknown = orders.ObservedFillType(raw="future_fill", known=None)
    assert orders.Fill(**_payload(fill_type=unknown)).fill_type.known is None


@pytest.mark.parametrize(
    ("model_name", "construct_fails"),
    (
        ("Fill", False),
        ("FillProductSummary", False),
        ("NumericFillOrderRef", False),
        ("FillMetadataValue", False),
        ("ObservedFillType", True),
    ),
)
def test_fill_models_inherit_closed_boundary_policy_and_reject_construct(
    model_name: str, construct_fails: bool
) -> None:
    """Catch one concrete fill model weakening A1d or construct admission."""
    model = getattr(_orders(), model_name)
    for key, expected in {
        "extra": "forbid",
        "frozen": True,
        "hide_input_in_errors": True,
        "revalidate_instances": "always",
        "strict": True,
        "validate_default": True,
    }.items():
        assert model.model_config[key] == expected
    if construct_fails:
        with pytest.raises(ValidationError):
            model.model_construct()
    else:
        forged = model.model_construct()
        with pytest.raises(ValidationError):
            forged.model_dump()


@pytest.mark.parametrize(
    ("helper_name", "message"),
    (
        ("parse_compact_fill_uuid", "invalid compact fill UUID"),
        ("parse_numeric_fill_order_ref", "invalid numeric fill order reference"),
    ),
)
def test_fill_id_helpers_reject_string_subclasses_without_callbacks_or_retention(
    helper_name: str, message: str
) -> None:
    """Catch grammar helpers normalizing or retaining hostile caller strings."""
    calls: list[str] = []

    class Hostile(str):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("compared")

        def __hash__(self) -> int:
            calls.append("hash")
            raise AssertionError("hashed")

        def __str__(self) -> str:
            calls.append("str")
            raise AssertionError("rendered")

    value = Hostile("071b551365574d0aad99557931137dbd")
    with pytest.raises(ValueError, match=f"^{message}$") as captured:
        getattr(_orders(), helper_name)(value)
    error = captured.value
    assert error.__cause__ is error.__context__ is None
    assert getattr(error, "__notes__", None) in (None, [])
    traceback = error.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_name == helper_name:
            assert all(local is not value for local in traceback.tb_frame.f_locals.values())
        traceback = traceback.tb_next
    assert calls == []


def test_fill_tagged_union_rejects_subclasses_and_corrupted_exact_branch() -> None:
    """Catch mapping/model subclasses or corrupt numeric state bypassing exact tags."""
    orders = _orders()
    adapter = TypeAdapter(orders.FillOrderRef)

    class Root(dict[str, object]):
        pass

    class NumericSubclass(orders.NumericFillOrderRef):
        pass

    valid = {"kind": "NUMERIC", "raw": "1", "order_id": 1}
    for invalid in (Root(valid), NumericSubclass(**valid)):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
    numeric = orders.NumericFillOrderRef(**valid)
    object.__getattribute__(numeric, "__dict__")["raw"] = "2"
    with pytest.raises(ValidationError):
        adapter.validate_python(numeric)


def test_fill_requires_every_field_exact_utc_and_nested_model_identity() -> None:
    """Catch response omission, non-UTC time, or structural product substitution."""
    orders = _orders()
    payload = _payload()
    for field in tuple(payload):
        missing = dict(payload)
        missing.pop(field)
        with pytest.raises(ValidationError):
            orders.Fill.model_validate(missing)

    class ProductSubclass(orders.FillProductSummary):
        pass

    with pytest.raises(ValidationError):
        orders.Fill(**_payload(product=ProductSubclass(**_product())))
    with pytest.raises(ValidationError):
        orders.Fill(**_payload(created_at=datetime(2026, 11, 5, 12, 0)))


@pytest.mark.parametrize("commission", (Decimal("-1"), Decimal("0"), Decimal("1")))
@pytest.mark.parametrize("token", ("normal", "adl", "liquidation", "otc", "future_fill"))
def test_nonsettlement_fill_types_use_numeric_refs_and_commission_stays_signed(
    commission: Decimal, token: str
) -> None:
    """Catch inferred commission signs or non-settlement order-reference widening."""
    orders = _orders()
    known = None if token == "future_fill" else orders.FillType(token)
    fill_type = orders.ObservedFillType(raw=token, known=known)
    value = orders.Fill(**_payload(fill_type=fill_type, commission=commission))
    assert type(value.order_id) is orders.NumericFillOrderRef
    assert value.commission == commission
