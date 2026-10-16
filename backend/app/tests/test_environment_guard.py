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
# ENV x DATA_CLASS (D-14): client data only in production; every other ENV is synthetic.
MATRIX = [
    ("local", "synthetic", True),
    ("local", "client", False),
    ("test", "synthetic", True),
    ("test", "client", False),
    ("staging", "synthetic", True),
    ("staging", "client", False),
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


# --- Phase 6 review follow-ups -------------------------------------------------------------


@pytest.mark.parametrize("env", ["staging", "production"])
def test_deployments_must_state_their_data_class(env: str) -> None:
    """Fail closed (D-13): a deployment that forgets DATA_CLASS must not claim 'synthetic'."""
    with pytest.raises(ValidationError, match=f"DATA_CLASS is required when ENV={env}"):
        Settings(ENV=env, **BASE[env])


@pytest.mark.parametrize("env", ["local", "test"])
def test_local_and_test_default_to_synthetic(env: str) -> None:
    assert Settings(ENV=env).data_class == "synthetic"


def test_cors_preflight_carries_the_data_class(plain: TestClient) -> None:
    r = plain.options(
        "/api/v1/me",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 200
    assert r.headers["X-Data-Class"] == "synthetic"


def test_unhandled_error_carries_the_data_class() -> None:
    app = create_app(Settings(ENV="test", DATABASE_URL="postgresql+asyncpg://x:y@127.0.0.1:1/none"))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    assert r.headers["X-Data-Class"] == "synthetic"
