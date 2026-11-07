"""Deterministic table-argument assembly for immutable API-audit metadata."""

from hmt_crypto.persistence._api_audit_schema_checks import api_audit_checks
from hmt_crypto.persistence._api_audit_schema_relations import api_audit_relations


def api_audit_table_args() -> tuple[object, ...]:
    """Build one fresh portable constraint/index graph for the ORM table."""
    return (*api_audit_checks(), *api_audit_relations())


__all__ = ["api_audit_table_args"]
