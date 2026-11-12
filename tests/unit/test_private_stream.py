import pytest

from hmt_crypto.worker.private_stream import (
    _acknowledged_channels,
    _receive_subscription_ack,
    _stream_key,
    _subscription_payload,
)


class _Socket:
    def __init__(self, frames: list[str]) -> None:
        self._frames = iter(frames)

    async def recv(self) -> str:
        return next(self._frames)


def test_subscription_uses_explicit_qualified_symbols() -> None:
    channels = _subscription_payload()["payload"]["channels"]  # type: ignore[index]

    assert channels == [
        {"name": "orders", "symbols": ["BTCUSD", "ETHUSD"]},
        {"name": "positions", "symbols": ["BTCUSD", "ETHUSD"]},
        {"name": "margins"},
        {"name": "v2/user_trades", "symbols": ["BTCUSD", "ETHUSD"]},
    ]


def test_subscription_ack_requires_every_private_channel_without_error() -> None:
    frame = {
        "type": "subscriptions",
        "channels": [
            {"name": "orders", "symbols": ["BTCUSD", "ETHUSD"]},
            {"name": "positions", "symbols": ["BTCUSD", "ETHUSD"]},
            {"name": "margins"},
            {"name": "v2/user_trades", "symbols": ["BTCUSD", "ETHUSD"]},
        ],
    }

    assert _acknowledged_channels(frame) == {
        "orders",
        "positions",
        "margins",
        "v2/user_trades",
    }
    frame["channels"][2]["error"] = "subscription forbidden"  # type: ignore[index]
    with pytest.raises(ValueError, match="rejected"):
        _acknowledged_channels(frame)


def test_sequence_scope_is_per_channel_and_symbol() -> None:
    assert _stream_key("orders", {"symbol": "BTCUSD"}) == ("orders", "BTCUSD")
    assert _stream_key("v2/user_trades", {"sy": "ETHUSD"}) == (
        "v2/user_trades",
        "ETHUSD",
    )
    assert _stream_key("margins", {"asset_symbol": "USD"}) == ("margins", "account")


@pytest.mark.asyncio
async def test_subscription_ack_tolerates_interleaved_heartbeat_and_snapshot() -> None:
    socket = _Socket(
        [
            '{"type":"heartbeat"}',
            '{"type":"orders","action":"snapshot"}',
            '{"type":"subscriptions","channels":['
            '{"name":"orders","symbols":["BTCUSD","ETHUSD"]},'
            '{"name":"positions","symbols":["BTCUSD","ETHUSD"]},'
            '{"name":"margins"},'
            '{"name":"v2/user_trades","symbols":["BTCUSD","ETHUSD"]}]}'
        ]
    )

    assert await _receive_subscription_ack(socket) == {
        "orders",
        "positions",
        "margins",
        "v2/user_trades",
    }
