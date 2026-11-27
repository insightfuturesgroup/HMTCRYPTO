"""Ops-5 renders fake configuration and enforces a root-only persistent contract."""

from pathlib import Path

from ops.aws.install_config import render_files


def test_rendered_fake_config_is_locked_and_has_no_delta_credentials() -> None:
    files = render_files(
        {
            "runtime_url": "postgresql+asyncpg://" + "runtime:fake@pooler:5432/postgres",
            "migration_url": "postgresql+asyncpg://" + "owner:fake@direct:5432/postgres",
            "operator_hash": "fake-hash",
            "supabase_url": "https://fixture.supabase.co",
            "publishable_key": "fake-publishable",
            "secret_key": "fake-server-key",
            "tls_email": "fixture@example.invalid",
        }
    )
    joined = "\n".join(files.values())
    assert "PRODUCTION_WRITES_FORBIDDEN=true" in joined
    assert "HMT_LIVE_ORDER_DISPATCH_ENABLED=false" in joined
    assert "HMT_LIVE_ORDER_QUALIFICATION_SHA256=\n" in joined
    assert "DELTA_API_KEY" not in joined
    assert "DELTA_API_SECRET" not in joined


def test_install_contract_is_atomic_persistent_and_root_only() -> None:
    source = Path("ops/aws/install_config.py").read_text()
    assert 'Path("/etc/hmt")' in source
    assert "os.umask(0o077)" in source
    assert "os.replace(temporary, path)" in source
    assert "os.fchmod(descriptor, 0o600)" in source
    assert "os.fchown(descriptor, 0, 0)" in source
    assert 'repo / ".env": etc / "compose.env"' in source
    assert "legacy_run" in source
    assert "root-only in tmpfs" not in source
