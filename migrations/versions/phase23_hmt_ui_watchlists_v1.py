"""Add grouped user watchlists for the HMT-compatible market rail.

Revision ID: phase23_hmt_ui_watchlists_v1
Revises: phase22_live_qualification_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase23_hmt_ui_watchlists_v1"
down_revision: str | Sequence[str] | None = "phase22_live_qualification_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlist_groups",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["hmt_crypto.platform_users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "name", name="uq_watchlist_group_user_name"),
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_watchlist_group_user_position",
        "watchlist_groups",
        ["user_id", "position"],
        schema="hmt_crypto",
    )
    op.add_column("watchlist_items", sa.Column("group_id", sa.Uuid()), schema="hmt_crypto")
    op.add_column(
        "watchlist_items",
        sa.Column("position", sa.BigInteger(), nullable=False, server_default="0"),
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            """
            INSERT INTO hmt_crypto.watchlist_groups
                (id, user_id, name, position, created_at, updated_at)
            SELECT md5(user_id::text || chr(58) || 'watchlist-1')::uuid,
                   user_id, 'Watchlist 1', 0, min(created_at), now()
            FROM hmt_crypto.watchlist_items
            GROUP BY user_id
            """
        )
    )
    op.execute(
        sa.text(
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
            """
        )
    )
    op.alter_column("watchlist_items", "group_id", nullable=False, schema="hmt_crypto")
    op.create_foreign_key(
        "fk_watchlist_items_group",
        "watchlist_items",
        "watchlist_groups",
        ["group_id"],
        ["id"],
        source_schema="hmt_crypto",
        referent_schema="hmt_crypto",
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "uq_watchlist_user_symbol", "watchlist_items", schema="hmt_crypto", type_="unique"
    )
    op.drop_index("ix_watchlist_user_created", table_name="watchlist_items", schema="hmt_crypto")
    op.create_unique_constraint(
        "uq_watchlist_group_symbol",
        "watchlist_items",
        ["group_id", "symbol"],
        schema="hmt_crypto",
    )
    op.create_index(
        "ix_watchlist_group_position",
        "watchlist_items",
        ["group_id", "position"],
        schema="hmt_crypto",
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON hmt_crypto.watchlist_groups TO hmt_crypto_app"
        )
    )


def downgrade() -> None:
    raise RuntimeError("Phase 23 is forward-fix only; restore from backup for rollback")
