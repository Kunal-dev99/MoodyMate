"""Phase 6, BE-6.1: environment guard. Client data only where it may live; every response
says which class of data the environment holds."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app

OIDC: dict[str, Any] = {
    "AUTH_MODE": "oidc",
    "OIDC_ISSUER": "https://idp.example",
    "OIDC_AUDIENCE": "moodymate-api",
    "OIDC_CLIENT_ID": "moodymate-web",
}
# Settings each ENV needs to be valid apart from DATA_CLASS.
BASE: dict[str, dict[str, Any]] = {
    "local": {},
    "test": {},
    "staging": OIDC,
    "production": OIDC,
}
# ENV x DATA_CLASS: client data only in staging and production; local and test are synthetic.
MATRIX = [
    ("local", "synthetic", True),
    ("local", "client", False),
    ("test", "synthetic", True),
    ("test", "client", False),
    ("staging", "synthetic", True),
    ("staging", "client", True),
    ("production", "synthetic", True),
    ("production", "client", True),
]


@pytest.mark.parametrize(("env", "data_class", "allowed"), MATRIX)
def test_env_by_data_class(env: str, data_class: str, allowed: bool) -> None:
    def build() -> Settings:
        return Settings(ENV=env, DATA_CLASS=data_class, **BASE[env])

    if allowed:
        assert build().DATA_CLASS == data_class
    else:
        with pytest.raises(
            ValidationError, match=f"DATA_CLASS=client is not allowed when ENV={env}"
        ):
            build()


def test_every_combination_is_covered() -> None:
    envs = ("local", "test", "staging", "production")
    assert {(e, d) for e, d, _ in MATRIX} == {(e, d) for e in envs for d in ("synthetic", "client")}


@pytest.fixture
def plain() -> TestClient:
    return TestClient(
        create_app(Settings(ENV="test", DATABASE_URL="postgresql+asyncpg://x:y@127.0.0.1:1/none"))
    )


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/api/v1/health", 200),
        ("GET", "/api/v1/auth/config", 200),
        ("GET", "/api/v1/me", 401),  # an error envelope carries it too
        ("GET", "/api/v1/no-such-route", 404),
    ],
)
def test_every_response_carries_the_data_class(
    plain: TestClient, method: str, path: str, status: int
) -> None:
    r = plain.request(method, path)
    assert r.status_code == status
    assert r.headers["X-Data-Class"] == "synthetic"


def test_data_class_header_is_readable_cross_origin(plain: TestClient) -> None:
    r = plain.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
    assert "x-data-class" in r.headers["access-control-expose-headers"].lower()


def test_auth_config_reports_the_environment(plain: TestClient) -> None:
    body = plain.get("/api/v1/auth/config").json()
    assert body["environment"] == {"env": "test", "data_class": "synthetic"}
