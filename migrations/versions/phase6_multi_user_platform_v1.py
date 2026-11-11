"""Add invitation-only users, account assignments, secret references and market state.

Revision ID: phase6_multi_user_platform_v1
Revises: phase5_runtime_grants_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase6_multi_user_platform_v1"
down_revision: str | Sequence[str] | None = "phase5_runtime_grants_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "hmt_crypto"
RUNTIME_ROLE = "hmt_crypto_app"


def _uuid(name: str, *, primary_key: bool = False, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.Uuid(), primary_key=primary_key, nullable=nullable)


def _time(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "platform_users",
        _uuid("id", primary_key=True),
        _uuid("auth_user_id"),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        _time("created_at"),
        _time("updated_at"),
        _time("last_login_at", nullable=True),
        sa.CheckConstraint(
            "role IN ('admin','trader','viewer')", name="ck_platform_users_role_allowed"
        ),
        sa.CheckConstraint(
            "status IN ('invited','active','disabled')", name="ck_platform_users_status_allowed"
        ),
        sa.UniqueConstraint("auth_user_id", name="uq_platform_users_auth_user"),
        sa.UniqueConstraint("email", name="uq_platform_users_email"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_platform_users_status_role", "platform_users", ["status", "role"], schema=SCHEMA
    )

    op.create_table(
        "account_assignments",
        _uuid("id", primary_key=True),
        _uuid("user_id"),
        _uuid("account_id"),
        sa.Column("permission", sa.String(16), nullable=False),
        _time("created_at"),
        _time("updated_at"),
        sa.CheckConstraint(
            "permission IN ('view','trade','manage')",
            name="ck_account_assignments_permission_allowed",
        ),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.platform_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "account_id", name="uq_account_assignments_user_account"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_account_assignments_account", "account_assignments", ["account_id"], schema=SCHEMA
    )

    op.create_table(
        "credential_secret_references",
        _uuid("id", primary_key=True),
        _uuid("account_id"),
        sa.Column("credential_version", sa.BigInteger(), nullable=False),
        sa.Column("secret_arn", sa.String(2048), nullable=False),
        sa.Column("fingerprint_hint", sa.String(12), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _uuid("created_by_user_id"),
        _time("created_at"),
        _time("revoked_at", nullable=True),
        sa.CheckConstraint(
            "credential_version >= 1", name="ck_credential_secret_references_version_positive"
        ),
        sa.CheckConstraint(
            "status IN ('pending_verification','verified','revoked')",
            name="ck_credential_secret_references_status_allowed",
        ),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], [f"{SCHEMA}.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "account_id", "credential_version", name="uq_credential_secret_account_version"
        ),
        sa.UniqueConstraint("secret_arn", name="uq_credential_secret_arn"),
        schema=SCHEMA,
    )

    op.create_table(
        "watchlist_items",
        _uuid("id", primary_key=True),
        _uuid("user_id"),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _time("created_at"),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.platform_users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_watchlist_user_created", "watchlist_items", ["user_id", "created_at"], schema=SCHEMA
    )

    op.create_table(
        "market_quotes",
        _uuid("id", primary_key=True),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("mark_price", sa.String(64), nullable=True),
        sa.Column("spot_price", sa.String(64), nullable=True),
        sa.Column("best_bid", sa.String(64), nullable=True),
        sa.Column("best_ask", sa.String(64), nullable=True),
        _time("observed_at"),
        _time("updated_at"),
        sa.CheckConstraint("region = 'india'", name="ck_market_quotes_india_only"),
        sa.CheckConstraint(
            "environment IN ('testnet','prod')", name="ck_market_quotes_environment_allowed"
        ),
        sa.UniqueConstraint(
            "region", "environment", "product_id", name="uq_market_quotes_venue_product"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_market_quotes_venue_observed",
        "market_quotes",
        ["region", "environment", "observed_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "platform_audit_events",
        _uuid("id", primary_key=True),
        _uuid("actor_user_id", nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("details", sa.Text(), nullable=False, server_default="{}"),
        _time("occurred_at"),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], [f"{SCHEMA}.platform_users.id"], ondelete="RESTRICT"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_platform_audit_occurred", "platform_audit_events", ["occurred_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_platform_audit_actor_occurred",
        "platform_audit_events",
        ["actor_user_id", "occurred_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "application_settings",
        _uuid("id", primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        _uuid("updated_by_user_id"),
        _time("updated_at"),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], [f"{SCHEMA}.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("key", name="uq_application_settings_key"),
        schema=SCHEMA,
    )

    op.create_table(
        "gtt_orders",
        _uuid("id", primary_key=True),
        _uuid("account_id"),
        _uuid("created_by_user_id"),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(128), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("contracts", sa.BigInteger(), nullable=False),
        sa.Column("trigger_kind", sa.String(32), nullable=False),
        sa.Column("trigger_price", sa.String(64), nullable=True),
        sa.Column("trail_offset", sa.String(64), nullable=True),
        sa.Column("limit_price", sa.String(64), nullable=True),
        sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(32), nullable=False),
        _uuid("idempotency_key"),
        _uuid("triggered_order_id", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _time("created_at"),
        _time("updated_at"),
        sa.CheckConstraint(
            "trigger_kind IN ('stop_loss','target','trailing_stop')",
            name="ck_gtt_orders_trigger_kind_allowed",
        ),
        sa.CheckConstraint("side IN ('buy','sell')", name="ck_gtt_orders_side_allowed"),
        sa.CheckConstraint(
            "status IN ('armed','triggered','submitting','uncertain',"
            "'completed','cancelled','failed')",
            name="ck_gtt_orders_status_allowed",
        ),
        sa.CheckConstraint("contracts > 0", name="ck_gtt_orders_contracts_positive"),
        sa.CheckConstraint("version >= 1", name="ck_gtt_orders_version_positive"),
        sa.CheckConstraint(
            "(trigger_kind = 'trailing_stop' AND trail_offset IS NOT NULL) OR "
            "(trigger_kind <> 'trailing_stop' AND trigger_price IS NOT NULL)",
            name="ck_gtt_orders_trigger_value_required",
        ),
        sa.ForeignKeyConstraint(["account_id"], [f"{SCHEMA}.accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], [f"{SCHEMA}.platform_users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_gtt_account_idempotency"),
        schema=SCHEMA,
    )
    op.create_index("ix_gtt_account_status", "gtt_orders", ["account_id", "status"], schema=SCHEMA)
    op.create_index("ix_gtt_product_status", "gtt_orders", ["product_id", "status"], schema=SCHEMA)

    op.execute(
        sa.text(
            "CREATE FUNCTION hmt_crypto.reject_platform_audit_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'platform audit is append-only'; END $$"
        )
    )
    op.execute(
        sa.text("REVOKE ALL ON FUNCTION hmt_crypto.reject_platform_audit_mutation() FROM PUBLIC")
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER platform_audit_append_only BEFORE UPDATE OR DELETE "
            "ON hmt_crypto.platform_audit_events FOR EACH ROW "
            "EXECUTE FUNCTION hmt_crypto.reject_platform_audit_mutation()"
        )
    )
    op.execute(
        sa.text(f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA {SCHEMA} TO {RUNTIME_ROLE}")
    )
    op.execute(
        sa.text(f"REVOKE UPDATE, DELETE ON {SCHEMA}.platform_audit_events FROM {RUNTIME_ROLE}")
    )


def downgrade() -> None:
    raise RuntimeError("Phase 6 is forward-fix only; restore from backup for rollback")
