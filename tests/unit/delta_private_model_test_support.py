"""Shared helpers for the staged Delta private-account boundary tests."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from pydantic import ValidationError


def account_api() -> SimpleNamespace:
    module = importlib.import_module("hmt_crypto.domain.account")
    names = (
        "AccountRef",
        "CollectionMode",
        "DeltaRestSource",
        "DiagnosticPaginationKind",
        "ExactBool",
        "ExactDeltaEnvironment",
        "ExactDeltaRegion",
        "ExactExecutionMode",
        "ExactTrue",
        "FieldPresence",
        "LookupFoundKind",
        "LookupNotFoundKind",
        "MissingField",
        "MissingKind",
        "NullableField",
        "NullableOpaqueCursor",
        "NullField",
        "NullKind",
        "NumericFillOrderKind",
        "ObservationContext",
        "Observed",
        "ObservedEnum",
        "OpaqueCursor",
        "OpaqueCursorValueField",
        "PrivatePaginationProvenance",
        "PrivatePolicyVersion",
        "SettlementFillOrderKind",
        "StrictPaginationKind",
        "TruncationReason",
        "ValueField",
        "ValueKind",
    )
    return SimpleNamespace(module=module, **{name: getattr(module, name) for name in names})


def valid_account(api: SimpleNamespace) -> object:
    from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode

    return api.AccountRef(
        account_id=UUID("00000000-0000-0000-0000-000000000001"),
        external_user_id="12345",
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=ExecutionMode.PAPER,
        credential_version=7,
    )


def isolated_python(source: str) -> subprocess.CompletedProcess[str]:
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project_root / "src")
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=project_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def reachable_exceptions(root: BaseException) -> tuple[BaseException, ...]:
    pending = [root]
    seen: set[int] = set()
    reachable: list[BaseException] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        reachable.append(current)
        for linked in (current.__context__, current.__cause__):
            if linked is not None:
                pending.append(linked)
    return tuple(reachable)


def assert_safe_generic_error(error: BaseException, canary: str) -> None:
    for reachable in reachable_exceptions(error):
        rendered = f"{reachable!s} {reachable!r} {reachable.args!r}"
        if isinstance(reachable, ValidationError):
            rendered += f" {reachable.errors(include_url=False)!r}"
        assert canary not in rendered
    if isinstance(error, ValidationError):
        assert error.errors(include_url=False) == [
            {
                "type": "private_generic_specialization",
                "loc": (),
                "msg": "private generic specialization unavailable",
                "input": None,
            }
        ]
