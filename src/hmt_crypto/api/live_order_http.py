"""Single-attempt signed Delta India Live order transport, not worker-wired."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import httpx

from hmt_crypto.api.live_order_transport import PreparedLiveOrder
from hmt_crypto.exchange.delta.signer import DeltaSigner

_ORDER_URL = "https://api.india.delta.exchange/v2/orders"
_ORDER_PATH = "/v2/orders"
_MAX_RESPONSE_BYTES = 1_048_576
_PERMIT_ISSUER = object()


class LiveOrderTransportUncertain(Exception):
    """Transport entry may have occurred and requires client-ID recovery."""


class _LiveDispatchPermit:
    submission_id: UUID
    body_sha256: str
    sending_evidence_sha256: str
    expires_at: datetime
    __slots__ = ("body_sha256", "expires_at", "sending_evidence_sha256", "submission_id")

    def __init__(
        self,
        *,
        submission_id: UUID,
        body_sha256: str,
        sending_evidence_sha256: str,
        expires_at: datetime,
        issuer: object,
    ) -> None:
        if issuer is not _PERMIT_ISSUER:
            raise TypeError("live dispatch permit requires durable issuer")
        object.__setattr__(self, "submission_id", submission_id)
        object.__setattr__(self, "body_sha256", body_sha256)
        object.__setattr__(self, "sending_evidence_sha256", sending_evidence_sha256)
        object.__setattr__(self, "expires_at", expires_at)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("live dispatch permits are immutable")

    def __repr__(self) -> str:
        return "_LiveDispatchPermit(evidence=[REDACTED])"


def _issue_live_dispatch_permit(
    *,
    submission_id: UUID,
    body_sha256: str,
    sending_evidence_sha256: str,
    expires_at: datetime,
) -> _LiveDispatchPermit:
    """Internal bridge for a transaction that already persisted SENDING evidence."""
    if (
        type(submission_id) is not UUID
        or submission_id.int == 0
        or any(
            type(value) is not str
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in (body_sha256, sending_evidence_sha256)
        )
        or not isinstance(expires_at, datetime)
        or expires_at.tzinfo is None
        or expires_at.utcoffset() is None
    ):
        raise ValueError("invalid durable live dispatch evidence")
    return _LiveDispatchPermit(
        submission_id=submission_id,
        body_sha256=body_sha256,
        sending_evidence_sha256=sending_evidence_sha256,
        expires_at=expires_at.astimezone(UTC),
        issuer=_PERMIT_ISSUER,
    )


@dataclass(frozen=True, slots=True)
class LiveOrderHttpResponse:
    status_code: int
    body: bytes


class DeltaIndiaLiveOrderHttpAdapter:
    """Exact one-shot POST adapter; it contains no retry loop or alternate origin."""

    def __init__(
        self, *, signer: DeltaSigner, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if type(signer) is not DeltaSigner:
            raise TypeError("live order adapter requires DeltaSigner")
        self._signer = signer
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def send_once(
        self, prepared: PreparedLiveOrder, permit: _LiveDispatchPermit
    ) -> LiveOrderHttpResponse:
        if type(prepared) is not PreparedLiveOrder or type(permit) is not _LiveDispatchPermit:
            raise LiveOrderTransportUncertain from None
        now = datetime.now(UTC)
        if (
            permit.expires_at <= now
            or prepared.body_sha256 != permit.body_sha256
            or sha256(prepared.body).hexdigest() != permit.body_sha256
        ):
            raise LiveOrderTransportUncertain from None
        signed = self._signer.sign_now("POST", _ORDER_PATH, payload=prepared.body)
        headers = signed.as_headers()
        headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        request = self._client.build_request(
            "POST", _ORDER_URL, headers=headers, content=prepared.body
        )
        try:
            response = await self._client.send(request, stream=True)
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise LiveOrderTransportUncertain
                chunks.append(chunk)
            return LiveOrderHttpResponse(response.status_code, b"".join(chunks))
        except LiveOrderTransportUncertain:
            raise
        except Exception:
            raise LiveOrderTransportUncertain from None
        finally:
            if "response" in locals():
                await response.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = [
    "DeltaIndiaLiveOrderHttpAdapter",
    "LiveOrderHttpResponse",
    "LiveOrderTransportUncertain",
]
