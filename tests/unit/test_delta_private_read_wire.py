"""Frozen, unsigned Delta private-read bytes and V1 evidence attestations."""

from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import pickle
import subprocess
import sys
import textwrap
from pathlib import Path

import httpx
import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.exchange.delta import private_read_wire as wire
from hmt_crypto.exchange.delta.private_query import _canonical_private_read_target
from hmt_crypto.exchange.delta.private_routes import (
    PrivateDispatchCapability,
    PrivatePaginationPolicy,
    PrivateProfileState,
    PrivateRegionSupport,
    PrivateRouteId,
    PrivateRouteSpec,
    get_private_route_spec,
)
from hmt_crypto.exchange.delta.rate_limit_policy import POLICY_VERSION
from hmt_crypto.exchange.private_requests import (
    FillQuery,
    LatestPositionQuery,
    LeverageQuery,
    MarginedPositionQuery,
    OpenOrderQuery,
    OpenOrderStateFilter,
    OrderByClientIdQuery,
    OrderByIdQuery,
    OrderHistoryQuery,
    WalletTransactionQuery,
)

pytestmark = pytest.mark.unit

_PREFIX = b"HMT_PRIVATE_READ_REQUEST_V1\x00"
_PROFILE_FIELDS = PrivateRouteSpec.__match_args__


def _independent_digest(*components: bytes, width: int = 8, endian: str = "big") -> str:
    framed = bytearray(_PREFIX)
    for component in components:
        framed.extend(len(component).to_bytes(width, endian))
        framed.extend(component)
    return hashlib.sha256(framed).hexdigest()


def _assert_safe_failure(
    call: object,
    *,
    operation: str,
    canary: str = "fixture-secret-wire",
) -> None:
    with pytest.raises(ConfigurationError) as captured:
        call()  # type: ignore[operator]
    error = captured.value
    assert error.context == {"operation": operation}
    assert error.args == ("configuration_error: Configuration is invalid",)
    rendered = f"{error!s} {error!r} {error.context!r} {getattr(error, '__notes__', ())!r}"
    assert canary not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def _freeze(
    query: object,
    *,
    region: object = DeltaRegion.INDIA,
    environment: object = DeltaEnvironment.TESTNET,
) -> object:
    return wire._freeze_private_read(  # type: ignore[attr-defined, no-any-return]
        query,
        region=region,
        environment=environment,
    )


def _attest(value: object) -> object:
    return wire._attest_frozen_private_read(value)  # type: ignore[attr-defined, no-any-return]


def test_v1_evidence_has_four_literal_independent_vectors() -> None:
    """Catch ambiguous concatenation, wrong width/endian, order, or prefix."""
    assert _independent_digest(b"", b"", b"", b"", b"") == (
        "9f1a24292ec51279849b462afd73ccff7bbb3355c107abd8947a867814ddfa50"
    )
    assert (
        _independent_digest(b"METHOD08", b"ORIGIN08", b"PATH0008", b"QUERY008", b"BODY0008")
        == "8a4030e69f0caa79c5a2456d78c875c1ef89cb37585cd870dc9154a5eedbbbb9"
    )

    assert hasattr(wire, "_freeze_balances_private_read")
    balances = wire._freeze_balances_private_read(  # type: ignore[attr-defined]
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )
    assert balances.request_evidence_sha256 == (
        "f48e6190199440c8bea24048eecd3d2b483e0629ae236a6f64c16b7c957778a5"
    )

    orders = _freeze(
        OpenOrderQuery(
            product_ids=(27, 99),
            states=(OpenOrderStateFilter.OPEN, OpenOrderStateFilter.PENDING),
            after="A&B=C",
        ),
        environment=DeltaEnvironment.PROD,
    )
    assert orders.request_evidence_sha256 == (
        "03e53a40b07c1f1ab9e115c7ab7caab05e0603326c43f379052bbef8d5911ac7"
    )


def test_v1_evidence_changes_for_every_component_and_framing_mutation() -> None:
    """Catch dropped empty fields, leading-query loss, or alternate framing."""
    components = (
        b"GET",
        b"https://api.india.delta.exchange",
        b"/v2/orders",
        b"?product_ids=27,99&states=open,pending&after=A%26B%3DC&page_size=10",
        b"",
    )
    baseline = _independent_digest(*components)
    assert baseline == "03e53a40b07c1f1ab9e115c7ab7caab05e0603326c43f379052bbef8d5911ac7"

    variants: list[tuple[bytes, ...]] = []
    for index, component in enumerate(components):
        changed = list(components)
        changed[index] = component + b"X"
        variants.append(tuple(changed))
    variants.extend(
        (
            (components[1], components[0], *components[2:]),
            (*components[:3], components[3][1:], components[4]),
            (*components[:4], b"{}"),
            (*components, b""),
        )
    )
    assert all(_independent_digest(*variant) != baseline for variant in variants)
    assert _independent_digest(*components, width=4) != baseline
    assert _independent_digest(*components, endian="little") != baseline


def test_balances_freeze_supports_exactly_two_india_origins() -> None:
    """Catch origin widening, trailing-slash drift, or environment confusion."""
    cases = (
        (
            DeltaEnvironment.PROD,
            "https://api.india.delta.exchange",
            "5d5fe6d47367cca23cf748726321891dee7bd33430290b71c7728dfa2c61d27e",
        ),
        (
            DeltaEnvironment.TESTNET,
            "https://cdn-ind.testnet.deltaex.org",
            "f48e6190199440c8bea24048eecd3d2b483e0629ae236a6f64c16b7c957778a5",
        ),
    )
    for environment, origin, digest in cases:
        value = wire._freeze_balances_private_read(  # type: ignore[attr-defined]
            region=DeltaRegion.INDIA,
            environment=environment,
        )
        assert type(value) is wire._FrozenPrivateReadRequestV1  # type: ignore[attr-defined]
        assert value.region is DeltaRegion.INDIA
        assert value.environment is environment
        assert value.method == "GET"
        assert value.origin == origin
        assert value.path == "/v2/wallet/balances"
        assert value.query_string == b""
        assert value.payload == b""
        assert value.request_evidence_sha256 == digest
        assert _attest(value) is value


def test_freeze_rejects_global_raw_forged_and_non_api_origin_inputs() -> None:
    """Catch scalar lookalikes or development URLs entering frozen evidence."""
    forged_region = str.__new__(DeltaRegion, "india")
    object.__setattr__(forged_region, "_name_", "INDIA")
    object.__setattr__(forged_region, "_value_", "india")
    forged_environment = str.__new__(DeltaEnvironment, "testnet")
    object.__setattr__(forged_environment, "_name_", "TESTNET")
    object.__setattr__(forged_environment, "_value_", "testnet")
    invalid: tuple[tuple[object, object], ...] = (
        (DeltaRegion.GLOBAL, DeltaEnvironment.PROD),
        (DeltaRegion.GLOBAL, DeltaEnvironment.TESTNET),
        ("india", DeltaEnvironment.PROD),
        (DeltaRegion.INDIA, "testnet"),
        (forged_region, DeltaEnvironment.PROD),
        (DeltaRegion.INDIA, forged_environment),
        ("https://localhost:9443", DeltaEnvironment.TESTNET),
        ("https://api.india.delta.exchange/", DeltaEnvironment.PROD),
        (" https://api.india.delta.exchange", DeltaEnvironment.PROD),
    )
    for region, environment in invalid:
        _assert_safe_failure(
            lambda region=region, environment=environment: _freeze(
                OpenOrderQuery(after="fixture-secret-wire"),
                region=region,
                environment=environment,
            ),
            operation="freeze_private_read",
        )

    balances_signature = inspect.signature(wire._freeze_balances_private_read)  # type: ignore[attr-defined]
    read_signature = inspect.signature(wire._freeze_private_read)  # type: ignore[attr-defined]
    assert tuple(balances_signature.parameters) == ("region", "environment")
    assert tuple(read_signature.parameters) == ("query", "region", "environment")
    assert "origin" not in read_signature.parameters
    assert "url" not in read_signature.parameters


@pytest.mark.parametrize(
    ("slot", "replacement"),
    (
        ("_region", DeltaRegion.GLOBAL),
        ("_environment", DeltaEnvironment.PROD),
        ("_value", "https://fixture-secret-wire.invalid"),
    ),
)
def test_origin_singleton_slot_corruption_fails_every_owning_boundary(
    slot: str,
    replacement: object,
) -> None:
    """Never treat an arbitrary HTTPS value as a canonical India origin."""
    from hmt_crypto.exchange.delta import private_origin

    request = wire._freeze_balances_private_read(  # type: ignore[attr-defined]
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )
    binding = private_origin._canonical_private_origin(
        DeltaRegion.INDIA,
        DeltaEnvironment.TESTNET,
    )
    original = object.__getattribute__(binding, slot)
    object.__setattr__(binding, slot, replacement)
    try:
        _assert_broken_origin_boundaries(request)
    finally:
        object.__setattr__(binding, slot, original)
    assert _attest(request) is request


@pytest.mark.parametrize("slot", ("_region", "_environment", "_value"))
def test_deleted_origin_singleton_slots_fail_safely_and_restore(slot: str) -> None:
    """Collapse missing canonical-origin state to stable boundary errors."""
    from hmt_crypto.exchange.delta import private_origin

    request = wire._freeze_balances_private_read(  # type: ignore[attr-defined]
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )
    binding = private_origin._canonical_private_origin(
        DeltaRegion.INDIA,
        DeltaEnvironment.TESTNET,
    )
    original = object.__getattribute__(binding, slot)
    object.__delattr__(binding, slot)
    try:
        _assert_broken_origin_boundaries(request)
    finally:
        object.__setattr__(binding, slot, original)
    assert _attest(request) is request


def _assert_broken_origin_boundaries(request: object) -> None:
    from hmt_crypto.exchange.delta import private_origin

    _assert_safe_failure(
        lambda: private_origin._canonical_private_origin(
            DeltaRegion.INDIA, DeltaEnvironment.TESTNET
        ),
        operation="canonical_private_origin",
    )
    _assert_safe_failure(
        lambda: wire._freeze_balances_private_read(  # type: ignore[attr-defined]
            region=DeltaRegion.INDIA, environment=DeltaEnvironment.TESTNET
        ),
        operation="freeze_private_read",
    )
    _assert_safe_failure(lambda: _attest(request), operation="attest_private_read")


def test_every_read_target_freezes_exact_owned_wire_values() -> None:
    """Catch route loss, mutable bytes, or a second encoder in the wire layer."""
    queries = (
        WalletTransactionQuery(asset_id=9),
        MarginedPositionQuery(product_ids=(8, 7)),
        LatestPositionQuery(product_id=6),
        OpenOrderQuery(product_ids=(27, 99), after="A&B=C"),
        OrderHistoryQuery(before="history:cursor"),
        FillQuery(after="fill%cursor"),
        OrderByIdQuery(order_id=5),
        OrderByClientIdQuery(client_order_id="client:five"),
        LeverageQuery(product_id=4),
    )
    expected_routes = tuple(PrivateRouteId)[1:10]
    for query, route_id in zip(queries, expected_routes, strict=True):
        value = _freeze(query)
        target = _canonical_private_read_target(query)  # type: ignore[arg-type]
        assert value.route_id is route_id
        assert value.region is DeltaRegion.INDIA
        assert value.environment is DeltaEnvironment.TESTNET
        assert type(value.method) is str and value.method == "GET"
        assert value.origin == "https://cdn-ind.testnet.deltaex.org"
        assert type(value.path) is str and value.path == target.path
        assert type(value.query_string) is bytes and value.query_string == target.query_string
        assert type(value.payload) is bytes and value.payload == b""
        assert type(value.request_evidence_sha256) is str
        assert value.request_evidence_sha256 == _independent_digest(
            b"GET",
            b"https://cdn-ind.testnet.deltaex.org",
            target.path.encode("ascii"),
            target.query_string,
            b"",
        )
        assert _attest(value) is value


def test_frozen_request_is_sealed_redacted_and_not_exported() -> None:
    """Catch direct construction, copying, serialization, or selector leakage."""
    import hmt_crypto.exchange.delta as delta

    value = _freeze(OpenOrderQuery(after="fixture-secret-wire"))
    assert not hasattr(value, "__dict__")
    assert "_FrozenPrivateReadRequestV1" not in delta.__all__
    assert "_freeze_private_read" not in delta.__all__
    assert "_FrozenPrivateReadRequestV1" not in vars(delta)
    assert "fixture-secret-wire" not in repr(value)
    assert "fixture-secret-wire" not in str(value)
    assert "/v2/" not in repr(value)
    assert value == value
    assert value != _freeze(OpenOrderQuery(after="fixture-secret-wire"))

    with pytest.raises(TypeError):
        wire._FrozenPrivateReadRequestV1()  # type: ignore[attr-defined, call-arg]
    with pytest.raises(TypeError):

        class Derived(wire._FrozenPrivateReadRequestV1):  # type: ignore[attr-defined, misc]
            pass

    for operation in (
        lambda: copy.copy(value),
        lambda: copy.deepcopy(value),
        lambda: pickle.dumps(value),
        lambda: value.__reduce__(),
        lambda: value.__reduce_ex__(4),
        lambda: value.__getstate__(),
    ):
        with pytest.raises(TypeError):
            operation()
    with pytest.raises((AttributeError, TypeError)):
        value.path = "/fixture-secret-wire"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("slot", "replacement"),
    (
        ("_route_id", "open_orders"),
        ("_region", DeltaRegion.GLOBAL),
        ("_environment", "testnet"),
        ("_method", "POST"),
        ("_origin", "https://fixture-secret-wire.invalid"),
        ("_path", "/v2/fills"),
        ("_query_string", b"?after=fixture-secret-wire-X&page_size=10"),
        ("_payload", b"{}"),
        ("_request_evidence_sha256", "0" * 64),
        ("_policy_version", "fixture-secret-wire-policy"),
        ("_origin_binding", object()),
        ("_target", object()),
        ("_semantic_query", object()),
    ),
)
def test_attestation_rejects_every_corrupted_private_slot(slot: str, replacement: object) -> None:
    """Catch trust in exposed bytes, digest, retained source, or policy binding."""
    value = _freeze(OpenOrderQuery(after="fixture-secret-wire"))
    object.__setattr__(value, slot, replacement)
    _assert_safe_failure(
        lambda: _attest(value),
        operation="attest_private_read",
    )


def test_self_consistent_route_path_digest_substitution_still_rejects() -> None:
    """Catch digest-only attestation without semantic/A4 route re-rendering."""
    value = _freeze(OpenOrderQuery(after="fixture-secret-wire"))
    replacement_path = "/v2/fills"
    replacement_query = b"?page_size=10"
    object.__setattr__(value, "_route_id", PrivateRouteId.FILLS)
    object.__setattr__(value, "_path", replacement_path)
    object.__setattr__(value, "_query_string", replacement_query)
    object.__setattr__(
        value,
        "_request_evidence_sha256",
        _independent_digest(
            b"GET",
            b"https://cdn-ind.testnet.deltaex.org",
            replacement_path.encode("ascii"),
            replacement_query,
            b"",
        ),
    )
    _assert_safe_failure(
        lambda: _attest(value),
        operation="attest_private_read",
    )


def test_a4_region_or_dispatch_drift_rejects_before_freeze() -> None:
    """Catch route-profile widening hidden behind correct target bytes."""
    spec = get_private_route_spec(PrivateRouteId.OPEN_ORDERS)
    for field, replacement in (
        ("region_support", "global"),
        ("dispatch_capability", PrivateDispatchCapability.NONE),
    ):
        original = object.__getattribute__(spec, field)
        object.__setattr__(spec, field, replacement)
        try:
            _assert_safe_failure(
                lambda: _freeze(OpenOrderQuery(after="fixture-secret-wire")),
                operation="freeze_private_read",
            )
        finally:
            object.__setattr__(spec, field, original)
    assert get_private_route_spec(PrivateRouteId.OPEN_ORDERS).region_support is (
        PrivateRegionSupport.INDIA_ONLY
    )


def _freeze_profile_case(route_id: PrivateRouteId) -> object:
    if route_id is PrivateRouteId.BALANCES:
        return wire._freeze_balances_private_read(  # type: ignore[attr-defined, no-any-return]
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
        )
    if route_id is PrivateRouteId.OPEN_ORDERS:
        return _freeze(OpenOrderQuery(after="fixture-secret-wire"))
    if route_id is PrivateRouteId.FILLS:
        return _freeze(FillQuery(after="fixture-secret-wire"))
    raise AssertionError("unreviewed test route")


@pytest.mark.parametrize(
    "route_id",
    (PrivateRouteId.BALANCES, PrivateRouteId.OPEN_ORDERS, PrivateRouteId.FILLS),
)
def test_every_deleted_a4_slot_fails_freeze_and_existing_request_attestation(
    route_id: PrivateRouteId,
) -> None:
    """Cover nonpaged, paged, and held profiles across all 24 slots."""
    spec = get_private_route_spec(route_id)
    for field in _PROFILE_FIELDS:
        request = _freeze_profile_case(route_id)
        original = object.__getattribute__(spec, field)
        object.__delattr__(spec, field)
        try:
            _assert_safe_failure(
                lambda route_id=route_id: _freeze_profile_case(route_id),
                operation="freeze_private_read",
            )
            _assert_safe_failure(
                lambda request=request: _attest(request),
                operation="attest_private_read",
            )
        finally:
            object.__setattr__(spec, field, original)
        assert _attest(request) is request


@pytest.mark.parametrize(
    ("route_id", "field", "replacement"),
    (
        (PrivateRouteId.BALANCES, "pagination", PrivatePaginationPolicy.FORWARD_CURSOR),
        (PrivateRouteId.BALANCES, "default_page_size", 10),
        (PrivateRouteId.OPEN_ORDERS, "pagination", PrivatePaginationPolicy.NONE),
        (PrivateRouteId.OPEN_ORDERS, "max_pages", None),
        (PrivateRouteId.FILLS, "dispatch_capability", PrivateDispatchCapability.NONE),
        (
            PrivateRouteId.FILLS,
            "profile_state",
            PrivateProfileState.DISABLED_PENDING_TESTNET,
        ),
    ),
)
def test_profile_corruption_fails_wire_for_nonpaged_paged_and_held_routes(
    route_id: PrivateRouteId,
    field: str,
    replacement: object,
) -> None:
    """Keep all three read-profile shapes bound during freeze and attest."""
    request = _freeze_profile_case(route_id)
    spec = get_private_route_spec(route_id)
    original = object.__getattribute__(spec, field)
    object.__setattr__(spec, field, replacement)
    try:
        _assert_safe_failure(
            lambda: _freeze_profile_case(route_id),
            operation="freeze_private_read",
        )
        _assert_safe_failure(
            lambda: _attest(request),
            operation="attest_private_read",
        )
    finally:
        object.__setattr__(spec, field, original)
    assert _attest(request) is request


def test_attested_values_form_one_exact_httpx_request_without_normalization() -> None:
    """Catch transport-side query sorting, comma escaping, or percent decoding."""
    value = _attest(
        _freeze(
            OpenOrderQuery(
                product_ids=(27, 99),
                states=(OpenOrderStateFilter.OPEN, OpenOrderStateFilter.PENDING),
                after="A&B=C",
            ),
            environment=DeltaEnvironment.PROD,
        )
    )
    request = httpx.Request(
        value.method,
        value.origin + value.path + value.query_string.decode("ascii"),
        content=value.payload,
    )
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "api.india.delta.exchange"
    assert request.url.raw_path == value.path.encode("ascii") + value.query_string
    assert request.content == b""
    assert request.url.raw_path.endswith(
        b"?product_ids=27,99&states=open,pending&after=A%26B%3DC&page_size=10"
    )
    assert request.headers.get("authorization") is None


def test_wire_module_has_no_signer_http_client_io_or_runtime_authority() -> None:
    """Catch a frozen evidence value becoming a request/sign/send component."""
    tree = ast.parse(Path(wire.__file__ or "").read_text())
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    denied = {
        "asyncio",
        "hmac",
        "httpx",
        "logging",
        "os",
        "pathlib",
        "socket",
        "sqlalchemy",
        "threading",
        "hmt_crypto.exchange.credentials",
        "hmt_crypto.exchange.delta.client",
        "hmt_crypto.exchange.delta.signer",
        "hmt_crypto.exchange.delta.transport",
        "hmt_crypto.exchange.delta.weighted_limiter",
        "hmt_crypto.persistence",
        "hmt_crypto.worker",
    }
    assert imports.isdisjoint(denied)
    assert not any(isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree))
    assert not hasattr(wire, "request")
    assert not hasattr(wire, "send")
    assert not hasattr(wire, "sign")
    assert POLICY_VERSION == "delta-docs-2026-11-04-private-v1"


def test_all_a5_modules_have_no_runtime_or_dispatch_authority_surface() -> None:
    """Keep the three A5 owners pure, synchronous, and package-private."""
    import hmt_crypto.exchange.delta as delta
    from hmt_crypto.exchange.delta import private_origin, private_query

    denied_roots = (
        "asyncio",
        "hmac",
        "httpx",
        "logging",
        "os",
        "pathlib",
        "socket",
        "sqlalchemy",
        "threading",
        "hmt_crypto.exchange.credentials",
        "hmt_crypto.exchange.delta.client",
        "hmt_crypto.exchange.delta.private_admission",
        "hmt_crypto.exchange.delta.private_transport",
        "hmt_crypto.exchange.delta.private_write_wire",
        "hmt_crypto.exchange.delta.signer",
        "hmt_crypto.exchange.delta.transport",
        "hmt_crypto.exchange.delta.weighted_limiter",
        "hmt_crypto.persistence",
        "hmt_crypto.worker",
        "supabase",
    )
    forbidden_functions = {"request", "send", "sign", "retry", "audit"}
    private_names = {
        "CanonicalPrivateOrigin",
        "_canonical_private_origin",
        "_CanonicalPrivateReadTarget",
        "_canonical_balances_target",
        "_canonical_private_read_target",
        "_FrozenPrivateReadRequestV1",
        "_freeze_balances_private_read",
        "_freeze_private_read",
        "_attest_frozen_private_read",
    }

    for module in (private_origin, private_query, wire):
        tree = ast.parse(Path(module.__file__ or "").read_text())
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not any(
            imported == root or imported.startswith(root + ".")
            for imported in imports
            for root in denied_roots
        )
        functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert functions.isdisjoint(forbidden_functions)
        assert not any(
            isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree)
        )
        assert not hasattr(module, "__all__")

    assert private_names.isdisjoint(delta.__all__)
    assert private_names.isdisjoint(vars(delta))


def test_a5_import_freeze_attest_and_rejection_acquire_no_runtime_authority() -> None:
    """Exercise A5 in isolation while every external authority class is trapped."""
    root = Path(__file__).resolve().parents[2]
    source = r"""
        import asyncio
        import builtins
        import importlib
        import importlib.abc
        import logging
        import os
        import pathlib
        import socket
        import sys
        import threading
        import time

        import httpx
        import sqlalchemy
        from sqlalchemy.ext import asyncio as sqlalchemy_asyncio

        suppliers = (
            "hmt_crypto.config",
            "hmt_crypto.errors",
            "hmt_crypto.exchange.private_requests",
            "hmt_crypto.exchange.delta.private_routes",
            "hmt_crypto.exchange.delta.rate_limit_policy",
        )
        for supplier in suppliers:
            importlib.import_module(supplier)

        targets = (
            "hmt_crypto.exchange.delta.private_origin",
            "hmt_crypto.exchange.delta.private_query",
            "hmt_crypto.exchange.delta.private_read_wire",
        )
        assert not any(name in sys.modules for name in targets)

        events = []
        def denied(label):
            def reject(*args, **kwargs):
                del args, kwargs
                events.append(label)
                raise AssertionError(label)
            return reject

        real_environment = os.environ
        class GuardedEnvironment:
            def get(self, key, default=None):
                if key == "PYDANTIC_DISABLE_PLUGINS":
                    return real_environment.get(key, default)
                return denied("os.environ.get")(key, default)
            def __getitem__(self, key):
                if key == "PYDANTIC_DISABLE_PLUGINS":
                    return real_environment[key]
                return denied("os.environ.index")(key)

        os.environ = GuardedEnvironment()
        os.getenv = denied("os.getenv")
        builtins.open = denied("builtins.open")
        pathlib.Path.open = denied("Path.open")
        pathlib.Path.read_bytes = denied("Path.read_bytes")
        pathlib.Path.read_text = denied("Path.read_text")
        pathlib.Path.write_bytes = denied("Path.write_bytes")
        pathlib.Path.write_text = denied("Path.write_text")
        socket.socket = denied("socket.socket")
        socket.create_connection = denied("socket.create_connection")
        socket.getaddrinfo = denied("socket.getaddrinfo")
        asyncio.create_task = denied("asyncio.create_task")
        asyncio.BaseEventLoop.create_task = denied("loop.create_task")
        threading.Thread.start = denied("thread.start")
        logging.basicConfig = denied("logging.basicConfig")
        logging.Logger.addHandler = denied("logging.addHandler")
        time.time = denied("time.time")
        time.monotonic = denied("time.monotonic")
        time.sleep = denied("time.sleep")
        httpx.Client = denied("httpx.Client")
        httpx.AsyncClient = denied("httpx.AsyncClient")
        sqlalchemy.create_engine = denied("sqlalchemy.create_engine")
        sqlalchemy_asyncio.create_async_engine = denied("sqlalchemy.create_async_engine")

        config = sys.modules["hmt_crypto.config"]
        config.Settings.__init__ = denied("credential.Settings")
        blocked_imports = (
            "supabase",
            "hmt_crypto.exchange.credentials",
            "hmt_crypto.exchange.delta.client",
            "hmt_crypto.exchange.delta.private_admission",
            "hmt_crypto.exchange.delta.private_mutation_parsers",
            "hmt_crypto.exchange.delta.private_transport",
            "hmt_crypto.exchange.delta.private_write_wire",
            "hmt_crypto.exchange.delta.signer",
            "hmt_crypto.exchange.delta.weighted_limiter",
            "hmt_crypto.persistence.audit_repository",
            "hmt_crypto.persistence.command_repository",
            "hmt_crypto.persistence.db",
        )
        class Blocker(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                del path, target
                blocked = any(
                    fullname == name or fullname.startswith(name + ".")
                    for name in blocked_imports
                )
                if blocked:
                    return denied("blocked import")(fullname)
                return None
        sys.meta_path.insert(0, Blocker())

        origin = importlib.import_module(targets[0])
        query = importlib.import_module(targets[1])
        wire = importlib.import_module(targets[2])
        requests = sys.modules["hmt_crypto.exchange.private_requests"]
        value = wire._freeze_private_read(
            requests.OpenOrderQuery(product_ids=(27,), after="A&B=C"),
            region=config.DeltaRegion.INDIA,
            environment=config.DeltaEnvironment.TESTNET,
        )
        assert wire._attest_frozen_private_read(value) is value
        assert value.path == "/v2/orders"
        assert value.query_string == b"?product_ids=27&after=A%26B%3DC&page_size=10"
        try:
            wire._freeze_balances_private_read(
                region=config.DeltaRegion.GLOBAL,
                environment=config.DeltaEnvironment.PROD,
            )
        except Exception as error:
            assert type(error).__name__ == "ConfigurationError"
            assert error.context == {"operation": "freeze_private_read"}
            assert error.__context__ is None
        else:
            raise AssertionError("Global private origin accepted")
        assert origin._canonical_private_origin is not None
        assert query._canonical_private_read_target is not None
        assert events == []
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
