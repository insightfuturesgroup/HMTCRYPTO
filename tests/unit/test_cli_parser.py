"""Typer parsing remains inside the CLI's safe JSON boundary."""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from hmt_crypto import cli, cli_runtime

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("arguments", "hostile_marker"),
    [
        (("UNKNOWN-COMMAND-SECRET",), "UNKNOWN-COMMAND-SECRET"),
        (("ticker", "XBTUSD_CAT", "--UNKNOWN-OPTION-SECRET"), "UNKNOWN-OPTION-SECRET"),
        (("ticker", "XBTUSD_CAT", "EXTRA-ARGUMENT-SECRET"), "EXTRA-ARGUMENT-SECRET"),
    ],
)
def test_parser_failures_are_safe_json_without_reflecting_input(
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, ...],
    hostile_marker: str,
) -> None:
    monkeypatch.setattr(
        cli_runtime,
        "_build_public_client",
        lambda settings: pytest.fail("parser failure must not create a client"),
    )

    result = CliRunner().invoke(cli.app, list(arguments))
    stream = result.stdout or result.stderr
    payload: Any = json.loads(stream)

    assert result.exit_code == 2
    assert stream.count("\n") == 1
    assert payload == {"code": "invalid_input", "message": "Command input is invalid"}
    assert hostile_marker not in result.stdout + result.stderr
