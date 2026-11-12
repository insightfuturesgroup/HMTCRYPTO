"""Multi-user control-plane records; no secret values are stored here."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
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


class PlatformSession(Base):
    __tablename__ = "platform_sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="expiry_order"),
        CheckConstraint("last_seen_at >= created_at", name="last_seen_order"),
        UniqueConstraint("token_sha256", name="uq_platform_sessions_token_sha256"),
        Index(
            "ix_platform_sessions_user_active",
            "user_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    token_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    user_agent_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))


class PlatformNotification(Base):
    __tablename__ = "platform_notifications"
    __table_args__ = (
        CheckConstraint("severity IN ('critical','warning')", name="severity_allowed"),
        CheckConstraint("last_observed_at >= first_observed_at", name="observed_order"),
        UniqueConstraint("finding_key", name="uq_platform_notifications_finding_key"),
        Index(
            "ix_platform_notifications_active",
            "severity",
            "last_observed_at",
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    finding_key: Mapped[str] = mapped_column(NulFreeString(256), nullable=False)
    account_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT")
    )
    account_name: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    severity: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    code: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    message: Mapped[str] = mapped_column(NulFreeString(512), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acknowledged_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT")
    )


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


class WatchlistGroup(Base):
    __tablename__ = "watchlist_groups"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_watchlist_group_user_name"),
        Index("ix_watchlist_group_user_position", "user_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    position: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("group_id", "symbol", name="uq_watchlist_group_symbol"),
        Index("ix_watchlist_group_position", "group_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    group_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.watchlist_groups.id", ondelete="RESTRICT"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False, default=0)
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


class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        CheckConstraint("region = 'india'", name="india_only"),
        CheckConstraint("environment = 'prod'", name="prod_only"),
        CheckConstraint("resolution = '1m'", name="resolution"),
        CheckConstraint("open > 0 AND high > 0 AND low > 0 AND close > 0", name="prices_positive"),
        CheckConstraint("high >= low", name="range"),
        CheckConstraint("volume IS NULL OR volume >= 0", name="volume_nonnegative"),
        UniqueConstraint(
            "region",
            "environment",
            "product_id",
            "resolution",
            "timestamp",
            name="uq_market_candles_identity",
        ),
        Index("ix_market_candles_product_time", "product_id", "resolution", text("timestamp DESC")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    region: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    environment: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    symbol: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    resolution: Mapped[str] = mapped_column(NulFreeString(8), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


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


class ProtectionGroup(Base):
    """One fill-bound three-leg reduce-only OCO protection group."""

    __tablename__ = "protection_groups"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_fill','armed','claiming','completed','cancelled','failed')",
            name="status_allowed",
        ),
        CheckConstraint("protected_contracts > 0", name="contracts_positive"),
        CheckConstraint("average_fill_price > 0", name="fill_price_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("entry_intent_id", name="uq_protection_group_entry_intent"),
        Index("ix_protection_group_account_status", "account_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    entry_intent_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.live_order_intents.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    symbol: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    exit_side: Mapped[str] = mapped_column(NulFreeString(8), nullable=False)
    protected_contracts: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    average_fill_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    strategy_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    protection_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    catalogue_revision: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    reconciliation_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hmt_crypto.private_reconciliation_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    risk_policy_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    credential_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    qualification_evidence_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hmt_crypto.live_qualification_evidence.id", ondelete="RESTRICT"),
        nullable=False,
    )
    claimed_leg_id: Mapped[UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(NulFreeString(24), nullable=False)
    version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": version}


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
        CheckConstraint(
            "trail_anchor IS NULL OR trigger_kind = 'trailing_stop'",
            name="trail_anchor_only_for_trailing",
        ),
        CheckConstraint(
            "(triggered_at IS NULL AND trigger_evidence_sha256 IS NULL) OR "
            "(triggered_at IS NOT NULL AND trigger_evidence_sha256 IS NOT NULL)",
            name="trigger_evidence_pair",
        ),
        UniqueConstraint("account_id", "idempotency_key", name="uq_gtt_account_idempotency"),
        Index("ix_gtt_account_status", "account_id", "status"),
        Index("ix_gtt_product_status", "product_id", "status"),
        Index("ix_gtt_protection_group_status", "protection_group_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    protection_group_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.protection_groups.id", ondelete="RESTRICT")
    )
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    symbol: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    side: Mapped[str] = mapped_column(NulFreeString(8), nullable=False)
    contracts: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    trigger_kind: Mapped[str] = mapped_column(NulFreeString(32), nullable=False)
    trigger_price: Mapped[str | None] = mapped_column(NulFreeString(64))
    trail_offset: Mapped[str | None] = mapped_column(NulFreeString(64))
    limit_price: Mapped[str | None] = mapped_column(NulFreeString(64))
    trail_anchor: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    last_quote_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.market_quotes.id", ondelete="RESTRICT")
    )
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(NulFreeString(32), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    triggered_order_id: Mapped[UUID | None] = mapped_column(Uuid)
    triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    trigger_evidence_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    last_error_code: Mapped[str | None] = mapped_column(NulFreeString(64))
    version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": version}


class TradingViewStrategy(Base):
    __tablename__ = "tradingview_strategies"
    __table_args__ = (
        CheckConstraint("contracts > 0", name="contracts_positive"),
        CheckConstraint("order_type IN ('market','limit')", name="order_type_allowed"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("stop_loss_pct > 0", name="stop_loss_pct_positive"),
        CheckConstraint("target_pct > 0", name="target_pct_positive"),
        CheckConstraint("trailing_offset_pct > 0", name="trailing_offset_pct_positive"),
        UniqueConstraint("account_id", "name", name="uq_tv_strategies_account_name"),
        Index("ix_tv_strategies_account_enabled", "account_id", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    symbol: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    contracts: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    order_type: Mapped[str] = mapped_column(NulFreeString(8), nullable=False)
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stop_loss_pct: Mapped[Decimal] = mapped_column(
        Numeric(9, 6), nullable=False, default=Decimal("2")
    )
    target_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False, default=Decimal("4"))
    trailing_offset_pct: Mapped[Decimal] = mapped_column(
        Numeric(9, 6), nullable=False, default=Decimal("1")
    )
    protection_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": version}


class TradingViewWebhookKey(Base):
    __tablename__ = "tradingview_webhook_keys"
    __table_args__ = (
        UniqueConstraint("key_sha256", name="uq_tv_webhook_keys_digest"),
        Index("ix_tv_webhook_keys_strategy_active", "strategy_id", "revoked_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    strategy_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hmt_crypto.tradingview_strategies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    key_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TradingViewSignal(Base):
    __tablename__ = "tradingview_signals"
    __table_args__ = (
        CheckConstraint("side IN ('buy','sell')", name="side_allowed"),
        CheckConstraint(
            "status IN ('received','admitted','rejected','duplicate')", name="status_allowed"
        ),
        UniqueConstraint("strategy_id", "external_signal_id", name="uq_tv_signals_external"),
        Index("ix_tv_signals_strategy_received", "strategy_id", "received_at"),
        Index("ix_tv_signals_account_status", "account_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    strategy_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hmt_crypto.tradingview_strategies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    external_signal_id: Mapped[str] = mapped_column(NulFreeString(128), nullable=False)
    side: Mapped[str] = mapped_column(NulFreeString(8), nullable=False)
    limit_price: Mapped[str | None] = mapped_column(NulFreeString(64))
    payload_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    status: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    rejection_code: Mapped[str | None] = mapped_column(NulFreeString(64))
    intent_id: Mapped[UUID | None] = mapped_column(Uuid)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AccountLiveControl(Base):
    """Fail-closed Live readiness state; absence and unknown states deny dispatch."""

    __tablename__ = "account_live_controls"
    __table_args__ = (
        CheckConstraint(
            "credential_state IN ('unconfigured','pending','verified','failed')",
            name="credential_state_allowed",
        ),
        CheckConstraint(
            "reconciliation_state IN ('unknown','running','ready','degraded','blocked')",
            name="reconciliation_state_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("account_id", name="uq_account_live_controls_account"),
        Index("ix_account_live_controls_readiness", "credential_state", "reconciliation_state"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    credential_state: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    reconciliation_state: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    entries_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exits_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reconciled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_code: Mapped[str | None] = mapped_column(NulFreeString(64))
    version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": version}


class PrivateReconciliationSnapshot(Base):
    """Bounded evidence from one read-only Delta private-state acquisition."""

    __tablename__ = "private_reconciliation_snapshots"
    __table_args__ = (
        CheckConstraint("credential_version >= 1", name="credential_version_pos"),
        CheckConstraint("balance_count >= 0", name="balance_count_nonneg"),
        CheckConstraint("position_count >= 0", name="position_count_nonneg"),
        CheckConstraint("open_order_count >= 0", name="open_order_count_nonneg"),
        CheckConstraint("fill_count >= 0", name="fill_count_nonneg"),
        CheckConstraint("wallet_transaction_count >= 0", name="wallet_tx_count_nonneg"),
        CheckConstraint("outcome IN ('ready','blocked','unavailable')", name="outcome_allowed"),
        CheckConstraint(
            "balances_payload IS NULL OR octet_length(balances_payload) <= 1048576",
            name="balances_payload_bounded",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "positions_payload IS NULL OR octet_length(positions_payload) <= 1048576",
            name="positions_payload_bounded",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "open_orders_payload IS NULL OR octet_length(open_orders_payload) <= 1048576",
            name="open_orders_payload_bounded",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "fills_payload IS NULL OR octet_length(fills_payload) <= 1048576",
            name="fills_payload_bounded",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "wallet_transactions_payload IS NULL OR "
            "octet_length(wallet_transactions_payload) <= 1048576",
            name="wallet_tx_payload_bounded",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "(worker_owner_id IS NULL AND worker_generation IS NULL) OR "
            "(worker_owner_id IS NOT NULL AND worker_generation >= 1)",
            name="worker_fence_pair",
        ),
        UniqueConstraint("account_id", "request_id", name="uq_private_reconciliation_request"),
        Index("ix_private_reconciliation_account_observed", "account_id", "observed_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    credential_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    balance_count: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    position_count: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    open_order_count: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    fill_count: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    wallet_transaction_count: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    foreign_product_ids: Mapped[str] = mapped_column(Text, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    outcome: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    balances_payload: Mapped[str | None] = mapped_column(Text)
    positions_payload: Mapped[str | None] = mapped_column(Text)
    open_orders_payload: Mapped[str | None] = mapped_column(Text)
    fills_payload: Mapped[str | None] = mapped_column(Text)
    wallet_transactions_payload: Mapped[str | None] = mapped_column(Text)
    worker_owner_id: Mapped[UUID | None] = mapped_column(Uuid)
    worker_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PrivateWorkerLease(Base):
    """One expiring owner per account; loss of ownership removes read authority."""

    __tablename__ = "private_worker_leases"
    __table_args__ = (
        CheckConstraint("credential_version >= 1", name="credential_version_pos"),
        CheckConstraint("generation >= 1", name="generation_pos"),
        CheckConstraint("lease_until > heartbeat_at", name="lease_after_heartbeat"),
        UniqueConstraint("account_id", name="uq_private_worker_leases_account"),
        Index("ix_private_worker_leases_expiry", "lease_until"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    owner_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    credential_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    generation: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class AccountRiskPolicy(Base):
    """Admin-owned hard limits required before any Live gate can open."""

    __tablename__ = "account_risk_policies"
    __table_args__ = (
        CheckConstraint("max_order_notional > 0", name="max_order_notional_pos"),
        CheckConstraint("max_position_notional > 0", name="max_position_notional_pos"),
        CheckConstraint("max_daily_loss > 0", name="max_daily_loss_pos"),
        CheckConstraint("max_open_orders >= 1", name="max_open_orders_pos"),
        CheckConstraint("max_inflight_signals >= 1", name="max_inflight_signals_pos"),
        CheckConstraint("usd_inr_rate > 0", name="usd_inr_rate_pos"),
        CheckConstraint("limit_currency = 'INR'", name="limit_currency_inr"),
        CheckConstraint("version >= 1", name="version_pos"),
        UniqueConstraint("account_id", name="uq_account_risk_policies_account"),
        Index("ix_account_risk_policies_updated_by", "updated_by_user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    max_order_notional: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    max_position_notional: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    max_daily_loss: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    max_open_orders: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    max_inflight_signals: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False, default=5)
    limit_currency: Mapped[str] = mapped_column(NulFreeString(8), nullable=False, default="INR")
    usd_inr_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False, default=Decimal("100")
    )
    max_order_limit_inr: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("5000")
    )
    max_position_limit_inr: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("10000")
    )
    max_daily_loss_limit_inr: Mapped[Decimal] = mapped_column(
        Numeric(38, 18), nullable=False, default=Decimal("2000")
    )
    allowed_product_ids: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": version}


class PrivateStreamEvidence(Base):
    """Durable, secret-free evidence for one authenticated private channel."""

    __tablename__ = "private_stream_evidence"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('orders','positions','margins','v2/user_trades')", name="channel_allowed"
        ),
        CheckConstraint(
            "state IN ('connected','snapshot','live','gap','disconnected')", name="state_allowed"
        ),
        Index("ix_private_stream_account_observed", "account_id", "observed_at"),
        Index(
            "ix_private_stream_account_channel_observed",
            "account_id",
            "channel",
            "observed_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(NulFreeString(32), nullable=False)
    state: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    sequence_number: Mapped[int | None] = mapped_column(StrictBigInteger())
    credential_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    worker_owner_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    worker_generation: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class LiveQualificationEvidence(Base):
    """Immutable approval evidence; digest activation is a separate operator action."""

    __tablename__ = "live_qualification_evidence"
    __table_args__ = (
        CheckConstraint(
            "status IN ('collecting','qualified','rejected','revoked')", name="status_allowed"
        ),
        UniqueConstraint("evidence_sha256", name="uq_live_qualification_digest"),
        Index("ix_live_qualification_account_status", "account_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    release_commit: Mapped[str] = mapped_column(NulFreeString(40), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    evidence_payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    window_completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT")
    )
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class PlatformLiveControl(Base):
    """Singleton global Live gate; false is the deployment default."""

    __tablename__ = "platform_live_controls"
    __table_args__ = (
        CheckConstraint("control_key = 'global'", name="global_key"),
        CheckConstraint("version >= 1", name="version_pos"),
        Index("ix_platform_live_controls_updated_by", "updated_by_user_id"),
    )

    control_key: Mapped[str] = mapped_column(NulFreeString(16), primary_key=True)
    entries_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(NulFreeString(256), nullable=False)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": version}


class LiveOrderPreview(Base):
    """Short-lived, non-dispatchable Live validation evidence."""

    __tablename__ = "live_order_previews"
    __table_args__ = (
        CheckConstraint("side IN ('buy','sell')", name="side_allowed"),
        CheckConstraint("order_type IN ('market','limit')", name="order_type_allowed"),
        CheckConstraint("contracts >= 1", name="contracts_pos"),
        CheckConstraint("notional > 0", name="notional_pos"),
        CheckConstraint("expires_at > created_at", name="expiry_order"),
        UniqueConstraint("account_id", "request_id", name="uq_live_order_previews_request"),
        Index("ix_live_order_previews_account_expiry", "account_id", "expires_at"),
        Index("ix_live_order_previews_created_by", "created_by_user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    side: Mapped[str] = mapped_column(NulFreeString(8), nullable=False)
    contracts: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    order_type: Mapped[str] = mapped_column(NulFreeString(8), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False)
    price_basis: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    notional: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    catalogue_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.catalogue_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    reconciliation_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hmt_crypto.private_reconciliation_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    risk_policy_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class LiveOrderIntent(Base):
    """Durable admitted intent with no exchange-dispatch capability."""

    __tablename__ = "live_order_intents"
    __table_args__ = (
        CheckConstraint("status = 'ADMITTED'", name="admitted_only"),
        UniqueConstraint("preview_id", name="uq_live_order_intents_preview"),
        UniqueConstraint("account_id", "idempotency_key", name="uq_live_order_intents_request"),
        Index("ix_live_order_intents_account_created", "account_id", "created_at"),
        Index("ix_live_order_intents_created_by", "created_by_user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    preview_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.live_order_previews.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    intent_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class LiveOrderSubmission(Base):
    """Durable lease-fenced submission state; transport remains separately qualified."""

    __tablename__ = "live_order_submissions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','CLAIMED','PREPARED','SENDING','UNCERTAIN',"
            "'ACCEPTED','REJECTED','BLOCKED')",
            name="status_allowed",
        ),
        CheckConstraint("attempt_number >= 0", name="attempt_nonnegative"),
        CheckConstraint(
            "status NOT IN ('PREPARED','SENDING','UNCERTAIN','ACCEPTED','REJECTED') "
            "OR attempt_number >= 1",
            name="attempt_required",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(status = 'PENDING' AND worker_owner_id IS NULL AND worker_generation IS NULL "
            "AND credential_version IS NULL AND claim_until IS NULL) OR status <> 'PENDING'",
            name="pending_unclaimed",
        ),
        CheckConstraint(
            "status = 'PENDING' OR (worker_owner_id IS NOT NULL AND worker_generation IS NOT NULL "
            "AND credential_version IS NOT NULL AND claim_until IS NOT NULL)",
            name="claimed_fence_required",
        ),
        CheckConstraint(
            "(status = 'ACCEPTED' AND exchange_order_id IS NOT NULL) OR "
            "(status <> 'ACCEPTED' AND exchange_order_id IS NULL)",
            name="accepted_exchange_order_required",
        ),
        CheckConstraint(
            "status <> 'UNCERTAIN' OR last_error_code IS NOT NULL",
            name="uncertain_error_required",
        ),
        CheckConstraint(
            "(prepared_request_payload IS NULL AND prepared_request_sha256 IS NULL) OR "
            "(prepared_request_payload IS NOT NULL AND prepared_request_sha256 IS NOT NULL)",
            name="prepared_evidence_bundle",
        ),
        CheckConstraint(
            "status NOT IN ('PREPARED','SENDING','UNCERTAIN','ACCEPTED','REJECTED') OR "
            "prepared_request_sha256 IS NOT NULL",
            name="prepared_evidence_required",
        ),
        CheckConstraint(
            "status NOT IN ('SENDING','UNCERTAIN','ACCEPTED','REJECTED') OR sent_at IS NOT NULL",
            name="sent_timestamp_required",
        ),
        CheckConstraint(
            "(status IN ('ACCEPTED','REJECTED','BLOCKED') AND resolved_at IS NOT NULL) OR "
            "(status NOT IN ('ACCEPTED','REJECTED','BLOCKED') AND resolved_at IS NULL)",
            name="resolution_timestamp",
        ),
        UniqueConstraint("intent_id", name="uq_live_order_submissions_intent"),
        UniqueConstraint(
            "account_id", "client_order_id", name="uq_live_order_submissions_client_order"
        ),
        Index("ix_live_order_submissions_account_status", "account_id", "status", "created_at"),
        Index("ix_live_order_submissions_claim", "status", "claim_until"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    intent_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.live_order_intents.id", ondelete="RESTRICT"), nullable=False
    )
    client_order_id: Mapped[str] = mapped_column(NulFreeString(32), nullable=False)
    status: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    request_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    prepared_request_payload: Mapped[str | None] = mapped_column(Text)
    prepared_request_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    worker_owner_id: Mapped[UUID | None] = mapped_column(Uuid)
    worker_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    credential_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    claim_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempt_number: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    exchange_order_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    last_error_code: Mapped[str | None] = mapped_column(NulFreeString(64))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": version}


class LiveOrderSubmissionEvent(Base):
    """Append-only transition evidence for one Live submission."""

    __tablename__ = "live_order_submission_events"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            "from_status IN ('PENDING','CLAIMED','PREPARED','SENDING','UNCERTAIN',"
            "'ACCEPTED','REJECTED','BLOCKED')",
            name="from_status_allowed",
        ),
        CheckConstraint(
            "to_status IN ('PENDING','CLAIMED','PREPARED','SENDING','UNCERTAIN',"
            "'ACCEPTED','REJECTED','BLOCKED')",
            name="to_status_allowed",
        ),
        UniqueConstraint("submission_id", "sequence", name="uq_submission_events_sequence"),
        Index("ix_submission_events_account_occurred", "account_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    submission_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hmt_crypto.live_order_submissions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    from_status: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    to_status: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    worker_owner_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    worker_generation: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    credential_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class LiveOrderRecoveryCheck(Base):
    """Append-only evidence from client-order-ID uncertainty lookup."""

    __tablename__ = "live_order_recovery_checks"
    __table_args__ = (
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("result IN ('FOUND','NOT_FOUND','UNAVAILABLE')", name="result_allowed"),
        CheckConstraint(
            "(result = 'FOUND' AND exchange_order_id IS NOT NULL) OR "
            "(result <> 'FOUND' AND exchange_order_id IS NULL)",
            name="found_order_required",
        ),
        UniqueConstraint("submission_id", "attempt", name="uq_recovery_checks_attempt"),
        Index("ix_recovery_checks_account_occurred", "account_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    submission_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("hmt_crypto.live_order_submissions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    result: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    client_order_id: Mapped[str] = mapped_column(NulFreeString(32), nullable=False)
    exchange_order_id: Mapped[int | None] = mapped_column(StrictBigInteger())
    worker_owner_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    worker_generation: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    credential_version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class LiveOrderAction(Base):
    """One durable cancel or non-risk-increasing modification request."""

    __tablename__ = "live_order_actions"
    __table_args__ = (
        CheckConstraint("action IN ('CANCEL','MODIFY')", name="action_allowed"),
        CheckConstraint(
            "status IN ('PENDING','SENDING','UNCERTAIN','SUCCEEDED','REJECTED','BLOCKED')",
            name="status_allowed",
        ),
        CheckConstraint("order_id > 0", name="order_id_positive"),
        CheckConstraint("product_id > 0", name="product_id_positive"),
        CheckConstraint("target_size IS NULL OR target_size > 0", name="target_size_positive"),
        CheckConstraint(
            "(action = 'CANCEL' AND target_size IS NULL AND target_limit_price IS NULL) OR "
            "(action = 'MODIFY' AND target_size IS NOT NULL)",
            name="action_shape",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "(status = 'PENDING' AND worker_owner_id IS NULL AND worker_generation IS NULL "
            "AND credential_version IS NULL) OR status <> 'PENDING'",
            name="pending_unclaimed",
        ),
        UniqueConstraint("account_id", "idempotency_key", name="uq_live_actions_request"),
        Index("ix_live_actions_account_status_created", "account_id", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("hmt_crypto.platform_users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(NulFreeString(8), nullable=False)
    order_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    product_id: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    target_size: Mapped[int | None] = mapped_column(StrictBigInteger())
    target_limit_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    request_payload: Mapped[str] = mapped_column(Text, nullable=False)
    request_sha256: Mapped[str] = mapped_column(NulFreeString(64), nullable=False)
    status: Mapped[str] = mapped_column(NulFreeString(16), nullable=False)
    worker_owner_id: Mapped[UUID | None] = mapped_column(Uuid)
    worker_generation: Mapped[int | None] = mapped_column(StrictBigInteger())
    credential_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    exchange_response_sha256: Mapped[str | None] = mapped_column(NulFreeString(64))
    last_error_code: Mapped[str | None] = mapped_column(NulFreeString(64))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(StrictBigInteger(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    __mapper_args__ = {"version_id_col": version}


__all__ = [
    "AccountAssignment",
    "AccountRiskPolicy",
    "ApplicationSetting",
    "GttOrder",
    "LiveOrderIntent",
    "LiveOrderAction",
    "LiveOrderRecoveryCheck",
    "LiveOrderPreview",
    "LiveOrderSubmission",
    "LiveOrderSubmissionEvent",
    "AccountLiveControl",
    "CredentialSecretReference",
    "MarketQuote",
    "PlatformAuditEvent",
    "PlatformLiveControl",
    "PlatformUser",
    "PrivateReconciliationSnapshot",
    "PrivateWorkerLease",
    "TradingViewSignal",
    "TradingViewStrategy",
    "TradingViewWebhookKey",
    "WatchlistGroup",
    "WatchlistItem",
]
