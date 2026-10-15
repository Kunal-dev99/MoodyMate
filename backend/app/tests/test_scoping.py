"""BE-2.5: tenant row scope is derived from the principal, never from the request."""

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.repository import DataScope
from app.governance.constants import DEFAULT_TENANT_ID
from app.governance.repository import UserRepository
from app.tests.conftest import dev_token, insert_tenant, insert_user

pytestmark = pytest.mark.db


def test_cross_tenant_reads_return_nothing_even_with_crafted_id(api: TestClient, db: str) -> None:
    other_tenant = insert_tenant(db, "other-bank")
    foreign_user = insert_user(db, "https://idp.other", "bob", ("platform_admin",), other_tenant)
    admin = dev_token(api, "platform_admin")  # default tenant

    listed = api.get("/api/v1/users", headers=admin).json()
    assert foreign_user not in {uuid.UUID(u["id"]) for u in listed["items"]}
    assert all(u["display_name"] != "User bob" for u in listed["items"])

    crafted = api.get(f"/api/v1/users/{foreign_user}", headers=admin)
    assert crafted.status_code == 404
    assert "bob" not in crafted.text

    # A random ID gets the identical response: no existence oracle across tenants.
    missing = api.get(f"/api/v1/users/{uuid.uuid4()}", headers=admin)
    assert (missing.status_code, missing.json()["code"]) == (404, crafted.json()["code"])


def test_own_tenant_reads_work(api: TestClient, db: str) -> None:
    mine = insert_user(db, "https://idp.test", "carol", ("auditor",))
    r = api.get(f"/api/v1/users/{mine}", headers=dev_token(api, "platform_admin"))
    assert r.status_code == 200
    assert r.json()["roles"] == ["auditor"]


def test_tenant_parameters_in_the_request_are_ignored(api: TestClient, db: str) -> None:
    other_tenant = insert_tenant(db, "other-bank-2")
    insert_user(db, "https://idp.other", "dave", (), other_tenant)
    admin = dev_token(api, "platform_admin")
    r = api.get(
        "/api/v1/users",
        params={"tenant_id": str(other_tenant)},
        headers={**admin, "X-Tenant-ID": str(other_tenant)},
    )
    assert r.status_code == 200
    assert all(u["display_name"] != "User dave" for u in r.json()["items"])


def test_repository_scope_filters_at_the_query(db: str) -> None:
    other_tenant = insert_tenant(db, "other-bank-3")
    foreign = insert_user(db, "https://idp.other", "erin", (), other_tenant)
    local = insert_user(db, "https://idp.test", "frank")

    async def scenario() -> None:
        engine = create_async_engine(db)
        async with async_sessionmaker(engine)() as s:
            repo = UserRepository(s, DataScope(tenant_id=DEFAULT_TENANT_ID))
            assert await repo.get(foreign) is None
            assert (await repo.get(local)) is not None
            users, total = await repo.page(100, 0)
            assert foreign not in {u.id for u in users}
            assert total == len(users)
        await engine.dispose()

    asyncio.run(scenario())
