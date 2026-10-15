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
