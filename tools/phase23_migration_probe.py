"""Rollback-only phase 23 DDL probe with bounded, non-sensitive output."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from hmt_crypto.persistence.migration_configuration import (
    load_migration_url,
    migration_connect_args,
)

STATEMENTS: Sequence[tuple[str, str]] = (
    (
        "create_groups",
        """
        CREATE TABLE hmt_crypto.watchlist_groups (
            id UUID NOT NULL,
            user_id UUID NOT NULL,
            name VARCHAR(64) NOT NULL,
            position BIGINT DEFAULT 0 NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (id),
            CONSTRAINT uq_watchlist_group_user_name UNIQUE (user_id, name),
            FOREIGN KEY(user_id) REFERENCES hmt_crypto.platform_users (id)
                ON DELETE RESTRICT
        )
        """,
    ),
    (
        "index_groups",
        "CREATE INDEX ix_watchlist_group_user_position "
        "ON hmt_crypto.watchlist_groups (user_id, position)",
    ),
    ("add_group_id", "ALTER TABLE hmt_crypto.watchlist_items ADD COLUMN group_id UUID"),
    (
        "add_position",
        "ALTER TABLE hmt_crypto.watchlist_items "
        "ADD COLUMN position BIGINT DEFAULT 0 NOT NULL",
    ),
    (
        "backfill_groups",
        """
        INSERT INTO hmt_crypto.watchlist_groups
            (id, user_id, name, position, created_at, updated_at)
        SELECT md5(user_id::text || chr(58) || 'watchlist-1')::uuid,
               user_id, 'Watchlist 1', 0, min(created_at), now()
        FROM hmt_crypto.watchlist_items
        GROUP BY user_id
        """,
    ),
    (
        "backfill_items",
        """
        UPDATE hmt_crypto.watchlist_items AS item
        SET group_id = md5(item.user_id::text || chr(58) || 'watchlist-1')::uuid,
            position = ranked.position
        FROM (
            SELECT id, row_number() OVER
                (PARTITION BY user_id ORDER BY created_at, id) - 1 AS position
            FROM hmt_crypto.watchlist_items
        ) AS ranked
        WHERE ranked.id = item.id
        """,
    ),
    (
        "require_group_id",
        "ALTER TABLE hmt_crypto.watchlist_items ALTER COLUMN group_id SET NOT NULL",
    ),
    (
        "foreign_key",
        "ALTER TABLE hmt_crypto.watchlist_items "
        "ADD CONSTRAINT fk_watchlist_items_group FOREIGN KEY(group_id) "
        "REFERENCES hmt_crypto.watchlist_groups (id) ON DELETE RESTRICT",
    ),
    (
        "drop_old_unique",
        "ALTER TABLE hmt_crypto.watchlist_items "
        "DROP CONSTRAINT uq_watchlist_user_symbol",
    ),
    ("drop_old_index", "DROP INDEX hmt_crypto.ix_watchlist_user_created"),
    (
        "new_unique",
        "ALTER TABLE hmt_crypto.watchlist_items "
        "ADD CONSTRAINT uq_watchlist_group_symbol UNIQUE (group_id, symbol)",
    ),
    (
        "new_index",
        "CREATE INDEX ix_watchlist_group_position "
        "ON hmt_crypto.watchlist_items (group_id, position)",
    ),
    (
        "runtime_grant",
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON hmt_crypto.watchlist_groups TO hmt_crypto_app",
    ),
)


def _sqlstate(error: BaseException) -> str:
    current: BaseException | None = error
    while current is not None:
        for attribute in ("sqlstate", "pgcode"):
            candidate = getattr(current, attribute, None)
            if isinstance(candidate, str) and re.fullmatch(r"[0-9A-Z]{5}", candidate):
                return candidate
        current = current.__cause__ or current.__context__
    return "none"


async def main() -> None:
    url = load_migration_url()
    engine = create_async_engine(
        url,
        hide_parameters=True,
        poolclass=NullPool,
        connect_args=migration_connect_args(url),
    )
    stage = "connect"
    transaction = None
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            for next_stage, statement in STATEMENTS:
                stage = next_stage
                await connection.execute(text(statement))
            await transaction.rollback()
            transaction = None
            print('{"rolledBack":true,"stage":"complete","status":"PASS"}')
    except BaseException as error:
        if transaction is not None:
            await transaction.rollback()
        print(
            '{"errorType":"'
            + type(error).__name__
            + '","rolledBack":true,"sqlstate":"'
            + _sqlstate(error)
            + '","stage":"'
            + stage
            + '","status":"FAIL"}'
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
