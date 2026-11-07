"""Deterministic, account-scoped OMS client-order identifiers."""

from __future__ import annotations

import re
from uuid import UUID

import pytest

from hmt_crypto.oms.client_order_id import make_client_order_id
from hmt_crypto.oms.models import (
    OmsAccountScope,
    OmsEnvironment,
    OmsExecutionMode,
    OmsRegion,
    OmsTransitionError,
)

_ACCOUNT_ID = UUID("00112233-4455-6677-8899-aabbccddeeff")
_COMMAND_ID = UUID("fedcba98-7654-3210-fedc-ba9876543210")
_MISSING = object()


def _account(**changes: object) -> OmsAccountScope:
    values: dict[str, object] = {
        "account_id": _ACCOUNT_ID,
        "external_user_id": "12345",
        "region": OmsRegion.INDIA,
        "environment": OmsEnvironment.TESTNET,
        "mode": OmsExecutionMode.PAPER,
        "credential_version": 1,
    }
    return OmsAccountScope(**(values | changes))


def _forged_uuid(slot_value: object = _MISSING) -> UUID:
    value = object.__new__(UUID)
    if slot_value is not _MISSING:
        UUID.int.__set__(value, slot_value)
    return value


def test_client_order_id_matches_the_hand_checked_v1_vector() -> None:
    """Changing canonical digest framing must fail the stable correlation vector."""
    assert make_client_order_id(_account(), _COMMAND_ID) == "HMC1_D5IJDONB4FTJWRY7QLSKS52XUEY"


def test_client_order_id_is_exactly_32_safe_ascii_characters() -> None:
    """A generated identifier must satisfy the local and Delta request contract."""
    value = make_client_order_id(_account(), _COMMAND_ID)
    assert len(value) == 32
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", value)


def test_client_order_id_is_repeatable_and_stable_across_credential_rotation() -> None:
    """Credential rotation must not destroy lookup identity for an existing command."""
    original = make_client_order_id(_account(credential_version=1), _COMMAND_ID)
    assert make_client_order_id(_account(credential_version=9), _COMMAND_ID) == original
    assert make_client_order_id(_account(credential_version=9), _COMMAND_ID) == original


def test_external_user_text_is_omitted_under_the_durable_account_binding_invariant() -> None:
    """The coordinator must immutably bind account UUID to Delta user before ID derivation."""
    baseline = make_client_order_id(_account(external_user_id="12345"), _COMMAND_ID)
    assert make_client_order_id(_account(external_user_id="67890"), _COMMAND_ID) == baseline


def test_nil_account_and_command_uuids_are_intentionally_valid_identities() -> None:
    """Nil UUID is a durable value here, not a missing-value sentinel."""
    scope = _account(account_id=UUID(int=0))
    assert scope.account_id == UUID(int=0)
    assert len(make_client_order_id(scope, UUID(int=0))) == 32


@pytest.mark.parametrize(
    "changes,command_id",
    [
        ({"account_id": UUID(int=2)}, _COMMAND_ID),
        ({"region": OmsRegion.GLOBAL}, _COMMAND_ID),
        ({"environment": OmsEnvironment.PROD}, _COMMAND_ID),
        ({"mode": OmsExecutionMode.LIVE}, _COMMAND_ID),
        ({}, UUID(int=3)),
    ],
)
def test_client_order_id_isolated_by_every_durable_scope_dimension(
    changes: dict[str, object],
    command_id: UUID,
) -> None:
    """Two durable scopes must not accidentally share a venue correlation key."""
    baseline = make_client_order_id(_account(), _COMMAND_ID)
    assert make_client_order_id(_account(**changes), command_id) != baseline


def test_client_order_id_sample_has_no_collisions_and_never_uses_attempt_number() -> None:
    """Stable command identity, not retry count or randomness, determines the value."""
    observed = {make_client_order_id(_account(), UUID(int=index)) for index in range(1, 10_001)}
    assert len(observed) == 10_000


def test_account_scope_rejects_external_user_ids_outside_signed_64_bit_range() -> None:
    """A digit-only but out-of-range venue user ID must not enter digest identity."""
    with pytest.raises(OmsTransitionError):
        _account(external_user_id="9223372036854775808")


def test_scope_rejects_reconstructed_enum_members_before_equality_or_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope identity accepts only captured enum singletons, never equal StrEnum objects."""
    calls: list[str] = []

    def forbidden_hash(value: object) -> int:
        del value
        calls.append("hash")
        raise AssertionError("scope-enum-callback")

    def forbidden_eq(left: object, right: object) -> bool:
        del left, right
        calls.append("eq")
        raise AssertionError("scope-enum-callback")

    cases = (
        (OmsRegion, "region", "india"),
        (OmsEnvironment, "environment", "testnet"),
        (OmsExecutionMode, "mode", "PAPER"),
    )
    for enum_type, field, member_literal in cases:
        for literal in (member_literal, "NOT_A_CANONICAL_MEMBER"):
            reconstructed = str.__new__(enum_type, literal)
            assert type(reconstructed) is enum_type
            calls.clear()
            with monkeypatch.context() as patch:
                patch.setattr(enum_type, "__hash__", forbidden_hash)
                patch.setattr(enum_type, "__eq__", forbidden_eq)
                with pytest.raises(OmsTransitionError):
                    _account(**{field: reconstructed})
            assert calls == []


def test_malformed_uuid_slots_fail_closed_without_callbacks_for_both_id_positions() -> None:
    """UUID copies must validate the captured integer slot before any UUID property access."""
    calls: list[str] = []

    class HostileSlot:
        def __getattribute__(self, name: str) -> object:
            calls.append(f"get:{name}")
            raise AssertionError("uuid-slot-secret-canary")

        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError("uuid-slot-secret-canary")

        def __hash__(self) -> int:
            calls.append("hash")
            raise AssertionError("uuid-slot-secret-canary")

        def __index__(self) -> int:
            calls.append("index")
            raise AssertionError("uuid-slot-secret-canary")

        def __repr__(self) -> str:
            calls.append("repr")
            raise AssertionError("uuid-slot-secret-canary")

    malformed = (
        _forged_uuid(),
        _forged_uuid(False),
        _forged_uuid(-1),
        _forged_uuid(2**128),
        _forged_uuid(HostileSlot()),
    )
    for value in malformed:
        operations = (
            lambda value=value: _account(account_id=value),
            lambda value=value: make_client_order_id(_account(), value),
        )
        for operation in operations:
            with pytest.raises(OmsTransitionError) as captured:
                operation()
            assert "uuid-slot-secret-canary" not in str(captured.value)
            assert "uuid-slot-secret-canary" not in repr(captured.value)
    assert calls == []


def test_client_order_id_rejects_subclasses_reconstruction_and_hostile_objects_safely() -> None:
    """Malformed identity inputs must fail before invoking caller-controlled hooks."""
    calls: list[str] = []

    class UuidChild(UUID):
        pass

    class Hostile:
        def __getattribute__(self, name: str) -> object:
            calls.append(f"get:{name}")
            raise AssertionError("client-id-secret-canary")

        def __repr__(self) -> str:
            calls.append("repr")
            raise AssertionError("client-id-secret-canary")

    forged = object.__new__(OmsAccountScope)
    values: tuple[tuple[object, object], ...] = (
        (Hostile(), _COMMAND_ID),
        (_account(), Hostile()),
        (_account(), UuidChild(int=4)),
        (forged, _COMMAND_ID),
    )
    for account, command_id in values:
        with pytest.raises(OmsTransitionError) as captured:
            make_client_order_id(account, command_id)
        assert "client-id-secret-canary" not in str(captured.value)
        assert "client-id-secret-canary" not in repr(captured.value)
    assert calls == []
