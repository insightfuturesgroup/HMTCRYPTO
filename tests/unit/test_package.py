"""Package availability, lazy Delta exports, and supported interpreter contract."""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import subprocess
import sys
import textwrap
from importlib.metadata import requires
from pathlib import Path
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

_DOMAIN_PRIVATE_ORDINARY = (
    ("AccountRef", "hmt_crypto.domain.account"),
    ("ObservationContext", "hmt_crypto.domain.account"),
    ("MissingField", "hmt_crypto.domain.account"),
    ("NullField", "hmt_crypto.domain.account"),
    ("CollectionMode", "hmt_crypto.domain.account"),
    ("TruncationReason", "hmt_crypto.domain.account"),
    ("PrivatePaginationProvenance", "hmt_crypto.domain.account"),
    ("WalletTransactionType", "hmt_crypto.domain.private_wallet"),
    ("Balance", "hmt_crypto.domain.private_wallet"),
    ("BalanceSnapshot", "hmt_crypto.domain.private_wallet"),
    ("WalletTransaction", "hmt_crypto.domain.private_wallet"),
    ("MarginMode", "hmt_crypto.domain.private_positions"),
    ("PositionProductIdentity", "hmt_crypto.domain.private_positions"),
    ("LatestPosition", "hmt_crypto.domain.private_positions"),
    ("MarginedPosition", "hmt_crypto.domain.private_positions"),
    *(
        (name, "hmt_crypto.domain.private_orders")
        for name in (
            "OrderSide",
            "OrderType",
            "OrderState",
            "StopOrderType",
            "MmpLevel",
            "StopTriggerMethod",
            "TimeInForce",
            "ReduceOnlyEncoding",
            "ObservedReduceOnly",
            "Order",
            "FillType",
            "FillRole",
            "FillContractType",
            "FillNotionalType",
            "FillAssetSummary",
            "FillSpotIndexSummary",
            "FillProductSummary",
            "NumericFillOrderRef",
            "FillOrderRef",
            "Fill",
            "LeverageEncoding",
            "Leverage",
        )
    ),
)
_DOMAIN_RESULTS = (
    ("ObservedBalanceSnapshot", "hmt_crypto.domain.private_wallet", (2, 4, 0, 0)),
    ("StrictWalletTransactionPaginationResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    (
        "DiagnosticWalletTransactionPaginationResult",
        "hmt_crypto.domain.private_orders",
        (2, 4, 2, 43),
    ),
    ("ObservedLatestPositionLookupResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    ("ObservedMarginedPositions", "hmt_crypto.domain.private_positions", (2, 0, 2, 0)),
    ("StrictOrderPaginationResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    ("DiagnosticOrderPaginationResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    ("StrictFillPaginationResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    ("DiagnosticFillPaginationResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    ("ObservedOrderLookupResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    ("ObservedLeverage", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
)
_DOMAIN_PHASE1_ALL = (
    "Asset",
    "Candle",
    "ContractType",
    "ExactDecimal",
    "NonNegativeDecimal",
    "NotionalType",
    "PositiveDecimal",
    "Product",
    "ProductState",
    "RequestReference",
    "Ticker",
    "TradingStatus",
    "UtcDateTime",
    "parse_decimal",
    "parse_utc_datetime",
)
_DOMAIN_ALL = (
    *_DOMAIN_PHASE1_ALL,
    *(name for name, _module in _DOMAIN_PRIVATE_ORDINARY),
    *(name for name, _module, _counts in _DOMAIN_RESULTS),
)
_EXCHANGE_PRIVATE = (
    ("PrivateReadClient", "hmt_crypto.exchange.private"),
    *(
        (name, "hmt_crypto.exchange.private_requests")
        for name in (
            "PrivateContractTypeFilter",
            "MarginedPositionContractTypeFilter",
            "OpenOrderStateFilter",
            "OrderTypeFilter",
            "WalletTransactionQuery",
            "MarginedPositionQuery",
            "LatestPositionQuery",
            "OpenOrderQuery",
            "OrderHistoryQuery",
            "FillQuery",
            "OrderByIdQuery",
            "OrderByClientIdQuery",
            "LeverageQuery",
        )
    ),
)
_EXCHANGE_PHASE1_ALL = (
    "CandleRequest",
    "CandleResolution",
    "CandleResult",
    "MarketDataClient",
    "OrderBookLevel",
    "OrderBookRequest",
    "OrderBookSnapshot",
    "ProductQuery",
    "ProductRef",
)
_EXCHANGE_ALL = (*_EXCHANGE_PHASE1_ALL, *(name for name, _module in _EXCHANGE_PRIVATE))
_GUARDED_ORIGINS = (
    "ObservedEnum",
    "ValueField",
    "FieldPresence",
    "NullableField",
    "Observed",
    "LookupFound",
    "LookupResult",
    "PrivateCursorPage",
    "StrictPrivatePaginationResult",
    "DiagnosticPrivatePaginationResult",
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


def test_private_read_package_exports_are_exact_lazy_cached_and_identity_preserving() -> None:
    evidence = _isolated(
        f"""
        import importlib
        import json
        import sys

        domain_exports = {_DOMAIN_PRIVATE_ORDINARY!r} + tuple(
            (name, module) for name, module, _counts in {_DOMAIN_RESULTS!r}
        )
        exchange_exports = {_EXCHANGE_PRIVATE!r}
        domain = importlib.import_module("hmt_crypto.domain")
        exchange = importlib.import_module("hmt_crypto.exchange")
        owner_names = tuple(dict.fromkeys(
            module for _name, module in domain_exports + exchange_exports
        ))
        initially_loaded = [name for name in owner_names if name in sys.modules]
        initially_cached = [
            name for name, _module in domain_exports if name in vars(domain)
        ] + [name for name, _module in exchange_exports if name in vars(exchange)]
        expected_domain_dir = sorted(set(vars(domain)) | set(domain.__all__))
        expected_exchange_dir = sorted(set(vars(exchange)) | set(exchange.__all__))
        domain_dir = domain.__dir__()
        exchange_dir = exchange.__dir__()
        failures = []
        for package, exports in ((domain, domain_exports), (exchange, exchange_exports)):
            for name, module_name in exports:
                value = getattr(package, name)
                target = getattr(sys.modules[module_name], name)
                if value is not target:
                    failures.append(name + ":identity")
                if getattr(package, name) is not value or vars(package)[name] is not value:
                    failures.append(name + ":cache")
        print(json.dumps({{
            "domain_all": domain.__all__,
            "exchange_all": exchange.__all__,
            "domain_dir_complete": domain_dir == expected_domain_dir,
            "exchange_dir_complete": exchange_dir == expected_exchange_dir,
            "dirs_fresh": domain_dir is not domain.__dir__()
                and exchange_dir is not exchange.__dir__(),
            "exact_strings": all(
                type(name) is str for name in domain_dir + exchange_dir
            ),
            "initially_loaded": initially_loaded,
            "initially_cached": initially_cached,
            "failures": failures,
        }}))
        """
    )
    assert evidence == {
        "domain_all": list(_DOMAIN_ALL),
        "exchange_all": list(_EXCHANGE_ALL),
        "domain_dir_complete": True,
        "exchange_dir_complete": True,
        "dirs_fresh": True,
        "exact_strings": True,
        "initially_loaded": [],
        "initially_cached": [],
        "failures": [],
    }


@pytest.mark.parametrize("guarded_name", _GUARDED_ORIGINS)
def test_guarded_origin_is_denied_by_domain_package(guarded_name: str) -> None:
    evidence = _isolated(
        f"""
        import importlib
        import json
        import sys

        owners = (
            "hmt_crypto.domain.account",
            "hmt_crypto.domain.private_wallet",
            "hmt_crypto.domain.private_positions",
            "hmt_crypto.domain.private_orders",
        )
        package = importlib.import_module("hmt_crypto.domain")
        try:
            getattr(package, {guarded_name!r})
        except AttributeError as error:
            outcome = {{
                "args": error.args,
                "name": error.name,
                "obj_is_none": error.obj is None,
            }}
        else:
            raise AssertionError("guarded origin exported")
        print(json.dumps({{
            "listed": {guarded_name!r} in package.__all__,
            "outcome": outcome,
            "owners": [name for name in owners if name in sys.modules],
        }}))
        """
    )
    assert evidence == {
        "listed": False,
        "outcome": {
            "args": ["unknown domain package attribute"],
            "name": "unknown domain package attribute",
            "obj_is_none": True,
        },
        "owners": [],
    }


def test_private_roots_deny_unknown_hostile_mutation_and_execution_names() -> None:
    evidence = _isolated(
        """
        import importlib
        import json
        import sys

        domain = importlib.import_module("hmt_crypto.domain")
        exchange = importlib.import_module("hmt_crypto.exchange")
        class HostileName(str):
            def __eq__(self, other):
                raise AssertionError("hostile equality")
            def __hash__(self):
                raise AssertionError("hostile hash")
            def __repr__(self):
                raise AssertionError("hostile repr")
            def __str__(self):
                raise AssertionError("hostile str")
        canary = "fixture-secret-private-root"
        denied = (
            (domain, canary), (domain, HostileName(canary)),
            (exchange, canary), (exchange, HostileName(canary)),
            (exchange, "PlaceOrderRequest"),
            (exchange, "PrivateExecutionClient"),
        )
        errors = []
        for package, name in denied:
            try:
                package.__getattr__(name)
            except AttributeError as error:
                rendered = str(error) + repr(error)
                errors.append({
                    "args": error.args,
                    "safe": canary not in rendered,
                    "obj_is_none": error.obj is None,
                })
            else:
                raise AssertionError("denied package lookup succeeded")
        owners = sorted(
            name for name in sys.modules
            if name.startswith("hmt_crypto.domain.private_")
            or name.startswith("hmt_crypto.exchange.private")
        )
        print(json.dumps({"errors": errors, "owners": owners}))
        """
    )
    assert evidence == {
        "errors": [
            {
                "args": [message],
                "safe": True,
                "obj_is_none": True,
            }
            for message in (
                "unknown domain package attribute",
                "unknown domain package attribute",
                "unknown exchange package attribute",
                "unknown exchange package attribute",
                "unknown exchange package attribute",
                "unknown exchange package attribute",
            )
        ],
        "owners": [],
    }


@pytest.mark.parametrize(("result_name", "target_module", "counts"), _DOMAIN_RESULTS)
def test_canonical_result_is_first_private_export_with_exact_owner_counts(
    result_name: str, target_module: str, counts: tuple[int, int, int, int]
) -> None:
    evidence = _isolated(
        f"""
        import importlib
        import json
        import sys

        owners = (
            ("hmt_crypto.domain.account", "_ACCOUNT_REVIEWED_GENERIC_SPECIALIZATIONS"),
            ("hmt_crypto.domain.private_wallet", "_WALLET_REVIEWED_GENERIC_SPECIALIZATIONS"),
            ("hmt_crypto.domain.private_positions", "_POSITIONS_REVIEWED_GENERIC_SPECIALIZATIONS"),
            ("hmt_crypto.domain.private_orders", "_ORDERS_REVIEWED_GENERIC_SPECIALIZATIONS"),
        )
        package = importlib.import_module("hmt_crypto.domain")
        before = [name for name, _table in owners if name in sys.modules]
        imported = []
        real_import = package.import_module
        def traced_import(name):
            imported.append(name)
            return real_import(name)
        package.import_module = traced_import
        manifest_calls = []
        def profile(frame, event, arg):
            if (
                event == "call"
                and frame.f_code.co_name
                == "_reviewed_generic_manifest_for_verification"
            ):
                manifest_calls.append(frame.f_code.co_name)
        sys.setprofile(profile)
        try:
            value = getattr(package, {result_name!r})
        finally:
            sys.setprofile(None)
        target = sys.modules[{target_module!r}]
        vector = [
            len(getattr(sys.modules[name], table)) if name in sys.modules else 0
            for name, table in owners
        ]
        print(json.dumps({{
            "before": before,
            "imports": imported,
            "identity": value is getattr(target, {result_name!r}),
            "cached": vars(package)[{result_name!r}] is value,
            "counts": vector,
            "manifest_calls": manifest_calls,
        }}))
        """
    )
    assert evidence == {
        "before": [],
        "imports": [target_module],
        "identity": True,
        "cached": True,
        "counts": list(counts),
        "manifest_calls": [],
    }


@pytest.mark.parametrize("order", ("config-first", "packages-first", "direct-first"))
def test_private_read_import_orders_preserve_config_baseline(order: str) -> None:
    evidence = _isolated(
        f"""
        import importlib
        import json
        import sys

        order = {order!r}
        if order == "config-first":
            importlib.import_module("hmt_crypto.config")
            domain = importlib.import_module("hmt_crypto.domain")
            exchange = importlib.import_module("hmt_crypto.exchange")
            domain.AccountRef
            exchange.WalletTransactionQuery
        elif order == "packages-first":
            domain = importlib.import_module("hmt_crypto.domain")
            exchange = importlib.import_module("hmt_crypto.exchange")
            domain.AccountRef
            exchange.WalletTransactionQuery
            importlib.import_module("hmt_crypto.config")
        else:
            for name in (
                "hmt_crypto.domain.account", "hmt_crypto.domain.private_wallet",
                "hmt_crypto.domain.private_positions", "hmt_crypto.domain.private_orders",
                "hmt_crypto.exchange.private_requests", "hmt_crypto.exchange.private",
            ):
                importlib.import_module(name)
            importlib.import_module("hmt_crypto.config")
        forbidden = (
            "hmt_crypto.exchange.delta.client",
            "hmt_crypto.exchange.delta.signer",
            "hmt_crypto.exchange.delta.transport",
            "hmt_crypto.exchange.delta.rate_limiter",
        )
        print(json.dumps({{
            "endpoints": "hmt_crypto.exchange.delta.endpoints" in sys.modules,
            "httpx": "httpx" in sys.modules,
            "forbidden": [name for name in forbidden if name in sys.modules],
        }}))
        """
    )
    assert evidence == {"endpoints": True, "httpx": True, "forbidden": []}


def _run_package_mypy(tmp_path: Path, name: str, source: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / name
    path.write_text(textwrap.dedent(source))
    environment = os.environ.copy()
    environment["MYPYPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    return subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--show-error-codes", str(path)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_private_result_package_exports_pass_strict_mypy(tmp_path: Path) -> None:
    result = _run_package_mypy(
        tmp_path,
        "positive_private_results.py",
        """
        from hmt_crypto.domain import (
            DiagnosticFillPaginationResult, DiagnosticOrderPaginationResult,
            DiagnosticWalletTransactionPaginationResult, ObservedBalanceSnapshot,
            ObservedLatestPositionLookupResult, ObservedLeverage,
            ObservedMarginedPositions, ObservedOrderLookupResult,
            StrictFillPaginationResult, StrictOrderPaginationResult,
            StrictWalletTransactionPaginationResult,
        )
        from hmt_crypto.domain.private_orders import (
            DiagnosticFillPaginationResult as E1,
            DiagnosticOrderPaginationResult as E2,
            DiagnosticWalletTransactionPaginationResult as E3,
            ObservedLatestPositionLookupResult as E4, ObservedLeverage as E5,
            ObservedOrderLookupResult as E6, StrictFillPaginationResult as E7,
            StrictOrderPaginationResult as E8,
            StrictWalletTransactionPaginationResult as E9,
        )
        from hmt_crypto.domain.private_positions import ObservedMarginedPositions as E10
        from hmt_crypto.domain.private_wallet import ObservedBalanceSnapshot as E11

        def prove(
            a: DiagnosticFillPaginationResult, b: DiagnosticOrderPaginationResult,
            c: DiagnosticWalletTransactionPaginationResult,
            d: ObservedLatestPositionLookupResult, e: ObservedLeverage,
            f: ObservedOrderLookupResult, g: StrictFillPaginationResult,
            h: StrictOrderPaginationResult, i: StrictWalletTransactionPaginationResult,
            j: ObservedMarginedPositions, k: ObservedBalanceSnapshot,
        ) -> tuple[E1, E2, E3, E4, E5, E6, E7, E8, E9, E10, E11]:
            return a, b, c, d, e, f, g, h, i, j, k
        """,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_guarded_origin_package_imports_fail_strict_mypy(tmp_path: Path) -> None:
    imports = "\n".join(f"from hmt_crypto.domain import {name}" for name in _GUARDED_ORIGINS)
    result = _run_package_mypy(tmp_path, "negative_guarded_origins.py", imports)
    assert result.returncode != 0
    assert result.stdout.count("[attr-defined]") == len(_GUARDED_ORIGINS)


def test_private_lazy_resolution_has_no_runtime_side_effect_authority() -> None:
    evidence = _isolated(
        """
        import asyncio
        import builtins
        import importlib
        import json
        import logging
        import pathlib
        import socket
        import sys
        import threading

        domain = importlib.import_module("hmt_crypto.domain")
        exchange = importlib.import_module("hmt_crypto.exchange")
        def blocked(*args, **kwargs):
            raise AssertionError("private lazy import side effect")
        original_open = builtins.open
        def guarded_open(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                raise AssertionError("private lazy import file write")
            return original_open(file, mode, *args, **kwargs)
        builtins.open = guarded_open
        pathlib.Path.write_text = blocked
        pathlib.Path.write_bytes = blocked
        socket.create_connection = blocked
        asyncio.create_task = blocked
        asyncio.ensure_future = blocked
        threading.Thread.start = blocked
        logging.Logger.addHandler = blocked
        domain.AccountRef
        exchange.WalletTransactionQuery
        forbidden = sorted(
            name for name in sys.modules
            if name.startswith("hmt_crypto.exchange.delta.")
            and name.rsplit(".", 1)[-1] in {
                "client", "signer", "transport", "rate_limiter", "limiter"
            }
        )
        print(json.dumps({"forbidden": forbidden}))
        """
    )
    assert evidence == {"forbidden": []}


def test_private_package_roots_have_only_type_checking_private_import_edges() -> None:
    root = Path(__file__).resolve().parents[2] / "src/hmt_crypto"
    for relative in ("domain/__init__.py", "exchange/__init__.py"):
        tree = ast.parse((root / relative).read_text())
        eager_private_imports = [
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and ".private" in node.module
        ]
        assert eager_private_imports == []
        type_checking = next(
            node
            for node in tree.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        )
        assert any(
            isinstance(node, ast.ImportFrom) and ".private" in (node.module or "")
            for node in type_checking.body
        )
    delta_source = (root / "exchange/delta/__init__.py").read_text()
    assert "private_requests" not in delta_source
    assert "exchange.private" not in delta_source
