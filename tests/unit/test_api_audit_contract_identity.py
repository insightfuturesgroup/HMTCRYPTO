"""Fresh-import identity and import-purity checks for audit contracts."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = "hmt_crypto.persistence.api_audit_contracts"
LEAVES = (
    "hmt_crypto.persistence._api_audit_contexts",
    "hmt_crypto.persistence._api_audit_summaries",
    "hmt_crypto.persistence._api_audit_records",
)


def _probe(first: str) -> dict[str, object]:
    source = textwrap.dedent(
        f"""
        import importlib, json, pickle, sys
        before = set(sys.modules)
        importlib.import_module({first!r})
        facade = importlib.import_module({PUBLIC!r})
        leaves = {[*LEAVES]!r}
        names = [
            'PublicAuditContext', 'AccountAuditContext',
            'VenueRequestSummaryV1', 'VenueResponseSummaryV1',
            'SimulatorRequestSummaryV1', 'SimulatorResponseSummaryV1',
            'LocalRejectionSummaryV1', 'TransferRejectionSummaryV1',
            'ApiAuditRecordV1', 'AuditPayloadDescriptor',
            'AuditStorageAllocation',
        ]
        objects = [getattr(facade, name) for name in names]
        result = {{
            'modules': [obj.__module__ for obj in objects],
            'pickled': [pickle.loads(pickle.dumps(obj)) is obj for obj in objects],
            'same': [
                all(getattr(importlib.import_module(leaf), name, obj) is obj for leaf in leaves)
                for name, obj in zip(names, objects, strict=True)
            ],
            'forbidden': sorted(set(sys.modules) - before & {{
                'httpx', 'socket', 'sqlalchemy.ext.asyncio',
                'hmt_crypto.exchange.delta.client',
                'hmt_crypto.exchange.delta.signer',
            }}),
            'refs': sorted(facade.ApiAuditRecordV1.model_json_schema().get('$defs', {{}})),
        }}
        print(json.dumps(result, sort_keys=True))
        """
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-I", "-c", source],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("first", (PUBLIC, *LEAVES))
def test_facade_and_leaf_first_imports_preserve_public_identity(first: str) -> None:
    result = _probe(first)
    assert result["modules"] == [PUBLIC] * 11
    assert result["pickled"] == [True] * 11
    assert result["same"] == [True] * 11
    assert result["forbidden"] == []
    assert "PublicAuditContext" in result["refs"]


def test_contract_sources_do_not_import_runtime_or_io_modules() -> None:
    for relative in (
        "src/hmt_crypto/persistence/_api_audit_contexts.py",
        "src/hmt_crypto/persistence/_api_audit_summaries.py",
        "src/hmt_crypto/persistence/_api_audit_records.py",
        "src/hmt_crypto/persistence/api_audit_contracts.py",
    ):
        tree = ast.parse((ROOT / relative).read_text())
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        for denied in (
            "httpx",
            "socket",
            "asyncio",
            "sqlalchemy",
            "hmt_crypto.exchange.delta.client",
            "hmt_crypto.exchange.delta.signer",
        ):
            assert all(name != denied and not name.startswith(f"{denied}.") for name in imports)
