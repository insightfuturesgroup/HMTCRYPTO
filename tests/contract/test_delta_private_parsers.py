"""Fixture manifest and authority-boundary tests for offline private parsers."""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

from delta_private_parser_test_support import (
    CANCEL_ALL_FIXTURE,
    CREATE_BRACKET_FIXTURE,
    EDIT_BRACKET_FIXTURE,
    LEVERAGE_FIXTURE,
    MARGIN_MODE_FIXTURE,
    ORDER_FIXTURE,
    POSITION_FIXTURE,
)


def test_offline_candidate_fixture_manifest_is_bounded_clean_and_referenced() -> None:
    root = Path(__file__).parents[1] / "fixtures" / "delta" / "private" / "mutations"
    referenced = {
        ORDER_FIXTURE,
        LEVERAGE_FIXTURE,
        MARGIN_MODE_FIXTURE,
        POSITION_FIXTURE,
        CANCEL_ALL_FIXTURE,
        CREATE_BRACKET_FIXTURE,
        EDIT_BRACKET_FIXTURE,
    }
    files = tuple(sorted(root.rglob("*.json")))
    assert files
    assert {path.name for path in files} == referenced
    for path in files:
        assert path.name.startswith("offline_candidate_")
        assert not any(
            word in path.name for word in ("verified", "recorded", "testnet", "production")
        )
        body = path.read_bytes()
        assert 0 < len(body) <= 1_048_576
        text = body.decode("utf-8")
        assert not any(
            token in text.casefold()
            for token in ("api-key", "signature", "authorization", "secret")
        )
        value = json.loads(text)
        assert type(value) is dict


def test_a6_production_leaves_have_no_io_or_runtime_authority_dependencies() -> None:
    root = Path(__file__).parents[2] / "src" / "hmt_crypto" / "exchange" / "delta"
    names = (
        "private_mutation_profile.py",
        "private_write_wire.py",
        "private_payload_parsers.py",
        "private_mutation_parsers.py",
    )
    forbidden_import_fragments = (
        "httpx",
        "socket",
        "asyncio",
        "threading",
        "logging",
        "persistence",
        "supabase",
        "signing",
        "transport",
        "rate_limiter",
        "retry",
        "client",
        "audit",
        "catalog",
        "config",
        "risk",
        "oms",
    )
    forbidden_calls = {
        "send",
        "sign",
        "execute",
        "request",
        "create_task",
        "getenv",
        "open",
        "sleep",
    }
    for name in names:
        tree = ast.parse((root / name).read_text())
        assert not any(
            isinstance(node, (ast.AsyncFunctionDef, ast.Await)) for node in ast.walk(tree)
        )
        imports = tuple(
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        ) + tuple(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            fragment in imported for imported in imports for fragment in forbidden_import_fragments
        )
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        } | {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert calls.isdisjoint(forbidden_calls)


def test_a6_internal_surfaces_are_not_exported_from_package_roots() -> None:
    delta = importlib.import_module("hmt_crypto.exchange.delta")
    exchange = importlib.import_module("hmt_crypto.exchange")
    package = importlib.import_module("hmt_crypto")
    forbidden = {
        "parse_place_order_response",
        "parse_modify_order_response",
        "parse_cancel_order_response",
        "parse_cancel_all_response",
        "parse_set_leverage_response",
        "parse_set_margin_mode_response",
        "parse_change_position_margin_response",
        "parse_create_bracket_response",
        "parse_edit_bracket_response",
        "_encode_offline_mutation_candidate",
        "_create_offline_mutation_codec_profile",
        "_require_canonical_mutation_route",
        "_MUTATION_ROUTE_PROFILES",
    }
    for module in (delta, exchange, package):
        assert forbidden.isdisjoint(vars(module))


def test_fresh_process_import_encode_parse_and_reject_perform_no_runtime_io() -> None:
    script = textwrap.dedent(
        r"""
        import asyncio
        import logging
        import os
        import socket
        import sqlite3
        import subprocess
        import sys
        import threading
        import time

        def forbidden(*_args, **_kwargs):
            raise AssertionError("runtime I/O boundary reached")

        socket.socket = forbidden
        socket.create_connection = forbidden
        sqlite3.connect = forbidden
        subprocess.Popen = forbidden
        threading.Thread.start = forbidden
        asyncio.create_task = forbidden
        logging.Logger._log = forbidden
        time.time = forbidden
        time.monotonic = forbidden
        original_getenv = os.getenv

        def guarded_getenv(name, default=None):
            if name == "PYDANTIC_DISABLE_PLUGINS":
                return original_getenv(name, default)
            return forbidden(name, default)

        os.getenv = guarded_getenv
        importing = [True]

        def audit(event, args):
            if event.startswith("socket.") or event in {
                "subprocess.Popen", "os.system", "os.spawn", "sqlite3.connect"
            }:
                forbidden(event, args)
            if event == "open" and not importing[0]:
                forbidden(event, args)

        sys.addaudithook(audit)

        from decimal import Decimal
        from hmt_crypto.domain.private_orders import OrderSide, OrderType
        from hmt_crypto.errors import AmbiguousExchangeResult
        from hmt_crypto.exchange.delta.private_mutation_parsers import (
            parse_set_leverage_response,
        )
        from hmt_crypto.exchange.delta.private_write_wire import (
            _UnverifiedBooleanEncoding,
            _UnverifiedLeverageEncoding,
            _attest_unverified_mutation_candidate,
            _create_offline_mutation_codec_profile,
            _encode_offline_mutation_candidate,
        )
        from hmt_crypto.exchange.private_requests import PlaceOrderRequest, SetLeverageRequest

        importing[0] = False
        profile = _create_offline_mutation_codec_profile(
            boolean_encoding=_UnverifiedBooleanEncoding.JSON_BOOLEAN_CANDIDATE,
            leverage_encoding=_UnverifiedLeverageEncoding.DECIMAL_STRING_CANDIDATE,
        )
        candidate = _encode_offline_mutation_candidate(
            PlaceOrderRequest(
                product_id=27,
                size=2,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT_ORDER,
                limit_price=Decimal("123.45"),
            ),
            profile=profile,
        )
        assert _attest_unverified_mutation_candidate(candidate) is candidate
        leverage_request = SetLeverageRequest(product_id=27, leverage=Decimal("5"))
        parsed = parse_set_leverage_response(
            b'{"success":true,"result":{"leverage":"5","order_margin":"0","product_id":27}}',
            status_code=200,
            request=leverage_request,
        )
        assert parsed.product_id == 27
        try:
            parse_set_leverage_response(b'{', status_code=200, request=leverage_request)
        except AmbiguousExchangeResult:
            pass
        else:
            raise AssertionError("malformed mutation response was not ambiguous")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
