"""Local start-script helper (scripts/db_check.py): diagnose why the backend cannot sign in
to its database, without echoing the password. Phase 33 (BE-33.4, D-59 §6), folded in (+0):
the engine applies DB_STATEMENT_TIMEOUT_MS, and an exhausted pool is a prompt 503
db_pool_exhausted, never a hang. BE-33.7 (D-59 §7), folded in (+0): the 5,000-flow perf
fold stays under 3x the values recorded in docs/evidence/phase-33/perf.json."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.main import create_app
from scripts import perf_run
from scripts.db_check import BAD_LOGIN, NO_DATABASE, OK, UNREACHABLE, check


def _with(url: str, **parts: Any) -> str:
    return make_url(url).set(**parts).render_as_string(hide_password=False)


def check_perf_fold(db: str) -> None:
    """BE-33.7: both evidence files exist; the deterministic 5,000-flow set, measured again
    inline and in the pool (each in a fresh process, as the script does), gives the recorded
    result hash in both modes and stays under the recorded ceilings (3x the measured
    duration and peak RSS, the worker's too)."""
    recorded = json.loads(perf_run.DEFAULT_OUT.read_text())
    doc = perf_run.PERFORMANCE_DOC.read_text()
    for need in ("rate-limit", "S3", "Supported size", "Concurrency"):  # BE-33.7 Done-when
        assert need in doc, need
    fold, factor = recorded["fold"], perf_run.CEILING_FACTOR
    assert (fold["flows"], fold["ceiling_factor"]) == (perf_run.FOLD_FLOWS, factor)
    for mode in perf_run.MODES:
        then, ceiling = fold["measured"][mode], fold["ceilings"][mode]
        now = perf_run.measure(perf_run.FOLD_FLOWS, mode, db)
        assert now["result_sha256"] == then["result_sha256"], mode
        assert now["duration_seconds"] < ceiling["duration_seconds"], (mode, now, ceiling)
        assert now["api_peak_rss_mib"] < ceiling["api_peak_rss_mib"], (mode, now, ceiling)
        if mode == "pool":
            worker = then["worker_peak_rss_mib"] * factor
            assert now["worker_peak_rss_mib"] < worker, (mode, now["worker_peak_rss_mib"], worker)


def test_db_check_reports_each_failure(database_url: str, db: str, artifact_dir: Path) -> None:
    assert asyncio.run(check(database_url))[0] == OK
    code, message = asyncio.run(check(_with(database_url, password="not-the-password")))
    assert code == BAD_LOGIN and "not-the-password" not in message
    assert asyncio.run(check(_with(database_url, username="no_such_role_xyz")))[0] == BAD_LOGIN
    assert (
        asyncio.run(check(_with(database_url, database="no_such_database_xyz")))[0] == NO_DATABASE
    )
    unreachable = "postgresql+asyncpg://u:p@127.0.0.1:1/x"
    assert asyncio.run(check(unreachable, connect_timeout=2))[0] == UNREACHABLE

    # BE-33.4: the statement timeout is the setting, on every pooled connection.
    settings = Settings(
        ENV="test",
        DATABASE_URL=db,
        ARTIFACT_STORE_DIR=artifact_dir,
        DB_STATEMENT_TIMEOUT_MS=4321,
        DB_POOL_SIZE=1,
        DB_MAX_OVERFLOW=0,
        DB_POOL_TIMEOUT_SECONDS=1,
    )
    assert (settings.DB_STATEMENT_TIMEOUT_MS, settings.DB_POOL_SIZE) == (4321, 1)
    app = create_app(settings)
    with TestClient(app) as c:
        engine = app.state.db.engine
        portal = c.portal
        assert portal is not None

        async def show() -> Any:
            async with engine.connect() as conn:
                return (await conn.execute(text("SHOW statement_timeout"))).scalar_one()

        assert portal.call(show) == "4321ms"
        token = c.post("/api/v1/dev/sessions", json={"role": "risk_analyst"})
        assert token.status_code == 201, token.text
        headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
        assert c.get("/api/v1/sources", headers=headers).status_code == 200

        # Pool 1, overflow 0, timeout 1: with the only connection held, a request is a prompt,
        # retryable 503 db_pool_exhausted (the envelope, no hang); released, it is served.
        async def hold() -> Any:
            return await engine.connect()

        held = portal.call(hold)
        try:
            t0 = time.monotonic()
            r = c.get("/api/v1/sources", headers=headers)
            assert time.monotonic() - t0 < 5, "the exhausted pool hung the request"
            assert r.status_code == 503, r.text
            assert (r.json()["code"], r.json()["retryable"]) == ("db_pool_exhausted", True)
            assert set(r.json()) == {"code", "message", "details", "correlation_id", "retryable"}
            assert int(r.headers["Retry-After"]) >= 1
        finally:
            portal.call(held.close)
        assert c.get("/api/v1/sources", headers=headers).status_code == 200

    check_perf_fold(db)  # BE-33.7
