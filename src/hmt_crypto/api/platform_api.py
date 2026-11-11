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
from hmt_crypto.api.live_order_admission import (
    LiveOrderAdmissionError,
    evaluate_order_sizing,
    position_notional,
)
from hmt_crypto.api.live_submission import make_live_client_order_id
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
    AccountRiskPolicy,
    CatalogueProduct,
    CatalogueSnapshot,
    CredentialSecretReference,
    CredentialVersion,
    GttOrder,
    LiveOrderIntent,
    LiveOrderPreview,
    LiveOrderSubmission,
    MarketQuote,
    PlatformAuditEvent,
    PlatformLiveControl,
    PlatformUser,
    PrivateReconciliationSnapshot,
    PrivateWorkerLease,
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


class RiskPolicyBody(_Body):
    max_order_notional: str = Field(min_length=1, max_length=64)
    max_position_notional: str = Field(min_length=1, max_length=64)
    max_daily_loss: str = Field(min_length=1, max_length=64)
    max_open_orders: int = Field(ge=1, le=10_000)
    allowed_product_ids: list[int] = Field(min_length=1, max_length=500)
    enabled: bool


class LiveGateBody(_Body):
    entries_enabled: bool
    exits_enabled: bool
    reason: str = Field(min_length=3, max_length=256)


class GlobalEntryGateBody(_Body):
    entries_enabled: bool
    reason: str = Field(min_length=3, max_length=256)


class OrderPreviewBody(_Body):
    account_id: UUID
    product_id: int = Field(gt=0)
    side: Literal["buy", "sell"]
    contracts: int = Field(gt=0, lt=2**63)
    order_type: Literal["market", "limit"]
    limit_price: str | None = Field(default=None, max_length=64)
    reduce_only: bool


class OrderIntentBody(_Body):
    preview_id: UUID


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


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="invalid_risk_limit") from None
    exponent = parsed.as_tuple().exponent
    if not parsed.is_finite() or parsed <= 0 or type(exponent) is not int or exponent < -18:
        raise HTTPException(status_code=400, detail="invalid_risk_limit")
    return parsed


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
                    fill_count=evidence.fill_count,
                    wallet_transaction_count=evidence.wallet_transaction_count,
                    foreign_product_ids=json.dumps(evidence.foreign_product_ids),
                    complete=evidence.complete,
                    content_sha256=evidence.content_sha256,
                    outcome=outcome,
                    balances_payload=evidence.balances_payload,
                    positions_payload=evidence.positions_payload,
                    open_orders_payload=evidence.open_orders_payload,
                    fills_payload=evidence.fills_payload,
                    wallet_transactions_payload=evidence.wallet_transactions_payload,
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
            "fillCount": evidence.fill_count,
            "walletTransactionCount": evidence.wallet_transaction_count,
            "observedAt": _utc(observed_at),
        }

    @app.get("/api/v1/accounts/{account_id}/state")
    async def account_state(account_id: UUID, request: Request) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            await account_permission(session, actor, account_id)
            control = (
                await session.execute(
                    select(AccountLiveControl).where(AccountLiveControl.account_id == account_id)
                )
            ).scalar_one_or_none()
            lease = (
                await session.execute(
                    select(PrivateWorkerLease).where(PrivateWorkerLease.account_id == account_id)
                )
            ).scalar_one_or_none()
            row = (
                await session.execute(
                    select(PrivateReconciliationSnapshot)
                    .where(PrivateReconciliationSnapshot.account_id == account_id)
                    .order_by(desc(PrivateReconciliationSnapshot.observed_at))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if (
                row is None
                or control is None
                or control.reconciliation_state != "ready"
                or lease is None
                or lease.lease_until <= datetime.now(UTC)
                or lease.credential_version != row.credential_version
                or row.worker_owner_id != lease.owner_id
                or row.worker_generation != lease.generation
                or row.outcome != "ready"
                or row.balances_payload is None
                or row.positions_payload is None
                or row.open_orders_payload is None
                or row.fills_payload is None
                or row.wallet_transactions_payload is None
            ):
                raise HTTPException(status_code=409, detail="account_state_not_ready")
            reconciled_at = control.reconciled_at
            if reconciled_at is None:
                raise HTTPException(status_code=409, detail="account_state_not_ready")
            age_seconds = max(0, int((datetime.now(UTC) - reconciled_at).total_seconds()))
            return {
                "accountId": str(account_id),
                "observedAt": _utc(row.observed_at),
                "reconciledAt": _utc(reconciled_at),
                "ageSeconds": age_seconds,
                "fresh": age_seconds <= 60,
                "credentialVersion": row.credential_version,
                "balances": json.loads(row.balances_payload),
                "positions": json.loads(row.positions_payload),
                "openOrders": json.loads(row.open_orders_payload),
                "fills": json.loads(row.fills_payload),
                "walletTransactions": json.loads(row.wallet_transactions_payload),
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

    @app.post("/api/v1/trading/previews")
    async def create_order_preview(body: OrderPreviewBody, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        request_id = UUID(_request_id(request))
        now = datetime.now(UTC)
        async with database.transaction() as session:
            permission = await account_permission(session, actor, body.account_id)
            if actor.role == "viewer" or permission == "view":
                raise HTTPException(status_code=403, detail="trading_permission_required")
            replay = await session.scalar(
                select(LiveOrderPreview).where(
                    LiveOrderPreview.account_id == body.account_id,
                    LiveOrderPreview.request_id == request_id,
                )
            )
            if replay is not None:
                try:
                    requested_limit = (
                        None if body.limit_price is None else Decimal(body.limit_price)
                    )
                except InvalidOperation:
                    raise HTTPException(status_code=409, detail="idempotency_conflict") from None
                if (
                    replay.product_id != body.product_id
                    or replay.side != body.side
                    or replay.contracts != body.contracts
                    or replay.order_type != body.order_type
                    or replay.reduce_only != body.reduce_only
                    or replay.limit_price != requested_limit
                ):
                    raise HTTPException(status_code=409, detail="idempotency_conflict")
                return {
                    "previewId": str(replay.id),
                    "accountId": str(replay.account_id),
                    "productId": replay.product_id,
                    "price": str(replay.price),
                    "priceBasis": replay.price_basis,
                    "notional": str(replay.notional),
                    "expiresAt": _utc(replay.expires_at),
                    "dispatchable": False,
                }
            account = await session.get(Account, body.account_id)
            control = await session.scalar(
                select(AccountLiveControl).where(AccountLiveControl.account_id == body.account_id)
            )
            risk = await session.scalar(
                select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == body.account_id)
            )
            global_control = await session.get(PlatformLiveControl, "global")
            lease = await session.scalar(
                select(PrivateWorkerLease).where(PrivateWorkerLease.account_id == body.account_id)
            )
            reconciliation = await session.scalar(
                select(PrivateReconciliationSnapshot)
                .where(PrivateReconciliationSnapshot.account_id == body.account_id)
                .order_by(desc(PrivateReconciliationSnapshot.observed_at))
                .limit(1)
            )
            catalogue = await session.scalar(
                select(CatalogueSnapshot)
                .where(
                    CatalogueSnapshot.region == "india",
                    CatalogueSnapshot.environment == "prod",
                    CatalogueSnapshot.fresh_until > now,
                )
                .order_by(desc(CatalogueSnapshot.validated_at))
                .limit(1)
            )
            product_row = (
                None
                if catalogue is None
                else await session.scalar(
                    select(CatalogueProduct).where(
                        CatalogueProduct.snapshot_id == catalogue.id,
                        CatalogueProduct.product_id == body.product_id,
                    )
                )
            )
            quote = await session.scalar(
                select(MarketQuote).where(
                    MarketQuote.region == "india",
                    MarketQuote.environment == "prod",
                    MarketQuote.product_id == body.product_id,
                    MarketQuote.observed_at >= now - timedelta(seconds=15),
                )
            )
            allowed = set() if risk is None else set(json.loads(risk.allowed_product_ids))
            gate_open = bool(
                control and (control.exits_enabled if body.reduce_only else control.entries_enabled)
            )
            ready = (
                account is not None
                and account.mode == "LIVE"
                and account.environment == "prod"
                and account.identity_verified_at is not None
                and control is not None
                and control.credential_state == "verified"
                and control.reconciliation_state == "ready"
                and control.reconciled_at is not None
                and control.reconciled_at >= now - timedelta(seconds=60)
                and gate_open
                and risk is not None
                and risk.enabled
                and body.product_id in allowed
                and lease is not None
                and lease.lease_until > now
                and reconciliation is not None
                and reconciliation.outcome == "ready"
                and reconciliation.observed_at >= now - timedelta(seconds=60)
                and reconciliation.worker_owner_id == lease.owner_id
                and reconciliation.worker_generation == lease.generation
                and reconciliation.credential_version == lease.credential_version
                and catalogue is not None
                and product_row is not None
                and quote is not None
                and (body.reduce_only or bool(global_control and global_control.entries_enabled))
            )
            if not ready:
                raise HTTPException(status_code=409, detail="order_preview_prerequisites_failed")
            assert risk is not None
            assert reconciliation is not None
            assert catalogue is not None
            assert product_row is not None
            assert quote is not None
            try:
                positions = json.loads(reconciliation.positions_payload or "")
                open_orders = json.loads(reconciliation.open_orders_payload or "")
                wallet = json.loads(reconciliation.wallet_transactions_payload or "")
            except (TypeError, ValueError):
                raise HTTPException(status_code=409, detail="account_state_not_ready") from None
            if (
                not isinstance(positions, list)
                or not isinstance(open_orders, list)
                or not isinstance(wallet, list)
            ):
                raise HTTPException(status_code=409, detail="account_state_not_ready")
            if len(open_orders) >= risk.max_open_orders and not body.reduce_only:
                raise HTTPException(status_code=409, detail="open_order_limit_reached")
            product = product_row.to_product()
            market_price = quote.best_ask if body.side == "buy" else quote.best_bid
            try:
                sizing = evaluate_order_sizing(
                    product=product,
                    contracts=body.contracts,
                    order_type=body.order_type,
                    limit_price=body.limit_price,
                    market_price=market_price,
                    reduce_only=body.reduce_only,
                    max_order_notional=risk.max_order_notional,
                )
            except LiveOrderAdmissionError:
                raise HTTPException(status_code=400, detail="invalid_order_sizing") from None
            signed_position = 0
            for item in positions:
                if isinstance(item, dict) and item.get("product_id") == body.product_id:
                    value = item.get("size")
                    if type(value) is not int:
                        raise HTTPException(status_code=409, detail="account_state_not_ready")
                    signed_position = value
            direction = 1 if body.side == "buy" else -1
            if body.reduce_only and (
                signed_position == 0
                or signed_position * direction >= 0
                or body.contracts > abs(signed_position)
            ):
                raise HTTPException(status_code=409, detail="reduce_only_violation")
            projected_contracts = signed_position + direction * body.contracts
            projected_notional = (
                Decimal(0)
                if projected_contracts == 0
                else position_notional(
                    product=product, contracts=abs(projected_contracts), price=sizing.price
                )
            )
            if projected_notional > risk.max_position_notional:
                raise HTTPException(status_code=409, detail="position_limit_exceeded")
            daily_net = Decimal(0)
            for item in wallet:
                if not isinstance(item, dict):
                    raise HTTPException(status_code=409, detail="account_state_not_ready")
                created_at, kind, amount = (
                    item.get("created_at"),
                    item.get("transaction_type"),
                    item.get("amount"),
                )
                if (
                    type(created_at) is str
                    and created_at[:10] == now.date().isoformat()
                    and kind
                    in {"cashflow", "commission", "funding", "liquidation_fee", "settlement"}
                ):
                    try:
                        daily_net += Decimal(str(amount))
                    except InvalidOperation:
                        raise HTTPException(
                            status_code=409, detail="account_state_not_ready"
                        ) from None
            if max(Decimal(0), -daily_net) >= risk.max_daily_loss and not body.reduce_only:
                raise HTTPException(status_code=409, detail="daily_loss_limit_reached")
            evidence_values = {
                "accountId": str(body.account_id),
                "catalogue": str(catalogue.id),
                "contracts": body.contracts,
                "notional": str(sizing.notional),
                "price": str(sizing.price),
                "productId": body.product_id,
                "reconciliation": str(reconciliation.id),
                "reduceOnly": body.reduce_only,
                "riskVersion": risk.version,
                "side": body.side,
            }
            digest = hashlib.sha256(
                json.dumps(
                    evidence_values, ensure_ascii=True, separators=(",", ":"), sort_keys=True
                ).encode("ascii")
            ).hexdigest()
            preview = LiveOrderPreview(
                id=uuid4(),
                account_id=body.account_id,
                created_by_user_id=actor.profile_id,
                request_id=request_id,
                product_id=body.product_id,
                side=body.side,
                contracts=body.contracts,
                order_type=body.order_type,
                limit_price=None if body.limit_price is None else sizing.price,
                reduce_only=body.reduce_only,
                price_basis=sizing.price_basis,
                price=sizing.price,
                notional=sizing.notional,
                catalogue_snapshot_id=catalogue.id,
                reconciliation_snapshot_id=reconciliation.id,
                risk_policy_version=risk.version,
                evidence_sha256=digest,
                created_at=now,
                expires_at=now + timedelta(seconds=15),
            )
            session.add(preview)
            await append_audit(
                session,
                actor=actor,
                action="LIVE_ORDER_PREVIEWED",
                target_type="account",
                target_id=str(body.account_id),
                outcome="SUCCEEDED",
                request_id=str(request_id),
                details={"productId": body.product_id, "reduceOnly": body.reduce_only},
            )
            return {
                "previewId": str(preview.id),
                "accountId": str(body.account_id),
                "productId": body.product_id,
                "price": str(sizing.price),
                "priceBasis": sizing.price_basis,
                "notional": str(sizing.notional),
                "expiresAt": _utc(preview.expires_at),
                "dispatchable": False,
            }

    @app.post("/api/v1/trading/intents")
    async def admit_order_intent(body: OrderIntentBody, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        request_id = UUID(_request_id(request))
        now = datetime.now(UTC)
        async with database.transaction() as session:
            preview = await session.get(LiveOrderPreview, body.preview_id, with_for_update=True)
            if preview is None or preview.created_by_user_id != actor.profile_id:
                raise HTTPException(status_code=404, detail="preview_not_found")
            permission = await account_permission(session, actor, preview.account_id)
            if actor.role == "viewer" or permission == "view":
                raise HTTPException(status_code=403, detail="trading_permission_required")
            replay = await session.scalar(
                select(LiveOrderIntent).where(
                    LiveOrderIntent.account_id == preview.account_id,
                    LiveOrderIntent.idempotency_key == request_id,
                )
            )
            if replay is not None:
                if replay.preview_id != preview.id:
                    raise HTTPException(status_code=409, detail="idempotency_conflict")
                return {"intentId": str(replay.id), "status": replay.status, "dispatchable": False}
            used = await session.scalar(
                select(LiveOrderIntent.id).where(LiveOrderIntent.preview_id == preview.id)
            )
            control = await session.scalar(
                select(AccountLiveControl).where(
                    AccountLiveControl.account_id == preview.account_id
                )
            )
            risk = await session.scalar(
                select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == preview.account_id)
            )
            lease = await session.scalar(
                select(PrivateWorkerLease).where(
                    PrivateWorkerLease.account_id == preview.account_id
                )
            )
            reconciliation = await session.get(
                PrivateReconciliationSnapshot, preview.reconciliation_snapshot_id
            )
            global_control = await session.get(PlatformLiveControl, "global")
            if (
                used is not None
                or preview.expires_at <= now
                or control is None
                or risk is None
                or not risk.enabled
                or risk.version != preview.risk_policy_version
                or control.reconciliation_state != "ready"
                or control.reconciled_at is None
                or control.reconciled_at < now - timedelta(seconds=60)
                or lease is None
                or lease.lease_until <= now
                or reconciliation is None
                or reconciliation.outcome != "ready"
                or reconciliation.observed_at < now - timedelta(seconds=60)
                or reconciliation.worker_owner_id != lease.owner_id
                or reconciliation.worker_generation != lease.generation
                or reconciliation.credential_version != lease.credential_version
                or (preview.reduce_only and not control.exits_enabled)
                or (not preview.reduce_only and not control.entries_enabled)
                or (
                    not preview.reduce_only
                    and (global_control is None or not global_control.entries_enabled)
                )
            ):
                raise HTTPException(status_code=409, detail="preview_expired_or_invalidated")
            intent_id = uuid4()
            digest = hashlib.sha256(
                f"{intent_id}:{preview.id}:{preview.evidence_sha256}".encode("ascii")
            ).hexdigest()
            session.add(
                LiveOrderIntent(
                    id=intent_id,
                    account_id=preview.account_id,
                    preview_id=preview.id,
                    created_by_user_id=actor.profile_id,
                    idempotency_key=request_id,
                    status="ADMITTED",
                    intent_sha256=digest,
                    created_at=now,
                )
            )
            client_order_id = make_live_client_order_id(preview.account_id, intent_id)
            request_sha256 = hashlib.sha256(
                f"submission:v1:{intent_id}:{client_order_id}:{preview.evidence_sha256}".encode(
                    "ascii"
                )
            ).hexdigest()
            session.add(
                LiveOrderSubmission(
                    id=uuid4(),
                    account_id=preview.account_id,
                    intent_id=intent_id,
                    client_order_id=client_order_id,
                    status="PENDING",
                    request_sha256=request_sha256,
                    prepared_request_payload=None,
                    prepared_request_sha256=None,
                    worker_owner_id=None,
                    worker_generation=None,
                    credential_version=None,
                    claim_until=None,
                    attempt_number=0,
                    exchange_order_id=None,
                    last_error_code=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await append_audit(
                session,
                actor=actor,
                action="LIVE_ORDER_INTENT_ADMITTED",
                target_type="intent",
                target_id=str(intent_id),
                outcome="SUCCEEDED",
                request_id=str(request_id),
                details={"previewId": str(preview.id)},
            )
            return {"intentId": str(intent_id), "status": "ADMITTED", "dispatchable": False}

    @app.get("/api/v1/risk/{account_id}")
    async def get_risk_policy(account_id: UUID, request: Request) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            await account_permission(session, actor, account_id)
            row = await session.scalar(
                select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == account_id)
            )
            if row is None:
                return {"accountId": str(account_id), "configured": False, "policy": None}
            return {
                "accountId": str(account_id),
                "configured": True,
                "policy": {
                    "maxOrderNotional": str(row.max_order_notional),
                    "maxPositionNotional": str(row.max_position_notional),
                    "maxDailyLoss": str(row.max_daily_loss),
                    "maxOpenOrders": row.max_open_orders,
                    "allowedProductIds": json.loads(row.allowed_product_ids),
                    "enabled": row.enabled,
                    "updatedAt": _utc(row.updated_at),
                },
            }

    @app.put("/api/v1/risk/{account_id}")
    async def put_risk_policy(
        account_id: UUID, body: RiskPolicyBody, request: Request
    ) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        if actor.last_login_at is None or datetime.now(UTC) - actor.last_login_at > timedelta(
            minutes=15
        ):
            raise HTTPException(status_code=403, detail="fresh_authentication_required")
        product_ids = tuple(sorted(set(body.allowed_product_ids)))
        if len(product_ids) != len(body.allowed_product_ids) or any(
            value <= 0 for value in product_ids
        ):
            raise HTTPException(status_code=400, detail="invalid_product_scope")
        limits = (
            _positive_decimal(body.max_order_notional),
            _positive_decimal(body.max_position_notional),
            _positive_decimal(body.max_daily_loss),
        )
        now = datetime.now(UTC)
        async with database.transaction() as session:
            account = await session.get(Account, account_id)
            if account is None or account.mode != "LIVE" or account.environment != "prod":
                raise HTTPException(status_code=404, detail="account_not_found")
            catalogue = await session.scalar(
                select(CatalogueSnapshot)
                .where(
                    CatalogueSnapshot.region == "india",
                    CatalogueSnapshot.environment == "prod",
                    CatalogueSnapshot.fresh_until > now,
                )
                .order_by(desc(CatalogueSnapshot.validated_at))
                .limit(1)
            )
            if catalogue is None:
                raise HTTPException(status_code=409, detail="catalogue_not_ready")
            known = set(
                await session.scalars(
                    select(CatalogueProduct.product_id).where(
                        CatalogueProduct.snapshot_id == catalogue.id,
                        CatalogueProduct.product_id.in_(product_ids),
                    )
                )
            )
            if known != set(product_ids):
                raise HTTPException(status_code=400, detail="invalid_product_scope")
            row = await session.scalar(
                select(AccountRiskPolicy)
                .where(AccountRiskPolicy.account_id == account_id)
                .with_for_update()
            )
            if row is None:
                row = AccountRiskPolicy(
                    id=uuid4(),
                    account_id=account_id,
                    max_order_notional=limits[0],
                    max_position_notional=limits[1],
                    max_daily_loss=limits[2],
                    max_open_orders=body.max_open_orders,
                    allowed_product_ids=json.dumps(product_ids),
                    enabled=body.enabled,
                    updated_by_user_id=actor.profile_id,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.max_order_notional, row.max_position_notional, row.max_daily_loss = limits
                row.max_open_orders = body.max_open_orders
                row.allowed_product_ids = json.dumps(product_ids)
                row.enabled = body.enabled
                row.updated_by_user_id = actor.profile_id
                row.updated_at = now
            control = await session.scalar(
                select(AccountLiveControl).where(AccountLiveControl.account_id == account_id)
            )
            if control is not None:
                control.entries_enabled = False
                control.exits_enabled = False
                control.updated_at = now
            await append_audit(
                session,
                actor=actor,
                action="RISK_POLICY_UPDATED",
                target_type="account",
                target_id=str(account_id),
                outcome="SUCCEEDED",
                request_id=request_id,
                details={"enabled": body.enabled, "productCount": len(product_ids)},
            )
        return {"accountId": str(account_id), "configured": True, "enabled": body.enabled}

    @app.put("/api/v1/admin/live-control")
    async def set_global_live_control(
        body: GlobalEntryGateBody, request: Request
    ) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        if actor.last_login_at is None or datetime.now(UTC) - actor.last_login_at > timedelta(
            minutes=15
        ):
            raise HTTPException(status_code=403, detail="fresh_authentication_required")
        now = datetime.now(UTC)
        async with database.transaction() as session:
            row = await session.get(PlatformLiveControl, "global", with_for_update=True)
            if row is None:
                raise HTTPException(status_code=503, detail="live_control_unavailable")
            row.entries_enabled = body.entries_enabled
            row.reason = body.reason
            row.updated_by_user_id = actor.profile_id
            row.updated_at = now
            if not body.entries_enabled:
                controls = (
                    await session.execute(select(AccountLiveControl).with_for_update())
                ).scalars()
                for control in controls:
                    control.entries_enabled = False
                    control.updated_at = now
            await append_audit(
                session,
                actor=actor,
                action="GLOBAL_ENTRY_GATE_CHANGED",
                target_type="platform",
                target_id="global",
                outcome="SUCCEEDED",
                request_id=request_id,
                details={"enabled": body.entries_enabled, "reason": body.reason},
            )
        return {"entriesEnabled": body.entries_enabled, "reason": body.reason}

    @app.put("/api/v1/admin/accounts/{account_id}/live-control")
    async def set_account_live_control(
        account_id: UUID, body: LiveGateBody, request: Request
    ) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        now = datetime.now(UTC)
        if actor.last_login_at is None or now - actor.last_login_at > timedelta(minutes=15):
            raise HTTPException(status_code=403, detail="fresh_authentication_required")
        async with database.transaction() as session:
            control = await session.scalar(
                select(AccountLiveControl)
                .where(AccountLiveControl.account_id == account_id)
                .with_for_update()
            )
            if control is None:
                raise HTTPException(status_code=404, detail="account_not_found")
            if body.entries_enabled or body.exits_enabled:
                account = await session.get(Account, account_id)
                risk = await session.scalar(
                    select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == account_id)
                )
                lease = await session.scalar(
                    select(PrivateWorkerLease).where(PrivateWorkerLease.account_id == account_id)
                )
                snapshot = await session.scalar(
                    select(PrivateReconciliationSnapshot)
                    .where(PrivateReconciliationSnapshot.account_id == account_id)
                    .order_by(desc(PrivateReconciliationSnapshot.observed_at))
                    .limit(1)
                )
                global_control = await session.get(PlatformLiveControl, "global")
                product_ids = () if risk is None else tuple(json.loads(risk.allowed_product_ids))
                fresh_quotes = 0
                if product_ids:
                    fresh_quotes = int(
                        await session.scalar(
                            select(func.count(MarketQuote.id)).where(
                                MarketQuote.region == "india",
                                MarketQuote.environment == "prod",
                                MarketQuote.product_id.in_(product_ids),
                                MarketQuote.observed_at >= now - timedelta(seconds=30),
                            )
                        )
                        or 0
                    )
                ready = (
                    account is not None
                    and account.mode == "LIVE"
                    and account.environment == "prod"
                    and account.identity_verified_at is not None
                    and control.credential_state == "verified"
                    and control.reconciliation_state == "ready"
                    and control.reconciled_at is not None
                    and control.reconciled_at >= now - timedelta(seconds=60)
                    and risk is not None
                    and risk.enabled
                    and bool(product_ids)
                    and fresh_quotes == len(product_ids)
                    and lease is not None
                    and lease.lease_until > now
                    and snapshot is not None
                    and snapshot.outcome == "ready"
                    and snapshot.worker_owner_id == lease.owner_id
                    and snapshot.worker_generation == lease.generation
                    and snapshot.credential_version == lease.credential_version
                )
                if not ready or (
                    body.entries_enabled
                    and (global_control is None or not global_control.entries_enabled)
                ):
                    raise HTTPException(status_code=409, detail="live_gate_prerequisites_failed")
            control.entries_enabled = body.entries_enabled
            control.exits_enabled = body.exits_enabled
            control.updated_at = now
            await append_audit(
                session,
                actor=actor,
                action="ACCOUNT_LIVE_GATE_CHANGED",
                target_type="account",
                target_id=str(account_id),
                outcome="SUCCEEDED",
                request_id=request_id,
                details={
                    "entriesEnabled": body.entries_enabled,
                    "exitsEnabled": body.exits_enabled,
                    "reason": body.reason,
                },
            )
        return {
            "accountId": str(account_id),
            "entriesEnabled": body.entries_enabled,
            "exitsEnabled": body.exits_enabled,
        }

    @app.get("/api/v1/admin/health/platform")
    async def platform_health(request: Request) -> dict[str, object]:
        await admin(request)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            global_control = await session.get(PlatformLiveControl, "global")
            rows = await session.execute(
                select(Account, AccountLiveControl, AccountRiskPolicy, PrivateWorkerLease)
                .join(AccountLiveControl, AccountLiveControl.account_id == Account.id)
                .outerjoin(AccountRiskPolicy, AccountRiskPolicy.account_id == Account.id)
                .outerjoin(PrivateWorkerLease, PrivateWorkerLease.account_id == Account.id)
                .order_by(Account.profile_name)
            )
            accounts = []
            for account, control, risk, lease in rows:
                reconciliation_age = (
                    None
                    if control.reconciled_at is None
                    else max(0, int((now - control.reconciled_at).total_seconds()))
                )
                accounts.append(
                    {
                        "accountId": str(account.id),
                        "profileName": account.profile_name,
                        "credentialState": control.credential_state,
                        "reconciliationState": control.reconciliation_state,
                        "reconciliationAgeSeconds": reconciliation_age,
                        "workerLeaseActive": lease is not None and lease.lease_until > now,
                        "workerGeneration": None if lease is None else lease.generation,
                        "riskConfigured": risk is not None,
                        "riskEnabled": bool(risk and risk.enabled),
                        "entriesEnabled": control.entries_enabled,
                        "exitsEnabled": control.exits_enabled,
                        "lastErrorCode": control.last_error_code,
                    }
                )
            return {
                "observedAt": _utc(now),
                "globalEntriesEnabled": bool(global_control and global_control.entries_enabled),
                "globalReason": None if global_control is None else global_control.reason,
                "accounts": accounts,
            }

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
                risk = await session.scalar(
                    select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == body.account_id)
                )
                lease = await session.scalar(
                    select(PrivateWorkerLease).where(
                        PrivateWorkerLease.account_id == body.account_id
                    )
                )
                snapshot = await session.scalar(
                    select(PrivateReconciliationSnapshot)
                    .where(PrivateReconciliationSnapshot.account_id == body.account_id)
                    .order_by(desc(PrivateReconciliationSnapshot.observed_at))
                    .limit(1)
                )
                quote = await session.scalar(
                    select(MarketQuote).where(
                        MarketQuote.region == "india",
                        MarketQuote.environment == "prod",
                        MarketQuote.product_id == body.product_id,
                        MarketQuote.observed_at >= now - timedelta(seconds=30),
                    )
                )
                allowed_products = (
                    set() if risk is None else set(json.loads(risk.allowed_product_ids))
                )
                if (
                    account is None
                    or account.mode != "LIVE"
                    or account.identity_verified_at is None
                    or account.current_credential_version is None
                    or control is None
                    or control.credential_state != "verified"
                    or control.reconciliation_state != "ready"
                    or control.reconciled_at is None
                    or control.reconciled_at < now - timedelta(seconds=60)
                    or not control.exits_enabled
                    or risk is None
                    or not risk.enabled
                    or body.product_id not in allowed_products
                    or lease is None
                    or lease.lease_until <= now
                    or snapshot is None
                    or snapshot.outcome != "ready"
                    or snapshot.worker_owner_id != lease.owner_id
                    or snapshot.worker_generation != lease.generation
                    or snapshot.credential_version != lease.credential_version
                    or quote is None
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
