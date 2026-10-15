"""Correlation-ID middleware (BE-1.4).

Accepts a well-formed inbound `X-Correlation-ID` or generates one, exposes it through a
context variable for logging and error envelopes, and echoes it on every response —
including responses for unhandled exceptions, which this middleware converts into the
standard error envelope so the header is never lost.
"""

import logging
import re
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Correlation-ID"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

logger = logging.getLogger("app.request")


def get_correlation_id() -> str | None:
    cid: str | None = correlation_id_var.get()
    return cid


def _resolve(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.decode("latin-1").lower() == HEADER.lower():
            candidate: str = value.decode("latin-1")
            # Reject anything that could inject into logs or headers.
            if _SAFE_ID.match(candidate):
                return candidate
            break
    return str(uuid.uuid4())


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cid = _resolve(scope)
        token = correlation_id_var.set(cid)
        response_started = False

        async def send_with_header(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                MutableHeaders(scope=message)[HEADER] = cid
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        except Exception:
            logger.exception("unhandled_exception", extra={"path": scope.get("path")})
            if response_started:
                raise
            # Imported here to avoid a cycle: errors uses get_correlation_id.
            from app.core.errors import internal_error_response

            await internal_error_response(cid)(scope, receive, send_with_header)
        finally:
            correlation_id_var.reset(token)
