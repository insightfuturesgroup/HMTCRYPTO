"""Shared helpers for the staged Delta private-account boundary tests."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from pydantic import ValidationError

_PRIVATE_OWNER_MODULE_NAMES = (
    "hmt_crypto.domain.private_orders",
    "hmt_crypto.domain._private_order_contracts",
    "hmt_crypto.domain._private_fill_contracts",
    "hmt_crypto.domain._private_result_contracts",
    "hmt_crypto.domain.private_wallet",
    "hmt_crypto.domain._private_wallet_contracts",
    "hmt_crypto.domain.private_positions",
    "hmt_crypto.domain._private_positions_contracts",
)


@contextmanager
def preserve_private_owner_modules() -> Iterator[None]:
    """Restore pre-existing private-domain module bindings after an isolated test module."""
    absent = object()
    domain = sys.modules.get("hmt_crypto.domain")
    modules = {name: sys.modules.get(name, absent) for name in _PRIVATE_OWNER_MODULE_NAMES}
    domain_bindings = {
        name: (
            domain.__dict__.get(name.rsplit(".", 1)[1], absent) if domain is not None else absent
        )
        for name in _PRIVATE_OWNER_MODULE_NAMES
    }
    try:
        yield
    finally:
        current_domain = sys.modules.get("hmt_crypto.domain")
        for name in _PRIVATE_OWNER_MODULE_NAMES:
            previous_module = modules[name]
            if previous_module is absent:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous_module
            if current_domain is None:
                continue
            short_name = name.rsplit(".", 1)[1]
            previous_binding = domain_bindings[name]
            if previous_binding is absent:
                current_domain.__dict__.pop(short_name, None)
            else:
                current_domain.__dict__[short_name] = previous_binding


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
