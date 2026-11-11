"""Invitation-only multi-user API composition for Phase 1."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, desc, func, select
from sqlalchemy.exc import IntegrityError

from hmt_crypto.api.delta_reconciliation import (
    ReconciliationReadError,
    read_private_reconciliation,
)
from hmt_crypto.api.delta_verification import (
    DeltaIdentityEvidence,
    DeltaVerificationRejected,
    DeltaVerificationUnavailable,
    verify_delta_credentials,
)
from hmt_crypto.api.platform_auth import (
    AuthenticationRejected,
    AuthenticationUnavailable,
    SupabaseAuthGateway,
)
from hmt_crypto.api.secret_store import AwsDeltaSecretStore
from hmt_crypto.api.settings import ControlApiSettings
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import (
    Account,
    AccountAssignment,
    AccountLiveControl,
    CatalogueProduct,
    CatalogueSnapshot,
    CredentialSecretReference,
    CredentialVersion,
    GttOrder,
    MarketQuote,
    PlatformAuditEvent,
    PlatformUser,
    PrivateReconciliationSnapshot,
    WatchlistItem,
)

_ACCESS_COOKIE = "__Host-hmt_auth_access"
_REFRESH_COOKIE = "__Host-hmt_auth_refresh"
_CSRF_COOKIE = "__Host-hmt_auth_csrf"
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class LoginBody(_Body):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=1024)


class InviteBody(_Body):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(min_length=1, max_length=128)
    role: Literal["admin", "trader", "viewer"]


class UpdateUserBody(_Body):
    role: Literal["admin", "trader", "viewer"]
    status: Literal["active", "disabled"]


class CreateAccountBody(_Body):
    profile_name: str = Field(min_length=1, max_length=128)
    environment: Literal["prod"]
    owner_user_id: UUID


class AssignmentBody(_Body):
    user_id: UUID
    permission: Literal["view", "trade", "manage"]


class CredentialBody(_Body):
    api_key: str = Field(min_length=8, max_length=512)
    api_secret: str = Field(min_length=16, max_length=1024)


class WatchlistBody(_Body):
    symbol: str = Field(min_length=1, max_length=128)
    product_id: int = Field(gt=0)


class GttBody(_Body):
    account_id: UUID
    product_id: int = Field(gt=0)
    symbol: str = Field(min_length=1, max_length=128)
    side: Literal["buy", "sell"]
    contracts: int = Field(gt=0, lt=2**63)
    trigger_kind: Literal["stop_loss", "target", "trailing_stop"]
    trigger_price: str | None = Field(default=None, max_length=64)
    trail_offset: str | None = Field(default=None, max_length=64)
    limit_price: str | None = Field(default=None, max_length=64)


@dataclass(frozen=True, slots=True)
class Principal:
    profile_id: UUID
    auth_user_id: UUID
    email: str
    full_name: str
    role: str
    last_login_at: datetime | None


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _request_id(request: Request) -> str:
    raw = request.headers.get("idempotency-key")
    try:
        value = UUID(raw) if raw is not None else None
    except ValueError:
        value = None
    if value is None or value.int == 0:
        raise HTTPException(status_code=400, detail="idempotency_key_required")
    return str(value)


def _email(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized.count("@") != 1 or any(character.isspace() for character in normalized):
        raise HTTPException(status_code=400, detail="invalid_request")
    return normalized


def _user_view(row: PlatformUser) -> dict[str, object]:
    return {
        "id": str(row.id),
        "email": row.email,
        "fullName": row.full_name,
        "role": row.role,
        "status": row.status,
        "createdAt": _utc(row.created_at),
        "lastLoginAt": None if row.last_login_at is None else _utc(row.last_login_at),
    }


def install_platform_routes(
    app: FastAPI,
    *,
    settings: ControlApiSettings,
    database: Database,
) -> SupabaseAuthGateway:
    """Install multi-user routes and return the owned Auth gateway for shutdown."""

    if not settings.multi_user_enabled:
        raise ValueError("multi-user routes require enabled settings")
    assert settings.supabase_url is not None
    assert settings.supabase_publishable_key is not None
    assert settings.supabase_secret_key is not None
    assert settings.aws_region is not None
    auth = SupabaseAuthGateway(
        base_url=settings.supabase_url,
        publishable_key=settings.supabase_publishable_key.get_secret_value(),
        secret_key=settings.supabase_secret_key.get_secret_value(),
    )
    secret_store = AwsDeltaSecretStore(
        region=settings.aws_region,
        prefix=settings.delta_secret_prefix,
    )

    def require_origin(request: Request) -> None:
        if request.headers.get("origin") != settings.allowed_origin:
            raise HTTPException(status_code=403, detail="origin_rejected")

    def require_csrf(request: Request) -> None:
        require_origin(request)
        cookie = request.cookies.get(_CSRF_COOKIE)
        header = request.headers.get("x-csrf-token")
        if cookie is None or header is None or not hmac.compare_digest(cookie, header):
            raise HTTPException(status_code=403, detail="csrf_rejected")

    async def principal(request: Request, *, mutation: bool = False) -> Principal:
        if mutation:
            require_csrf(request)
        access = request.cookies.get(_ACCESS_COOKIE)
        if access is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        try:
            identity = await auth.user(access)
        except AuthenticationRejected:
            raise HTTPException(status_code=401, detail="authentication_required") from None
        except AuthenticationUnavailable:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from None
        async with database.transaction() as session:
            row = (
                await session.execute(
                    select(PlatformUser).where(PlatformUser.auth_user_id == identity.auth_user_id)
                )
            ).scalar_one_or_none()
            if row is None or row.status != "active" or row.email.casefold() != identity.email:
                raise HTTPException(status_code=403, detail="account_disabled_or_unassigned")
            return Principal(
                row.id,
                row.auth_user_id,
                row.email,
                row.full_name,
                row.role,
                row.last_login_at,
            )

    async def admin(request: Request, *, mutation: bool = False) -> Principal:
        actor = await principal(request, mutation=mutation)
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail="admin_required")
        return actor

    async def append_audit(
        session: object,
        *,
        actor: Principal | None,
        action: str,
        target_type: str,
        target_id: str,
        outcome: str,
        request_id: str,
        details: dict[str, object] | None = None,
    ) -> None:
        session.add(  # type: ignore[attr-defined]
            PlatformAuditEvent(
                id=uuid4(),
                actor_user_id=None if actor is None else actor.profile_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                outcome=outcome,
                request_id=request_id,
                details=json.dumps(details or {}, separators=(",", ":"), sort_keys=True),
                occurred_at=datetime.now(UTC),
            )
        )

    async def account_permission(session: object, actor: Principal, account_id: UUID) -> str:
        if actor.role == "admin":
            return "manage"
        permission = await session.scalar(  # type: ignore[attr-defined]
            select(AccountAssignment.permission).where(
                AccountAssignment.user_id == actor.profile_id,
                AccountAssignment.account_id == account_id,
            )
        )
        if permission is None:
            raise HTTPException(status_code=404, detail="account_not_found")
        return str(permission)

    def set_auth_cookies(
        response: JSONResponse, access: str, refresh: str, csrf: str, age: int
    ) -> None:
        for name, value, httponly in (
            (_ACCESS_COOKIE, access, True),
            (_REFRESH_COOKIE, refresh, True),
            (_CSRF_COOKIE, csrf, False),
        ):
            response.set_cookie(
                name,
                value,
                httponly=httponly,
                secure=True,
                samesite="strict",
                path="/",
                max_age=age if name == _ACCESS_COOKIE else settings.session_absolute_seconds,
            )

    def clear_auth_cookies(response: JSONResponse) -> None:
        for name in (_ACCESS_COOKIE, _REFRESH_COOKIE, _CSRF_COOKIE):
            response.delete_cookie(name, path="/", secure=True, samesite="strict")

    @app.post("/api/v1/auth/login")
    async def platform_login(body: LoginBody, request: Request) -> JSONResponse:
        require_origin(request)
        try:
            tokens = await auth.login(_email(body.email), body.password)
        except AuthenticationRejected:
            raise HTTPException(status_code=401, detail="authentication_failed") from None
        except AuthenticationUnavailable:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from None
        now = datetime.now(UTC)
        async with database.transaction() as session:
            row = (
                await session.execute(
                    select(PlatformUser).where(
                        PlatformUser.auth_user_id == tokens.identity.auth_user_id
                    )
                )
            ).scalar_one_or_none()
            if (
                row is None
                or row.status == "disabled"
                or row.email.casefold() != tokens.identity.email
            ):
                raise HTTPException(status_code=403, detail="account_disabled_or_unassigned")
            row.status = "active"
            row.last_login_at = now
            row.updated_at = now
            actor = Principal(row.id, row.auth_user_id, row.email, row.full_name, row.role, now)
            await append_audit(
                session,
                actor=actor,
                action="AUTH_LOGIN",
                target_type="user",
                target_id=str(row.id),
                outcome="SUCCEEDED",
                request_id=str(uuid4()),
            )
        csrf = secrets.token_urlsafe(32)
        response = JSONResponse(
            {
                "authenticated": True,
                "csrfToken": csrf,
                "user": {
                    "id": str(actor.profile_id),
                    "email": actor.email,
                    "fullName": actor.full_name,
                    "role": actor.role,
                },
            }
        )
        set_auth_cookies(
            response, tokens.access_token, tokens.refresh_token, csrf, tokens.expires_in
        )
        return response

    @app.get("/api/v1/auth/session")
    async def platform_session(request: Request) -> JSONResponse:
        try:
            actor = await principal(request)
        except HTTPException as error:
            if error.status_code not in {401, 403}:
                raise
            response = JSONResponse({"authenticated": False, "csrfToken": None, "user": None})
            clear_auth_cookies(response)
            return response
        return JSONResponse(
            {
                "authenticated": True,
                "csrfToken": request.cookies.get(_CSRF_COOKIE),
                "user": {
                    "id": str(actor.profile_id),
                    "email": actor.email,
                    "fullName": actor.full_name,
                    "role": actor.role,
                },
            }
        )

    @app.delete("/api/v1/auth/session")
    async def platform_logout(request: Request) -> JSONResponse:
        actor = await principal(request, mutation=True)
        access = request.cookies.get(_ACCESS_COOKIE)
        if access is not None:
            await auth.logout(access)
        async with database.transaction() as session:
            await append_audit(
                session,
                actor=actor,
                action="AUTH_LOGOUT",
                target_type="user",
                target_id=str(actor.profile_id),
                outcome="SUCCEEDED",
                request_id=str(uuid4()),
            )
        response = JSONResponse({"authenticated": False})
        clear_auth_cookies(response)
        return response

    @app.get("/api/v1/admin/users")
    async def list_users(request: Request) -> dict[str, object]:
        await admin(request)
        async with database.transaction() as session:
            rows = (
                await session.execute(select(PlatformUser).order_by(PlatformUser.email))
            ).scalars()
            return {"users": [_user_view(row) for row in rows]}

    @app.post("/api/v1/admin/users/invite")
    async def invite_user(body: InviteBody, request: Request) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        email = _email(body.email)
        try:
            identity = await auth.invite(email, redirect_to=settings.allowed_origin)
        except AuthenticationRejected:
            raise HTTPException(status_code=409, detail="invite_rejected") from None
        now = datetime.now(UTC)
        try:
            async with database.transaction() as session:
                row = PlatformUser(
                    id=uuid4(),
                    auth_user_id=identity.auth_user_id,
                    email=email,
                    full_name=body.full_name.strip(),
                    role=body.role,
                    status="invited",
                    created_at=now,
                    updated_at=now,
                    last_login_at=None,
                )
                session.add(row)
                await session.flush()
                await append_audit(
                    session,
                    actor=actor,
                    action="USER_INVITED",
                    target_type="user",
                    target_id=str(row.id),
                    outcome="SUCCEEDED",
                    request_id=request_id,
                    details={"role": body.role},
                )
                result = _user_view(row)
        except IntegrityError:
            raise HTTPException(status_code=409, detail="user_already_exists") from None
        return result

    @app.patch("/api/v1/admin/users/{user_id}")
    async def update_user(
        user_id: UUID, body: UpdateUserBody, request: Request
    ) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        if user_id == actor.profile_id and (body.status == "disabled" or body.role != "admin"):
            raise HTTPException(status_code=409, detail="cannot_remove_own_admin_access")
        async with database.transaction() as session:
            row = await session.get(PlatformUser, user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="user_not_found")
            row.role, row.status, row.updated_at = body.role, body.status, datetime.now(UTC)
            await append_audit(
                session,
                actor=actor,
                action="USER_UPDATED",
                target_type="user",
                target_id=str(row.id),
                outcome="SUCCEEDED",
                request_id=request_id,
                details={"role": body.role, "status": body.status},
            )
            return _user_view(row)

    @app.get("/api/v1/accounts")
    async def list_accounts(request: Request) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            query = (
                select(Account, AccountAssignment.permission, AccountLiveControl)
                .join(AccountAssignment, AccountAssignment.account_id == Account.id)
                .outerjoin(AccountLiveControl, AccountLiveControl.account_id == Account.id)
            )
            if actor.role != "admin":
                query = query.where(AccountAssignment.user_id == actor.profile_id)
            rows = (await session.execute(query.order_by(Account.profile_name))).all()
            return {
                "accounts": [
                    {
                        "id": str(account.id),
                        "profileName": account.profile_name,
                        "region": account.region,
                        "environment": account.environment,
                        "mode": account.mode,
                        "permission": permission,
                        "identityVerified": account.identity_verified_at is not None,
                        "credentialConfigured": account.current_credential_version is not None,
                        "credentialState": "unconfigured"
                        if control is None
                        else control.credential_state,
                        "reconciliationState": "unknown"
                        if control is None
                        else control.reconciliation_state,
                        "entriesEnabled": False if control is None else control.entries_enabled,
                        "exitsEnabled": False if control is None else control.exits_enabled,
                    }
                    for account, permission, control in rows
                ]
            }

    @app.post("/api/v1/accounts")
    async def create_account(body: CreateAccountBody, request: Request) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        if _PROFILE.fullmatch(body.profile_name) is None:
            raise HTTPException(status_code=400, detail="invalid_profile_name")
        now, account_id = datetime.now(UTC), uuid4()
        try:
            async with database.transaction() as session:
                owner = await session.get(PlatformUser, body.owner_user_id)
                if owner is None or owner.status == "disabled":
                    raise HTTPException(status_code=404, detail="owner_not_found")
                account = Account(
                    id=account_id,
                    region="india",
                    environment=body.environment,
                    mode="LIVE",
                    profile_name=body.profile_name,
                    external_user_id=None,
                    external_account_id=None,
                    identity_verified_at=None,
                    current_credential_version=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(account)
                session.add(
                    AccountAssignment(
                        id=uuid4(),
                        user_id=owner.id,
                        account_id=account.id,
                        permission="manage",
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    AccountLiveControl(
                        id=uuid4(),
                        account_id=account.id,
                        credential_state="unconfigured",
                        reconciliation_state="unknown",
                        entries_enabled=False,
                        exits_enabled=False,
                        verified_at=None,
                        reconciled_at=None,
                        last_error_code=None,
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await append_audit(
                    session,
                    actor=actor,
                    action="ACCOUNT_CREATED",
                    target_type="account",
                    target_id=str(account.id),
                    outcome="SUCCEEDED",
                    request_id=request_id,
                    details={"environment": body.environment, "mode": "LIVE"},
                )
        except IntegrityError:
            raise HTTPException(status_code=409, detail="account_already_exists") from None
        return {"id": str(account_id), "status": "credential_required", "mode": "LIVE"}

    @app.put("/api/v1/accounts/{account_id}/assignments")
    async def assign_account(
        account_id: UUID, body: AssignmentBody, request: Request
    ) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            if (
                await session.get(Account, account_id) is None
                or await session.get(PlatformUser, body.user_id) is None
            ):
                raise HTTPException(status_code=404, detail="assignment_target_not_found")
            row = (
                await session.execute(
                    select(AccountAssignment).where(
                        AccountAssignment.account_id == account_id,
                        AccountAssignment.user_id == body.user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = AccountAssignment(
                    id=uuid4(),
                    user_id=body.user_id,
                    account_id=account_id,
                    permission=body.permission,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.permission, row.updated_at = body.permission, now
            await append_audit(
                session,
                actor=actor,
                action="ACCOUNT_ASSIGNED",
                target_type="account",
                target_id=str(account_id),
                outcome="SUCCEEDED",
                request_id=request_id,
                details={"userId": str(body.user_id), "permission": body.permission},
            )
        return {
            "accountId": str(account_id),
            "userId": str(body.user_id),
            "permission": body.permission,
        }

    @app.post("/api/v1/accounts/{account_id}/credentials")
    async def replace_credentials(
        account_id: UUID, body: CredentialBody, request: Request
    ) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        if actor.last_login_at is None or datetime.now(UTC) - actor.last_login_at > timedelta(
            minutes=15
        ):
            raise HTTPException(status_code=403, detail="fresh_authentication_required")
        async with database.transaction() as session:
            account = await session.get(Account, account_id)
            if account is None:
                raise HTTPException(status_code=404, detail="account_not_found")
            current = await session.scalar(
                select(func.max(CredentialSecretReference.credential_version)).where(
                    CredentialSecretReference.account_id == account_id
                )
            )
            version = int(current or 0) + 1
        stored = await secret_store.put(
            account_id=account_id,
            version=version,
            api_key=body.api_key,
            api_secret=body.api_secret,
        )
        fingerprint = hashlib.sha256(body.api_key.encode("utf-8")).hexdigest()[:12]
        try:
            async with database.transaction() as session:
                account = await session.get(Account, account_id)
                if account is None:
                    raise HTTPException(status_code=404, detail="account_not_found")
                now = datetime.now(UTC)
                session.add(
                    CredentialVersion(
                        id=uuid4(),
                        account_id=account.id,
                        region=account.region,
                        environment=account.environment,
                        mode=account.mode,
                        credential_version=version,
                        credential_slot=f"DELTA_{account.region.upper()}_{account.environment.upper()}",
                        fingerprint_hint=fingerprint,
                        observed_external_user_id=None,
                        observed_external_account_id=None,
                        binding_ref=None,
                        probe_ref=None,
                        reconciliation_ref=None,
                        recorded_at=now,
                    )
                )
                session.add(
                    CredentialSecretReference(
                        id=uuid4(),
                        account_id=account.id,
                        credential_version=version,
                        secret_arn=stored.arn,
                        fingerprint_hint=fingerprint,
                        status="pending_verification",
                        created_by_user_id=actor.profile_id,
                        created_at=now,
                        revoked_at=None,
                    )
                )
                account.current_credential_version = version
                account.updated_at = now
                control = (
                    await session.execute(
                        select(AccountLiveControl).where(
                            AccountLiveControl.account_id == account.id
                        )
                    )
                ).scalar_one()
                control.credential_state = "pending"
                control.reconciliation_state = "unknown"
                control.entries_enabled = False
                control.exits_enabled = False
                control.verified_at = None
                control.reconciled_at = None
                control.last_error_code = None
                control.updated_at = now
                await append_audit(
                    session,
                    actor=actor,
                    action="CREDENTIAL_REPLACED",
                    target_type="account",
                    target_id=str(account.id),
                    outcome="SUCCEEDED",
                    request_id=request_id,
                    details={"version": version, "status": "pending_verification"},
                )
        except BaseException:
            await secret_store.quarantine(stored.arn)
            raise
        return {
            "accountId": str(account_id),
            "credentialVersion": version,
            "fingerprintHint": fingerprint,
            "status": "pending_verification",
        }

    @app.post("/api/v1/accounts/{account_id}/verify")
    async def verify_account(account_id: UUID, request: Request) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        if actor.last_login_at is None or datetime.now(UTC) - actor.last_login_at > timedelta(
            minutes=15
        ):
            raise HTTPException(status_code=403, detail="fresh_authentication_required")
        async with database.transaction() as session:
            account = await session.get(Account, account_id)
            if account is None or account.current_credential_version is None:
                raise HTTPException(status_code=409, detail="credentials_not_configured")
            reference = (
                await session.execute(
                    select(CredentialSecretReference).where(
                        CredentialSecretReference.account_id == account.id,
                        CredentialSecretReference.credential_version
                        == account.current_credential_version,
                    )
                )
            ).scalar_one_or_none()
            if reference is None or reference.status == "revoked":
                raise HTTPException(status_code=409, detail="credentials_not_configured")
            secret_arn = reference.secret_arn
            environment = account.environment
            credential_version = account.current_credential_version
        failure_code: str | None
        evidence: DeltaIdentityEvidence | None = None
        try:
            api_key, api_secret = await secret_store.read(secret_arn)
            try:
                evidence = await verify_delta_credentials(
                    environment=environment,
                    api_key=api_key,
                    api_secret=api_secret,
                )
            finally:
                api_key = ""
                api_secret = ""
        except DeltaVerificationRejected:
            failure_code = "credential_or_ip_rejected"
        except (DeltaVerificationUnavailable, RuntimeError):
            failure_code = "verification_unavailable"
        else:
            failure_code = None
        async with database.transaction() as session:
            account = await session.get(Account, account_id)
            if account is None or account.current_credential_version != credential_version:
                raise HTTPException(status_code=409, detail="credential_revision_changed")
            reference = (
                await session.execute(
                    select(CredentialSecretReference).where(
                        CredentialSecretReference.account_id == account.id,
                        CredentialSecretReference.credential_version == credential_version,
                    )
                )
            ).scalar_one()
            credential = (
                await session.execute(
                    select(CredentialVersion).where(
                        CredentialVersion.account_id == account.id,
                        CredentialVersion.credential_version == credential_version,
                    )
                )
            ).scalar_one()
            control = (
                await session.execute(
                    select(AccountLiveControl).where(AccountLiveControl.account_id == account.id)
                )
            ).scalar_one()
            now = datetime.now(UTC)
            control.entries_enabled = False
            control.exits_enabled = False
            control.reconciliation_state = "unknown"
            control.reconciled_at = None
            control.updated_at = now
            if failure_code is not None:
                control.credential_state = "failed"
                control.last_error_code = failure_code
                reference.status = "pending_verification"
                await append_audit(
                    session,
                    actor=actor,
                    action="CREDENTIAL_VERIFICATION",
                    target_type="account",
                    target_id=str(account.id),
                    outcome="REJECTED",
                    request_id=request_id,
                    details={"errorCode": failure_code},
                )
                result = {
                    "accountId": str(account.id),
                    "verified": False,
                    "errorCode": failure_code,
                }
            else:
                if evidence is None:
                    raise HTTPException(status_code=503, detail="verification_unavailable")
                control.credential_state = "verified"
                control.verified_at = now
                control.last_error_code = None
                reference.status = "verified"
                credential.observed_external_user_id = evidence.external_user_id
                account.external_user_id = evidence.external_user_id
                account.identity_verified_at = now
                account.updated_at = now
                await append_audit(
                    session,
                    actor=actor,
                    action="CREDENTIAL_VERIFICATION",
                    target_type="account",
                    target_id=str(account.id),
                    outcome="SUCCEEDED",
                    request_id=request_id,
                    details={"credentialVersion": credential_version},
                )
                result = {"accountId": str(account.id), "verified": True, "errorCode": None}
        if failure_code == "verification_unavailable":
            raise HTTPException(status_code=503, detail=failure_code)
        if failure_code is not None:
            raise HTTPException(status_code=422, detail=failure_code)
        return result

    @app.post("/api/v1/accounts/{account_id}/reconcile")
    async def reconcile_account(account_id: UUID, request: Request) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = UUID(_request_id(request))
        if actor.last_login_at is None or datetime.now(UTC) - actor.last_login_at > timedelta(
            minutes=15
        ):
            raise HTTPException(status_code=403, detail="fresh_authentication_required")
        async with database.transaction() as session:
            previous = (
                await session.execute(
                    select(PrivateReconciliationSnapshot).where(
                        PrivateReconciliationSnapshot.account_id == account_id,
                        PrivateReconciliationSnapshot.request_id == request_id,
                    )
                )
            ).scalar_one_or_none()
            if previous is not None:
                return {
                    "accountId": str(account_id),
                    "outcome": previous.outcome,
                    "complete": previous.complete,
                    "foreignProductIds": json.loads(previous.foreign_product_ids),
                    "balanceCount": previous.balance_count,
                    "positionCount": previous.position_count,
                    "openOrderCount": previous.open_order_count,
                    "observedAt": _utc(previous.observed_at),
                }
            account = await session.get(Account, account_id)
            if account is None or account.current_credential_version is None:
                raise HTTPException(status_code=409, detail="credentials_not_configured")
            control = (
                await session.execute(
                    select(AccountLiveControl).where(AccountLiveControl.account_id == account.id)
                )
            ).scalar_one()
            if control.credential_state != "verified" or account.external_user_id is None:
                raise HTTPException(status_code=409, detail="credentials_not_verified")
            reference = (
                await session.execute(
                    select(CredentialSecretReference).where(
                        CredentialSecretReference.account_id == account.id,
                        CredentialSecretReference.credential_version
                        == account.current_credential_version,
                        CredentialSecretReference.status == "verified",
                    )
                )
            ).scalar_one_or_none()
            snapshot = await session.scalar(
                select(CatalogueSnapshot)
                .where(
                    CatalogueSnapshot.region == "india",
                    CatalogueSnapshot.environment == "prod",
                )
                .order_by(desc(CatalogueSnapshot.validated_at))
                .limit(1)
            )
            if reference is None or snapshot is None:
                raise HTTPException(status_code=409, detail="reconciliation_prerequisite_missing")
            allowed_ids = frozenset(
                int(value)
                for value in (
                    await session.scalars(
                        select(CatalogueProduct.product_id).where(
                            CatalogueProduct.snapshot_id == snapshot.id
                        )
                    )
                ).all()
            )
            if not allowed_ids:
                raise HTTPException(status_code=409, detail="catalogue_not_ready")
            credential_version = account.current_credential_version
            secret_arn = reference.secret_arn
            external_user_id = account.external_user_id
            control.reconciliation_state = "running"
            control.entries_enabled = False
            control.exits_enabled = False
            control.last_error_code = None
            control.updated_at = datetime.now(UTC)
        try:
            api_key, api_secret = await secret_store.read(secret_arn)
            try:
                evidence = await read_private_reconciliation(
                    api_key=api_key,
                    api_secret=api_secret,
                    expected_user_id=external_user_id,
                    allowed_product_ids=allowed_ids,
                )
            finally:
                api_key = ""
                api_secret = ""
        except (ReconciliationReadError, RuntimeError):
            async with database.transaction() as session:
                control = (
                    await session.execute(
                        select(AccountLiveControl).where(
                            AccountLiveControl.account_id == account_id
                        )
                    )
                ).scalar_one()
                control.reconciliation_state = "degraded"
                control.entries_enabled = False
                control.exits_enabled = False
                control.last_error_code = "reconciliation_unavailable"
                control.updated_at = datetime.now(UTC)
                await append_audit(
                    session,
                    actor=actor,
                    action="PRIVATE_RECONCILIATION",
                    target_type="account",
                    target_id=str(account_id),
                    outcome="UNAVAILABLE",
                    request_id=str(request_id),
                    details={"errorCode": "reconciliation_unavailable"},
                )
            raise HTTPException(status_code=503, detail="reconciliation_unavailable") from None
        outcome = "ready" if evidence.complete and not evidence.foreign_product_ids else "blocked"
        observed_at = datetime.now(UTC)
        async with database.transaction() as session:
            account = await session.get(Account, account_id)
            if account is None or account.current_credential_version != credential_version:
                raise HTTPException(status_code=409, detail="credential_revision_changed")
            control = (
                await session.execute(
                    select(AccountLiveControl).where(AccountLiveControl.account_id == account.id)
                )
            ).scalar_one()
            session.add(
                PrivateReconciliationSnapshot(
                    id=uuid4(),
                    account_id=account.id,
                    credential_version=credential_version,
                    request_id=request_id,
                    balance_count=evidence.balance_count,
                    position_count=evidence.position_count,
                    open_order_count=evidence.open_order_count,
                    foreign_product_ids=json.dumps(evidence.foreign_product_ids),
                    complete=evidence.complete,
                    content_sha256=evidence.content_sha256,
                    outcome=outcome,
                    observed_at=observed_at,
                )
            )
            control.reconciliation_state = outcome
            control.entries_enabled = False
            control.exits_enabled = False
            control.reconciled_at = observed_at
            control.last_error_code = None if outcome == "ready" else "foreign_or_incomplete_state"
            control.updated_at = observed_at
            await append_audit(
                session,
                actor=actor,
                action="PRIVATE_RECONCILIATION",
                target_type="account",
                target_id=str(account.id),
                outcome=outcome.upper(),
                request_id=str(request_id),
                details={
                    "credentialVersion": credential_version,
                    "complete": evidence.complete,
                    "foreignProductCount": len(evidence.foreign_product_ids),
                },
            )
        return {
            "accountId": str(account_id),
            "outcome": outcome,
            "complete": evidence.complete,
            "foreignProductIds": list(evidence.foreign_product_ids),
            "balanceCount": evidence.balance_count,
            "positionCount": evidence.position_count,
            "openOrderCount": evidence.open_order_count,
            "observedAt": _utc(observed_at),
        }

    @app.get("/api/v1/market/products")
    async def market_products(request: Request) -> dict[str, object]:
        await principal(request)
        async with database.transaction() as session:
            snapshot = (
                await session.execute(
                    select(CatalogueSnapshot)
                    .where(CatalogueSnapshot.region == "india")
                    .order_by(desc(CatalogueSnapshot.validated_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if snapshot is None:
                return {"snapshot": None, "products": []}
            rows = (
                await session.execute(
                    select(CatalogueProduct, MarketQuote)
                    .outerjoin(
                        MarketQuote,
                        and_(
                            MarketQuote.product_id == CatalogueProduct.product_id,
                            MarketQuote.region == snapshot.region,
                            MarketQuote.environment == snapshot.environment,
                        ),
                    )
                    .where(CatalogueProduct.snapshot_id == snapshot.id)
                    .order_by(CatalogueProduct.symbol)
                )
            ).all()
            products = []
            for product_row, quote in rows:
                product = product_row.to_product()
                products.append(
                    {
                        "productId": product.id,
                        "symbol": product.symbol,
                        "contractType": product.contract_type.value,
                        "tickSize": str(product.tick_size),
                        "tradingStatus": product.trading_status.value,
                        "markPrice": None if quote is None else quote.mark_price,
                        "bestBid": None if quote is None else quote.best_bid,
                        "bestAsk": None if quote is None else quote.best_ask,
                        "observedAt": None if quote is None else _utc(quote.observed_at),
                    }
                )
            return {
                "snapshot": {
                    "revision": snapshot.revision,
                    "environment": snapshot.environment,
                    "validatedAt": _utc(snapshot.validated_at),
                    "freshUntil": _utc(snapshot.fresh_until),
                    "fresh": snapshot.fresh_until >= datetime.now(UTC),
                },
                "products": products,
            }

    @app.get("/api/v1/market/watchlist")
    async def watchlist(request: Request) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            rows = (
                await session.execute(
                    select(WatchlistItem)
                    .where(
                        WatchlistItem.user_id == actor.profile_id, WatchlistItem.active.is_(True)
                    )
                    .order_by(WatchlistItem.created_at)
                )
            ).scalars()
            return {
                "items": [
                    {"id": str(row.id), "symbol": row.symbol, "productId": row.product_id}
                    for row in rows
                ]
            }

    @app.put("/api/v1/market/watchlist")
    async def upsert_watchlist(body: WatchlistBody, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        request_id = _request_id(request)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            row = (
                await session.execute(
                    select(WatchlistItem).where(
                        WatchlistItem.user_id == actor.profile_id,
                        WatchlistItem.symbol == body.symbol,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = WatchlistItem(
                    id=uuid4(),
                    user_id=actor.profile_id,
                    symbol=body.symbol,
                    product_id=body.product_id,
                    active=True,
                    created_at=now,
                )
                session.add(row)
            else:
                row.active, row.product_id = True, body.product_id
            await append_audit(
                session,
                actor=actor,
                action="WATCHLIST_UPDATED",
                target_type="product",
                target_id=str(body.product_id),
                outcome="SUCCEEDED",
                request_id=request_id,
            )
            return {"id": str(row.id), "symbol": row.symbol, "productId": row.product_id}

    @app.get("/api/v1/admin/audit")
    async def audit_log(request: Request) -> dict[str, object]:
        await admin(request)
        async with database.transaction() as session:
            rows = (
                await session.execute(
                    select(PlatformAuditEvent)
                    .order_by(desc(PlatformAuditEvent.occurred_at))
                    .limit(200)
                )
            ).scalars()
            return {
                "events": [
                    {
                        "id": str(row.id),
                        "action": row.action,
                        "targetType": row.target_type,
                        "targetId": row.target_id,
                        "outcome": row.outcome,
                        "occurredAt": _utc(row.occurred_at),
                    }
                    for row in rows
                ]
            }

    @app.get("/api/v1/gtt")
    async def list_gtt(request: Request, account_id: UUID) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            await account_permission(session, actor, account_id)
            rows = (
                await session.execute(
                    select(GttOrder)
                    .where(GttOrder.account_id == account_id)
                    .order_by(desc(GttOrder.created_at))
                    .limit(500)
                )
            ).scalars()
            return {
                "orders": [
                    {
                        "id": str(row.id),
                        "accountId": str(row.account_id),
                        "productId": row.product_id,
                        "symbol": row.symbol,
                        "side": row.side,
                        "contracts": row.contracts,
                        "triggerKind": row.trigger_kind,
                        "triggerPrice": row.trigger_price,
                        "trailOffset": row.trail_offset,
                        "limitPrice": row.limit_price,
                        "reduceOnly": row.reduce_only,
                        "status": row.status,
                        "createdAt": _utc(row.created_at),
                        "updatedAt": _utc(row.updated_at),
                    }
                    for row in rows
                ]
            }

    @app.post("/api/v1/gtt")
    async def create_gtt(body: GttBody, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        request_id = _request_id(request)
        values = [
            value
            for value in (body.trigger_price, body.trail_offset, body.limit_price)
            if value is not None
        ]
        try:
            parsed = [Decimal(value) for value in values]
        except InvalidOperation:
            raise HTTPException(status_code=400, detail="invalid_price") from None
        if any(not value.is_finite() or value <= 0 for value in parsed):
            raise HTTPException(status_code=400, detail="invalid_price")
        if (body.trigger_kind == "trailing_stop") != (body.trail_offset is not None):
            raise HTTPException(status_code=400, detail="invalid_trigger")
        if body.trigger_kind != "trailing_stop" and body.trigger_price is None:
            raise HTTPException(status_code=400, detail="invalid_trigger")
        now, gtt_id = datetime.now(UTC), uuid4()
        try:
            async with database.transaction() as session:
                permission = await account_permission(session, actor, body.account_id)
                if actor.role == "viewer" or permission == "view":
                    raise HTTPException(status_code=403, detail="trading_permission_required")
                account = await session.get(Account, body.account_id)
                control = (
                    await session.execute(
                        select(AccountLiveControl).where(
                            AccountLiveControl.account_id == body.account_id
                        )
                    )
                ).scalar_one_or_none()
                if (
                    account is None
                    or account.mode != "LIVE"
                    or account.identity_verified_at is None
                    or account.current_credential_version is None
                    or control is None
                    or control.credential_state != "verified"
                    or control.reconciliation_state != "ready"
                    or not control.exits_enabled
                ):
                    raise HTTPException(status_code=409, detail="account_not_live_ready")
                row = GttOrder(
                    id=gtt_id,
                    account_id=body.account_id,
                    created_by_user_id=actor.profile_id,
                    product_id=body.product_id,
                    symbol=body.symbol,
                    side=body.side,
                    contracts=body.contracts,
                    trigger_kind=body.trigger_kind,
                    trigger_price=body.trigger_price,
                    trail_offset=body.trail_offset,
                    limit_price=body.limit_price,
                    reduce_only=True,
                    status="armed",
                    idempotency_key=UUID(request_id),
                    triggered_order_id=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                await append_audit(
                    session,
                    actor=actor,
                    action="GTT_ARMED",
                    target_type="gtt",
                    target_id=str(gtt_id),
                    outcome="SUCCEEDED",
                    request_id=request_id,
                    details={"accountId": str(body.account_id), "triggerKind": body.trigger_kind},
                )
        except IntegrityError:
            async with database.transaction() as session:
                existing = (
                    await session.execute(
                        select(GttOrder).where(
                            GttOrder.account_id == body.account_id,
                            GttOrder.idempotency_key == UUID(request_id),
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    raise HTTPException(status_code=409, detail="gtt_conflict") from None
                gtt_id = existing.id
        return {"id": str(gtt_id), "status": "armed", "reduceOnly": True}

    @app.post("/api/v1/gtt/{gtt_id}/cancel")
    async def cancel_gtt(gtt_id: UUID, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        request_id = _request_id(request)
        async with database.transaction() as session:
            row = await session.get(GttOrder, gtt_id)
            if row is None:
                raise HTTPException(status_code=404, detail="gtt_not_found")
            permission = await account_permission(session, actor, row.account_id)
            if actor.role == "viewer" or permission == "view":
                raise HTTPException(status_code=403, detail="trading_permission_required")
            if row.status not in {"armed", "triggered"}:
                raise HTTPException(status_code=409, detail="gtt_not_cancellable")
            row.status, row.updated_at = "cancelled", datetime.now(UTC)
            await append_audit(
                session,
                actor=actor,
                action="GTT_CANCELLED",
                target_type="gtt",
                target_id=str(row.id),
                outcome="SUCCEEDED",
                request_id=request_id,
            )
        return {"id": str(gtt_id), "status": "cancelled"}

    return auth


__all__ = ["install_platform_routes"]
