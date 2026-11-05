"""Private package import, typing, and side-effect artifact boundaries."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_DOMAIN_RESULTS = (
    ("ObservedBalanceSnapshot", "hmt_crypto.domain.private_wallet", (2, 4, 0, 0)),
    (
        "StrictWalletTransactionPaginationResult",
        "hmt_crypto.domain.private_orders",
        (2, 4, 2, 43),
    ),
    (
        "DiagnosticWalletTransactionPaginationResult",
        "hmt_crypto.domain.private_orders",
        (2, 4, 2, 43),
    ),
    (
        "ObservedLatestPositionLookupResult",
        "hmt_crypto.domain.private_orders",
        (2, 4, 2, 43),
    ),
    ("ObservedMarginedPositions", "hmt_crypto.domain.private_positions", (2, 0, 2, 0)),
    ("StrictOrderPaginationResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    (
        "DiagnosticOrderPaginationResult",
        "hmt_crypto.domain.private_orders",
        (2, 4, 2, 43),
    ),
    ("StrictFillPaginationResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    (
        "DiagnosticFillPaginationResult",
        "hmt_crypto.domain.private_orders",
        (2, 4, 2, 43),
    ),
    ("ObservedOrderLookupResult", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
    ("ObservedLeverage", "hmt_crypto.domain.private_orders", (2, 4, 2, 43)),
)
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
