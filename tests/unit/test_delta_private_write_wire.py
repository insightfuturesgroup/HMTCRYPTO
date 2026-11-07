"""Offline-only mutation wire candidates with no dispatch authority."""

from __future__ import annotations

import copy
import pickle
from dataclasses import replace
from decimal import Decimal
from typing import Protocol, runtime_checkable

import pytest
from pydantic import ValidationError

from hmt_crypto.domain.private_orders import (
    MmpLevel,
    OrderSide,
    OrderType,
    StopTriggerMethod,
    TimeInForce,
)
from hmt_crypto.exchange import private_requests as requests
from hmt_crypto.exchange.delta import private_mutation_profile
from hmt_crypto.exchange.delta import private_write_wire as wire_subject
from hmt_crypto.exchange.delta.private_routes import (
    PrivateDispatchCapability,
    PrivateIdempotencyPolicy,
    PrivateProfileState,
    PrivateRegionSupport,
    PrivateResponseSchema,
    PrivateRouteAccess,
    PrivateRouteId,
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
from hmt_crypto.exchange.delta.rate_limit_policy import POLICY_VERSION

pytestmark = pytest.mark.unit


def _profile(
    *,
    boolean: _UnverifiedBooleanEncoding = (_UnverifiedBooleanEncoding.JSON_BOOLEAN_CANDIDATE),
    leverage: _UnverifiedLeverageEncoding = (_UnverifiedLeverageEncoding.DECIMAL_STRING_CANDIDATE),
) -> _OfflineMutationCodecProfile:
    return _create_offline_mutation_codec_profile(
        boolean_encoding=boolean,
        leverage_encoding=leverage,
    )


def _place(**changes: object) -> requests.PlaceOrderRequest:
    values: dict[str, object] = {
        "product_id": 27,
        "size": 2,
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT_ORDER,
        "limit_price": Decimal("123.45"),
        "client_order_id": "HMT_ABC_1",
    }
    values.update(changes)
    return requests.PlaceOrderRequest(**values)


@pytest.mark.parametrize(
    ("mutation", "route_id", "path", "expected"),
    (
        (
            _place(reduce_only=False),
            PrivateRouteId.PLACE_ORDER,
            "/v2/orders",
            b'{"product_id":27,"size":2,"side":"buy","order_type":"limit_order",'
            b'"limit_price":"123.45","reduce_only":false,"client_order_id":"HMT_ABC_1"}',
        ),
        (
            requests.ModifyOrderRequest(
                id=91,
                product_id=27,
                size=3,
                limit_price=Decimal("124.5"),
                mmp=MmpLevel.MMP1,
                post_only=False,
                stop_price=Decimal("120"),
            ),
            PrivateRouteId.MODIFY_ORDER,
            "/v2/orders",
            b'{"id":91,"product_id":27,"size":3,"limit_price":"124.5",'
            b'"mmp":"mmp1","post_only":false,"stop_price":"120"}',
        ),
        (
            requests.CancelOrderRequest(id=91, product_id=27),
            PrivateRouteId.CANCEL_ORDER,
            "/v2/orders",
            b'{"id":91,"product_id":27}',
        ),
        (
            requests.CancelAllRequest(
                product_id=27,
                cancel_limit_orders=True,
                cancel_stop_orders=False,
                cancel_reduce_only_orders=True,
            ),
            PrivateRouteId.CANCEL_ALL,
            "/v2/orders/all",
            b'{"product_id":27,"cancel_limit_orders":true,"cancel_stop_orders":false,'
            b'"cancel_reduce_only_orders":true}',
        ),
        (
            requests.SetLeverageRequest(product_id=27, leverage=Decimal("5.00")),
            PrivateRouteId.SET_LEVERAGE,
            "/v2/products/27/orders/leverage",
            b'{"leverage":"5"}',
        ),
        (
            requests.SetMarginModeRequest(
                margin_mode=requests.RequestMarginMode.PORTFOLIO,
                subaccount_user_id="12345",
            ),
            PrivateRouteId.SET_MARGIN_MODE,
            "/v2/users/margin_mode",
            b'{"margin_mode":"portfolio","subaccount_user_id":"12345"}',
        ),
        (
            requests.ChangePositionMarginRequest(product_id=27, delta_margin=Decimal("-10.50")),
            PrivateRouteId.CHANGE_POSITION_MARGIN,
            "/v2/positions/change_margin",
            b'{"product_id":27,"delta_margin":"-10.5"}',
        ),
        (
            requests.CreateBracketRequest(
                product_id=27,
                stop_loss_order=requests.BracketStopLossOrder(
                    order_type=OrderType.LIMIT_ORDER,
                    stop_price=Decimal("110"),
                    limit_price=Decimal("109.5"),
                ),
                take_profit_order=requests.BracketTakeProfitOrder(
                    order_type=OrderType.MARKET_ORDER,
                    stop_price=Decimal("140"),
                ),
                bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            ),
            PrivateRouteId.CREATE_BRACKET,
            "/v2/orders/bracket",
            b'{"product_id":27,"stop_loss_order":{"order_type":"limit_order",'
            b'"stop_price":"110","limit_price":"109.5"},"take_profit_order":'
            b'{"order_type":"market_order","stop_price":"140"},'
            b'"bracket_stop_trigger_method":"mark_price"}',
        ),
        (
            requests.EditBracketRequest(
                id=91,
                product_id=27,
                bracket_stop_loss_limit_price=Decimal("109.5"),
                bracket_take_profit_price=Decimal("140"),
                bracket_stop_trigger_method=StopTriggerMethod.SPOT_PRICE,
            ),
            PrivateRouteId.EDIT_BRACKET,
            "/v2/orders/bracket",
            b'{"id":91,"product_id":27,"bracket_stop_loss_limit_price":"109.5",'
            b'"bracket_take_profit_price":"140",'
            b'"bracket_stop_trigger_method":"spot_price"}',
        ),
    ),
)
def test_every_mutation_route_has_literal_ordered_candidate_bytes(
    mutation: object,
    route_id: PrivateRouteId,
    path: str,
    expected: bytes,
) -> None:
    candidate = _encode_offline_mutation_candidate(
        mutation,  # type: ignore[arg-type]
        profile=_profile(),
    )

    assert candidate.route_id is route_id
    assert candidate.method == get_private_route_spec(route_id).method
    assert candidate.path == path
    assert candidate.query_string == b""
    assert candidate.body == expected
    assert candidate.venue_verified is False
    assert candidate.dispatchable is False
    assert _attest_unverified_mutation_candidate(candidate) is candidate


def test_disputed_boolean_and_leverage_encodings_are_explicit_candidates() -> None:
    string_boolean = _encode_offline_mutation_candidate(
        _place(reduce_only=False),
        profile=_profile(boolean=_UnverifiedBooleanEncoding.STRING_BOOLEAN_CANDIDATE),
    )
    assert string_boolean.body.endswith(b'"reduce_only":"false","client_order_id":"HMT_ABC_1"}')

    integer_leverage = _encode_offline_mutation_candidate(
        requests.SetLeverageRequest(product_id=27, leverage=Decimal("5")),
        profile=_profile(leverage=_UnverifiedLeverageEncoding.JSON_INTEGER_CANDIDATE),
    )
    assert integer_leverage.body == b'{"leverage":5}'

    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _encode_offline_mutation_candidate(
            requests.SetLeverageRequest(product_id=27, leverage=Decimal("5.5")),
            profile=_profile(leverage=_UnverifiedLeverageEncoding.JSON_INTEGER_CANDIDATE),
        )


def test_place_full_optional_body_preserves_a4_order_and_omits_none() -> None:
    request = _place(
        stop_order_type=requests.RequestStopOrderType.STOP_LOSS_ORDER,
        stop_price=Decimal("120"),
        stop_trigger_method=StopTriggerMethod.LAST_TRADED_PRICE,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )
    candidate = _encode_offline_mutation_candidate(request, profile=_profile())
    assert candidate.body == (
        b'{"product_id":27,"size":2,"side":"buy","order_type":"limit_order",'
        b'"limit_price":"123.45","stop_order_type":"stop_loss_order",'
        b'"stop_price":"120","stop_trigger_method":"last_traded_price",'
        b'"time_in_force":"ioc","reduce_only":false,"client_order_id":"HMT_ABC_1"}'
    )
    assert b"trail_amount" not in candidate.body
    assert b"null" not in candidate.body


def test_positive_and_negative_position_margin_retain_canonical_sign() -> None:
    bodies = tuple(
        _encode_offline_mutation_candidate(
            requests.ChangePositionMarginRequest(product_id=27, delta_margin=Decimal(value)),
            profile=_profile(),
        ).body
        for value in ("10.500", "-10.500")
    )
    assert bodies == (
        b'{"product_id":27,"delta_margin":"10.5"}',
        b'{"product_id":27,"delta_margin":"-10.5"}',
    )


def test_create_bracket_leg_combinations_remain_distinct() -> None:
    stop = requests.BracketStopLossOrder(
        order_type=OrderType.MARKET_ORDER, trail_amount=Decimal("5")
    )
    take = requests.BracketTakeProfitOrder(
        order_type=OrderType.MARKET_ORDER, stop_price=Decimal("140")
    )
    bodies = tuple(
        _encode_offline_mutation_candidate(
            requests.CreateBracketRequest(
                product_id=27,
                stop_loss_order=stop if include_stop else None,
                take_profit_order=take if include_take else None,
                bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            ),
            profile=_profile(),
        ).body
        for include_stop, include_take in ((True, False), (False, True), (True, True))
    )
    assert len(set(bodies)) == 3
    assert b'"trail_amount":"5"' in bodies[0]
    assert b'"take_profit_order"' not in bodies[0]
    assert b'"stop_loss_order"' not in bodies[1]
    assert b'"stop_loss_order"' in bodies[2] and b'"take_profit_order"' in bodies[2]


def test_request_is_exact_class_revalidated_before_field_access() -> None:
    calls: list[str] = []

    class Hostile:
        def __getattribute__(self, name: str) -> object:
            calls.append(name)
            raise AssertionError("hostile access")

    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _encode_offline_mutation_candidate(Hostile(), profile=_profile())  # type: ignore[arg-type]
    assert calls == []

    request = _place()
    object.__setattr__(request, "size", True)
    with pytest.raises((ValidationError, ValueError)):
        _encode_offline_mutation_candidate(request, profile=_profile())


def test_profile_requires_sealed_factory_and_exact_candidate_enums() -> None:
    with pytest.raises(TypeError):
        _OfflineMutationCodecProfile(  # type: ignore[call-arg]
            _UnverifiedBooleanEncoding.JSON_BOOLEAN_CANDIDATE,
            _UnverifiedLeverageEncoding.DECIMAL_STRING_CANDIDATE,
        )
    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _create_offline_mutation_codec_profile(  # type: ignore[arg-type]
            boolean_encoding="unverified_candidate_json_boolean",
            leverage_encoding=_UnverifiedLeverageEncoding.DECIMAL_STRING_CANDIDATE,
        )
    profile = _profile()
    assert profile.venue_verified is False
    assert profile.dispatchable is False


def test_profile_and_candidate_are_immutable_noncopyable_and_nonserializable() -> None:
    profile = _profile()
    candidate = _encode_offline_mutation_candidate(_place(), profile=profile)
    for value in (profile, candidate):
        with pytest.raises(TypeError):
            copy.copy(value)
        with pytest.raises(TypeError):
            copy.deepcopy(value)
        with pytest.raises(TypeError):
            pickle.dumps(value)
        with pytest.raises(AttributeError):
            value.new_field = "fixture-secret"
    with pytest.raises(TypeError):
        _UnverifiedMutationWireCandidate()  # type: ignore[call-arg]
    assert "HMT_ABC_1" not in repr(candidate)
    assert "123.45" not in str(candidate)
    assert "bytes=" in repr(candidate)


def test_attestation_rejects_every_private_slot_corruption() -> None:
    candidate = _encode_offline_mutation_candidate(_place(), profile=_profile())
    corruptions = {
        "_UnverifiedMutationWireCandidate__route_id": PrivateRouteId.CANCEL_ORDER,
        "_UnverifiedMutationWireCandidate__method": "DELETE",
        "_UnverifiedMutationWireCandidate__path": "/v2/orders/all",
        "_UnverifiedMutationWireCandidate__query_string": b"?secret=1",
        "_UnverifiedMutationWireCandidate__body": b"{}",
        "_UnverifiedMutationWireCandidate__policy_version": "wrong-policy",
        "_UnverifiedMutationWireCandidate__profile": _profile(
            boolean=_UnverifiedBooleanEncoding.STRING_BOOLEAN_CANDIDATE
        ),
        "_UnverifiedMutationWireCandidate__semantic_fingerprint": b"0" * 32,
    }
    for slot, replacement in corruptions.items():
        fresh = _encode_offline_mutation_candidate(_place(), profile=_profile())
        object.__setattr__(fresh, slot, replacement)
        with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
            _attest_unverified_mutation_candidate(fresh)

    alternate = _encode_offline_mutation_candidate(_place(size=3), profile=_profile())
    object.__setattr__(
        candidate,
        "_UnverifiedMutationWireCandidate__semantic_request",
        object.__getattribute__(alternate, "_UnverifiedMutationWireCandidate__semantic_request"),
    )
    object.__setattr__(candidate, "_UnverifiedMutationWireCandidate__body", alternate.body)
    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _attest_unverified_mutation_candidate(candidate)


def test_attestation_rejects_equal_but_non_authoritative_policy_object() -> None:
    assert wire_subject.POLICY_VERSION is POLICY_VERSION
    candidate = _encode_offline_mutation_candidate(_place(), profile=_profile())
    equal_policy = "".join(POLICY_VERSION)
    assert equal_policy == POLICY_VERSION and equal_policy is not POLICY_VERSION
    object.__setattr__(
        candidate,
        "_UnverifiedMutationWireCandidate__policy_version",
        equal_policy,
    )
    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _attest_unverified_mutation_candidate(candidate)


def test_cancel_candidate_cannot_attest_against_coordinated_post_route_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _encode_offline_mutation_candidate(
        requests.CancelOrderRequest(id=91, product_id=27), profile=_profile()
    )
    object.__setattr__(candidate, "_UnverifiedMutationWireCandidate__method", "POST")
    original = get_private_route_spec

    def drifted(route_id: PrivateRouteId) -> object:
        spec = original(route_id)
        return replace(spec, method="POST") if route_id is PrivateRouteId.CANCEL_ORDER else spec

    profile_owner = getattr(wire_subject, "_mutation_profile", wire_subject)
    monkeypatch.setattr(profile_owner, "get_private_route_spec", drifted)
    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _attest_unverified_mutation_candidate(candidate)


@pytest.mark.parametrize(
    ("route_id", "method", "path", "path_fields", "body_fields", "nested", "schema", "ceiling"),
    (
        (
            PrivateRouteId.PLACE_ORDER,
            "POST",
            "/v2/orders",
            (),
            (
                "product_id",
                "size",
                "side",
                "order_type",
                "limit_price",
                "stop_order_type",
                "stop_price",
                "trail_amount",
                "stop_trigger_method",
                "bracket_stop_trigger_method",
                "bracket_stop_loss_limit_price",
                "bracket_stop_loss_price",
                "bracket_trail_amount",
                "bracket_take_profit_limit_price",
                "bracket_take_profit_price",
                "time_in_force",
                "mmp",
                "post_only",
                "reduce_only",
                "client_order_id",
            ),
            (),
            PrivateResponseSchema.ORDER,
            1_048_576,
        ),
        (
            PrivateRouteId.MODIFY_ORDER,
            "PUT",
            "/v2/orders",
            (),
            (
                "id",
                "product_id",
                "size",
                "limit_price",
                "mmp",
                "post_only",
                "stop_price",
                "trail_amount",
            ),
            (),
            PrivateResponseSchema.ORDER,
            1_048_576,
        ),
        (
            PrivateRouteId.CANCEL_ORDER,
            "DELETE",
            "/v2/orders",
            (),
            ("id", "product_id"),
            (),
            PrivateResponseSchema.ORDER,
            1_048_576,
        ),
        (
            PrivateRouteId.CANCEL_ALL,
            "DELETE",
            "/v2/orders/all",
            (),
            (
                "product_id",
                "cancel_limit_orders",
                "cancel_stop_orders",
                "cancel_reduce_only_orders",
            ),
            (),
            PrivateResponseSchema.ACKNOWLEDGEMENT,
            262_144,
        ),
        (
            PrivateRouteId.SET_LEVERAGE,
            "POST",
            "/v2/products/{product_id}/orders/leverage",
            ("product_id",),
            ("leverage",),
            (),
            PrivateResponseSchema.LEVERAGE,
            262_144,
        ),
        (
            PrivateRouteId.SET_MARGIN_MODE,
            "PUT",
            "/v2/users/margin_mode",
            (),
            ("margin_mode", "subaccount_user_id"),
            (),
            PrivateResponseSchema.MARGIN_MODE,
            262_144,
        ),
        (
            PrivateRouteId.CHANGE_POSITION_MARGIN,
            "POST",
            "/v2/positions/change_margin",
            (),
            ("product_id", "delta_margin"),
            (),
            PrivateResponseSchema.MARGINED_POSITION,
            1_048_576,
        ),
        (
            PrivateRouteId.CREATE_BRACKET,
            "POST",
            "/v2/orders/bracket",
            (),
            ("product_id", "stop_loss_order", "take_profit_order", "bracket_stop_trigger_method"),
            (
                "stop_loss_order.order_type",
                "stop_loss_order.stop_price",
                "stop_loss_order.trail_amount",
                "stop_loss_order.limit_price",
                "take_profit_order.order_type",
                "take_profit_order.stop_price",
                "take_profit_order.limit_price",
            ),
            PrivateResponseSchema.ACKNOWLEDGEMENT,
            262_144,
        ),
        (
            PrivateRouteId.EDIT_BRACKET,
            "PUT",
            "/v2/orders/bracket",
            (),
            (
                "id",
                "product_id",
                "bracket_stop_loss_limit_price",
                "bracket_stop_loss_price",
                "bracket_take_profit_limit_price",
                "bracket_take_profit_price",
                "bracket_trail_amount",
                "bracket_stop_trigger_method",
            ),
            (),
            PrivateResponseSchema.ACKNOWLEDGEMENT,
            262_144,
        ),
    ),
)
def test_all_nine_canonical_route_profiles_and_body_bounds_are_pinned(
    route_id: PrivateRouteId,
    method: str,
    path: str,
    path_fields: tuple[str, ...],
    body_fields: tuple[str, ...],
    nested: tuple[str, ...],
    schema: PrivateResponseSchema,
    ceiling: int,
) -> None:
    spec = get_private_route_spec(route_id)
    assert (
        spec.method,
        spec.path_template,
        spec.path_fields,
        spec.query_fields,
        spec.body_fields,
        spec.nested_body_fields,
        spec.response_schema,
        spec.max_response_bytes,
    ) == (method, path, path_fields, (), body_fields, nested, schema, ceiling)


@pytest.mark.parametrize("route_id", tuple(PrivateRouteId)[10:])
def test_each_canonical_profile_rejects_its_own_ceiling_drift(
    route_id: PrivateRouteId,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = get_private_route_spec

    def drifted(candidate: PrivateRouteId) -> object:
        spec = original(candidate)
        return (
            replace(spec, max_response_bytes=spec.max_response_bytes + 1)
            if candidate is route_id
            else spec
        )

    monkeypatch.setattr(private_mutation_profile, "get_private_route_spec", drifted)
    with pytest.raises(ValueError, match="canonical mutation profile unavailable"):
        private_mutation_profile._require_canonical_mutation_route(route_id)


@pytest.mark.parametrize(
    ("mutation", "required_fragments"),
    (
        (
            _place(
                stop_order_type=requests.RequestStopOrderType.STOP_LOSS_ORDER,
                stop_price=Decimal("120"),
                stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            ),
            (b'"stop_order_type"', b'"stop_price"', b'"stop_trigger_method"'),
        ),
        (
            _place(
                stop_order_type=requests.RequestStopOrderType.TAKE_PROFIT_ORDER,
                trail_amount=Decimal("2"),
                stop_trigger_method=StopTriggerMethod.SPOT_PRICE,
            ),
            (b'"trail_amount"',),
        ),
        (
            _place(time_in_force=TimeInForce.GTC, mmp=MmpLevel.MMP2, post_only=False),
            (b'"time_in_force":"gtc"', b'"mmp":"mmp2"', b'"post_only":false'),
        ),
        (
            _place(
                bracket_stop_trigger_method=StopTriggerMethod.LAST_TRADED_PRICE,
                bracket_stop_loss_limit_price=Decimal("111"),
                bracket_stop_loss_price=Decimal("112"),
                bracket_take_profit_limit_price=Decimal("139"),
                bracket_take_profit_price=Decimal("140"),
            ),
            (
                b'"bracket_stop_trigger_method"',
                b'"bracket_stop_loss_limit_price"',
                b'"bracket_stop_loss_price"',
                b'"bracket_take_profit_limit_price"',
                b'"bracket_take_profit_price"',
            ),
        ),
        (
            _place(
                bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
                bracket_trail_amount=Decimal("3"),
            ),
            (b'"bracket_trail_amount"',),
        ),
        (
            requests.ModifyOrderRequest(
                id=1,
                product_id=27,
                size=2,
                limit_price=Decimal("101"),
                mmp=MmpLevel.MMP1,
                post_only=False,
                stop_price=Decimal("99"),
            ),
            (b'"limit_price"', b'"mmp"', b'"post_only":false', b'"stop_price"'),
        ),
        (
            requests.ModifyOrderRequest(id=1, product_id=27, size=2, trail_amount=Decimal("4")),
            (b'"trail_amount"',),
        ),
        (
            requests.EditBracketRequest(
                id=1,
                product_id=27,
                bracket_stop_loss_limit_price=Decimal("98"),
                bracket_stop_loss_price=Decimal("99"),
                bracket_take_profit_limit_price=Decimal("109"),
                bracket_take_profit_price=Decimal("110"),
                bracket_stop_trigger_method=StopTriggerMethod.MARK_PRICE,
            ),
            (
                b'"bracket_stop_loss_limit_price"',
                b'"bracket_stop_loss_price"',
                b'"bracket_take_profit_limit_price"',
                b'"bracket_take_profit_price"',
                b'"bracket_stop_trigger_method"',
            ),
        ),
        (
            requests.EditBracketRequest(id=1, product_id=27, bracket_trail_amount=Decimal("5")),
            (b'"bracket_trail_amount"',),
        ),
    ),
)
def test_every_conditional_optional_write_family_has_literal_presence_evidence(
    mutation: object, required_fragments: tuple[bytes, ...]
) -> None:
    body = _encode_offline_mutation_candidate(
        mutation,
        profile=_profile(),  # type: ignore[arg-type]
    ).body
    assert all(fragment in body for fragment in required_fragments)
    assert [body.index(fragment) for fragment in required_fragments] == sorted(
        body.index(fragment) for fragment in required_fragments
    )
    assert b":null" not in body


def test_maximum_request_scalars_remain_canonical_and_bounded() -> None:
    maximum_id = 2**63 - 1
    maximum_decimal = Decimal("9" * 20 + "." + "9" * 18)
    request = requests.PlaceOrderRequest(
        product_id=maximum_id,
        size=maximum_id,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_ORDER,
        limit_price=maximum_decimal,
        client_order_id="X" * 32,
    )
    candidate = _encode_offline_mutation_candidate(request, profile=_profile())
    assert str(maximum_id).encode() in candidate.body
    assert format(maximum_decimal, "f").encode() in candidate.body
    assert b'"client_order_id":"' + b"X" * 32 + b'"' in candidate.body
    assert len(candidate.body) <= get_private_route_spec(candidate.route_id).max_response_bytes
    other_maximum_requests = (
        requests.ModifyOrderRequest(id=maximum_id, product_id=maximum_id, size=maximum_id),
        requests.CancelOrderRequest(id=maximum_id, product_id=maximum_id),
        requests.SetLeverageRequest(product_id=maximum_id, leverage=maximum_decimal),
        requests.SetMarginModeRequest(
            margin_mode=requests.RequestMarginMode.ISOLATED,
            subaccount_user_id=str(maximum_id),
        ),
        requests.ChangePositionMarginRequest(
            product_id=maximum_id, delta_margin=maximum_decimal.copy_negate()
        ),
        requests.EditBracketRequest(
            id=maximum_id,
            product_id=maximum_id,
            bracket_take_profit_price=maximum_decimal,
        ),
    )
    for maximum_request in other_maximum_requests:
        encoded = _encode_offline_mutation_candidate(maximum_request, profile=_profile())
        assert len(encoded.body) <= get_private_route_spec(encoded.route_id).max_response_bytes


def test_candidate_body_is_rejected_before_crossing_attested_route_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = get_private_route_spec(PrivateRouteId.PLACE_ORDER)
    monkeypatch.setattr(
        wire_subject,
        "_require_offline_spec",
        lambda _route_id: replace(spec, max_response_bytes=1),
    )
    with pytest.raises(ValueError, match="offline mutation candidate unavailable"):
        _encode_offline_mutation_candidate(_place(), profile=_profile())


def test_candidates_are_exact_a4_offline_write_profiles() -> None:
    cases = (
        _place(),
        requests.ModifyOrderRequest(id=1, product_id=27, size=2),
        requests.CancelOrderRequest(id=1, product_id=27),
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
        requests.EditBracketRequest(
            id=1,
            product_id=27,
            bracket_take_profit_price=Decimal("140"),
        ),
    )
    for request in cases:
        candidate = _encode_offline_mutation_candidate(request, profile=_profile())
        spec = get_private_route_spec(candidate.route_id)
        assert spec.region_support is PrivateRegionSupport.INDIA_ONLY
        assert spec.access is PrivateRouteAccess.WRITE
        assert spec.idempotency is PrivateIdempotencyPolicy.NO_EXCHANGE_GUARANTEE
        assert spec.dispatch_capability is PrivateDispatchCapability.NONE
        assert spec.profile_state is PrivateProfileState.OFFLINE_CODEC_ONLY
        assert candidate.method == spec.method
        assert len(candidate.body) <= spec.max_response_bytes


@runtime_checkable
class _AuthorityProtocol(Protocol):
    verified: bool

    def send(self) -> object: ...

    def sign(self) -> object: ...

    def execute(self) -> object: ...


def test_candidates_have_no_transport_signing_or_execution_authority() -> None:
    import hmt_crypto.exchange.delta as delta

    candidate = _encode_offline_mutation_candidate(_place(), profile=_profile())
    assert not isinstance(candidate, _AuthorityProtocol)
    for name in ("send", "sign", "execute", "capability", "verified"):
        assert not hasattr(candidate, name)
    for name in (
        "_OfflineMutationCodecProfile",
        "_UnverifiedMutationWireCandidate",
        "_encode_offline_mutation_candidate",
    ):
        assert name not in delta.__all__
