from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pydantic import SecretStr

from hmt_crypto.api.app import create_app
from hmt_crypto.api.control_plane import (
    ComponentHealth,
    ControlPlaneUnavailable,
    DashboardSnapshot,
    HealthSnapshot,
    KillCommand,
    KillImpact,
    QueuedCommand,
)
from hmt_crypto.api.operator_audit import OperatorAuditRecord
from hmt_crypto.api.settings import ControlApiSettings
from hmt_crypto.errors import ConfigurationError

ORIGIN = "https://console.example"
PASSWORD = "correct horse battery staple"
NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


class RecordingOperatorAudit:
    def __init__(self) -> None:
        self.records: list[OperatorAuditRecord] = []

    async def append(self, record: OperatorAuditRecord) -> None:
        self.records.append(record)


class FakeControlPlane:
    def __init__(self) -> None:
        self.commands: dict[UUID, QueuedCommand] = {}
        self.dashboard_available = True
        self.preview_calls: list[bool] = []

    async def health(self) -> HealthSnapshot:
        component = ComponentHealth(
            component="reconciliation", state="ready", reason="current", observed_at=NOW
        )
        return HealthSnapshot(
            live=True, ready=True, sequence=7, observed_at=NOW, components=(component,)
        )

    async def dashboard(self) -> DashboardSnapshot:
        if not self.dashboard_available:
            raise ControlPlaneUnavailable() from None
        return DashboardSnapshot(
            sequence=8,
            generated_at=NOW,
            evidence_observed_at=NOW,
            region="india",
            environment="testnet",
            mode="PAPER",
            account_label="paper-primary",
            catalogue_fresh=True,
            stream_connected=True,
            reconciliation_state="READY_CLAIM",
            risk_halted=False,
            collateral_asset="INR",
            equity="1000.00",
            available_balance="900.00",
            total_exposure="100.00",
            daily_pnl="5.00",
            margin_usage_percent="10.00",
            positions=(),
            open_orders=(),
        )

    async def preview_kill(self, *, flatten: bool) -> KillImpact:
        self.preview_calls.append(flatten)
        return KillImpact(
            open_order_count=2,
            position_count=1,
            evidence_state="RECONCILED_CLAIM",
            evidence_observed_at=NOW,
            warning="This halts entries and reconciles exchange truth.",
        )

    async def enqueue_kill(self, command: KillCommand) -> QueuedCommand:
        receipt = self.commands.get(command.request_id)
        if receipt is None:
            active = await self.active_kill()
            if active is not None:
                return active
            receipt = QueuedCommand(
                command_id=command.command_id,
                state="QUEUED",
                accepted_at=command.accepted_at,
            )
            self.commands[command.request_id] = receipt
        return receipt

    async def command_status(self, command_id: UUID) -> QueuedCommand | None:
        return self.commands.get(command_id)

    async def active_kill(self) -> QueuedCommand | None:
        return next(
            (item for item in self.commands.values() if item.state not in {"COMPLETED", "FAILED"}),
            None,
        )


@pytest.fixture(scope="module")
def password_hash() -> str:
    return PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)


@pytest.fixture
def api(password_hash: str) -> tuple[TestClient, FakeControlPlane]:
    settings = ControlApiSettings(
        HMT_API_OPERATOR_PASSWORD_HASH=SecretStr(password_hash),
        HMT_API_ALLOWED_ORIGIN=ORIGIN,
    )
    control = FakeControlPlane()
    return (
        TestClient(
            create_app(
                settings,
                control_plane=control,
                operator_audit=RecordingOperatorAudit(),
            ),
            base_url=ORIGIN,
        ),
        control,
    )


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/session", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
    )
    assert response.status_code == 200
    csrf = response.json()["csrfToken"]
    assert type(csrf) is str
    return csrf


def test_settings_reject_insecure_origins(password_hash: str) -> None:
    with pytest.raises(ConfigurationError):
        ControlApiSettings(
            HMT_API_OPERATOR_PASSWORD_HASH=SecretStr(password_hash),
            HMT_API_ALLOWED_ORIGIN="http://public.example",
        )


def test_settings_require_database_control_and_account_as_one_pair(
    password_hash: str,
) -> None:
    with pytest.raises(ConfigurationError):
        ControlApiSettings(
            HMT_API_OPERATOR_PASSWORD_HASH=SecretStr(password_hash),
            HMT_API_ALLOWED_ORIGIN=ORIGIN,
            HMT_API_DATABASE_CONTROL_ENABLED=True,
        )
    with pytest.raises(ConfigurationError):
        ControlApiSettings(
            HMT_API_OPERATOR_PASSWORD_HASH=SecretStr(password_hash),
            HMT_API_ALLOWED_ORIGIN=ORIGIN,
            HMT_API_ACCOUNT_ID=uuid4(),
        )


def test_app_closes_injected_owner_on_lifespan_exit(password_hash: str) -> None:
    closed: list[bool] = []

    async def close() -> None:
        closed.append(True)

    settings = ControlApiSettings(
        HMT_API_OPERATOR_PASSWORD_HASH=SecretStr(password_hash),
        HMT_API_ALLOWED_ORIGIN=ORIGIN,
    )
    with TestClient(
        create_app(
            settings,
            control_plane=FakeControlPlane(),
            operator_audit=RecordingOperatorAudit(),
            shutdown=close,
        ),
        base_url=ORIGIN,
    ) as client:
        assert client.get("/api/v1/health/live").json() == {"live": True}
    assert closed == [True]


def test_login_cookies_and_security_headers(api: tuple[TestClient, FakeControlPlane]) -> None:
    client, _ = api
    response = client.post(
        "/api/v1/session", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
    )
    cookies = response.headers.get_list("set-cookie")
    assert response.status_code == 200
    assert any("__Host-hmt_session=" in item and "HttpOnly" in item for item in cookies)
    assert any("__Host-hmt_csrf=" in item and "HttpOnly" not in item for item in cookies)
    assert all("Secure" in item and "SameSite=strict" in item for item in cookies)
    assert PASSWORD not in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_login_fails_closed_without_a_durable_operator_audit_sink(password_hash: str) -> None:
    settings = ControlApiSettings(
        HMT_API_OPERATOR_PASSWORD_HASH=SecretStr(password_hash),
        HMT_API_ALLOWED_ORIGIN=ORIGIN,
    )
    client = TestClient(
        create_app(settings, control_plane=FakeControlPlane()),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )

    response = client.post(
        "/api/v1/session",
        headers={"Origin": ORIGIN},
        json={"password": PASSWORD},
    )

    assert response.status_code == 503
    assert response.json() == {"error": "control_plane_unavailable"}
    assert all("hmt_session" not in item for item in response.headers.get_list("set-cookie"))


def test_authentication_and_mutations_are_origin_csrf_bound(
    api: tuple[TestClient, FakeControlPlane],
) -> None:
    client, control = api
    denied = client.post(
        "/api/v1/session",
        headers={"Origin": ORIGIN},
        json={"password": "wrong-password-value"},
    )
    assert denied.json() == {"error": "authentication_failed"}
    csrf = _login(client)
    missing = client.post(
        "/api/v1/commands/kill/preview",
        headers={"Origin": ORIGIN},
        json={"flatten": False},
    )
    hostile = client.post(
        "/api/v1/commands/kill/preview",
        headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf},
        json={"flatten": False},
    )
    assert missing.status_code == hostile.status_code == 403
    assert control.preview_calls == []


def test_failed_login_throttle_never_locks_out_a_valid_operator(
    password_hash: str,
) -> None:
    settings = ControlApiSettings(
        HMT_API_OPERATOR_PASSWORD_HASH=SecretStr(password_hash),
        HMT_API_ALLOWED_ORIGIN=ORIGIN,
        HMT_API_LOGIN_ATTEMPTS_PER_FIVE_MINUTES=2,
    )
    client = TestClient(
        create_app(
            settings,
            control_plane=FakeControlPlane(),
            operator_audit=RecordingOperatorAudit(),
        ),
        base_url=ORIGIN,
    )
    headers = {"Origin": ORIGIN}

    assert (
        client.post(
            "/api/v1/session", headers=headers, json={"password": "wrong-password-one"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/session", headers=headers, json={"password": "wrong-password-two"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/session", headers=headers, json={"password": "wrong-password-three"}
        ).status_code
        == 429
    )

    accepted = client.post("/api/v1/session", headers=headers, json={"password": PASSWORD})
    assert accepted.status_code == 200


def test_session_dashboard_logout_lifecycle(api: tuple[TestClient, FakeControlPlane]) -> None:
    client, _ = api
    csrf = _login(client)
    assert client.get("/api/v1/session").json()["csrfToken"] == csrf
    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["region"] == "india"
    logout = client.delete("/api/v1/session", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf})
    assert logout.status_code == 200
    assert client.get("/api/v1/dashboard").status_code == 401


def test_operator_login_reads_kill_and_logout_emit_bounded_audit_records(
    password_hash: str,
) -> None:
    settings = ControlApiSettings(
        HMT_API_OPERATOR_PASSWORD_HASH=SecretStr(password_hash),
        HMT_API_ALLOWED_ORIGIN=ORIGIN,
    )
    sink = RecordingOperatorAudit()
    client = TestClient(
        create_app(
            settings,
            control_plane=FakeControlPlane(),
            operator_audit=sink,
        ),
        base_url=ORIGIN,
    )

    csrf = _login(client)
    assert client.get("/api/v1/dashboard").status_code == 200
    preview = client.post(
        "/api/v1/commands/kill/preview",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={"flatten": False},
    ).json()
    command_id = str(uuid4())
    assert (
        client.post(
            "/api/v1/commands/kill",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": csrf,
                "Idempotency-Key": command_id,
            },
            json={
                "previewId": preview["previewId"],
                "flatten": False,
                "confirmation": None,
            },
        ).status_code
        == 200
    )
    assert client.get(f"/api/v1/commands/{command_id}").status_code == 200
    assert client.get("/api/v1/commands/kill/active").status_code == 200
    assert (
        client.delete(
            "/api/v1/session", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        ).status_code
        == 200
    )

    assert [record.event_kind for record in sink.records] == [
        "LOGIN",
        "DASHBOARD_READ",
        "KILL_PREVIEW",
        "KILL_ENQUEUE",
        "COMMAND_STATUS",
        "ACTIVE_KILL_STATUS",
        "LOGOUT",
    ]
    assert all(not hasattr(record, "password") for record in sink.records)
    assert all(not hasattr(record, "peer") for record in sink.records)


def test_kill_preview_binds_one_idempotency_identity(
    api: tuple[TestClient, FakeControlPlane],
) -> None:
    client, control = api
    csrf = _login(client)
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    preview = client.post(
        "/api/v1/commands/kill/preview", headers=headers, json={"flatten": True}
    ).json()
    request_id = str(uuid4())
    body = {
        "previewId": preview["previewId"],
        "flatten": True,
        "confirmation": "FLATTEN_ALL_POSITIONS",
    }
    first = client.post(
        "/api/v1/commands/kill",
        headers={**headers, "Idempotency-Key": request_id},
        json=body,
    )
    replay = client.post(
        "/api/v1/commands/kill",
        headers={**headers, "Idempotency-Key": request_id},
        json=body,
    )
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert len(control.commands) == 1
    changed = client.post(
        "/api/v1/commands/kill",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json=body,
    )
    assert changed.status_code == 409
    assert client.get("/api/v1/commands/kill/active").json() == {"active": first.json()}

    second_preview = client.post(
        "/api/v1/commands/kill/preview", headers=headers, json={"flatten": False}
    ).json()
    raced = client.post(
        "/api/v1/commands/kill",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={
            "previewId": second_preview["previewId"],
            "flatten": False,
            "confirmation": None,
        },
    )
    assert raced.status_code == 409
    assert raced.json() == {"detail": "kill_already_active"}


def test_unknown_scoped_command_is_authoritative_404_not_backend_unavailable(
    api: tuple[TestClient, FakeControlPlane],
) -> None:
    client, _ = api
    _login(client)

    response = client.get(f"/api/v1/commands/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "command_not_found"}


def test_kill_remains_available_when_dashboard_is_down(
    api: tuple[TestClient, FakeControlPlane],
) -> None:
    client, control = api
    csrf = _login(client)
    control.dashboard_available = False
    assert client.get("/api/v1/dashboard").status_code == 503
    response = client.post(
        "/api/v1/commands/kill/preview",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        json={"flatten": False},
    )
    assert response.status_code == 200


def test_oversized_requests_fail_before_argon2(api: tuple[TestClient, FakeControlPlane]) -> None:
    client, _ = api
    response = client.post(
        "/api/v1/session",
        headers={"Origin": ORIGIN, "Content-Length": "999999"},
        content=b"{}",
    )
    assert response.status_code == 413
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_metrics_are_authenticated_and_use_route_templates(
    api: tuple[TestClient, FakeControlPlane],
) -> None:
    client, _ = api
    assert client.get("/api/v1/metrics").status_code == 401
    _login(client)
    assert client.get("/api/v1/dashboard").status_code == 200

    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    assert "hmt_crypto_http_requests_total" in response.text
    assert 'route="/api/v1/dashboard"' in response.text
    assert "commandId" not in response.text
