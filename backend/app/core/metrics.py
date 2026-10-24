"""Prometheus metrics (BE-34.4, D-62 §2).

The series live in one process-wide `REGISTRY`, and are served only by the internal listener
`build_metrics_app` builds (`METRICS_ENABLED`, default off; optional `METRICS_TOKEN` bearer).
Nothing is mounted on the public app, so `/metrics` there is a plain 404 and the route x role
matrix is unchanged.

Labels are bounded and value-free: an HTTP method, a route *template* (never a raw path), a
status code, a run status or an import refusal/finding code. No tenant, principal, id or
value is ever a label (D-62 §2).

Series:

* `moodymate_http_requests_total{method,route,status}` and
  `moodymate_http_request_duration_seconds{method,route}`: every request;
* `moodymate_http_request_errors_total{method,route,status}`: the 5xx answers;
* `moodymate_runs_total{status}` and `moodymate_run_duration_seconds{status}`: every
  executed calculation run;
* `moodymate_worker_busy` and `moodymate_worker_queue_depth`: runs executing now and runs
  waiting for a worker (inline execution has no queue);
* `moodymate_import_failures_total{stage,reason_code}`: refused uploads (`upload`, the
  refusal code) and imports ending FAILED (`validation`, each file-level blocker code);
* `moodymate_db_pool_in_use`, `moodymate_db_pool_idle`, `moodymate_db_pool_overflow` and
  `moodymate_db_pool_size`: the API process's connection pool;
* `process_resident_memory_bytes` and the other `process_*` series (Linux).

The metrics are per process. Several API processes each need their own listener port, or
one process per container (the backend image runs one).
"""

import asyncio
import contextlib
import hmac
import logging
import socket
from collections.abc import Callable, Iterable, Iterator
from typing import Any, Protocol

import uvicorn
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    make_asgi_app,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import Settings, secret_text

logger = logging.getLogger("app.metrics")

METRICS_PATH = "/metrics"
UNMATCHED_ROUTE = "unmatched"  # a path no route serves: never the raw path (cardinality)

REGISTRY = CollectorRegistry(auto_describe=True)
ProcessCollector(registry=REGISTRY)

HTTP_REQUESTS = Counter(
    "moodymate_http_requests",
    "HTTP requests answered, by method, route template and status code.",
    ("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_ERRORS = Counter(
    "moodymate_http_request_errors",
    "HTTP requests answered with a 5xx status, by method, route template and status code.",
    ("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "moodymate_http_request_duration_seconds",
    "HTTP request latency in seconds, by method and route template.",
    ("method", "route"),
    registry=REGISTRY,
)
RUNS = Counter(
    "moodymate_runs",
    "Calculation runs executed, by terminal status.",
    ("status",),
    registry=REGISTRY,
)
RUN_DURATION = Histogram(
    "moodymate_run_duration_seconds",
    "Calculation run execution time in seconds, by terminal status.",
    ("status",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 900),
    registry=REGISTRY,
)
WORKERS_BUSY = Gauge(
    "moodymate_worker_busy",
    "Calculation runs executing now in this process.",
    registry=REGISTRY,
)
WORKER_QUEUE = Gauge(
    "moodymate_worker_queue_depth",
    "Calculation runs waiting for a worker (always 0 with inline execution).",
    registry=REGISTRY,
)
IMPORT_FAILURES = Counter(
    "moodymate_import_failures",
    "Refused uploads (stage=upload) and imports ending FAILED (stage=validation), by reason "
    "code.",
    ("stage", "reason_code"),
    registry=REGISTRY,
)


class PoolStats(Protocol):
    """The parts of a SQLAlchemy `QueuePool` the pool collector reads."""

    def size(self) -> int: ...
    def checkedout(self) -> int: ...
    def checkedin(self) -> int: ...
    def overflow(self) -> int: ...


class _PoolCollector(Collector):
    """Reads the bound pool at scrape time. One collector for the process: binding a new
    database (each app the tests build) replaces the previous one."""

    def __init__(self) -> None:
        self.pool: Callable[[], PoolStats | None] = lambda: None

    def collect(self) -> Iterable[GaugeMetricFamily]:
        pool = self.pool()
        families = {
            "size": GaugeMetricFamily(
                "moodymate_db_pool_size", "Configured size of the API process's DB pool."
            ),
            "in_use": GaugeMetricFamily(
                "moodymate_db_pool_in_use", "DB pool connections checked out now."
            ),
            "idle": GaugeMetricFamily(
                "moodymate_db_pool_idle", "DB pool connections open and checked in."
            ),
            "overflow": GaugeMetricFamily(
                "moodymate_db_pool_overflow", "DB connections open beyond the pool size."
            ),
        }
        if pool is not None:
            with contextlib.suppress(AttributeError, TypeError):  # a pool without the stats
                values = {
                    "size": pool.size(),
                    "in_use": pool.checkedout(),
                    "idle": pool.checkedin(),
                    "overflow": max(pool.overflow(), 0),
                }
                for name, value in values.items():
                    families[name].add_metric([], value)
        yield from families.values()


_POOL = _PoolCollector()
REGISTRY.register(_POOL)


def bind_pool(pool: Callable[[], PoolStats | None]) -> None:
    """The DB pool the pool series read (`create_app` binds its own database's)."""
    _POOL.pool = pool


def _status_text(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value)


_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def observe_request(method: str, route: str, status: int, seconds: float) -> None:
    method = method if method in _METHODS else "OTHER"  # bounded, whatever a client sends
    code = str(status)
    HTTP_REQUESTS.labels(method, route, code).inc()
    HTTP_LATENCY.labels(method, route).observe(seconds)
    if status >= 500:
        HTTP_ERRORS.labels(method, route, code).inc()


@contextlib.contextmanager
def run_executing() -> Iterator[None]:
    """Counts a run as busy while it executes (inline execution: in this process)."""
    WORKERS_BUSY.inc()
    try:
        yield
    finally:
        WORKERS_BUSY.dec()


def observe_run(status: object, seconds: float) -> None:
    """One executed run: its terminal status (a `CalculationRunStatus` value) and time."""
    label = _status_text(status)
    RUNS.labels(label).inc()
    RUN_DURATION.labels(label).observe(seconds)


def set_worker_queue(depth: int) -> None:
    """For a worker pool (D-59): runs waiting for a worker."""
    WORKER_QUEUE.set(depth)


def count_import_failure(stage: str, reasons: Iterable[object]) -> None:
    """`stage` is `upload` or `validation`; each reason is a refusal or finding code."""
    for reason in sorted({_status_text(r) for r in reasons}):
        IMPORT_FAILURES.labels(stage, reason).inc()


def _unauthorized(scope: Scope, token: str) -> bool:
    expected = f"Bearer {token}".encode()
    for name, value in scope.get("headers", []):
        if name.lower() == b"authorization":
            return not hmac.compare_digest(value, expected)
    return True


async def _plain(send: Send, status: int, body: bytes, headers: list[Any] | None = None) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), *(headers or [])],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_metrics_app(token: str | None = None) -> ASGIApp:
    """The internal listener's app: `GET /metrics` only. With a `token`, a request without
    `Authorization: Bearer <token>` gets 401 and no series."""
    exporter = make_asgi_app(REGISTRY)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        if scope.get("path") != METRICS_PATH:
            await _plain(send, 404, b"not found\n")
            return
        if scope.get("method") not in ("GET", "HEAD"):
            await _plain(send, 405, b"method not allowed\n", [(b"allow", b"GET, HEAD")])
            return
        if token is not None and _unauthorized(scope, token):
            await _plain(send, 401, b"unauthorized\n", [(b"www-authenticate", b"Bearer")])
            return
        await exporter(scope, receive, send)

    return app


def create_metrics_app(settings: Settings) -> ASGIApp:
    """The internal listener's app for these settings (`METRICS_TOKEN` enforced when set)."""
    return build_metrics_app(secret_text(settings.METRICS_TOKEN))


class MetricsListener:
    """The internal listener: its own uvicorn server on `host:port`, started and stopped by
    the app's lifespan. It leaves the process's signal handling to the main server."""

    def __init__(self, settings: Settings) -> None:
        class _Server(uvicorn.Server):
            @contextlib.contextmanager
            def capture_signals(self) -> Iterator[None]:
                yield  # the public server owns SIGINT/SIGTERM

        config = uvicorn.Config(
            create_metrics_app(settings),
            host=settings.METRICS_HOST,
            port=settings.METRICS_PORT,
            lifespan="off",
            access_log=False,
            log_config=None,
        )
        self.server = _Server(config)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Binds the port here, so a port in use stops the app's startup with a clear error
        (inside uvicorn a failed bind exits the process)."""
        config = self.server.config
        try:
            sock = socket.create_server((config.host, config.port), reuse_port=False)
        except OSError as exc:
            raise RuntimeError("METRICS_HOST:METRICS_PORT cannot be bound") from exc
        sock.setblocking(False)
        self._task = asyncio.create_task(self.server.serve(sockets=[sock]))
        logger.info("metrics_listener_started")

    async def stop(self) -> None:
        if self._task is None:
            return
        self.server.should_exit = True
        with contextlib.suppress(Exception):
            await self._task
        self._task = None
