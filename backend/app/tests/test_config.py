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

from app.core.config import (
    DEV_CURSOR_KEY_DEFAULT,
    IMPORT_HARD_LIMITS,
    REPO_ROOT,
    Settings,
    get_settings,
)
from app.core.logging import REDACTED, JsonFormatter, Sensitive
from app.main import create_app
from app.tests.observability_kit import (
    check_access_log,
    check_health_with_the_database_down,
    check_metrics_are_internal_and_token_gated,
    check_no_vendor_error_tracker,
    check_reporter_got_only_class_and_correlation,
    fake_reporter,
)

DOWN_DSN = "postgresql+asyncpg://x:y@127.0.0.1:1/none"  # an unreachable database
OIDC: dict[str, Any] = {
    "AUTH_MODE": "oidc",
    "OIDC_ISSUER": "https://idp.example",
    "OIDC_AUDIENCE": "moodymate-api",
    "OIDC_CLIENT_ID": "moodymate-web",
}
DEPLOYED = {
    **OIDC,
    "ARTIFACT_STORE_DIR": "/var/lib/moodymate/artifacts",
    "CURSOR_SIGNING_KEY": "cursor-signing-key-for-tests-0123456789abcdef",  # D-59 §6
}
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
            **{k: v for k, v in DEPLOYED.items() if k != "ARTIFACT_STORE_DIR"},
        )
        # D-59 §6: the cursor signing key is required in deployments too.
        refused(
            f"CURSOR_SIGNING_KEY is required when ENV={env}",
            ENV=env,
            DATA_CLASS="synthetic",
            **{k: v for k, v in DEPLOYED.items() if k != "CURSOR_SIGNING_KEY"},
        )
        # ... and is never the published development key (D-13).
        refused(
            f"CURSOR_SIGNING_KEY must not be the development key when ENV={env}",
            ENV=env,
            DATA_CLASS="synthetic",
            **{**DEPLOYED, "CURSOR_SIGNING_KEY": DEV_CURSOR_KEY_DEFAULT},
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

    # BE-28.3: rate limits default on; off only in local/test, refused in deployments.
    for env, extra in BASE.items():
        assert Settings(ENV=env, DATA_CLASS="synthetic", **extra).RATE_LIMIT_ENABLED is True
        if env in ("local", "test"):
            assert Settings(ENV=env, RATE_LIMIT_ENABLED=False).RATE_LIMIT_ENABLED is False
        else:
            refused(
                f"RATE_LIMIT_ENABLED=false is not allowed when ENV={env}",
                ENV=env,
                DATA_CLASS="synthetic",
                RATE_LIMIT_ENABLED=False,
                **extra,
            )

    # Phase 33 (BE-33.1, BE-33.3, BE-33.4, BE-33.6; D-59): worker, timeout and DB pool settings
    # are validated at startup. Values of 0 or below are refused (an overflow of 0 is valid);
    # the pool defaults are SQLAlchemy's; inline execution and the filesystem store are opt-out.
    settings = Settings(ENV="test")
    assert (settings.DB_POOL_SIZE, settings.DB_MAX_OVERFLOW, settings.DB_POOL_TIMEOUT_SECONDS) == (
        5,
        10,
        30,
    )
    assert settings.CALC_EXECUTION_MODE in ("inline", "pool")
    assert settings.ARTIFACT_STORE_KIND == "filesystem"
    assert settings.CALC_RUN_TIMEOUT_SECONDS < settings.CALC_RUN_STALE_SECONDS
    positive = (
        "DB_POOL_SIZE",
        "DB_POOL_TIMEOUT_SECONDS",
        "DB_POOL_RECYCLE_SECONDS",
        "DB_STATEMENT_TIMEOUT_MS",
        "CALC_WORKERS",
        "CALC_RUN_TIMEOUT_SECONDS",
        "CALC_SWEEP_INTERVAL_SECONDS",
    )
    for name in positive:
        assert getattr(Settings.model_validate({"ENV": "test", name: 1}), name) == 1, name
        for bad in (0, -1):
            refused(name, ENV="test", **{name: bad})
    for name in ("DB_MAX_OVERFLOW", "CALC_WORKER_QUEUE_MAX"):  # 0: no overflow, no queue
        assert getattr(Settings.model_validate({"ENV": "test", name: 0}), name) == 0, name
        refused(name, ENV="test", **{name: -1})
    for mode in ("inline", "pool"):
        assert Settings(ENV="test", CALC_EXECUTION_MODE=mode).CALC_EXECUTION_MODE == mode
    # BE-33.1/BE-33.4: in pool mode the admitted runs (workers + queue) stay below the DB
    # pool's connections; inline admits no bound, so a pool of 1 stays valid there.
    crowded: dict[str, Any] = {"CALC_WORKERS": 2, "CALC_WORKER_QUEUE_MAX": 13}  # 15 = 5 + 10
    assert Settings(ENV="test", **crowded).CALC_WORKER_QUEUE_MAX == 13
    pooled = {**crowded, "CALC_EXECUTION_MODE": "pool"}
    refused("CALC_WORKERS \\+ CALC_WORKER_QUEUE_MAX must be below", ENV="test", **pooled)
    assert Settings(ENV="test", **pooled, DB_MAX_OVERFLOW=11).CALC_WORKER_QUEUE_MAX == 13
    refused("CALC_EXECUTION_MODE", ENV="test", CALC_EXECUTION_MODE="threads")
    # BE-33.3: the run timeout sits below the stale threshold.
    refused(
        "CALC_RUN_TIMEOUT_SECONDS",
        ENV="test",
        CALC_RUN_TIMEOUT_SECONDS=60,
        CALC_RUN_STALE_SECONDS=60,
    )
    # BE-33.6 (D-13): the S3 store's settings are required outside local/test.
    refused("ARTIFACT_STORE_KIND", ENV="test", ARTIFACT_STORE_KIND="tape")
    for env, extra in BASE.items():
        if env in ("staging", "production"):
            with pytest.raises(ValidationError, match="ARTIFACT_S3_"):
                Settings(ENV=env, DATA_CLASS="synthetic", ARTIFACT_STORE_KIND="s3", **extra)


def test_responses_carry_the_envelope_correlation_id_and_data_class() -> None:
    app = create_app(Settings(ENV="test", DATABASE_URL=DOWN_DSN))

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
    # BE-34.6: the 500 reaches the error-tracker hook with only the class and correlation id.
    with fake_reporter() as calls:
        assert c.get("/_test/boom", headers={"X-Correlation-ID": "corr-test-2"}).status_code == 500
    check_reporter_got_only_class_and_correlation(calls, RuntimeError, "corr-test-2")
    check_no_vendor_error_tracker()
    # BE-34.5: DB down -> live 200, ready 503 `database: fail`, value-free. BE-34.4: metrics
    # are on the internal listener only, token-gated when METRICS_TOKEN is set.
    check_health_with_the_database_down(c, DOWN_DSN)
    check_metrics_are_internal_and_token_gated(app.state.settings)
    preflight = c.options(
        "/api/v1/me",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 200 and preflight.headers["X-Data-Class"] == "synthetic"
    secure(preflight, hsts=False)

    # BE-28.2: security headers on every response; HSTS only in staging/production.
    for path, _status, _code in cases:
        secure(c.get(path), hsts=False)
    local = create_app(Settings(ENV="local", DATABASE_URL="postgresql+asyncpg://x:y@127.0.0.1:1/n"))
    secure(TestClient(local).get("/api/v1/health"), hsts=False)
    for env in ("staging", "production"):
        deployed = Settings(
            ENV=env,
            DATA_CLASS="synthetic",
            DATABASE_URL="postgresql+asyncpg://x:y@127.0.0.1:1/none",
            **DEPLOYED,
        )
        r = TestClient(create_app(deployed)).get("/api/v1/health")
        assert r.status_code == 200, env
        secure(r, hsts=True)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def secure(r: Any, *, hsts: bool) -> None:
    """BE-28.2: the fixed security headers, and HSTS exactly when the ENV is deployed."""
    for name, value in SECURITY_HEADERS.items():
        assert r.headers.get(name) == value, (r.request.url.path, name, r.headers.get(name))
    if hsts:
        value = r.headers.get("Strict-Transport-Security", "")
        max_age = value.split(";")[0].strip()
        assert max_age.startswith("max-age=") and int(max_age[8:]) >= 31_536_000, value
    else:
        assert "Strict-Transport-Security" not in r.headers, r.request.url.path


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

    # BE-33.6: S3 credentials never reach a log line, the settings' repr or a dump of them.
    s3_secret, s3_key_id = "S3-SECRET-do-not-log-0123456789", "AKIA-DO-NOT-LOG-42"
    s3_settings = Settings(
        ENV="test",
        ARTIFACT_STORE_KIND="s3",
        ARTIFACT_S3_BUCKET="moodymate-artifacts",
        ARTIFACT_S3_ACCESS_KEY_ID=s3_key_id,
        ARTIFACT_S3_SECRET_ACCESS_KEY=s3_secret,
    )
    assert s3_settings.ARTIFACT_STORE_KIND == "s3"
    logger.info(
        "artifact store configured",
        extra={"settings": s3_settings.model_dump(), "aws_secret_access_key": s3_secret},
    )
    logger.info("store %r settings %s", s3_settings, s3_settings)
    out = buf.getvalue()
    for leaked in (s3_secret, s3_key_id):
        assert leaked not in out and leaked not in repr(s3_settings), leaked
    # BE-34.3: one JSON access line per request; never a query, body, header or token.
    check_access_log(lambda **kw: create_app(Settings(ENV="test", DATABASE_URL=DOWN_DSN, **kw)))
