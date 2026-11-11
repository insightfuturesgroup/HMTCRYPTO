"""Secure same-origin FastAPI composition for the operator control plane."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from hmt_crypto.api.control_plane import (
    ControlPlane,
    ControlPlaneUnavailable,
    KillCommand,
    UnavailableControlPlane,
)
from hmt_crypto.api.database_control_plane import DatabaseControlPlane
from hmt_crypto.api.operator_audit import (
    DatabaseOperatorAuditSink,
    OperatorActionVariant,
    OperatorAuditRecord,
    OperatorAuditSink,
    OperatorEventKind,
    OperatorOutcome,
    UnavailableOperatorAuditSink,
)
from hmt_crypto.api.platform_api import install_platform_routes
from hmt_crypto.api.platform_auth import SupabaseAuthGateway
from hmt_crypto.api.security import KillPreviewRegistry, LoginRateLimiter
from hmt_crypto.api.session_store import (
    Argon2PasswordVerifier,
    AuthenticatedSession,
    OperatorSessionStore,
)
from hmt_crypto.api.settings import ControlApiSettings, ControlRuntimeSettings
from hmt_crypto.api.views import command_view, dashboard_view, health_view, utc_text
from hmt_crypto.config import ExecutionMode
from hmt_crypto.observability.metrics import ApiMetrics
from hmt_crypto.oms.kill_worker import KillWorkerError, KillWorkflowWorker
from hmt_crypto.oms.paper_kill_backend import DurablePaperKillActionBackend
from hmt_crypto.persistence.configuration import database_url
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.repository_contracts import AccountScope

_SESSION_COOKIE = "__Host-hmt_session"
_CSRF_COOKIE = "__Host-hmt_csrf"
_LOCAL_SESSION_COOKIE = "hmt_session"
_LOCAL_CSRF_COOKIE = "hmt_csrf"
_FLATTEN_CONFIRMATION = "FLATTEN_ALL_POSITIONS"


def _safe_error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code})


async def _read_json_object(request: Request, maximum: int) -> dict[str, object]:
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > maximum:
                raise ValueError
            body.extend(chunk)
        value = json.loads(bytes(body))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_request") from None
    finally:
        for index in range(len(body)):
            body[index] = 0
    if type(value) is not dict or len(value) > 16 or any(type(key) is not str for key in value):
        raise HTTPException(status_code=400, detail="invalid_request") from None
    return value


def _exact_keys(value: dict[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise HTTPException(status_code=400, detail="invalid_request") from None


def _parse_uuid(value: object) -> UUID:
    if type(value) is not str or len(value) != 36:
        raise HTTPException(status_code=400, detail="invalid_request") from None
    try:
        parsed = UUID(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_request") from None
    if parsed.int == 0 or str(parsed) != value:
        raise HTTPException(status_code=400, detail="invalid_request") from None
    return parsed


def create_app(
    settings: ControlApiSettings,
    *,
    control_plane: ControlPlane | None = None,
    operator_audit: OperatorAuditSink | None = None,
    background: Callable[[asyncio.Event], Awaitable[None]] | None = None,
    shutdown: Callable[[], Awaitable[None]] | None = None,
    platform_database: Database | None = None,
) -> FastAPI:
    """Build a fail-closed single-operator API with explicit injected authority."""
    if type(settings) is not ControlApiSettings:
        raise TypeError("settings must be an exact ControlApiSettings")
    control = control_plane or UnavailableControlPlane()
    audit_sink = operator_audit or UnavailableOperatorAuditSink()
    verifier = Argon2PasswordVerifier(settings.operator_password_hash.get_secret_value())
    sessions = OperatorSessionStore(
        idle_timeout=timedelta(seconds=settings.session_idle_seconds),
        absolute_timeout=timedelta(seconds=settings.session_absolute_seconds),
        max_sessions=settings.max_sessions,
    )
    previews = KillPreviewRegistry()
    login_limiter = LoginRateLimiter(settings.login_attempts_per_five_minutes)
    login_verification_gate = asyncio.Semaphore(2)
    metrics = ApiMetrics()
    platform_auth: SupabaseAuthGateway | None = None
    secure = settings.cookie_secure
    session_cookie = _SESSION_COOKIE if secure else _LOCAL_SESSION_COOKIE
    csrf_cookie = _CSRF_COOKIE if secure else _LOCAL_CSRF_COOKIE

    async def audit(
        event_kind: OperatorEventKind,
        outcome: OperatorOutcome,
        *,
        session_id: UUID | None = None,
        correlation_id: UUID | None = None,
        action_variant: OperatorActionVariant | None = None,
    ) -> None:
        await audit_sink.append(
            OperatorAuditRecord(
                event_kind=event_kind,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
                session_id=session_id,
                correlation_id=correlation_id,
                action_variant=action_variant,
            )
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task: asyncio.Future[None] | None = (
            None if background is None else asyncio.ensure_future(background(stop))
        )
        try:
            yield
        finally:
            if task is not None:
                stop.set()
                try:
                    async with asyncio.timeout(10):
                        await task
                except asyncio.CancelledError:
                    raise
                except Exception:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            if platform_auth is not None:
                await platform_auth.aclose()
            if shutdown is not None:
                await shutdown()

    app = FastAPI(
        title="HMT-Crypto Control API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.monotonic()
        try:
            response: Response | None = None
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) < 0 or int(content_length) > settings.max_request_bytes:
                        response = _safe_error(413, "request_too_large")
                except ValueError:
                    response = _safe_error(400, "invalid_request")
            if response is None:
                try:
                    response = await call_next(request)
                except ControlPlaneUnavailable:
                    response = _safe_error(503, "control_plane_unavailable")
        except BaseException:
            route = getattr(request.scope.get("route"), "path", "/unmatched")
            if type(route) is not str or not route.startswith("/"):
                route = "/unmatched"
            metrics.observe(
                method=request.method,
                route=route,
                status=500,
                elapsed=float(max(0.0, time.monotonic() - started)),
            )
            raise
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        if secure:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        route = getattr(request.scope.get("route"), "path", "/unmatched")
        if type(route) is not str or not route.startswith("/"):
            route = "/unmatched"
        metrics.observe(
            method=request.method,
            route=route,
            status=response.status_code,
            elapsed=float(max(0.0, time.monotonic() - started)),
        )
        return response

    def require_origin(request: Request) -> None:
        if request.headers.get("origin") != settings.allowed_origin:
            raise HTTPException(status_code=403, detail="origin_rejected") from None

    async def require_session(request: Request) -> AuthenticatedSession:
        token = request.cookies.get(session_cookie)
        authenticated = await sessions.authenticate(token)
        if authenticated is None:
            raise HTTPException(status_code=401, detail="authentication_required") from None
        return authenticated

    async def require_mutation(request: Request) -> AuthenticatedSession:
        require_origin(request)
        authenticated = await require_session(request)
        if not await sessions.authorize_csrf(
            authenticated,
            session_token=request.cookies.get(session_cookie),
            csrf_cookie=request.cookies.get(csrf_cookie),
            csrf_header=request.headers.get("x-csrf-token"),
        ):
            raise HTTPException(status_code=403, detail="csrf_rejected") from None
        return authenticated

    def set_session_cookies(response: Response, session_token: str, csrf_token: str) -> None:
        response.set_cookie(
            session_cookie,
            session_token,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/",
            max_age=settings.session_absolute_seconds,
        )
        response.set_cookie(
            csrf_cookie,
            csrf_token,
            httponly=False,
            secure=secure,
            samesite="strict",
            path="/",
            max_age=settings.session_absolute_seconds,
        )

    def clear_session_cookies(response: Response) -> None:
        response.delete_cookie(session_cookie, path="/", secure=secure, samesite="strict")
        response.delete_cookie(csrf_cookie, path="/", secure=secure, samesite="strict")

    @app.get("/api/v1/health/live")
    async def live() -> dict[str, bool]:
        return {"live": True}

    @app.get("/api/v1/health/ready")
    async def ready() -> JSONResponse:
        snapshot = await control.health()
        return JSONResponse(
            status_code=200 if snapshot.ready else 503,
            content=health_view(snapshot),
        )

    @app.get("/api/v1/session")
    async def session_status(request: Request) -> JSONResponse:
        token = request.cookies.get(session_cookie)
        csrf = request.cookies.get(csrf_cookie)
        authenticated = await sessions.authenticate(token)
        if authenticated is None or not await sessions.authorize_csrf(
            authenticated,
            session_token=token,
            csrf_cookie=csrf,
            csrf_header=csrf,
        ):
            response = JSONResponse(
                {"authenticated": False, "csrfToken": None, "absoluteExpiresAt": None}
            )
            clear_session_cookies(response)
            return response
        return JSONResponse(
            {
                "authenticated": True,
                "csrfToken": csrf,
                "absoluteExpiresAt": utc_text(authenticated.record.absolute_expires_at),
            }
        )

    @app.post("/api/v1/session")
    async def login(request: Request) -> JSONResponse:
        require_origin(request)
        peer = request.client.host if request.client is not None else "unknown"
        body = await _read_json_object(request, settings.max_request_bytes)
        _exact_keys(body, {"password"})
        password = body.get("password")
        try:
            async with login_verification_gate:
                accepted = await verifier.verify_async(password)
        finally:
            body.clear()
            password = None
        if not accepted:
            admitted = await login_limiter.record_failure(peer)
            await audit("LOGIN", "REJECTED" if admitted else "RATE_LIMITED")
            if not admitted:
                return _safe_error(429, "authentication_rate_limited")
            return _safe_error(401, "authentication_failed")
        await login_limiter.clear(peer)
        issued = await sessions.issue()
        try:
            await audit("LOGIN", "SUCCEEDED", session_id=issued.record.session_id)
        except BaseException:
            authenticated = await sessions.authenticate(issued.session_token)
            if authenticated is not None:
                await sessions.revoke(authenticated)
            raise
        response = JSONResponse(
            {
                "authenticated": True,
                "csrfToken": issued.csrf_token,
                "absoluteExpiresAt": utc_text(issued.record.absolute_expires_at),
            }
        )
        set_session_cookies(response, issued.session_token, issued.csrf_token)
        return response

    @app.delete("/api/v1/session")
    async def logout(request: Request) -> JSONResponse:
        authenticated = await require_mutation(request)
        await audit("LOGOUT", "SUCCEEDED", session_id=authenticated.record.session_id)
        await sessions.revoke(authenticated)
        response = JSONResponse({"authenticated": False})
        clear_session_cookies(response)
        return response

    @app.get("/api/v1/dashboard")
    async def dashboard(request: Request) -> dict[str, object]:
        authenticated = await require_session(request)
        snapshot = await control.dashboard()
        await audit("DASHBOARD_READ", "SUCCEEDED", session_id=authenticated.record.session_id)
        return dashboard_view(snapshot)

    @app.post("/api/v1/commands/kill/preview")
    async def preview_kill(request: Request) -> dict[str, object]:
        authenticated = await require_mutation(request)
        body = await _read_json_object(request, settings.max_request_bytes)
        _exact_keys(body, {"flatten"})
        flatten = body.get("flatten")
        if type(flatten) is not bool:
            raise HTTPException(status_code=400, detail="invalid_request") from None
        impact = await control.preview_kill(flatten=flatten)
        action_variant: OperatorActionVariant = "CANCEL_AND_FLATTEN" if flatten else "CANCEL_ONLY"
        await audit(
            "KILL_PREVIEW",
            "SUCCEEDED",
            session_id=authenticated.record.session_id,
            action_variant=action_variant,
        )
        preview = await previews.issue(
            session_id=authenticated.record.session_id,
            flatten=flatten,
            impact=impact,
        )
        return {
            "previewId": str(preview.preview_id),
            "expiresAt": utc_text(preview.expires_at),
            "flatten": preview.flatten,
            "openOrderCount": impact.open_order_count,
            "positionCount": impact.position_count,
            "evidenceState": impact.evidence_state,
            "evidenceObservedAt": (
                None
                if impact.evidence_observed_at is None
                else utc_text(impact.evidence_observed_at)
            ),
            "warning": impact.warning,
        }

    @app.post("/api/v1/commands/kill")
    async def engage_kill(request: Request) -> dict[str, object]:
        authenticated = await require_mutation(request)
        request_id = _parse_uuid(request.headers.get("idempotency-key"))
        body = await _read_json_object(request, settings.max_request_bytes)
        _exact_keys(body, {"previewId", "flatten", "confirmation"})
        preview_id = _parse_uuid(body.get("previewId"))
        flatten = body.get("flatten")
        confirmation = body.get("confirmation")
        if type(flatten) is not bool or (
            confirmation is not None and type(confirmation) is not str
        ):
            raise HTTPException(status_code=400, detail="invalid_request") from None
        if flatten:
            if confirmation != _FLATTEN_CONFIRMATION:
                raise HTTPException(status_code=400, detail="flatten_confirmation_required")
        elif confirmation is not None:
            raise HTTPException(status_code=400, detail="invalid_request") from None
        preview = await previews.bind_request(
            preview_id=preview_id,
            session_id=authenticated.record.session_id,
            flatten=flatten,
            request_id=request_id,
        )
        if preview is None:
            raise HTTPException(status_code=409, detail="preview_invalid_or_expired") from None
        receipt = await control.enqueue_kill(
            KillCommand(
                command_id=request_id,
                request_id=request_id,
                preview_id=preview.preview_id,
                flatten=flatten,
                accepted_at=datetime.now(UTC),
            )
        )
        if receipt.command_id != request_id:
            await audit(
                "KILL_ENQUEUE",
                "CONFLICT",
                session_id=authenticated.record.session_id,
                correlation_id=request_id,
                action_variant="CANCEL_AND_FLATTEN" if flatten else "CANCEL_ONLY",
            )
            raise HTTPException(status_code=409, detail="kill_already_active") from None
        await audit(
            "KILL_ENQUEUE",
            "SUCCEEDED",
            session_id=authenticated.record.session_id,
            correlation_id=request_id,
            action_variant="CANCEL_AND_FLATTEN" if flatten else "CANCEL_ONLY",
        )
        return command_view(receipt)

    @app.get("/api/v1/commands/{command_id}")
    async def get_command(command_id: str, request: Request) -> dict[str, object]:
        authenticated = await require_session(request)
        parsed_id = _parse_uuid(command_id)
        command = await control.command_status(parsed_id)
        await audit(
            "COMMAND_STATUS",
            "NOT_FOUND" if command is None else "SUCCEEDED",
            session_id=authenticated.record.session_id,
            correlation_id=parsed_id,
        )
        if command is None:
            raise HTTPException(status_code=404, detail="command_not_found") from None
        return command_view(command)

    @app.get("/api/v1/commands/kill/active")
    async def active_kill(request: Request) -> dict[str, object]:
        authenticated = await require_session(request)
        active = await control.active_kill()
        await audit(
            "ACTIVE_KILL_STATUS",
            "NOT_FOUND" if active is None else "SUCCEEDED",
            session_id=authenticated.record.session_id,
            correlation_id=None if active is None else active.command_id,
        )
        return {"active": None if active is None else command_view(active)}

    @app.get("/api/v1/openapi.json")
    async def openapi(request: Request) -> dict[str, object]:
        await require_session(request)
        schema: dict[str, object] = app.openapi()
        return schema

    @app.get("/api/v1/metrics")
    async def prometheus_metrics(request: Request) -> Response:
        await require_session(request)
        return Response(
            content=metrics.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    if settings.multi_user_enabled:
        if platform_database is None:
            raise RuntimeError("multi-user API requires the runtime database")
        platform_auth = install_platform_routes(
            app,
            settings=settings,
            database=platform_database,
        )

    return app


def create_app_from_environment() -> FastAPI:
    """Load environment settings and compose a fail-closed API process."""
    api_settings = ControlApiSettings()  # type: ignore[call-arg]
    if not api_settings.database_control_enabled:
        return create_app(api_settings)
    account_id = api_settings.account_id
    if account_id is None:
        raise RuntimeError("database control account is unavailable")
    runtime_settings = ControlRuntimeSettings()
    database = Database(runtime_settings.database_runtime_config())
    control = DatabaseControlPlane(
        database,
        runtime_settings,
        account_id,
    )
    scope = AccountScope(
        account_id=account_id,
        region=runtime_settings.delta_region.value,
        environment=runtime_settings.delta_env.value,
        mode=runtime_settings.execution_mode.value,
    )
    audit_sink = DatabaseOperatorAuditSink(database, scope)
    background: Callable[[asyncio.Event], Awaitable[None]] | None = None
    if api_settings.embed_paper_worker:
        runtime_database = database_url(runtime_settings.database_url)
        if (
            runtime_settings.execution_mode is ExecutionMode.LIVE
            or runtime_database.drivername != "sqlite+aiosqlite"
            or not runtime_settings.database_single_process
        ):
            raise RuntimeError("embedded worker requires single-process SQLite PAPER/BACKTEST")
        backend = DurablePaperKillActionBackend(database, scope)
        worker = KillWorkflowWorker(
            database,
            scope,
            owner_id=uuid4(),
            authority=backend.action_authority(),
        )

        async def run_embedded_paper_worker(stop: asyncio.Event) -> None:
            while not stop.is_set():
                delay = 0.25
                try:
                    result = await worker.run_once()
                    if result.disposition != "IDLE":
                        delay = 0.0
                except asyncio.CancelledError:
                    raise
                except KillWorkerError:
                    delay = 1.0
                if delay <= 0:
                    await asyncio.sleep(0)
                    continue
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass

        background = run_embedded_paper_worker
    return create_app(
        api_settings,
        control_plane=control,
        operator_audit=audit_sink,
        background=background,
        shutdown=control.aclose,
        platform_database=database,
    )


__all__ = ["create_app", "create_app_from_environment"]
