"""Account projections and restricted credential metadata; never API credentials.

ORM scope guards do not replace later reviewed DB triggers/grants. A verified
identity here is evidence, never write permission.
"""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.types import TypeDecorator

from hmt_crypto.persistence.db import Base
from hmt_crypto.persistence.errors import PersistenceValueError
from hmt_crypto.persistence.schema_support import (
    SCOPE,
    StrictBigInteger,
    account_fk,
    bounded,
    mode_check,
    sha_check,
    venue_checks,
)
from hmt_crypto.persistence.types import UTCDateTime


def _fingerprint_raw(value: object) -> str:
    raw = value.get_secret_value() if type(value) is SecretStr else value
    if type(raw) is not str or len(raw) != 64 or any(c not in "0123456789abcdef" for c in raw):
        raise PersistenceValueError()
    return raw


class _Fingerprint(TypeDecorator[SecretStr]):
    impl = String(64)
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return _fingerprint_raw(value)

    def process_result_value(self, value: object, dialect: Dialect) -> SecretStr | None:
        raw = self.process_bind_param(value, dialect)
        return SecretStr(raw) if raw is not None else None


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
    region: Mapped[str] = mapped_column(String(16))
    environment: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))
    profile_name: Mapped[str] = mapped_column(String(128))
    external_user_id: Mapped[str | None] = mapped_column(String(128))
    external_account_id: Mapped[str | None] = mapped_column(String(128))
    identity_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    current_credential_version: Mapped[int | None] = mapped_column(StrictBigInteger())
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
        for name in ("id", "region", "environment", "mode", "profile_name")
    ):
        raise PersistenceValueError()


class CredentialVersion(Base):
    """Immutable credential evidence; full fingerprint is deferred and secret-wrapped.

    Binding/probe/reconciliation UUIDs reference observations whose tables belong
    to later slices. They deliberately do not assert successful verification.
    Ordinary query consumers must use the explicit safe view, never ORM vars().
    """

    __tablename__ = "credential_versions"
    __table_args__ = (
        *venue_checks(),
        mode_check(),
        account_fk("credential_versions"),
        bounded("credential_slot"),
        bounded("observed_external_user_id"),
        bounded("observed_external_account_id"),
        sha_check("fingerprint_sha256"),
        CheckConstraint("credential_version >= 1", name="version_positive"),
        CheckConstraint(
            "observed_external_account_id IS NULL OR observed_external_user_id IS NOT NULL",
            name="observed_identity",
        ),
        UniqueConstraint(*SCOPE, "credential_version", name="uq_credential_versions_scope_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(Uuid)
    region: Mapped[str] = mapped_column(String(16))
    environment: Mapped[str] = mapped_column(String(16))
    mode: Mapped[str] = mapped_column(String(16))
    credential_version: Mapped[int] = mapped_column(StrictBigInteger())
    credential_slot: Mapped[str] = mapped_column(String(128))
    fingerprint_sha256: Mapped[SecretStr] = mapped_column(_Fingerprint(), deferred=True)
    observed_external_user_id: Mapped[str | None] = mapped_column(String(128))
    observed_external_account_id: Mapped[str | None] = mapped_column(String(128))
    binding_ref: Mapped[UUID | None] = mapped_column(Uuid)
    probe_ref: Mapped[UUID | None] = mapped_column(Uuid)
    reconciliation_ref: Mapped[UUID | None] = mapped_column(Uuid)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())

    @validates("fingerprint_sha256")
    def _own_fingerprint(self, key: str, value: object) -> SecretStr:
        return SecretStr(_fingerprint_raw(value))

    def to_safe_dict(self) -> dict[str, str | int]:
        """Return whitelisted query metadata without fingerprint or opaque markers."""
        return {
            "id": str(self.id),
            "credential_version": self.credential_version,
            "recorded_at": self.recorded_at.isoformat(),
        }
