"""Explicit credential-free account bootstrap for an already-migrated database."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from hmt_crypto.config import ExecutionMode, Settings
from hmt_crypto.errors import ConfigurationError
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import Account, AccountLiveControl

_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class AccountBootstrapResult:
    """Safe identity returned after idempotent local account bootstrap."""

    account_id: UUID
    profile_name: str
    created: bool


async def bootstrap_account(
    settings: Settings,
    *,
    profile_name: str,
    confirm_locked_live: bool = False,
) -> AccountBootstrapResult:
    """Create a credential-free profile; LIVE requires an explicit locked confirmation."""
    live = settings.execution_mode is ExecutionMode.LIVE
    if (
        type(settings) is not Settings
        or type(profile_name) is not str
        or _PROFILE.fullmatch(profile_name) is None
        or type(confirm_locked_live) is not bool
        or (live and (not confirm_locked_live or settings.delta_env.value != "prod"))
        or (not live and confirm_locked_live)
    ):
        raise ConfigurationError(context={"operation": "account_bootstrap"}) from None
    database = Database(settings)
    try:
        async with database.transaction() as session:
            now = datetime.now(UTC)
            values = dict(
                id=uuid4(),
                region=settings.delta_region.value,
                environment=settings.delta_env.value,
                mode=settings.execution_mode.value,
                profile_name=profile_name,
                external_user_id=None,
                external_account_id=None,
                identity_verified_at=None,
                current_credential_version=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
            insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
            statement = (
                insert(Account)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["region", "environment", "mode", "profile_name"]
                )
                .returning(Account.id, Account.profile_name)
            )
            inserted = (await session.execute(statement)).one_or_none()
            if inserted is not None:
                result = AccountBootstrapResult(inserted.id, inserted.profile_name, True)
            else:
                winner = (
                    await session.execute(
                        select(Account.id, Account.profile_name).where(
                            Account.region == settings.delta_region.value,
                            Account.environment == settings.delta_env.value,
                            Account.mode == settings.execution_mode.value,
                            Account.profile_name == profile_name,
                        )
                    )
                ).one_or_none()
                if winner is None:
                    raise ConfigurationError(context={"operation": "account_bootstrap"}) from None
                result = AccountBootstrapResult(winner.id, winner.profile_name, False)
            if live:
                control = insert(AccountLiveControl).values(
                    id=uuid4(),
                    account_id=result.account_id,
                    credential_state="unconfigured",
                    reconciliation_state="unknown",
                    entries_enabled=False,
                    exits_enabled=False,
                    verified_at=None,
                    reconciled_at=None,
                    last_error_code=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                await session.execute(control.on_conflict_do_nothing(index_elements=["account_id"]))
        return result
    finally:
        await database.aclose()


__all__ = ["AccountBootstrapResult", "bootstrap_account"]
