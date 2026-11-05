"""Package availability, lazy Delta exports, and supported interpreter contract."""

from __future__ import annotations

import importlib
import inspect
import json
import subprocess
import sys
import textwrap
from importlib.metadata import requires
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.version import Version
from pydantic import BaseModel

pytestmark = pytest.mark.unit

_DELTA_EXPORTS = (
    (
        "ContractConversion",
        "hmt_crypto.exchange.delta.sizing",
        "ContractConversion",
    ),
    ("ContractRounding", "hmt_crypto.exchange.delta.sizing", "ContractRounding"),
    (
        "ContractSizerRegistry",
        "hmt_crypto.exchange.delta.sizing",
        "ContractSizerRegistry",
    ),
    ("CursorPage", "hmt_crypto.exchange.delta.paginator", "CursorPage"),
    ("DeltaPublicClient", "hmt_crypto.exchange.delta.client", "DeltaPublicClient"),
    ("DeltaEndpointSet", "hmt_crypto.exchange.delta.endpoints", "DeltaEndpointSet"),
    ("DeltaSigner", "hmt_crypto.exchange.delta.signer", "DeltaSigner"),
    ("MISSING_AFTER", "hmt_crypto.exchange.delta.paginator", "MISSING_AFTER"),
    ("L2TimestampPolicy", "hmt_crypto.exchange.delta.client", "L2TimestampPolicy"),
    (
        "LinearContractSizer",
        "hmt_crypto.exchange.delta.sizing",
        "LinearContractSizer",
    ),
    ("PaginationResult", "hmt_crypto.exchange.delta.paginator", "PaginationResult"),
    ("PriceBasis", "hmt_crypto.exchange.delta.sizing", "PriceBasis"),
    ("PriceQuantization", "hmt_crypto.exchange.delta.sizing", "PriceQuantization"),
    ("SignedAuthHeaders", "hmt_crypto.exchange.delta.signer", "SignedAuthHeaders"),
    (
        "StreamingCapability",
        "hmt_crypto.exchange.delta.endpoints",
        "StreamingCapability",
    ),
    ("SourceBoundTicker", "hmt_crypto.exchange.delta.sizing", "SourceBoundTicker"),
    ("SizingUnit", "hmt_crypto.exchange.delta.sizing", "SizingUnit"),
    (
        "collect_cursor_pages",
        "hmt_crypto.exchange.delta.paginator",
        "collect_cursor_pages",
    ),
    (
        "get_delta_endpoints",
        "hmt_crypto.exchange.delta.endpoints",
        "get_delta_endpoints",
    ),
)
_DELTA_ALL = tuple(name for name, _module, _attribute in _DELTA_EXPORTS)
_RUNTIME_LEAF_PREFIXES = (
    "httpx",
    "hmt_crypto.exchange.delta.client",
    "hmt_crypto.exchange.delta.endpoints",
    "hmt_crypto.exchange.delta.paginator",
    "hmt_crypto.exchange.delta.signer",
    "hmt_crypto.exchange.delta.sizing",
    "hmt_crypto.exchange.delta.sizing_linear",
    "hmt_crypto.exchange.delta.sizing_math",
    "hmt_crypto.exchange.delta.sizing_models",
    "hmt_crypto.exchange.delta.sizing_validation",
    "hmt_crypto.exchange.delta.limiter_accounting",
    "hmt_crypto.exchange.delta.rate_limit",
    "hmt_crypto.exchange.delta.rate_limit_policy",
    "hmt_crypto.exchange.delta.rate_limiter",
    "hmt_crypto.exchange.delta.retry_policy",
    "hmt_crypto.exchange.delta.weighted_limiter",
    "hmt_crypto.exchange.delta.limiter",
    "hmt_crypto.exchange.delta.transport",
    "hmt_crypto.exchange.delta.wire",
    "hmt_crypto.exchange.delta.wire_json",
    "hmt_crypto.exchange.delta.wire_schemas",
)


def test_private_boundary_distribution_requires_its_supported_pydantic_api() -> None:
    """Catch resolver ranges that admit an API the private boundary cannot call."""
    requirements = [Requirement(raw) for raw in requires("hmt-crypto") or ()]
    pydantic_requirement = next(
        requirement for requirement in requirements if requirement.name == "pydantic"
    )
    assert Version("2.14.2") in pydantic_requirement.specifier
    assert Version("2.14.1") not in pydantic_requirement.specifier
    assert Version("2.15") not in pydantic_requirement.specifier

    required_parameters = {
        "model_validate": {"extra", "by_alias", "by_name"},
        "model_validate_json": {"extra", "by_alias", "by_name"},
        "model_validate_strings": {"extra", "by_alias", "by_name"},
        "model_dump": {"exclude_computed_fields", "polymorphic_serialization"},
        "model_dump_json": {
            "ensure_ascii",
            "exclude_computed_fields",
            "polymorphic_serialization",
        },
    }
    for method_name, expected in required_parameters.items():
        actual = set(inspect.signature(getattr(BaseModel, method_name)).parameters)
        assert expected <= actual


def test_delta_runtime_leaf_blocklist_covers_actual_sibling_modules() -> None:
    expected = {
        "hmt_crypto.exchange.delta.limiter_accounting",
        "hmt_crypto.exchange.delta.rate_limit_policy",
        "hmt_crypto.exchange.delta.rate_limiter",
        "hmt_crypto.exchange.delta.retry_policy",
        "hmt_crypto.exchange.delta.sizing_linear",
        "hmt_crypto.exchange.delta.sizing_math",
        "hmt_crypto.exchange.delta.sizing_models",
        "hmt_crypto.exchange.delta.sizing_validation",
        "hmt_crypto.exchange.delta.wire_json",
        "hmt_crypto.exchange.delta.wire_schemas",
    }
    assert expected <= set(_RUNTIME_LEAF_PREFIXES)


def _isolated(script: str) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    parsed: object = json.loads(result.stdout)
    assert type(parsed) is dict
    return parsed


def _isolation_script(import_target: str) -> str:
    return f"""
        import asyncio
        import builtins
        import importlib
        import json
        import pathlib
        import socket
        import sys

        prefixes = {_RUNTIME_LEAF_PREFIXES!r}

        def blocked(fullname):
            return any(
                fullname == prefix or fullname.startswith(prefix + ".")
                for prefix in prefixes
            )

        class BlockedRuntimeFinder:
            def find_spec(self, fullname, path=None, target=None):
                if blocked(fullname):
                    raise AssertionError("blocked runtime import")
                return None

        def side_effect(*args, **kwargs):
            raise AssertionError("import side effect")

        original_open = builtins.open
        def guarded_open(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                raise AssertionError("import file write")
            return original_open(file, mode, *args, **kwargs)

        socket.socket = side_effect
        socket.create_connection = side_effect
        asyncio.create_task = side_effect
        asyncio.ensure_future = side_effect
        pathlib.Path.write_text = side_effect
        pathlib.Path.write_bytes = side_effect
        builtins.open = guarded_open
        sys.meta_path.insert(0, BlockedRuntimeFinder())

        imported = importlib.import_module({import_target!r})
        package = importlib.import_module("hmt_crypto.exchange.delta")
        loaded = sorted(name for name in sys.modules if blocked(name))
        print(json.dumps({{
            "all": package.__all__,
            "loaded": loaded,
            "target_module": imported.__name__,
        }}))
    """


def test_package_imports_on_supported_python() -> None:
    assert sys.version_info >= (3, 11)
    assert importlib.import_module("hmt_crypto") is not None


def test_delta_package_public_surface_order_is_unchanged() -> None:
    package = importlib.import_module("hmt_crypto.exchange.delta")
    assert package.__all__ == list(_DELTA_ALL)


@pytest.mark.parametrize(
    ("import_target", "expected_target"),
    [
        ("hmt_crypto.exchange.delta", "hmt_crypto.exchange.delta"),
        (
            "hmt_crypto.exchange.delta.private_routes",
            "hmt_crypto.exchange.delta.private_routes",
        ),
    ],
)
def test_delta_package_and_private_route_imports_are_runtime_isolated(
    import_target: str, expected_target: str
) -> None:
    evidence = _isolated(_isolation_script(import_target))
    assert evidence == {
        "all": list(_DELTA_ALL),
        "loaded": [],
        "target_module": expected_target,
    }


def test_delta_config_first_import_adds_only_its_existing_endpoint_baseline() -> None:
    evidence = _isolated(
        f"""
        import importlib
        import json
        import sys

        runtime_prefixes = {_RUNTIME_LEAF_PREFIXES!r}
        config = importlib.import_module("hmt_crypto.config")
        before = set(sys.modules)
        package = importlib.import_module("hmt_crypto.exchange.delta")
        route = importlib.import_module("hmt_crypto.exchange.delta.private_routes")
        after = set(sys.modules)

        forbidden_after_config = (
            "hmt_crypto.exchange.delta.client",
            "hmt_crypto.exchange.delta.paginator",
            "hmt_crypto.exchange.delta.signer",
            "hmt_crypto.exchange.delta.sizing",
            "hmt_crypto.exchange.delta.rate_limiter",
            "hmt_crypto.exchange.delta.transport",
            "hmt_crypto.exchange.delta.wire_json",
            "hmt_crypto.exchange.delta.wire_schemas",
        )
        unexpected = sorted(
            name for name in after
            if any(name == prefix or name.startswith(prefix + ".")
                   for prefix in forbidden_after_config)
        )
        delta = sorted(
            name for name in after - before
            if any(name == prefix or name.startswith(prefix + ".")
                   for prefix in runtime_prefixes)
        )
        print(json.dumps({{
            "endpoint_loaded": "hmt_crypto.exchange.delta.endpoints" in before,
            "httpx_loaded": "httpx" in before,
            "unexpected": unexpected,
            "delta": delta,
            "package": package.__name__,
            "route_identity": route.PrivateRouteId.__module__,
            "config": config.__name__,
        }}))
        """
    )
    assert evidence == {
        "endpoint_loaded": True,
        "httpx_loaded": True,
        "unexpected": [],
        "delta": [],
        "package": "hmt_crypto.exchange.delta",
        "route_identity": "hmt_crypto.exchange.delta.private_routes",
        "config": "hmt_crypto.config",
    }


def test_every_delta_export_is_lazy_identity_preserving_and_cached() -> None:
    evidence = _isolated(
        f"""
        import importlib
        import json

        exports = {_DELTA_EXPORTS!r}
        package = importlib.import_module("hmt_crypto.exchange.delta")
        initially_cached = [name for name, _, _ in exports if name in vars(package)]
        failures = []
        for name, module_name, attribute_name in exports:
            value = getattr(package, name)
            expected = getattr(importlib.import_module(module_name), attribute_name)
            if value is not expected:
                failures.append(name + ":identity")
            if getattr(package, name) is not value:
                failures.append(name + ":cache")
            if vars(package).get(name) is not value:
                failures.append(name + ":globals")
        print(json.dumps({{
            "initially_cached": initially_cached,
            "failures": failures,
            "all": package.__all__,
        }}))
        """
    )
    assert evidence == {
        "initially_cached": [],
        "failures": [],
        "all": list(_DELTA_ALL),
    }


def test_delta_lazy_lookup_rejects_hostile_and_unknown_names_without_importing() -> None:
    evidence = _isolated(
        f"""
        import importlib
        import json
        import sys

        prefixes = {_RUNTIME_LEAF_PREFIXES!r}
        package = importlib.import_module("hmt_crypto.exchange.delta")

        class HostileName(str):
            def __eq__(self, other):
                raise AssertionError("hostile equality")
            def __hash__(self):
                raise AssertionError("hostile hash")
            def __repr__(self):
                raise AssertionError("hostile repr")
            def __str__(self):
                raise AssertionError("hostile str")

        errors = []
        for name in (HostileName("DeltaPublicClient"), "not_an_export"):
            try:
                package.__getattr__(name)
            except AttributeError as error:
                errors.append(error.args)
            else:
                raise AssertionError("lazy lookup unexpectedly succeeded")
        loaded = sorted(
            name for name in sys.modules
            if any(name == prefix or name.startswith(prefix + ".")
                   for prefix in prefixes)
        )
        print(json.dumps({{"errors": errors, "loaded": loaded}}))
        """
    )
    assert evidence == {
        "errors": [
            ["unknown Delta package attribute"],
            ["unknown Delta package attribute"],
        ],
        "loaded": [],
    }


def test_delta_normal_getattr_errors_do_not_retain_caller_names_or_package() -> None:
    evidence = _isolated(
        """
        import importlib
        import json

        package = importlib.import_module("hmt_crypto.exchange.delta")
        canary = "fixture-secret-package-attribute"

        class HostileName(str):
            def __repr__(self):
                raise AssertionError("fixture-secret-name-repr")
            def __str__(self):
                raise AssertionError("fixture-secret-name-str")

        observed = []
        for name in (canary, HostileName(canary)):
            try:
                getattr(package, name)
            except AttributeError as error:
                safe_name = error.name if type(error.name) is str else None
                rendered = str(error)
                represented = repr(error)
                observed.append({
                    "args": error.args,
                    "name": safe_name,
                    "obj_is_none": error.obj is None,
                    "rendered_safe": canary not in rendered,
                    "represented_safe": canary not in represented,
                })
            else:
                raise AssertionError("normal getattr unexpectedly succeeded")
        print(json.dumps({"observed": observed}))
        """
    )
    expected = {
        "args": ["unknown Delta package attribute"],
        "name": "unknown Delta package attribute",
        "obj_is_none": True,
        "rendered_safe": True,
        "represented_safe": True,
    }
    assert evidence == {"observed": [expected, expected]}


def test_delta_lazy_dir_is_fresh_exact_and_does_not_resolve_exports() -> None:
    evidence = _isolated(
        f"""
        import importlib
        import json

        expected = {_DELTA_ALL!r}
        package = importlib.import_module("hmt_crypto.exchange.delta")
        first = package.__dir__()
        second = package.__dir__()
        print(json.dumps({{
            "first": first,
            "second": second,
            "distinct": first is not second,
            "exact": all(type(name) is str for name in first + second),
            "cached": [name for name in expected if name in vars(package)],
            "builtin_dir": dir(package),
        }}))
        """
    )
    assert evidence == {
        "first": list(_DELTA_ALL),
        "second": list(_DELTA_ALL),
        "distinct": True,
        "exact": True,
        "cached": [],
        "builtin_dir": sorted(_DELTA_ALL),
    }


def test_delta_explicit_signer_and_wire_json_submodule_imports_still_work() -> None:
    evidence = _isolated(
        """
        import json
        from hmt_crypto.exchange.delta import signer, wire_json

        print(json.dumps({
            "signer": signer.__name__,
            "wire_json": wire_json.__name__,
        }))
        """
    )
    assert evidence == {
        "signer": "hmt_crypto.exchange.delta.signer",
        "wire_json": "hmt_crypto.exchange.delta.wire_json",
    }
