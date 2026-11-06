"""Adversarial lifecycle, traceback, and filesystem tests for credential ownership."""

from __future__ import annotations

import gc
import json
import os
import weakref
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from threading import Event, Thread
from types import FrameType, ModuleType, TracebackType
from uuid import UUID

import pytest
from pydantic import SecretStr

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode, Settings
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange import _credential_validation as credential_validation
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.credential_provider import build_credential_provider
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin
from hmt_crypto.exchange.delta.private_read_wire import _freeze_balances_private_read
from hmt_crypto.exchange.delta.signer import DeltaSigner

pytestmark = pytest.mark.unit

_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000002761")
_SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000002762")
_PROOF_ID = UUID("00000000-0000-0000-0000-000000002763")
_BINDING_REF = UUID("00000000-0000-0000-0000-000000002764")
_PROBE_REF = UUID("00000000-0000-0000-0000-000000002765")
_KEY = "security-api-key-canary"
_SECRET = "security-api-secret-canary"


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "DELTA_REGION": "india",
        "DELTA_ENV": "testnet",
        "DELTA_INDIA_TESTNET_API_KEY": _KEY,
        "DELTA_INDIA_TESTNET_API_SECRET": _SECRET,
    }
    values.update(changes)
    return Settings(**values)


def _secret_file(path: Path, *, key: str = _KEY, secret: str = _SECRET) -> Path:
    path.write_text(
        json.dumps({"api_key": key, "api_secret": secret}, separators=(",", ":")),
        encoding="ascii",
    )
    path.chmod(0o600)
    return path


def _issue(provider: object) -> tuple[object, object]:
    return provider.enrollment_issuer.issue_enrollment_credential(
        snapshot_id=_SNAPSHOT_ID,
        credential_version=7,
        account_id=_ACCOUNT_ID,
        external_user_id="12345",
        origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        mode=ExecutionMode.PAPER,
        credential_epoch=11,
        user_agent="hmt-crypto/security-test",
    )


def _production_locals(error: BaseException) -> list[object]:
    values: list[object] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        values.extend(current.args)
        values.extend(vars(current).values())
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        traceback = current.__traceback__
        while traceback is not None:
            filename = traceback.tb_frame.f_code.co_filename
            if "/src/hmt_crypto/" in filename:
                values.extend(traceback.tb_frame.f_locals.values())
            traceback = traceback.tb_next
    return values


def _reachable_plaintext(roots: list[object]) -> set[str]:
    pending: list[tuple[object, int]] = [(value, 0) for value in roots]
    seen: set[int] = set()
    found: set[str] = set()
    while pending:
        value, depth = pending.pop()
        if id(value) in seen or depth > 10:
            continue
        seen.add(id(value))
        if type(value) is str:
            found.add(value)
            continue
        if type(value) is SecretStr:
            found.add(value.get_secret_value())
            continue
        if value is None or type(value) in (int, float, bool, bytes, type):
            continue
        if isinstance(value, (ModuleType, FrameType, TracebackType)) or callable(value):
            continue
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for pair in value.items() for item in pair)
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            pending.extend((item, depth + 1) for item in value)
            continue
        attributes = getattr(value, "__dict__", None)
        if type(attributes) is dict:
            pending.extend((item, depth + 1) for item in attributes.values())
        for owner in type(value).__mro__:
            slots = vars(owner).get("__slots__", ())
            if type(slots) is str:
                slots = (slots,)
            for slot in slots:
                if slot == "__weakref__":
                    continue
                attribute = (
                    f"_{owner.__name__.lstrip('_')}{slot}"
                    if slot.startswith("__") and not slot.endswith("__")
                    else slot
                )
                try:
                    item = object.__getattribute__(value, attribute)
                except Exception:
                    continue
                pending.append((item, depth + 1))
    return found


def _assert_traceback_secret_free(error: BaseException, *secrets: str) -> None:
    reachable = _reachable_plaintext(_production_locals(error))
    rendered = f"{error!s} {error!r} {error.__cause__!r} {error.__context__!r}"
    for secret in secrets:
        assert secret not in reachable
        assert secret not in rendered


def test_invalid_user_agent_failure_traceback_cannot_recover_inline_secrets() -> None:
    provider = build_credential_provider(_settings())
    with pytest.raises(ConfigurationError) as captured:
        provider.enrollment_issuer.issue_enrollment_credential(
            snapshot_id=_SNAPSHOT_ID,
            credential_version=7,
            account_id=_ACCOUNT_ID,
            external_user_id="12345",
            origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            mode=ExecutionMode.PAPER,
            credential_epoch=11,
            user_agent="bad\nagent",
        )
    _assert_traceback_secret_free(captured.value, _KEY, _SECRET)
    provider.close()


def test_invalid_enrollment_revalidation_traceback_cannot_recover_inline_secrets() -> None:
    provider = build_credential_provider(_settings())
    signer, currentness = _issue(provider)
    with pytest.raises(ConfigurationError) as captured:
        currentness.revalidate_enrollment_credential_nonblocking(
            signer=signer,
            expected_account_id=_ACCOUNT_ID,
            expected_external_user_id="54321",
            expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
            expected_credential_version=7,
        )
    _assert_traceback_secret_free(captured.value, _KEY, _SECRET)
    provider.close()


def test_invalid_enrollment_invalidation_traceback_cannot_recover_inline_secrets() -> None:
    provider = build_credential_provider(_settings())
    signer, currentness = _issue(provider)
    with pytest.raises(ConfigurationError) as captured:
        currentness.invalidate_enrollment_signature_expired_nonblocking(
            signer=signer,
            expected_account_id=_ACCOUNT_ID,
            correlation_id=True,
        )
    _assert_traceback_secret_free(captured.value, _KEY, _SECRET)
    provider.close()


def _proof_pair(signer: object) -> tuple[object, object]:
    observed = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    return credentials._issue_account_binding_proof_for_tests(
        credentials._task27_fixture_marker_for_tests(),
        signer,
        proof_id=_PROOF_ID,
        account=AccountRef(
            account_id=_ACCOUNT_ID,
            external_user_id="12345",
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
            mode=ExecutionMode.PAPER,
            credential_version=7,
        ),
        origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        binding_ref=_BINDING_REF,
        probe_ref=_PROBE_REF,
        credential_epoch=11,
        proof_epoch=13,
        observed_at=observed,
        expires_at=observed + timedelta(seconds=30),
    )


def test_invalid_proof_revalidation_traceback_cannot_recover_inline_secrets() -> None:
    provider = build_credential_provider(_settings())
    signer, _enrollment = _issue(provider)
    proof, currentness = _proof_pair(signer)
    with pytest.raises(ConfigurationError) as captured:
        currentness.revalidate_private_read_nonblocking(
            signer=signer,
            proof=proof,
            expected_account=AccountRef(
                account_id=_ACCOUNT_ID,
                external_user_id="54321",
                region=DeltaRegion.INDIA,
                environment=DeltaEnvironment.TESTNET,
                mode=ExecutionMode.PAPER,
                credential_version=7,
            ),
            expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
            expected_credential_version=7,
        )
    _assert_traceback_secret_free(captured.value, _KEY, _SECRET)
    provider.close()


def test_invalid_proof_invalidation_traceback_cannot_recover_inline_secrets() -> None:
    provider = build_credential_provider(_settings())
    signer, _enrollment = _issue(provider)
    proof, currentness = _proof_pair(signer)
    with pytest.raises(ConfigurationError) as captured:
        currentness.invalidate_signature_expired_nonblocking(
            signer=signer,
            proof=proof,
            correlation_id=True,
        )
    _assert_traceback_secret_free(captured.value, _KEY, _SECRET)
    provider.close()


def test_build_and_reload_file_failures_have_secret_free_traceback_graphs(
    tmp_path: Path,
) -> None:
    path = _secret_file(tmp_path / "delta.json")
    settings = _settings(
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
    )
    provider = build_credential_provider(settings)
    replacement_key = "replacement-trace-key-canary"
    replacement_secret = "replacement-trace-secret-canary"
    path.write_text(
        json.dumps(
            {"api_key": replacement_key, "api_secret": replacement_secret, "extra": _SECRET}
        ),
        encoding="ascii",
    )
    path.chmod(0o600)
    with pytest.raises(ConfigurationError) as reload_error:
        provider.reload()
    _assert_traceback_secret_free(
        reload_error.value,
        _KEY,
        _SECRET,
        replacement_key,
        replacement_secret,
    )

    with pytest.raises(ConfigurationError) as build_error:
        build_credential_provider(settings)
    _assert_traceback_secret_free(
        build_error.value,
        _KEY,
        _SECRET,
        replacement_key,
        replacement_secret,
    )
    provider.close()


def test_reload_close_and_gc_remove_owned_registry_graphs() -> None:
    provider = build_credential_provider(_settings())
    signer, enrollment = _issue(provider)
    signer_id, enrollment_id = id(signer), id(enrollment)
    state = credentials._SIGNER_STATES[signer_id]
    state_reference = weakref.ref(state)

    provider.reload()

    assert signer_id not in credentials._SIGNER_STATES
    assert enrollment_id not in credentials._ENROLLMENT_STATES
    del state
    gc.collect()
    assert state_reference() is None

    replacement, replacement_enrollment = _issue(provider)
    replacement_id = id(replacement)
    replacement_enrollment_id = id(replacement_enrollment)
    provider.close()
    assert replacement_id not in credentials._SIGNER_STATES
    assert replacement_enrollment_id not in credentials._ENROLLMENT_STATES

    collected_provider = build_credential_provider(_settings())
    collected_signer, collected_enrollment = _issue(collected_provider)
    collected_state = credentials._SIGNER_STATES[id(collected_signer)]
    collected_state_reference = weakref.ref(collected_state)
    provider_reference = weakref.ref(collected_provider)
    collected_signer_id = id(collected_signer)
    collected_enrollment_id = id(collected_enrollment)
    del collected_state, collected_provider
    gc.collect()
    assert provider_reference() is None
    assert collected_state_reference() is None
    assert collected_signer_id not in credentials._SIGNER_STATES
    assert collected_enrollment_id not in credentials._ENROLLMENT_STATES


def test_close_removes_proof_graphs_derived_from_owned_signer() -> None:
    provider = build_credential_provider(_settings())
    signer, _enrollment = _issue(provider)
    observed = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    proof, currentness = credentials._issue_account_binding_proof_for_tests(
        credentials._task27_fixture_marker_for_tests(),
        signer,
        proof_id=_PROOF_ID,
        account=AccountRef(
            account_id=_ACCOUNT_ID,
            external_user_id="12345",
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
            mode=ExecutionMode.PAPER,
            credential_version=7,
        ),
        origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        binding_ref=_BINDING_REF,
        probe_ref=_PROBE_REF,
        credential_epoch=11,
        proof_epoch=13,
        observed_at=observed,
        expires_at=observed + timedelta(seconds=30),
    )

    provider.close()

    assert id(proof) not in credentials._PROOF_STATES
    assert id(currentness) not in credentials._CURRENTNESS_STATES


def test_reload_waits_for_inflight_sign_and_old_signer_fails_after_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = build_credential_provider(_settings())
    signer, _enrollment = _issue(provider)
    request = _freeze_balances_private_read(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )
    entered, release, reload_started, reload_done = Event(), Event(), Event(), Event()
    signed: list[object] = []
    errors: list[BaseException] = []
    original = DeltaSigner.sign_at

    def blocking_sign(self: DeltaSigner, *args: object, **kwargs: object) -> object:
        entered.set()
        assert release.wait(2)
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    def sign() -> None:
        try:
            signed.append(
                credentials._sign_owned_private_read_at(signer, request, timestamp=1_799_999_999)
            )
        except BaseException as error:
            errors.append(error)

    def reload_provider() -> None:
        reload_started.set()
        try:
            provider.reload()
        except BaseException as error:
            errors.append(error)
        finally:
            reload_done.set()

    monkeypatch.setattr(DeltaSigner, "sign_at", blocking_sign)
    sign_thread = Thread(target=sign)
    sign_thread.start()
    assert entered.wait(2)
    reload_thread = Thread(target=reload_provider)
    reload_thread.start()
    assert reload_started.wait(2)
    assert not reload_done.wait(0.05)
    release.set()
    sign_thread.join(2)
    reload_thread.join(2)
    assert errors == [] and len(signed) == 1 and reload_done.is_set()
    with pytest.raises(ConfigurationError):
        credentials._sign_owned_private_read_at(signer, request, timestamp=1_800_000_000)
    provider.close()


def test_reload_waits_for_inflight_currentness_revalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = build_credential_provider(_settings())
    signer, currentness = _issue(provider)
    entered, release, reload_done = Event(), Event(), Event()
    returned: list[object] = []
    errors: list[BaseException] = []
    original = credential_validation._external_user

    def blocking_user(value: object) -> str:
        entered.set()
        assert release.wait(2)
        return original(value)

    def revalidate() -> None:
        try:
            returned.append(
                currentness.revalidate_enrollment_credential_nonblocking(
                    signer=signer,
                    expected_account_id=_ACCOUNT_ID,
                    expected_external_user_id="12345",
                    expected_origin=_canonical_private_origin(
                        DeltaRegion.INDIA, DeltaEnvironment.TESTNET
                    ),
                    expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
                    expected_credential_version=7,
                )
            )
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(credential_validation, "_external_user", blocking_user)
    currentness_thread = Thread(target=revalidate)
    currentness_thread.start()
    assert entered.wait(2)
    reload_thread = Thread(target=lambda: (provider.reload(), reload_done.set()))
    reload_thread.start()
    assert not reload_done.wait(0.05)
    release.set()
    currentness_thread.join(2)
    reload_thread.join(2)
    assert errors == [] and returned == [signer] and reload_done.is_set()
    with pytest.raises(ConfigurationError):
        currentness.revalidate_enrollment_credential_nonblocking(
            signer=signer,
            expected_account_id=_ACCOUNT_ID,
            expected_external_user_id="12345",
            expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
            expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
            expected_credential_version=7,
        )
    provider.close()


@pytest.mark.parametrize("replacement_kind", ["rename", "symlink"])
def test_secret_file_ancestor_replacement_during_read_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    selected_root = tmp_path / "selected"
    selected_root.mkdir()
    selected_path = _secret_file(selected_root / "delta.json")
    attacker_root = tmp_path / "attacker"
    attacker_root.mkdir()
    attacker_key = "attacker-key-canary"
    attacker_secret = "attacker-secret-canary"
    _secret_file(attacker_root / "delta.json", key=attacker_key, secret=attacker_secret)
    settings = _settings(
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(selected_path),
    )
    file_module = import_module("hmt_crypto.exchange._credential_file")
    real_read = os.read
    moved = tmp_path / "selected-moved"

    def replacing_read(descriptor: int, count: int) -> bytes:
        payload = real_read(descriptor, count)
        selected_root.rename(moved)
        if replacement_kind == "symlink":
            selected_root.symlink_to(attacker_root, target_is_directory=True)
        else:
            attacker_root.rename(selected_root)
        return payload

    monkeypatch.setattr(file_module.os, "read", replacing_read)
    with pytest.raises(ConfigurationError) as captured:
        build_credential_provider(settings)
    _assert_traceback_secret_free(
        captured.value,
        _KEY,
        _SECRET,
        attacker_key,
        attacker_secret,
    )
