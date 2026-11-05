"""Independent, test-only fixture helpers for Delta private parser cases."""

from __future__ import annotations

import json
from pathlib import Path

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "delta" / "private" / "mutations"


def fixture_bytes(group: str, name: str) -> bytes:
    """Read one explicitly offline candidate fixture as immutable bytes."""
    return (_FIXTURE_ROOT / group / name).read_bytes()


def fixture_result(group: str, name: str) -> dict[str, object]:
    """Return an isolated result object using only the stdlib test decoder."""
    envelope = json.loads(fixture_bytes(group, name))
    assert type(envelope) is dict
    result = envelope["result"]
    assert type(result) is dict
    return result


ORDER_FIXTURE = "offline_candidate_order_success.json"
LEVERAGE_FIXTURE = "offline_candidate_leverage_success.json"
MARGIN_MODE_FIXTURE = "offline_candidate_margin_mode_success.json"
POSITION_FIXTURE = "offline_candidate_position_success.json"
CANCEL_ALL_FIXTURE = "offline_candidate_cancel_all_success.json"
CREATE_BRACKET_FIXTURE = "offline_candidate_create_bracket_success.json"
EDIT_BRACKET_FIXTURE = "offline_candidate_edit_bracket_success.json"
