"""Closed account-domain contracts for Delta private observations."""

from __future__ import annotations

import pickle
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from delta_private_model_test_support import account_api, isolated_python, valid_account
from pydantic import TypeAdapter, ValidationError

pytestmark = pytest.mark.unit


def test_account_import_is_inert_and_owner_local() -> None:
    """Catch account import opening later owners or runtime exchange authority."""
    result = isolated_python(
        """
import importlib
import sys

account = importlib.import_module("hmt_crypto.domain.account")
assert "hmt_crypto.domain.private_wallet" not in sys.modules
assert "hmt_crypto.domain.private_positions" not in sys.modules
assert "hmt_crypto.domain.private_orders" not in sys.modules
assert account.ValueField[account.OpaqueCursor] is account.OpaqueCursorValueField
assert account.NullableField[account.OpaqueCursor] is account.NullableOpaqueCursor
assert len(account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS) == 2
print("ok")
"""
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_account_identity_and_observation_context_are_exact_and_time_ordered() -> None:
    """Catch coercive account identity or a response preceding its request."""
    from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    api = account_api()
    account = valid_account(api)
    requested = datetime(2026, 11, 5, 10, 30, tzinfo=UTC)
    context = api.ObservationContext(
        account=account,
        endpoint_id=PrivateRouteId.BALANCES,
        correlation_id=UUID("00000000-0000-0000-0000-000000000002"),
        requested_at=requested,
        received_at=requested,
        source="delta_rest",
    )
    assert context.received_at is requested
    assert context.model_dump() == {
        "account": {
            "account_id": UUID("00000000-0000-0000-0000-000000000001"),
            "external_user_id": "12345",
            "region": DeltaRegion.INDIA,
            "environment": DeltaEnvironment.TESTNET,
            "mode": ExecutionMode.PAPER,
            "credential_version": 7,
        },
        "endpoint_id": PrivateRouteId.BALANCES,
        "correlation_id": UUID("00000000-0000-0000-0000-000000000002"),
        "requested_at": requested,
        "received_at": requested,
        "source": "delta_rest",
    }
    assert '"source":"delta_rest"' in context.model_dump_json()

    invalid_accounts = (
        {"account_id": str(UUID(int=1))},
        {"external_user_id": 12345},
        {"region": "india"},
        {"environment": "testnet"},
        {"mode": "PAPER"},
        {"credential_version": True},
    )
    base = {
        "account_id": UUID(int=1),
        "external_user_id": "12345",
        "region": DeltaRegion.INDIA,
        "environment": DeltaEnvironment.TESTNET,
        "mode": ExecutionMode.PAPER,
        "credential_version": 7,
    }
    for change in invalid_accounts:
        with pytest.raises(ValidationError) as captured:
            api.AccountRef(**(base | change))
        assert all(detail.get("input") is None for detail in captured.value.errors())

    with pytest.raises(ValidationError):
        api.ObservationContext(
            account=account,
            endpoint_id=PrivateRouteId.BALANCES,
            correlation_id=UUID(int=2),
            requested_at=requested,
            received_at=requested - timedelta(microseconds=1),
            source="delta_rest",
        )


def test_private_pagination_provenance_owns_exact_nonempty_evidence() -> None:
    """Catch mutable page evidence, reversed time, or credential drift."""
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    api = account_api()
    account = valid_account(api)
    first = datetime(2026, 11, 5, 10, 30, tzinfo=UTC)
    correlation = UUID("00000000-0000-0000-0000-000000000003")
    value = api.PrivatePaginationProvenance(
        account=account,
        endpoint_id=PrivateRouteId.TRANSACTIONS,
        first_requested_at=first,
        last_received_at=first + timedelta(seconds=1),
        page_correlation_ids=(correlation, correlation),
        credential_version=7,
        policy_version="delta-docs-2026-11-04-private-v1",
        mode=api.CollectionMode.STRICT,
    )
    assert value.page_correlation_ids == (correlation, correlation)
    assert value.model_copy() == value

    for change in (
        {"page_correlation_ids": ()},
        {"page_correlation_ids": [correlation]},
        {"credential_version": 8},
        {"last_received_at": first - timedelta(microseconds=1)},
        {"policy_version": "fixture-secret-policy"},
        {"mode": "STRICT"},
    ):
        payload = {
            "account": account,
            "endpoint_id": PrivateRouteId.TRANSACTIONS,
            "first_requested_at": first,
            "last_received_at": first,
            "page_correlation_ids": (correlation,),
            "credential_version": 7,
            "policy_version": "delta-docs-2026-11-04-private-v1",
            "mode": api.CollectionMode.STRICT,
        }
        with pytest.raises(ValidationError) as captured:
            api.PrivatePaginationProvenance(**(payload | change))
        assert "fixture-secret" not in str(captured.value)


def test_account_literal_and_enum_boundaries_reject_raw_or_coercive_values() -> None:
    """Catch bool/string coercion bypassing exact account vocabulary."""
    api = account_api()
    literal_cases = (
        (api.MissingKind, "MISSING"),
        (api.NullKind, "NULL"),
        (api.ValueKind, "VALUE"),
        (api.DeltaRestSource, "delta_rest"),
        (api.PrivatePolicyVersion, "delta-docs-2026-11-04-private-v1"),
        (api.NumericFillOrderKind, "NUMERIC"),
        (api.SettlementFillOrderKind, "SETTLEMENT"),
        (api.LookupFoundKind, "FOUND"),
        (api.LookupNotFoundKind, "NOT_FOUND"),
        (api.StrictPaginationKind, "STRICT"),
        (api.DiagnosticPaginationKind, "DIAGNOSTIC"),
    )
    for alias, expected in literal_cases:
        adapter = TypeAdapter(alias)
        assert adapter.validate_python(expected) == expected
        with pytest.raises(ValidationError):
            adapter.validate_python(expected + "_wrong")

    assert TypeAdapter(api.ExactBool).validate_python(True) is True
    assert TypeAdapter(api.ExactTrue).validate_python(True) is True
    for invalid in (1, 0, "true", False):
        with pytest.raises(ValidationError):
            TypeAdapter(api.ExactTrue).validate_python(invalid)

    assert api.CollectionMode.STRICT.value == "STRICT"
    assert api.TruncationReason.PAGE_LIMIT.value == "PAGE_LIMIT"
    with pytest.raises(ValidationError):
        TypeAdapter(api.CollectionMode).validate_python("STRICT")


def test_nullable_cursor_preserves_null_and_value_as_distinct_exact_branches() -> None:
    """Catch a nullable alias accepting missing, coercive, or ambiguous branches."""
    api = account_api()
    value = api.OpaqueCursorValueField(kind="VALUE", value="next:cursor")
    null = api.NullField(kind="NULL")
    adapter = TypeAdapter(api.NullableOpaqueCursor)

    validated_value = adapter.validate_python(value)
    assert validated_value == value
    assert validated_value is not value
    validated_null = adapter.validate_python(null)
    assert validated_null == null
    assert validated_null is not null
    assert type(adapter.validate_python({"kind": "NULL"})) is api.NullField
    parsed = adapter.validate_python({"kind": "VALUE", "value": "next:cursor"})
    assert type(parsed) is api.OpaqueCursorValueField
    assert parsed.model_dump() == {"kind": "VALUE", "value": "next:cursor"}
    assert api.ValueField[api.OpaqueCursor] is api.OpaqueCursorValueField
    assert api.NullableField[api.OpaqueCursor] is api.NullableOpaqueCursor

    for invalid in (
        {"kind": "MISSING"},
        {"kind": "VALUE", "value": None},
        {"kind": "NULL", "value": "next:cursor"},
        {"kind": "OTHER"},
        {},
        [("kind", "NULL")],
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)

    assert null.model_dump() == {"kind": "NULL"}


def test_unreviewed_generic_subscriptions_return_a_permanent_safe_rejection() -> None:
    """Catch Pydantic creating or caching authority for an unreviewed payload."""
    api = account_api()
    marker = api.ValueField[list[int]]
    assert marker is api.ValueField[dict[str, int]]
    assert marker is not api.OpaqueCursorValueField
    assert api.FieldPresence[api.OpaqueCursor] is not api.NullableOpaqueCursor

    adapters = (TypeAdapter(marker), TypeAdapter(api.ValueField), TypeAdapter(api.Observed[int]))
    for adapter in adapters:
        with pytest.raises(ValidationError) as captured:
            adapter.validate_python({"kind": "VALUE", "value": []})
        assert captured.value.errors(include_url=False) == [
            {
                "type": "private_generic_specialization",
                "loc": (),
                "msg": "private generic specialization unavailable",
                "input": None,
            }
        ]

    with pytest.raises(ValidationError):
        marker.model_validate({})
    with pytest.raises(ValidationError):
        marker.model_construct()
    with pytest.raises(TypeError, match="private generic specialization unavailable"):
        marker.model_json_schema()


def test_account_specializations_have_canonical_identity_and_frozen_policy() -> None:
    """Catch duplicate candidates or generated models weakening A1d controls."""
    api = account_api()
    canonical = api.OpaqueCursorValueField
    assert canonical.__module__ == "hmt_crypto.domain.account"
    assert canonical.__name__ == "OpaqueCursorValueField"
    assert pickle.loads(pickle.dumps(canonical)) is canonical
    assert api.ValueField[api.OpaqueCursor] is canonical
    assert api.ValueField[api.OpaqueCursor] is canonical
    for key, expected in {
        "extra": "forbid",
        "frozen": True,
        "hide_input_in_errors": True,
        "revalidate_instances": "always",
        "strict": True,
        "validate_default": True,
    }.items():
        assert canonical.model_config[key] == expected
    assert not canonical.__pydantic_decorators__.field_serializers
    assert not canonical.__pydantic_decorators__.model_serializers

    account = api.module
    table = account._ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS
    assert type(table) is tuple and len(table) == 2
    assert table[0].family is api.ValueField
    assert table[0].payload is api.OpaqueCursor
    assert table[0].specialization is canonical
    assert table[1].family is api.NullableField
    assert table[1].payload is api.OpaqueCursor
    assert table[1].specialization is api.NullableOpaqueCursor
    result = isolated_python(
        """
import importlib

account = importlib.import_module("hmt_crypto.domain.account")
try:
    account._reviewed_generic_manifest_for_verification()
except RuntimeError as error:
    assert str(error) == "private generic manifest unavailable"
else:
    raise AssertionError("fresh account import exposed the private generic manifest")
print("ok")
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_rejected_subscription_does_not_invoke_or_retain_hostile_payload() -> None:
    """Catch admission hashing, comparing, formatting, or retaining caller payloads."""
    import gc
    import weakref

    api = account_api()
    calls: list[str] = []

    class HostileMeta(type):
        def __hash__(cls) -> int:
            calls.append("hash")
            return 1

        def __eq__(cls, other: object) -> bool:
            del other
            calls.append("eq")
            return False

        def __repr__(cls) -> str:
            calls.append("repr")
            return "fixture-secret-hostile"

    payload = HostileMeta("EphemeralPayload", (), {})
    reference = weakref.ref(payload)
    marker = api.ValueField[payload]
    assert marker is api.ValueField[int]
    assert calls == []
    del payload
    gc.collect()
    assert reference() is None


def test_account_contracts_reject_hostile_root_nested_key_and_container_subclasses() -> None:
    """Exact ownership checks precede mapping, key, scalar, and tuple callbacks."""
    from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    api = account_api()
    calls: list[str] = []

    class HostileDict(dict[object, object]):
        def __iter__(self):
            calls.append("iter")
            raise AssertionError("hostile mapping iteration ran")

        def items(self):
            calls.append("items")
            raise AssertionError("hostile mapping items ran")

    class HostileKey(str):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("hostile key equality ran")

        def __hash__(self) -> int:
            return str.__hash__(self)

    class HostileTuple(tuple[object, ...]):
        def __iter__(self):
            calls.append("tuple-iter")
            raise AssertionError("hostile tuple iteration ran")

    with pytest.raises(ValidationError):
        api.AccountRef.model_validate(HostileDict())
    assert calls == []

    key = HostileKey("account_id")
    keyed = {
        key: UUID(int=1),
        "external_user_id": "12345",
        "region": DeltaRegion.INDIA,
        "environment": DeltaEnvironment.TESTNET,
        "mode": ExecutionMode.PAPER,
        "credential_version": 7,
    }
    calls.clear()
    with pytest.raises(ValidationError):
        api.AccountRef.model_validate(keyed)
    assert calls == []

    account = valid_account(api)
    now = datetime(2026, 11, 5, 10, 30, tzinfo=UTC)
    context_payload = {
        "account": HostileDict(account.model_dump()),
        "endpoint_id": PrivateRouteId.BALANCES,
        "correlation_id": UUID(int=2),
        "requested_at": now,
        "received_at": now,
        "source": "delta_rest",
    }
    with pytest.raises(ValidationError):
        api.ObservationContext.model_validate(context_payload)
    assert calls == []

    provenance = {
        "account": account,
        "endpoint_id": PrivateRouteId.TRANSACTIONS,
        "first_requested_at": now,
        "last_received_at": now,
        "page_correlation_ids": HostileTuple((UUID(int=3),)),
        "credential_version": 7,
        "policy_version": "delta-docs-2026-11-04-private-v1",
        "mode": api.CollectionMode.STRICT,
    }
    with pytest.raises(ValidationError):
        api.PrivatePaginationProvenance.model_validate(provenance)
    assert calls == []


def test_account_literals_booleans_config_and_route_enums_are_python_exact() -> None:
    """Every account vocabulary scalar rejects JSON/raw/subclass representations."""
    from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    api = account_api()
    calls: list[str] = []

    class HostileString(str):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("hostile literal equality ran")

        def __ne__(self, other: object) -> bool:
            del other
            calls.append("ne")
            raise AssertionError("hostile literal inequality ran")

    aliases = (
        (api.MissingKind, "MISSING"),
        (api.NullKind, "NULL"),
        (api.ValueKind, "VALUE"),
        (api.DeltaRestSource, "delta_rest"),
        (api.PrivatePolicyVersion, "delta-docs-2026-11-04-private-v1"),
        (api.NumericFillOrderKind, "NUMERIC"),
        (api.SettlementFillOrderKind, "SETTLEMENT"),
        (api.LookupFoundKind, "FOUND"),
        (api.LookupNotFoundKind, "NOT_FOUND"),
        (api.StrictPaginationKind, "STRICT"),
        (api.DiagnosticPaginationKind, "DIAGNOSTIC"),
    )
    for alias, literal in aliases:
        adapter = TypeAdapter(alias)
        assert adapter.validate_python(literal) == literal
        assert adapter.dump_python(literal) == literal
        with pytest.raises(ValidationError):
            adapter.validate_json(f'"{literal}"')
        with pytest.raises(ValidationError):
            adapter.validate_python(HostileString(literal))
        assert calls == []

    bool_adapter = TypeAdapter(api.ExactBool)
    assert bool_adapter.validate_python(True) is True
    assert bool_adapter.validate_python(False) is False
    for invalid in (0, 1, "true", "false"):
        with pytest.raises(ValidationError):
            bool_adapter.validate_python(invalid)

    enum_cases = (
        (api.ExactDeltaRegion, DeltaRegion.INDIA, "india"),
        (api.ExactDeltaEnvironment, DeltaEnvironment.TESTNET, "testnet"),
        (api.ExactExecutionMode, ExecutionMode.PAPER, "PAPER"),
        (api.CollectionMode, api.CollectionMode.STRICT, "STRICT"),
        (api.TruncationReason, api.TruncationReason.PAGE_LIMIT, "PAGE_LIMIT"),
        (PrivateRouteId, PrivateRouteId.BALANCES, "balances"),
    )
    for alias, member, raw in enum_cases:
        adapter = TypeAdapter(alias)
        assert adapter.validate_python(member) is member
        with pytest.raises(ValidationError):
            adapter.validate_python(raw)


def test_nullable_cursor_hostile_and_corrupt_branch_matrix_is_callback_free() -> None:
    """Alias admission checks exact root, tag, then exact branch ownership."""
    api = account_api()
    adapter = TypeAdapter(api.NullableOpaqueCursor)
    calls: list[str] = []

    class HostileTag(str):
        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("hostile tag equality ran")

        def __ne__(self, other: object) -> bool:
            del other
            calls.append("ne")
            raise AssertionError("hostile tag inequality ran")

    class HostileMapping(dict[str, object]):
        def __iter__(self):
            calls.append("iter")
            raise AssertionError("hostile mapping iteration ran")

    class NullSubclass(api.NullField):
        pass

    invalid = (
        HostileMapping(kind="NULL"),
        {"kind": HostileTag("NULL")},
        {"kind": "MISSING"},
        {"kind": "NULL", "value": "next"},
        {"kind": "VALUE", "value": None},
        {"kind": "VALUE", "value": "next", "extra": 1},
        NullSubclass(kind="NULL"),
    )
    calls.clear()
    for value in invalid:
        with pytest.raises(ValidationError):
            adapter.validate_python(value)
        assert calls == []

    corrupt = api.NullField(kind="NULL")
    state = object.__getattribute__(corrupt, "__dict__")
    dict.__setitem__(state, "kind", HostileTag("NULL"))
    with pytest.raises(ValidationError):
        adapter.validate_python(corrupt)
    assert calls == []

    source = {"kind": "VALUE", "value": "next"}
    value = adapter.validate_python(source)
    source["kind"] = "NULL"
    source["value"] = "changed"
    assert type(value) is api.OpaqueCursorValueField
    assert value.model_dump() == {"kind": "VALUE", "value": "next"}
    copied = value.model_copy()
    assert copied == value and copied is not value
    assert value.model_dump_json() == '{"kind":"VALUE","value":"next"}'
