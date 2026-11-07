"""Opt-in India-testnet qualification placeholder for signed private reads."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


def _credentials_enabled() -> bool:
    return (
        os.getenv("RUN_DELTA_PRIVATE_TESTNET_SMOKE") == "1"
        and bool(os.getenv("DELTA_INDIA_TESTNET_API_KEY"))
        and bool(os.getenv("DELTA_INDIA_TESTNET_API_SECRET"))
        and bool(os.getenv("DELTA_INDIA_TESTNET_EXTERNAL_USER_ID"))
    )


@pytest.mark.skipif(
    not _credentials_enabled(),
    reason="set explicit India-testnet private-read credentials and opt-in flag",
)
def test_private_read_testnet_qualification_requires_server_authorities() -> None:
    """Never forge account-binding/clock/audit evidence merely to run a smoke test."""
    pytest.skip(
        "production account-binding, refreshable-clock, and durable-audit issuers are "
        "not available; private testnet dispatch remains fail-closed"
    )
