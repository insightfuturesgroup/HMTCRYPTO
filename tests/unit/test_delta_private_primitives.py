"""Strict private-boundary primitives reject coercion before caller callbacks."""

from __future__ import annotations

import ast
import copy
import importlib
import pickle
import subprocess
import sys
import textwrap
import warnings
from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from enum import Enum, StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError, create_model, field_validator
from pydantic_core import PydanticSerializationError

pytestmark = pytest.mark.unit

_MAX_I63 = 2**63 - 1


def private_api() -> SimpleNamespace:
    """Load the wished-for A1 API while keeping the initial RED collectable."""
    try:
        primitives = importlib.import_module("hmt_crypto.domain.private_primitives")
        routes = importlib.import_module("hmt_crypto.exchange.delta.private_routes")
    except ModuleNotFoundError:
        pytest.fail("A1 private primitives and route identity are missing")
    names = (
        "PrivateBoundaryModel",
        "PrivateId",
        "Contracts",
        "UnfilledContracts",
        "SignedContracts",
        "ExternalUserText",
        "ExactUuid",
        "CredentialVersion",
        "StrictEpoch",
        "StrictPositivePid",
        "StrictPositiveInt",
        "StrictNonNegativeInt",
        "Sha256Hex",
        "OmsClientOrderId",
        "ObservedDeltaClientOrderId",
        "ProductSymbol",
        "AssetSymbol",
        "ExactAwareUtcDatetime",
        "UnknownEnumToken",
        "OpaqueCursor",
        "PrivateDecimal",
        "PositivePrivateDecimal",
        "NonNegativePrivateDecimal",
        "NonZeroPrivateDecimal",
        "BoundedDiagnosticText",
        "parse_private_decimal_text",
        "parse_epoch_microseconds",
        "parse_position_iso_datetime",
    )
    try:
        values = {name: getattr(primitives, name) for name in names}
        values["PrivateRouteId"] = routes.PrivateRouteId
    except AttributeError:
        pytest.fail("A1 private primitive API is incomplete")
    return SimpleNamespace(**values)


def exact_enum_api() -> SimpleNamespace:
    """Load the binding-added canonical enum supplier API."""
    primitives = importlib.import_module("hmt_crypto.domain.private_primitives")
    names = (
        "ExactStrEnum",
        "ExactEnumField",
        "exact_enum_literal",
        "exact_enum_member_for_literal",
    )
    try:
        return SimpleNamespace(**{name: getattr(primitives, name) for name in names})
    except AttributeError:
        pytest.fail("A1 canonical exact-enum supplier is incomplete")


def capture_foreign_exact_enum(enum_type: object, manifest: tuple[tuple[str, str], ...]) -> None:
    """Capture one ordinary test enum through the package-private source API."""
    enum_module = importlib.import_module("hmt_crypto._private_enums")
    enum_module._capture_foreign_exact_enum(enum_type, manifest)


def validate(alias: object, value: object) -> object:
    api = private_api()
    probe = create_model(
        "PrivatePrimitiveProbe",
        __base__=api.PrivateBoundaryModel,
        value=(alias, ...),
    )
    return probe(value=value).value


@pytest.mark.parametrize(
    ("name", "valid_low", "valid_high", "invalid_low", "invalid_high"),
    [
        ("PrivateId", 1, _MAX_I63, 0, 2**63),
        ("Contracts", 1, _MAX_I63, 0, 2**63),
        ("UnfilledContracts", 0, _MAX_I63, -1, 2**63),
        ("SignedContracts", -_MAX_I63, _MAX_I63, -(2**63), 2**63),
        ("CredentialVersion", 1, _MAX_I63, 0, 2**63),
        ("StrictEpoch", 1, _MAX_I63, 0, 2**63),
        ("StrictPositiveInt", 1, _MAX_I63, 0, 2**63),
        ("StrictNonNegativeInt", 0, _MAX_I63, -1, 2**63),
        ("StrictPositivePid", 1, 2**31 - 1, 0, 2**31),
    ],
)
def test_integer_primitives_enforce_their_exact_independent_ranges(
    name: str,
    valid_low: int,
    valid_high: int,
    invalid_low: int,
    invalid_high: int,
) -> None:
    api = private_api()
    alias = getattr(api, name)
    assert validate(alias, valid_low) == valid_low
    assert validate(alias, valid_high) == valid_high
    for invalid in (invalid_low, invalid_high):
        with pytest.raises(ValidationError):
            validate(alias, invalid)


@pytest.mark.parametrize("raw", [True, False, 1.0, "1", Decimal("1")])
def test_integer_primitives_reject_coercion(raw: object) -> None:
    with pytest.raises(ValidationError):
        validate(private_api().PrivateId, raw)


def test_credential_version_and_epoch_have_distinct_owned_aliases() -> None:
    api = private_api()
    assert api.CredentialVersion is not api.StrictEpoch


def test_integer_subclass_is_rejected_without_comparison_callbacks() -> None:
    class HostileInt(int):
        def __lt__(self, other: object) -> bool:
            raise AssertionError("private-int-payload")

        def __gt__(self, other: object) -> bool:
            raise AssertionError("private-int-payload")

    with pytest.raises(ValidationError) as error:
        validate(private_api().PrivateId, HostileInt(1))
    assert "private-int-payload" not in str(error.value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", "1"),
        (str(_MAX_I63), str(_MAX_I63)),
    ],
)
def test_external_user_text_is_canonical_and_bounded(raw: str, expected: str) -> None:
    assert validate(private_api().ExternalUserText, raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "0", "01", "+1", "-1", " 1", "1 ", "١", str(2**63), 1, b"1"],
)
def test_external_user_text_rejects_noncanonical_or_out_of_range_values(raw: object) -> None:
    with pytest.raises(ValidationError):
        validate(private_api().ExternalUserText, raw)


def test_exact_uuid_accepts_only_builtin_uuid_objects() -> None:
    value = uuid4()
    owned = validate(private_api().ExactUuid, value)
    assert owned == value
    assert owned is not value
    for invalid in (str(value), value.bytes, 1):
        with pytest.raises(ValidationError):
            validate(private_api().ExactUuid, invalid)

    class HostileUuid(UUID):
        def __str__(self) -> str:
            raise AssertionError("private-uuid-payload")

    with pytest.raises(ValidationError) as error:
        validate(private_api().ExactUuid, HostileUuid(bytes=value.bytes))
    assert "private-uuid-payload" not in str(error.value)


@pytest.mark.parametrize(
    "corruption", ["int_subclass", "object", "negative", "too_large", "missing"]
)
def test_exact_uuid_owns_trusted_slot_data_and_rejects_corruption(
    corruption: str,
) -> None:
    """Catch exact UUID objects retaining hostile or missing internal slot data."""
    api = private_api()

    class HostileInt(int):
        def __int__(self) -> int:
            raise AssertionError("fixture-secret-uuid-int")

        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-uuid-int")

    class HostileValue:
        def __index__(self) -> int:
            raise AssertionError("fixture-secret-uuid-object")

        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-uuid-object")

    value = uuid4()
    if corruption == "int_subclass":
        object.__setattr__(value, "int", HostileInt(value.int))
    elif corruption == "object":
        object.__setattr__(value, "int", HostileValue())
    elif corruption == "negative":
        object.__setattr__(value, "int", -1)
    elif corruption == "too_large":
        object.__setattr__(value, "int", 2**128)
    else:
        object.__delattr__(value, "int")
    with pytest.raises(ValidationError) as captured:
        validate(api.ExactUuid, value)
    assert "fixture-secret" not in f"{captured.value!s} {captured.value!r}"
    assert all(detail.get("input") is None for detail in captured.value.errors())


def test_exact_uuid_model_copy_and_dump_revalidate_owned_slot_data() -> None:
    """Catch post-validation UUID slot corruption reaching copy or serializers."""
    api = private_api()
    model = create_model(
        "PrivateUuidStateProbe",
        __base__=api.PrivateBoundaryModel,
        value=(api.ExactUuid, ...),
    )
    caller = uuid4()
    instance = model(value=caller)
    assert instance.value == caller
    assert instance.value is not caller

    class HostileInt(int):
        def __int__(self) -> int:
            raise AssertionError("fixture-secret-uuid-state")

        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-uuid-state")

    object.__setattr__(instance.value, "int", HostileInt(instance.value.int))
    for operation in (instance.model_copy, instance.model_dump, instance.model_dump_json):
        with pytest.raises(ValidationError) as captured:
            operation()
        assert "fixture-secret" not in f"{captured.value!s} {captured.value!r}"
        assert all(detail.get("input") is None for detail in captured.value.errors())


def test_exact_datetime_requires_builtin_zero_offset_and_normalizes_to_utc() -> None:
    api = private_api()
    value = datetime(2026, 11, 5, 12, 30, 15, 123456, tzinfo=timezone(timedelta(0), "GMT"))
    result = validate(api.ExactAwareUtcDatetime, value)
    assert result == datetime(2026, 11, 5, 12, 30, 15, 123456, tzinfo=UTC)
    assert result.tzinfo is UTC

    for invalid in (
        datetime(2026, 11, 5),
        datetime(2026, 11, 5, tzinfo=timezone(timedelta(hours=1))),
        "2026-11-05T00:00:00Z",
        1_762_300_800,
    ):
        with pytest.raises(ValidationError):
            validate(api.ExactAwareUtcDatetime, invalid)


def test_datetime_and_timezone_subclasses_are_rejected_before_callbacks() -> None:
    class HostileDateTime(datetime):
        def utcoffset(self) -> timedelta | None:
            raise AssertionError("private-datetime-payload")

    class HostileZone(tzinfo):
        def utcoffset(self, value: datetime | None) -> timedelta | None:
            raise AssertionError("private-zone-payload")

    values = (
        HostileDateTime(2026, 11, 5, tzinfo=UTC),
        datetime(2026, 11, 5, tzinfo=HostileZone()),
    )
    for value in values:
        with pytest.raises(ValidationError) as error:
            validate(private_api().ExactAwareUtcDatetime, value)
        assert "private-" not in str(error.value)


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("Sha256Hex", "0123456789abcdef" * 4),
        ("OmsClientOrderId", "Oms_ABC-123"),
        ("ObservedDeltaClientOrderId", "observed.id:1-2_3"),
        ("ProductSymbol", "BTC-USDT:PERP.v1"),
        ("AssetSymbol", "USDC.e"),
        ("UnknownEnumToken", "new_state:v2"),
        ("OpaqueCursor", "opaque%2Fcursor=="),
        ("BoundedDiagnosticText", "bounded diagnostic — no control chars"),
    ],
)
def test_string_primitives_accept_only_their_owned_grammars(name: str, valid: str) -> None:
    assert validate(getattr(private_api(), name), valid) == valid


@pytest.mark.parametrize(
    ("name", "invalid"),
    [
        ("Sha256Hex", "A" * 64),
        ("Sha256Hex", "a" * 63),
        ("OmsClientOrderId", "dot.not.allowed"),
        ("OmsClientOrderId", "x" * 33),
        ("ObservedDeltaClientOrderId", "slash/not/allowed"),
        ("ObservedDeltaClientOrderId", ""),
        ("ProductSymbol", "BTC/USDT"),
        ("ProductSymbol", "BTC%2FUSDT"),
        ("AssetSymbol", "x" * 33),
        ("UnknownEnumToken", "not printable\n"),
        ("UnknownEnumToken", "x" * 65),
        ("OpaqueCursor", "contains space"),
        ("OpaqueCursor", "line\nbreak"),
        ("OpaqueCursor", "x" * 1025),
        ("BoundedDiagnosticText", "line\nbreak"),
        ("BoundedDiagnosticText", "x" * 257),
    ],
)
def test_string_primitives_reject_values_outside_their_grammar(name: str, invalid: object) -> None:
    with pytest.raises(ValidationError):
        validate(getattr(private_api(), name), invalid)


def test_broader_observed_client_id_cannot_satisfy_oms_identity() -> None:
    api = private_api()
    observed = validate(api.ObservedDeltaClientOrderId, "observed.id:1")
    with pytest.raises(ValidationError):
        validate(api.OmsClientOrderId, observed)


def test_hostile_string_subclass_is_rejected_without_callbacks_or_secret_leak() -> None:
    class HostileStr(str):
        def __str__(self) -> str:
            raise AssertionError("fixture-secret-private-string")

        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-private-string")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-private-string")

        def __hash__(self) -> int:
            raise AssertionError("fixture-secret-private-string")

    with pytest.raises(ValidationError) as error:
        validate(private_api().OmsClientOrderId, HostileStr("safe"))
    assert "fixture-secret-private-string" not in str(error.value)
    assert all(detail.get("input") is None for detail in error.value.errors())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("0"), Decimal("0")),
        (Decimal("-12.345000"), Decimal("-12.345")),
        (
            Decimal("99999999999999999999.999999999999999999"),
            Decimal("99999999999999999999.999999999999999999"),
        ),
        (Decimal("1E+2"), Decimal("100")),
    ],
)
def test_private_decimal_is_exact_canonical_38_18(raw: Decimal, expected: Decimal) -> None:
    assert validate(private_api().PrivateDecimal, raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        1,
        1.0,
        "1",
        True,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("100000000000000000000"),
        Decimal("0.0000000000000000001"),
        Decimal("1e1000000"),
        Decimal("1e-1000000"),
    ],
)
def test_private_decimal_rejects_coercion_nonfinite_and_38_18_loss(raw: object) -> None:
    with pytest.raises(ValidationError):
        validate(private_api().PrivateDecimal, raw)


def test_decimal_subclass_is_rejected_before_numeric_callbacks() -> None:
    class HostileDecimal(Decimal):
        def is_finite(self) -> bool:
            raise AssertionError("private-decimal-payload")

    with pytest.raises(ValidationError) as error:
        validate(private_api().PrivateDecimal, HostileDecimal("1.2"))
    assert "private-decimal-payload" not in str(error.value)


@pytest.mark.parametrize(
    ("name", "accepted", "rejected"),
    [
        ("PositivePrivateDecimal", Decimal("0.000000000000000001"), Decimal("0")),
        ("NonNegativePrivateDecimal", Decimal("0"), Decimal("-0.000000000000000001")),
        ("NonZeroPrivateDecimal", Decimal("-1"), Decimal("0")),
    ],
)
def test_decimal_sign_aliases_are_field_specific(
    name: str, accepted: Decimal, rejected: Decimal
) -> None:
    alias = getattr(private_api(), name)
    assert validate(alias, accepted) == accepted
    with pytest.raises(ValidationError):
        validate(alias, rejected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", Decimal("0")),
        ("-1.2300", Decimal("-1.23")),
        (
            "99999999999999999999.999999999999999999",
            Decimal("99999999999999999999.999999999999999999"),
        ),
    ],
)
def test_private_decimal_text_parser_accepts_bounded_non_exponent_text(
    raw: str, expected: Decimal
) -> None:
    assert private_api().parse_private_decimal_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " 1",
        "1 ",
        "+1",
        "01",
        ".1",
        "1.",
        "1e2",
        "NaN",
        "100000000000000000000",
        "0.0000000000000000001",
        1,
        b"1",
    ],
)
def test_private_decimal_text_parser_rejects_noncanonical_or_lossy_values(raw: object) -> None:
    with pytest.raises(ValueError, match="private decimal"):
        private_api().parse_private_decimal_text(raw)


def test_epoch_microseconds_has_one_explicit_utc_unit_and_range() -> None:
    parse = private_api().parse_epoch_microseconds
    assert parse("946684800000000") == datetime(2000, 1, 1, tzinfo=UTC)
    assert parse("4102444799999999") == datetime(2099, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)
    for invalid in (
        "946684799999999",
        "4102444800000000",
        "01762300800000000",
        "+1762300800000000",
        "1762300800",
        1_762_300_800_000_000,
        True,
    ):
        with pytest.raises(ValueError, match="epoch microseconds"):
            parse(invalid)


def test_position_iso_parser_requires_aware_bounded_text_and_normalizes_utc() -> None:
    parse = private_api().parse_position_iso_datetime
    assert parse("2000-01-01T05:30:00+05:30") == datetime(2000, 1, 1, tzinfo=UTC)
    assert parse("2099-12-31T23:59:59.999999Z") == datetime(
        2099, 12, 31, 23, 59, 59, 999999, tzinfo=UTC
    )
    for invalid in (
        "1999-12-31T23:59:59Z",
        "2100-01-01T00:00:00Z",
        "2026-11-05T00:00:00",
        " 2026-11-05T00:00:00Z",
        1_762_300_800,
    ):
        with pytest.raises(ValueError, match="position timestamp"):
            parse(invalid)


@pytest.mark.parametrize(
    "raw",
    [
        "2026-11-05 00:00:00Z",
        "2026-11-05t00:00:00Z",
        "2026-11-05T00:00:00z",
        "2026-11-05T00:00:00Z+00:00",
        "2026-11-05T00:00:00+05:30:00",
        "2026-11-05T00:00:00+14:01",
        "2026-11-05T00:00:00-14:01",
        "2026-11-05T00:00:00+15:00",
        "2026-11-05T00:00:00-15:00",
        "2026-11-05T00:00:00,123456Z",
        "2026-11-05T00:00:00.1234567Z",
        "2026-11-05T00:00:00\x00Z",
        "2026‐11‐05T00:00:00Z",
        "2026-11-05T00:00:00Z ",
    ],
)
def test_position_iso_parser_rejects_nonliteral_or_out_of_profile_forms(raw: str) -> None:
    with pytest.raises(ValueError, match="position timestamp"):
        private_api().parse_position_iso_datetime(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "2026-11-05T14:00:00+14:00",
            datetime(2026, 11, 5, tzinfo=UTC),
        ),
        (
            "2026-11-05T00:00:00-14:00",
            datetime(2026, 11, 5, 14, 0, tzinfo=UTC),
        ),
        (
            "2026-11-05T12:30:15.1+00:00",
            datetime(2026, 11, 5, 12, 30, 15, 100000, tzinfo=UTC),
        ),
    ],
)
def test_position_iso_parser_accepts_literal_offset_boundaries(
    raw: str, expected: datetime
) -> None:
    assert private_api().parse_position_iso_datetime(raw) == expected


def test_private_boundary_model_is_frozen_revalidated_and_owns_copies() -> None:
    api = private_api()
    model = create_model(
        "PrivateOwnershipProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, ...),
        values=(tuple[api.StrictPositiveInt, ...], ...),
    )
    source = (1, 2)
    value = model(identifier=7, values=source)
    assert value.values == source
    assert value.values is not source
    with pytest.raises(ValidationError, match="frozen"):
        value.identifier = 8
    with pytest.raises(ValidationError):
        value.model_copy(update={"identifier": "8"})
    copied = copy.deepcopy(value)
    assert copied == value
    assert copied is not value
    revalidated = model.model_validate(value)
    assert revalidated == value
    assert revalidated is not value


def test_private_model_root_accepts_only_exact_dict_or_exact_concrete_instance() -> None:
    api = private_api()
    model = create_model(
        "PrivateRootProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, 7),
    )
    direct = model(identifier=8)
    assert direct.identifier == 8
    from_dict = model.model_validate({"identifier": 9})
    assert from_dict.identifier == 9
    revalidated = model.model_validate(direct)
    assert revalidated == direct
    assert revalidated is not direct
    copied = direct.model_copy()
    assert copied == direct
    assert copied is not direct

    class ModelSubclass(model):
        pass

    subclassed = ModelSubclass(identifier=10)
    with pytest.raises(ValidationError) as error:
        model.model_validate(subclassed)
    assert all(detail.get("input") is None for detail in error.value.errors())

    for invalid in (object(), None, [], (), "fixture-secret-root", 7):
        with pytest.raises(ValidationError) as error:
            model.model_validate(invalid)
        assert "fixture-secret-root" not in str(error.value)
        assert all(detail.get("input") is None for detail in error.value.errors())


def test_private_model_root_rejects_hostile_containers_and_keys_before_callbacks() -> None:
    api = private_api()
    model = create_model(
        "PrivateHostileRootProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, 7),
    )

    class HostileDict(dict[str, object]):
        def __iter__(self) -> Iterator[str]:
            raise AssertionError("fixture-secret-root-dict")

        def __len__(self) -> int:
            raise AssertionError("fixture-secret-root-dict")

        def keys(self) -> object:
            raise AssertionError("fixture-secret-root-dict")

        def get(self, key: str, default: object = None) -> object:
            raise AssertionError("fixture-secret-root-dict")

    class HostileMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise AssertionError("fixture-secret-root-mapping")

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("fixture-secret-root-mapping")

        def __len__(self) -> int:
            raise AssertionError("fixture-secret-root-mapping")

    for invalid in (HostileDict(identifier=8), HostileMapping()):
        with pytest.raises(ValidationError) as error:
            model.model_validate(invalid)
        assert "fixture-secret-root" not in str(error.value)
        assert all(detail.get("input") is None for detail in error.value.errors())

    class HostileKey(str):
        armed = False

        def __hash__(self) -> int:
            if self.armed:
                raise AssertionError("fixture-secret-root-key")
            return super().__hash__()

        def __eq__(self, other: object) -> bool:
            if self.armed:
                raise AssertionError("fixture-secret-root-key")
            return super().__eq__(other)

    key = HostileKey("identifier")
    invalid_dict: dict[object, object] = {key: 8}
    key.armed = True
    with pytest.raises(ValidationError) as error:
        model.model_validate(invalid_dict)
    assert "fixture-secret-root-key" not in str(error.value)
    assert all(detail.get("input") is None for detail in error.value.errors())


def test_private_model_root_rules_cover_json_strings_and_all_default_models() -> None:
    api = private_api()
    default_model = create_model(
        "PrivateDefaultRootProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, 7),
    )
    assert default_model.model_validate_json("{}").identifier == 7
    for invalid_json in (
        "[]",
        "null",
        '"fixture-secret-json-root"',
        "7",
    ):
        with pytest.raises(ValidationError) as error:
            default_model.model_validate_json(invalid_json)
        assert "fixture-secret-json-root" not in str(error.value)
        assert all(detail.get("input") is None for detail in error.value.errors())

    text_model = create_model(
        "PrivateStringsRootProbe",
        __base__=api.PrivateBoundaryModel,
        client_id=(api.OmsClientOrderId, ...),
    )
    assert text_model.model_validate_strings({"client_id": "safe_1"}).client_id == "safe_1"

    class HostileStrings(dict[str, str]):
        def __iter__(self) -> Iterator[str]:
            raise AssertionError("fixture-secret-strings-root")

        def __len__(self) -> int:
            raise AssertionError("fixture-secret-strings-root")

        def items(self) -> object:
            raise AssertionError("fixture-secret-strings-root")

    with pytest.raises(ValidationError) as error:
        text_model.model_validate_strings(HostileStrings(client_id="safe_1"))
    assert "fixture-secret-strings-root" not in str(error.value)
    assert all(detail.get("input") is None for detail in error.value.errors())


def test_private_model_validate_strings_owns_root_before_pydantic_callbacks() -> None:
    api = private_api()
    model = create_model(
        "PrivateOwnedStringsProbe",
        __base__=api.PrivateBoundaryModel,
        client_id=(api.OmsClientOrderId, ...),
    )
    assert model.model_validate_strings({"client_id": "safe_1"}).client_id == "safe_1"
    assert model.model_validate_strings({"client_id": "safe_1"}, strict=True).client_id == "safe_1"

    class HostilePrimitive:
        def __getattribute__(self, name: str) -> object:
            if name.startswith("__"):
                raise AssertionError("fixture-secret-strings-primitive")
            return object.__getattribute__(self, name)

    class HostileDict(dict[str, str]):
        def __iter__(self) -> Iterator[str]:
            raise AssertionError("fixture-secret-strings-dict")

        def __len__(self) -> int:
            raise AssertionError("fixture-secret-strings-dict")

        def items(self) -> object:
            raise AssertionError("fixture-secret-strings-dict")

        def keys(self) -> object:
            raise AssertionError("fixture-secret-strings-dict")

        def get(self, key: str, default: object = None) -> object:
            raise AssertionError("fixture-secret-strings-dict")

    class HostileMapping(Mapping[str, str]):
        def __getitem__(self, key: str) -> str:
            raise AssertionError("fixture-secret-strings-mapping")

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("fixture-secret-strings-mapping")

        def __len__(self) -> int:
            raise AssertionError("fixture-secret-strings-mapping")

    for invalid in (
        HostilePrimitive(),
        HostileDict(client_id="safe_1"),
        HostileMapping(),
    ):
        for strict in (None, True):
            with pytest.raises(ValidationError) as error:
                if strict is None:
                    model.model_validate_strings(invalid)
                else:
                    model.model_validate_strings(invalid, strict=strict)
            assert "fixture-secret-strings" not in str(error.value)
            assert all(detail.get("input") is None for detail in error.value.errors())

    class HostileKey(str):
        armed = False

        def __hash__(self) -> int:
            if self.armed:
                raise AssertionError("fixture-secret-strings-key")
            return super().__hash__()

        def __eq__(self, other: object) -> bool:
            if self.armed:
                raise AssertionError("fixture-secret-strings-key")
            return super().__eq__(other)

    key = HostileKey("client_id")
    hostile_key_root: dict[object, str] = {key: "safe_1"}
    key.armed = True
    with pytest.raises(ValidationError) as error:
        model.model_validate_strings(hostile_key_root)
    assert "fixture-secret-strings-key" not in str(error.value)
    assert all(detail.get("input") is None for detail in error.value.errors())


def test_private_model_validate_strings_sanitizes_error_inputs_and_context() -> None:
    api = private_api()
    canary = "fixture-secret-strings-context"

    class LeakyStringsProbe(api.PrivateBoundaryModel):
        client_id: str

        @field_validator("client_id")
        @classmethod
        def reject_with_caller_value(cls, value: str) -> str:
            raise ValueError(f"rejected caller value {value}")

    for strict in (None, True):
        with pytest.raises(ValidationError) as error:
            if strict is None:
                LeakyStringsProbe.model_validate_strings({"client_id": canary})
            else:
                LeakyStringsProbe.model_validate_strings({"client_id": canary}, strict=strict)
        assert canary not in str(error.value)
        assert canary not in repr(error.value)
        for detail in error.value.errors():
            assert detail.get("input") is None
            assert canary not in repr(detail.get("ctx"))


@pytest.mark.parametrize("entry", ["python", "json", "strings"])
@pytest.mark.parametrize(
    ("control", "invalid"),
    [
        ("extra", "ignore"),
        ("extra", "allow"),
        ("strict", False),
        ("strict", 0),
        ("context", {}),
        ("by_alias", True),
        ("by_name", False),
    ],
)
def test_private_model_validation_controls_cannot_weaken_the_boundary(
    entry: str, control: str, invalid: object
) -> None:
    """Catch caller controls bypassing strict, extra-forbid model admission."""
    api = private_api()
    model = create_model(
        "PrivateValidationControlProbe",
        __base__=api.PrivateBoundaryModel,
        client_id=(api.OmsClientOrderId, ...),
    )
    kwargs = {control: invalid}
    with pytest.raises(ValidationError) as captured:
        if entry == "python":
            model.model_validate({"client_id": "safe_1"}, **kwargs)
        elif entry == "json":
            model.model_validate_json('{"client_id":"safe_1"}', **kwargs)
        else:
            model.model_validate_strings({"client_id": "safe_1"}, **kwargs)
    details = captured.value.errors(include_url=False)
    assert details == [
        {
            "type": "private_model_controls",
            "loc": (),
            "msg": "invalid private model controls",
            "input": None,
        }
    ]


def test_private_model_validation_controls_reject_hostile_values_without_callbacks() -> None:
    """Catch pre-validator control inspection invoking caller callbacks."""
    api = private_api()
    model = create_model(
        "PrivateHostileValidationControlProbe",
        __base__=api.PrivateBoundaryModel,
        client_id=(api.OmsClientOrderId, ...),
    )

    class HostileControl:
        def __bool__(self) -> bool:
            raise AssertionError("fixture-secret-validation-control-bool")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-validation-control-eq")

        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-validation-control-repr")

    calls = (
        lambda: model.model_validate({"client_id": "safe_1"}, context=HostileControl()),
        lambda: model.model_validate_json('{"client_id":"safe_1"}', extra=HostileControl()),
        lambda: model.model_validate_strings({"client_id": "safe_1"}, strict=HostileControl()),
    )
    for call in calls:
        with pytest.raises(ValidationError) as captured:
            call()
        assert captured.value.errors(include_url=False)[0]["type"] == ("private_model_controls")
        assert "fixture-secret" not in f"{captured.value!s} {captured.value!r}"


def test_private_model_validation_controls_allow_only_explicit_safe_values() -> None:
    """Keep supported explicit controls equivalent to strict defaults."""
    api = private_api()
    model = create_model(
        "PrivateSafeValidationControlProbe",
        __base__=api.PrivateBoundaryModel,
        client_id=(api.OmsClientOrderId, ...),
    )
    assert (
        model.model_validate(
            {"client_id": "safe_1"},
            strict=True,
            extra="forbid",
            from_attributes=False,
        ).client_id
        == "safe_1"
    )
    assert (
        model.model_validate_json('{"client_id":"safe_1"}', strict=True, extra="forbid").client_id
        == "safe_1"
    )
    assert (
        model.model_validate_strings({"client_id": "safe_1"}, strict=True, extra="forbid").client_id
        == "safe_1"
    )


@pytest.mark.parametrize(
    "raw",
    [
        '{"identifier":"fixture-secret-malformed-json',
        b'{"identifier":"fixture-secret-malformed-json',
        bytearray(b'{"identifier":"fixture-secret-malformed-json'),
    ],
)
def test_private_model_malformed_json_errors_discard_raw_buffers(raw: object) -> None:
    api = private_api()
    model = create_model(
        "PrivateMalformedJsonProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, 7),
    )
    with pytest.raises(ValidationError) as error:
        model.model_validate_json(raw)
    assert "fixture-secret-malformed-json" not in str(error.value)
    assert all(detail.get("input") is None for detail in error.value.errors())


def test_private_model_copy_rejects_hostile_update_containers_before_callbacks() -> None:
    api = private_api()
    model = create_model(
        "PrivateCopyProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, ...),
    )
    value = model(identifier=7)

    class HostileMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise AssertionError("fixture-secret-copy-container")

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("fixture-secret-copy-container")

        def __len__(self) -> int:
            raise AssertionError("fixture-secret-copy-container")

    with pytest.raises(TypeError) as error:
        value.model_copy(update=HostileMapping())
    assert "fixture-secret-copy-container" not in str(error.value)

    class HostileKey(str):
        def __hash__(self) -> int:
            return hash("identifier")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-copy-key")

    with pytest.raises(TypeError) as error:
        value.model_copy(update={HostileKey("identifier"): 8})
    assert "fixture-secret-copy-key" not in str(error.value)


def _corrupt_private_model_state(value: object, corruption: str) -> None:
    class HostileDict(dict[object, object]):
        def __iter__(self) -> Iterator[object]:
            raise AssertionError("fixture-secret-corrupt-dict")

        def __len__(self) -> int:
            raise AssertionError("fixture-secret-corrupt-dict")

        def items(self) -> object:
            raise AssertionError("fixture-secret-corrupt-dict")

        def copy(self) -> object:
            raise AssertionError("fixture-secret-corrupt-dict")

        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise AssertionError("fixture-secret-corrupt-dict")

    class HostileSet(set[object]):
        def __iter__(self) -> Iterator[object]:
            raise AssertionError("fixture-secret-corrupt-set")

        def __len__(self) -> int:
            raise AssertionError("fixture-secret-corrupt-set")

        def copy(self) -> object:
            raise AssertionError("fixture-secret-corrupt-set")

        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise AssertionError("fixture-secret-corrupt-set")

    class HostileKey(str):
        armed = False

        def __hash__(self) -> int:
            if self.armed:
                raise AssertionError("fixture-secret-corrupt-key")
            return super().__hash__()

        def __eq__(self, other: object) -> bool:
            if self.armed:
                raise AssertionError("fixture-secret-corrupt-key")
            return super().__eq__(other)

        def __str__(self) -> str:
            if self.armed:
                raise AssertionError("fixture-secret-corrupt-key")
            return super().__str__()

        def __repr__(self) -> str:
            if self.armed:
                raise AssertionError("fixture-secret-corrupt-key")
            return super().__repr__()

    class HostileValue:
        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-corrupt-value")

        def __str__(self) -> str:
            raise AssertionError("fixture-secret-corrupt-value")

    if corruption == "state_dict_subclass":
        object.__setattr__(value, "__dict__", HostileDict(identifier=7))
    elif corruption == "state_hostile_key":
        key = HostileKey("identifier")
        state: dict[object, object] = {key: 7}
        key.armed = True
        object.__setattr__(value, "__dict__", state)
    elif corruption == "state_unknown_key":
        object.__setattr__(
            value,
            "__dict__",
            {
                "identifier": 7,
                "fixture-secret-corrupt-unknown": HostileValue(),
            },
        )
    elif corruption == "state_missing_field":
        object.__setattr__(value, "__dict__", {})
    elif corruption == "fields_set_subclass":
        object.__setattr__(value, "__pydantic_fields_set__", HostileSet({"identifier"}))
    elif corruption == "fields_set_hostile_key":
        key = HostileKey("identifier")
        fields_set: set[object] = {key}
        key.armed = True
        object.__setattr__(value, "__pydantic_fields_set__", fields_set)
    elif corruption == "fields_set_unknown_key":
        object.__setattr__(
            value,
            "__pydantic_fields_set__",
            {"identifier", "fixture-secret-corrupt-unknown"},
        )
    elif corruption == "fields_set_missing_required":
        object.__setattr__(value, "__pydantic_fields_set__", set())
    elif corruption == "extra_dict":
        object.__setattr__(value, "__pydantic_extra__", {})
    elif corruption == "extra_dict_subclass":
        object.__setattr__(value, "__pydantic_extra__", HostileDict())
    elif corruption == "private_dict":
        object.__setattr__(value, "__pydantic_private__", {})
    elif corruption == "private_dict_subclass":
        object.__setattr__(value, "__pydantic_private__", HostileDict())
    else:  # pragma: no cover - test table controls this helper
        raise AssertionError("unknown test corruption")


@pytest.mark.parametrize(
    "entry",
    [
        "model_validate",
        "model_copy",
        "copy",
        "deepcopy",
        "model_dump",
        "model_dump_json",
    ],
)
@pytest.mark.parametrize(
    "corruption",
    [
        "state_dict_subclass",
        "state_hostile_key",
        "state_unknown_key",
        "state_missing_field",
        "fields_set_subclass",
        "fields_set_hostile_key",
        "fields_set_unknown_key",
        "fields_set_missing_required",
        "extra_dict",
        "extra_dict_subclass",
        "private_dict",
        "private_dict_subclass",
    ],
)
def test_private_model_integrity_precedes_validation_copy_and_serialization_callbacks(
    entry: str, corruption: str
) -> None:
    api = private_api()
    model = create_model(
        "PrivateCorruptStateProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, ...),
    )
    value = model(identifier=7)
    _corrupt_private_model_state(value, corruption)

    with pytest.raises(ValidationError) as error:
        if entry == "model_validate":
            model.model_validate(value)
        elif entry == "model_copy":
            value.model_copy()
        elif entry == "copy":
            copy.copy(value)
        elif entry == "deepcopy":
            copy.deepcopy(value)
        elif entry == "model_dump":
            value.model_dump()
        elif entry == "model_dump_json":
            value.model_dump_json()
        else:  # pragma: no cover - test table controls this branch
            raise AssertionError("unknown test entry")
    rendered = f"{error.value!s} {error.value!r} {error.value.errors()!r}"
    assert "fixture-secret-corrupt" not in rendered
    assert all(detail.get("input") is None for detail in error.value.errors())


def test_private_model_valid_state_supports_all_guarded_entry_paths() -> None:
    api = private_api()
    model = create_model(
        "PrivateValidStateProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, ...),
        label=(api.OmsClientOrderId, "default_label"),
    )
    value = model(identifier=7)
    assert model.model_validate(value) == value
    assert value.model_copy() == value
    assert copy.copy(value) == value
    assert copy.deepcopy(value) == value
    assert value.model_dump() == {"identifier": 7, "label": "default_label"}
    assert value.model_dump_json() == '{"identifier":7,"label":"default_label"}'


@pytest.mark.parametrize("entry", ["model_copy", "model_dump", "model_dump_json"])
def test_private_model_integrity_guard_precedes_instance_method_lookup(entry: str) -> None:
    api = private_api()
    model = create_model(
        "PrivateCorruptLookupProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, ...),
    )
    value = model(identifier=7)

    class HostileEntryKey(str):
        armed = False

        def __hash__(self) -> int:
            if self.armed:
                raise AssertionError("fixture-secret-corrupt-entry-key")
            return super().__hash__()

        def __eq__(self, other: object) -> bool:
            if self.armed:
                raise AssertionError("fixture-secret-corrupt-entry-key")
            return super().__eq__(other)

    key = HostileEntryKey(entry)
    state: dict[object, object] = {key: object()}
    key.armed = True
    object.__setattr__(value, "__dict__", state)

    with pytest.raises(ValidationError) as error:
        method = getattr(value, entry)
        method()
    rendered = f"{error.value!s} {error.value!r} {error.value.errors()!r}"
    assert "fixture-secret-corrupt" not in rendered
    assert all(detail.get("input") is None for detail in error.value.errors())


@pytest.mark.parametrize("entry", ["__copy__", "__deepcopy__"])
def test_private_model_integrity_guard_precedes_copy_protocol_lookup(entry: str) -> None:
    api = private_api()
    model = create_model(
        "PrivateCorruptCopyLookupProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, ...),
    )
    value = model(identifier=7)

    class HostileEntryKey(str):
        armed = False

        def __hash__(self) -> int:
            if self.armed:
                raise AssertionError("fixture-secret-corrupt-copy-key")
            return super().__hash__()

        def __eq__(self, other: object) -> bool:
            if self.armed:
                raise AssertionError("fixture-secret-corrupt-copy-key")
            return super().__eq__(other)

    key = HostileEntryKey(entry)
    state: dict[object, object] = {"identifier": 7, key: object()}
    key.armed = True
    object.__setattr__(value, "__dict__", state)

    with pytest.raises(ValidationError) as lookup_error:
        getattr(value, entry)
    assert "fixture-secret-corrupt" not in str(lookup_error.value)

    with pytest.raises(ValidationError) as error:
        if entry == "__copy__":
            copy.copy(value)
        else:
            copy.deepcopy(value)
    rendered = f"{error.value!s} {error.value!r} {error.value.errors()!r}"
    assert "fixture-secret-corrupt" not in rendered
    assert all(detail.get("input") is None for detail in error.value.errors())


@pytest.mark.parametrize(
    "entry",
    ["model_dump", "model_dump_json", "model_copy", "copy", "deepcopy"],
)
@pytest.mark.parametrize("corruption", ["hostile_object", "exact_scalar_subclass"])
def test_private_model_revalidates_corrupt_field_values_before_dump_or_copy(
    entry: str, corruption: str
) -> None:
    api = private_api()
    model = create_model(
        "PrivateCorruptValueProbe",
        __base__=api.PrivateBoundaryModel,
        identifier=(api.PrivateId, ...),
    )
    value = model(identifier=7)

    class HostileValue:
        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-corrupt-value")

        def __str__(self) -> str:
            raise AssertionError("fixture-secret-corrupt-value")

        def __int__(self) -> int:
            raise AssertionError("fixture-secret-corrupt-value")

        def __index__(self) -> int:
            raise AssertionError("fixture-secret-corrupt-value")

    class HostileInt(int):
        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-corrupt-int")

        def __str__(self) -> str:
            raise AssertionError("fixture-secret-corrupt-int")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-corrupt-int")

        def __lt__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-corrupt-int")

        def __gt__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-corrupt-int")

    corrupt_value = HostileValue() if corruption == "hostile_object" else HostileInt(7)
    value.__dict__["identifier"] = corrupt_value

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with pytest.raises(ValidationError) as error:
            if entry == "model_dump":
                value.model_dump()
            elif entry == "model_dump_json":
                value.model_dump_json()
            elif entry == "model_copy":
                value.model_copy()
            elif entry == "copy":
                copy.copy(value)
            else:
                copy.deepcopy(value)
    assert captured == []
    rendered = f"{error.value!s} {error.value!r} {error.value.errors()!r}"
    assert "fixture-secret-corrupt" not in rendered
    assert all(detail.get("input") is None for detail in error.value.errors())


def test_private_boundary_errors_hide_raw_secret_inputs() -> None:
    secret = "fixture-secret-private-boundary"
    with pytest.raises(ValidationError) as error:
        validate(private_api().ExternalUserText, secret)
    assert secret not in str(error.value)
    assert secret not in repr(error.value)
    assert all(detail.get("input") is None for detail in error.value.errors())


def test_exact_str_enum_accepts_every_canonical_member_and_no_raw_text_mode() -> None:
    api = exact_enum_api()

    class Synthetic(api.ExactStrEnum):
        ALPHA = "alpha"
        BETA = "beta"

    adapter = TypeAdapter(Synthetic)
    model = create_model(
        "PrivateSyntheticEnumProbe",
        __base__=private_api().PrivateBoundaryModel,
        value=(Synthetic, ...),
    )
    for member, literal in ((Synthetic.ALPHA, "alpha"), (Synthetic.BETA, "beta")):
        assert adapter.validate_python(member) is member
        assert adapter.dump_python(member) is member
        assert adapter.dump_json(member) == f'"{literal}"'.encode()
        assert model(value=member).value is member
        assert model(value=member).model_dump()["value"] is member
        assert model(value=member).model_dump_json() == f'{{"value":"{literal}"}}'

    for raw in ("alpha", type("TextSubclass", (str,), {})("alpha")):
        with pytest.raises(ValidationError):
            adapter.validate_python(raw)
        with pytest.raises(ValidationError):
            model(value=raw)
    with pytest.raises(ValidationError):
        adapter.validate_json('"alpha"')
    with pytest.raises(ValidationError):
        model.model_validate_json('{"value":"alpha"}')
    with pytest.raises(ValidationError):
        model.model_validate_strings({"value": "alpha"})


def test_exact_str_enum_rejects_foreign_subclass_and_exact_class_forgeries() -> None:
    api = exact_enum_api()

    class Empty(api.ExactStrEnum):
        pass

    with pytest.raises(TypeError, match="exact enum"):

        class EmptyChild(Empty):
            CHILD = "child"

    class Other(api.ExactStrEnum):
        MEMBER = "member"

    class Foreign(StrEnum):
        MEMBER = "member"

    adapter = TypeAdapter(Empty)
    forged = str.__new__(Empty, "forged")
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", "forged")
    for invalid in (forged, Other.MEMBER, Foreign.MEMBER, "forged"):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
        with pytest.raises(PydanticSerializationError):
            adapter.dump_python(invalid)
        with pytest.raises(PydanticSerializationError):
            adapter.dump_json(invalid)


def test_exact_str_enum_never_calls_incoming_callbacks_or_missing_hook() -> None:
    api = exact_enum_api()

    class CallbackEnum(api.ExactStrEnum):
        SAFE = "safe"

        def __eq__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-enum-equality")

        def __hash__(self) -> int:
            raise AssertionError("fixture-secret-enum-hash")

        def __str__(self) -> str:
            raise AssertionError("fixture-secret-enum-string")

        @classmethod
        def _missing_(cls, value: object) -> object:
            raise AssertionError("fixture-secret-enum-missing")

    adapter = TypeAdapter(CallbackEnum)
    assert adapter.validate_python(CallbackEnum.SAFE) is CallbackEnum.SAFE
    assert adapter.dump_json(CallbackEnum.SAFE) == b'"safe"'

    forged = str.__new__(CallbackEnum, "safe")
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", "safe")
    with pytest.raises(ValidationError):
        adapter.validate_python(forged)
    with pytest.raises(PydanticSerializationError) as error:
        adapter.dump_json(forged)
    assert "fixture-secret" not in str(error.value)
    with pytest.raises(ValidationError):
        adapter.validate_python("safe")


def test_exact_enum_adapter_validation_errors_are_stable_and_input_free() -> None:
    """Catch direct adapter errors retaining raw JSON or hostile forged members."""
    api = exact_enum_api()

    class Synthetic(api.ExactStrEnum):
        SAFE = "safe"

        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-enum-repr")

    adapter = TypeAdapter(Synthetic)
    forged = str.__new__(Synthetic, "fixture-secret-forged-adapter")
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", "fixture-secret-forged-adapter")
    invalid_calls = (
        lambda: adapter.validate_python(forged),
        lambda: adapter.validate_json('"fixture-secret-raw-json"'),
    )
    for call in invalid_calls:
        with pytest.raises(ValidationError) as captured:
            call()
        details = captured.value.errors(include_url=False)
        assert details == [
            {
                "type": "exact_enum_member",
                "loc": (),
                "msg": "value must be a canonical exact enum member",
                "input": None,
            }
        ]
        rendered = f"{captured.value!s} {captured.value!r} {details!r}"
        assert "fixture-secret" not in rendered


def test_native_exact_enum_rejects_before_hostile_class_or_repr_callbacks() -> None:
    """Exact route admission must precede Pydantic's instance handler."""
    route_type = private_api().PrivateRouteId
    callbacks: list[str] = []

    class HostileIncoming:
        def __getattribute__(self, name: str) -> object:
            if name == "__class__":
                callbacks.append("class")
                return route_type
            return object.__getattribute__(self, name)

        def __repr__(self) -> str:
            callbacks.append("repr")
            return "fixture-secret-native-enum-repr"

    with pytest.raises(ValidationError) as captured:
        TypeAdapter(route_type).validate_python(HostileIncoming())

    details = captured.value.errors(include_url=False)
    assert details == [
        {
            "type": "exact_enum_member",
            "loc": (),
            "msg": "value must be a canonical exact enum member",
            "input": None,
        }
    ]
    assert "fixture-secret" not in f"{captured.value!s} {captured.value!r} {details!r}"
    assert callbacks == []


def test_foreign_exact_enum_rejects_before_hostile_class_or_repr_callbacks() -> None:
    """Foreign exact-enum metadata has the same pre-handler admission rule."""
    enum_api = exact_enum_api()

    class Foreign(StrEnum):
        SAFE = "safe"

    capture_foreign_exact_enum(Foreign, (("SAFE", "safe"),))

    callbacks: list[str] = []

    class HostileIncoming:
        def __getattribute__(self, name: str) -> object:
            if name == "__class__":
                callbacks.append("class")
                return Foreign
            return object.__getattribute__(self, name)

        def __repr__(self) -> str:
            callbacks.append("repr")
            return "fixture-secret-foreign-enum-repr"

    alias = Annotated[Foreign, enum_api.ExactEnumField(Foreign)]
    with pytest.raises(ValidationError) as captured:
        TypeAdapter(alias).validate_python(HostileIncoming())

    details = captured.value.errors(include_url=False)
    assert details == [
        {
            "type": "exact_enum_member",
            "loc": (),
            "msg": "value must be a canonical exact enum member",
            "input": None,
        }
    ]
    assert "fixture-secret" not in f"{captured.value!s} {captured.value!r} {details!r}"
    assert callbacks == []


def test_private_model_exact_enum_rejects_before_hostile_class_or_repr_callbacks() -> None:
    """Embedded exact enums must remain callback-free at the private boundary."""
    api = private_api()
    callbacks: list[str] = []

    class HostileIncoming:
        def __getattribute__(self, name: str) -> object:
            if name == "__class__":
                callbacks.append("class")
                return api.PrivateRouteId
            return object.__getattribute__(self, name)

        def __repr__(self) -> str:
            callbacks.append("repr")
            return "fixture-secret-private-model-enum-repr"

    model = create_model(
        "PrivateHostileExactEnumProbe",
        __base__=api.PrivateBoundaryModel,
        route=(api.PrivateRouteId, ...),
    )
    with pytest.raises(ValidationError) as captured:
        model.model_validate({"route": HostileIncoming()})

    details = captured.value.errors(include_url=False)
    assert details == [
        {
            "type": "private_model_value",
            "loc": (),
            "msg": "invalid private model value",
            "input": None,
        }
    ]
    assert "fixture-secret" not in f"{captured.value!s} {captured.value!r} {details!r}"
    assert callbacks == []


def test_exact_enum_registration_rejects_aliases_and_duplicate_literals() -> None:
    api = exact_enum_api()

    with pytest.raises(ValueError) as native_error:

        class AliasedExact(api.ExactStrEnum):
            FIRST = "same"
            SECOND = "same"

    assert native_error.value.args == ("exact enum aliases are forbidden",)

    class AliasedForeign(StrEnum):
        FIRST = "same"
        SECOND = "same"

    with pytest.raises(ValueError) as foreign_error:
        capture_foreign_exact_enum(AliasedForeign, (("FIRST", "same"), ("SECOND", "same")))
    assert foreign_error.value.args == ("exact enum aliases are forbidden",)


def test_exact_enum_field_is_frozen_and_hardens_registered_foreign_enum() -> None:
    api = exact_enum_api()

    class Foreign(StrEnum):
        FIRST = "first"
        SECOND = "second"

    capture_foreign_exact_enum(Foreign, (("FIRST", "first"), ("SECOND", "second")))
    field = api.ExactEnumField(Foreign)
    with pytest.raises(FrozenInstanceError):
        field.enum_type = Foreign

    alias = Annotated[Foreign, field]
    adapter = TypeAdapter(alias)
    model = create_model(
        "PrivateForeignEnumProbe",
        __base__=private_api().PrivateBoundaryModel,
        value=(alias, ...),
    )
    for member, literal in ((Foreign.FIRST, "first"), (Foreign.SECOND, "second")):
        assert adapter.validate_python(member) is member
        assert adapter.dump_python(member) is member
        assert adapter.dump_json(member) == f'"{literal}"'.encode()
        assert model(value=member).model_dump_json() == f'{{"value":"{literal}"}}'

    class OtherForeign(StrEnum):
        FIRST = "first"

    class TextSubclass(str):
        pass

    with pytest.raises(TypeError, match="captured enum class"):
        TypeAdapter(Annotated[OtherForeign, field])

    for invalid in ("first", TextSubclass("first"), OtherForeign.FIRST):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
    with pytest.raises(ValidationError):
        adapter.validate_json('"first"')
    with pytest.raises(ValidationError):
        model.model_validate_json('{"value":"first"}')
    with pytest.raises(ValidationError):
        model.model_validate_strings({"value": "first"})

    original_name = Foreign.FIRST._name_
    original_value = Foreign.FIRST._value_
    object.__setattr__(Foreign.FIRST, "_name_", "FIXTURE_SECRET_MUTATED")
    object.__setattr__(Foreign.FIRST, "_value_", "fixture-secret-mutated")
    try:
        assert adapter.validate_python(Foreign.FIRST) is Foreign.FIRST
        assert adapter.dump_python(Foreign.FIRST) is Foreign.FIRST
        assert adapter.dump_json(Foreign.FIRST) == b'"first"'
        assert api.exact_enum_literal(Foreign, Foreign.FIRST) == "first"
    finally:
        object.__setattr__(Foreign.FIRST, "_name_", original_name)
        object.__setattr__(Foreign.FIRST, "_value_", original_value)

    forged = str.__new__(Foreign, "first")
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", "first")
    for invalid in (forged,):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
        with pytest.raises(PydanticSerializationError):
            adapter.dump_json(invalid)


def test_exact_enum_helpers_use_exact_strings_and_registered_type_identity() -> None:
    api = exact_enum_api()

    class Synthetic(api.ExactStrEnum):
        FIRST = "first"
        SECOND = "second"

    assert api.exact_enum_literal(Synthetic, Synthetic.FIRST) == "first"
    assert api.exact_enum_literal(Synthetic, Synthetic.SECOND) == "second"
    assert api.exact_enum_member_for_literal(Synthetic, "first") is Synthetic.FIRST
    assert api.exact_enum_member_for_literal(Synthetic, "second") is Synthetic.SECOND
    assert api.exact_enum_member_for_literal(Synthetic, "unknown") is None

    class HostileText(str):
        def __eq__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-helper-text")

        def __hash__(self) -> int:
            raise AssertionError("fixture-secret-helper-text")

    assert api.exact_enum_member_for_literal(Synthetic, HostileText("first")) is None

    forged = str.__new__(Synthetic, "first")
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", "first")
    with pytest.raises(ValueError, match="canonical"):
        api.exact_enum_literal(Synthetic, forged)

    callbacks: list[str] = []

    class HostileMeta(type):
        def __getattribute__(cls, name: str) -> object:
            callbacks.append(f"get:{name}")
            return super().__getattribute__(name)

        def __eq__(cls, other: object) -> bool:
            callbacks.append("eq")
            return False

        def __hash__(cls) -> int:
            callbacks.append("hash")
            return 1

        def __subclasscheck__(cls, subclass: type[object]) -> bool:
            callbacks.append("subclass")
            return False

    class HostileClass(metaclass=HostileMeta):
        pass

    with pytest.raises(ValueError, match="registered"):
        api.exact_enum_literal(HostileClass, Synthetic.FIRST)
    with pytest.raises(ValueError, match="registered"):
        api.exact_enum_member_for_literal(HostileClass, "first")
    with pytest.raises(TypeError, match="source is not captured"):
        api.ExactEnumField(HostileClass)
    assert callbacks == []


def test_exact_enum_supplier_exposes_no_public_mutable_registry() -> None:
    primitives = importlib.import_module("hmt_crypto.domain.private_primitives")
    public_names = set(primitives.__all__)
    assert {
        "ExactStrEnum",
        "ExactEnumField",
        "exact_enum_literal",
        "exact_enum_member_for_literal",
    } <= public_names
    assert all("registry" not in name.lower() for name in public_names)


def test_exact_enum_field_rejects_uncaptured_foreign_enum_without_callbacks() -> None:
    """Foreign source admission is explicit and lookup-only."""
    api = exact_enum_api()

    class Uncaptured(StrEnum):
        SAFE = "safe"

    with pytest.raises(TypeError, match="^exact enum source is not captured$"):
        api.ExactEnumField(Uncaptured)

    callbacks: list[str] = []

    class HostileMeta(type):
        def __getattribute__(cls, name: str) -> object:
            callbacks.append(name)
            raise AssertionError("fixture-secret-uncaptured-callback")

        def __repr__(cls) -> str:
            callbacks.append("repr")
            raise AssertionError("fixture-secret-uncaptured-callback")

    class Hostile(metaclass=HostileMeta):
        pass

    with pytest.raises(TypeError, match="^exact enum source is not captured$") as captured:
        api.ExactEnumField(Hostile)
    assert callbacks == []
    assert "fixture-secret" not in str(captured.value)


def test_foreign_exact_enum_capture_is_exact_idempotent_and_callback_free() -> None:
    """Source capture validates shape first and never replaces a frozen entry."""
    callbacks: list[str] = []

    class HostileTuple(tuple):
        def __iter__(self) -> Iterator[object]:
            callbacks.append("iter")
            raise AssertionError("fixture-secret-manifest")

        def __len__(self) -> int:
            callbacks.append("len")
            raise AssertionError("fixture-secret-manifest")

        def __getitem__(self, index: object) -> object:
            callbacks.append("getitem")
            raise AssertionError("fixture-secret-manifest")

        def __repr__(self) -> str:
            callbacks.append("repr")
            raise AssertionError("fixture-secret-manifest")

    class HostileText(str):
        def __eq__(self, other: object) -> bool:
            callbacks.append("eq")
            raise AssertionError("fixture-secret-manifest")

        def __hash__(self) -> int:
            callbacks.append("hash")
            raise AssertionError("fixture-secret-manifest")

        def __repr__(self) -> str:
            callbacks.append("repr")
            raise AssertionError("fixture-secret-manifest")

    class HostileMeta(type):
        def __getattribute__(cls, name: str) -> object:
            callbacks.append(f"get:{name}")
            raise AssertionError("fixture-secret-source")

        def __repr__(cls) -> str:
            callbacks.append("repr")
            raise AssertionError("fixture-secret-source")

    class HostileSource(metaclass=HostileMeta):
        pass

    class Foreign(StrEnum):
        SAFE = "safe"

    malformed: tuple[object, ...] = (
        [],
        (("SAFE",),),
        (("SAFE", "safe", "extra"),),
        (["SAFE", "safe"],),
        (HostileTuple(("SAFE", "safe")),),
        ((HostileText("SAFE"), "safe"),),
        HostileTuple((("SAFE", "safe"),)),
        object(),
    )
    for manifest in malformed:
        with pytest.raises(TypeError) as captured:
            capture_foreign_exact_enum(Foreign, manifest)  # type: ignore[arg-type]
        assert captured.value.args == ("invalid exact enum source class",)

    with pytest.raises(TypeError) as captured:
        capture_foreign_exact_enum(HostileSource, (("SAFE", "safe"),))
    assert captured.value.args == ("invalid exact enum source class",)
    assert callbacks == []

    for manifest in ((("WRONG", "safe"),), (("SAFE", "wrong"),)):
        with pytest.raises(TypeError) as captured:
            capture_foreign_exact_enum(Foreign, manifest)
        assert captured.value.args == ("invalid exact enum source class",)

    manifest = (("SAFE", "safe"),)
    capture_foreign_exact_enum(Foreign, manifest)
    capture_foreign_exact_enum(Foreign, manifest)
    with pytest.raises(TypeError) as captured:
        capture_foreign_exact_enum(Foreign, (("SAFE", "changed"),))
    assert captured.value.args == ("invalid exact enum source class",)

    api = exact_enum_api()
    field = api.ExactEnumField(Foreign)
    assert api.exact_enum_literal(Foreign, Foreign.SAFE) == "safe"
    assert TypeAdapter(Annotated[Foreign, field]).validate_python(Foreign.SAFE) is Foreign.SAFE


def test_config_enum_snapshot_survives_consistent_live_replacement() -> None:
    """Late coherent enum-map replacement cannot poison a source snapshot."""
    from hmt_crypto.config import ExecutionMode

    api = exact_enum_api()
    original = ExecutionMode.PAPER
    member_map = type.__getattribute__(ExecutionMode, "_member_map_")
    value_map = type.__getattribute__(ExecutionMode, "_value2member_map_")
    forged = str.__new__(ExecutionMode, "PAPER")
    object.__setattr__(forged, "_name_", "PAPER")
    object.__setattr__(forged, "_value_", "PAPER")
    object.__setattr__(forged, "__objclass__", ExecutionMode)
    object.__setattr__(forged, "_sort_order_", 1)
    type.__setattr__(ExecutionMode, "PAPER", forged)
    member_map["PAPER"] = forged
    value_map["PAPER"] = forged
    try:
        adapter = TypeAdapter(Annotated[ExecutionMode, api.ExactEnumField(ExecutionMode)])
        assert adapter.validate_python(original) is original
        assert adapter.dump_json(original) == b'"PAPER"'
        with pytest.raises(ValidationError):
            adapter.validate_python(forged)
        with pytest.raises(PydanticSerializationError):
            adapter.dump_json(forged)
    finally:
        type.__setattr__(ExecutionMode, "PAPER", original)
        member_map["PAPER"] = original
        value_map["PAPER"] = original


def test_config_enum_capture_does_not_change_strenum_or_settings_behavior() -> None:
    """Source capture is observational and config parsing remains unchanged."""
    from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode, Settings

    assert type(ExecutionMode) is type(StrEnum)
    assert tuple((member.name, member.value) for member in ExecutionMode) == (
        ("BACKTEST", "BACKTEST"),
        ("PAPER", "PAPER"),
        ("LIVE", "LIVE"),
    )
    assert tuple((member.name, member.value) for member in DeltaRegion) == (
        ("INDIA", "india"),
        ("GLOBAL", "global"),
    )
    assert tuple((member.name, member.value) for member in DeltaEnvironment) == (
        ("TESTNET", "testnet"),
        ("PROD", "prod"),
    )
    configured = Settings(
        _env_file=None,
        execution_mode="BACKTEST",
        delta_region="global",
        delta_env="prod",
    )
    assert configured.execution_mode is ExecutionMode.BACKTEST
    assert configured.delta_region is DeltaRegion.GLOBAL
    assert configured.delta_env is DeltaEnvironment.PROD
    dumped = configured.model_dump(mode="json")
    assert dumped["execution_mode"] == "BACKTEST"
    assert dumped["delta_region"] == "global"
    assert dumped["delta_env"] == "prod"


def test_private_primitive_facade_preserves_exact_public_surface_and_identity() -> None:
    """The split is invisible at the supported public facade."""
    facade = importlib.import_module("hmt_crypto.domain.private_primitives")
    enum_leaf = importlib.import_module("hmt_crypto._private_enums")
    model_leaf = importlib.import_module("hmt_crypto.domain._private_model")
    expected = [
        "AssetSymbol",
        "BoundedDiagnosticText",
        "Contracts",
        "CredentialVersion",
        "ExactAwareUtcDatetime",
        "ExactEnumField",
        "ExactStrEnum",
        "ExactUuid",
        "ExternalUserText",
        "NonNegativePrivateDecimal",
        "NonZeroPrivateDecimal",
        "ObservedDeltaClientOrderId",
        "OmsClientOrderId",
        "OpaqueCursor",
        "PositivePrivateDecimal",
        "PrivateBoundaryModel",
        "PrivateDecimal",
        "PrivateId",
        "ProductSymbol",
        "Sha256Hex",
        "SignedContracts",
        "StrictEpoch",
        "StrictNonNegativeInt",
        "StrictPositiveInt",
        "StrictPositivePid",
        "UnfilledContracts",
        "UnknownEnumToken",
        "exact_enum_literal",
        "exact_enum_member_for_literal",
        "parse_epoch_microseconds",
        "parse_position_iso_datetime",
        "parse_private_decimal_text",
    ]
    assert facade.__all__ == expected
    for name in (
        "ExactStrEnum",
        "ExactEnumField",
        "exact_enum_literal",
        "exact_enum_member_for_literal",
    ):
        assert getattr(facade, name) is getattr(enum_leaf, name)
    assert facade.PrivateBoundaryModel is model_leaf.PrivateBoundaryModel
    for name in (
        "ExactStrEnum",
        "ExactEnumField",
        "PrivateBoundaryModel",
        "exact_enum_literal",
        "exact_enum_member_for_literal",
    ):
        value = getattr(facade, name)
        assert value.__module__ == "hmt_crypto.domain.private_primitives"
        assert value.__name__ == name
        assert value.__qualname__ == name


def test_private_primitive_public_identity_is_bound_during_construction() -> None:
    """Class construction and schema refs never see leaf paths."""
    from hmt_crypto.config import ExecutionMode

    facade = importlib.import_module("hmt_crypto.domain.private_primitives")
    project_root = Path(__file__).resolve().parents[2]
    leaves = (
        project_root / "src/hmt_crypto/_private_enums.py",
        project_root / "src/hmt_crypto/domain/_private_model.py",
    )
    public_classes = {"ExactStrEnum", "ExactEnumField", "PrivateBoundaryModel"}
    found: set[str] = set()
    for path in leaves:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for statement in tree.body:
            if isinstance(statement, ast.ClassDef) and statement.name in public_classes:
                found.add(statement.name)
                assert any(
                    isinstance(item, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "__module__"
                        for target in item.targets
                    )
                    and isinstance(item.value, ast.Constant)
                    and item.value.value == "hmt_crypto.domain.private_primitives"
                    for item in statement.body
                )
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                assert not any(
                    isinstance(node, ast.Attribute)
                    and node.attr == "__module__"
                    and isinstance(node.value, ast.Name)
                    and node.value.id in public_classes
                    for node in ast.walk(statement)
                )
    assert found == public_classes
    model = facade.PrivateBoundaryModel
    assert model.__pydantic_core_schema__["ref"] == (
        f"hmt_crypto.domain.private_primitives.PrivateBoundaryModel:{id(model)}"
    )
    assert "hmt_crypto.domain._private_model" not in repr(model.__pydantic_core_schema__)

    class Native(facade.ExactStrEnum):
        SAFE = "safe"

    metadata = facade.ExactEnumField(ExecutionMode)
    foreign_alias = Annotated[ExecutionMode, metadata]
    native_adapter = TypeAdapter(Native)
    foreign_adapter = TypeAdapter(foreign_alias)
    native_core = native_adapter.core_schema
    foreign_core = foreign_adapter.core_schema
    assert native_core["schema"]["cls"] is Native
    assert native_core["metadata"]["pydantic_js_functions"][0].__self__ is Native
    assert foreign_core["schema"]["cls"] is ExecutionMode
    assert foreign_core["metadata"]["pydantic_js_annotation_functions"][0].__self__ is metadata
    assert native_adapter.json_schema(mode="validation") == {"not": {}}
    assert native_adapter.json_schema(mode="serialization") == {
        "enum": ["safe"],
        "type": "string",
    }
    assert foreign_adapter.json_schema(mode="validation") == {"not": {}}
    assert foreign_adapter.json_schema(mode="serialization") == {
        "enum": ["BACKTEST", "PAPER", "LIVE"],
        "type": "string",
    }

    derived = create_model(
        "PrivateConstructionIdentityProbe",
        __base__=model,
        native=(Native, ...),
        foreign=(foreign_alias, ...),
    )
    assert derived.__pydantic_core_schema__["ref"] == (
        f"{derived.__module__}.{derived.__qualname__}:{id(derived)}"
    )
    validation_schema = derived.model_json_schema(mode="validation")
    serialization_schema = derived.model_json_schema(mode="serialization")
    assert validation_schema == {
        "additionalProperties": False,
        "properties": {
            "native": {"not": {}, "title": "Native"},
            "foreign": {"not": {}, "title": "Foreign"},
        },
        "required": ["native", "foreign"],
        "title": "PrivateConstructionIdentityProbe",
        "type": "object",
    }
    assert serialization_schema == {
        "additionalProperties": False,
        "properties": {
            "native": {"enum": ["safe"], "title": "Native", "type": "string"},
            "foreign": {
                "enum": ["BACKTEST", "PAPER", "LIVE"],
                "title": "Foreign",
                "type": "string",
            },
        },
        "required": ["native", "foreign"],
        "title": "PrivateConstructionIdentityProbe",
        "type": "object",
    }
    for schema in (
        model.__pydantic_core_schema__,
        derived.__pydantic_core_schema__,
        native_adapter.json_schema(mode="validation"),
        native_adapter.json_schema(mode="serialization"),
        foreign_adapter.json_schema(mode="validation"),
        foreign_adapter.json_schema(mode="serialization"),
        validation_schema,
        serialization_schema,
    ):
        rendered = repr(schema)
        assert "hmt_crypto._private_enums" not in rendered
        assert "hmt_crypto.domain._private_model" not in rendered


def test_private_primitive_public_pickle_roundtrips_resolve_the_facade() -> None:
    """Moved objects remain pickle-compatible through their public identities."""
    from hmt_crypto.config import ExecutionMode
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    facade = importlib.import_module("hmt_crypto.domain.private_primitives")
    for value in (
        facade.ExactStrEnum,
        facade.ExactEnumField,
        facade.PrivateBoundaryModel,
        facade.exact_enum_literal,
        facade.exact_enum_member_for_literal,
        PrivateRouteId.BALANCES,
    ):
        assert pickle.loads(pickle.dumps(value)) is value

    metadata = pickle.loads(pickle.dumps(facade.ExactEnumField(ExecutionMode)))
    assert type(metadata) is facade.ExactEnumField
    assert metadata.enum_type is ExecutionMode
    adapter = TypeAdapter(Annotated[ExecutionMode, metadata])
    assert adapter.validate_python(ExecutionMode.PAPER) is ExecutionMode.PAPER
    assert adapter.dump_python(ExecutionMode.PAPER) is ExecutionMode.PAPER
    assert adapter.dump_json(ExecutionMode.PAPER) == b'"PAPER"'
    assert adapter.json_schema(mode="validation") == {"not": {}}
    assert adapter.json_schema(mode="serialization") == {
        "enum": ["BACKTEST", "PAPER", "LIVE"],
        "type": "string",
    }
    model = pickle.loads(pickle.dumps(facade.PrivateBoundaryModel()))
    assert type(model) is facade.PrivateBoundaryModel
    assert model.model_dump() == {}
    assert model.model_dump_json() == "{}"
    assert type(model).model_validate({}).model_dump() == {}
    assert type(model).model_json_schema(mode="validation") == {
        "additionalProperties": False,
        "description": "Immutable strict model that owns inputs and hides them from failures.",
        "properties": {},
        "title": "PrivateBoundaryModel",
        "type": "object",
    }


def test_private_primitive_split_has_one_responsibility_per_module() -> None:
    """Each supplier leaf owns only its frozen responsibility."""
    project_root = Path(__file__).resolve().parents[2]
    enum_tree = ast.parse(
        (project_root / "src/hmt_crypto/_private_enums.py").read_text(encoding="utf-8")
    )
    model_tree = ast.parse(
        (project_root / "src/hmt_crypto/domain/_private_model.py").read_text(encoding="utf-8")
    )
    facade_tree = ast.parse(
        (project_root / "src/hmt_crypto/domain/private_primitives.py").read_text(encoding="utf-8")
    )
    enum_names = {node.name for node in enum_tree.body if isinstance(node, ast.ClassDef)}
    model_names = {node.name for node in model_tree.body if isinstance(node, ast.ClassDef)}
    facade_classes = {node.name for node in facade_tree.body if isinstance(node, ast.ClassDef)}
    facade_assignments = {
        target.id
        for node in facade_tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
        if isinstance(target, ast.Name)
    }
    assert enum_names == {"ExactStrEnum", "ExactEnumField"}
    assert model_names == {"PrivateBoundaryModel"}
    assert facade_classes == set()
    assert "_EXACT_ENUM_REGISTRY" not in facade_assignments
    assert "_EXACT_ENUM_FIELD_REGISTRY" not in facade_assignments


def _fresh_python(script: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_private_primitive_fresh_import_graph_remains_inert() -> None:
    """The split adds neither cycles nor runtime-authority imports."""
    _fresh_python(
        """
        import sys
        import hmt_crypto.config
        assert "hmt_crypto._private_enums" in sys.modules
        assert "hmt_crypto.domain.private_primitives" not in sys.modules
        """
    )
    _fresh_python(
        """
        import sys
        import hmt_crypto.domain.private_primitives
        blocked = ("hmt_crypto.config", "hmt_crypto.exchange", "hmt_crypto.persistence")
        assert not any(
            name == prefix or name.startswith(prefix + ".")
            for name in sys.modules
            for prefix in blocked
        )
        assert "httpx" not in sys.modules
        assert "sqlalchemy" not in sys.modules
        """
    )


def test_private_enum_capture_import_order_matrix() -> None:
    """All supported import orders share one captured identity authority."""
    for imports in (
        "import hmt_crypto.domain.private_primitives\nimport hmt_crypto.config",
        "import hmt_crypto.config\nimport hmt_crypto.domain.private_primitives",
        "import hmt_crypto.exchange.delta.private_routes\n"
        "import hmt_crypto.config\n"
        "import hmt_crypto.domain.private_primitives",
    ):
        _fresh_python(
            imports
            + "\n"
            + textwrap.dedent(
                """
                import importlib
                import sys
                from typing import Annotated
                from pydantic import TypeAdapter
                from hmt_crypto import config
                from hmt_crypto.domain.private_primitives import (
                    ExactEnumField,
                    exact_enum_literal,
                    exact_enum_member_for_literal,
                )

                enum_leaf = importlib.import_module("hmt_crypto._private_enums")
                assert sys.modules["hmt_crypto._private_enums"] is enum_leaf
                assert ExactEnumField.__post_init__.__globals__ is enum_leaf.__dict__
                assert exact_enum_literal.__globals__ is enum_leaf.__dict__
                assert exact_enum_member_for_literal.__globals__ is enum_leaf.__dict__
                specs = (
                    (
                        config.ExecutionMode,
                        (("BACKTEST", "BACKTEST"), ("PAPER", "PAPER"), ("LIVE", "LIVE")),
                    ),
                    (config.DeltaRegion, (("INDIA", "india"), ("GLOBAL", "global"))),
                    (
                        config.DeltaEnvironment,
                        (("TESTNET", "testnet"), ("PROD", "prod")),
                    ),
                )
                for enum_type, manifest in specs:
                    registrations = [
                        registration
                        for registration in enum_leaf._EXACT_ENUM_REGISTRY
                        if registration[0] is enum_type
                    ]
                    assert len(registrations) == 1
                    entries = registrations[0][1]
                    assert registrations[0][2] == manifest
                    assert len(entries) == len(manifest)
                    field = ExactEnumField(enum_type)
                    adapter = TypeAdapter(Annotated[enum_type, field])
                    for (name, literal), (registered_member, registered_literal) in zip(
                        manifest, entries, strict=True
                    ):
                        member = getattr(enum_type, name)
                        assert registered_member is member
                        assert registered_literal == literal
                        assert exact_enum_literal(enum_type, member) == literal
                        assert exact_enum_member_for_literal(enum_type, literal) is member
                        assert adapter.validate_python(member) is member
                settings = config.Settings(
                    _env_file=None, delta_region="global", delta_env="prod"
                )
                assert settings.delta_region is config.DeltaRegion.GLOBAL
                assert settings.model_dump(mode="json")["delta_env"] == "prod"
                """
            )
        )


def test_private_primitive_public_identity_survives_pickle_lookup() -> None:
    """Facade lookup also resolves native route subclasses and members."""
    from hmt_crypto.exchange.delta.private_routes import PrivateRouteId

    facade = importlib.import_module("hmt_crypto.domain.private_primitives")
    assert pickle.loads(pickle.dumps(facade.ExactStrEnum)) is facade.ExactStrEnum
    assert pickle.loads(pickle.dumps(PrivateRouteId)) is PrivateRouteId
    assert pickle.loads(pickle.dumps(PrivateRouteId.OPEN_ORDERS)) is PrivateRouteId.OPEN_ORDERS


def test_private_primitive_support_modules_remain_reviewable() -> None:
    """The split leaves three physically reviewable implementation units."""
    project_root = Path(__file__).resolve().parents[2]
    ceilings = {
        project_root / "src/hmt_crypto/_private_enums.py": 500,
        project_root / "src/hmt_crypto/domain/_private_model.py": 500,
        project_root / "src/hmt_crypto/domain/private_primitives.py": 400,
    }
    for path, ceiling in ceilings.items():
        assert len(path.read_text(encoding="utf-8").splitlines()) <= ceiling


def test_private_route_identity_is_closed_and_requires_the_enum_at_model_boundary() -> None:
    api = private_api()
    expected = {
        "BALANCES": "balances",
        "TRANSACTIONS": "transactions",
        "MARGINED_POSITIONS": "margined_positions",
        "LATEST_POSITION": "latest_position",
        "OPEN_ORDERS": "open_orders",
        "ORDER_HISTORY": "order_history",
        "FILLS": "fills",
        "ORDER_BY_ID": "order_by_id",
        "ORDER_BY_CLIENT_ID": "order_by_client_id",
        "GET_LEVERAGE": "get_leverage",
        "PLACE_ORDER": "place_order",
        "MODIFY_ORDER": "modify_order",
        "CANCEL_ORDER": "cancel_order",
        "CANCEL_ALL": "cancel_all",
        "SET_LEVERAGE": "set_leverage",
        "SET_MARGIN_MODE": "set_margin_mode",
        "CHANGE_POSITION_MARGIN": "change_position_margin",
        "CREATE_BRACKET": "create_bracket",
        "EDIT_BRACKET": "edit_bracket",
    }
    assert issubclass(api.PrivateRouteId, StrEnum)
    assert {member.name: member.value for member in api.PrivateRouteId} == expected
    assert validate(api.PrivateRouteId, api.PrivateRouteId.BALANCES) is api.PrivateRouteId.BALANCES
    for invalid in ("balances", "withdrawal", 1):
        with pytest.raises(ValidationError):
            validate(api.PrivateRouteId, invalid)


def test_private_route_identity_never_coerces_json_or_validate_strings() -> None:
    api = private_api()
    model = create_model(
        "PrivateRouteProbe",
        __base__=api.PrivateBoundaryModel,
        route=(api.PrivateRouteId, ...),
    )
    value = model(route=api.PrivateRouteId.BALANCES)
    assert value.model_dump(mode="python") == {"route": api.PrivateRouteId.BALANCES}
    assert value.model_dump_json() == '{"route":"balances"}'
    assert model.model_validate(value) == value

    with pytest.raises(ValidationError):
        model.model_validate_json('{"route":"balances"}')
    with pytest.raises(ValidationError):
        model.model_validate_strings({"route": "balances"})


def _forge_private_route_member(route_type: type[StrEnum], value: str) -> StrEnum:
    forged = str.__new__(route_type, value)
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", value)
    return forged


@pytest.mark.parametrize("raw", ["balances", "fixture-secret-forged-route"])
def test_private_route_requires_one_of_the_canonical_enum_singletons(raw: str) -> None:
    api = private_api()
    adapter = TypeAdapter(api.PrivateRouteId)
    forged = _forge_private_route_member(api.PrivateRouteId, raw)
    assert type(forged) is api.PrivateRouteId
    assert all(forged is not member for member in api.PrivateRouteId)

    with pytest.raises(ValidationError):
        adapter.validate_python(forged)
    with pytest.raises(ValidationError):
        validate(api.PrivateRouteId, forged)

    model = create_model(
        "PrivateForgedRouteProbe",
        __base__=api.PrivateBoundaryModel,
        route=(api.PrivateRouteId, ...),
    )
    with pytest.raises(ValidationError):
        model(route=forged)


@pytest.mark.parametrize("raw", ["balances", "fixture-secret-forged-route"])
def test_private_route_serializers_reject_forged_exact_class_members(raw: str) -> None:
    api = private_api()
    adapter = TypeAdapter(api.PrivateRouteId)
    forged = _forge_private_route_member(api.PrivateRouteId, raw)

    with pytest.raises(PydanticSerializationError):
        adapter.dump_python(forged)
    with pytest.raises(PydanticSerializationError):
        adapter.dump_json(forged)

    model = create_model(
        "PrivateForgedRouteSerializationProbe",
        __base__=api.PrivateBoundaryModel,
        route=(api.PrivateRouteId, ...),
    )
    value = model(route=api.PrivateRouteId.BALANCES)
    value.__dict__["route"] = forged
    with pytest.raises(ValidationError) as python_error:
        value.model_dump()
    with pytest.raises(ValidationError) as json_error:
        value.model_dump_json()
    for error in (python_error.value, json_error.value):
        assert all(detail.get("input") is None for detail in error.errors())
        assert raw not in str(error)


def test_private_route_type_adapter_serializes_valid_canonical_members() -> None:
    route = private_api().PrivateRouteId.BALANCES
    adapter = TypeAdapter(private_api().PrivateRouteId)
    assert adapter.dump_python(route) is route
    assert adapter.dump_json(route) == b'"balances"'


def test_private_route_uses_exact_enum_supplier_for_every_canonical_member() -> None:
    enum_api = exact_enum_api()
    route_type = private_api().PrivateRouteId
    expected_literals = (
        "balances",
        "transactions",
        "margined_positions",
        "latest_position",
        "open_orders",
        "order_history",
        "fills",
        "order_by_id",
        "order_by_client_id",
        "get_leverage",
        "place_order",
        "modify_order",
        "cancel_order",
        "cancel_all",
        "set_leverage",
        "set_margin_mode",
        "change_position_margin",
        "create_bracket",
        "edit_bracket",
    )
    adapter = TypeAdapter(route_type)
    assert tuple(route_type) == tuple(adapter.validate_python(member) for member in route_type)
    for member, literal in zip(route_type, expected_literals, strict=True):
        assert enum_api.exact_enum_literal(route_type, member) == literal
        assert enum_api.exact_enum_member_for_literal(route_type, literal) is member
        assert adapter.dump_json(member) == f'"{literal}"'.encode()


def test_private_route_identity_and_literal_are_independent_of_live_enum_state() -> None:
    api = private_api()
    enum_api = exact_enum_api()
    route = api.PrivateRouteId.BALANCES
    original_name = route._name_
    original_value = route._value_
    object.__setattr__(route, "_name_", "FIXTURE_SECRET_MUTATED_NAME")
    object.__setattr__(route, "_value_", "fixture-secret-mutated-value")
    try:
        adapter = TypeAdapter(api.PrivateRouteId)
        assert adapter.validate_python(route) is route
        assert adapter.dump_python(route) is route
        assert adapter.dump_json(route) == b'"balances"'
        assert enum_api.exact_enum_literal(api.PrivateRouteId, route) == "balances"
        assert enum_api.exact_enum_member_for_literal(api.PrivateRouteId, "balances") is route

        model = create_model(
            "PrivateMutatedCanonicalRouteProbe",
            __base__=api.PrivateBoundaryModel,
            route=(api.PrivateRouteId, ...),
        )
        value = model(route=route)
        assert value.route is route
        assert value.model_dump(mode="python")["route"] is route
        assert value.model_dump_json() == '{"route":"balances"}'
    finally:
        object.__setattr__(route, "_name_", original_name)
        object.__setattr__(route, "_value_", original_value)


def test_exact_str_enum_duck_json_serialization_uses_frozen_identity_snapshot() -> None:
    """Catch Any JSON serialization reading live native enum state."""
    api = exact_enum_api()

    class Synthetic(api.ExactStrEnum):
        SAFE = "safe"

    adapter = TypeAdapter(Synthetic)
    member = Synthetic.SAFE
    original_value = member._value_
    object.__setattr__(member, "_value_", "fixture-secret-mutated-native")
    try:
        assert member.value == "safe"
        assert adapter.dump_json(member, serialize_as_any=True) == b'"safe"'
        assert adapter.dump_python(member, mode="json", serialize_as_any=True) == "safe"
    finally:
        object.__setattr__(member, "_value_", original_value)

    forged = str.__new__(Synthetic, "fixture-secret-forged-native")
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", "fixture-secret-forged-native-value")
    with pytest.raises(ValueError) as value_error:
        _ = forged.value
    with pytest.raises(PydanticSerializationError) as json_error:
        adapter.dump_json(forged, serialize_as_any=True)
    with pytest.raises(ValueError) as python_json_error:
        adapter.dump_python(forged, mode="json", serialize_as_any=True)
    rendered = f"{value_error.value!s} {json_error.value!s} {python_json_error.value!s}"
    assert "fixture-secret" not in rendered


def test_foreign_exact_enum_default_serialization_uses_snapshot_without_monkeypatch() -> None:
    """Catch default serialization reading live state or altering a foreign class."""
    api = exact_enum_api()

    class Foreign(StrEnum):
        SAFE = "safe"

    capture_foreign_exact_enum(Foreign, (("SAFE", "safe"),))
    original_class_value_descriptor = vars(Enum)["value"]
    adapter = TypeAdapter(Annotated[Foreign, api.ExactEnumField(Foreign)])
    assert vars(Enum)["value"] is original_class_value_descriptor
    assert "value" not in vars(Foreign)

    member = Foreign.SAFE
    original_value = member._value_
    object.__setattr__(member, "_value_", "fixture-secret-mutated-foreign")
    try:
        assert adapter.dump_python(member) is member
        assert adapter.dump_json(member) == b'"safe"'
    finally:
        object.__setattr__(member, "_value_", original_value)

    forged = str.__new__(Foreign, "fixture-secret-forged-foreign")
    object.__setattr__(forged, "_name_", "FORGED")
    object.__setattr__(forged, "_value_", "fixture-secret-forged-foreign-value")
    with pytest.raises(PydanticSerializationError):
        adapter.dump_python(forged)
    with pytest.raises(PydanticSerializationError):
        adapter.dump_json(forged)


def _serialization_bypass_violations(
    source: str, *, filename: str, hardened: bool = False
) -> list[str]:
    """Return unsupported serializer paths found in one production source."""
    violations: list[str] = []
    tree = ast.parse(source, filename=filename)
    pydantic_modules: set[str] = set()
    core_modules: set[str] = set()
    base_model_names: set[str] = set()
    schema_serializer_names: set[str] = set()
    core_function_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name == "pydantic":
                    pydantic_modules.add(local_name)
                elif alias.name == "pydantic_core":
                    core_modules.add(local_name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "pydantic":
                for alias in node.names:
                    if alias.name == "BaseModel":
                        base_model_names.add(alias.asname or alias.name)
            elif node.module == "pydantic_core":
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    if alias.name == "SchemaSerializer":
                        schema_serializer_names.add(local_name)
                        violations.append(f"{filename}:{node.lineno}:pydantic core serializer")
                    elif alias.name in {"to_json", "to_jsonable_python"}:
                        core_function_names.add(local_name)
                        violations.append(f"{filename}:{node.lineno}:pydantic core serializer")

    def is_unbound_base_dump(node: ast.Call) -> bool:
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"model_dump", "model_dump_json"}
            and (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in base_model_names
                or isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "BaseModel"
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id in pydantic_modules
            )
        )

    reviewed_hardened_calls: set[int] = set()
    if hardened:
        expected = {
            ("model_copy", "model_dump"),
            ("model_dump", "model_dump"),
            ("model_dump_json", "model_dump_json"),
        }
        observed: list[tuple[str, str]] = []
        for statement in tree.body:
            if not isinstance(statement, ast.ClassDef) or statement.name != "PrivateBoundaryModel":
                continue
            for method in statement.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for candidate in ast.walk(method):
                    if isinstance(candidate, ast.Call) and is_unbound_base_dump(candidate):
                        pair = (method.name, candidate.func.attr)
                        observed.append(pair)
                        if pair in expected:
                            reviewed_hardened_calls.add(id(candidate))
        if len(observed) != len(expected) or set(observed) != expected:
            violations.append(f"{filename}:0:unreviewed hardened BaseModel dump")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "__pydantic_serializer__":
            violations.append(f"{filename}:{node.lineno}:private serializer")
        if not isinstance(node, ast.Call):
            continue
        is_core_module_call = (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in core_modules
            and node.func.attr in {"SchemaSerializer", "to_json", "to_jsonable_python"}
        )
        is_core_function_call = (
            isinstance(node.func, ast.Name)
            and node.func.id in schema_serializer_names | core_function_names
        )
        is_private_serializer_call = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"to_json", "to_python"}
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in {"serializer", "__pydantic_serializer__"}
        )
        if is_core_module_call or is_core_function_call:
            violations.append(f"{filename}:{node.lineno}:pydantic core serializer")
        if is_private_serializer_call:
            violations.append(f"{filename}:{node.lineno}:private serializer")
        unbound_base_dump = is_unbound_base_dump(node)
        is_hardened_clean_dump = (
            id(node) in reviewed_hardened_calls
            and unbound_base_dump
            and bool(node.args)
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "clean"
        )
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "dump_python",
            "dump_json",
        }:
            violations.append(f"{filename}:{node.lineno}:direct adapter dump")
        if unbound_base_dump and not is_hardened_clean_dump:
            violations.append(f"{filename}:{node.lineno}:unbound BaseModel dump")
        if is_hardened_clean_dump and not any(
            keyword.arg == "serialize_as_any"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in node.keywords
        ):
            violations.append(f"{filename}:{node.lineno}:missing literal duck false")
        for keyword in node.keywords:
            if keyword.arg == "serialize_as_any" and not (
                isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            ):
                violations.append(f"{filename}:{node.lineno}:duck serialization")
    return violations


@pytest.mark.parametrize(
    ("source", "marker"),
    [
        ("import pydantic_core as pc\npc.to_json(value)\n", "pydantic core serializer"),
        (
            "import pydantic_core as pc\npc.to_jsonable_python(value)\n",
            "pydantic core serializer",
        ),
        ("adapter.serializer.to_json(value)\n", "private serializer"),
        (
            "from pydantic import BaseModel as BM\nBM.model_dump(value)\n",
            "unbound BaseModel dump",
        ),
        (
            "import pydantic as pd\npd.BaseModel.model_dump_json(value)\n",
            "unbound BaseModel dump",
        ),
        (
            "from pydantic_core import SchemaSerializer as SS\nSS(schema)\n",
            "pydantic core serializer",
        ),
        (
            "from pydantic_core import to_json as encode\nencode(value)\n",
            "pydantic core serializer",
        ),
    ],
)
def test_serialization_bypass_scanner_resolves_normal_aliases(source: str, marker: str) -> None:
    """Catch aliases hiding direct Pydantic serializer bypasses."""
    violations = _serialization_bypass_violations(source, filename="snippet.py")
    assert any(marker in violation for violation in violations)


def test_serialization_bypass_scanner_allows_only_reviewed_model_leaf_calls() -> None:
    """A clean variable and literal duck flag do not create a broad exemption."""
    source = """
from pydantic import BaseModel

class PrivateBoundaryModel:
    def model_copy(self, clean):
        BaseModel.model_dump(clean, serialize_as_any=False)

    def model_dump(self, clean):
        BaseModel.model_dump(clean, serialize_as_any=False)

    def model_dump_json(self, clean):
        BaseModel.model_dump_json(clean, serialize_as_any=False)

def unreviewed(clean):
    return BaseModel.model_dump(clean, serialize_as_any=False)
"""
    violations = _serialization_bypass_violations(
        source, filename="_private_model.py", hardened=True
    )
    assert any("unbound BaseModel dump" in violation for violation in violations)


def test_project_sources_forbid_unsupported_pydantic_serialization_bypasses() -> None:
    """Keep duck/direct DTO/core serializer bypasses out of operational code."""
    project_root = Path(__file__).resolve().parents[2]
    hardened_implementation = project_root / "src/hmt_crypto/domain/_private_model.py"
    violations: list[str] = []
    for path in sorted((project_root / "src").rglob("*.py")):
        violations.extend(
            _serialization_bypass_violations(
                path.read_text(encoding="utf-8"),
                filename=str(path),
                hardened=path == hardened_implementation,
            )
        )
    assert violations == []


@pytest.mark.parametrize("method_name", ["model_dump", "model_dump_json"])
@pytest.mark.parametrize(
    ("keyword", "invalid"),
    [
        ("include", {"value"}),
        ("exclude", {"value"}),
        ("context", {}),
        ("fallback", lambda value: value),
        ("by_alias", 0),
        ("exclude_unset", 0),
        ("exclude_defaults", 0),
        ("exclude_none", 0),
        ("exclude_computed_fields", 0),
        ("round_trip", 0),
        ("warnings", 0),
        ("polymorphic_serialization", True),
        ("polymorphic_serialization", 0),
    ],
)
def test_private_model_rejects_unsafe_serialization_controls_before_pydantic(
    method_name: str, keyword: str, invalid: object
) -> None:
    """Catch permissive flags and caller containers/callbacks reaching Pydantic."""
    model = create_model(
        "PrivateSerializationControlProbe",
        __base__=private_api().PrivateBoundaryModel,
        value=(private_api().PrivateId, ...),
    )
    value = model(value=7)
    with pytest.raises(TypeError, match="serialization controls") as error:
        getattr(value, method_name)(**{keyword: invalid})
    assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize("method_name", ["model_dump", "model_dump_json"])
@pytest.mark.parametrize("invalid_kind", ["true", "zero", "text", "hostile"])
def test_private_model_duck_gate_is_structured_sanitized_and_callback_free(
    method_name: str, invalid_kind: str
) -> None:
    """Catch duck rejection exposing inputs or invoking caller behavior."""

    class HostileDuck:
        def __bool__(self) -> bool:
            raise AssertionError("fixture-secret-duck-bool")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-duck-eq")

        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-duck-repr")

    invalid: object
    if invalid_kind == "true":
        invalid = True
    elif invalid_kind == "zero":
        invalid = 0
    elif invalid_kind == "text":
        invalid = "fixture-secret-duck-text"
    else:
        invalid = HostileDuck()

    model = create_model(
        "PrivateDuckErrorProbe",
        __base__=private_api().PrivateBoundaryModel,
        value=(private_api().PrivateId, ...),
    )
    value = model(value=7)
    with pytest.raises(ValidationError) as captured:
        getattr(value, method_name)(serialize_as_any=invalid)
    details = captured.value.errors(include_url=False)
    assert details == [
        {
            "type": "private_model_duck_serialization",
            "loc": (),
            "msg": "private model duck serialization unavailable",
            "input": None,
        }
    ]
    rendered = f"{captured.value!s} {captured.value!r} {details!r}"
    assert "fixture-secret" not in rendered


@pytest.mark.parametrize("method_name", ["model_dump", "model_dump_json"])
def test_private_model_rejects_duck_before_common_control_dispatch(
    method_name: str,
) -> None:
    """Catch duck admission reaching any later overridable control helper."""
    api = private_api()

    class CanaryBoundary(api.PrivateBoundaryModel):
        @classmethod
        def _require_common_serialization_controls(cls, **controls: object) -> None:
            del cls, controls
            raise AssertionError("fixture-secret-common-control")

    model = create_model(
        "PrivateDuckFirstOperationProbe",
        __base__=CanaryBoundary,
        value=(api.PrivateId, ...),
    )
    value = model(value=7)
    with pytest.raises(ValidationError) as error:
        getattr(value, method_name)(serialize_as_any=True)
    assert error.value.errors(include_url=False)[0]["type"] == ("private_model_duck_serialization")
    assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize("method_name", ["model_dump", "model_dump_json"])
@pytest.mark.parametrize("keyword", ["include", "exclude", "context", "fallback"])
def test_private_model_rejects_hostile_serialization_controls_without_callbacks(
    method_name: str, keyword: str
) -> None:
    """Catch equality, iteration, truthiness, call, or repr on unowned controls."""

    class HostileControl:
        def __bool__(self) -> bool:
            raise AssertionError("fixture-secret-control-bool")

        def __call__(self, value: object) -> object:
            raise AssertionError("fixture-secret-control-call")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-control-eq")

        def __iter__(self) -> Iterator[object]:
            raise AssertionError("fixture-secret-control-iter")

        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-control-repr")

    model = create_model(
        "PrivateHostileSerializationControlProbe",
        __base__=private_api().PrivateBoundaryModel,
        value=(private_api().PrivateId, ...),
    )
    value = model(value=7)
    with pytest.raises(TypeError, match="serialization controls") as error:
        getattr(value, method_name)(**{keyword: HostileControl()})
    assert "fixture-secret" not in str(error.value)


def test_private_model_exact_checks_copy_and_serialization_scalar_controls() -> None:
    """Catch bool/int/string subclasses being treated as supported flags."""

    class HostileInt(int):
        def __index__(self) -> int:
            raise AssertionError("fixture-secret-scalar-index")

    class HostileText(str):
        def __eq__(self, other: object) -> bool:
            raise AssertionError("fixture-secret-scalar-eq")

    model = create_model(
        "PrivateScalarSerializationControlProbe",
        __base__=private_api().PrivateBoundaryModel,
        value=(private_api().PrivateId, ...),
    )
    value = model(value=7)
    calls = (
        lambda: value.model_copy(deep=1),
        lambda: value.model_dump(mode=HostileText("python")),
        lambda: value.model_dump(warnings=HostileText("warn")),
        lambda: value.model_dump_json(indent=HostileInt(2)),
        lambda: value.model_dump_json(ensure_ascii=0),
    )
    for call in calls:
        with pytest.raises(TypeError, match="serialization controls") as error:
            call()
        assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize("indent", [-1, 100_000])
def test_private_model_json_indent_is_explicitly_bounded(indent: int) -> None:
    """Catch negative or resource-amplifying JSON indentation."""
    model = create_model(
        "PrivateJsonIndentProbe",
        __base__=private_api().PrivateBoundaryModel,
        value=(private_api().PrivateId, ...),
    )
    with pytest.raises(TypeError, match="serialization controls"):
        model(value=7).model_dump_json(indent=indent)

    for supported in (None, 0, 2, 8):
        rendered = model(value=7).model_dump_json(indent=supported)
        assert '"value"' in rendered


def test_exact_enum_field_schema_uses_identity_snapshot_after_metadata_poisoning() -> None:
    """Catch schema construction trusting mutable dataclass instance attributes."""
    api = exact_enum_api()

    class Foreign(StrEnum):
        SAFE = "safe"

    class Other(StrEnum):
        POISON = "fixture-secret-poison"

    class HostileEntries:
        def __iter__(self) -> Iterator[object]:
            raise AssertionError("fixture-secret-metadata-iter")

        def __repr__(self) -> str:
            raise AssertionError("fixture-secret-metadata-repr")

    capture_foreign_exact_enum(Foreign, (("SAFE", "safe"),))
    field = api.ExactEnumField(Foreign)
    object.__setattr__(field, "enum_type", Other)
    object.__setattr__(field, "_entries", HostileEntries())
    adapter = TypeAdapter(Annotated[Foreign, field])
    assert adapter.validate_python(Foreign.SAFE) is Foreign.SAFE
    assert adapter.dump_json(Foreign.SAFE) == b'"safe"'
    assert adapter.json_schema(mode="serialization") == {
        "enum": ["safe"],
        "type": "string",
    }
    with pytest.raises(TypeError, match="captured enum class") as error:
        TypeAdapter(Annotated[Other, field])
    assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize(
    "corruption",
    [
        "member_names_type",
        "member_names_extra",
        "member_map_extra",
        "value_map_extra",
        "class_binding",
        "member_name",
        "member_value",
    ],
)
def test_foreign_enum_registration_rejects_inconsistent_standard_internals(
    corruption: str,
) -> None:
    """Catch accepting a source class whose standard Enum maps disagree."""

    class Foreign(StrEnum):
        SAFE = "safe"

    member_names = type.__getattribute__(Foreign, "_member_names_")
    member_map = type.__getattribute__(Foreign, "_member_map_")
    value_map = type.__getattribute__(Foreign, "_value2member_map_")
    if corruption == "member_names_type":

        class HostileList(list[str]):
            def __iter__(self) -> Iterator[str]:
                raise AssertionError("fixture-secret-member-names")

        type.__setattr__(Foreign, "_member_names_", HostileList(member_names))
    elif corruption == "member_names_extra":
        member_names.append("GHOST")
    elif corruption == "member_map_extra":
        member_map["GHOST"] = Foreign.SAFE
    elif corruption == "value_map_extra":
        value_map["ghost"] = Foreign.SAFE
    elif corruption == "class_binding":
        type.__setattr__(Foreign, "SAFE", object())
    elif corruption == "member_name":
        object.__setattr__(Foreign.SAFE, "_name_", "GHOST")
    else:
        object.__setattr__(Foreign.SAFE, "_value_", "fixture-secret-member-value")

    with pytest.raises((TypeError, ValueError), match="exact enum") as error:
        capture_foreign_exact_enum(Foreign, (("SAFE", "safe"),))
    assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize("internal_name", ["_member_names_", "_member_map_", "_value2member_map_"])
def test_foreign_enum_registration_reads_raw_class_state_without_descriptors(
    internal_name: str,
) -> None:
    """Catch registration invoking descriptors installed on Enum internals."""
    callbacks: list[str] = []

    class Foreign(StrEnum):
        SAFE = "safe"

    class HostileDescriptor:
        def __get__(self, instance: object, owner: object) -> object:
            callbacks.append("get")
            raise AssertionError("fixture-secret-enum-descriptor")

        def __set__(self, instance: object, value: object) -> None:
            callbacks.append("set")
            raise AssertionError("fixture-secret-enum-descriptor")

    type.__setattr__(Foreign, internal_name, HostileDescriptor())
    with pytest.raises(TypeError, match="exact enum") as error:
        capture_foreign_exact_enum(Foreign, (("SAFE", "safe"),))
    assert callbacks == []
    assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize("internal_name", ["_name_", "_value_"])
def test_foreign_enum_registration_rejects_member_descriptors_without_callbacks(
    internal_name: str,
) -> None:
    """Catch member internal reads dispatching attacker data descriptors."""
    callbacks: list[str] = []

    class Foreign(StrEnum):
        SAFE = "safe"

    class HostileDescriptor:
        def __get__(self, instance: object, owner: object) -> object:
            callbacks.append("get")
            raise AssertionError("fixture-secret-member-descriptor")

        def __set__(self, instance: object, value: object) -> None:
            callbacks.append("set")
            raise AssertionError("fixture-secret-member-descriptor")

    type.__setattr__(Foreign, internal_name, HostileDescriptor())
    with pytest.raises(TypeError, match="exact enum") as error:
        capture_foreign_exact_enum(Foreign, (("SAFE", "safe"),))
    assert callbacks == []
    assert "fixture-secret" not in str(error.value)


def test_foreign_enum_registration_rejects_custom_mro_before_member_callbacks() -> None:
    """Catch inherited member descriptors firing during snapshot admission."""
    callbacks: list[str] = []
    armed = False

    class HostileMixin:
        @property
        def _name_(self) -> object:
            if armed:
                callbacks.append("get")
                raise AssertionError("fixture-secret-mro-descriptor")
            return vars(self)["_stored_name"]

        @_name_.setter
        def _name_(self, value: object) -> None:
            if armed:
                callbacks.append("set")
                raise AssertionError("fixture-secret-mro-descriptor")
            vars(self)["_stored_name"] = value

    class Foreign(HostileMixin, StrEnum):
        SAFE = "safe"

    callbacks.clear()
    armed = True
    with pytest.raises(TypeError, match="exact enum") as error:
        capture_foreign_exact_enum(Foreign, (("SAFE", "safe"),))
    assert callbacks == []
    assert "fixture-secret" not in str(error.value)


@pytest.mark.parametrize("foreign", [False, True])
def test_exact_enum_json_schemas_are_truthful_for_adapters_and_models(
    foreign: bool,
) -> None:
    """Catch schemas advertising raw JSON input or omitting frozen literals."""
    api = exact_enum_api()

    if foreign:

        class Synthetic(StrEnum):
            ALPHA = "alpha"
            BETA = "beta"

        capture_foreign_exact_enum(Synthetic, (("ALPHA", "alpha"), ("BETA", "beta")))
        enum_alias = Annotated[Synthetic, api.ExactEnumField(Synthetic)]
    else:

        class Synthetic(api.ExactStrEnum):
            ALPHA = "alpha"
            BETA = "beta"

        enum_alias = Synthetic

    adapter = TypeAdapter(enum_alias)
    assert adapter.json_schema(mode="validation") == {"not": {}}
    assert adapter.json_schema(mode="serialization") == {
        "enum": ["alpha", "beta"],
        "type": "string",
    }

    model = create_model(
        "PrivateExactEnumSchemaProbe",
        __base__=private_api().PrivateBoundaryModel,
        value=(enum_alias, ...),
    )
    validation_field = model.model_json_schema(mode="validation")["properties"]["value"]
    serialization_field = model.model_json_schema(mode="serialization")["properties"]["value"]
    assert validation_field["not"] == {}
    assert "type" not in validation_field
    assert serialization_field["enum"] == ["alpha", "beta"]
    assert serialization_field["type"] == "string"


@pytest.mark.parametrize("foreign", [False, True])
def test_empty_exact_enum_json_schemas_admit_no_input_or_output(foreign: bool) -> None:
    """Catch an empty registered enum being represented as serializable text."""
    api = exact_enum_api()

    if foreign:

        class Empty(StrEnum):
            pass

        capture_foreign_exact_enum(Empty, ())
        enum_alias = Annotated[Empty, api.ExactEnumField(Empty)]
    else:

        class Empty(api.ExactStrEnum):
            pass

        enum_alias = Empty

    adapter = TypeAdapter(enum_alias)
    assert adapter.json_schema(mode="validation") == {"not": {}}
    assert adapter.json_schema(mode="serialization") == {"not": {}}
