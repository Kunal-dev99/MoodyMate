"""Request observability middleware: the access log (BE-34.3) and the HTTP metrics (BE-34.4).

One JSON line per request on the `app.access` logger (`ACCESS_LOG_ENABLED`), carrying only:

* `correlation_id`: the `X-Correlation-ID` the response carries (so they are equal);
* `method`, `route` (the route *template*, e.g. `/api/v1/calculation-runs/{run_id}`, or
  `unmatched`), `status` and `duration_ms`.

It never carries the raw path, the query string, the body, a header or a token (D-62 §3,
D-50 §3). While it is on, uvicorn's own access log (which prints the path with its query) is
silenced; `ACCESS_LOG_ENABLED=false` restores it.

The HTTP series are recorded in process whatever `METRICS_ENABLED` holds; only the internal
listener exposes them. The middleware runs outermost, so it sees every answer: CORS
preflights, and the 500 envelope `CorrelationIdMiddleware` makes of an unhandled exception.
"""

import logging
import time

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics
from app.core.correlation import HEADER, correlation_id_var
from app.core.routes import served_route

access_logger = logging.getLogger("app.access")
_HEADER = HEADER.lower().encode("latin-1")


def route_template(scope: Scope) -> str:
    """The template the request was served under (include prefixes applied, through
    `app.core.routes`), never the raw path: that can carry ids or values."""
    if scope.get("route") is None or scope.get("app") is None:
        return metrics.UNMATCHED_ROUTE
    try:
        served = served_route(Request(scope))
    except Exception:  # noqa: BLE001 - observability never breaks a request
        served = None
    return served.path if served is not None and served.path else metrics.UNMATCHED_ROUTE


class RequestObservabilityMiddleware:
    def __init__(self, app: ASGIApp, *, access_log: bool) -> None:
        self.app = app
        self.access_log = access_log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500  # an answer that never started is a server error
        correlation_id: str | None = None

        async def observe(message: Message) -> None:
            nonlocal status, correlation_id
            if message["type"] == "http.response.start":
                status = int(message["status"])
                for name, value in message.get("headers", []):
                    if name.lower() == _HEADER:
                        correlation_id = value.decode("latin-1")
            await send(message)

        try:
            await self.app(scope, receive, observe)
        finally:
            seconds = time.perf_counter() - started
            method = str(scope.get("method", ""))
            route = route_template(scope)
            # Recorded whatever METRICS_ENABLED holds (in-process counters only): the
            # setting decides whether the internal listener serves them.
            metrics.observe_request(method, route, status, seconds)
            if self.access_log:
                # The formatter reads the correlation id from the context; the inner
                # middleware has already reset it, so the response header's id is set here.
                token = correlation_id_var.set(correlation_id)
                try:
                    access_logger.info(
                        "http_request",
                        extra={
                            "method": method,
                            "route": route,
                            "status": status,
                            "duration_ms": round(seconds * 1000, 3),
                        },
                    )
                finally:
                    correlation_id_var.reset(token)
