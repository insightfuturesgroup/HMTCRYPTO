"""Execution metadata must stay private, scoped and restricted to this unit."""

from schema_test_support import schema_api
from sqlalchemy import MetaData, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable


def test_execution_metadata_adds_exact_five_tables_and_compiles_postgres():
    api = schema_api()
    assert {table.name for table in api.metadata.tables.values()} == {
        "accounts",
        "credential_versions",
        "catalogue_snapshots",
        "catalogue_products",
        "signals",
        "command_operations",
        "orders",
        "order_attempts",
        "order_attempt_events",
        "order_transitions",
    }, "The five execution evidence tables are missing"
    metadata = MetaData(schema="hmt_crypto", naming_convention=api.metadata.naming_convention)
    for table in api.metadata.tables.values():
        table.to_metadata(metadata)
    ddl = []
    for table in metadata.tables.values():
        ddl.append(str(CreateTable(table).compile(dialect=postgresql.dialect())))
        for constraint in table.constraints:
            assert constraint.name
        for fk in table.foreign_key_constraints:
            assert fk.ondelete == fk.onupdate == "RESTRICT"
            if fk.referred_table.name in {
                "accounts",
                "credential_versions",
                "signals",
                "command_operations",
                "orders",
                "order_attempts",
            }:
                assert {"region", "environment", "mode"} <= set(fk.column_keys)
            if fk.use_alter:
                ddl.append(str(AddConstraint(fk).compile(dialect=postgresql.dialect())))
        for index in table.indexes:
            assert index.name
            ddl.append(str(CreateIndex(index).compile(dialect=postgresql.dialect())))
    assert "TIMESTAMP WITH TIME ZONE" in "\n".join(ddl)
    for name, wanted in (
        ("catalogue_snapshots", ("id", "region", "environment")),
        ("signals", ("id", "account_id", "region", "environment", "mode")),
        ("orders", ("id", "account_id", "region", "environment", "mode", "product_id")),
    ):
        assert wanted in {
            tuple(column.name for column in constraint.columns)
            for constraint in metadata.tables[f"hmt_crypto.{name}"].constraints
            if isinstance(constraint, UniqueConstraint)
        }
