"""Trusted-server Supabase Auth gateway with no database/Data API access."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx


class AuthenticationRejected(Exception):
    pass


class AuthenticationUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AuthenticatedIdentity:
    auth_user_id: UUID
    email: str


@dataclass(frozen=True, slots=True)
class AuthTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    identity: AuthenticatedIdentity


def _identity(value: object) -> AuthenticatedIdentity:
    if not isinstance(value, dict):
        raise AuthenticationRejected()
    raw_id = value.get("id")
    email = value.get("email")
    if type(raw_id) is not str or type(email) is not str or not email or len(email) > 320:
        raise AuthenticationRejected()
    try:
        user_id = UUID(raw_id)
    except ValueError:
        raise AuthenticationRejected() from None
    return AuthenticatedIdentity(user_id, email.casefold())


class SupabaseAuthGateway:
    def __init__(self, *, base_url: str, publishable_key: str, secret_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._publishable_key = publishable_key
        self._secret_key = secret_key
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10), follow_redirects=False)

    async def _request(
        self, method: str, path: str, *, privileged: bool = False, **kwargs: Any
    ) -> httpx.Response:
        key = self._secret_key if privileged else self._publishable_key
        headers = dict(kwargs.pop("headers", {}))
        headers["apikey"] = key
        if privileged:
            headers["Authorization"] = f"Bearer {key}"
        try:
            return await self._client.request(
                method, f"{self._base_url}/auth/v1{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError:
            raise AuthenticationUnavailable() from None

    async def login(self, email: str, password: str) -> AuthTokens:
        response = await self._request(
            "POST", "/token?grant_type=password", json={"email": email, "password": password}
        )
        if response.status_code != 200:
            raise AuthenticationRejected()
        try:
            body = response.json()
            access = body["access_token"]
            refresh = body["refresh_token"]
            expires = body["expires_in"]
            identity = _identity(body["user"])
        except (KeyError, TypeError, ValueError):
            raise AuthenticationUnavailable() from None
        if (
            type(access) is not str
            or type(refresh) is not str
            or type(expires) is not int
            or not 60 <= expires <= 86_400
        ):
            raise AuthenticationUnavailable()
        return AuthTokens(access, refresh, expires, identity)

    async def user(self, access_token: str) -> AuthenticatedIdentity:
        response = await self._request(
            "GET", "/user", headers={"Authorization": f"Bearer {access_token}"}
        )
        if response.status_code != 200:
            raise AuthenticationRejected()
        return _identity(response.json())

    async def refresh(self, refresh_token: str) -> AuthTokens:
        response = await self._request(
            "POST", "/token?grant_type=refresh_token", json={"refresh_token": refresh_token}
        )
        if response.status_code != 200:
            raise AuthenticationRejected()
        body = response.json()
        try:
            return AuthTokens(
                body["access_token"],
                body["refresh_token"],
                body["expires_in"],
                _identity(body["user"]),
            )
        except (KeyError, TypeError, ValueError):
            raise AuthenticationUnavailable() from None

    async def logout(self, access_token: str) -> None:
        response = await self._request(
            "POST", "/logout", headers={"Authorization": f"Bearer {access_token}"}
        )
        if response.status_code not in {200, 204, 401}:
            raise AuthenticationUnavailable()

    async def invite(self, email: str, *, redirect_to: str) -> AuthenticatedIdentity:
        response = await self._request(
            "POST",
            "/invite",
            privileged=True,
            params={"redirect_to": redirect_to},
            json={"email": email},
        )
        if response.status_code not in {200, 201}:
            raise AuthenticationRejected()
        return _identity(response.json())

    async def revoke_user_sessions(self, auth_user_id: UUID) -> None:
        response = await self._request(
            "DELETE", f"/admin/users/{auth_user_id}/factors", privileged=True
        )
        # Factor deletion is deliberately not used as session revocation. Supabase's
        # admin logout endpoint is not universally available, so disabled users are
        # denied by the local profile on every request; access tokens remain harmless.
        if response.status_code not in {200, 404, 405}:
            return

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = [
    "AuthenticatedIdentity",
    "AuthenticationRejected",
    "AuthenticationUnavailable",
    "AuthTokens",
    "SupabaseAuthGateway",
]
