"""The current schema expectation comes from migrations, not model imports."""

from schema_table_source import migration_chain_tables


def test_migration_chain_discovery_matches_current_aggregate() -> None:
    from hmt_crypto.persistence import models

    assert {table.name for table in models.metadata.tables.values()} == migration_chain_tables()
