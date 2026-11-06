"""Production credential-provider ownership and secret-file safety."""

from __future__ import annotations

import ast
import copy
import gc
import hashlib
import inspect
import json
import os
import pickle
import weakref
from importlib import import_module
from pathlib import Path
from threading import Event, Thread
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode, Settings
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange import credential_provider as provider_module
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.credential_provider import (
    CredentialProvider,
    CredentialSourceKind,
    Task27EnrollmentCapabilityIssuer,
    build_credential_provider,
)
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin

pytestmark = pytest.mark.unit

_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000002751")
_SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000002752")
_KEY = "provider-api-key-canary"
_SECRET = "provider-api-secret-canary"


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


def _issue(issuer: Task27EnrollmentCapabilityIssuer) -> tuple[object, object]:
    return issuer.issue_enrollment_credential(
        snapshot_id=_SNAPSHOT_ID,
        credential_version=7,
        account_id=_ACCOUNT_ID,
        external_user_id="12345",
        origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        mode=ExecutionMode.PAPER,
        credential_epoch=11,
        user_agent="hmt-crypto/provider-test",
    )


def _revalidate(signer: object, currentness: object) -> object:
    return currentness.revalidate_enrollment_credential_nonblocking(
        signer=signer,
        expected_account_id=_ACCOUNT_ID,
        expected_external_user_id="12345",
        expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        expected_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
        expected_credential_version=7,
    )


def _safe_failure(call: object, *other_secrets: str) -> None:
    assert callable(call)
    with pytest.raises(ConfigurationError) as captured:
        call()  # type: ignore[operator]
    rendered = f"{captured.value!s} {captured.value!r}"
    for secret in (_KEY, _SECRET, *other_secrets):
        assert secret not in rendered


def _secret_file(path: Path, *, key: str = _KEY, secret: str = _SECRET) -> Path:
    path.write_text(
        json.dumps({"api_key": key, "api_secret": secret}, separators=(",", ":")),
        encoding="ascii",
    )
    path.chmod(0o600)
    return path


def test_inline_provider_issues_only_exact_sealed_enrollment_pair() -> None:
    provider = build_credential_provider(_settings())

    assert type(provider) is CredentialProvider
    assert provider.source_kind is CredentialSourceKind.INLINE
    assert provider.credential_slot is credentials.IndiaCredentialSlot.INDIA_TESTNET
    assert provider.fingerprint_hint == hashlib.sha256(_KEY.encode("ascii")).hexdigest()[:12]
    issuer = provider.enrollment_issuer
    assert type(issuer) is Task27EnrollmentCapabilityIssuer
    assert provider.enrollment_issuer is issuer
    signer, currentness = _issue(issuer)

    assert type(signer) is credentials.OwnedDeltaSignerSnapshot
    assert type(currentness) is credentials.Task27EnrollmentCredentialCurrentnessPort
    assert signer.fingerprint_hint == provider.fingerprint_hint
    assert _revalidate(signer, currentness) is signer
    for rendered in (repr(provider), str(provider), repr(issuer), str(issuer)):
        assert _KEY not in rendered and _SECRET not in rendered


def test_provider_authorities_expose_no_secret_signer_or_runtime_port() -> None:
    provider = build_credential_provider(_settings())
    issuer = provider.enrollment_issuer

    assert provider_module.__all__ == [
        "CredentialProvider",
        "CredentialSourceKind",
        "Task27EnrollmentCapabilityIssuer",
        "build_credential_provider",
    ]
    assert not hasattr(provider_module, "Task27EnrollmentCapabilityIssuerPort")
    for value in (provider, issuer):
        assert not hasattr(value, "__dict__")
        names = dir(value)
        assert all(
            fragment not in name.lower() for name in names for fragment in ("api_key", "api_secret")
        )
        assert all("signer" not in name.lower() for name in names)
    rendered = repr(vars(provider_module)) + repr(
        inspect.getclosurevars(provider_module._ISSUE_ENROLLMENT)
    )
    assert _KEY not in rendered and _SECRET not in rendered


def test_provider_source_has_no_network_time_database_or_signing_operation() -> None:
    path = Path(provider_module.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = ("httpx", "requests", "socket", "time", "sqlalchemy", "hmt_crypto.persistence")
    assert not any(
        imported == name or imported.startswith(f"{name}.")
        for imported in imports
        for name in forbidden
    )
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert attributes.isdisjoint(
        {"sign_at", "sign_now", "as_headers", "place_order", "request", "send"}
    )
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))


def test_reload_and_close_invalidate_every_previously_issued_pair() -> None:
    provider = build_credential_provider(_settings())
    issuer = provider.enrollment_issuer
    first, first_currentness = _issue(issuer)

    provider.reload()
    _safe_failure(lambda: _revalidate(first, first_currentness))
    second, second_currentness = _issue(issuer)
    assert _revalidate(second, second_currentness) is second

    provider.close()
    provider.close()
    _safe_failure(lambda: _revalidate(second, second_currentness))
    _safe_failure(lambda: _issue(issuer))
    _safe_failure(provider.reload)


def test_reload_is_linearized_with_inflight_issuance(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = build_credential_provider(_settings())
    issuer = provider.enrollment_issuer
    constructor_entered = Event()
    permit_constructor = Event()
    reload_finished = Event()
    issued: list[tuple[object, object]] = []
    errors: list[BaseException] = []
    real_signer = provider_module.DeltaSigner

    def blocking_signer(**values: object) -> object:
        constructor_entered.set()
        assert permit_constructor.wait(2)
        return real_signer(**values)  # type: ignore[arg-type]

    def issue() -> None:
        try:
            issued.append(_issue(issuer))
        except BaseException as error:
            errors.append(error)

    def reload_provider() -> None:
        try:
            provider.reload()
        except BaseException as error:
            errors.append(error)
        finally:
            reload_finished.set()

    monkeypatch.setattr(provider_module, "DeltaSigner", blocking_signer)
    issue_thread = Thread(target=issue)
    issue_thread.start()
    assert constructor_entered.wait(2)
    reload_thread = Thread(target=reload_provider)
    reload_thread.start()
    assert not reload_finished.wait(0.05)
    permit_constructor.set()
    issue_thread.join(2)
    reload_thread.join(2)

    assert errors == []
    assert len(issued) == 1
    assert reload_finished.is_set()
    _safe_failure(lambda: _revalidate(*issued[0]))


def test_provider_collection_revokes_issuer_and_every_issued_pair() -> None:
    provider = build_credential_provider(_settings())
    issuer = provider.enrollment_issuer
    signer, currentness = _issue(issuer)
    reference = weakref.ref(provider)

    del provider
    gc.collect()

    assert reference() is None
    _safe_failure(lambda: _revalidate(signer, currentness))
    _safe_failure(lambda: _issue(issuer))


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_provider_and_issuer_deny_copy_and_serialization(operation: object) -> None:
    provider = build_credential_provider(_settings())
    for value in (provider, provider.enrollment_issuer):
        with pytest.raises(TypeError):
            operation(value)  # type: ignore[operator]


def test_classes_deny_direct_construction_subclass_and_object_new_counterfeits() -> None:
    for owner_type in (CredentialProvider, Task27EnrollmentCapabilityIssuer):
        with pytest.raises(TypeError):
            owner_type()
        with pytest.raises(TypeError):
            type("Counterfeit", (owner_type,), {})
    counterfeit_provider = object.__new__(CredentialProvider)
    counterfeit_issuer = object.__new__(Task27EnrollmentCapabilityIssuer)
    _safe_failure(lambda: counterfeit_provider.fingerprint_hint)
    _safe_failure(lambda: _issue(counterfeit_issuer))


def test_provider_rejects_global_and_tampered_settings_before_secret_use() -> None:
    global_settings = _settings(
        DELTA_REGION="global",
        DELTA_GLOBAL_TESTNET_API_KEY="global-key-canary",
        DELTA_GLOBAL_TESTNET_API_SECRET="global-secret-canary",
    )
    _safe_failure(lambda: build_credential_provider(global_settings))

    original = _settings()
    tampered = original.model_copy(
        update={"delta_region": DeltaRegion.GLOBAL},
    )
    _safe_failure(lambda: build_credential_provider(tampered))


def test_provider_rejects_structural_settings_without_attribute_lookup() -> None:
    touched: list[str] = []

    class SettingsAdapter:
        def __getattribute__(self, name: str) -> object:
            touched.append(name)
            raise AssertionError("structural adapter was inspected")

    _safe_failure(lambda: build_credential_provider(SettingsAdapter()))  # type: ignore[arg-type]
    assert touched == []


def test_internal_composition_rejects_adapters_before_method_lookup() -> None:
    touched: list[str] = []

    class IssuerAdapter:
        def __getattribute__(self, name: str) -> object:
            touched.append(name)
            raise AssertionError("issuer adapter was inspected")

    values = dict(
        snapshot_id=_SNAPSHOT_ID,
        credential_version=7,
        account_id=_ACCOUNT_ID,
        external_user_id="12345",
        origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        mode=ExecutionMode.PAPER,
        credential_epoch=11,
        user_agent="hmt-crypto/provider-test",
    )
    _safe_failure(lambda: provider_module._ISSUE_ENROLLMENT(IssuerAdapter(), **values))
    _safe_failure(lambda: provider_module._ISSUE_ENROLLMENT(_issue, **values))
    assert touched == []


def test_secret_file_provider_loads_exact_selected_slot(tmp_path: Path) -> None:
    path = _secret_file(tmp_path / "delta.json")
    provider = build_credential_provider(
        _settings(
            DELTA_INDIA_TESTNET_API_KEY=None,
            DELTA_INDIA_TESTNET_API_SECRET=None,
            DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
        )
    )

    assert provider.source_kind is CredentialSourceKind.SECRET_FILE
    signer, currentness = _issue(provider.enrollment_issuer)
    assert _revalidate(signer, currentness) is signer
    assert signer.fingerprint_hint == provider.fingerprint_hint
    assert isinstance(
        _settings(
            DELTA_INDIA_TESTNET_API_KEY=None,
            DELTA_INDIA_TESTNET_API_SECRET=None,
            DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
        ).selected_credential_file,
        SecretStr,
    )


def test_secret_file_path_and_material_never_render_in_settings_or_errors(tmp_path: Path) -> None:
    path = tmp_path / "credential-path-canary.json"
    settings = _settings(
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
    )
    rendered = repr(settings) + str(settings.model_dump()) + settings.model_dump_json()

    assert str(path) not in rendered
    _safe_failure(lambda: build_credential_provider(settings), str(path))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"api_key": _KEY},
        {"api_key": _KEY, "api_secret": _SECRET, "extra": "forbidden"},
        {"api_key": "", "api_secret": _SECRET},
        {"api_key": _KEY, "api_secret": "space forbidden"},
        {"api_key": 7, "api_secret": _SECRET},
        {"api_key": "k" * 257, "api_secret": _SECRET},
        {"api_key": _KEY, "api_secret": "s" * 513},
    ],
)
def test_secret_file_rejects_non_exact_documents(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "delta.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    settings = _settings(
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
    )
    _safe_failure(lambda: build_credential_provider(settings))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"api_key":"first","api_key":"second","api_secret":"secret"}',
        b'["api-key","api-secret"]',
        b'{"api_key":"unicode-\xc3\xa9","api_secret":"secret"}',
        b'{"api_key":"key","api_secret":"line\\nfeed"}',
    ],
)
def test_secret_file_rejects_duplicate_nonobject_and_nonascii_bytes(
    tmp_path: Path, raw: bytes
) -> None:
    path = tmp_path / "delta.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    settings = _settings(
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
    )
    _safe_failure(lambda: build_credential_provider(settings))


def test_secret_file_rejects_symlink_permissions_links_and_oversize(tmp_path: Path) -> None:
    target = _secret_file(tmp_path / "target.json")
    symlink = tmp_path / "linked.json"
    symlink.symlink_to(target)
    insecure = _secret_file(tmp_path / "insecure.json")
    insecure.chmod(0o640)
    hardlink = tmp_path / "hardlink.json"
    os.link(target, hardlink)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * 4097)
    oversized.chmod(0o600)

    for path in (symlink, insecure, target, hardlink, oversized):
        settings = _settings(
            DELTA_INDIA_TESTNET_API_KEY=None,
            DELTA_INDIA_TESTNET_API_SECRET=None,
            DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
        )
        _safe_failure(lambda settings=settings: build_credential_provider(settings))


def test_secret_file_rejects_owner_executable_mode(tmp_path: Path) -> None:
    path = _secret_file(tmp_path / "executable.json")
    path.chmod(0o700)
    settings = _settings(
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
    )

    _safe_failure(lambda: build_credential_provider(settings))


def test_secret_file_rejects_directory_relative_and_parent_symlink_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    path = _secret_file(real_directory / "delta.json")
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)
    monkeypatch.chdir(real_directory)

    for candidate in (real_directory, Path("delta.json"), linked_directory / "delta.json"):
        settings = _settings(
            DELTA_INDIA_TESTNET_API_KEY=None,
            DELTA_INDIA_TESTNET_API_SECRET=None,
            DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(candidate),
        )
        _safe_failure(lambda settings=settings: build_credential_provider(settings))
    assert path.is_file()


def test_secret_file_replacement_during_read_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _secret_file(tmp_path / "delta.json")
    replacement = _secret_file(
        tmp_path / "replacement.json",
        key="race-replacement-key",
        secret="race-replacement-secret",
    )
    settings = _settings(
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
    )
    real_read = os.read

    def swapping_read(descriptor: int, count: int) -> bytes:
        payload = real_read(descriptor, count)
        os.replace(replacement, path)
        return payload

    file_module = import_module("hmt_crypto.exchange._credential_file")
    monkeypatch.setattr(file_module.os, "read", swapping_read)
    _safe_failure(
        lambda: build_credential_provider(settings),
        "race-replacement-key",
        "race-replacement-secret",
    )


def test_secret_file_reload_invalidates_old_pair_and_uses_new_material(tmp_path: Path) -> None:
    path = _secret_file(tmp_path / "delta.json")
    provider = build_credential_provider(
        _settings(
            DELTA_INDIA_TESTNET_API_KEY=None,
            DELTA_INDIA_TESTNET_API_SECRET=None,
            DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
        )
    )
    signer, currentness = _issue(provider.enrollment_issuer)
    first_hint = provider.fingerprint_hint
    _secret_file(path, key="replacement-api-key", secret="replacement-api-secret")

    provider.reload()

    _safe_failure(lambda: _revalidate(signer, currentness))
    assert provider.fingerprint_hint != first_hint
    replacement, replacement_currentness = _issue(provider.enrollment_issuer)
    assert replacement.fingerprint_hint == provider.fingerprint_hint
    assert _revalidate(replacement, replacement_currentness) is replacement


def test_failed_secret_file_reload_revokes_old_pair_and_can_recover(tmp_path: Path) -> None:
    path = _secret_file(tmp_path / "delta.json")
    provider = build_credential_provider(
        _settings(
            DELTA_INDIA_TESTNET_API_KEY=None,
            DELTA_INDIA_TESTNET_API_SECRET=None,
            DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path),
        )
    )
    signer, currentness = _issue(provider.enrollment_issuer)
    path.chmod(0o640)

    _safe_failure(provider.reload)
    _safe_failure(lambda: _revalidate(signer, currentness))
    _safe_failure(lambda: provider.fingerprint_hint)
    path.chmod(0o600)
    provider.reload()
    replacement, replacement_currentness = _issue(provider.enrollment_issuer)
    assert _revalidate(replacement, replacement_currentness) is replacement


def test_inline_and_file_sources_are_mutually_exclusive(tmp_path: Path) -> None:
    path = _secret_file(tmp_path / "delta.json")
    with pytest.raises(ValidationError) as captured:
        _settings(DELTA_INDIA_TESTNET_CREDENTIAL_FILE=str(path))
    rendered = f"{captured.value!s} {captured.value!r}"
    assert _KEY not in rendered and _SECRET not in rendered


def test_provider_never_borrows_file_or_inline_material_from_another_slot(
    tmp_path: Path,
) -> None:
    path = _secret_file(tmp_path / "other-slot.json")
    settings = _settings(
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_INDIA_PROD_CREDENTIAL_FILE=str(path),
    )
    _safe_failure(lambda: build_credential_provider(settings))


def test_global_file_slot_fails_before_opening_or_parsing_it(tmp_path: Path) -> None:
    absent = tmp_path / "global-must-not-open.json"
    settings = _settings(
        DELTA_REGION="global",
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_GLOBAL_TESTNET_CREDENTIAL_FILE=str(absent),
    )
    _safe_failure(lambda: build_credential_provider(settings))
    assert not absent.exists()


def test_india_prod_live_file_issues_prod_bound_identity_only(tmp_path: Path) -> None:
    path = _secret_file(tmp_path / "india-prod.json")
    settings = _settings(
        EXECUTION_MODE="LIVE",
        DELTA_ENV="prod",
        DELTA_LIVE_TRADING_ENABLED=True,
        DELTA_LIVE_CONFIRMATION="I_UNDERSTAND_THIS_CAN_PLACE_REAL_ORDERS",
        DELTA_INDIA_TESTNET_API_KEY=None,
        DELTA_INDIA_TESTNET_API_SECRET=None,
        DELTA_INDIA_PROD_CREDENTIAL_FILE=str(path),
    )
    provider = build_credential_provider(settings)

    signer, currentness = provider.enrollment_issuer.issue_enrollment_credential(
        snapshot_id=_SNAPSHOT_ID,
        credential_version=7,
        account_id=_ACCOUNT_ID,
        external_user_id="12345",
        origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.PROD),
        mode=ExecutionMode.LIVE,
        credential_epoch=11,
        user_agent="hmt-crypto/provider-test",
    )

    assert provider.credential_slot is credentials.IndiaCredentialSlot.INDIA_PROD
    assert (
        currentness.revalidate_enrollment_credential_nonblocking(
            signer=signer,
            expected_account_id=_ACCOUNT_ID,
            expected_external_user_id="12345",
            expected_origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.PROD),
            expected_slot=credentials.IndiaCredentialSlot.INDIA_PROD,
            expected_credential_version=7,
        )
        is signer
    )
    _safe_failure(lambda: _issue(provider.enrollment_issuer))


def test_exact_issuer_rejects_foreign_identity_without_minting() -> None:
    provider = build_credential_provider(_settings())
    issuer = provider.enrollment_issuer
    bad = dict(
        snapshot_id=_SNAPSHOT_ID,
        credential_version=7,
        account_id=_ACCOUNT_ID,
        external_user_id="12345",
        origin=_canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET),
        mode=ExecutionMode.PAPER,
        credential_epoch=11,
        user_agent="hmt-crypto/provider-test",
    )
    for key, value in (
        ("snapshot_id", UUID(int=0)),
        ("credential_version", True),
        ("account_id", UUID(int=0)),
        ("external_user_id", "0123"),
        (
            "origin",
            _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.PROD),
        ),
        ("mode", ExecutionMode.BACKTEST),
        ("credential_epoch", 0),
        ("user_agent", "bad\nagent"),
        ("user_agent", object()),
    ):
        row = bad | {key: value}
        _safe_failure(lambda row=row: issuer.issue_enrollment_credential(**row))
