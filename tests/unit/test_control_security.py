from __future__ import annotations

import asyncio
import threading

import pytest
from argon2 import PasswordHasher

from hmt_crypto.api.security import LoginRateLimiter
from hmt_crypto.api.session_store import Argon2PasswordVerifier


@pytest.mark.asyncio
async def test_login_peer_buckets_are_strictly_bounded_and_expire() -> None:
    now = [0.0]
    limiter = LoginRateLimiter(2, max_peers=64, clock=lambda: now[0])

    for index in range(500):
        assert await limiter.record_failure(f"192.0.2.{index}") in {True, False}

    assert limiter.peer_bucket_count <= 64
    now[0] = 301.0
    assert await limiter.record_failure("198.51.100.1") is True
    assert limiter.peer_bucket_count == 1


@pytest.mark.asyncio
async def test_valid_password_verification_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "correct horse battery staple"
    encoded = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(password)
    verifier = Argon2PasswordVerifier(encoded)
    event_loop_thread = threading.get_ident()
    verification_threads: list[int] = []
    original = PasswordHasher.verify

    def observed(owner: PasswordHasher, encoded_hash: str, candidate: str) -> bool:
        verification_threads.append(threading.get_ident())
        return original(owner, encoded_hash, candidate)

    monkeypatch.setattr(PasswordHasher, "verify", observed)
    assert await verifier.verify_async(password) is True
    assert verification_threads
    assert all(identity != event_loop_thread for identity in verification_threads)


@pytest.mark.asyncio
async def test_cancelled_password_verification_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(
        "correct horse battery staple"
    )
    verifier = Argon2PasswordVerifier(encoded)
    release = threading.Event()

    def blocked(_owner: PasswordHasher, _encoded_hash: str, _candidate: str) -> bool:
        release.wait(timeout=1)
        return False

    monkeypatch.setattr(PasswordHasher, "verify", blocked)
    task = asyncio.create_task(verifier.verify_async("correct horse battery staple"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
