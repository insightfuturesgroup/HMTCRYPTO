"""Concrete bounded alert transports; provider diagnostics never escape."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from hmt_crypto.observability.alerts import AlertDeliveryError, AlertEvent

_BOT_TOKEN = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{10,128}\Z")
_CHAT_ID = re.compile(r"-?[0-9]{1,32}\Z")
_IDEMPOTENCY_KEY = re.compile(r"hmt-alert:[0-9A-Za-z:-]{1,128}\Z")


def _key(value: object) -> str:
    if type(value) is not str or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise AlertDeliveryError() from None
    return value


class TelegramAlertTransport:
    """Send closed operational text through Telegram's HTTPS Bot API."""

    __slots__ = ("_chat_id", "_client", "_token")

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        bot_token: SecretStr,
        chat_id: str,
    ) -> None:
        try:
            token = bot_token.get_secret_value()
        except Exception:
            raise ValueError("invalid Telegram alert configuration") from None
        if (
            type(client) is not httpx.AsyncClient
            or type(bot_token) is not SecretStr
            or _BOT_TOKEN.fullmatch(token) is None
            or type(chat_id) is not str
            or _CHAT_ID.fullmatch(chat_id) is None
        ):
            raise ValueError("invalid Telegram alert configuration") from None
        self._client = client
        self._token = SecretStr(token)
        self._chat_id = chat_id

    async def deliver(self, event: AlertEvent, *, idempotency_key: str) -> None:
        """Deliver one secret-free message or raise one sanitized failure."""
        if type(event) is not AlertEvent:
            raise AlertDeliveryError() from None
        key = _key(idempotency_key)
        try:
            token = self._token.get_secret_value()
            response = await self._client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                headers={"Idempotency-Key": key},
                json={
                    "chat_id": self._chat_id,
                    "disable_web_page_preview": True,
                    "text": event.safe_text(),
                },
            )
            if response.status_code != 200:
                raise RuntimeError
            payload = response.json()
            if type(payload) is not dict or payload.get("ok") is not True:
                raise RuntimeError
        except Exception:
            raise AlertDeliveryError() from None


class HttpsJsonWebhookTransport:
    """Send a minimal allowlisted JSON event to one fixed public HTTPS host."""

    __slots__ = ("_bearer_token", "_client", "_url")

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        url: str,
        bearer_token: SecretStr | None = None,
    ) -> None:
        try:
            parsed = urlsplit(url)
            bearer = None if bearer_token is None else bearer_token.get_secret_value()
        except Exception:
            raise ValueError("invalid alert webhook configuration") from None
        if (
            type(client) is not httpx.AsyncClient
            or type(url) is not str
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            or bearer_token is not None
            and (type(bearer_token) is not SecretStr or not bearer or len(bearer) > 4096)
        ):
            raise ValueError("invalid alert webhook configuration") from None
        self._client = client
        self._url = url
        self._bearer_token = None if bearer is None else SecretStr(bearer)

    async def deliver(self, event: AlertEvent, *, idempotency_key: str) -> None:
        """Deliver one allowlisted event object or raise one sanitized failure."""
        if type(event) is not AlertEvent:
            raise AlertDeliveryError() from None
        key = _key(idempotency_key)
        headers = {"Idempotency-Key": key}
        if self._bearer_token is not None:
            headers["Authorization"] = f"Bearer {self._bearer_token.get_secret_value()}"
        try:
            response = await self._client.post(
                self._url,
                headers=headers,
                json={
                    "event_id": str(event.event_id),
                    "kind": event.kind.value,
                    "message": event.safe_text(),
                    "severity": event.severity.value,
                },
            )
            if not 200 <= response.status_code <= 299:
                raise RuntimeError
        except Exception:
            raise AlertDeliveryError() from None


__all__ = ["HttpsJsonWebhookTransport", "TelegramAlertTransport"]
