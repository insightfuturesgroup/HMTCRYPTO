"""Executable line ceilings and manifest for every current Phase 3 path."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = {
    "src/hmt_crypto/exchange/base.py",
    "src/hmt_crypto/exchange/delta/endpoints.py",
    "src/hmt_crypto/exchange/delta/websocket_codec.py",
    "src/hmt_crypto/exchange/delta/websocket_contracts.py",
    "src/hmt_crypto/exchange/delta/websocket_errors.py",
    "src/hmt_crypto/exchange/delta/websocket_ownership.py",
    "src/hmt_crypto/exchange/delta/websocket_ports.py",
    "src/hmt_crypto/exchange/delta/websocket_public_parser_support.py",
    "src/hmt_crypto/exchange/delta/websocket_public_parsers.py",
    "src/hmt_crypto/exchange/delta/websocket_subscriptions.py",
    "src/hmt_crypto/exchange/delta/websocket_transport.py",
    "src/hmt_crypto/market_data/contracts.py",
    "src/hmt_crypto/market_data/connection_attempts.py",
    "src/hmt_crypto/market_data/orderbook.py",
    "src/hmt_crypto/market_data/primitives.py",
    "src/hmt_crypto/market_data/public_stream.py",
    "src/hmt_crypto/market_data/public_stream_control.py",
    "src/hmt_crypto/market_data/public_stream_models.py",
    "src/hmt_crypto/market_data/public_stream_operations.py",
    "src/hmt_crypto/market_data/public_stream_registry_poll.py",
    "src/hmt_crypto/market_data/public_stream_runner.py",
    "src/hmt_crypto/market_data/public_stream_supervisor.py",
    "src/hmt_crypto/market_data/public_stream_support.py",
    "src/hmt_crypto/market_data/readiness.py",
    "src/hmt_crypto/market_data/reconnect_policy.py",
    "src/hmt_crypto/market_data/state.py",
    "src/hmt_crypto/market_data/state_models.py",
    "src/hmt_crypto/market_data/stream_control_owner.py",
    "src/hmt_crypto/market_data/stream_control_composition.py",
    "src/hmt_crypto/market_data/stream_control_evidence.py",
    "src/hmt_crypto/market_data/stream_control_io.py",
    "src/hmt_crypto/market_data/stream_evidence_mailbox.py",
    "src/hmt_crypto/market_data/stream_liveness.py",
    "src/hmt_crypto/market_data/stream_liveness_runner.py",
}
TEST_SUPPORT = {
    "tests/architecture/test_phase3_artifact_exclusion.py",
    "tests/architecture/test_phase3_module_limits.py",
    "tests/contract/test_delta_public_ws_parsers.py",
    "tests/fixtures/delta/websocket/control/heartbeat.json",
    "tests/fixtures/delta/websocket/control/pong.json",
    "tests/fixtures/delta/websocket/control/subscriptions.json",
    "tests/fixtures/delta/websocket/public/F10_ob_updates_snapshot.json",
    "tests/fixtures/delta/websocket/public/F12_mark_candle.json",
    "tests/fixtures/delta/websocket/public/F13_system_status.json",
    "tests/integration/support/fake_websocket.py",
    "tests/integration/public_stream_failure_support.py",
    "tests/integration/test_public_stream_actor.py",
    "tests/integration/test_public_stream_registry_deadline.py",
    "tests/integration/test_public_stream_supervisor.py",
    "tests/integration/test_public_stream_transport_failures.py",
    "tests/unit/test_delta_orderbook_engine.py",
    "tests/unit/test_delta_orderbook_properties.py",
    "tests/unit/test_market_connection_attempt_governor.py",
    "tests/unit/test_market_data_contracts.py",
    "tests/unit/test_market_readiness.py",
    "tests/unit/test_market_reconnect_policy.py",
    "tests/unit/test_market_state.py",
    "tests/unit/test_market_stream_liveness.py",
    "tests/unit/test_market_stream_liveness_runner.py",
    "tests/unit/test_market_stream_control_owner.py",
    "tests/unit/test_websocket_codec.py",
    "tests/unit/test_websocket_contracts.py",
    "tests/unit/test_websocket_endpoint_policy.py",
    "tests/unit/test_websocket_subscription_registry.py",
    "tests/unit/test_websocket_subscriptions.py",
    "tests/unit/test_websocket_transport.py",
}


def test_phase3_manifest_covers_every_current_path() -> None:
    discovered = {
        path.relative_to(ROOT).as_posix()
        for base in (ROOT / "src", ROOT / "tests")
        for path in base.rglob("*websocket*.py")
    }
    discovered.update(
        {
            "src/hmt_crypto/exchange/base.py",
            "src/hmt_crypto/exchange/delta/endpoints.py",
        }
    )
    discovered.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src/hmt_crypto/market_data").glob("*.py")
    )
    discovered.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests/architecture").glob("test_phase3_*.py")
    )
    for pattern in (
        "contract/*public_ws*.py",
        "integration/*public_stream*.py",
        "unit/test_delta_orderbook*.py",
        "unit/test_market*.py",
    ):
        discovered.update(
            path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").glob(pattern)
        )
    discovered.update(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests/fixtures/delta/websocket").rglob("*")
        if path.is_file()
    )
    assert discovered == PRODUCTION | TEST_SUPPORT


def test_phase3_files_respect_reviewed_physical_line_ceilings() -> None:
    for relative in PRODUCTION:
        count = len((ROOT / relative).read_text().splitlines())
        assert count <= 500, (relative, count, "target")
        assert count <= 600, (relative, count, "hard")
    for relative in TEST_SUPPORT:
        count = len((ROOT / relative).read_text().splitlines())
        assert count <= 800, (relative, count)


def test_phase3_runtime_imports_have_no_eager_external_side_effects() -> None:
    script = r"""import asyncio
import builtins
import importlib
import os
import pathlib
import socket
import sys
import threading

sys.path.insert(0, "src")
targets = (
    "hmt_crypto.exchange.delta.endpoints",
    "hmt_crypto.exchange.delta.websocket_contracts",
    "hmt_crypto.exchange.delta.websocket_errors",
    "hmt_crypto.exchange.delta.websocket_ownership",
    "hmt_crypto.exchange.delta.websocket_ports",
    "hmt_crypto.exchange.delta.websocket_subscriptions",
    "hmt_crypto.exchange.delta.websocket_codec",
    "hmt_crypto.exchange.delta.websocket_transport",
    "hmt_crypto.exchange.delta.websocket_public_parser_support",
    "hmt_crypto.exchange.delta.websocket_public_parsers",
    "hmt_crypto.market_data.contracts",
    "hmt_crypto.market_data.connection_attempts",
    "hmt_crypto.market_data.orderbook",
    "hmt_crypto.market_data.primitives",
    "hmt_crypto.market_data.public_stream_models",
    "hmt_crypto.market_data.public_stream_operations",
    "hmt_crypto.market_data.public_stream_registry_poll",
    "hmt_crypto.market_data.public_stream_control",
    "hmt_crypto.market_data.public_stream_support",
    "hmt_crypto.market_data.public_stream_runner",
    "hmt_crypto.market_data.public_stream_supervisor",
    "hmt_crypto.market_data.readiness",
    "hmt_crypto.market_data.reconnect_policy",
    "hmt_crypto.market_data.state_models",
    "hmt_crypto.market_data.state",
    "hmt_crypto.market_data.stream_control_owner",
    "hmt_crypto.market_data.stream_control_composition",
    "hmt_crypto.market_data.stream_control_evidence",
    "hmt_crypto.market_data.stream_control_io",
    "hmt_crypto.market_data.stream_evidence_mailbox",
    "hmt_crypto.market_data.stream_liveness",
    "hmt_crypto.market_data.stream_liveness_runner",
    "hmt_crypto.market_data.public_stream",
)
for target in targets:
    importlib.import_module(target)
for target in targets:
    del sys.modules[target]

def forbidden(*args, **kwargs):
    raise AssertionError("eager external side effect")

socket.socket = forbidden
asyncio.create_task = forbidden
threading.Thread.start = forbidden
builtins.open = forbidden
pathlib.Path.open = forbidden
pathlib.Path.read_text = forbidden
pathlib.Path.read_bytes = forbidden
os.getenv = forbidden
os._Environ.__getitem__ = forbidden

for target in targets:
    importlib.import_module(target)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
