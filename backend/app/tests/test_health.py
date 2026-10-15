import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.core.config import Settings
from app.main import create_app


def test_health_degraded_when_db_unreachable(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "degraded",
        "version": __version__,
        "env": "test",
        "database": "unreachable",
    }


@pytest.mark.db
def test_health_ok_with_db(database_url: str) -> None:
    with TestClient(create_app(Settings(ENV="test", DATABASE_URL=database_url))) as c:
        body = c.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_health_needs_no_auth(client: TestClient) -> None:
    assert client.get("/api/v1/health").status_code == 200
