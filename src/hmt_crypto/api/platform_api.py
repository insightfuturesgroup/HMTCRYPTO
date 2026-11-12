"""Invitation-only multi-user API composition for Phase 1."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, desc, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
    projected_position_contracts,
)
from hmt_crypto.api.live_submission import make_live_client_order_id
from hmt_crypto.api.platform_auth import (
    AuthenticationRejected,
    AuthenticationUnavailable,
    SupabaseAuthGateway,
)
from hmt_crypto.api.secret_store import AwsDeltaSecretStore
from hmt_crypto.api.settings import ControlApiSettings
from hmt_crypto.logging import get_logger
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.errors import PersistenceError
from hmt_crypto.persistence.models import (
    Account,
    AccountAssignment,
    AccountLiveControl,
    AccountRiskPolicy,
    ApplicationSetting,
    CatalogueProduct,
    CatalogueSnapshot,
    CredentialSecretReference,
    CredentialVersion,
    GttOrder,
    LiveOrderAction,
    LiveOrderIntent,
    LiveOrderPreview,
    LiveOrderSubmission,
    LiveQualificationEvidence,
    MarketQuote,
    PlatformAuditEvent,
    PlatformLiveControl,
    PlatformNotification,
    PlatformSession,
    PlatformUser,
    PrivateReconciliationSnapshot,
    PrivateStreamEvidence,
    PrivateWorkerLease,
    TradingViewSignal,
    TradingViewStrategy,
    TradingViewWebhookKey,
    WatchlistGroup,
    WatchlistItem,
)

_ACCESS_COOKIE = "__Host-hmt_auth_access"
_REFRESH_COOKIE = "__Host-hmt_auth_refresh"
_CSRF_COOKIE = "__Host-hmt_auth_csrf"
_SESSION_COOKIE = "__Host-hmt_platform_session"
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.: -]{0,127}\Z")
_LOGGER = get_logger("api.platform")


def _payload_decimal(row: dict[str, object], key: str) -> Decimal:
    try:
        value = Decimal(str(row.get(key, "0")))
    except InvalidOperation:
        return Decimal(0)
    return value if value.is_finite() else Decimal(0)


def _payload_list(value: str | None) -> list[dict[str, object]]:
    try:
        decoded: object = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [row for row in decoded if isinstance(row, dict)]


def _page_payload_rows(
    rows: object,
    *,
    symbol: str | None,
    status: str | None,
    order_type: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    sort: str,
    page: int,
    page_size: int,
) -> dict[str, object]:
    if not isinstance(rows, list):
        raise HTTPException(status_code=409, detail="account_state_not_ready")
    selected = [row for row in rows if isinstance(row, dict)]
    if symbol:
        expected = symbol.upper()
        selected = [
            row
            for row in selected
            if str(row.get("product_symbol", row.get("symbol", ""))).upper() == expected
        ]
    if status:
        expected_status = status.lower()
        selected = [
            row
            for row in selected
            if str(row.get("state", row.get("status", ""))).lower() == expected_status
        ]
    if order_type:
        expected_type = order_type.lower()
        selected = [
            row for row in selected if str(row.get("order_type", "")).lower() == expected_type
        ]

    def observed_at(row: dict[str, object]) -> datetime:
        raw = row.get(
            "created_at",
            row.get("timestamp", row.get("updated_at", row.get("time", ""))),
        )
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    if date_from is not None:
        selected = [row for row in selected if observed_at(row) >= date_from]
    if date_to is not None:
        selected = [row for row in selected if observed_at(row) <= date_to]
    selected.sort(key=observed_at, reverse=sort == "newest")
    total = len(selected)
    start = (page - 1) * page_size
    return {
        "items": selected[start : start + page_size],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class LoginBody(_Body):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=1024)


class PasswordRecoveryBody(_Body):
    access_token: str = Field(min_length=32, max_length=8192)
    new_password: str = Field(min_length=12, max_length=1024)


class InviteBody(_Body):
    email: str = Field(min_length=3, max_length=320)
    full_name: str = Field(min_length=1, max_length=128)
    role: Literal["admin", "trader", "viewer"]


class UpdateUserBody(_Body):
    role: Literal["admin", "trader", "viewer"]
    status: Literal["invited", "active", "disabled"]


class CreateAccountBody(_Body):
    profile_name: str = Field(min_length=1, max_length=128)
    environment: Literal["prod"]


class UpdateAccountBody(_Body):
    profile_name: str = Field(min_length=1, max_length=128)


class AssignmentBody(_Body):
    user_id: UUID
    permission: Literal["view", "trade", "manage"]


class CredentialBody(_Body):
    api_key: str = Field(min_length=8, max_length=512)
    api_secret: str = Field(min_length=16, max_length=1024)


class WatchlistBody(_Body):
    symbol: str = Field(min_length=1, max_length=128)
    product_id: int = Field(gt=0)


class WatchlistGroupBody(_Body):
    name: str = Field(min_length=1, max_length=64)


class WatchlistGroupItemBody(WatchlistBody):
    position: int = Field(default=0, ge=0, lt=10_000)


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
    max_inflight_signals: int = Field(default=5, ge=1, le=100)
    usd_inr_rate: str = Field(default="100", min_length=1, max_length=64)
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


class OrderActionBody(_Body):
    account_id: UUID
    action: Literal["cancel", "modify"]
    order_id: int = Field(gt=0, lt=2**63)
    product_id: int = Field(gt=0, lt=2**63)
    size: int | None = Field(default=None, gt=0, lt=2**63)
    limit_price: str | None = Field(default=None, max_length=64)


class GeneralSettingsBody(_Body):
    operations_contact_email: str = Field(min_length=3, max_length=320)
    display_timezone: Literal["UTC", "Asia/Kolkata"]


class TradingViewStrategyBody(_Body):
    account_id: UUID
    name: str = Field(min_length=1, max_length=128)
    product_id: int = Field(gt=0)
    symbol: str = Field(min_length=1, max_length=128)
    contracts: int = Field(gt=0, lt=2**63)
    order_type: Literal["market", "limit"]
    reduce_only: bool
    stop_loss_pct: str = Field(default="2", min_length=1, max_length=32)
    target_pct: str = Field(default="4", min_length=1, max_length=32)
    trailing_offset_pct: str = Field(default="1", min_length=1, max_length=32)
    protection_required: bool = True
    enabled: bool = False


class TradingViewStrategyStateBody(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    product_id: int | None = Field(default=None, gt=0)
    symbol: str | None = Field(default=None, min_length=1, max_length=128)
    contracts: int | None = Field(default=None, gt=0, lt=2**63)
    order_type: Literal["market", "limit"] | None = None
    reduce_only: bool | None = None
    enabled: bool | None = None
    stop_loss_pct: str | None = Field(default=None, min_length=1, max_length=32)
    target_pct: str | None = Field(default=None, min_length=1, max_length=32)
    trailing_offset_pct: str | None = Field(default=None, min_length=1, max_length=32)
    protection_required: bool | None = None


class TradingViewSignalBody(_Body):
    signal_id: str = Field(min_length=1, max_length=128)
    side: Literal["buy", "sell"]
    timestamp: int | str
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

    def require_trading_writes_allowed() -> None:
        if settings.private_read_only:
            raise HTTPException(status_code=409, detail="private_read_only")

    async def principal(request: Request, *, mutation: bool = False) -> Principal:
        if mutation:
            require_csrf(request)
        access = request.cookies.get(_ACCESS_COOKIE)
        platform_token = request.cookies.get(_SESSION_COOKIE)
        if access is None or platform_token is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        try:
            identity = await auth.user(access)
        except AuthenticationRejected:
            raise HTTPException(status_code=401, detail="authentication_required") from None
        except AuthenticationUnavailable:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from None
        now = datetime.now(UTC)
        token_sha256 = hashlib.sha256(platform_token.encode("utf-8")).hexdigest()
        async with database.transaction() as session:
            result = (
                await session.execute(
                    select(PlatformUser, PlatformSession)
                    .join(PlatformSession, PlatformSession.user_id == PlatformUser.id)
                    .where(
                        PlatformUser.auth_user_id == identity.auth_user_id,
                        PlatformSession.token_sha256 == token_sha256,
                    )
                )
            ).one_or_none()
            if result is None:
                raise HTTPException(status_code=401, detail="authentication_required")
            row, platform_session = result
            if (
                row.status != "active"
                or row.email.casefold() != identity.email
                or platform_session.revoked_at is not None
                or platform_session.expires_at <= now
                or platform_session.last_seen_at
                <= now - timedelta(seconds=settings.session_idle_seconds)
            ):
                raise HTTPException(status_code=403, detail="account_disabled_or_unassigned")
            platform_session.last_seen_at = now
            platform_session.expires_at = now + timedelta(
                seconds=settings.session_absolute_seconds
            )
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
        if mutation and (
            actor.last_login_at is None
            or actor.last_login_at < datetime.now(UTC) - timedelta(minutes=15)
        ):
            raise HTTPException(status_code=401, detail="fresh_authentication_required")
        return actor

    async def qualification_status(
        session: object,
        *,
        account_id: UUID,
        now: datetime,
    ) -> tuple[bool, str, LiveQualificationEvidence | None, list[PrivateStreamEvidence]]:
        evidence = await session.scalar(  # type: ignore[attr-defined]
            select(LiveQualificationEvidence)
            .where(LiveQualificationEvidence.account_id == account_id)
            .order_by(desc(LiveQualificationEvidence.created_at))
            .limit(1)
        )
        streams = list(
            await session.scalars(  # type: ignore[attr-defined]
                select(PrivateStreamEvidence).where(
                    PrivateStreamEvidence.account_id == account_id,
                    PrivateStreamEvidence.observed_at >= now - timedelta(seconds=45),
                    PrivateStreamEvidence.state.in_(("snapshot", "live")),
                )
            )
        )
        required_channels = {"orders", "positions", "margins", "v2/user_trades"}
        if {row.channel for row in streams} != required_channels:
            return False, "private_stream_stale", evidence, streams
        if evidence is None or evidence.status != "qualified":
            return False, "qualification_missing", evidence, streams
        if evidence.window_completed_at is None:
            return False, "qualification_missing", evidence, streams
        if evidence.window_completed_at - evidence.window_started_at < timedelta(hours=24):
            return False, "qualification_window_incomplete", evidence, streams
        return True, "qualified", evidence, streams

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
        active = await session.scalar(  # type: ignore[attr-defined]
            select(Account.id).where(Account.id == account_id, Account.archived_at.is_(None))
        )
        if active is None:
            raise HTTPException(status_code=404, detail="account_not_found")
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
        response: JSONResponse,
        access: str,
        refresh: str,
        csrf: str,
        platform_token: str,
        age: int,
    ) -> None:
        persistent_age = 315_360_000  # Ten years; server-side revocation remains authoritative.
        for name, value, httponly in (
            (_ACCESS_COOKIE, access, True),
            (_REFRESH_COOKIE, refresh, True),
            (_CSRF_COOKIE, csrf, False),
            (_SESSION_COOKIE, platform_token, True),
        ):
            response.set_cookie(
                name,
                value,
                httponly=httponly,
                secure=True,
                samesite="strict",
                path="/",
                max_age=(age if name == _ACCESS_COOKIE else persistent_age),
            )

    def clear_auth_cookies(response: JSONResponse) -> None:
        for name in (_ACCESS_COOKIE, _REFRESH_COOKIE, _CSRF_COOKIE, _SESSION_COOKIE):
            response.delete_cookie(name, path="/", secure=True, samesite="strict")

    @app.post("/api/v1/auth/password")
    async def complete_password_recovery(
        body: PasswordRecoveryBody, request: Request
    ) -> dict[str, object]:
        require_origin(request)
        try:
            identity = await auth.update_password(body.access_token, body.new_password)
        except AuthenticationRejected:
            raise HTTPException(status_code=401, detail="password_recovery_rejected") from None
        except AuthenticationUnavailable:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from None
        async with database.transaction() as session:
            row = await session.scalar(
                select(PlatformUser).where(PlatformUser.auth_user_id == identity.auth_user_id)
            )
            if row is None or row.status == "disabled":
                raise HTTPException(status_code=403, detail="account_disabled_or_unassigned")
            sessions = (
                await session.execute(
                    select(PlatformSession).where(
                        PlatformSession.user_id == row.id,
                        PlatformSession.revoked_at.is_(None),
                    )
                )
            ).scalars()
            now = datetime.now(UTC)
            for platform_session in sessions:
                platform_session.revoked_at = now
            await append_audit(
                session,
                actor=Principal(
                    row.id,
                    row.auth_user_id,
                    row.email,
                    row.full_name,
                    row.role,
                    row.last_login_at,
                ),
                action="USER_PASSWORD_RECOVERED",
                target_type="user",
                target_id=str(row.id),
                outcome="SUCCEEDED",
                request_id=str(uuid4()),
            )
        return {"updated": True}

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
        platform_token = secrets.token_urlsafe(32)
        platform_token_sha256 = hashlib.sha256(platform_token.encode("utf-8")).hexdigest()
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
            active_sessions = list(
                (
                    await session.execute(
                        select(PlatformSession)
                        .where(
                            PlatformSession.user_id == row.id,
                            PlatformSession.revoked_at.is_(None),
                            PlatformSession.expires_at > now,
                        )
                        .order_by(PlatformSession.created_at)
                    )
                ).scalars()
            )
            overflow = max(0, len(active_sessions) - settings.max_sessions + 1)
            for old_session in active_sessions[:overflow]:
                old_session.revoked_at = now
            session.add(
                PlatformSession(
                    id=uuid4(),
                    user_id=row.id,
                    token_sha256=platform_token_sha256,
                    created_at=now,
                    expires_at=now + timedelta(seconds=settings.session_absolute_seconds),
                    last_seen_at=now,
                    revoked_at=None,
                    user_agent_sha256=hashlib.sha256(
                        request.headers.get("user-agent", "").encode("utf-8")
                    ).hexdigest(),
                )
            )
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
                "readOnlyMode": settings.private_read_only,
                "user": {
                    "id": str(actor.profile_id),
                    "email": actor.email,
                    "fullName": actor.full_name,
                    "role": actor.role,
                },
            }
        )
        set_auth_cookies(
            response,
            tokens.access_token,
            tokens.refresh_token,
            csrf,
            platform_token,
            tokens.expires_in,
        )
        return response

    @app.get("/api/v1/auth/session")
    async def platform_session(request: Request) -> JSONResponse:
        try:
            actor = await principal(request)
        except HTTPException as error:
            if error.status_code not in {401, 403}:
                raise
            response = JSONResponse(
                {
                    "authenticated": False,
                    "csrfToken": None,
                    "readOnlyMode": settings.private_read_only,
                    "user": None,
                }
            )
            clear_auth_cookies(response)
            return response
        return JSONResponse(
            {
                "authenticated": True,
                "csrfToken": request.cookies.get(_CSRF_COOKIE),
                "readOnlyMode": settings.private_read_only,
                "user": {
                    "id": str(actor.profile_id),
                    "email": actor.email,
                    "fullName": actor.full_name,
                    "role": actor.role,
                },
            }
        )

    @app.post("/api/v1/auth/refresh")
    async def refresh_platform_session(request: Request) -> JSONResponse:
        """Rotate Supabase tokens and keep an active application session alive."""

        require_origin(request)
        refresh = request.cookies.get(_REFRESH_COOKIE)
        platform_token = request.cookies.get(_SESSION_COOKIE)
        csrf = request.cookies.get(_CSRF_COOKIE)
        if refresh is None or platform_token is None or csrf is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        try:
            tokens = await auth.refresh(refresh)
        except AuthenticationRejected:
            raise HTTPException(status_code=401, detail="authentication_required") from None
        except AuthenticationUnavailable:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from None
        now = datetime.now(UTC)
        token_sha256 = hashlib.sha256(platform_token.encode("utf-8")).hexdigest()
        async with database.transaction() as session:
            result = (
                await session.execute(
                    select(PlatformUser, PlatformSession)
                    .join(PlatformSession, PlatformSession.user_id == PlatformUser.id)
                    .where(
                        PlatformUser.auth_user_id == tokens.identity.auth_user_id,
                        PlatformSession.token_sha256 == token_sha256,
                    )
                )
            ).one_or_none()
            if result is None:
                raise HTTPException(status_code=401, detail="authentication_required")
            row, platform_session = result
            if (
                row.status != "active"
                or row.email.casefold() != tokens.identity.email
                or platform_session.revoked_at is not None
            ):
                raise HTTPException(status_code=403, detail="account_disabled_or_unassigned")
            platform_session.last_seen_at = now
            platform_session.expires_at = now + timedelta(
                seconds=settings.session_absolute_seconds
            )
            actor = Principal(
                row.id,
                row.auth_user_id,
                row.email,
                row.full_name,
                row.role,
                row.last_login_at,
            )
        response = JSONResponse(
            {
                "authenticated": True,
                "csrfToken": csrf,
                "readOnlyMode": settings.private_read_only,
                "user": {
                    "id": str(actor.profile_id),
                    "email": actor.email,
                    "fullName": actor.full_name,
                    "role": actor.role,
                },
            }
        )
        set_auth_cookies(
            response,
            tokens.access_token,
            tokens.refresh_token,
            csrf,
            platform_token,
            tokens.expires_in,
        )
        return response

    @app.delete("/api/v1/auth/session")
    async def platform_logout(request: Request) -> JSONResponse:
        actor = await principal(request, mutation=True)
        access = request.cookies.get(_ACCESS_COOKIE)
        platform_token = request.cookies.get(_SESSION_COOKIE)
        if access is not None:
            await auth.logout(access)
        async with database.transaction() as session:
            if platform_token is not None:
                token_sha256 = hashlib.sha256(platform_token.encode("utf-8")).hexdigest()
                platform_session = await session.scalar(
                    select(PlatformSession).where(
                        PlatformSession.user_id == actor.profile_id,
                        PlatformSession.token_sha256 == token_sha256,
                    )
                )
                if platform_session is not None and platform_session.revoked_at is None:
                    platform_session.revoked_at = datetime.now(UTC)
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
            if body.status == "disabled":
                now = datetime.now(UTC)
                sessions = (
                    await session.execute(
                        select(PlatformSession).where(
                            PlatformSession.user_id == row.id,
                            PlatformSession.revoked_at.is_(None),
                        )
                    )
                ).scalars()
                for platform_session in sessions:
                    platform_session.revoked_at = now
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

    @app.post("/api/v1/admin/users/{user_id}/revoke-sessions")
    async def revoke_user_sessions(user_id: UUID, request: Request) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            row = await session.get(PlatformUser, user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="user_not_found")
            sessions = list(
                (
                    await session.execute(
                        select(PlatformSession).where(
                            PlatformSession.user_id == user_id,
                            PlatformSession.revoked_at.is_(None),
                        )
                    )
                ).scalars()
            )
            for platform_session in sessions:
                platform_session.revoked_at = now
            await append_audit(
                session,
                actor=actor,
                action="USER_SESSIONS_REVOKED",
                target_type="user",
                target_id=str(user_id),
                outcome="SUCCEEDED",
                request_id=request_id,
                details={"sessionCount": len(sessions)},
            )
        return {"revoked": len(sessions)}

    @app.post("/api/v1/admin/users/{user_id}/password-reset")
    async def send_user_password_reset(user_id: UUID, request: Request) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        async with database.transaction() as session:
            row = await session.get(PlatformUser, user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="user_not_found")
            email = row.email
        try:
            await auth.send_password_reset(email, redirect_to=settings.allowed_origin)
        except AuthenticationRejected:
            raise HTTPException(status_code=409, detail="password_reset_rejected") from None
        except AuthenticationUnavailable:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from None
        async with database.transaction() as session:
            await append_audit(
                session,
                actor=actor,
                action="USER_PASSWORD_RESET_SENT",
                target_type="user",
                target_id=str(user_id),
                outcome="SUCCEEDED",
                request_id=request_id,
            )
        return {"sent": True}

    @app.get("/api/v1/accounts")
    async def list_accounts(request: Request) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            if actor.role == "admin":
                query = (
                    select(
                        Account,
                        literal("manage").label("permission"),
                        AccountLiveControl,
                    )
                    .outerjoin(AccountLiveControl, AccountLiveControl.account_id == Account.id)
                    .where(
                        Account.region == "india",
                        Account.environment == "prod",
                        Account.mode == "LIVE",
                        Account.archived_at.is_(None),
                    )
                )
            else:
                query = (
                    select(Account, AccountAssignment.permission, AccountLiveControl)
                    .join(AccountAssignment, AccountAssignment.account_id == Account.id)
                    .outerjoin(AccountLiveControl, AccountLiveControl.account_id == Account.id)
                    .where(
                        AccountAssignment.user_id == actor.profile_id,
                        Account.region == "india",
                        Account.environment == "prod",
                        Account.mode == "LIVE",
                        Account.archived_at.is_(None),
                    )
                )
            rows = (await session.execute(query.order_by(Account.profile_name))).all()
            now = datetime.now(UTC)
            payload: list[dict[str, object]] = []
            for account, permission, control in rows:
                lease = await session.scalar(
                    select(PrivateWorkerLease).where(PrivateWorkerLease.account_id == account.id)
                )
                snapshot = await session.scalar(
                    select(PrivateReconciliationSnapshot)
                    .where(PrivateReconciliationSnapshot.account_id == account.id)
                    .order_by(desc(PrivateReconciliationSnapshot.observed_at))
                    .limit(1)
                )
                protection_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(GttOrder)
                        .where(
                            GttOrder.account_id == account.id,
                            GttOrder.status.in_(("armed", "triggered")),
                        )
                    )
                    or 0
                )
                observed = None if snapshot is None else snapshot.observed_at
                reconciled = None if control is None else control.reconciled_at
                payload.append(
                    {
                        "id": str(account.id),
                        "profileName": account.profile_name,
                        "region": account.region,
                        "environment": account.environment,
                        "mode": account.mode,
                        "permission": permission,
                        "identityVerified": account.identity_verified_at is not None,
                        "credentialConfigured": account.current_credential_version is not None,
                        "credentialState": (
                            "unconfigured" if control is None else control.credential_state
                        ),
                        "credentialVersion": account.current_credential_version,
                        "reconciliationState": (
                            "unknown" if control is None else control.reconciliation_state
                        ),
                        "reconciledAt": (
                            None
                            if control is None or control.reconciled_at is None
                            else _utc(control.reconciled_at)
                        ),
                        "leaseActive": bool(lease is not None and lease.lease_until > now),
                        "leaseUntil": None if lease is None else _utc(lease.lease_until),
                        "fresh": bool(
                            reconciled is not None
                            and reconciled >= now - timedelta(seconds=60)
                            and control is not None
                            and control.reconciliation_state == "ready"
                        ),
                        "observedAt": None if observed is None else _utc(observed),
                        "activeProtectionCount": protection_count,
                        "entriesEnabled": False if control is None else control.entries_enabled,
                        "exitsEnabled": False if control is None else control.exits_enabled,
                    }
                )
            return {"accounts": payload}

    @app.post("/api/v1/accounts")
    async def create_account(body: CreateAccountBody, request: Request) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        request_id = _request_id(request)
        profile_name = " ".join(body.profile_name.strip().split())
        if _PROFILE.fullmatch(profile_name) is None:
            raise HTTPException(status_code=400, detail="invalid_profile_name")
        now, account_id = datetime.now(UTC), uuid4()
        stage = "owner_lookup"
        try:
            async with database.transaction() as session:
                owner = await session.get(PlatformUser, actor.profile_id)
                if owner is None or owner.status == "disabled":
                    raise HTTPException(status_code=404, detail="owner_not_found")
                existing = await session.scalar(
                    select(Account.id).where(
                        Account.region == "india",
                        Account.environment == body.environment,
                        Account.mode == "LIVE",
                        Account.profile_name == profile_name,
                    )
                )
                if existing is not None:
                    raise HTTPException(status_code=409, detail="account_name_exists")
                account = Account(
                    id=account_id,
                    region="india",
                    environment=body.environment,
                    mode="LIVE",
                    profile_name=profile_name,
                    external_user_id=None,
                    external_account_id=None,
                    identity_verified_at=None,
                    current_credential_version=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(account)
                # Materialize the account parent before inserting assignment and
                # readiness rows.  The Account mapper also participates in the
                # credential-version FK cycle, so relying on implicit unit-of-work
                # ordering can send its dependants first on PostgreSQL.
                stage = "account_parent"
                await session.flush()
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
                # Keep the audit record after all account-owned FK rows.  This
                # makes a failed child write roll back the whole operation while
                # preserving deterministic insertion order.
                stage = "account_children"
                await session.flush()
                stage = "account_audit"
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
            raise HTTPException(status_code=409, detail="account_name_exists") from None
        except PersistenceError:
            _LOGGER.error(
                "account_create_failed",
                request_id=request_id,
                operation="account_create",
                stage=stage,
                error_code="account_create_failed",
            )
            raise HTTPException(status_code=503, detail="account_create_failed") from None
        return {"id": str(account_id), "status": "credential_required", "mode": "LIVE"}

    @app.patch("/api/v1/accounts/{account_id}")
    async def update_account(
        account_id: UUID, body: UpdateAccountBody, request: Request
    ) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        now = datetime.now(UTC)
        profile_name = " ".join(body.profile_name.strip().split())
        if _PROFILE.fullmatch(profile_name) is None:
            raise HTTPException(status_code=400, detail="invalid_profile_name")
        try:
            async with database.transaction() as session:
                account = await session.get(Account, account_id, with_for_update=True)
                if account is None or account.archived_at is not None:
                    raise HTTPException(status_code=404, detail="account_not_found")
                permission = await account_permission(session, actor, account_id)
                if actor.role != "admin" and permission != "manage":
                    raise HTTPException(status_code=403, detail="manage_permission_required")
                account.profile_name = profile_name
                account.updated_at = now
                await append_audit(
                    session,
                    actor=actor,
                    action="ACCOUNT_RENAMED",
                    target_type="account",
                    target_id=str(account.id),
                    outcome="SUCCEEDED",
                    request_id=_request_id(request),
                    details={"profileName": profile_name},
                )
        except IntegrityError:
            raise HTTPException(status_code=409, detail="account_name_exists") from None
        return {"id": str(account_id), "profileName": profile_name}

    @app.delete("/api/v1/accounts/{account_id}")
    async def archive_account(account_id: UUID, request: Request) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        now = datetime.now(UTC)
        if actor.last_login_at is None or now - actor.last_login_at > timedelta(minutes=15):
            raise HTTPException(status_code=403, detail="fresh_authentication_required")
        async with database.transaction() as session:
            account = await session.get(Account, account_id, with_for_update=True)
            if account is None or account.archived_at is not None:
                raise HTTPException(status_code=404, detail="account_not_found")
            control = await session.scalar(
                select(AccountLiveControl)
                .where(AccountLiveControl.account_id == account_id)
                .with_for_update()
            )
            lease = await session.scalar(
                select(PrivateWorkerLease)
                .where(PrivateWorkerLease.account_id == account_id)
                .with_for_update()
            )
            if control is None or control.entries_enabled or control.exits_enabled:
                raise HTTPException(status_code=409, detail="account_gates_must_be_disabled")
            if lease is not None and lease.lease_until > now:
                raise HTTPException(status_code=409, detail="account_worker_active")
            strategies = (
                await session.execute(
                    select(TradingViewStrategy).where(TradingViewStrategy.account_id == account_id)
                )
            ).scalars()
            for strategy in strategies:
                strategy.enabled = False
                strategy.version += 1
                strategy.updated_at = now
            account.archived_at = now
            account.updated_at = now
            await append_audit(
                session,
                actor=actor,
                action="ACCOUNT_ARCHIVED",
                target_type="account",
                target_id=str(account.id),
                outcome="SUCCEEDED",
                request_id=_request_id(request),
                details={"profileName": account.profile_name},
            )
        return {"id": str(account_id), "archived": True}

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

    @app.get("/api/v1/admin/account-assignments")
    async def list_account_assignments(request: Request) -> dict[str, object]:
        await admin(request)
        async with database.transaction() as session:
            rows = (
                await session.execute(
                    select(AccountAssignment, Account.profile_name, PlatformUser.email)
                    .join(Account, Account.id == AccountAssignment.account_id)
                    .join(PlatformUser, PlatformUser.id == AccountAssignment.user_id)
                    .order_by(Account.profile_name, PlatformUser.email)
                )
            ).all()
            return {
                "assignments": [
                    {
                        "id": str(row.id),
                        "accountId": str(row.account_id),
                        "accountName": account_name,
                        "userId": str(row.user_id),
                        "userEmail": email,
                        "permission": row.permission,
                        "updatedAt": _utc(row.updated_at),
                    }
                    for row, account_name, email in rows
                ]
            }

    @app.delete("/api/v1/accounts/{account_id}/assignments/{user_id}")
    async def remove_account_assignment(
        account_id: UUID, user_id: UUID, request: Request
    ) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            row = await session.scalar(
                select(AccountAssignment)
                .where(
                    AccountAssignment.account_id == account_id,
                    AccountAssignment.user_id == user_id,
                )
                .with_for_update()
            )
            if row is None:
                raise HTTPException(status_code=404, detail="assignment_not_found")
            if row.permission == "manage":
                other_managers = await session.scalar(
                    select(func.count(AccountAssignment.id)).where(
                        AccountAssignment.account_id == account_id,
                        AccountAssignment.permission == "manage",
                        AccountAssignment.id != row.id,
                    )
                )
                if int(other_managers or 0) == 0:
                    raise HTTPException(status_code=409, detail="last_account_manager")
            await session.delete(row)
            await append_audit(
                session,
                actor=actor,
                action="ACCOUNT_ASSIGNMENT_REMOVED",
                target_type="account",
                target_id=str(account_id),
                outcome="SUCCEEDED",
                request_id=_request_id(request),
                details={"userId": str(user_id), "removedAt": _utc(now)},
            )
        return {"accountId": str(account_id), "userId": str(user_id), "removed": True}

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
                credential = CredentialVersion(
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
                session.add(credential)
                # The account's current-version FK points at this row. Materialize
                # it before updating the account to avoid unit-of-work ordering
                # through the existing account/credential cycle.
                await session.flush()
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
            now = datetime.now(UTC)
            age_seconds = max(0, int((now - reconciled_at).total_seconds()))
            fresh_until = reconciled_at + timedelta(seconds=60)
            lease_active = lease is not None and lease.lease_until > now
            fence_matches = bool(
                lease is not None
                and lease.credential_version == row.credential_version
                and row.worker_owner_id == lease.owner_id
                and row.worker_generation == lease.generation
            )
            fresh = bool(
                age_seconds <= 60
                and control.reconciliation_state == "ready"
                and lease_active
                and fence_matches
                and row.outcome == "ready"
            )
            return {
                "accountId": str(account_id),
                "observedAt": _utc(row.observed_at),
                "reconciledAt": _utc(reconciled_at),
                "freshUntil": _utc(fresh_until),
                "ageSeconds": age_seconds,
                "fresh": fresh,
                "reconciliationState": control.reconciliation_state,
                "leaseActive": lease_active,
                "leaseUntil": None if lease is None else _utc(lease.lease_until),
                "credentialVersion": row.credential_version,
                "balances": json.loads(row.balances_payload),
                "positions": json.loads(row.positions_payload),
                "openOrders": json.loads(row.open_orders_payload),
                "fills": json.loads(row.fills_payload),
                "walletTransactions": json.loads(row.wallet_transactions_payload),
            }

    @app.get("/api/v1/performance/{account_id}")
    async def account_performance(account_id: UUID, request: Request) -> dict[str, object]:
        state = await account_state(account_id, request)
        wallet = state["walletTransactions"]
        fills = state["fills"]
        if not isinstance(wallet, list) or not isinstance(fills, list):
            raise HTTPException(status_code=409, detail="account_state_not_ready")
        totals: dict[str, Decimal] = {}
        for row in wallet:
            if not isinstance(row, dict) or type(row.get("transaction_type")) is not str:
                raise HTTPException(status_code=409, detail="account_state_not_ready")
            try:
                amount = Decimal(str(row.get("amount")))
            except InvalidOperation:
                raise HTTPException(status_code=409, detail="account_state_not_ready") from None
            if not amount.is_finite():
                raise HTTPException(status_code=409, detail="account_state_not_ready")
            kind = str(row["transaction_type"])
            totals[kind] = totals.get(kind, Decimal(0)) + amount
        net = sum(totals.values(), Decimal(0))
        return {
            "accountId": str(account_id),
            "observedAt": state["observedAt"],
            "fresh": state["fresh"],
            "fillCount": len(fills),
            "netWalletChange": format(net, "f"),
            "funding": format(totals.get("funding", Decimal(0)), "f"),
            "commission": format(totals.get("commission", Decimal(0)), "f"),
            "liquidationFees": format(totals.get("liquidation_fee", Decimal(0)), "f"),
            "settlement": format(totals.get("settlement", Decimal(0)), "f"),
            "totals": {key: format(value, "f") for key, value in sorted(totals.items())},
        }

    @app.get("/api/v1/dashboard/overview")
    async def dashboard_overview(request: Request) -> dict[str, object]:
        actor = await principal(request)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            query = (
                select(Account, AccountLiveControl)
                .join(AccountLiveControl, AccountLiveControl.account_id == Account.id)
                .where(Account.archived_at.is_(None))
            )
            if actor.role != "admin":
                query = query.join(
                    AccountAssignment, AccountAssignment.account_id == Account.id
                ).where(AccountAssignment.user_id == actor.profile_id)
            accounts = (await session.execute(query.order_by(Account.profile_name))).all()
            cards: list[dict[str, object]] = []
            total_available = Decimal(0)
            total_wallet = Decimal(0)
            total_margin = Decimal(0)
            total_unrealized = Decimal(0)
            total_positions = 0
            total_orders = 0
            total_gtt = 0
            for account, control in accounts:
                snapshot = await session.scalar(
                    select(PrivateReconciliationSnapshot)
                    .where(PrivateReconciliationSnapshot.account_id == account.id)
                    .order_by(desc(PrivateReconciliationSnapshot.observed_at))
                    .limit(1)
                )
                balances = [] if snapshot is None else _payload_list(snapshot.balances_payload)
                positions = [] if snapshot is None else _payload_list(snapshot.positions_payload)
                orders = [] if snapshot is None else _payload_list(snapshot.open_orders_payload)
                available = sum(
                    (_payload_decimal(row, "available_balance") for row in balances), Decimal(0)
                )
                wallet_balance = sum(
                    (_payload_decimal(row, "balance") for row in balances), Decimal(0)
                )
                blocked = sum(
                    (_payload_decimal(row, "blocked_margin") for row in balances), Decimal(0)
                )
                unrealized = sum(
                    (_payload_decimal(row, "unrealized_pnl") for row in positions), Decimal(0)
                )
                active_gtt = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(GttOrder)
                        .where(
                            GttOrder.account_id == account.id,
                            GttOrder.status.in_(("armed", "triggered")),
                        )
                    )
                    or 0
                )
                observed = None if snapshot is None else snapshot.observed_at
                reconciled = control.reconciled_at
                fresh = bool(
                    reconciled is not None
                    and reconciled >= now - timedelta(seconds=60)
                    and control.reconciliation_state == "ready"
                )
                card = {
                    "accountId": str(account.id),
                    "profileName": account.profile_name,
                    "availableBalance": format(available, "f"),
                    "walletBalance": format(wallet_balance, "f"),
                    "usedMargin": format(blocked, "f"),
                    "unrealizedPnl": format(unrealized, "f"),
                    "positionCount": len(positions),
                    "openOrderCount": len(orders),
                    "activeGttCount": active_gtt,
                    "fresh": fresh,
                    "observedAt": None if observed is None else _utc(observed),
                }
                cards.append(card)
                total_available += available
                total_wallet += wallet_balance
                total_margin += blocked
                total_unrealized += unrealized
                total_positions += len(positions)
                total_orders += len(orders)
                total_gtt += active_gtt
        return {
            "observedAt": _utc(now),
            "fresh": bool(cards) and all(bool(card["fresh"]) for card in cards),
            "totals": {
                "availableBalance": format(total_available, "f"),
                "walletBalance": format(total_wallet, "f"),
                "usedMargin": format(total_margin, "f"),
                "unrealizedPnl": format(total_unrealized, "f"),
                "positions": total_positions,
                "openOrders": total_orders,
                "activeGtt": total_gtt,
            },
            "accounts": cards,
        }

    @app.get("/api/v1/positions")
    async def normalized_positions(
        request: Request,
        account_id: UUID,
        symbol: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort: str = "newest",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, object]:
        if page < 1 or not 1 <= page_size <= 100 or sort not in {"newest", "oldest"}:
            raise HTTPException(status_code=400, detail="invalid_pagination")
        state = await account_state(account_id, request)
        return _page_payload_rows(
            state["positions"],
            symbol=symbol,
            status=None,
            order_type=None,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            page=page,
            page_size=page_size,
        ) | {"accountId": str(account_id), "fresh": state["fresh"]}

    @app.get("/api/v1/orders")
    async def normalized_orders(
        request: Request,
        account_id: UUID,
        symbol: str | None = None,
        status: str | None = None,
        order_type: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort: str = "newest",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, object]:
        if page < 1 or not 1 <= page_size <= 100 or sort not in {"newest", "oldest"}:
            raise HTTPException(status_code=400, detail="invalid_pagination")
        state = await account_state(account_id, request)
        return _page_payload_rows(
            state["openOrders"],
            symbol=symbol,
            status=status,
            order_type=order_type,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            page=page,
            page_size=page_size,
        ) | {"accountId": str(account_id), "fresh": state["fresh"]}

    @app.get("/api/v1/fills")
    async def normalized_fills(
        request: Request,
        account_id: UUID,
        symbol: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        sort: str = "newest",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, object]:
        if page < 1 or not 1 <= page_size <= 100 or sort not in {"newest", "oldest"}:
            raise HTTPException(status_code=400, detail="invalid_pagination")
        state = await account_state(account_id, request)
        return _page_payload_rows(
            state["fills"],
            symbol=symbol,
            status=None,
            order_type=None,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            page=page,
            page_size=page_size,
        ) | {"accountId": str(account_id), "fresh": state["fresh"]}

    @app.get("/api/v1/notifications")
    async def current_notifications(request: Request) -> dict[str, object]:
        actor = await principal(request)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            query = (
                select(Account, AccountLiveControl, PrivateWorkerLease, AccountRiskPolicy)
                .outerjoin(AccountLiveControl, AccountLiveControl.account_id == Account.id)
                .outerjoin(PrivateWorkerLease, PrivateWorkerLease.account_id == Account.id)
                .outerjoin(AccountRiskPolicy, AccountRiskPolicy.account_id == Account.id)
            )
            if actor.role != "admin":
                query = query.join(
                    AccountAssignment, AccountAssignment.account_id == Account.id
                ).where(AccountAssignment.user_id == actor.profile_id)
            rows = (await session.execute(query.order_by(Account.profile_name))).all()
            findings: dict[str, tuple[UUID | None, str, str, str, str]] = {}
            for account, control, lease, risk in rows:
                account_findings: list[tuple[str, str, str]] = []
                if control is None or control.credential_state != "verified":
                    account_findings.append(
                        ("critical", "credentials", "Delta credentials are not verified")
                    )
                if control is None or control.reconciliation_state != "ready":
                    account_findings.append(
                        ("critical", "reconciliation", "Private reconciliation is not ready")
                    )
                elif control.reconciled_at is None or control.reconciled_at < now - timedelta(
                    seconds=60
                ):
                    account_findings.append(
                        ("critical", "reconciliation_stale", "Private reconciliation is stale")
                    )
                if lease is None or lease.lease_until <= now:
                    account_findings.append(
                        ("critical", "worker_lease", "No active private worker lease")
                    )
                if risk is None or not risk.enabled:
                    account_findings.append(
                        ("warning", "risk_policy", "Live risk policy is not enabled")
                    )
                if control is not None and control.last_error_code is not None:
                    account_findings.append(
                        ("critical", control.last_error_code, "Account control reported an error")
                    )
                for severity, code, message in account_findings:
                    key = f"{account.id}:{code}"
                    findings[key] = (
                        account.id,
                        account.profile_name,
                        severity,
                        code,
                        message,
                    )
            newest_quote = await session.scalar(select(func.max(MarketQuote.observed_at)))
            if newest_quote is None or newest_quote < now - timedelta(seconds=15):
                findings["platform:market_feed_stale"] = (
                    None,
                    "Platform",
                    "critical",
                    "market_feed_stale",
                    "Delta public market feed is unavailable or stale",
                )
            existing = {
                row.finding_key: row
                for row in (
                    await session.scalars(
                        select(PlatformNotification).where(
                            PlatformNotification.finding_key.in_(findings)
                        )
                    )
                ).all()
            }
            for key, (account_id, account_name, severity, code, message) in findings.items():
                notification = existing.get(key)
                if notification is None:
                    session.add(
                        PlatformNotification(
                            id=uuid4(),
                            finding_key=key,
                            account_id=account_id,
                            account_name=account_name,
                            severity=severity,
                            code=code,
                            message=message,
                            first_observed_at=now,
                            last_observed_at=now,
                        )
                    )
                else:
                    notification.account_name = account_name
                    notification.severity = severity
                    notification.message = message
                    notification.last_observed_at = now
                    if notification.resolved_at is not None:
                        notification.first_observed_at = now
                        notification.resolved_at = None
                        notification.acknowledged_at = None
                        notification.acknowledged_by_user_id = None
            if actor.role == "admin":
                active_rows = (
                    await session.scalars(
                        select(PlatformNotification).where(
                            PlatformNotification.resolved_at.is_(None)
                        )
                    )
                ).all()
                for notification in active_rows:
                    if notification.finding_key not in findings:
                        notification.resolved_at = now
            await session.flush()
            visible = select(PlatformNotification).where(PlatformNotification.resolved_at.is_(None))
            if actor.role != "admin":
                visible = visible.outerjoin(
                    AccountAssignment,
                    AccountAssignment.account_id == PlatformNotification.account_id,
                ).where(
                    (PlatformNotification.account_id.is_(None))
                    | (AccountAssignment.user_id == actor.profile_id)
                )
            notifications = (
                await session.scalars(
                    visible.order_by(
                        PlatformNotification.severity,
                        PlatformNotification.last_observed_at.desc(),
                    )
                )
            ).all()
            return {
                "notifications": [
                    {
                        "id": str(row.id),
                        "accountId": str(row.account_id) if row.account_id else None,
                        "accountName": row.account_name,
                        "severity": row.severity,
                        "code": row.code,
                        "message": row.message,
                        "observedAt": _utc(row.last_observed_at),
                        "firstObservedAt": _utc(row.first_observed_at),
                        "acknowledgedAt": (
                            _utc(row.acknowledged_at) if row.acknowledged_at else None
                        ),
                    }
                    for row in notifications
                ],
                "generatedAt": _utc(now),
            }

    @app.post("/api/v1/notifications/{notification_id}/acknowledge")
    async def acknowledge_notification(
        notification_id: UUID, request: Request
    ) -> dict[str, object]:
        require_csrf(request)
        actor = await admin(request, mutation=True)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            row = await session.get(PlatformNotification, notification_id)
            if row is None or row.resolved_at is not None:
                raise HTTPException(status_code=404, detail="notification_not_found")
            row.acknowledged_at = now
            row.acknowledged_by_user_id = actor.profile_id
            await append_audit(
                session,
                actor=actor,
                action="NOTIFICATION_ACKNOWLEDGED",
                target_type="notification",
                target_id=str(notification_id),
                outcome="SUCCEEDED",
                request_id=_request_id(request),
            )
            return {"id": str(row.id), "acknowledgedAt": _utc(now)}

    @app.get("/api/v1/settings")
    async def get_general_settings(request: Request) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            rows = (
                await session.execute(
                    select(ApplicationSetting).where(
                        ApplicationSetting.key.in_(
                            ("operations_contact_email", "display_timezone")
                        ),
                        ApplicationSetting.sensitive.is_(False),
                    )
                )
            ).scalars()
            values = {row.key: row.value for row in rows}
        return {
            "venue": "Delta Exchange India",
            "region": "india",
            "environment": "prod",
            "mode": "LIVE",
            "operationsContactEmail": values.get("operations_contact_email", ""),
            "displayTimezone": values.get("display_timezone", "UTC"),
            "editable": actor.role == "admin",
        }

    @app.put("/api/v1/settings")
    async def update_general_settings(
        body: GeneralSettingsBody, request: Request
    ) -> dict[str, object]:
        actor = await admin(request, mutation=True)
        if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", body.operations_contact_email) is None:
            raise HTTPException(status_code=400, detail="invalid_operations_contact")
        now = datetime.now(UTC)
        updates = {
            "operations_contact_email": body.operations_contact_email.casefold(),
            "display_timezone": body.display_timezone,
        }
        async with database.transaction() as session:
            for key, value in updates.items():
                row = await session.scalar(
                    select(ApplicationSetting).where(ApplicationSetting.key == key)
                )
                if row is None:
                    session.add(
                        ApplicationSetting(
                            id=uuid4(),
                            key=key,
                            value=value,
                            sensitive=False,
                            updated_by_user_id=actor.profile_id,
                            updated_at=now,
                        )
                    )
                else:
                    row.value = value
                    row.sensitive = False
                    row.updated_by_user_id = actor.profile_id
                    row.updated_at = now
            await append_audit(
                session,
                actor=actor,
                action="GENERAL_SETTINGS_UPDATED",
                target_type="platform",
                target_id="general",
                outcome="SUCCEEDED",
                request_id=_request_id(request),
                details={"displayTimezone": body.display_timezone},
            )
        return {"updated": True}

    async def market_products_payload() -> dict[str, object]:
        now = datetime.now(UTC)
        async with database.transaction() as session:
            snapshot = (
                await session.execute(
                    select(CatalogueSnapshot)
                    .where(
                        CatalogueSnapshot.region == "india",
                        CatalogueSnapshot.environment == "prod",
                    )
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
                        "receivedAt": None if quote is None else _utc(quote.updated_at),
                        "quoteFresh": bool(
                            quote is not None and quote.updated_at >= now - timedelta(seconds=30)
                        ),
                    }
                )
            return {
                "snapshot": {
                    "revision": snapshot.revision,
                    "environment": snapshot.environment,
                    "validatedAt": _utc(snapshot.validated_at),
                    "freshUntil": _utc(snapshot.fresh_until),
                    "fresh": snapshot.fresh_until >= now,
                },
                "products": products,
            }

    async def market_change_token() -> str:
        """Return a small projection version without materializing every product."""

        async with database.transaction() as session:
            snapshot = (
                await session.execute(
                    select(CatalogueSnapshot.id, CatalogueSnapshot.environment)
                    .where(
                        CatalogueSnapshot.region == "india",
                        CatalogueSnapshot.environment == "prod",
                    )
                    .order_by(desc(CatalogueSnapshot.validated_at))
                    .limit(1)
                )
            ).one_or_none()
            if snapshot is None:
                return "empty"
            newest_quote = await session.scalar(
                select(func.max(MarketQuote.updated_at)).where(
                    MarketQuote.region == "india",
                    MarketQuote.environment == snapshot.environment,
                )
            )
            return f"{snapshot.id}:{_utc(newest_quote)}"

    @app.get("/api/v1/market/products")
    async def market_products(request: Request) -> dict[str, object]:
        await principal(request)
        return await market_products_payload()

    @app.get("/api/v1/market/stream")
    async def market_stream(request: Request) -> StreamingResponse:
        """Push changed local WebSocket projections without browser polling.

        The public worker remains Delta's sole WebSocket consumer.  This
        same-origin SSE bridge emits only when the persisted projection
        changes and sends a cheap heartbeat while idle.
        """

        await principal(request)

        async def events() -> AsyncIterator[str]:
            previous_token = ""
            idle_ticks = 0
            while not await request.is_disconnected():
                token = await market_change_token()
                if token != previous_token or idle_ticks >= 15:
                    previous_token = token
                    idle_ticks = 0
                    payload = await market_products_payload()
                    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
                    yield f"event: market\ndata: {encoded}\n\n"
                else:
                    idle_ticks += 1
                await asyncio.sleep(1)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    async def default_watchlist_group(session: AsyncSession, user_id: UUID) -> WatchlistGroup:
        row = await session.scalar(
            select(WatchlistGroup)
            .where(WatchlistGroup.user_id == user_id)
            .order_by(WatchlistGroup.position, WatchlistGroup.created_at)
            .limit(1)
        )
        if row is not None:
            return row
        now = datetime.now(UTC)
        row = WatchlistGroup(
            id=uuid4(),
            user_id=user_id,
            name="Watchlist 1",
            position=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        return row

    @app.get("/api/v1/market/watchlists")
    async def watchlist_groups(request: Request) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            await default_watchlist_group(session, actor.profile_id)
            groups = list(
                (
                    await session.execute(
                        select(WatchlistGroup)
                        .where(WatchlistGroup.user_id == actor.profile_id)
                        .order_by(WatchlistGroup.position, WatchlistGroup.created_at)
                    )
                ).scalars()
            )
            items = list(
                (
                    await session.execute(
                        select(WatchlistItem)
                        .where(
                            WatchlistItem.user_id == actor.profile_id,
                            WatchlistItem.active.is_(True),
                        )
                        .order_by(WatchlistItem.group_id, WatchlistItem.position)
                    )
                ).scalars()
            )
            by_group: dict[UUID, list[dict[str, object]]] = {}
            for item in items:
                by_group.setdefault(item.group_id, []).append(
                    {
                        "id": str(item.id),
                        "symbol": item.symbol,
                        "productId": item.product_id,
                        "position": item.position,
                    }
                )
            return {
                "groups": [
                    {
                        "id": str(group.id),
                        "name": group.name,
                        "position": group.position,
                        "items": by_group.get(group.id, []),
                    }
                    for group in groups
                ]
            }

    @app.post("/api/v1/market/watchlists")
    async def create_watchlist_group(
        body: WatchlistGroupBody, request: Request
    ) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        name = " ".join(body.name.strip().split())
        if _PROFILE.fullmatch(name) is None:
            raise HTTPException(status_code=400, detail="invalid_watchlist_name")
        now = datetime.now(UTC)
        try:
            async with database.transaction() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(WatchlistGroup)
                    .where(WatchlistGroup.user_id == actor.profile_id)
                )
                if int(count or 0) >= 7:
                    raise HTTPException(status_code=409, detail="watchlist_limit_reached")
                row = WatchlistGroup(
                    id=uuid4(),
                    user_id=actor.profile_id,
                    name=name,
                    position=int(count or 0),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                await append_audit(
                    session,
                    actor=actor,
                    action="WATCHLIST_GROUP_CREATED",
                    target_type="watchlist_group",
                    target_id=str(row.id),
                    outcome="SUCCEEDED",
                    request_id=_request_id(request),
                )
        except IntegrityError:
            raise HTTPException(status_code=409, detail="watchlist_name_exists") from None
        return {"id": str(row.id), "name": row.name, "position": row.position, "items": []}

    @app.patch("/api/v1/market/watchlists/{group_id}")
    async def rename_watchlist_group(
        group_id: UUID, body: WatchlistGroupBody, request: Request
    ) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        name = " ".join(body.name.strip().split())
        if _PROFILE.fullmatch(name) is None:
            raise HTTPException(status_code=400, detail="invalid_watchlist_name")
        try:
            async with database.transaction() as session:
                row = await session.scalar(
                    select(WatchlistGroup).where(
                        WatchlistGroup.id == group_id,
                        WatchlistGroup.user_id == actor.profile_id,
                    )
                )
                if row is None:
                    raise HTTPException(status_code=404, detail="watchlist_not_found")
                row.name = name
                row.updated_at = datetime.now(UTC)
        except IntegrityError:
            raise HTTPException(status_code=409, detail="watchlist_name_exists") from None
        return {"id": str(group_id), "name": name}

    @app.delete("/api/v1/market/watchlists/{group_id}")
    async def delete_watchlist_group(group_id: UUID, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        async with database.transaction() as session:
            groups = list(
                (
                    await session.execute(
                        select(WatchlistGroup)
                        .where(WatchlistGroup.user_id == actor.profile_id)
                        .order_by(WatchlistGroup.position)
                        .with_for_update()
                    )
                ).scalars()
            )
            row = next((group for group in groups if group.id == group_id), None)
            if row is None:
                raise HTTPException(status_code=404, detail="watchlist_not_found")
            if len(groups) == 1:
                raise HTTPException(status_code=409, detail="last_watchlist_required")
            items = (
                await session.execute(
                    select(WatchlistItem).where(WatchlistItem.group_id == group_id)
                )
            ).scalars()
            for item in items:
                await session.delete(item)
            await session.delete(row)
        return {"id": str(group_id), "deleted": True}

    @app.put("/api/v1/market/watchlists/{group_id}/items")
    async def add_watchlist_group_item(
        group_id: UUID, body: WatchlistGroupItemBody, request: Request
    ) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            group = await session.scalar(
                select(WatchlistGroup).where(
                    WatchlistGroup.id == group_id,
                    WatchlistGroup.user_id == actor.profile_id,
                )
            )
            if group is None:
                raise HTTPException(status_code=404, detail="watchlist_not_found")
            row = await session.scalar(
                select(WatchlistItem).where(
                    WatchlistItem.group_id == group_id,
                    WatchlistItem.symbol == body.symbol,
                )
            )
            if row is None:
                row = WatchlistItem(
                    id=uuid4(),
                    user_id=actor.profile_id,
                    group_id=group_id,
                    symbol=body.symbol,
                    product_id=body.product_id,
                    active=True,
                    position=body.position,
                    created_at=now,
                )
                session.add(row)
            else:
                row.active = True
                row.product_id = body.product_id
                row.position = body.position
        return {"id": str(row.id), "symbol": row.symbol, "productId": row.product_id}

    @app.delete("/api/v1/market/watchlists/{group_id}/items/{item_id}")
    async def delete_watchlist_group_item(
        group_id: UUID, item_id: UUID, request: Request
    ) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        async with database.transaction() as session:
            row = await session.scalar(
                select(WatchlistItem).where(
                    WatchlistItem.id == item_id,
                    WatchlistItem.group_id == group_id,
                    WatchlistItem.user_id == actor.profile_id,
                )
            )
            if row is None:
                raise HTTPException(status_code=404, detail="watchlist_item_not_found")
            await session.delete(row)
        return {"id": str(item_id), "deleted": True}

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
                    .order_by(WatchlistItem.position, WatchlistItem.created_at)
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
            group = await default_watchlist_group(session, actor.profile_id)
            row = (
                await session.execute(
                    select(WatchlistItem).where(
                        WatchlistItem.user_id == actor.profile_id,
                        WatchlistItem.group_id == group.id,
                        WatchlistItem.symbol == body.symbol,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = WatchlistItem(
                    id=uuid4(),
                    user_id=actor.profile_id,
                    group_id=group.id,
                    symbol=body.symbol,
                    product_id=body.product_id,
                    active=True,
                    position=0,
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

    @app.delete("/api/v1/market/watchlist/{item_id}")
    async def remove_watchlist_item(item_id: UUID, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        request_id = _request_id(request)
        async with database.transaction() as session:
            row = await session.scalar(
                select(WatchlistItem).where(
                    WatchlistItem.id == item_id,
                    WatchlistItem.user_id == actor.profile_id,
                )
            )
            if row is None:
                raise HTTPException(status_code=404, detail="watchlist_item_not_found")
            row.active = False
            await append_audit(
                session,
                actor=actor,
                action="WATCHLIST_REMOVED",
                target_type="product",
                target_id=str(row.product_id),
                outcome="SUCCEEDED",
                request_id=request_id,
            )
        return {"removed": True}

    @app.post("/api/v1/trading/previews")
    async def create_order_preview(body: OrderPreviewBody, request: Request) -> dict[str, object]:
        require_trading_writes_allowed()
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
                    "dispatchable": True,
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
            (
                qualification_ready,
                qualification_reason,
                qualification,
                stream_rows,
            ) = await qualification_status(
                session,
                account_id=body.account_id,
                now=now,
            )
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
                and qualification_ready
                and all(
                    row.credential_version == lease.credential_version
                    and row.worker_owner_id == lease.owner_id
                    and row.worker_generation == lease.generation
                    for row in stream_rows
                )
            )
            if not qualification_ready:
                raise HTTPException(status_code=409, detail=qualification_reason)
            if not ready:
                raise HTTPException(status_code=409, detail="order_preview_prerequisites_failed")
            assert risk is not None
            assert reconciliation is not None
            assert catalogue is not None
            assert product_row is not None
            assert quote is not None
            assert qualification is not None
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
            try:
                projected_contracts = projected_position_contracts(
                    positions=positions,
                    open_orders=open_orders,
                    product_id=body.product_id,
                    new_side=body.side,
                    new_contracts=body.contracts,
                    new_reduce_only=body.reduce_only,
                )
            except LiveOrderAdmissionError:
                raise HTTPException(status_code=409, detail="account_state_not_ready") from None
            projected_notional = (
                Decimal(0)
                if projected_contracts == 0
                else position_notional(
                    product=product, contracts=projected_contracts, price=sizing.price
                )
            )
            aggregate_exposure = projected_notional
            for item in positions:
                if not isinstance(item, dict):
                    raise HTTPException(status_code=409, detail="account_state_not_ready")
                other_id = item.get("product_id")
                other_size = item.get("size")
                if (
                    type(other_id) is not int
                    or type(other_size) is not int
                    or other_id == body.product_id
                    or other_size == 0
                ):
                    continue
                other_product = await session.scalar(
                    select(CatalogueProduct).where(
                        CatalogueProduct.snapshot_id == catalogue.id,
                        CatalogueProduct.product_id == other_id,
                    )
                )
                other_quote = await session.scalar(
                    select(MarketQuote).where(
                        MarketQuote.region == "india",
                        MarketQuote.environment == "prod",
                        MarketQuote.product_id == other_id,
                        MarketQuote.observed_at >= now - timedelta(seconds=15),
                    )
                )
                if other_product is None or other_quote is None:
                    raise HTTPException(status_code=409, detail="quote_stale")
                if other_quote.mark_price is None:
                    raise HTTPException(status_code=409, detail="quote_stale")
                aggregate_exposure += position_notional(
                    product=other_product.to_product(),
                    contracts=abs(other_size),
                    price=Decimal(other_quote.mark_price),
                )
            if aggregate_exposure > risk.max_position_notional:
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
                "qualification": str(qualification.id),
                "protection": {"stopLossPct": "2", "targetPct": "4", "trailingPct": "1"},
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
                "projectedExposure": str(aggregate_exposure),
                "riskHeadroom": str(risk.max_position_notional - aggregate_exposure),
                "quoteAgeSeconds": max(0, int((now - quote.observed_at).total_seconds())),
                "reconciliationAgeSeconds": max(
                    0, int((now - reconciliation.observed_at).total_seconds())
                ),
                "requiredProtection": {
                    "stopLossPct": "2",
                    "targetPct": "4",
                    "trailingOffsetPct": "1",
                },
                "expiresAt": _utc(preview.expires_at),
                "dispatchable": True,
            }

    @app.post("/api/v1/trading/intents")
    async def admit_order_intent(body: OrderIntentBody, request: Request) -> dict[str, object]:
        require_trading_writes_allowed()
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
                return {"intentId": str(replay.id), "status": replay.status, "dispatchable": True}
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
                    sent_at=None,
                    resolved_at=None,
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
            return {"intentId": str(intent_id), "status": "ADMITTED", "dispatchable": True}

    @app.get("/api/v1/trading/actions")
    async def list_order_actions(request: Request, account_id: UUID) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            await account_permission(session, actor, account_id)
            rows = (
                await session.execute(
                    select(LiveOrderAction)
                    .where(LiveOrderAction.account_id == account_id)
                    .order_by(desc(LiveOrderAction.created_at))
                    .limit(500)
                )
            ).scalars()
            return {
                "actions": [
                    {
                        "id": str(row.id),
                        "action": row.action.lower(),
                        "orderId": row.order_id,
                        "productId": row.product_id,
                        "targetSize": row.target_size,
                        "targetLimitPrice": (
                            None if row.target_limit_price is None else str(row.target_limit_price)
                        ),
                        "status": row.status,
                        "lastErrorCode": row.last_error_code,
                        "createdAt": _utc(row.created_at),
                        "resolvedAt": (None if row.resolved_at is None else _utc(row.resolved_at)),
                    }
                    for row in rows
                ]
            }

    @app.post("/api/v1/trading/actions")
    async def create_order_action(body: OrderActionBody, request: Request) -> dict[str, object]:
        require_trading_writes_allowed()
        actor = await principal(request, mutation=True)
        request_id = UUID(_request_id(request))
        now = datetime.now(UTC)
        if body.action == "cancel" and (body.size is not None or body.limit_price is not None):
            raise HTTPException(status_code=400, detail="invalid_cancel_shape")
        if body.action == "modify" and body.size is None:
            raise HTTPException(status_code=400, detail="invalid_modify_shape")
        async with database.transaction() as session:
            permission = await account_permission(session, actor, body.account_id)
            if actor.role == "viewer" or permission == "view":
                raise HTTPException(status_code=403, detail="trading_permission_required")
            replay = await session.scalar(
                select(LiveOrderAction).where(
                    LiveOrderAction.account_id == body.account_id,
                    LiveOrderAction.idempotency_key == request_id,
                )
            )
            if replay is not None:
                if (
                    replay.action != body.action.upper()
                    or replay.order_id != body.order_id
                    or replay.product_id != body.product_id
                ):
                    raise HTTPException(status_code=409, detail="idempotency_conflict")
                return {"actionId": str(replay.id), "status": replay.status}
            account = await session.get(Account, body.account_id)
            control = await session.scalar(
                select(AccountLiveControl).where(AccountLiveControl.account_id == body.account_id)
            )
            lease = await session.scalar(
                select(PrivateWorkerLease).where(PrivateWorkerLease.account_id == body.account_id)
            )
            reconciliation = await session.scalar(
                select(PrivateReconciliationSnapshot)
                .where(PrivateReconciliationSnapshot.account_id == body.account_id)
                .order_by(desc(PrivateReconciliationSnapshot.observed_at))
                .limit(1)
            )
            if (
                account is None
                or account.environment != "prod"
                or account.mode != "LIVE"
                or account.identity_verified_at is None
                or control is None
                or control.credential_state != "verified"
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
            ):
                raise HTTPException(status_code=409, detail="account_state_not_ready")
            try:
                open_orders = json.loads(reconciliation.open_orders_payload or "")
            except (TypeError, ValueError):
                raise HTTPException(status_code=409, detail="account_state_not_ready") from None
            if not isinstance(open_orders, list):
                raise HTTPException(status_code=409, detail="account_state_not_ready")

            def positive_int(value: object) -> int | None:
                if type(value) is int and value > 0:
                    return value
                if type(value) is str and value.isascii() and value.isdigit():
                    parsed = int(value)
                    return parsed if parsed > 0 else None
                return None

            target_order: dict[str, object] | None = None
            for candidate in open_orders:
                if not isinstance(candidate, dict):
                    raise HTTPException(status_code=409, detail="account_state_not_ready")
                if positive_int(candidate.get("id")) == body.order_id:
                    target_order = candidate
                    break
            if (
                target_order is None
                or positive_int(target_order.get("product_id")) != body.product_id
            ):
                raise HTTPException(status_code=409, detail="open_order_not_found")
            target_size: int | None = None
            target_price: Decimal | None = None
            if body.action == "modify":
                current_size = positive_int(target_order.get("size"))
                unfilled_size = positive_int(target_order.get("unfilled_size"))
                if current_size is None or unfilled_size is None or body.size is None:
                    raise HTTPException(status_code=409, detail="account_state_not_ready")
                already_filled = current_size - unfilled_size
                if body.size < max(1, already_filled) or body.size > current_size:
                    raise HTTPException(status_code=409, detail="modify_must_not_increase_risk")
                if not control.entries_enabled and target_order.get("reduce_only") is not True:
                    raise HTTPException(status_code=409, detail="entry_gate_closed")
                target_size = body.size
                if body.limit_price is not None:
                    product_row = await session.scalar(
                        select(CatalogueProduct)
                        .join(
                            CatalogueSnapshot,
                            CatalogueSnapshot.id == CatalogueProduct.snapshot_id,
                        )
                        .where(
                            CatalogueSnapshot.region == "india",
                            CatalogueSnapshot.environment == "prod",
                            CatalogueSnapshot.fresh_until > now,
                            CatalogueProduct.product_id == body.product_id,
                        )
                        .order_by(desc(CatalogueSnapshot.validated_at))
                        .limit(1)
                    )
                    try:
                        target_price = Decimal(body.limit_price)
                    except InvalidOperation:
                        raise HTTPException(status_code=400, detail="invalid_limit_price") from None
                    if (
                        product_row is None
                        or not target_price.is_finite()
                        or target_price <= 0
                        or target_price % product_row.to_product().tick_size != 0
                    ):
                        raise HTTPException(status_code=400, detail="invalid_limit_price")
            payload_values: dict[str, object] = {
                "id": body.order_id,
                "product_id": body.product_id,
            }
            if body.action == "modify":
                payload_values["size"] = target_size
                if target_price is not None:
                    payload_values["limit_price"] = format(target_price, "f")
            payload = json.dumps(
                payload_values, ensure_ascii=True, separators=(",", ":"), sort_keys=True
            )
            digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
            action = LiveOrderAction(
                id=uuid4(),
                account_id=body.account_id,
                created_by_user_id=actor.profile_id,
                idempotency_key=request_id,
                action=body.action.upper(),
                order_id=body.order_id,
                product_id=body.product_id,
                target_size=target_size,
                target_limit_price=target_price,
                request_payload=payload,
                request_sha256=digest,
                status="PENDING",
                worker_owner_id=None,
                worker_generation=None,
                credential_version=None,
                exchange_response_sha256=None,
                last_error_code=None,
                sent_at=None,
                resolved_at=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(action)
            await append_audit(
                session,
                actor=actor,
                action=f"LIVE_ORDER_{action.action}_QUEUED",
                target_type="order",
                target_id=str(body.order_id),
                outcome="SUCCEEDED",
                request_id=str(request_id),
                details={"accountId": str(body.account_id), "productId": body.product_id},
            )
            return {"actionId": str(action.id), "status": action.status}

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
                    "maxInflightSignals": row.max_inflight_signals,
                    "limitCurrency": row.limit_currency,
                    "usdInrRate": str(row.usd_inr_rate),
                    "maxOrderLimitInr": str(row.max_order_limit_inr),
                    "maxPositionLimitInr": str(row.max_position_limit_inr),
                    "maxDailyLossLimitInr": str(row.max_daily_loss_limit_inr),
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
        inr_limits = (
            _positive_decimal(body.max_order_notional),
            _positive_decimal(body.max_position_notional),
            _positive_decimal(body.max_daily_loss),
        )
        usd_inr_rate = _positive_decimal(body.usd_inr_rate)
        if (
            inr_limits != (Decimal("5000"), Decimal("10000"), Decimal("2000"))
            or usd_inr_rate != Decimal("100")
            or body.max_open_orders != 5
            or body.max_inflight_signals != 5
        ):
            raise HTTPException(status_code=409, detail="risk_scope_requires_requalification")
        limits = tuple(value / usd_inr_rate for value in inr_limits)
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
            product_symbols = set(
                await session.scalars(
                    select(CatalogueProduct.symbol).where(
                        CatalogueProduct.snapshot_id == catalogue.id,
                        CatalogueProduct.product_id.in_(product_ids),
                    )
                )
            )
            if product_symbols != {"BTCUSD", "ETHUSD"}:
                raise HTTPException(status_code=409, detail="symbol_not_allowed")
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
                    max_inflight_signals=body.max_inflight_signals,
                    limit_currency="INR",
                    usd_inr_rate=usd_inr_rate,
                    max_order_limit_inr=inr_limits[0],
                    max_position_limit_inr=inr_limits[1],
                    max_daily_loss_limit_inr=inr_limits[2],
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
                row.max_inflight_signals = body.max_inflight_signals
                row.limit_currency = "INR"
                row.usd_inr_rate = usd_inr_rate
                (
                    row.max_order_limit_inr,
                    row.max_position_limit_inr,
                    row.max_daily_loss_limit_inr,
                ) = inr_limits
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
        if body.entries_enabled:
            require_trading_writes_allowed()
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
        if body.entries_enabled or body.exits_enabled:
            require_trading_writes_allowed()
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
                (
                    qualification_ready,
                    qualification_reason,
                    _,
                    stream_rows,
                ) = await qualification_status(
                    session,
                    account_id=account_id,
                    now=now,
                )
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
                    and qualification_ready
                    and all(
                        row.credential_version == lease.credential_version
                        and row.worker_owner_id == lease.owner_id
                        and row.worker_generation == lease.generation
                        for row in stream_rows
                    )
                )
                if not qualification_ready:
                    raise HTTPException(status_code=409, detail=qualification_reason)
                # Stage the qualified account gate first. Order admission still
                # requires the independently locked global gate, enabled last.
                if not ready:
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
                .where(Account.archived_at.is_(None))
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

    @app.get("/api/v1/admin/accounts/{account_id}/qualification")
    async def account_qualification(account_id: UUID, request: Request) -> dict[str, object]:
        await admin(request)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            account = await session.get(Account, account_id)
            if account is None or account.archived_at is not None:
                raise HTTPException(status_code=404, detail="account_not_found")
            ready, reason, evidence, streams = await qualification_status(
                session,
                account_id=account_id,
                now=now,
            )
            by_channel = {row.channel: row for row in streams}
            return {
                "accountId": str(account_id),
                "qualified": ready,
                "reason": reason,
                "observedAt": _utc(now),
                "requiredWindowHours": 24,
                "evidence": None
                if evidence is None
                else {
                    "releaseCommit": evidence.release_commit,
                    "evidenceSha256": evidence.evidence_sha256,
                    "status": evidence.status,
                    "windowStartedAt": _utc(evidence.window_started_at),
                    "windowCompletedAt": None
                    if evidence.window_completed_at is None
                    else _utc(evidence.window_completed_at),
                    "approvedAt": None
                    if evidence.approved_at is None
                    else _utc(evidence.approved_at),
                },
                "privateChannels": [
                    {
                        "channel": channel,
                        "state": "missing"
                        if channel not in by_channel
                        else by_channel[channel].state,
                        "sequence": None
                        if channel not in by_channel
                        else by_channel[channel].sequence_number,
                        "observedAt": None
                        if channel not in by_channel
                        else _utc(by_channel[channel].observed_at),
                    }
                    for channel in ("orders", "positions", "margins", "v2/user_trades")
                ],
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
                        "protectionGroupId": None
                        if row.protection_group_id is None
                        else str(row.protection_group_id),
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

    @app.get("/api/v1/strategies")
    async def list_strategies(request: Request, account_id: UUID) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            await account_permission(session, actor, account_id)
            rows = list(
                (
                    await session.execute(
                        select(TradingViewStrategy)
                        .where(TradingViewStrategy.account_id == account_id)
                        .order_by(desc(TradingViewStrategy.created_at))
                        .limit(500)
                    )
                ).scalars()
            )
            active_keys = {
                row.strategy_id: row.key_fingerprint
                for row in (
                    await session.execute(
                        select(TradingViewWebhookKey)
                        .join(
                            TradingViewStrategy,
                            TradingViewStrategy.id == TradingViewWebhookKey.strategy_id,
                        )
                        .where(
                            TradingViewStrategy.account_id == account_id,
                            TradingViewWebhookKey.revoked_at.is_(None),
                        )
                    )
                ).scalars()
            }
            return {
                "strategies": [
                    {
                        "id": str(row.id),
                        "accountId": str(row.account_id),
                        "name": row.name,
                        "productId": row.product_id,
                        "symbol": row.symbol,
                        "contracts": row.contracts,
                        "orderType": row.order_type,
                        "reduceOnly": row.reduce_only,
                        "stopLossPct": str(row.stop_loss_pct),
                        "targetPct": str(row.target_pct),
                        "trailingOffsetPct": str(row.trailing_offset_pct),
                        "protectionRequired": row.protection_required,
                        "enabled": row.enabled,
                        "version": row.version,
                        "webhookConfigured": row.id in active_keys,
                        "webhookFingerprint": active_keys.get(row.id),
                        "updatedAt": _utc(row.updated_at),
                    }
                    for row in rows
                ]
            }

    @app.get("/api/v1/strategies/signals")
    async def list_strategy_signals(request: Request, account_id: UUID) -> dict[str, object]:
        actor = await principal(request)
        async with database.transaction() as session:
            await account_permission(session, actor, account_id)
            rows = (
                await session.execute(
                    select(TradingViewSignal, TradingViewStrategy.name)
                    .join(
                        TradingViewStrategy,
                        TradingViewStrategy.id == TradingViewSignal.strategy_id,
                    )
                    .where(TradingViewSignal.account_id == account_id)
                    .order_by(desc(TradingViewSignal.received_at))
                    .limit(500)
                )
            ).all()
            return {
                "signals": [
                    {
                        "id": str(signal.id),
                        "strategyId": str(signal.strategy_id),
                        "strategyName": strategy_name,
                        "externalSignalId": signal.external_signal_id,
                        "side": signal.side,
                        "limitPrice": signal.limit_price,
                        "status": signal.status,
                        "rejectionCode": signal.rejection_code,
                        "intentId": None if signal.intent_id is None else str(signal.intent_id),
                        "receivedAt": _utc(signal.received_at),
                        "processedAt": (
                            None if signal.processed_at is None else _utc(signal.processed_at)
                        ),
                    }
                    for signal, strategy_name in rows
                ]
            }

    @app.post("/api/v1/strategies")
    async def create_strategy(body: TradingViewStrategyBody, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        request_id = _request_id(request)
        now, strategy_id = datetime.now(UTC), uuid4()
        protection_values = (
            _positive_decimal(body.stop_loss_pct),
            _positive_decimal(body.target_pct),
            _positive_decimal(body.trailing_offset_pct),
        )
        if any(value > 100 for value in protection_values):
            raise HTTPException(status_code=400, detail="invalid_protection")
        async with database.transaction() as session:
            permission = await account_permission(session, actor, body.account_id)
            if actor.role != "admin" and permission != "manage":
                raise HTTPException(status_code=403, detail="permission_denied")
            if not body.protection_required:
                raise HTTPException(status_code=409, detail="protection_required")
            if body.symbol not in {"BTCUSD", "ETHUSD"}:
                raise HTTPException(status_code=409, detail="symbol_not_allowed")
            product = await session.scalar(
                select(CatalogueProduct)
                .join(CatalogueSnapshot, CatalogueSnapshot.id == CatalogueProduct.snapshot_id)
                .where(
                    CatalogueSnapshot.region == "india",
                    CatalogueSnapshot.environment == "prod",
                    CatalogueSnapshot.fresh_until > now,
                    CatalogueProduct.product_id == body.product_id,
                    CatalogueProduct.symbol == body.symbol,
                )
                .order_by(desc(CatalogueSnapshot.validated_at))
                .limit(1)
            )
            if product is None:
                raise HTTPException(status_code=409, detail="strategy_product_not_ready")
            session.add(
                TradingViewStrategy(
                    id=strategy_id,
                    account_id=body.account_id,
                    created_by_user_id=actor.profile_id,
                    name=body.name,
                    product_id=body.product_id,
                    symbol=body.symbol,
                    contracts=body.contracts,
                    order_type=body.order_type,
                    reduce_only=body.reduce_only,
                    stop_loss_pct=protection_values[0],
                    target_pct=protection_values[1],
                    trailing_offset_pct=protection_values[2],
                    protection_required=body.protection_required,
                    enabled=body.enabled,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await append_audit(
                session,
                actor=actor,
                action="TRADINGVIEW_STRATEGY_CREATED",
                target_type="strategy",
                target_id=str(strategy_id),
                outcome="SUCCEEDED",
                request_id=request_id,
                details={"accountId": str(body.account_id), "enabled": body.enabled},
            )
        return {"id": str(strategy_id), "enabled": body.enabled}

    @app.patch("/api/v1/strategies/{strategy_id}")
    async def update_strategy_state(
        strategy_id: UUID, body: TradingViewStrategyStateBody, request: Request
    ) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        now = datetime.now(UTC)
        async with database.transaction() as session:
            strategy = await session.get(TradingViewStrategy, strategy_id)
            if strategy is None:
                raise HTTPException(status_code=404, detail="strategy_not_found")
            permission = await account_permission(session, actor, strategy.account_id)
            if actor.role != "admin" and permission != "manage":
                raise HTTPException(status_code=403, detail="permission_denied")
            if body.protection_required is False:
                raise HTTPException(status_code=409, detail="protection_required")
            if (body.product_id is None) != (body.symbol is None):
                raise HTTPException(status_code=400, detail="strategy_product_incomplete")
            if body.product_id is not None and body.symbol is not None:
                product = await session.scalar(
                    select(CatalogueProduct)
                    .join(CatalogueSnapshot, CatalogueSnapshot.id == CatalogueProduct.snapshot_id)
                    .where(
                        CatalogueSnapshot.region == "india",
                        CatalogueSnapshot.environment == "prod",
                        CatalogueSnapshot.fresh_until > now,
                        CatalogueProduct.product_id == body.product_id,
                        CatalogueProduct.symbol == body.symbol,
                    )
                    .order_by(desc(CatalogueSnapshot.validated_at))
                    .limit(1)
                )
                if product is None:
                    raise HTTPException(status_code=409, detail="strategy_product_not_ready")
                if body.symbol not in {"BTCUSD", "ETHUSD"}:
                    raise HTTPException(status_code=409, detail="symbol_not_allowed")
                strategy.product_id = body.product_id
                strategy.symbol = body.symbol
            for field in (
                "name",
                "contracts",
                "order_type",
                "reduce_only",
                "enabled",
                "protection_required",
            ):
                value = getattr(body, field)
                if value is not None:
                    setattr(strategy, field, value)
            for field in ("stop_loss_pct", "target_pct", "trailing_offset_pct"):
                value = getattr(body, field)
                if value is not None:
                    parsed = _positive_decimal(value)
                    if parsed > 100:
                        raise HTTPException(status_code=400, detail="invalid_protection")
                    setattr(strategy, field, parsed)
            strategy.version += 1
            strategy.updated_at = now
            await append_audit(
                session,
                actor=actor,
                action="TRADINGVIEW_STRATEGY_UPDATED",
                target_type="strategy",
                target_id=str(strategy.id),
                outcome="SUCCEEDED",
                request_id=_request_id(request),
            )
        return {"id": str(strategy_id), "enabled": strategy.enabled}

    @app.post("/api/v1/strategies/{strategy_id}/webhook-key")
    async def rotate_strategy_key(strategy_id: UUID, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        if actor.last_login_at is None or datetime.now(UTC) - actor.last_login_at > timedelta(
            minutes=15
        ):
            raise HTTPException(status_code=403, detail="fresh_session_required")
        raw_key = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw_key.encode("ascii")).hexdigest()
        now = datetime.now(UTC)
        async with database.transaction() as session:
            strategy = await session.get(TradingViewStrategy, strategy_id)
            if strategy is None:
                raise HTTPException(status_code=404, detail="strategy_not_found")
            permission = await account_permission(session, actor, strategy.account_id)
            if actor.role == "viewer" or permission == "view":
                raise HTTPException(status_code=403, detail="trading_permission_required")
            active = (
                await session.execute(
                    select(TradingViewWebhookKey).where(
                        TradingViewWebhookKey.strategy_id == strategy_id,
                        TradingViewWebhookKey.revoked_at.is_(None),
                    )
                )
            ).scalars()
            for row in active:
                row.revoked_at = now
            session.add(
                TradingViewWebhookKey(
                    id=uuid4(),
                    strategy_id=strategy_id,
                    key_sha256=digest,
                    key_fingerprint=digest[:12],
                    created_by_user_id=actor.profile_id,
                    created_at=now,
                    revoked_at=None,
                )
            )
            await append_audit(
                session,
                actor=actor,
                action="TRADINGVIEW_KEY_ROTATED",
                target_type="strategy",
                target_id=str(strategy_id),
                outcome="SUCCEEDED",
                request_id=_request_id(request),
                details={"fingerprint": digest[:12]},
            )
        return {
            "webhookPath": f"/api/v1/webhooks/tradingview/{raw_key}",
            "fingerprint": digest[:12],
            "shownOnce": True,
        }

    @app.delete("/api/v1/strategies/{strategy_id}/webhook-key")
    async def revoke_strategy_key(strategy_id: UUID, request: Request) -> dict[str, object]:
        actor = await principal(request, mutation=True)
        if actor.last_login_at is None or datetime.now(UTC) - actor.last_login_at > timedelta(
            minutes=15
        ):
            raise HTTPException(status_code=403, detail="fresh_session_required")
        now = datetime.now(UTC)
        async with database.transaction() as session:
            strategy = await session.get(TradingViewStrategy, strategy_id)
            if strategy is None:
                raise HTTPException(status_code=404, detail="strategy_not_found")
            permission = await account_permission(session, actor, strategy.account_id)
            if actor.role == "viewer" or permission == "view":
                raise HTTPException(status_code=403, detail="trading_permission_required")
            active = list(
                (
                    await session.execute(
                        select(TradingViewWebhookKey).where(
                            TradingViewWebhookKey.strategy_id == strategy_id,
                            TradingViewWebhookKey.revoked_at.is_(None),
                        )
                    )
                ).scalars()
            )
            for row in active:
                row.revoked_at = now
            await append_audit(
                session,
                actor=actor,
                action="TRADINGVIEW_KEY_REVOKED",
                target_type="strategy",
                target_id=str(strategy.id),
                outcome="SUCCEEDED",
                request_id=_request_id(request),
                details={"revokedCount": len(active)},
            )
        return {"id": str(strategy_id), "revoked": len(active)}

    @app.post("/api/v1/webhooks/tradingview/{key}")
    async def tradingview_webhook(key: str, body: TradingViewSignalBody) -> JSONResponse:
        require_trading_writes_allowed()
        if len(key) < 32 or len(key) > 128 or re.fullmatch(r"[A-Za-z0-9_-]+", key) is None:
            raise HTTPException(status_code=404, detail="webhook_not_found")
        now = datetime.now(UTC)
        try:
            signal_time = (
                datetime.fromtimestamp(body.timestamp, tz=UTC)
                if isinstance(body.timestamp, int)
                else datetime.fromisoformat(body.timestamp.replace("Z", "+00:00"))
            )
            if signal_time.tzinfo is None:
                raise ValueError
            signal_time = signal_time.astimezone(UTC)
        except (OSError, OverflowError, ValueError):
            raise HTTPException(status_code=400, detail="invalid_signal_timestamp") from None
        if abs((now - signal_time).total_seconds()) > 60:
            raise HTTPException(status_code=409, detail="stale_signal")
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", body.signal_id) is None:
            raise HTTPException(status_code=400, detail="invalid_signal_id")
        if body.limit_price is not None:
            try:
                limit = Decimal(body.limit_price)
            except InvalidOperation:
                raise HTTPException(status_code=400, detail="invalid_limit_price") from None
            if not limit.is_finite() or limit <= 0:
                raise HTTPException(status_code=400, detail="invalid_limit_price")
        digest = hashlib.sha256(key.encode("ascii")).hexdigest()
        payload_digest = hashlib.sha256(
            json.dumps(
                {
                    "limitPrice": body.limit_price,
                    "side": body.side,
                    "signalId": body.signal_id,
                    "timestamp": body.timestamp,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        ).hexdigest()
        async with database.transaction() as session:
            webhook_key = await session.scalar(
                select(TradingViewWebhookKey).where(
                    TradingViewWebhookKey.key_sha256 == digest,
                    TradingViewWebhookKey.revoked_at.is_(None),
                )
            )
            strategy = (
                None
                if webhook_key is None
                else await session.get(TradingViewStrategy, webhook_key.strategy_id)
            )
            if strategy is None or not strategy.enabled:
                raise HTTPException(status_code=404, detail="webhook_not_found")
            if not strategy.protection_required:
                raise HTTPException(status_code=409, detail="protection_required")
            qualified, reason, _, _ = await qualification_status(
                session,
                account_id=strategy.account_id,
                now=now,
            )
            if not qualified:
                raise HTTPException(status_code=409, detail=reason)
            risk = await session.scalar(
                select(AccountRiskPolicy).where(AccountRiskPolicy.account_id == strategy.account_id)
            )
            inflight = await session.scalar(
                select(func.count(TradingViewSignal.id)).where(
                    TradingViewSignal.account_id == strategy.account_id,
                    TradingViewSignal.status.in_(("received", "admitted")),
                )
            )
            if risk is None or int(inflight or 0) >= risk.max_inflight_signals:
                raise HTTPException(status_code=409, detail="signal_concurrency_exceeded")
            if (strategy.order_type == "limit") != (body.limit_price is not None):
                raise HTTPException(status_code=409, detail="strategy_signal_incompatible")
            existing = await session.scalar(
                select(TradingViewSignal).where(
                    TradingViewSignal.strategy_id == strategy.id,
                    TradingViewSignal.external_signal_id == body.signal_id,
                )
            )
            if existing is not None:
                if existing.payload_sha256 != payload_digest:
                    raise HTTPException(status_code=409, detail="signal_id_conflict")
                return JSONResponse(status_code=202, content={"status": "duplicate"})
            signal_id = uuid4()
            session.add(
                TradingViewSignal(
                    id=signal_id,
                    strategy_id=strategy.id,
                    account_id=strategy.account_id,
                    external_signal_id=body.signal_id,
                    side=body.side,
                    limit_price=body.limit_price,
                    payload_sha256=payload_digest,
                    status="received",
                    rejection_code=None,
                    intent_id=None,
                    received_at=now,
                    processed_at=None,
                )
            )
        return JSONResponse(status_code=202, content={"status": "received"})

    @app.post("/api/v1/gtt")
    async def create_gtt(body: GttBody, request: Request) -> dict[str, object]:
        require_trading_writes_allowed()
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
                    trail_anchor=None,
                    last_quote_id=None,
                    reduce_only=True,
                    status="armed",
                    idempotency_key=UUID(request_id),
                    triggered_order_id=None,
                    triggered_at=None,
                    trigger_evidence_sha256=None,
                    last_error_code=None,
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
        require_trading_writes_allowed()
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
