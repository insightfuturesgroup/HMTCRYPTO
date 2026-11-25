"""Exercise cleanup control flow with fake transports and UUID constructors."""

import ast
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

pytestmark = pytest.mark.safety
ROOT = Path(__file__).parents[2] / "src/hmt_crypto"


def extracted(path, name, namespace):
    tree = ast.parse((ROOT / path).read_text())
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["market_data/public_stream_control.py", "market_data/stream_control_owner.py"]
)
@pytest.mark.parametrize("error_type", [asyncio.CancelledError, RuntimeError])
async def test_send_propagates_failure_without_closing_connection(path, error_type):
    failure = error_type()

    async def fail():
        raise failure

    async def bounded(operation, **kwargs):
        await operation

    async def physical_send(_message):
        await fail()

    state = SimpleNamespace(
        bounded=bounded,
        deadline=lambda: 1,
        control_revision=0,
        _send_lock=asyncio.Lock(),
        _lock=asyncio.Lock(),
        _require_live=lambda: None,
        _physical_send=physical_send,
        _successful_actor_sends=0,
        close=Mock(),
    )
    method = extracted(
        path,
        "send",
        {
            "_OperationKind": SimpleNamespace(SEND="send"),
            "_issue_operation": lambda *args: fail(),
            "StreamControlStateError": RuntimeError,
        },
    )
    with pytest.raises(error_type) as caught:
        await method(state, "offline-canary")
    assert caught.value is failure
    assert state.control_revision == state._successful_actor_sends == 0
    state.close.assert_not_called()
    trace = caught.value.__traceback__
    while trace:
        if trace.tb_frame.f_code is method.__code__:
            assert "message" not in trace.tb_frame.f_locals
        trace = trace.tb_next


@pytest.mark.parametrize("mode", ["constructor_failure", "noncanonical_result", "valid"])
def test_uuid_constructor_errors_and_rejection_preserve_cleanup(mode):
    def constructor(**kwargs):
        if mode == "constructor_failure":
            raise ValueError("offline-canary")
        if mode == "noncanonical_result":
            return SimpleNamespace(hex="0" * 32)
        return UUID(**kwargs)

    method = extracted(
        "domain/_private_fill_contracts.py",
        "parse_compact_fill_uuid",
        {
            "UUID": constructor,
            "_COMPACT_UUID": re.compile("[0-9a-f]{32}"),
        },
    )
    value = "1" * 32
    if mode == "valid":
        assert method(value).hex == value
        return
    with pytest.raises(ValueError, match="^invalid compact fill UUID$") as caught:
        method(value)
    trace = caught.value.__traceback__
    while trace:
        if trace.tb_frame.f_code is method.__code__:
            assert "value" not in trace.tb_frame.f_locals
            assert "raw" not in trace.tb_frame.f_locals
        trace = trace.tb_next
