"""Best-effort Redis fast paths; PostgreSQL remains the durable authority."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

MARKET_CHANNEL = "hmt:market:updates"
ORDER_CHANNEL = "hmt:orders:wake"


def redis_client() -> Any:
    from redis.asyncio import Redis

    return Redis.from_url(
        os.getenv("HMT_REDIS_URL", "redis://redis:6379/0"),
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=2,
        health_check_interval=30,
    )


async def publish_json(channel: str, payload: dict[str, Any]) -> None:
    client = redis_client()
    try:
        await client.publish(channel, json.dumps(payload, separators=(",", ":")))
    finally:
        await client.aclose()


async def subscribe_json(channel: str) -> AsyncIterator[dict[str, Any]]:
    client = redis_client()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            value = json.loads(message["data"])
            if isinstance(value, dict):
                yield value
    finally:
        await pubsub.aclose()
        await client.aclose()


async def wait_for_message(channel: str, wait_seconds: float) -> bool:
    """Wait for a best-effort wake-up without replacing durable DB polling."""
    client = redis_client()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            remaining = deadline - asyncio.get_running_loop().time()
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=min(remaining, 1.0),
            )
            if message is not None:
                return True
        return False
    finally:
        await pubsub.aclose()
        await client.aclose()
