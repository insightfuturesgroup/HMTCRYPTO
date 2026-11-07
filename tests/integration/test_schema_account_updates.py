"""Account projection concurrency and immutable operational scope boundaries."""

import pytest
from schema_test_support import account, schema_api
from schema_test_support import schema_db as schema_db
from sqlalchemy.ext.asyncio import AsyncSession

from hmt_crypto.persistence.errors import PersistenceError


@pytest.mark.asyncio
async def test_stale_account_projection_update_cannot_overwrite_committed_version(schema_db):
    owner = account()
    async with schema_db.transaction() as session:
        session.add(owner)
    # Read/close the stale session's SQLite snapshot before the competing commit;
    # the detached object's expected version must still guard its later update.
    stale_session = AsyncSession(schema_db.engine, expire_on_commit=False)
    try:
        stale = await stale_session.get(schema_api().Account, owner.id)
        await stale_session.commit()
        async with schema_db.transaction() as session:
            fresh = await session.get(schema_api().Account, owner.id)
            fresh.external_user_id = "verified-user"
            fresh.identity_verified_at = owner.created_at
        stale.external_user_id = "stale-user"
        stale.identity_verified_at = owner.created_at
        from sqlalchemy.orm.exc import StaleDataError

        with pytest.raises(StaleDataError):
            await stale_session.commit()
        await stale_session.rollback()
    finally:
        await stale_session.close()
    async with schema_db.transaction() as session:
        stored = await session.get(schema_api().Account, owner.id)
        assert stored.external_user_id == "verified-user"
        assert stored.version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column,value",
    [
        ("region", "global"),
        ("environment", "prod"),
        ("mode", "LIVE"),
        ("profile_name", "adopt-other-profile"),
    ],
)
async def test_account_operational_identity_cannot_be_reassigned_by_orm(schema_db, column, value):
    owner = account()
    async with schema_db.transaction() as session:
        session.add(owner)
    with pytest.raises(PersistenceError):
        async with schema_db.transaction() as session:
            stored = await session.get(schema_api().Account, owner.id)
            setattr(stored, column, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_version", [1, 3])
async def test_manual_version_reset_fails_and_stale_projection_stays_rejected(
    schema_db, manual_version
):
    from sqlalchemy.orm.exc import StaleDataError

    api = schema_api()
    owner = account()
    async with schema_db.transaction() as session:
        session.add(owner)
    async with AsyncSession(schema_db.engine, expire_on_commit=False) as stale_session:
        stale = await stale_session.get(api.Account, owner.id)
        await stale_session.commit()
        async with schema_db.transaction() as session:
            fresh = await session.get(api.Account, owner.id)
            fresh.external_user_id = "verified-user"
            fresh.identity_verified_at = owner.created_at
        with pytest.raises(PersistenceError):
            async with schema_db.transaction() as session:
                reset = await session.get(api.Account, owner.id)
                assert reset.version == 2
                reset.version = manual_version
        stale.external_user_id = "stale-overwrite"
        stale.identity_verified_at = owner.created_at
        with pytest.raises(StaleDataError):
            await stale_session.commit()
        await stale_session.rollback()
    async with schema_db.transaction() as session:
        current = await session.get(api.Account, owner.id)
        assert current.version == 2
        assert current.external_user_id == "verified-user"
