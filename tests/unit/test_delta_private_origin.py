"""Exact, inert canonical-origin supplier for Delta India private reads."""

from __future__ import annotations

import ast
import copy
import pickle
import subprocess
import sys
import textwrap
from dataclasses import FrozenInstanceError
from enum import StrEnum
from pathlib import Path

import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta.private_origin import (
    CanonicalPrivateOrigin,
    _canonical_private_origin,
)

pytestmark = pytest.mark.unit


def _assert_safe_failure(call: object, *, canary: str = "fixture-secret-origin") -> None:
    with pytest.raises(ConfigurationError) as captured:
        call()  # type: ignore[operator]
    error = captured.value
    assert error.context == {"operation": "canonical_private_origin"}
    assert error.args == ("configuration_error: Configuration is invalid",)
    assert canary not in str(error)
    assert canary not in repr(error)
    assert canary not in repr(error.context)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_origin_manifest_is_literal_and_returns_module_singletons() -> None:
    """Catch host drift, origin copying, or per-call value minting."""
    expected = (
        (
            DeltaRegion.INDIA,
            DeltaEnvironment.PROD,
            "https://api.india.delta.exchange",
        ),
        (
            DeltaRegion.INDIA,
            DeltaEnvironment.TESTNET,
            "https://cdn-ind.testnet.deltaex.org",
        ),
    )

    observed: list[tuple[DeltaRegion, DeltaEnvironment, str]] = []
    for region, environment, literal in expected:
        first = _canonical_private_origin(region, environment)
        second = _canonical_private_origin(region, environment)
        assert first is second
        assert type(first) is CanonicalPrivateOrigin
        assert first.region is region
        assert first.environment is environment
        assert type(first.value) is str and first.value == literal
        observed.append((first.region, first.environment, first.value))

    assert tuple(observed) == expected
    assert _canonical_private_origin(
        DeltaRegion.INDIA, DeltaEnvironment.PROD
    ) is not _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET)


def test_origin_resolver_rejects_global_raw_foreign_and_forged_enums() -> None:
    """Catch value-equality admission widening beyond exact config identities."""

    class ForeignRegion(StrEnum):
        INDIA = "india"

    class ForeignEnvironment(StrEnum):
        TESTNET = "testnet"

    class RegionText(str):
        pass

    forged_region = str.__new__(DeltaRegion, "india")
    object.__setattr__(forged_region, "_name_", "INDIA")
    object.__setattr__(forged_region, "_value_", "india")
    forged_environment = str.__new__(DeltaEnvironment, "testnet")
    object.__setattr__(forged_environment, "_name_", "TESTNET")
    object.__setattr__(forged_environment, "_value_", "testnet")

    invalid_pairs: tuple[tuple[object, object], ...] = (
        (DeltaRegion.GLOBAL, DeltaEnvironment.PROD),
        (DeltaRegion.GLOBAL, DeltaEnvironment.TESTNET),
        ("india", DeltaEnvironment.PROD),
        (DeltaRegion.INDIA, "prod"),
        (RegionText("india"), DeltaEnvironment.PROD),
        (ForeignRegion.INDIA, DeltaEnvironment.PROD),
        (DeltaRegion.INDIA, ForeignEnvironment.TESTNET),
        (forged_region, DeltaEnvironment.PROD),
        (DeltaRegion.INDIA, forged_environment),
        (None, None),
    )
    for region, environment in invalid_pairs:
        _assert_safe_failure(
            lambda region=region, environment=environment: _canonical_private_origin(
                region,
                environment,  # type: ignore[arg-type]
            )
        )


def test_origin_resolver_does_not_invoke_hostile_callbacks() -> None:
    """Catch comparison, hashing, formatting, iteration, or property access first."""
    calls: list[str] = []

    class Hostile:
        @property
        def value(self) -> str:
            calls.append("value")
            raise AssertionError

        def __eq__(self, other: object) -> bool:
            del other
            calls.append("eq")
            raise AssertionError

        def __hash__(self) -> int:
            calls.append("hash")
            raise AssertionError

        def __iter__(self):
            calls.append("iter")
            raise AssertionError

        def __str__(self) -> str:
            calls.append("str")
            return "fixture-secret-origin"

        def __repr__(self) -> str:
            calls.append("repr")
            return "fixture-secret-origin"

    hostile = Hostile()
    _assert_safe_failure(
        lambda: _canonical_private_origin(hostile, DeltaEnvironment.PROD),  # type: ignore[arg-type]
    )
    _assert_safe_failure(
        lambda: _canonical_private_origin(DeltaRegion.INDIA, hostile),  # type: ignore[arg-type]
    )
    assert calls == []


def test_origin_resolution_uses_identity_not_mutable_enum_value() -> None:
    """Catch live enum `.value` reads after A1 source capture."""
    member = DeltaRegion.INDIA
    original = object.__getattribute__(member, "_value_")
    object.__setattr__(member, "_value_", "fixture-secret-origin")
    try:
        origin = _canonical_private_origin(member, DeltaEnvironment.PROD)
        assert origin.region is member
        assert origin.value == "https://api.india.delta.exchange"
    finally:
        object.__setattr__(member, "_value_", original)


def test_origin_values_are_sealed_identity_objects() -> None:
    """Catch reconstruction, mutation, copying, subclassing, or serialization."""
    origin = _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET)
    assert not hasattr(origin, "__dict__")
    assert origin != _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.PROD)
    assert "cdn-ind" not in repr(origin)
    assert "cdn-ind" not in str(origin)

    with pytest.raises(TypeError):
        CanonicalPrivateOrigin()
    with pytest.raises(TypeError):

        class Derived(CanonicalPrivateOrigin):
            pass

    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        origin.value = "https://fixture-secret-origin.invalid"  # type: ignore[misc]
    for operation in (
        lambda: copy.copy(origin),
        lambda: copy.deepcopy(origin),
        lambda: pickle.dumps(origin),
        lambda: origin.__reduce__(),
        lambda: origin.__reduce_ex__(4),
        lambda: origin.__getstate__(),
    ):
        with pytest.raises(TypeError):
            operation()


def test_origin_module_is_not_a_package_export_or_authority_surface() -> None:
    """Catch accidental credentials, client, transport, or package-root exposure."""
    import hmt_crypto.exchange.delta as delta
    import hmt_crypto.exchange.delta.private_origin as module

    assert "CanonicalPrivateOrigin" not in delta.__all__
    assert "_canonical_private_origin" not in delta.__all__
    assert "CanonicalPrivateOrigin" not in vars(delta)
    assert "_canonical_private_origin" not in vars(delta)
    assert not hasattr(module, "__all__")

    tree = ast.parse(Path(module.__file__ or "").read_text())
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden_roots = {
        "asyncio",
        "httpx",
        "logging",
        "os",
        "pathlib",
        "socket",
        "sqlalchemy",
        "threading",
        "hmt_crypto.exchange.delta.client",
        "hmt_crypto.exchange.delta.signer",
        "hmt_crypto.exchange.delta.transport",
        "hmt_crypto.exchange.delta.weighted_limiter",
        "hmt_crypto.persistence",
        "hmt_crypto.worker",
    }
    assert imports.isdisjoint(forbidden_roots)
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))


def test_origin_import_adds_no_private_runtime_or_side_effect() -> None:
    """Catch transitive signer/client/transport/worker imports and import-time work."""
    root = Path(__file__).resolve().parents[2]
    source = """
        import importlib
        import sys

        importlib.import_module("hmt_crypto.config")
        before = set(sys.modules)
        module = importlib.import_module("hmt_crypto.exchange.delta.private_origin")
        added = set(sys.modules) - before
        denied = {
            "hmt_crypto.exchange.credentials",
            "hmt_crypto.exchange.delta.client",
            "hmt_crypto.exchange.delta.private_admission",
            "hmt_crypto.exchange.delta.private_transport",
            "hmt_crypto.exchange.delta.signer",
            "hmt_crypto.exchange.delta.weighted_limiter",
            "hmt_crypto.persistence.api_audit_service_contracts",
            "hmt_crypto.worker.clock",
            "hmt_crypto.worker.leadership",
        }
        assert added.isdisjoint(denied), sorted(added & denied)
        assert not hasattr(module, "send")
        assert not hasattr(module, "request")
        assert not hasattr(module, "sign")
        print("ok")
    """
    result = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(source)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
