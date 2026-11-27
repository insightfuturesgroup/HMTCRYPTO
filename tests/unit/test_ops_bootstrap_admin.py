"""Ops-7 account binding update is tested with fake text only."""

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
