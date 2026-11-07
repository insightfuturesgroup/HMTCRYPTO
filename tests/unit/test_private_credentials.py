"""Exact sealed Task 2.7 signer and account-proof supplier contracts."""

from __future__ import annotations

import ast
import copy
import hashlib
import hmac
import importlib
import inspect
import operator
import pickle
import subprocess
import sys
import textwrap
import traceback
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
from pydantic import SecretStr, TypeAdapter, ValidationError

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin
from hmt_crypto.exchange.delta.private_read_wire import _freeze_balances_private_read
from hmt_crypto.exchange.delta.signer import DeltaSigner, SignedAuthHeaders

pytestmark = pytest.mark.unit
pytest_plugins = ("tests.integration.support.task27_private_credentials_issuer",)

_OBSERVED = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
_EXPIRES = _OBSERVED + timedelta(seconds=30)
_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000002701")
_SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000002702")
_PROOF_ID = UUID("00000000-0000-0000-0000-000000002703")
_BINDING_REF = UUID("00000000-0000-0000-0000-000000002704")
_PROBE_REF = UUID("00000000-0000-0000-0000-000000002705")
_CORRELATION_ID = UUID("00000000-0000-0000-0000-000000002706")
_API_KEY = "task27-api-key-canary"
_API_SECRET = "task27-secret-canary"
_USER_AGENT = "hmt-crypto/task27-test"


def _support() -> ModuleType:
    return importlib.import_module("tests.integration.support.task27_private_credentials_issuer")


def _account(
    *,
    environment: DeltaEnvironment = DeltaEnvironment.TESTNET,
    mode: ExecutionMode = ExecutionMode.PAPER,
    external_user_id: str = "12345",
    credential_version: int = 7,
) -> AccountRef:
    return AccountRef(
        account_id=_ACCOUNT_ID,
        external_user_id=external_user_id,
        region=DeltaRegion.INDIA,
        environment=environment,
        mode=mode,
        credential_version=credential_version,
    )


def _issue(
    marker: object,
    *,
    environment: DeltaEnvironment = DeltaEnvironment.TESTNET,
    mode: ExecutionMode = ExecutionMode.PAPER,
    slot: credentials.IndiaCredentialSlot = credentials.IndiaCredentialSlot.INDIA_TESTNET,
    snapshot_id: UUID = _SNAPSHOT_ID,
) -> tuple[object, object]:
    return _support().issue_enrollment_credential(
        marker,
        snapshot_id=snapshot_id,
        credential_slot=slot,
        credential_version=7,
        account_id=_ACCOUNT_ID,
        external_user_id="12345",
        origin=_canonical_private_origin(DeltaRegion.INDIA, environment),
        mode=mode,
        credential_epoch=11,
        api_key=SecretStr(_API_KEY),
        api_secret=SecretStr(_API_SECRET),
        user_agent=_USER_AGENT,
    )


def _proof(
    marker: object,
    signer: object,
    *,
    environment: DeltaEnvironment = DeltaEnvironment.TESTNET,
    mode: ExecutionMode = ExecutionMode.PAPER,
) -> tuple[object, object]:
    return _support().issue_account_binding_proof(
        marker,
        signer,
        proof_id=_PROOF_ID,
        account=_account(environment=environment, mode=mode),
        origin=_canonical_private_origin(DeltaRegion.INDIA, environment),
        binding_ref=_BINDING_REF,
        probe_ref=_PROBE_REF,
        credential_epoch=11,
        proof_epoch=13,
        observed_at=_OBSERVED,
        expires_at=_EXPIRES,
    )


def _assert_safe_failure(call: object) -> None:
    assert callable(call)
    with pytest.raises(ConfigurationError) as captured:
        call()  # type: ignore[operator]
    assert captured.value.context == {"operation": "task27_private_credentials"}
    rendered = f"{captured.value!s} {captured.value!r}"
    assert _API_KEY not in rendered
    assert _API_SECRET not in rendered


def _revalidate_enrollment(signer: object, currentness: object) -> object:
    return currentness.revalidate_enrollment_credential_nonblocking(
        signer=signer,
        expected_account_id=_ACCOUNT_ID,
        expected_external_user_id="12345",
        expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
        expected_credential_version=7,
    )


def _revalidate_private(signer: object, proof: object, currentness: object) -> object:
    return currentness.revalidate_private_read_nonblocking(
        signer=signer,
        proof=proof,
        expected_account=_account(),
        expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
        expected_credential_version=7,
    )


def test_slot_and_exact_pydantic_alias_accept_only_canonical_members() -> None:
    assert tuple(credentials.IndiaCredentialSlot) == (
        credentials.IndiaCredentialSlot.INDIA_TESTNET,
        credentials.IndiaCredentialSlot.INDIA_PROD,
    )
    assert credentials.IndiaCredentialSlot.INDIA_TESTNET.value == "DELTA_INDIA_TESTNET"
    assert credentials.IndiaCredentialSlot.INDIA_PROD.value == "DELTA_INDIA_PROD"
    adapter = TypeAdapter(credentials.ExactIndiaCredentialSlot)
    member = credentials.IndiaCredentialSlot.INDIA_TESTNET
    assert adapter.validate_python(member) is member
    for invalid in ("DELTA_INDIA_TESTNET", DeltaEnvironment.TESTNET, object()):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_snapshots_expose_only_exact_safe_fields_and_same_identity(
    task27_private_credentials_marker: object,
) -> None:
    signer, enrollment = _issue(task27_private_credentials_marker)
    proof, currentness = _proof(task27_private_credentials_marker, signer)

    assert type(signer) is credentials.OwnedDeltaSignerSnapshot
    assert type(proof) is credentials.AccountBindingProofSnapshot
    assert type(currentness) is credentials.Task27PrivateCredentialCurrentnessPort
    assert type(enrollment) is credentials.Task27EnrollmentCredentialCurrentnessPort
    assert signer.snapshot_id == _SNAPSHOT_ID
    assert signer.credential_slot is credentials.IndiaCredentialSlot.INDIA_TESTNET
    assert signer.credential_version == 7
    assert signer.fingerprint_hint == hashlib.sha256(_API_KEY.encode()).hexdigest()[:12]
    assert proof.proof_id == _PROOF_ID
    assert proof.account == _account()
    assert proof.account is not proof.account
    assert proof.origin is _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET)
    assert proof.credential_slot is credentials.IndiaCredentialSlot.INDIA_TESTNET
    assert proof.credential_version == 7
    assert proof.binding_ref == _BINDING_REF
    assert proof.probe_ref == _PROBE_REF
    assert proof.credential_epoch == 11
    assert proof.proof_epoch == 13
    assert proof.observed_at == _OBSERVED
    assert proof.expires_at == _EXPIRES
    assert _revalidate_enrollment(signer, enrollment) is signer
    assert _revalidate_private(signer, proof, currentness) is proof
    assert repr(signer) == "OwnedDeltaSignerSnapshot(<redacted>)"
    assert repr(proof) == "AccountBindingProofSnapshot(<redacted>)"
    assert repr(currentness) == "Task27PrivateCredentialCurrentnessPort(<redacted>)"
    assert repr(enrollment) == "Task27EnrollmentCredentialCurrentnessPort(<redacted>)"

    assert {name for name in dir(signer) if not name.startswith("_")} == {
        "credential_slot",
        "credential_version",
        "fingerprint_hint",
        "snapshot_id",
    }
    assert {name for name in dir(proof) if not name.startswith("_")} == {
        "account",
        "binding_ref",
        "credential_epoch",
        "credential_slot",
        "credential_version",
        "expires_at",
        "observed_at",
        "origin",
        "probe_ref",
        "proof_epoch",
        "proof_id",
    }


def test_explicit_timestamp_signing_reattests_a5_request_and_never_reads_a_clock(
    task27_private_credentials_marker: object,
) -> None:
    signer, _enrollment = _issue(task27_private_credentials_marker)
    request = _freeze_balances_private_read(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )
    timestamp = 1_799_999_999

    signed = credentials._sign_owned_private_read_at(
        signer,
        request,
        timestamp=timestamp,
    )

    assert type(signed) is SignedAuthHeaders
    headers = signed.as_headers()
    prehash = b"GET" + str(timestamp).encode() + b"/v2/wallet/balances"
    expected = hmac.new(_API_SECRET.encode(), prehash, hashlib.sha256).hexdigest()
    assert headers == {
        "api-key": _API_KEY,
        "timestamp": str(timestamp),
        "signature": expected,
        "User-Agent": _USER_AGENT,
    }
    assert tuple(inspect.signature(credentials._sign_owned_private_read_at).parameters) == (
        "snapshot",
        "request",
        "timestamp",
    )
    assert "sign_now" not in dir(signer)

    object.__setattr__(request, "_path", "/v2/orders")
    _assert_safe_failure(
        lambda: credentials._sign_owned_private_read_at(
            signer,
            request,
            timestamp=timestamp,
        )
    )


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_all_owner_objects_deny_copy_and_serialization(
    operation: object,
    task27_private_credentials_marker: object,
) -> None:
    signer, enrollment = _issue(task27_private_credentials_marker)
    proof, currentness = _proof(task27_private_credentials_marker, signer)
    assert callable(operation)
    for value in (signer, proof, currentness, enrollment):
        with pytest.raises(TypeError):
            operation(value)  # type: ignore[operator]


def test_owner_classes_deny_construction_subclassing_and_reconstruction(
    task27_private_credentials_marker: object,
) -> None:
    classes = (
        credentials.OwnedDeltaSignerSnapshot,
        credentials.AccountBindingProofSnapshot,
        credentials.Task27PrivateCredentialCurrentnessPort,
        credentials.Task27EnrollmentCredentialCurrentnessPort,
    )
    for owner_type in classes:
        with pytest.raises(TypeError):
            owner_type()
        with pytest.raises(TypeError):
            type("Counterfeit", (owner_type,), {})

    signer, enrollment = _issue(task27_private_credentials_marker)
    counterfeit = object.__new__(credentials.OwnedDeltaSignerSnapshot)
    _assert_safe_failure(lambda: counterfeit.snapshot_id)
    _assert_safe_failure(lambda: _revalidate_enrollment(counterfeit, enrollment))
    counterfeit_proof = object.__new__(credentials.AccountBindingProofSnapshot)
    _assert_safe_failure(lambda: counterfeit_proof.proof_id)
    proof, currentness = _proof(task27_private_credentials_marker, signer)
    _assert_safe_failure(lambda: _revalidate_private(signer, counterfeit_proof, currentness))
    counterfeit_currentness = object.__new__(credentials.Task27PrivateCredentialCurrentnessPort)
    _assert_safe_failure(lambda: _revalidate_private(signer, proof, counterfeit_currentness))
    counterfeit_enrollment = object.__new__(credentials.Task27EnrollmentCredentialCurrentnessPort)
    _assert_safe_failure(lambda: _revalidate_enrollment(signer, counterfeit_enrollment))
    request = _freeze_balances_private_read(
        region=DeltaRegion.INDIA, environment=DeltaEnvironment.TESTNET
    )
    _assert_safe_failure(
        lambda: credentials._sign_owned_private_read_at(
            counterfeit, request, timestamp=1_799_999_999
        )
    )
    assert signer is not counterfeit


class _WrongSlot(StrEnum):
    INDIA_TESTNET = "DELTA_INDIA_TESTNET"


def test_issuer_rejects_wrong_slot_region_mode_and_raw_credential_objects(
    task27_private_credentials_marker: object,
) -> None:
    marker = task27_private_credentials_marker
    prod_origin = _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.PROD)
    base = dict(
        snapshot_id=_SNAPSHOT_ID,
        credential_version=7,
        account_id=_ACCOUNT_ID,
        external_user_id="12345",
        credential_epoch=11,
        api_key=SecretStr(_API_KEY),
        api_secret=SecretStr(_API_SECRET),
        user_agent=_USER_AGENT,
    )
    invalid_rows = (
        dict(credential_slot="DELTA_INDIA_TESTNET", origin=prod_origin, mode=ExecutionMode.LIVE),
        dict(credential_slot=_WrongSlot.INDIA_TESTNET, origin=prod_origin, mode=ExecutionMode.LIVE),
        dict(
            credential_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
            origin=prod_origin,
            mode=ExecutionMode.LIVE,
        ),
        dict(
            credential_slot=credentials.IndiaCredentialSlot.INDIA_PROD,
            origin=prod_origin,
            mode=ExecutionMode.PAPER,
        ),
        dict(
            credential_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
            origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            mode=ExecutionMode.BACKTEST,
        ),
    )
    for row in invalid_rows:
        _assert_safe_failure(
            lambda row=row: _support().issue_enrollment_credential(marker, **base, **row)
        )

    raw_signer = DeltaSigner(
        api_key=SecretStr(_API_KEY),
        api_secret=SecretStr(_API_SECRET),
        user_agent=_USER_AGENT,
        clock=lambda: 1,
    )
    request = _freeze_balances_private_read(
        region=DeltaRegion.INDIA, environment=DeltaEnvironment.TESTNET
    )
    for raw in (SecretStr(_API_SECRET), raw_signer):
        _assert_safe_failure(
            lambda raw=raw: credentials._sign_owned_private_read_at(
                raw, request, timestamp=1_799_999_999
            )
        )

    prod_signer, prod_enrollment = _issue(
        marker,
        environment=DeltaEnvironment.PROD,
        mode=ExecutionMode.LIVE,
        slot=credentials.IndiaCredentialSlot.INDIA_PROD,
    )
    assert type(prod_signer) is credentials.OwnedDeltaSignerSnapshot
    assert type(prod_enrollment) is credentials.Task27EnrollmentCredentialCurrentnessPort


def test_issuer_failure_traceback_never_retains_plaintext_credentials(
    task27_private_credentials_marker: object,
) -> None:
    with pytest.raises(ConfigurationError) as captured:
        _issue(task27_private_credentials_marker, snapshot_id=UUID(int=0))

    assert captured.value.context == {"operation": "task27_private_credentials"}
    failure = traceback.TracebackException.from_exception(
        captured.value,
        capture_locals=True,
    )
    owner_path = Path(credentials.__file__ or "").resolve()
    owner_frames = [
        frame for frame in failure.stack if Path(frame.filename).resolve() == owner_path
    ]
    assert [frame.name for frame in owner_frames] == ["_issue_enrollment_credential_for_tests"]
    rendered_locals = repr([frame.locals for frame in owner_frames])
    assert _API_KEY not in rendered_locals
    assert _API_SECRET not in rendered_locals


def test_currentness_rejects_cross_owner_and_every_expected_binding_mismatch(
    task27_private_credentials_marker: object,
) -> None:
    marker = task27_private_credentials_marker
    signer, _enrollment = _issue(marker)
    proof, currentness = _proof(marker, signer)
    other_signer, _other_enrollment = _issue(
        marker,
        snapshot_id=UUID("00000000-0000-0000-0000-000000002799"),
    )
    wrong_origin = _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.PROD)
    valid = dict(
        signer=signer,
        proof=proof,
        expected_account=_account(),
        expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
        expected_credential_version=7,
    )
    mismatches = (
        {"signer": other_signer},
        {"expected_account": _account(external_user_id="54321")},
        {"expected_account": _account(mode=ExecutionMode.LIVE)},
        {"expected_origin": wrong_origin},
        {"expected_slot": credentials.IndiaCredentialSlot.INDIA_PROD},
        {"expected_credential_version": 8},
    )
    for replacement in mismatches:
        values = {**valid, **replacement}
        _assert_safe_failure(
            lambda values=values: currentness.revalidate_private_read_nonblocking(**values)
        )


def test_enrollment_currentness_rejects_every_expected_binding_mismatch(
    task27_private_credentials_marker: object,
) -> None:
    signer, enrollment = _issue(task27_private_credentials_marker)
    valid = dict(
        signer=signer,
        expected_account_id=_ACCOUNT_ID,
        expected_external_user_id="12345",
        expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
        expected_credential_version=7,
    )
    mismatches = (
        {"expected_account_id": UUID("00000000-0000-0000-0000-000000002798")},
        {"expected_external_user_id": "54321"},
        {"expected_origin": _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.PROD)},
        {"expected_slot": credentials.IndiaCredentialSlot.INDIA_PROD},
        {"expected_credential_version": 8},
    )
    for replacement in mismatches:
        values = {**valid, **replacement}
        _assert_safe_failure(
            lambda values=values: enrollment.revalidate_enrollment_credential_nonblocking(**values)
        )


def test_proof_issuer_rejects_inconsistent_account_evidence_and_stale_equality(
    task27_private_credentials_marker: object,
) -> None:
    marker = task27_private_credentials_marker
    signer, _enrollment = _issue(marker)
    valid = dict(
        proof_id=_PROOF_ID,
        account=_account(),
        origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        binding_ref=_BINDING_REF,
        probe_ref=_PROBE_REF,
        credential_epoch=11,
        proof_epoch=13,
        observed_at=_OBSERVED,
        expires_at=_EXPIRES,
    )
    mismatches = (
        {"account": _account(external_user_id="54321")},
        {"account": _account(mode=ExecutionMode.LIVE)},
        {"account": _account(credential_version=8)},
        {"origin": _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.PROD)},
        {"credential_epoch": 12},
        {"observed_at": _EXPIRES},
        {"expires_at": _OBSERVED},
    )
    for replacement in mismatches:
        values = {**valid, **replacement}
        _assert_safe_failure(
            lambda values=values: _support().issue_account_binding_proof(
                marker,
                signer,
                **values,
            )
        )


def test_signing_rejects_cross_environment_invalid_timestamp_and_revocation(
    task27_private_credentials_marker: object,
) -> None:
    marker = task27_private_credentials_marker
    signer, enrollment = _issue(marker)
    prod_request = _freeze_balances_private_read(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.PROD,
    )
    _assert_safe_failure(
        lambda: credentials._sign_owned_private_read_at(
            signer,
            prod_request,
            timestamp=1_799_999_999,
        )
    )
    testnet_request = _freeze_balances_private_read(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )
    for invalid_timestamp in (True, 0, -1, 10_000_000_000):
        _assert_safe_failure(
            lambda invalid_timestamp=invalid_timestamp: credentials._sign_owned_private_read_at(
                signer,
                testnet_request,
                timestamp=invalid_timestamp,
            )
        )
    _support().invalidate_credential(marker, enrollment)
    _assert_safe_failure(
        lambda: credentials._sign_owned_private_read_at(
            signer,
            testnet_request,
            timestamp=1_799_999_999,
        )
    )


def test_stale_epoch_permission_and_revocation_publications_fail_closed(
    task27_private_credentials_marker: object,
) -> None:
    marker = task27_private_credentials_marker
    signer, enrollment = _issue(marker)
    proof, currentness = _proof(marker, signer)
    _support().advance_observed_at(marker, currentness, _EXPIRES)
    _assert_safe_failure(lambda: _revalidate_private(signer, proof, currentness))
    assert _revalidate_enrollment(signer, enrollment) is signer

    for mutation in (
        _support().advance_credential_epoch,
        _support().advance_proof_epoch,
        _support().invalidate_permission_probe,
        _support().invalidate_credential,
    ):
        signer, enrollment = _issue(marker)
        proof, currentness = _proof(marker, signer)
        mutation(marker, currentness)
        _assert_safe_failure(
            lambda signer=signer, proof=proof, currentness=currentness: _revalidate_private(
                signer, proof, currentness
            )
        )
        if mutation in (
            _support().advance_credential_epoch,
            _support().invalidate_credential,
        ):
            _assert_safe_failure(
                lambda signer=signer, enrollment=enrollment: _revalidate_enrollment(
                    signer, enrollment
                )
            )


def test_both_signature_expiry_paths_validate_then_terminally_invalidate(
    task27_private_credentials_marker: object,
) -> None:
    marker = task27_private_credentials_marker
    signer, enrollment = _issue(marker)
    proof, currentness = _proof(marker, signer)
    _assert_safe_failure(
        lambda: currentness.invalidate_signature_expired_nonblocking(
            signer=signer,
            proof=proof,
            correlation_id=True,
        )
    )
    assert _revalidate_private(signer, proof, currentness) is proof
    currentness.invalidate_signature_expired_nonblocking(
        signer=signer,
        proof=proof,
        correlation_id=_CORRELATION_ID,
    )
    _assert_safe_failure(lambda: _revalidate_private(signer, proof, currentness))
    _assert_safe_failure(lambda: _revalidate_enrollment(signer, enrollment))

    signer, enrollment = _issue(marker)
    enrollment.invalidate_enrollment_signature_expired_nonblocking(
        signer=signer,
        expected_account_id=_ACCOUNT_ID,
        correlation_id=_CORRELATION_ID,
    )
    _assert_safe_failure(lambda: _revalidate_enrollment(signer, enrollment))


def test_hostile_structural_substitutes_are_rejected_before_callbacks(
    task27_private_credentials_marker: object,
) -> None:
    touched: list[str] = []

    class Hostile:
        def __getattribute__(self, name: str) -> object:
            touched.append(name)
            raise AssertionError("hostile attribute reached")

        def __eq__(self, other: object) -> bool:
            touched.append("eq")
            raise AssertionError("hostile equality reached")

        def __hash__(self) -> int:
            touched.append("hash")
            raise AssertionError("hostile hash reached")

    signer, enrollment = _issue(task27_private_credentials_marker)
    proof, currentness = _proof(task27_private_credentials_marker, signer)
    hostile = Hostile()
    _assert_safe_failure(
        lambda: currentness.revalidate_private_read_nonblocking(
            signer=hostile,
            proof=proof,
            expected_account=_account(),
            expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
            expected_credential_version=7,
        )
    )
    _assert_safe_failure(lambda: _revalidate_enrollment(hostile, enrollment))
    _assert_safe_failure(
        lambda: credentials._sign_owned_private_read_at(
            signer,
            hostile,
            timestamp=1_799_999_999,
        )
    )
    assert touched == []


def test_owner_objects_are_immutable_and_equality_free(
    task27_private_credentials_marker: object,
) -> None:
    signer, enrollment = _issue(task27_private_credentials_marker)
    proof, currentness = _proof(task27_private_credentials_marker, signer)
    for value in (signer, proof, enrollment, currentness):
        with pytest.raises(TypeError):
            hash(value)
        with pytest.raises(TypeError):
            operator.eq(value, value)
        with pytest.raises(AttributeError):
            value.__setattr__("replacement", object())
        with pytest.raises(AttributeError):
            delattr(value, "replacement")


def test_secret_canaries_never_appear_in_owner_surfaces_or_failures(
    task27_private_credentials_marker: object,
) -> None:
    signer, enrollment = _issue(task27_private_credentials_marker)
    proof, currentness = _proof(task27_private_credentials_marker, signer)
    rendered = " ".join(
        f"{value!s} {value!r}" for value in (signer, proof, enrollment, currentness)
    )
    assert _API_KEY not in rendered
    assert _API_SECRET not in rendered
    for value in (signer, proof, enrollment, currentness):
        assert not hasattr(value, "__dict__")
        public = {name.casefold() for name in dir(value) if not name.startswith("_")}
        assert not any(
            fragment in name
            for name in public
            for fragment in ("api_key", "secret", "headers", "sign_now")
        )


def test_support_requires_exact_marker_and_production_module_is_inert_and_leaf() -> None:
    _assert_safe_failure(
        lambda: _support().issue_enrollment_credential(
            object(),
            snapshot_id=_SNAPSHOT_ID,
            credential_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
            credential_version=7,
            account_id=_ACCOUNT_ID,
            external_user_id="12345",
            origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            mode=ExecutionMode.PAPER,
            credential_epoch=11,
            api_key=SecretStr(_API_KEY),
            api_secret=SecretStr(_API_SECRET),
            user_agent=_USER_AGENT,
        )
    )
    _assert_safe_failure(
        lambda: _support().issue_account_binding_proof(
            object(),
            object(),
            proof_id=_PROOF_ID,
            account=_account(),
            origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            binding_ref=_BINDING_REF,
            probe_ref=_PROBE_REF,
            credential_epoch=11,
            proof_epoch=13,
            observed_at=_OBSERVED,
            expires_at=_EXPIRES,
        )
    )
    validation = importlib.import_module("hmt_crypto.exchange._credential_validation")
    source_path = Path(credentials.__file__ or "")
    validation_path = Path(validation.__file__ or "")
    forbidden = {
        "asyncio",
        "httpx",
        "os",
        "pathlib",
        "socket",
        "sqlalchemy",
        "hmt_crypto.exchange.client",
        "hmt_crypto.persistence",
    }
    for path in (source_path, validation_path):
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any(
            imported == name or imported.startswith(f"{name}.")
            for imported in imports
            for name in forbidden
        )
        assert not any(
            isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.Lambda))
            for node in ast.walk(tree)
        )
    validation_tree = ast.parse(validation_path.read_text(), filename=str(validation_path))
    validation_imports = {
        node.module or "" for node in ast.walk(validation_tree) if isinstance(node, ast.ImportFrom)
    }
    assert "hmt_crypto.exchange.credentials" not in validation_imports
    assert not any(
        name in vars(validation)
        for name in (
            "OwnedDeltaSignerSnapshot",
            "Task27PrivateCredentialCurrentnessPort",
            "_SIGNER_STATES",
            "_issue_enrollment_credential_for_tests",
        )
    )
    script = textwrap.dedent(
        """
        import importlib
        import sys

        sys.path.insert(0, ".")
        sys.path.insert(0, "src")
        validation = importlib.import_module(
            "hmt_crypto.exchange._credential_validation"
        )
        assert "hmt_crypto.exchange.credentials" not in sys.modules
        assert not hasattr(validation, "_SIGNER_STATES")
        importlib.import_module(
            "tests.integration.support.task27_private_credentials_issuer"
        )
        credentials = importlib.import_module("hmt_crypto.exchange.credentials")
        assert credentials._SIGNER_STATES == {}
        assert credentials._PROOF_STATES == {}
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=source_path.parents[3],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
