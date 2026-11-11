"""The rollback probe exposes only bounded structural database evidence."""

from hmt_crypto.worker.market_persistence_probe import _safe_database_evidence


class _DriverError(Exception):
    sqlstate = "23514"
    constraint_name = "ck_catalogue_safe"


def test_probe_extracts_only_safe_structural_evidence() -> None:
    error = RuntimeError("password=private-marker")
    error.__cause__ = _DriverError("DETAIL private-marker")

    evidence = _safe_database_evidence(error)

    assert evidence == {
        "error_type": "RuntimeError",
        "sqlstate": "23514",
        "constraint": "ck_catalogue_safe",
    }
    assert "private-marker" not in repr(evidence)


def test_probe_rejects_unbounded_constraint_metadata() -> None:
    error = _DriverError()
    error.constraint_name = "unsafe-name;private-marker"

    assert _safe_database_evidence(error) == {
        "error_type": "_DriverError",
        "sqlstate": "23514",
    }
