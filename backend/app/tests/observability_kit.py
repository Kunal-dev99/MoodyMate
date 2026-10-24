"""Phase 34 (D-62) test helpers, folded into existing test functions (D-26: 49 -> 49).

The interfaces these helpers expect, which the implementation must provide (or this kit is
adjusted to, in one place):

* `Settings.ACCESS_LOG_ENABLED`, `METRICS_ENABLED` (default False), `METRICS_TOKEN`,
  `ERROR_TRACKER_DSN`.
* Access log (BE-34.3): one JSON line per request through the root logger (JsonFormatter),
  carrying `correlation_id`, `method`, `route` (the route template), `status` and
  `duration_ms`, either top level or under `extra`.
* Metrics (BE-34.4): `app.core.metrics.create_metrics_app(settings)` returns the internal
  listener's ASGI app, serving `GET /metrics` in Prometheus text format; when `METRICS_TOKEN`
  is set it requires `Authorization: Bearer <token>` (otherwise 401 with
  `WWW-Authenticate: Bearer`, per the 401 ruling; the token compare is constant-time).
* Error tracker (BE-34.6): `app.core.error_tracker.report_exception(exc, correlation_id)` and
  `set_reporter(reporter) -> previous`; the default reporter is a no-op.
* `docs/ops/TRIGGERS.md` (BE-34.7): a markdown table whose first cell holds the migration
  revision (`0012` or `0012_import_tables`) and whose second cell holds the trigger name(s) in
  backticks.
"""

import importlib
import json
import logging
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import __version__
from app.core.config import REPO_ROOT
from app.core.logging import JsonFormatter

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
ACCESS_FIELDS = {"correlation_id", "method", "route", "status", "duration_ms"}
READY_CHECKS = {"database", "migrations", "artifact_store", "workers"}

# BE-34.4: the series D-62 §2 names. No tenant, principal or id label.
METRIC_FAMILIES = (
    "moodymate_http_request_duration_seconds",
    "moodymate_http_request_errors",
    "moodymate_run_duration_seconds",
    "moodymate_runs",
    "moodymate_worker_queue_depth",
    "moodymate_worker_busy",
    "moodymate_import_failures",
    "moodymate_db_pool_in_use",
    "process_resident_memory_bytes",
)
FORBIDDEN_LABELS = {
    "tenant", "tenant_id", "principal", "principal_id", "user", "user_id", "subject", "id",
    "run_id", "import_id", "correlation_id", "source_id", "path",
}  # fmt: skip

# BE-34.6: no vendor error-tracker SDK (D-62 §4 hard stop).
VENDOR_SDKS = (
    "sentry_sdk", "rollbar", "bugsnag", "honeybadger", "airbrake", "newrelic", "ddtrace",
    "elasticapm", "raygun4py", "opbeat",
)  # fmt: skip


# --- BE-34.3: access log ------------------------------------------------------------------


@contextmanager
def captured_json_lines() -> Iterator[list[str]]:
    """Every record reaching the root logger, formatted by the app's JsonFormatter. Attach
    after create_app (configure_logging replaces the root handlers)."""

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if not record.name.startswith(("httpx", "httpcore")):  # the test client's own log
                lines.append(self.format(record))

    lines: list[str] = []
    handler = Collect(level=logging.DEBUG)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield lines
    finally:
        root.removeHandler(handler)


def access_records(lines: list[str]) -> list[dict[str, Any]]:
    """The access-log lines, with `extra` merged into the top level."""
    out = []
    for line in lines:
        record = json.loads(line)
        merged = {**record, **(record.get("extra") or {})}
        if {"method", "route", "status"} <= set(merged):
            out.append(merged)
    return out


def check_access_log(make_app: Callable[..., FastAPI]) -> None:
    """A request with a bearer token, a sentinel query, header and body gives exactly one
    access line: correlation id (= the response header), method, route template, status and
    duration, and no sentinel, path id or token. ACCESS_LOG_ENABLED=false gives none."""
    token, query, body, header = "zzqtokensentinel77", "ZZQ-QUERY-77", "ZZQ-BODY-77", "ZZQ-HDR-77"
    path_id = "5f0c2a4e-1b7d-4c39-9e61-7a2b8c4d0e13"
    app = make_app(ACCESS_LOG_ENABLED=True)
    with captured_json_lines() as lines, TestClient(app, raise_server_exceptions=False) as c:
        r = c.post(
            f"/api/v1/imports/{path_id}/validate?note={query}",
            json={"note": body},
            headers={"Authorization": f"Bearer {token}", "X-Note": header},
        )
    records = access_records(lines)
    assert len(records) == 1, lines
    [line] = records
    assert ACCESS_FIELDS <= set(line), line
    assert line["correlation_id"] == r.headers["X-Correlation-ID"]
    assert (line["method"], line["route"], line["status"]) == (
        "POST",
        "/api/v1/imports/{import_id}/validate",
        r.status_code,
    ), line
    assert isinstance(line["duration_ms"], int | float) and line["duration_ms"] >= 0
    text = "\n".join(lines)
    for leaked in (token, query, body, header, path_id, "Authorization", "X-Note"):
        assert leaked not in text, leaked

    quiet = make_app(ACCESS_LOG_ENABLED=False)
    with captured_json_lines() as lines, TestClient(quiet) as c:
        c.get("/api/v1/health/live")
    assert access_records(lines) == [], lines


# --- BE-34.5: health ----------------------------------------------------------------------


class _NoIo:
    """Stands in for app.state.db: any use fails, so /health/live proves it does no I/O."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"/health/live touched the database ({name})")


def check_health_with_the_database_down(c: TestClient, dsn: str) -> None:
    """With the DB unreachable: live 200, ready 503 `database: fail` within the 2 s bound, and
    no DSN, host or version string in either body. Both need no credentials (PUBLIC_PATHS)."""
    started = time.monotonic()
    ready = c.get("/api/v1/health/ready")
    assert time.monotonic() - started < 5, "the DB check must give up within 2 s"
    assert ready.status_code == 503, ready.text
    body = ready.json()
    assert set(body) == {"status", "checks"} and set(body["checks"]) == READY_CHECKS, body
    assert body["status"] == "fail" and body["checks"]["database"] == "fail", body
    assert set(body["checks"].values()) <= {"ok", "fail"}, body
    db = c.app.state.db  # type: ignore[attr-defined]
    c.app.state.db = _NoIo()  # type: ignore[attr-defined]
    try:
        live = c.get("/api/v1/health/live")
    finally:
        c.app.state.db = db  # type: ignore[attr-defined]
    assert (live.status_code, live.json()) == (200, {"status": "ok"}), live.text
    host = dsn.split("@")[1].split("/")[0]
    for r in (ready, live):
        for leaked in (dsn, host, host.split(":")[0], "postgresql", "asyncpg", __version__):
            assert leaked not in r.text, (r.request.url.path, leaked)


def check_ready_reports_the_migration_head(api: TestClient, *, at_head: bool) -> None:
    """Ready is 200 at the head and 503 `migrations: fail` off it; never a revision string."""
    r = api.get("/api/v1/health/ready")
    body = r.json()
    assert set(body.get("checks", {})) == READY_CHECKS, r.text
    if at_head:
        assert (r.status_code, body["status"]) == (200, "ok"), r.text
        assert set(body["checks"].values()) == {"ok"}, r.text
    else:
        assert (r.status_code, body["status"]) == (503, "fail"), r.text
        assert body["checks"]["migrations"] == "fail" and body["checks"]["database"] == "ok"
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    for rev in script.walk_revisions():
        assert f'"{rev.revision}"' not in r.text and rev.revision not in body["checks"].values()


# --- BE-34.4: metrics ---------------------------------------------------------------------


def metrics_client(settings: Any) -> TestClient:
    metrics = importlib.import_module("app.core.metrics")
    return TestClient(metrics.create_metrics_app(settings))


def check_metrics_are_internal_and_token_gated(settings: Any) -> None:
    """The public app has no /metrics, even with metrics on; the internal listener enforces
    METRICS_TOKEN when set; METRICS_ENABLED defaults off."""
    from app.main import create_app

    assert type(settings).model_fields["METRICS_ENABLED"].default is False
    for enabled in (False, True):
        public = TestClient(create_app(settings.model_copy(update={"METRICS_ENABLED": enabled})))
        for path in ("/metrics", "/api/v1/metrics", "/api/metrics"):
            assert public.get(path).status_code == 404, (enabled, path)
    gated = settings.model_copy(update={"METRICS_ENABLED": True, "METRICS_TOKEN": "mt-77-secret"})
    c = metrics_client(gated)
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "mt-77-secret"}):
        r = c.get("/metrics", headers=headers)
        assert r.status_code == 401 and "# TYPE" not in r.text, (headers, r.status_code)
        assert r.headers.get("www-authenticate") == "Bearer", (headers, dict(r.headers))
    ok = c.get("/metrics", headers={"Authorization": "Bearer mt-77-secret"})
    assert ok.status_code == 200 and "# TYPE" in ok.text
    open_ = metrics_client(settings.model_copy(update={"METRICS_ENABLED": True}))
    assert open_.get("/metrics").status_code == 200


def check_metrics_after_a_run_and_a_failed_import(api: TestClient, failed_reason: str) -> None:
    """After a run and a failed import (the caller did both): every D-62 §2 family is present,
    the import failure is counted under its reason code, no forbidden label and no UUID."""
    from prometheus_client.parser import text_string_to_metric_families

    settings = api.app.state.settings.model_copy(update={"METRICS_ENABLED": True})  # type: ignore[attr-defined]
    r = metrics_client(settings).get("/metrics")
    assert r.status_code == 200, r.text
    families = {f.name: f for f in text_string_to_metric_families(r.text)}
    missing = [name for name in METRIC_FAMILIES if name not in families]
    assert missing == [], (missing, sorted(families))
    for family in families.values():
        for sample in family.samples:
            assert not FORBIDDEN_LABELS & set(sample.labels), (sample.name, sample.labels)
    assert any(
        s.labels.get("reason_code") == failed_reason and s.value >= 1
        for s in families["moodymate_import_failures"].samples
    ), families["moodymate_import_failures"].samples
    assert sum(s.value for s in families["moodymate_runs"].samples) >= 1
    assert UUID.search(r.text) is None, UUID.search(r.text)


# --- BE-34.6: error tracker ---------------------------------------------------------------


@contextmanager
def fake_reporter() -> Iterator[list[tuple[tuple[Any, ...], dict[str, Any]]]]:
    tracker = importlib.import_module("app.core.error_tracker")
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    previous = tracker.set_reporter(lambda *a, **k: calls.append((a, k)))
    try:
        yield calls
    finally:
        tracker.set_reporter(previous)


def check_reporter_got_only_class_and_correlation(
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]], exc_type: type, correlation: str
) -> None:
    assert len(calls) == 1, calls
    args, kwargs = calls[0]
    values = [*args, *kwargs.values()]
    names = (exc_type, exc_type.__name__, f"{exc_type.__module__}.{exc_type.__qualname__}")
    assert len(values) == 2 and correlation in values, calls
    [other] = [v for v in values if v != correlation]
    assert any(other is n or other == n for n in names), other


def check_no_vendor_error_tracker() -> None:
    backend = REPO_ROOT / "backend"
    sources = [p for p in (backend / "app").rglob("*.py") if "tests" not in p.parts]
    pattern = re.compile(rf"^\s*(import|from)\s+({'|'.join(VENDOR_SDKS)})\b", re.M)
    hits = [str(p) for p in sources if pattern.search(p.read_text())]
    assert hits == [], hits
    deps = (backend / "pyproject.toml").read_text().lower()
    for sdk in VENDOR_SDKS:
        assert sdk.replace("_", "-") not in deps and sdk not in deps, sdk
    tracker = importlib.import_module("app.core.error_tracker")
    assert tracker.report_exception(RuntimeError("x"), "c-1") is None  # default: no-op


# --- BE-34.7: TRIGGERS.md equals the parsed CREATE TRIGGER set ----------------------------

_CREATE_TRIGGER = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\s+([a-z0-9_]+)", re.I
)


def migration_triggers() -> set[tuple[str, str]]:
    """(revision, trigger) for every CREATE TRIGGER a migration's upgrade() executes. Each
    upgrade() runs against a recording `op` (no database), so templated names resolve."""
    found: dict[str, str] = {}
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    for rev in reversed(list(script.walk_revisions())):
        statements: list[str] = []

        def record(
            statement: Any, *_: Any, _into: list[str] = statements, **__: Any
        ) -> mock.MagicMock:
            _into.append(str(statement))
            return mock.MagicMock()

        op = mock.MagicMock()
        op.execute.side_effect = record
        op.get_bind.return_value.execute.side_effect = record
        with mock.patch.object(rev.module, "op", op, create=True):
            rev.module.upgrade()
        for statement in statements:
            for name in _CREATE_TRIGGER.findall(statement):
                found.setdefault(name, rev.revision)
    return {(revision, name) for name, revision in found.items()}


def documented_triggers(path: Path | None = None) -> set[tuple[str, str]]:
    path = path or REPO_ROOT / "docs" / "ops" / "TRIGGERS.md"
    out: set[tuple[str, str]] = set()
    for line in path.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.lstrip().startswith("|") or len(cells) < 2:
            continue
        revision = re.match(r"`?(\d{4})", cells[0])
        if revision:
            out |= {(revision.group(1), t) for t in re.findall(r"`([a-z0-9_]+)`", cells[1])}
    return out


def check_triggers_doc() -> None:
    parsed = migration_triggers()
    assert len(parsed) > 100, len(parsed)  # the parser sees the templated triggers too
    documented = documented_triggers()
    assert documented == parsed, {
        "undocumented": sorted(parsed - documented),
        "not_in_migrations": sorted(documented - parsed),
    }
