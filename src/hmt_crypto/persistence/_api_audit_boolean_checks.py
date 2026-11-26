"""Compile the three legacy audit Boolean checks without changing SQLite SQL."""

from typing import Any

from sqlalchemy import Boolean
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import ColumnElement


class AuditBooleanCheck(ColumnElement[bool]):
    """Trusted, static audit SQL; only the named Boolean literals vary by dialect."""

    type = Boolean()
    inherit_cache = False

    def __init__(self, sql: str) -> None:
        self.sql = sql


@compiles(AuditBooleanCheck)
def _portable(element: AuditBooleanCheck, compiler: SQLCompiler, **kwargs: Any) -> str:
    del compiler, kwargs
    return element.sql


@compiles(AuditBooleanCheck, "postgresql")
def _postgres(element: AuditBooleanCheck, compiler: SQLCompiler, **kwargs: Any) -> str:
    del compiler, kwargs
    sql = element.sql
    for column in ("entry_possible", "reconciliation_required", "prior_bytes_sent_possible"):
        sql = sql.replace(f"{column} = 0", f"{column} = FALSE")
        sql = sql.replace(f"{column} = 1", f"{column} = TRUE")
    return sql
