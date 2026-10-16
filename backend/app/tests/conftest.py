import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

# Never the live dev DB (D-03): tests use their own database.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_test",
)


def _db_reachable(url: str) -> bool:
    async def probe() -> bool:
        try:
            conn = await asyncpg.connect(url.replace("+asyncpg", ""), timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(probe())


@pytest.fixture(scope="session")
def database_url() -> str:
    if not _db_reachable(TEST_DATABASE_URL):
        if os.environ.get("REQUIRE_DB") == "1":
            pytest.fail(f"REQUIRE_DB=1 but test database is unreachable: {TEST_DATABASE_URL}")
        pytest.skip("test database unreachable (set REQUIRE_DB=1 to make this a failure)")
    return TEST_DATABASE_URL


@pytest.fixture
def settings() -> Settings:
    return Settings(ENV="test", DATABASE_URL="postgresql+asyncpg://nobody:x@127.0.0.1:1/none")


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


# --- Phase 2: migrated database, cleanup and helpers -----------------------------------

from collections.abc import Iterator  # noqa: E402
from typing import Any  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.governance.constants import DEFAULT_TENANT_ID  # noqa: E402

# Deletes everything that is not seed data (D-11), children first.
_CLEANUP = [
    "DELETE FROM user_role",
    "DELETE FROM user_account",
    "DELETE FROM role_permission WHERE seeded_by IS NULL",
    "DELETE FROM role WHERE seeded_by IS NULL",
    "DELETE FROM permission WHERE seeded_by IS NULL",
    "DELETE FROM tenant WHERE seeded_by IS NULL",
]


def alembic_config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["skip_logging"] = True
    # Tests reset the schema freely; the audit-trail downgrade guard is tested on its own.
    cfg.attributes["allow_audit_loss"] = True
    return cfg


def sql(url: str, statement: str, *args: Any) -> list[Any]:
    """Run one statement directly with asyncpg (outside the app) and return rows."""

    async def run() -> list[Any]:
        conn = await asyncpg.connect(url.replace("+asyncpg", ""))
        try:
            return list(await conn.fetch(statement, *args))
        finally:
            await conn.close()

    return asyncio.run(run())


def clean(url: str) -> None:
    for statement in _CLEANUP:
        sql(url, statement)


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> str:
    cfg = alembic_config(database_url)
    try:
        clean(database_url)  # previous run's leftovers would block the seed downgrade
    except asyncpg.UndefinedTableError:
        pass
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return database_url


@pytest.fixture
def db(migrated_db: str) -> Iterator[str]:
    clean(migrated_db)
    yield migrated_db
    clean(migrated_db)


@pytest.fixture
def api(db: str) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(ENV="test", DATABASE_URL=db))) as c:
        yield c


def dev_token(client: TestClient, role: str) -> dict[str, str]:
    r = client.post("/api/v1/dev/sessions", json={"role": role})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def insert_user(
    url: str,
    issuer: str,
    subject: str,
    roles: tuple[str, ...] = (),
    tenant_id: Any = DEFAULT_TENANT_ID,
    status: str = "ACTIVE",
) -> Any:
    row = sql(
        url,
        "INSERT INTO user_account (id, tenant_id, idp_issuer, idp_subject, email, display_name,"
        " status, created_by) VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, 'test')"
        " RETURNING id",
        tenant_id,
        issuer,
        subject,
        f"{subject}@example.test",
        f"User {subject}",
        status,
    )
    user_id = row[0]["id"]
    for code in roles:
        sql(
            url,
            "INSERT INTO user_role (user_id, role_id, created_by)"
            " SELECT $1, id, 'test' FROM role WHERE code = $2",
            user_id,
            code,
        )
    return user_id


def insert_tenant(url: str, slug: str) -> Any:
    return sql(
        url,
        "INSERT INTO tenant (id, name, slug, status, created_by)"
        " VALUES (gen_random_uuid(), $1, $1, 'ACTIVE', 'test') RETURNING id",
        slug,
    )[0]["id"]
