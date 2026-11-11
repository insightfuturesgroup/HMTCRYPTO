"""Read-only Delta credential/IP verification and external-user binding."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from hmt_crypto.exchange.delta.signer import DeltaSigner

_ORIGINS = {
    "prod": "https://api.india.delta.exchange",
    "testnet": "https://cdn-ind.testnet.deltaex.org",
}
_PATH = "/v2/wallet/balances"


class DeltaVerificationRejected(Exception):
    pass


class DeltaVerificationUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DeltaIdentityEvidence:
    external_user_id: str


async def verify_delta_credentials(
    *,
    environment: str,
    api_key: str,
    api_secret: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DeltaIdentityEvidence:
    """Perform one signed read; never probes trading with a mutation."""
    origin = _ORIGINS.get(environment)
    if origin is None:
        raise DeltaVerificationRejected()
    signer = DeltaSigner(
        api_key=SecretStr(api_key),
        api_secret=SecretStr(api_secret),
        user_agent="hmt-crypto-account-verifier/1.0",
        clock=lambda: int(time.time()),
    )
    headers = signer.sign_now("GET", _PATH).as_headers()
    headers["Accept"] = "application/json"
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5, read=10, write=5, pool=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            response = await client.get(f"{origin}{_PATH}", headers=headers)
    except httpx.HTTPError:
        raise DeltaVerificationUnavailable() from None
    finally:
        headers.clear()
    if response.status_code in {401, 403}:
        raise DeltaVerificationRejected()
    if response.status_code != 200:
        raise DeltaVerificationUnavailable()
    try:
        payload = response.json()
    except ValueError:
        raise DeltaVerificationUnavailable() from None
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise DeltaVerificationRejected()
    balances = payload.get("result")
    if not isinstance(balances, list) or not balances:
        raise DeltaVerificationRejected()
    user_ids: set[int] = set()
    for balance in balances:
        if not isinstance(balance, dict):
            raise DeltaVerificationRejected()
        user_id = balance.get("user_id")
        if type(user_id) is not int or user_id <= 0:
            raise DeltaVerificationRejected()
        user_ids.add(user_id)
    if len(user_ids) != 1:
        raise DeltaVerificationRejected()
    return DeltaIdentityEvidence(external_user_id=str(user_ids.pop()))


__all__ = [
    "DeltaIdentityEvidence",
    "DeltaVerificationRejected",
    "DeltaVerificationUnavailable",
    "verify_delta_credentials",
]
