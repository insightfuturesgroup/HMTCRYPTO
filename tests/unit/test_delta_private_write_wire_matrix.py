"""Exhaustive presence/order matrix for offline mutation wire candidates."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from hmt_crypto.domain.private_orders import (
    MmpLevel,
    OrderSide,
    OrderType,
    StopTriggerMethod,
    TimeInForce,
)
from hmt_crypto.errors import AmbiguousExchangeResult
from hmt_crypto.exchange import private_requests as requests
from hmt_crypto.exchange.delta import private_mutation_parsers, private_mutation_profile
from hmt_crypto.exchange.delta.private_routes import (
    PrivateRouteId,
    PrivateRouteSpec,
    get_private_route_spec,
)
from hmt_crypto.exchange.delta.private_write_wire import (
    _attest_unverified_mutation_candidate,
    _create_offline_mutation_codec_profile,
    _encode_offline_mutation_candidate,
    _OfflineMutationCodecProfile,
    _UnverifiedBooleanEncoding,
    _UnverifiedLeverageEncoding,
    _UnverifiedMutationWireCandidate,
)

pytestmark = pytest.mark.unit


def _profile() -> _OfflineMutationCodecProfile:
    return _create_offline_mutation_codec_profile(
        boolean_encoding=_UnverifiedBooleanEncoding.JSON_BOOLEAN_CANDIDATE,
        leverage_encoding=_UnverifiedLeverageEncoding.DECIMAL_STRING_CANDIDATE,
    )


def _place(**changes: object) -> requests.PlaceOrderRequest:
    values: dict[str, object] = {
        "product_id": 27,
        "size": 2,
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT_ORDER,
        "limit_price": Decimal("123.45"),
    }
    values.update(changes)
    return requests.PlaceOrderRequest(**values)


def _assert_canonical_keys(request: object) -> None:
    candidate = _encode_offline_mutation_candidate(
        request,
        profile=_profile(),  # type: ignore[arg-type]
    )
    payload = json.loads(candidate.body)
    assert type(payload) is dict
    spec = get_private_route_spec(candidate.route_id)
    expected = tuple(
        field for field in spec.body_fields if object.__getattribute__(request, field) is not None
    )
    assert tuple(payload) == expected
    for root in ("stop_loss_order", "take_profit_order"):
        if root not in payload:
            continue
        nested = payload[root]
        assert type(nested) is dict
        expected_nested = tuple(
            dotted.split(".", 1)[1]
            for dotted in spec.nested_body_fields
            if dotted.startswith(root + ".")
            and object.__getattribute__(
                object.__getattribute__(request, root), dotted.split(".", 1)[1]
            )
            is not None
        )
        assert tuple(nested) == expected_nested


@pytest.mark.parametrize(
    "mutation",
    (
        _place(time_in_force=TimeInForce.IOC),
        _place(reduce_only=False),
        _place(client_order_id="HMT_PRESENCE_1"),
        _place(
            stop_order_type=requests.RequestStopOrderType.STOP_LOSS_ORDER,
            stop_price=Decimal("120"),
            stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
        _place(
            stop_order_type=requests.RequestStopOrderType.TAKE_PROFIT_ORDER,
            trail_amount=Decimal("2"),
            stop_trigger_method=StopTriggerMethod.SPOT_PRICE,
        ),
        _place(
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            bracket_stop_loss_limit_price=Decimal("111"),
            bracket_stop_loss_price=Decimal("112"),
        ),
        _place(
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            bracket_trail_amount=Decimal("3"),
        ),
        _place(
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            bracket_take_profit_limit_price=Decimal("139"),
            bracket_take_profit_price=Decimal("140"),
        ),
        _place(time_in_force=TimeInForce.GTC, mmp=MmpLevel.MMP1),
        _place(time_in_force=TimeInForce.GTC, post_only=False),
    ),
)
def test_each_place_optional_family_preserves_exact_a4_order(mutation: object) -> None:
    _assert_canonical_keys(mutation)


@pytest.mark.parametrize(
    "field",
    ("limit_price", "mmp", "post_only", "stop_price", "trail_amount"),
)
def test_each_modify_optional_field_is_independently_present(field: str) -> None:
    values: dict[str, object] = {"id": 91, "product_id": 27, "size": 2}
    values[field] = {
        "limit_price": Decimal("124"),
        "mmp": MmpLevel.MMP2,
        "post_only": False,
        "stop_price": Decimal("120"),
        "trail_amount": Decimal("2"),
    }[field]
    request = requests.ModifyOrderRequest(**values)
    _assert_canonical_keys(request)


@pytest.mark.parametrize(
    "field",
    (
        "bracket_stop_loss_limit_price",
        "bracket_stop_loss_price",
        "bracket_take_profit_limit_price",
        "bracket_take_profit_price",
        "bracket_trail_amount",
        "bracket_stop_trigger_method",
    ),
)
def test_each_edit_bracket_optional_field_is_independently_present(field: str) -> None:
    values: dict[str, object] = {"id": 91, "product_id": 27}
    values[field] = (
        StopTriggerMethod.LAST_TRADED_PRICE
        if field == "bracket_stop_trigger_method"
        else Decimal("110")
    )
    request = requests.EditBracketRequest(**values)
    _assert_canonical_keys(request)


@pytest.mark.parametrize(
    "mutation",
    (
        requests.CreateBracketRequest(
            product_id=27,
            stop_loss_order=requests.BracketStopLossOrder(
                order_type=OrderType.LIMIT_ORDER,
                stop_price=Decimal("110"),
                limit_price=Decimal("109"),
            ),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
        requests.CreateBracketRequest(
            product_id=27,
            stop_loss_order=requests.BracketStopLossOrder(
                order_type=OrderType.MARKET_ORDER,
                trail_amount=Decimal("2"),
            ),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
        requests.CreateBracketRequest(
            product_id=27,
            take_profit_order=requests.BracketTakeProfitOrder(
                order_type=OrderType.LIMIT_ORDER,
                stop_price=Decimal("140"),
                limit_price=Decimal("139"),
            ),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
    ),
)
def test_each_nested_bracket_optional_shape_has_canonical_nested_order(
    mutation: object,
) -> None:
    _assert_canonical_keys(mutation)


def test_every_request_field_is_revalidated_before_encoding() -> None:
    cases = (
        _place(reduce_only=False, client_order_id="HMT_PRESENCE_1"),
        requests.ModifyOrderRequest(id=91, product_id=27, size=2, limit_price=Decimal("124")),
        requests.CancelOrderRequest(id=91, product_id=27),
        requests.CancelAllRequest(
            product_id=27,
            cancel_limit_orders=True,
            cancel_stop_orders=False,
            cancel_reduce_only_orders=False,
        ),
        requests.SetLeverageRequest(product_id=27, leverage=Decimal("5")),
        requests.SetMarginModeRequest(
            margin_mode=requests.RequestMarginMode.ISOLATED,
            subaccount_user_id="12345",
        ),
        requests.ChangePositionMarginRequest(product_id=27, delta_margin=Decimal("1")),
        requests.CreateBracketRequest(
            product_id=27,
            take_profit_order=requests.BracketTakeProfitOrder(
                order_type=OrderType.MARKET_ORDER, stop_price=Decimal("140")
            ),
            bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
        ),
        requests.EditBracketRequest(id=91, product_id=27, bracket_take_profit_price=Decimal("140")),
    )
    for request in cases:
        for field in type(request).model_fields:
            corrupted = request.model_copy()
            object.__setattr__(corrupted, field, object())
            with pytest.raises((ValueError, TypeError)):
                _encode_offline_mutation_candidate(
                    corrupted,
                    profile=_profile(),
                )


def test_candidate_fingerprint_requires_exact_members_and_original_profile_identity() -> None:
    profile = _profile()
    candidate = _encode_offline_mutation_candidate(_place(), profile=profile)
    fingerprint_slot = "_UnverifiedMutationWireCandidate__profile_fingerprint"

    plain_strings = (
        profile,
        _UnverifiedBooleanEncoding.JSON_BOOLEAN_CANDIDATE.value,
        _UnverifiedLeverageEncoding.DECIMAL_STRING_CANDIDATE.value,
    )
    object.__setattr__(candidate, fingerprint_slot, plain_strings)
    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _attest_unverified_mutation_candidate(candidate)

    for wrong_arity in ((), (profile,), (profile, profile.boolean_encoding)):
        fresh = _encode_offline_mutation_candidate(_place(), profile=profile)
        object.__setattr__(fresh, fingerprint_slot, wrong_arity)
        with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
            _attest_unverified_mutation_candidate(fresh)

    fresh = _encode_offline_mutation_candidate(_place(), profile=profile)
    equivalent_replacement = _profile()
    assert equivalent_replacement is not profile
    object.__setattr__(
        fresh,
        "_UnverifiedMutationWireCandidate__profile",
        equivalent_replacement,
    )
    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _attest_unverified_mutation_candidate(fresh)


def test_every_candidate_slot_is_present_and_independently_attested() -> None:
    expected_slots = {
        "__body",
        "__issuer",
        "__method",
        "__path",
        "__policy_version",
        "__profile",
        "__profile_fingerprint",
        "__query_string",
        "__route_id",
        "__semantic_fingerprint",
        "__semantic_request",
    }
    assert set(_UnverifiedMutationWireCandidate.__slots__) == expected_slots

    original_profile = _profile()
    corruptions: dict[str, object] = {
        "__body": b"{}",
        "__issuer": object(),
        "__method": "DELETE",
        "__path": "/v2/orders/all",
        "__policy_version": object(),
        "__profile": _profile(),
        "__profile_fingerprint": ("plain", "strings"),
        "__query_string": b"?after=secret",
        "__route_id": PrivateRouteId.CANCEL_ORDER,
        "__semantic_fingerprint": b"different",
        "__semantic_request": _place(size=3),
    }
    for short_name, replacement in corruptions.items():
        candidate = _encode_offline_mutation_candidate(
            _place(),
            profile=original_profile,
        )
        object.__setattr__(
            candidate,
            "_UnverifiedMutationWireCandidate" + short_name,
            replacement,
        )
        with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
            _attest_unverified_mutation_candidate(candidate)


def _assert_route_rejected_at_all_a6_admission_points(
    monkeypatch: pytest.MonkeyPatch,
    replacement: object,
) -> None:
    request = _place()
    candidate = _encode_offline_mutation_candidate(request, profile=_profile())
    monkeypatch.setattr(
        private_mutation_profile,
        "get_private_route_spec",
        lambda _route_id: replacement,
    )
    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _encode_offline_mutation_candidate(request, profile=_profile())
    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _attest_unverified_mutation_candidate(candidate)
    with pytest.raises(AmbiguousExchangeResult):
        private_mutation_parsers.parse_place_order_response(
            b"{}",
            status_code=200,
            request=request,
            external_user_id="12345",
        )


@pytest.mark.parametrize(
    "field",
    ("method", "path_template", "body_fields", "nested_body_fields"),
)
def test_hostile_route_scalar_and_tuple_subclasses_never_receive_callbacks(
    field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[str] = []

    class HostileString(str):
        def __eq__(self, _other: object) -> bool:
            callbacks.append("string equality")
            raise AssertionError("hostile string callback")

    class HostileTuple(tuple[object, ...]):
        def __eq__(self, _other: object) -> bool:
            callbacks.append("tuple equality")
            raise AssertionError("hostile tuple callback")

        def __iter__(self) -> object:
            callbacks.append("tuple iteration")
            raise AssertionError("hostile tuple callback")

    spec = replace(get_private_route_spec(PrivateRouteId.PLACE_ORDER))
    original = object.__getattribute__(spec, field)
    hostile: object
    if type(original) is str:
        hostile = HostileString(original)
    else:
        hostile = HostileTuple(original)
    object.__setattr__(spec, field, hostile)

    _assert_route_rejected_at_all_a6_admission_points(monkeypatch, spec)
    assert callbacks == []


def test_non_spec_route_object_is_rejected_without_attribute_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callbacks: list[str] = []

    class HostileNonSpec:
        def __getattribute__(self, name: str) -> object:
            callbacks.append(name)
            raise AssertionError("hostile route callback")

    _assert_route_rejected_at_all_a6_admission_points(monkeypatch, HostileNonSpec())
    assert callbacks == []


def test_reordered_body_tuple_cannot_escape_canonical_route_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = get_private_route_spec(PrivateRouteId.PLACE_ORDER)
    reordered = replace(
        spec,
        body_fields=(spec.body_fields[1], spec.body_fields[0], *spec.body_fields[2:]),
    )
    assert type(reordered) is PrivateRouteSpec
    _assert_route_rejected_at_all_a6_admission_points(monkeypatch, reordered)
