"""Strict Delta India client-order-ID lookup for uncertain Live submissions."""

from __future__ import annotations

import json
import re

import httpx

from hmt_crypto.api.live_order_recovery import RecoveryObservation
from hmt_crypto.exchange.delta.private_read_parsers import _parse_order_lookup_payload
from hmt_crypto.exchange.delta.private_routes import PrivateRouteId
from hmt_crypto.exchange.delta.signer import DeltaSigner

_ORIGIN = "https://api.india.delta.exchange"
_PATH_PREFIX = "/v2/orders/client_order_id/"
_CLIENT_ORDER_ID = re.compile(r"[A-Za-z0-9_-]{1,32}\Z", re.ASCII)
_MAX_RESPONSE_BYTES = 524_288


class LiveOrderRecoveryUnavailable(Exception):
    """No authoritative recovery observation could be obtained."""


class DeltaIndiaLiveOrderRecoveryAdapter:
    def __init__(
        self, *, signer: DeltaSigner, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if type(signer) is not DeltaSigner:
            raise TypeError("live recovery adapter requires DeltaSigner")
        self._signer = signer
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def lookup(self, *, client_order_id: str, expected_user_id: str) -> RecoveryObservation:
        if (
            type(client_order_id) is not str
            or _CLIENT_ORDER_ID.fullmatch(client_order_id) is None
            or type(expected_user_id) is not str
            or not expected_user_id.isdigit()
            or expected_user_id.startswith("0")
            or len(expected_user_id) > 19
        ):
            raise LiveOrderRecoveryUnavailable from None
        path = f"{_PATH_PREFIX}{client_order_id}"
        headers = self._signer.sign_now("GET", path).as_headers()
        headers["Accept"] = "application/json"
        try:
            response = await self._client.get(f"{_ORIGIN}{path}", headers=headers)
            body = await response.aread()
        except Exception:
            raise LiveOrderRecoveryUnavailable from None
        finally:
            headers.clear()
        if len(body) > _MAX_RESPONSE_BYTES:
            raise LiveOrderRecoveryUnavailable
        if response.status_code == 404:
            return RecoveryObservation("NOT_FOUND", None)
        if response.status_code != 200:
            raise LiveOrderRecoveryUnavailable
        try:
            envelope = json.loads(body)
            if not isinstance(envelope, dict) or envelope.get("success") is not True:
                raise ValueError
            order = _parse_order_lookup_payload(
                envelope.get("result"),
                endpoint_id=PrivateRouteId.ORDER_BY_CLIENT_ID,
                expected_user_id=expected_user_id,
                expected_client_order_id=client_order_id,
            )
            return RecoveryObservation("FOUND", order.id)
        except Exception:
            raise LiveOrderRecoveryUnavailable from None

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = ["DeltaIndiaLiveOrderRecoveryAdapter", "LiveOrderRecoveryUnavailable"]
