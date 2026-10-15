"""BE-2.1: each identity entity can be created and read back through the ORM."""

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.governance.models import (
    PermissionModel,
    RoleModel,
    RolePermission,
    Tenant,
    UserAccount,
    UserRole,
)

pytestmark = pytest.mark.db


def test_create_and_read_each_identity_entity(db: str) -> None:
    async def scenario() -> None:
        engine = create_async_engine(db)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid.uuid4().hex[:8]
        async with sessions() as s:
            tenant = Tenant(name="T", slug=f"t-{suffix}", created_by="test")
            perm = PermissionModel(code=f"test:{suffix}", created_by="test")
            role = RoleModel(code=f"role_{suffix}", name="Role", created_by="test")
            s.add_all([tenant, perm, role])
            await s.flush()
            user = UserAccount(
                tenant_id=tenant.id,
                idp_issuer="https://idp.test",
                idp_subject=f"sub-{suffix}",
                display_name="Test User",
                created_by="test",
            )
            s.add(user)
            await s.flush()
            s.add_all(
                [
                    UserRole(user_id=user.id, role_id=role.id, created_by="test"),
                    RolePermission(role_id=role.id, permission_id=perm.id, created_by="test"),
                ]
            )
            await s.commit()
            ids = (tenant.id, user.id, role.id, perm.id)

        async with sessions() as s:
            tenant_id, user_id, role_id, perm_id = ids
            assert (await s.get(Tenant, tenant_id)) is not None
            assert (await s.get(PermissionModel, perm_id)) is not None
            loaded = (
                await s.execute(select(UserAccount).where(UserAccount.id == user_id))
            ).scalar_one()
            assert loaded.status == "ACTIVE"
            assert [r.id for r in loaded.roles] == [role_id]
            assert [p.id for p in loaded.roles[0].permissions] == [perm_id]
            assert (await s.get(UserRole, (user_id, role_id))) is not None
            assert (await s.get(RolePermission, (role_id, perm_id))) is not None
        await engine.dispose()

    asyncio.run(scenario())


def test_idp_identity_is_unique(db: str) -> None:
    import asyncpg

    from app.tests.conftest import insert_user

    insert_user(db, "https://idp.test", "dup")
    with pytest.raises(asyncpg.UniqueViolationError):
        insert_user(db, "https://idp.test", "dup")
