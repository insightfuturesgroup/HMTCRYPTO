"""Known-vector and sealed Delta private WebSocket authentication tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from pydantic import SecretStr

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.errors import (
    AuthError,
    ConfigurationError,
    ExchangeDownError,
    ExchangeProtocolError,
    SignatureExpiredError,
    UnauthorizedApiAccessError,
)
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.delta import private_read_clock
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin
from hmt_crypto.exchange.delta.private_ws_auth import (
    PrivateWsAuthStatus,
    bind_private_ws_auth_issuer,
    parse_private_ws_auth_response,
    raise_for_private_ws_auth,
    require_india_testnet_private_endpoint,
)
from hmt_crypto.exchange.delta.websocket_codec import decode_ws_object
from hmt_crypto.exchange.delta.websocket_contracts import WsEndpoint, WsPlane
from hmt_crypto.exchange.private_dispatch import TrustedDispatchInstant
from hmt_crypto.worker import clock as worker_clock

ACCOUNT_ID = UUID("00000000-0000-0000-0000-00000000b402")
INCARNATION = UUID("00000000-0000-0000-0000-00000000b401")
ISSUED = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def endpoint() -> WsEndpoint:
    return WsEndpoint(
        plane=WsPlane.PRIVATE,
        url="wss://socket-ind.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )


def bound_authority(*, observations: int = 1) -> tuple[object, dict[str, Any]]:
    account = AccountRef(
        account_id=ACCOUNT_ID,
        external_user_id="12345",
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        mode=ExecutionMode.PAPER,
        credential_version=7,
    )
    origin = _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET)
    signer, _enrollment = credentials._issue_enrollment_credential_for_tests(
        credentials._task27_fixture_marker_for_tests(),
        snapshot_id=UUID("00000000-0000-0000-0000-00000000b403"),
        credential_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
        credential_version=7,
        account_id=ACCOUNT_ID,
        external_user_id="12345",
        origin=origin,
        mode=ExecutionMode.PAPER,
        credential_epoch=11,
        api_key=SecretStr("b4-test-api-key"),
        api_secret=SecretStr("b4-test-api-secret"),
        user_agent="hmt-crypto/b4-test",
    )
    proof, credential_currentness = credentials._issue_account_binding_proof_for_tests(
        credentials._task27_fixture_marker_for_tests(),
        signer,
        proof_id=UUID("00000000-0000-0000-0000-00000000b404"),
        account=account,
        origin=origin,
        binding_ref=UUID("00000000-0000-0000-0000-00000000b405"),
        probe_ref=UUID("00000000-0000-0000-0000-00000000b406"),
        credential_epoch=11,
        proof_epoch=13,
        observed_at=ISSUED,
        expires_at=ISSUED + timedelta(minutes=5),
    )
    clock, clock_currentness = worker_clock._issue_trusted_dispatch_clock_for_tests(
        worker_clock._task210_fixture_marker_for_tests(),
        clock_owner_id=UUID(int=104),
        process_incarnation_id=INCARNATION,
        clock_epoch=1,
        correlation_epoch=1,
        sample_monotonic_ns=400_000_000_000,
        observed_monotonic_ns=400_000_000_000,
        utc_epoch_seconds=1_800_000_000,
        utc_upper_bound_ns=1_800_000_000_100_000_000,
        nonblocking_execution_guard_ns=1,
    )
    instants = tuple(
        TrustedDispatchInstant(
            process_id=clock.process_id,
            process_incarnation_id=clock.process_incarnation_id,
            clock_epoch=clock.clock_epoch,
            correlation_epoch=clock.correlation_epoch,
            monotonic_ns=400_000_000_000 + index * 3_000_000_000,
            utc_epoch_seconds=1_800_000_000 + index * 3,
            utc_upper_bound_ns=(1_800_000_000 + index * 3) * 1_000_000_000 + 100_000_000,
        )
        for index in range(1, observations + 1)
    )
    instant_issuer = private_read_clock._issue_private_read_trusted_instant_issuer_for_tests(
        private_read_clock._private_read_trusted_instant_marker_for_tests(),
        clock=clock,
        clock_currentness=clock_currentness,
        observations=instants,
    )
    authority = bind_private_ws_auth_issuer(
        endpoint=endpoint(),
        account=account,
        signer=signer,
        account_binding=proof,
        credential_currentness=credential_currentness,
        clock=clock,
        clock_currentness=clock_currentness,
        trusted_instant_issuer=instant_issuer,
    )
    context: dict[str, Any] = {
        "account": account,
        "signer": signer,
        "account_binding": proof,
        "credential_currentness": credential_currentness,
        "clock": clock,
        "clock_currentness": clock_currentness,
    }
    return authority, context


def decode(frame: str) -> dict[str, object]:
    return decode_ws_object(frame, max_bytes=4096, stream_label="private")


def test_key_auth_known_vector_consumes_trusted_instant_and_uses_number() -> None:
    authority, _context = bound_authority()
    frame = authority.encode_fresh_auth()  # type: ignore[attr-defined]
    assert frame == (
        '{"type":"key-auth","payload":{"api-key":"b4-test-api-key",'
        '"timestamp":1800000003,'
        '"signature":"5504df90460e47bb37e6d484b0b547c19063125f82882b1ee15b4bca79f7d4f7"}}'
    )
    assert "b4-test-api-secret" not in frame
    assert "redacted" in repr(authority).casefold()
    with pytest.raises(ConfigurationError):
        authority.encode_fresh_auth()  # type: ignore[attr-defined]


def test_raw_secrets_or_forged_authorities_cannot_construct_auth_issuer() -> None:
    from hmt_crypto.exchange.delta.private_ws_auth import PrivateWsAuthIssuer

    with pytest.raises(TypeError):
        PrivateWsAuthIssuer()
    with pytest.raises(ConfigurationError):
        bind_private_ws_auth_issuer(  # type: ignore[arg-type]
            endpoint=endpoint(),
            account=object(),
            signer=object(),
            account_binding=object(),
            credential_currentness=object(),
            clock=object(),
            clock_currentness=object(),
            trusted_instant_issuer=object(),
        )


def test_only_exact_india_testnet_private_endpoint_is_admitted() -> None:
    assert require_india_testnet_private_endpoint(endpoint()) == endpoint()
    for candidate in (
        WsEndpoint(WsPlane.PRIVATE, "wss://socket.india.delta.exchange", "india", "prod"),
        WsEndpoint(WsPlane.PUBLIC, "wss://socket-ind.testnet.deltaex.org", "india", "testnet"),
        WsEndpoint(WsPlane.PRIVATE, "wss://evil.invalid", "india", "testnet"),
    ):
        with pytest.raises(ConfigurationError):
            require_india_testnet_private_endpoint(candidate)


@pytest.mark.parametrize(
    ("status", "code", "error_type"),
    [
        ("incomplete_payload", 400, AuthError),
        ("request_expired", 408, SignatureExpiredError),
        ("api_key_not_found", 404, AuthError),
        ("invalid_signature", 401, AuthError),
        ("ip_not_whitelisted", 401, UnauthorizedApiAccessError),
        ("internal_server_error", 500, ExchangeDownError),
    ],
)
def test_documented_auth_rejections_map_without_retaining_message(
    status: str, code: int, error_type: type[Exception]
) -> None:
    result = parse_private_ws_auth_response(
        decode(
            json.dumps(
                {
                    "type": "key-auth",
                    "success": False,
                    "status_code": code,
                    "status": status,
                    "message": "IP address not whitelisted. Your IP: 192.0.2.12",
                }
            )
        )
    )
    assert result.status is PrivateWsAuthStatus(status)
    with pytest.raises(error_type) as captured:
        raise_for_private_ws_auth(result)
    assert "192.0.2.12" not in str(captured.value)


def test_success_shape_is_exact_and_unknown_or_contradictory_shapes_fail() -> None:
    result = parse_private_ws_auth_response(
        decode('{"type":"key-auth","success":true,"status_code":200,"status":"authenticated"}')
    )
    assert result.success is True
    raise_for_private_ws_auth(result)
    malformed = (
        '{"type":"key-auth","success":true,"status_code":200,'
        '"status":"authenticated","message":"unexpected"}',
        '{"type":"key-auth","success":false,"status_code":401,'
        '"status":"authenticated","message":"no"}',
        '{"type":"key-auth","success":false,"status_code":418,'
        '"status":"invalid_signature","message":"no"}',
        '{"type":"key-auth","success":false,"status_code":401,'
        '"status":"new_status","message":"no"}',
    )
    for wire in malformed:
        with pytest.raises(ExchangeProtocolError):
            parse_private_ws_auth_response(decode(wire))


def test_duplicate_auth_keys_fail_closed() -> None:
    with pytest.raises(ExchangeProtocolError):
        decode(
            '{"type":"key-auth","success":true,"success":false,'
            '"status_code":200,"status":"authenticated"}'
        )
