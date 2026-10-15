from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ApiError
from app.main import create_app

ENVELOPE_KEYS = {"code", "message", "details", "correlation_id", "retryable"}


def _app_with_test_routes(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/_test/typed")
    async def typed(n: int) -> dict[str, int]:
        return {"n": n}

    @app.get("/_test/conflict")
    async def conflict() -> None:
        raise ApiError(409, "state_conflict", "Not allowed in this state.", {"state": "ACTIVE"})

    return app


def test_404_envelope(client: TestClient) -> None:
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == "not_found"
    assert body["retryable"] is False
    assert body["correlation_id"] == r.headers["X-Correlation-ID"]


def test_422_envelope_does_not_echo_input(settings: Settings) -> None:
    c = TestClient(_app_with_test_routes(settings))
    r = c.get("/_test/typed", params={"n": "Bearer leaked-token-value"})
    assert r.status_code == 422
    body = r.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == "validation_error"
    assert body["details"][0]["loc"] == ["query", "n"]
    assert "leaked-token-value" not in r.text


def test_500_envelope_without_stack_trace(settings: Settings) -> None:
    c = TestClient(_app_with_test_routes(settings), raise_server_exceptions=False)
    r = c.get("/_test/boom", headers={"X-Correlation-ID": "corr-500-test"})
    assert r.status_code == 500
    body = r.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["code"] == "internal_error"
    assert body["correlation_id"] == "corr-500-test"
    assert r.headers["X-Correlation-ID"] == "corr-500-test"
    for leak in ("Traceback", "RuntimeError", "secret internal detail"):
        assert leak not in r.text


def test_500_envelope_carries_cors_headers(settings: Settings) -> None:
    # A browser must be able to read the 500 envelope cross-origin, or the frontend
    # shows a network error and the correlation ID is lost.
    c = TestClient(_app_with_test_routes(settings), raise_server_exceptions=False)
    r = c.get("/_test/boom", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 500
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-correlation-id" in r.headers["access-control-expose-headers"].lower()
    assert r.json()["correlation_id"] == r.headers["X-Correlation-ID"]


def test_api_error_envelope(settings: Settings) -> None:
    c = TestClient(_app_with_test_routes(settings))
    r = c.get("/_test/conflict")
    assert r.status_code == 409
    assert r.json()["code"] == "state_conflict"
    assert r.json()["details"] == {"state": "ACTIVE"}
