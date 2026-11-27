"""Ops-7 account binding update is tested with fake text only."""

from pathlib import Path

import pytest

from ops.aws.bootstrap_admin import replace_account_id


def test_replace_exactly_one_account_binding() -> None:
    result = replace_account_id(
        "A=true\nHMT_API_ACCOUNT_ID=00000000-0000-4000-8000-000000000001\n",
        "10000000-0000-4000-8000-000000000001",
    )
    assert result.endswith("HMT_API_ACCOUNT_ID=10000000-0000-4000-8000-000000000001\n")


def test_missing_binding_fails() -> None:
    with pytest.raises(ValueError, match="missing"):
        replace_account_id("A=true\n", "10000000-0000-4000-8000-000000000001")


def test_bootstrap_updates_persistent_protected_api_environment() -> None:
    source = Path("ops/aws/bootstrap_admin.py").read_text()
    assert 'Path("/etc/hmt/api.env")' in source
    assert "/run/hmt" not in source
