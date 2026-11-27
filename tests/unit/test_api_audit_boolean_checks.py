"""Audit Boolean CHECKs must match migration truth on both supported dialects."""

import ast
import re
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from hmt_crypto.persistence._api_audit_schema_checks import api_audit_checks

pytestmark = pytest.mark.safety
NAMES = ("descriptor_slot", "terminal_truth_shape", "transfer_prior_bundle")


def baseline_check(name):
    path = Path(__file__).parents[2] / "migrations/versions/phase2_baseline_v1.py"
    tree = ast.parse(path.read_text())
    marker = f"CONSTRAINT ck_api_audit_events_{name} CHECK ("
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value
            if marker in sql:
                tail = sql.split(marker, 1)[1]
                depth = 1
                for index, character in enumerate(tail):
                    depth += (character == "(") - (character == ")")
                    if depth == 0:
                        return tail[:index]
    raise AssertionError(f"Missing baseline constraint: {name}")


@pytest.mark.parametrize("name", NAMES)
def test_postgres_boolean_check_matches_unchanged_baseline(name):
    check = next(item for item in api_audit_checks() if item.name == name)
    actual = str(check.sqltext.compile(dialect=postgresql.dialect()))
    assert re.sub(r"\s+", "", actual).lower() == re.sub(r"\s+", "", baseline_check(name)).lower()


@pytest.mark.parametrize("name", NAMES)
def test_sqlite_boolean_check_keeps_integer_literals(name):
    check = next(item for item in api_audit_checks() if item.name == name)
    sql = str(check.sqltext.compile(dialect=sqlite.dialect()))
    assert "TRUE" not in sql
    assert "FALSE" not in sql
    assert re.search(r"\b(?:entry_possible|prior_bytes_sent_possible) = [01]\b", sql)


@pytest.mark.parametrize(
    "name,valid,invalid",
    [
        (
            "descriptor_slot",
            "'SUMMARY_ONLY', NULL, NULL, NULL",
            "'RETAIN_SANITIZED_SNAPSHOT', 'SIMULATOR_OUTCOME', 0, 'RESPONSE'",
        ),
        (
            "terminal_truth_shape",
            "NULL, 'VENUE_ENTERED', 1, NULL",
            "NULL, 'VENUE_ENTERED', 0, NULL",
        ),
        ("transfer_prior_bundle", "NULL, NULL, NULL, NULL", "NULL, NULL, NULL, NULL"),
    ],
)
def test_sqlite_check_rejects_violation(name, valid, invalid):
    check = next(item for item in api_audit_checks() if item.name == name)
    sql = str(check.sqltext.compile(dialect=sqlite.dialect()))
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE probe (retention_mode TEXT, event_kind TEXT, entry_possible BOOLEAN, "
            "payload_slot TEXT, reconciliation_required BOOLEAN, outcome_kind TEXT, "
            "last_phase TEXT, wire_request_evidence_sha256 TEXT, "
            "pre_dispatch_audit_receipt_id TEXT, prior_dispatch_event_id TEXT, "
            "prior_consumption_id TEXT, prior_bytes_sent_possible BOOLEAN, "
            f"CHECK ({sql}))"
        )
        connection.execute(
            "INSERT INTO probe(retention_mode, event_kind, entry_possible, payload_slot) "
            f"VALUES ({valid})"
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            if name == "transfer_prior_bundle":
                connection.execute(
                    "INSERT INTO probe(prior_dispatch_event_id, prior_consumption_id, "
                    "prior_bytes_sent_possible) VALUES ('dispatch', 'consumption', 0)"
                )
            else:
                connection.execute(
                    "INSERT INTO probe(retention_mode, event_kind, entry_possible, payload_slot) "
                    f"VALUES ({invalid})"
                )
