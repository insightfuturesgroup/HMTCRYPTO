"""Explicitly opted-in, read-only Delta India testnet private-stream smoke."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from hmt_crypto.config import DeltaEnvironment, DeltaRegion, ExecutionMode
from hmt_crypto.domain import Product
from hmt_crypto.domain.account import AccountRef
from hmt_crypto.exchange import credentials
from hmt_crypto.exchange.delta import private_read_clock
from hmt_crypto.exchange.delta.catalog import CatalogProductLoad, ProductCatalog
from hmt_crypto.exchange.delta.catalog_models import CatalogSnapshot, CatalogSource
from hmt_crypto.exchange.delta.paginator import PaginationResult
from hmt_crypto.exchange.delta.private_origin import _canonical_private_origin
from hmt_crypto.exchange.delta.private_ws_auth import (
    bind_private_ws_auth_issuer,
    parse_private_ws_auth_response,
    raise_for_private_ws_auth,
)
from hmt_crypto.exchange.delta.private_ws_connection import (
    PrivateWsConnectionAuthority,
    bind_private_ws_connection_authority,
)
from hmt_crypto.exchange.delta.private_ws_protocol import private_ws_subscription_plan
from hmt_crypto.exchange.delta.rate_limit_policy import RateLimitScope, ScopeKind
from hmt_crypto.exchange.delta.websocket_codec import (
    decode_ws_object,
    encode_enable_heartbeat,
)
from hmt_crypto.exchange.delta.websocket_contracts import WsEndpoint, WsPlane
from hmt_crypto.exchange.delta.websocket_transport import DeltaWebsocketsConnector
from hmt_crypto.exchange.delta.wire_schemas import parse_product
from hmt_crypto.exchange.private_dispatch import TrustedDispatchInstant
from hmt_crypto.market_data import connection_attempts
from hmt_crypto.market_data.connection_attempts import ConnectionAttemptGovernor
from hmt_crypto.market_data.reconnect_policy import DEFAULT_RECONNECT_POLICY
from hmt_crypto.worker import clock as worker_clock
from hmt_crypto.worker import leadership

_OPT_IN = "I_UNDERSTAND_DELTA_INDIA_TESTNET_PRIVATE_READ_ONLY"
_MAX_SECRET_FILE_BYTES = 4096
_MAX_PRODUCT_FILE_BYTES = 1024 * 1024
_MAX_FRAME_BYTES = 64 * 1024


def _fail(message: str) -> NoReturn:
    pytest.fail(message, pytrace=False)


def _required_environment() -> dict[str, str]:
    opt_in = os.environ.get("HMT_DELTA_PRIVATE_WS_TESTNET_OPT_IN")
    if not opt_in:
        pytest.skip("Delta India private-stream testnet smoke was not explicitly opted in")
    if opt_in != _OPT_IN:
        _fail("Delta India private-stream testnet opt-in value is invalid")
    names = (
        "HMT_DELTA_INDIA_TESTNET_API_KEY_FILE",
        "HMT_DELTA_INDIA_TESTNET_API_SECRET_FILE",
        "HMT_DELTA_INDIA_TESTNET_ACCOUNT_ID",
        "HMT_DELTA_INDIA_TESTNET_EXTERNAL_USER_ID",
        "HMT_DELTA_INDIA_TESTNET_CREDENTIAL_VERSION",
        "HMT_DELTA_INDIA_TESTNET_PRODUCT_FILE",
        "HMT_DELTA_INDIA_TESTNET_PRODUCT_SYMBOL",
        "HMT_DELTA_INDIA_TESTNET_CATALOGUE_SNAPSHOT_ID",
        "HMT_DELTA_PRIVATE_WS_EGRESS_IDENTITY",
        "HMT_DELTA_PRIVATE_WS_STATIC_EGRESS_ATTESTED",
    )
    values = {name: os.environ.get(name, "") for name in names}
    if any(not value for value in values.values()):
        _fail("Delta India private-stream testnet host composition is incomplete")
    if values["HMT_DELTA_PRIVATE_WS_STATIC_EGRESS_ATTESTED"] != "true":
        _fail("Delta India testnet static egress is not attested")
    return values


def _read_secret(path_text: str) -> SecretStr:
    path = Path(path_text)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        _fail("Delta India testnet credential file is invalid")
    size = path.stat().st_size
    if not 1 <= size <= _MAX_SECRET_FILE_BYTES:
        _fail("Delta India testnet credential file is invalid")
    value = path.read_text(encoding="utf-8").strip()
    if not value or not value.isascii() or any(character.isspace() for character in value):
        _fail("Delta India testnet credential file is invalid")
    return SecretStr(value)


def _read_product(path_text: str, *, symbol: str) -> Product:
    path = Path(path_text)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        _fail("Delta India testnet product fixture is invalid")
    if not 1 <= path.stat().st_size <= _MAX_PRODUCT_FILE_BYTES:
        _fail("Delta India testnet product fixture is invalid")

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate product key")
            value[key] = item
        return value

    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
        product = parse_product(raw, endpoint_id="private_ws_testnet_smoke")
    except Exception:
        _fail("Delta India testnet product fixture is invalid")
    if product.symbol != symbol:
        _fail("Delta India testnet product symbol is mismatched")
    return product


async def _published_catalogue(product: Product, *, now: datetime) -> CatalogSnapshot:
    source = CatalogSource(
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
    )

    async def fetch_products() -> CatalogProductLoad:
        return CatalogProductLoad(
            source=source,
            pagination=PaginationResult(
                items=(product,),
                page_count=1,
                complete=True,
                truncated=False,
                terminal_after=None,
            ),
        )

    catalogue = ProductCatalog(
        source=source,
        fetch_products=fetch_products,
        ttl=timedelta(minutes=10),
        clock=lambda: now,
    )
    result = await catalogue.refresh()
    snapshot = catalogue.peek()
    if snapshot is None or result.active_revision is None:
        _fail("Delta India testnet catalogue publication failed")
    return snapshot


def _account(values: dict[str, str]) -> AccountRef:
    try:
        account_id = UUID(values["HMT_DELTA_INDIA_TESTNET_ACCOUNT_ID"])
        external_user_id = values["HMT_DELTA_INDIA_TESTNET_EXTERNAL_USER_ID"]
        credential_version = int(values["HMT_DELTA_INDIA_TESTNET_CREDENTIAL_VERSION"])
        if not external_user_id.isdigit() or int(external_user_id) <= 0:
            raise ValueError
        return AccountRef(
            account_id=account_id,
            external_user_id=external_user_id,
            region=DeltaRegion.INDIA,
            environment=DeltaEnvironment.TESTNET,
            mode=ExecutionMode.PAPER,
            credential_version=credential_version,
        )
    except Exception:
        _fail("Delta India testnet account identity is invalid")


def _endpoint() -> WsEndpoint:
    return WsEndpoint(
        plane=WsPlane.PRIVATE,
        url="wss://socket-ind.testnet.deltaex.org",
        region="india",
        environment="testnet",
    )


@pytest.mark.integration
@pytest.mark.testnet
@pytest.mark.asyncio
async def test_delta_india_testnet_private_stream_read_only_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authenticate and subscribe on India testnet without any mutation message."""
    values = _required_environment()
    now = datetime.now(UTC)
    epoch_seconds = int(now.timestamp())
    monotonic_ns = time.monotonic_ns()
    account = _account(values)
    api_key = _read_secret(values["HMT_DELTA_INDIA_TESTNET_API_KEY_FILE"])
    api_secret = _read_secret(values["HMT_DELTA_INDIA_TESTNET_API_SECRET_FILE"])
    product = _read_product(
        values["HMT_DELTA_INDIA_TESTNET_PRODUCT_FILE"],
        symbol=values["HMT_DELTA_INDIA_TESTNET_PRODUCT_SYMBOL"],
    )
    catalogue = await _published_catalogue(product, now=now)
    try:
        catalogue_snapshot_id = UUID(values["HMT_DELTA_INDIA_TESTNET_CATALOGUE_SNAPSHOT_ID"])
    except ValueError:
        _fail("Delta India testnet catalogue identity is invalid")
    plan = private_ws_subscription_plan(
        symbol=product.symbol,
        catalogue=catalogue,
        catalogue_snapshot_id=catalogue_snapshot_id,
    )

    origin = _canonical_private_origin(DeltaRegion.INDIA, DeltaEnvironment.TESTNET)
    signer, _enrollment = credentials._issue_enrollment_credential_for_tests(
        credentials._task27_fixture_marker_for_tests(),
        snapshot_id=uuid4(),
        credential_slot=credentials.IndiaCredentialSlot.INDIA_TESTNET,
        credential_version=account.credential_version,
        account_id=account.account_id,
        external_user_id=account.external_user_id,
        origin=origin,
        mode=ExecutionMode.PAPER,
        credential_epoch=1,
        api_key=api_key,
        api_secret=api_secret,
        user_agent="hmt-crypto/private-ws-testnet-smoke",
    )
    del api_key, api_secret
    proof, credential_currentness = credentials._issue_account_binding_proof_for_tests(
        credentials._task27_fixture_marker_for_tests(),
        signer,
        proof_id=uuid4(),
        account=account,
        origin=origin,
        binding_ref=uuid4(),
        probe_ref=uuid4(),
        credential_epoch=1,
        proof_epoch=1,
        observed_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    process_incarnation_id = uuid4()
    worker_proof, worker_currentness = leadership._issue_single_private_worker_for_tests(
        leadership._task210_fixture_marker_for_tests(),
        proof_id=uuid4(),
        deployment_revision="private-ws-testnet-smoke",
        external_user_id=account.external_user_id,
        worker_incarnation_id=uuid4(),
        process_incarnation_id=process_incarnation_id,
        region=DeltaRegion.INDIA,
        environment=DeltaEnvironment.TESTNET,
        ownership_epoch=1,
        issued_at=now,
        expires_at=now + timedelta(minutes=10),
        observed_at=now,
        process_started_monotonic_ns=max(
            0, monotonic_ns - DEFAULT_RECONNECT_POLICY.attempt_window_ns - 1
        ),
    )
    clock, clock_currentness = worker_clock._issue_trusted_dispatch_clock_for_tests(
        worker_clock._task210_fixture_marker_for_tests(),
        clock_owner_id=uuid4(),
        process_incarnation_id=process_incarnation_id,
        clock_epoch=1,
        correlation_epoch=1,
        sample_monotonic_ns=monotonic_ns,
        observed_monotonic_ns=monotonic_ns,
        utc_epoch_seconds=epoch_seconds,
        utc_upper_bound_ns=epoch_seconds * 1_000_000_000 + 999_999_999,
        nonblocking_execution_guard_ns=1,
    )
    samples = iter(
        (
            monotonic_ns,
            monotonic_ns + DEFAULT_RECONNECT_POLICY.attempt_window_ns + 1,
        )
    )
    last_sample = monotonic_ns + DEFAULT_RECONNECT_POLICY.attempt_window_ns + 1

    def sample_clock() -> int:
        return next(samples, last_sample)

    monkeypatch.setattr(connection_attempts, "_sample_clock", sample_clock)
    scope = RateLimitScope(
        "india",
        "testnet",
        ScopeKind.IP,
        values["HMT_DELTA_PRIVATE_WS_EGRESS_IDENTITY"],
    )
    attempts = ConnectionAttemptGovernor.for_single_process_egress(
        scope,
        ownership_proof=worker_proof,
        ownership_currentness=worker_currentness,
        policy=DEFAULT_RECONNECT_POLICY,
    )
    connector = DeltaWebsocketsConnector(max_size=1024 * 1024)
    auth_monotonic_ns = max(time.monotonic_ns(), monotonic_ns + 1)
    auth_epoch_seconds = int(datetime.now(UTC).timestamp())
    trusted_instant = TrustedDispatchInstant(
        process_id=clock.process_id,
        process_incarnation_id=process_incarnation_id,
        clock_epoch=clock.clock_epoch,
        correlation_epoch=clock.correlation_epoch,
        monotonic_ns=auth_monotonic_ns,
        utc_epoch_seconds=auth_epoch_seconds,
        utc_upper_bound_ns=auth_epoch_seconds * 1_000_000_000 + 999_999_999,
    )
    instant_issuer = private_read_clock._issue_private_read_trusted_instant_issuer_for_tests(
        private_read_clock._private_read_trusted_instant_marker_for_tests(),
        clock=clock,
        clock_currentness=clock_currentness,
        observations=(trusted_instant,),
    )
    auth = bind_private_ws_auth_issuer(
        endpoint=_endpoint(),
        account=account,
        signer=signer,
        account_binding=proof,
        credential_currentness=credential_currentness,
        clock=clock,
        clock_currentness=clock_currentness,
        trusted_instant_issuer=instant_issuer,
    )
    connection_authority = bind_private_ws_connection_authority(
        connector=connector,
        attempts=attempts,
        auth=auth,
        egress_scope=scope,
        worker_proof=worker_proof,
        worker_currentness=worker_currentness,
        reconnect_policy=DEFAULT_RECONNECT_POLICY,
    )
    assert type(connection_authority) is PrivateWsConnectionAuthority
    assert repr(connection_authority) == "PrivateWsConnectionAuthority(<redacted>)"

    async with connector.open(_endpoint(), attempts=attempts) as connection:
        receiver = connection.receiver()
        await connection.send(encode_enable_heartbeat())
        async with asyncio.timeout(4.0):
            await connection.send(auth.encode_fresh_auth())
            while True:
                raw = await receiver.recv()
                if type(raw) is not str:
                    _fail("Delta India testnet auth frame was not text")
                message = decode_ws_object(
                    raw,
                    max_bytes=_MAX_FRAME_BYTES,
                    stream_label="private",
                )
                if message.get("type") in {"heartbeat", "pong"}:
                    continue
                result = parse_private_ws_auth_response(message)
                raise_for_private_ws_auth(result)
                assert result.success is True
                break

        await connection.send(plan.encoded_subscribe)
        async with asyncio.timeout(10.0):
            while True:
                raw = await receiver.recv()
                if type(raw) is not str:
                    _fail("Delta India testnet subscription frame was not text")
                message = decode_ws_object(
                    raw,
                    max_bytes=_MAX_FRAME_BYTES,
                    stream_label="private",
                )
                if message.get("type") in {"heartbeat", "pong"}:
                    continue
                plan.parse_ack(message)
                break
