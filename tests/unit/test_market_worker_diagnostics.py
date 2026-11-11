"""Market worker diagnostics remain useful without leaking exception details."""

from hmt_crypto.errors import ConfigurationError
from hmt_crypto.worker.market_runner import _safe_failure_code


def test_market_worker_reports_stable_application_error_code() -> None:
    error = ConfigurationError(context={"path": "/private/value", "secret": "marker"})

    assert _safe_failure_code(error) == "configuration_error"


def test_market_worker_collapses_unknown_exception() -> None:
    error = RuntimeError("private-marker")

    assert _safe_failure_code(error) == "unexpected_error"
    assert "private-marker" not in _safe_failure_code(error)
