"""Account projections and restricted credential metadata; never API credentials.

ORM scope guards do not replace later reviewed DB triggers/grants. A verified
identity here is evidence, never write permission.
"""

from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    Uuid,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, validates

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    NulFreeString,
    StrictBigInteger,
    account_fk,
    bounded,
    hex_check,
    mode_check,
    venue_checks,
)
from hmt_crypto.persistence.types import UTCDateTime

_CREDENTIAL_SLOTS = frozenset(
    f"DELTA_{region}_{environment}"
    for region in ("INDIA", "GLOBAL")
    for environment in ("TESTNET", "PROD")
)


def _safe_hint(value: object) -> str | None:
    if value is None:
        return None
    if (
        type(value) is not str
        or len(value) != 12
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise PersistenceValueError()
    return value


class _FingerprintHint(NulFreeString):
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        return _safe_hint(value)


class _CredentialSlot(NulFreeString):
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        if value is not None and (type(value) is not str or value not in _CREDENTIAL_SLOTS):
            raise PersistenceValueError()
        return super().process_bind_param(value, dialect)


class Account(Base):
    """Mutable profile identity with nullable verified binding and no readiness flag."""

    __tablename__ = "accounts"
    __table_args__ = (
        *venue_checks(),
        mode_check(),
        bounded("profile_name"),
        bounded("external_user_id"),
        bounded("external_account_id"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("current_credential_version >= 1", name="credential_version_positive"),
        CheckConstraint("updated_at >= created_at", name="time_order"),
        CheckConstraint(
            "(external_user_id IS NULL AND external_account_id IS NULL "
            "AND identity_verified_at IS NULL) OR "
            "(external_user_id IS NOT NULL AND identity_verified_at IS NOT NULL)",
            name="verified_identity",
        ),
        UniqueConstraint("id", "region", "environment", "mode", name="uq_accounts_scope"),
        UniqueConstraint(
            "region", "environment", "mode", "profile_name", name="uq_accounts_profile_scope"
        ),
        ForeignKeyConstraint(
            ["id", "region", "environment", "mode", "current_credential_version"],
            [f"hmt_crypto.credential_versions.{name}" for name in (*SCOPE, "credential_version")],
            name="fk_accounts_current_credential_scope",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
            use_alter=True,
        ),
        Index(
            "uq_accounts_verified_live_user",
            "region",
            "environment",
            "external_user_id",
            unique=True,
            postgresql_where=text("mode = 'LIVE' AND external_user_id IS NOT NULL"),
            sqlite_where=text("mode = 'LIVE' AND external_user_id IS NOT NULL"),
        ),
        Index(
            "ix_accounts_current_credential",
            "id",
            "region",
            "environment",
            "mode",
            "current_credential_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    region: Mapped[str] = mapped_column(NulFreeString(16))
    environment: Mapped[str] = mapped_column(NulFreeString(16))
    mode: Mapped[str] = mapped_column(NulFreeString(16))
    profile_name: Mapped[str] = mapped_column(NulFreeString(128))
    external_user_id: Mapped[str | None] = mapped_column(NulFreeString(128))
    external_account_id: Mapped[str | None] = mapped_column(NulFreeString(128))
    identity_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    current_credential_version: Mapped[int | None] = mapped_column(StrictBigInteger())
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(StrictBigInteger())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())

    __mapper_args__ = {"version_id_col": version}


@event.listens_for(Account, "before_insert")
def _validate_initial_version(mapper: object, connection: object, target: Account) -> None:
    # SQLAlchemy's version generator initializes to 1; never silently replace a
    # supplied invalid version before its database CHECK could see that input.
    if type(target.version) is not int or target.version < 1:
        raise PersistenceValueError()


@event.listens_for(Account, "before_update")
def _guard_account_scope(mapper: object, connection: object, target: Account) -> None:
    state = inspect(target)
    if any(
        state.attrs[name].history.has_changes()
        for name in ("id", "region", "environment", "mode", "profile_name", "version")
    ):
        raise PersistenceValueError()


class CredentialVersion(Base):
    """Credential evidence with only the existing safe 12-hex diagnostic hint.

    Binding/probe/reconciliation UUIDs reference observations whose tables belong
    to later slices. They deliberately do not assert successful verification.
    Complete fingerprints have no storage field or accepted constructor input.
    """

    __tablename__ = "credential_versions"
    __table_args__ = (
        *venue_checks(),
        mode_check(),
        account_fk("credential_versions"),
        bounded("credential_slot"),
        bounded("observed_external_user_id"),
        bounded("observed_external_account_id"),
        hex_check("fingerprint_hint", 12),
        CheckConstraint(
            "credential_slot = 'DELTA_' || upper(region) || '_' || upper(environment)",
            name="credential_slot_scope",
        ),
        CheckConstraint("credential_version >= 1", name="version_positive"),
        CheckConstraint(
            "observed_external_account_id IS NULL OR observed_external_user_id IS NOT NULL",
            name="observed_identity",
        ),
        UniqueConstraint(*SCOPE, "credential_version", name="uq_credential_versions_scope_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid)
    region: Mapped[str] = mapped_column(NulFreeString(16))
    environment: Mapped[str] = mapped_column(NulFreeString(16))
    mode: Mapped[str] = mapped_column(NulFreeString(16))
    credential_version: Mapped[int] = mapped_column(StrictBigInteger())
    credential_slot: Mapped[str] = mapped_column(_CredentialSlot(32))
    fingerprint_hint: Mapped[str | None] = mapped_column(_FingerprintHint(12))
    observed_external_user_id: Mapped[str | None] = mapped_column(NulFreeString(128))
    observed_external_account_id: Mapped[str | None] = mapped_column(NulFreeString(128))
    binding_ref: Mapped[UUID | None] = mapped_column(Uuid)
    probe_ref: Mapped[UUID | None] = mapped_column(Uuid)
    reconciliation_ref: Mapped[UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    @validates("fingerprint_hint")
    def _own_hint(self, key: str, value: object) -> str | None:
        return _safe_hint(value)

    @validates("credential_slot")
    def _allowlisted_slot(self, key: str, value: object) -> str:
        if type(value) is not str or value not in _CREDENTIAL_SLOTS:
            raise PersistenceValueError()
        return value

    def to_safe_dict(self) -> dict[str, str | int | None]:
        """Return whitelisted query metadata without fingerprint or opaque markers."""
        return {
            "id": str(self.id),
            "credential_version": self.credential_version,
            "credential_slot": self.credential_slot,
            "fingerprint_hint": self.fingerprint_hint,
            "recorded_at": self.recorded_at.isoformat(),
        }


@event.listens_for(CredentialVersion, "before_insert")
@event.listens_for(CredentialVersion, "before_update")
def _bind_slot_to_scope(mapper: object, connection: object, target: CredentialVersion) -> None:
    expected = f"DELTA_{target.region.upper()}_{target.environment.upper()}"
    if expected not in _CREDENTIAL_SLOTS:
        raise PersistenceValueError()
    # The persisted column is required, but a newly constructed ORM row can
    # omit it until this event derives the allowlisted scope value.
    slot = cast(str | None, target.credential_slot)
    if slot is None:
        target.credential_slot = expected
    elif slot != expected:
        raise PersistenceValueError()
