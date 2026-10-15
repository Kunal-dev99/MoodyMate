"""BE-2.4: every /api/v1 route declares a permission (D-04 fail closed)."""

from fastapi import Depends
from fastapi.testclient import TestClient

from app.core.auth import PUBLIC_PATHS, declared_permission, require_permission, undeclared_routes
from app.core.config import Settings
from app.main import create_app


def test_every_api_route_declares_a_permission() -> None:
    app = create_app(Settings(ENV="test"))
    assert undeclared_routes(app) == []


def test_registry_catches_an_unguarded_route() -> None:
    app = create_app(Settings(ENV="test"))

    @app.get("/api/v1/_unguarded")
    async def unguarded() -> dict[str, str]:
        return {"secret": "data"}

    assert undeclared_routes(app) == ["GET /api/v1/_unguarded"]


def test_unguarded_route_is_denied_at_runtime() -> None:
    app = create_app(Settings(ENV="test"))

    @app.get("/api/v1/_unguarded")
    async def unguarded() -> dict[str, str]:
        return {"secret": "data"}

    r = TestClient(app).get("/api/v1/_unguarded")
    assert r.status_code == 403
    assert r.json()["code"] == "permission_not_declared"
    assert "secret" not in r.text


def test_guarded_route_is_recognised() -> None:
    app = create_app(Settings(ENV="test"))

    @app.get("/api/v1/_guarded", dependencies=[Depends(require_permission("schema:read"))])
    async def guarded() -> None:
        return None

    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/v1/_guarded")
    assert declared_permission(route) == "schema:read"  # type: ignore[arg-type]
    assert "GET /api/v1/_guarded" not in undeclared_routes(app)


def test_public_paths_are_exactly_the_reviewed_set() -> None:
    # Changing this set is a security decision: update the test deliberately.
    assert PUBLIC_PATHS == {
        "/api/v1/health",
        "/api/v1/auth/config",
        "/api/v1/dev/personas",
        "/api/v1/dev/sessions",
    }
