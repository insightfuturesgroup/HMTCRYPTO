"""Production-oriented Uvicorn entry point for the control API."""

from __future__ import annotations

import uvicorn

from hmt_crypto.api.settings import ControlApiSettings


def main() -> None:
    """Run one API process without reload or forwarded-header trust."""
    settings = ControlApiSettings()  # type: ignore[call-arg]
    uvicorn.run(
        "hmt_crypto.api.app:create_app_from_environment",
        factory=True,
        host=settings.bind_host,
        port=settings.bind_port,
        reload=False,
        workers=1,
        proxy_headers=False,
        server_header=False,
        date_header=True,
        access_log=False,
    )


__all__ = ["main"]
