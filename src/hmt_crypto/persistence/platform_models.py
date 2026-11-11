"""Multi-user control-plane records; no secret values are stored here."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.schema_support import NulFreeString, StrictBigInteger, bounded
from hmt_crypto.persistence.types import UTCDateTime


class PlatformUser(Base):
    __tablename__ = "platform_users"
    __table_args__ = (
        CheckConstraint("role IN ('admin','trader','viewer')", name="role_allowed"),
        CheckConstraint("status IN ('invited','active','disabled')", name="status_allowed"),
        bounded("email"),
        bounded("full_name"),
        UniqueConstraint("auth_user_id", name="uq_platform_users_auth_user"),
        UniqueConstraint("email", name="uq_platform_users_email"),
        Index("ix_platform_users_status_role", "status", "role"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    auth_user_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    email: Mapped[str] = mapped_column(NulFreeString(320), nullable=False)
    full_name: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    role: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    status: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AccountAssignment(Base):
    __tablename__ = "account_assignments"
    __table_args__ = (
        CheckConstraint("permission IN ('view','trade','manage')", name="permission_allowed"),
        UniqueConstraint("user_id", "account_id", name="uq_account_assignments_user_account"),
        Index("ix_account_assignments_account", "account_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    permission: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CredentialSecretReference(Base):
    __tablename__ = "credential_secret_references"
    __table_args__ = (
        CheckConstraint("credential_version >= 1", name="version_positive"),
        CheckConstraint(
            "status IN ('pending_verification','verified','revoked')", name="status_allowed"
        ),
        bounded("secret_arn"),
        bounded("fingerprint_hint"),
        UniqueConstraint(
            "account_id", "credential_version", name="uq_credential_secret_account_version"
        ),
        UniqueConstraint("secret_arn", name="uq_credential_secret_arn"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    credential_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    secret_arn: Mapped[str] = mapped_column(NulFreeString(2048), nullable=False)
    fingerprint_hint: Mapped[str] = mapped_column(NulFreeString(12), nullable=False)
    status: Mapped[str] = mapped_column(NulFreeString(32), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
        Index("ix_watchlist_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MarketQuote(Base):
    __tablename__ = "market_quotes"
    __table_args__ = (
        CheckConstraint("region = 'india'", name="india_only"),
        CheckConstraint("environment IN ('testnet','prod')", name="environment_allowed"),
        UniqueConstraint(
            "region", "environment", "product_id", name="uq_market_quotes_venue_product"
        ),
        Index("ix_market_quotes_venue_observed", "region", "environment", "observed_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    region: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    environment: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    symbol: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    mark_price: Mapped[str | None] = mapped_column(NulFreeString(64))
    spot_price: Mapped[str | None] = mapped_column(NulFreeString(64))
    best_bid: Mapped[str | None] = mapped_column(NulFreeString(64))
    best_ask: Mapped[str | None] = mapped_column(NulFreeString(64))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PlatformAuditEvent(Base):
    __tablename__ = "platform_audit_events"
    __table_args__ = (
        bounded("action"),
        bounded("target_type"),
        bounded("target_id"),
        bounded("outcome"),
        bounded("request_id"),
        Index("ix_platform_audit_occurred", "occurred_at"),
        Index("ix_platform_audit_actor_occurred", "actor_user_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    target_type: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    target_id: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    outcome: Mapped[str] = mapped_column(NulFreeString(32), nullable=False)
    request_id: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class ApplicationSetting(Base):
    __tablename__ = "application_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_application_settings_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class GttOrder(Base):
    """Durable application-managed exit trigger; never implies dispatch authority."""

    __tablename__ = "gtt_orders"
    __table_args__ = (
        CheckConstraint(
            "trigger_kind IN ('stop_loss','target','trailing_stop')", name="trigger_kind_allowed"
        ),
        CheckConstraint("side IN ('buy','sell')", name="side_allowed"),
        CheckConstraint(
            "status IN ('armed','triggered','submitting','uncertain',"
            "'completed','cancelled','failed')",
            name="status_allowed",
        ),
        CheckConstraint("contracts > 0", name="contracts_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(trigger_kind = 'trailing_stop' AND trail_offset IS NOT NULL) OR "
            "(trigger_kind <> 'trailing_stop' AND trigger_price IS NOT NULL)",
            name="trigger_value_required",
        ),
        UniqueConstraint("account_id", "idempotency_key", name="uq_gtt_account_idempotency"),
        Index("ix_gtt_account_status", "account_id", "status"),
        Index("ix_gtt_product_status", "product_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    symbol: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    side: Mapped[str] = mapped_column(NulFreeString(8), nullable=False)
    contracts: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    trigger_kind: Mapped[str] = mapped_column(NulFreeString(32), nullable=False)
    trigger_price: Mapped[str | None] = mapped_column(NulFreeString(64))
    trail_offset: Mapped[str | None] = mapped_column(NulFreeString(64))
    limit_price: Mapped[str | None] = mapped_column(NulFreeString(64))
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(NulFreeString(32), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    triggered_order_id: Mapped[UUID | None] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": version}


__all__ = [
    "AccountAssignment",
    "ApplicationSetting",
    "GttOrder",
    "CredentialSecretReference",
    "MarketQuote",
    "PlatformAuditEvent",
    "PlatformUser",
    "WatchlistItem",
]
