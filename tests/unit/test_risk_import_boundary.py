"""Risk evaluation imports no authority-bearing application layer."""

import subprocess
import sys


def test_fresh_risk_manager_import_has_no_db_network_exchange_or_config_authority() -> None:
    script = """
import json
import sys
from hmt_crypto.risk.manager import RiskManager

prefixes = (
    "hmt_crypto.persistence",
    "hmt_crypto.exchange",
    "hmt_crypto.config",
    "sqlalchemy",
    "httpx",
    "socket",
    "asyncio",
    "logging",
)
loaded = sorted(name for name in sys.modules if name.startswith(prefixes))
print(json.dumps(loaded))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "[]"
