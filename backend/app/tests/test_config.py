"""Environment and platform guards (BE-2.7, BE-6.1, D-13, D-14, D-15, D-16): settings fail
closed, every response carries the error envelope, correlation ID and data class, and logs
never carry tokens or secrets. No database needed."""

import io
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import IMPORT_HARD_LIMITS, REPO_ROOT, Settings, get_settings
from app.core.logging import REDACTED, JsonFormatter, Sensitive
from app.main import create_app

OIDC: dict[str, Any] = {
    "AUTH_MODE": "oidc",
    "OIDC_ISSUER": "https://idp.example",
    "OIDC_AUDIENCE": "moodymate-api",
    "OIDC_CLIENT_ID": "moodymate-web",
}
DEPLOYED = {**OIDC, "ARTIFACT_STORE_DIR": "/var/lib/moodymate/artifacts"}
BASE: dict[str, dict[str, Any]] = {
    "local": {},
    "test": {},
    "staging": DEPLOYED,
    "production": DEPLOYED,
}


def refused(match: str, **values: Any) -> None:
    with pytest.raises(ValidationError, match=match):
        Settings(**values)


def test_settings_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # ENV is required (BE-2.7).
    monkeypatch.delenv("ENV", raising=False)
    refused("ENV", _env_file=None)

    # Dev auth exists only in local/test (D-13), and the refusal does not echo the secret.
    secret = "do-not-print-this-secret-0123456789abcdef"
    for env in ("staging", "production"):
        with pytest.raises(ValidationError) as caught:
            Settings(ENV=env, AUTH_MODE="dev", AUTH_DEV_SECRET=secret)
        assert f"AUTH_MODE=dev is not allowed when ENV={env}" in str(caught.value)
        assert secret[:12] not in str(caught.value)
    for env in ("local", "test"):
        assert Settings(ENV=env, AUTH_MODE="dev").dev_sessions_enabled
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "dev")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            create_app()
    finally:
        get_settings.cache_clear()
    refused("OIDC_ISSUER", ENV="production", AUTH_MODE="oidc")
    refused("OIDC_ISSUER must use https", ENV="staging", **{**OIDC, "OIDC_ISSUER": "http://x/r"})

    # ENV x DATA_CLASS (D-14): client data only in production; deployments must state it.
    for env, extra in BASE.items():
        assert Settings(ENV=env, DATA_CLASS="synthetic", **extra).DATA_CLASS == "synthetic"
        if env == "production":
            assert Settings(ENV=env, DATA_CLASS="client", **extra).DATA_CLASS == "client"
        else:
            refused(
                f"DATA_CLASS=client is not allowed when ENV={env}",
                ENV=env,
                DATA_CLASS="client",
                **extra,
            )
        if env in ("staging", "production"):
            refused(f"DATA_CLASS is required when ENV={env}", ENV=env, **extra)
        else:
            assert Settings(ENV=env).data_class == "synthetic"

    # ARTIFACT_STORE_DIR (D-15): required in deployments, absolute, outside the repository.
    for env in ("staging", "production"):
        refused(
            f"ARTIFACT_STORE_DIR is required when ENV={env}",
            ENV=env,
            DATA_CLASS="synthetic",
            **OIDC,
        )
    for env in ("local", "test"):
        root = Settings(ENV=env).artifact_store_dir
        assert root.is_absolute() and not root.resolve().is_relative_to(REPO_ROOT)
    for inside in (".", "backend", "backend/samples/artifacts"):
        refused(
            "ARTIFACT_STORE_DIR must be outside the repository",
            ENV="test",
            ARTIFACT_STORE_DIR=REPO_ROOT / inside,
        )
    refused("must be an absolute path", ENV="test", ARTIFACT_STORE_DIR=Path("relative/a"))

    # IMPORT_MAX_* (D-16 §1) may lower the hard ceilings, never raise them.
    settings = Settings(ENV="test")
    assert {k: getattr(settings, k) for k in IMPORT_HARD_LIMITS} == IMPORT_HARD_LIMITS
    assert IMPORT_HARD_LIMITS["IMPORT_MAX_BYTES"] == 10 * 1024 * 1024
    for name, ceiling in IMPORT_HARD_LIMITS.items():
        assert getattr(Settings.model_validate({"ENV": "test", name: 1}), name) == 1
        with pytest.raises(ValidationError, match=f"{name} may only lower the hard ceiling"):
            Settings.model_validate({"ENV": "test", name: ceiling + 1})
    assert Settings(ENV="local").AI_ENABLED is False


def test_responses_carry_the_envelope_correlation_id_and_data_class() -> None:
    app = create_app(Settings(ENV="test", DATABASE_URL="postgresql+asyncpg://x:y@127.0.0.1:1/none"))

    @app.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/_test/typed")
    async def typed(n: int) -> dict[str, int]:
        return {"n": n}

    c = TestClient(app, raise_server_exceptions=False)
    envelope = {"code", "message", "details", "correlation_id", "retryable"}
    cases = [
        ("/api/v1/health", 200, None),
        ("/api/v1/me", 401, "not_authenticated"),
        ("/api/v1/no-such-route", 404, "not_found"),
        ("/_test/typed?n=Bearer%20leaked-token-value", 422, "validation_error"),
        ("/_test/boom", 500, "internal_error"),
    ]
    for path, status, code in cases:
        r = c.get(
            path, headers={"X-Correlation-ID": "corr-test-1", "Origin": "http://localhost:3000"}
        )
        assert r.status_code == status, path
        assert r.headers["X-Data-Class"] == "synthetic", path
        assert r.headers["X-Correlation-ID"] == "corr-test-1", path
        exposed = r.headers["access-control-expose-headers"].lower()
        assert "x-data-class" in exposed and "x-correlation-id" in exposed, path
        if code:
            assert set(r.json()) == envelope, path
            assert r.json()["code"] == code and r.json()["correlation_id"] == "corr-test-1"
        for leak in ("Traceback", "RuntimeError", "secret internal detail", "leaked-token-value"):
            assert leak not in r.text, (path, leak)
    # An unsafe inbound correlation ID is replaced, never echoed.
    r = c.get("/api/v1/health", headers={"X-Correlation-ID": "bad id\nINJECTED"})
    assert (
        "INJECTED" not in r.headers["X-Correlation-ID"] and len(r.headers["X-Correlation-ID"]) == 36
    )
    preflight = c.options(
        "/api/v1/me",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 200 and preflight.headers["X-Data-Class"] == "synthetic"


def test_logs_never_carry_tokens_or_secrets() -> None:
    jwt_text = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl"
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.redaction")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info(
        "login attempt with Bearer abc.def.ghi",
        extra={
            "payload": {
                "Authorization": "Bearer xyz-123-token",
                "client_secret": "s3cr3t-value",
                "password": "hunter2",
                "nested": [{"api_key": "AK-999"}],
                "note": f"forwarded {jwt_text}",
                "account_number": Sensitive("GB00TEST12345"),
                "amount": 125.5,
            },
            "signing_key": "K-2",
            "monkey": "visible",
        },
    )
    logger.info("token is %s", f"Bearer {jwt_text}")
    out = buf.getvalue()
    for leaked in ("abc.def.ghi", "xyz-123-token", "s3cr3t-value", "hunter2", "AK-999", "K-2"):
        assert leaked not in out, leaked
    assert jwt_text not in out and "GB00TEST12345" not in out
    record = json.loads(out.splitlines()[0])
    assert record["extra"]["payload"]["amount"] == 125.5  # non-sensitive values kept
    assert record["extra"]["payload"]["client_secret"] == REDACTED
    assert record["extra"]["monkey"] == "visible"  # words merely containing "key" are kept
    assert "correlation_id" in record
