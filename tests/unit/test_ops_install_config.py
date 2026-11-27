"""Ops-5 renders fake configuration with every gate closed."""

from ops.aws.install_config import render_files


def test_rendered_fake_config_is_locked_and_has_no_delta_credentials() -> None:
    files = render_files(
        {
            "runtime_url": "postgresql+asyncpg://runtime:fake@pooler:5432/postgres",
            "migration_url": "postgresql+asyncpg://owner:fake@direct:5432/postgres",
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
