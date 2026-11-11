"""Release preflight must prove a locked production configuration without echoing secrets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tools.ec2_release_preflight import PreflightError, verify

COMMIT = "c3d5fad123456789012345678901234567890123"


def _write(path: Path, content: str, mode: int = 0o600) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


def _configuration(root: Path) -> None:
    runtime = (
        "DATABASE_URL=postgresql+asyncpg://hmt_crypto_app.project:secret@"
        "aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?ssl=require\n"
        "DATABASE_CA_FILE=/run/secrets/supabase-prod-ca.crt\n"
        "EXECUTION_MODE=LIVE\nDELTA_REGION=india\nDELTA_ENV=prod\n"
        "PRODUCTION_WRITES_FORBIDDEN=true\n"
        "HMT_PRIVATE_READ_ONLY=true\n"
    )
    _write(
        root / "api.env",
        runtime
        + "HMT_API_OPERATOR_PASSWORD_HASH='$argon2id$not-a-real-hash'\n"
        + "HMT_API_ALLOWED_ORIGIN=https://crypto.helpmetrade.club\n"
        + "HMT_API_COOKIE_SECURE=true\nHMT_API_ALLOW_INSECURE_LOOPBACK=false\n"
        + "HMT_API_DATABASE_CONTROL_ENABLED=true\nHMT_API_EMBED_PAPER_WORKER=false\n"
        + "HMT_API_MULTI_USER_ENABLED=true\n"
        + "HMT_SUPABASE_URL=https://project.supabase.co\n"
        + "HMT_SUPABASE_PUBLISHABLE_KEY=sb_publishable_test\n"
        + "HMT_SUPABASE_SECRET_KEY=sb_secret_test\n"
        + "HMT_AWS_REGION=ap-southeast-2\n"
        + "HMT_API_ACCOUNT_ID=00000000-0000-4000-8000-000000000001\n",
    )
    _write(
        root / "worker.env",
        runtime
        + "HMT_LIVE_ORDER_DISPATCH_ENABLED=false\n"
        + "HMT_LIVE_ORDER_QUALIFICATION_SHA256=\n"
        + "HMT_AWS_REGION=ap-southeast-2\n",
    )
    _write(
        root / "migration.env",
        runtime
        + "DATABASE_MIGRATION_URL=postgresql+asyncpg://postgres:secret@"
        + "db.project.supabase.co:5432/postgres\n"
        + "DATABASE_MIGRATION_CA_FILE=/run/secrets/supabase-prod-ca.crt\n",
    )
    _write(root / "compose.env", "HMT_TLS_EMAIL=ops@example.com\n")
    _write(root / "supabase-prod-ca.crt", "test certificate fixture\n", 0o644)


def _git(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(args, **_kwargs):
        if args[-2:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout=COMMIT + "\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)


def test_locked_production_configuration_passes_without_secret_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configuration(tmp_path)
    _git(monkeypatch)

    result = verify(tmp_path, tmp_path, COMMIT, compose=False)

    assert result == {
        "status": "PASS",
        "reviewedCommit": COMMIT,
        "dispatchEnabled": False,
        "privateReadOnly": True,
        "productionWritesForbidden": True,
        "entryGatesExpected": "disabled",
        "secretsPrinted": False,
    }
    assert "sb_secret_test" not in repr(result)
    assert "postgres:secret" not in repr(result)


def test_preflight_rejects_dispatch_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configuration(tmp_path)
    worker = tmp_path / "worker.env"
    worker.write_text(
        worker.read_text(encoding="utf-8").replace(
            "HMT_LIVE_ORDER_DISPATCH_ENABLED=false",
            "HMT_LIVE_ORDER_DISPATCH_ENABLED=true",
        ),
        encoding="utf-8",
    )
    _git(monkeypatch)

    with pytest.raises(PreflightError, match="HMT_LIVE_ORDER_DISPATCH_ENABLED"):
        verify(tmp_path, tmp_path, COMMIT, compose=False)


def test_preflight_rejects_disabled_private_read_only_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configuration(tmp_path)
    api = tmp_path / "api.env"
    api.write_text(
        api.read_text(encoding="utf-8").replace(
            "HMT_PRIVATE_READ_ONLY=true",
            "HMT_PRIVATE_READ_ONLY=false",
        ),
        encoding="utf-8",
    )
    _git(monkeypatch)

    with pytest.raises(PreflightError, match="HMT_PRIVATE_READ_ONLY"):
        verify(tmp_path, tmp_path, COMMIT, compose=False)
