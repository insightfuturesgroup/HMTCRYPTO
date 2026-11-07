"""HTTP contract tests for the sealed Delta India signed-read runtime."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from decimal import Decimal
from typing import Any

import httpx
import pytest

from hmt_crypto.config import DeltaEnvironment, DeltaRegion
from hmt_crypto.errors import (
    AuthError,
    ConfigurationError,
    ExchangeProtocolError,
    SignatureExpiredError,
)
from hmt_crypto.exchange.delta import private_read_audit, private_read_clock
from hmt_crypto.exchange.delta.private_read_factory import (
    build_delta_india_private_read_client,
)
from hmt_crypto.exchange.delta.private_read_runtime import _DeltaPrivateReadRuntime
from hmt_crypto.exchange.delta.private_read_wire import (
    _freeze_balances_private_read,
    _freeze_private_read,
)
from hmt_crypto.exchange.private_dispatch import TrustedDispatchInstant
from hmt_crypto.exchange.private_requests import LatestPositionQuery
from tests.unit import private_read_admission_test_support as admission_support


async def _no_sleep(_delay: float) -> None:
    return None


def _runtime_authorities(
    authorities: dict[str, object],
    *,
    observation_count: int,
) -> tuple[object, object]:
    clock: Any = authorities["clock"]
    observations = tuple(
        TrustedDispatchInstant(
            process_id=clock.process_id,
            process_incarnation_id=clock.process_incarnation_id,
            clock_epoch=clock.clock_epoch,
            correlation_epoch=clock.correlation_epoch,
            monotonic_ns=400_000_000_000 + index * 3_000_000_000,
            utc_epoch_seconds=1_800_000_000 + index * 3,
            utc_upper_bound_ns=(1_800_000_000 + index * 3) * 1_000_000_000 + 100_000_000,
        )
        for index in range(1, observation_count + 1)
    )
    clock_marker = private_read_clock._private_read_trusted_instant_marker_for_tests()
    issuer = private_read_clock._issue_private_read_trusted_instant_issuer_for_tests(
        clock_marker,
        clock=authorities["clock"],
        clock_currentness=authorities["clock_currentness"],
        observations=observations,
    )
    audit_marker = private_read_audit._private_read_audit_marker_for_tests()
    sink = private_read_audit._issue_private_read_audit_sink_for_tests(
        audit_marker,
        account=authorities["account"],
    )
    return issuer, sink


async def _scenario() -> None:
    """Catch pre-sign rewrites, unmetered retries, redirects, or stale-auth reuse after expiry."""
    authorities = admission_support._context()
    instant_issuer, audit_sink = _runtime_authorities(authorities, observation_count=24)
    registry: Any = authorities["registry"]
    limiter: Any = object.__getattribute__(registry, "_limiter")
    gate: Any = object.__getattribute__(limiter, "_accounting_gate")
    with gate.hold():
        object.__setattr__(limiter, "_blocked_until_monotonic_ns", 0)

    with pytest.raises(ConfigurationError):
        build_delta_india_private_read_client(
            account=authorities["account"],
            signer=authorities["signer"],
            account_binding=authorities["account_binding"],
            credential_currentness=authorities["credential_currentness"],
            worker_proof=authorities["worker_proof"],
            worker_currentness=authorities["worker_currentness"],
            clock=authorities["clock"],
            clock_currentness=authorities["clock_currentness"],
            trusted_instant_issuer=instant_issuer,
            limiter_lease=authorities["limiter_lease"],
            audit_sink=object(),
        )

    request_headers: list[dict[str, str]] = []
    request_urls: list[str] = []
    responses = iter(
        (
            httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=b'{"success":true,"result":[],"meta":{"net_equity":"0","robo_trading_equity":"0"}}',
            ),
            httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=b'{"success":true,"result":{"size":0,"entry_price":"1"}}',
            ),
            httpx.Response(503, content=b"temporary"),
            httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=b'{"success":true,"result":[],"meta":{"net_equity":"0","robo_trading_equity":"0"}}',
            ),
            httpx.Response(429, headers={"x-rate-limit-reset": "0"}, content=b"slow down"),
            httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                content=b'{"success":true,"result":[],"meta":{"net_equity":"0","robo_trading_equity":"0"}}',
            ),
            httpx.Response(302, headers={"Location": "https://example.invalid/steal"}),
            httpx.Response(
                401,
                headers={"Content-Type": "application/json"},
                content=b'{"error":"SignatureExpired","message":"redacted"}',
            ),
        )
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        request_headers.append(dict(request.headers))
        request_urls.append(str(request.url))
        assert request.method == "GET"
        assert request.content == b""
        return next(responses)

    runtime = _DeltaPrivateReadRuntime(
        account=authorities["account"],
        signer=authorities["signer"],
        account_binding=authorities["account_binding"],
        credential_currentness=authorities["credential_currentness"],
        worker_proof=authorities["worker_proof"],
        worker_currentness=authorities["worker_currentness"],
        clock=authorities["clock"],
        clock_currentness=authorities["clock_currentness"],
        trusted_instant_issuer=instant_issuer,
        limiter_lease=authorities["limiter_lease"],
        audit_sink=audit_sink,
        transport=httpx.MockTransport(handler),
        sleeper=_no_sleep,
        jitter_source=lambda: Decimal("0"),
    )
    frozen = _freeze_balances_private_read(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )

    first = await runtime._get_json(frozen)
    assert first.result == []
    assert first.meta == {"net_equity": "0", "robo_trading_equity": "0"}
    assert first.received_at > first.requested_at
    latest = _freeze_private_read(
        LatestPositionQuery(product_id=27),
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )
    latest_result = await runtime._get_json(latest)
    assert latest_result.result == {"size": 0, "entry_price": "1"}
    second = await runtime._get_json(frozen)
    assert second.result == []
    third = await runtime._get_json(frozen)
    assert third.result == []

    with pytest.raises(ExchangeProtocolError):
        await runtime._get_json(frozen)
    with pytest.raises(SignatureExpiredError):
        await runtime._get_json(frozen)

    balances_url = "https://cdn-ind.testnet.deltaex.org/v2/wallet/balances"
    latest_url = "https://cdn-ind.testnet.deltaex.org/v2/positions?product_id=27"
    assert request_urls == [balances_url, latest_url, *([balances_url] * 6)]
    timestamps: list[int] = []
    signatures: list[str] = []
    for url, headers in zip(request_urls, request_headers, strict=True):
        assert headers["api-key"] == "b4-test-api-key"
        timestamps.append(int(headers["timestamp"]))
        target = httpx.URL(url).raw_path.decode("ascii")
        prehash = f"GET{headers['timestamp']}{target}".encode("ascii")
        expected_signature = hmac.new(
            b"b4-test-api-secret",
            prehash,
            hashlib.sha256,
        ).hexdigest()
        signatures.append(expected_signature)
        assert headers["signature"] == expected_signature
        assert headers["user-agent"] == "hmt-crypto/b4-test"
        assert "authorization" not in headers
    assert len(set(timestamps)) == len(timestamps)
    assert all(
        later - earlier == 9 for earlier, later in zip(timestamps, timestamps[1:], strict=False)
    )

    audit_marker = private_read_audit._private_read_audit_marker_for_tests()
    records = private_read_audit._private_read_audit_records_for_tests(
        audit_marker,
        audit_sink,
    )
    assert len(records) == 16
    assert all(record.account_id == authorities["account"].account_id for record in records)
    encoded_audit = "".join(record.model_dump_json() for record in records).casefold()
    assert "api-key" not in encoded_audit
    assert not any(signature.casefold() in encoded_audit for signature in signatures)
    assert "b4-test" not in encoded_audit

    snapshot = await limiter.snapshot()
    assert snapshot.rest_used == 24
    with pytest.raises(ConfigurationError):
        authorities["credential_currentness"].revalidate_private_read_nonblocking(
            signer=authorities["signer"],
            proof=authorities["account_binding"],
            expected_account=authorities["account"],
            expected_origin=object.__getattribute__(authorities["account_binding"], "origin"),
            expected_slot=object.__getattribute__(authorities["signer"], "credential_slot"),
            expected_credential_version=7,
        )
    with pytest.raises(ConfigurationError):
        authorities["clock_currentness"].revalidate_dispatch_clock_nonblocking(
            authorities["clock"],
            expected_process_id=authorities["worker_proof"].process_id,
            expected_process_incarnation_id=authorities["worker_proof"].process_incarnation_id,
        )

    await runtime.aclose()
    registry.close()


async def _unauthorized_scenario() -> None:
    authorities = admission_support._context()
    instant_issuer, audit_sink = _runtime_authorities(authorities, observation_count=3)
    registry: Any = authorities["registry"]
    limiter: Any = object.__getattribute__(registry, "_limiter")
    gate: Any = object.__getattribute__(limiter, "_accounting_gate")
    with gate.hold():
        object.__setattr__(limiter, "_blocked_until_monotonic_ns", 0)
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            403,
            headers={"Content-Type": "application/json"},
            content=b'{"success":false,"error":{"code":"ip_not_whitelisted_for_api_key"}}',
        )

    runtime = _DeltaPrivateReadRuntime(
        account=authorities["account"],
        signer=authorities["signer"],
        account_binding=authorities["account_binding"],
        credential_currentness=authorities["credential_currentness"],
        worker_proof=authorities["worker_proof"],
        worker_currentness=authorities["worker_currentness"],
        clock=authorities["clock"],
        clock_currentness=authorities["clock_currentness"],
        trusted_instant_issuer=instant_issuer,
        limiter_lease=authorities["limiter_lease"],
        audit_sink=audit_sink,
        transport=httpx.MockTransport(handler),
    )
    frozen = _freeze_balances_private_read(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )

    with pytest.raises(AuthError):
        await runtime._get_json(frozen)
    with pytest.raises(ConfigurationError):
        await runtime._get_json(frozen)
    assert calls == 1
    returned_clock = authorities["clock_currentness"].revalidate_dispatch_clock_nonblocking(
        authorities["clock"],
        expected_process_id=authorities["worker_proof"].process_id,
        expected_process_incarnation_id=authorities["worker_proof"].process_incarnation_id,
    )
    assert returned_clock is authorities["clock"]
    audit_marker = private_read_audit._private_read_audit_marker_for_tests()
    records = private_read_audit._private_read_audit_records_for_tests(
        audit_marker,
        audit_sink,
    )
    assert tuple(record.outcome.value for record in records) == (
        "DISPATCH_PENDING",
        "FAILURE",
    )

    await runtime.aclose()
    registry.close()


def run() -> None:
    """Run the real sealed owner scenario in one isolated event-loop process."""
    asyncio.run(_scenario())


def run_unauthorized() -> None:
    """Run the unauthorized terminal-invalidation scenario in an isolated process."""
    asyncio.run(_unauthorized_scenario())


def run_stale_clock_rejection() -> None:
    """Prove a refresh authority cannot replay one trusted observation."""
    authorities = admission_support._context()
    clock: Any = authorities["clock"]
    observation = TrustedDispatchInstant(
        process_id=clock.process_id,
        process_incarnation_id=clock.process_incarnation_id,
        clock_epoch=clock.clock_epoch,
        correlation_epoch=clock.correlation_epoch,
        monotonic_ns=403_000_000_000,
        utc_epoch_seconds=1_800_000_003,
        utc_upper_bound_ns=1_800_000_003_100_000_000,
    )
    marker = private_read_clock._private_read_trusted_instant_marker_for_tests()
    issuer = private_read_clock._issue_private_read_trusted_instant_issuer_for_tests(
        marker,
        clock=clock,
        clock_currentness=authorities["clock_currentness"],
        observations=(observation, observation),
    )
    first = issuer.issue_fresh_nonblocking(
        clock=clock,
        clock_currentness=authorities["clock_currentness"],
        expected_process_id=authorities["worker_proof"].process_id,
        expected_process_incarnation_id=authorities["worker_proof"].process_incarnation_id,
    )
    assert first.monotonic_ns == observation.monotonic_ns
    with pytest.raises(ConfigurationError):
        issuer.issue_fresh_nonblocking(
            clock=clock,
            clock_currentness=authorities["clock_currentness"],
            expected_process_id=authorities["worker_proof"].process_id,
            expected_process_incarnation_id=authorities["worker_proof"].process_incarnation_id,
        )
    authorities["registry"].close()
