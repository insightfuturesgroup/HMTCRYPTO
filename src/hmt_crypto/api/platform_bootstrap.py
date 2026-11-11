"""Operator-controlled first-admin bootstrap with no public HTTP endpoint."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select

from hmt_crypto.api.settings import ControlRuntimeSettings
from hmt_crypto.persistence.db import Database
from hmt_crypto.persistence.models import PlatformAuditEvent, PlatformUser


async def bootstrap_first_admin(
    settings: ControlRuntimeSettings,
    *,
    auth_user_id: UUID,
    email: str,
    full_name: str,
) -> tuple[UUID, bool]:
    """Create exactly the first platform admin or idempotently recover it."""
    normalized = email.strip().casefold()
    name = full_name.strip()
    if auth_user_id.int == 0 or normalized.count("@") != 1 or not 1 <= len(name) <= 128:
        raise ValueError("invalid first-admin identity")
    database = Database(settings.database_runtime_config())
    try:
        async with database.transaction() as session:
            existing = (
                await session.execute(
                    select(PlatformUser).where(PlatformUser.auth_user_id == auth_user_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.email != normalized or existing.role != "admin":
                    raise ValueError("bootstrap identity conflicts with an existing user")
                return existing.id, False
            count = await session.scalar(select(func.count()).select_from(PlatformUser))
            if count != 0:
                raise ValueError("first-admin bootstrap is closed")
            now, profile_id = datetime.now(UTC), uuid4()
            session.add(
                PlatformUser(
                    id=profile_id,
                    auth_user_id=auth_user_id,
                    email=normalized,
                    full_name=name,
                    role="admin",
                    status="active",
                    created_at=now,
                    updated_at=now,
                    last_login_at=None,
                )
            )
            session.add(
                PlatformAuditEvent(
                    id=uuid4(),
                    actor_user_id=profile_id,
                    action="FIRST_ADMIN_BOOTSTRAPPED",
                    target_type="user",
                    target_id=str(profile_id),
                    outcome="SUCCEEDED",
                    request_id=str(uuid4()),
                    details="{}",
                    occurred_at=now,
                )
            )
            return profile_id, True
    finally:
        await database.aclose()


__all__ = ["bootstrap_first_admin"]
