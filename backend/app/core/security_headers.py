"""Security headers on every response (BE-28.2, D-54 §1-§2).

Error envelopes, CORS preflights and the 500 envelope included: the middleware is added
outermost. HSTS only when ENV is staging or production: TLS is terminated in front of the
app there, and browsers ignore HSTS over plain http anyway.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}
HSTS_HEADER = "Strict-Transport-Security"
HSTS_VALUE = "max-age=31536000; includeSubDomains"  # D-54 §2: no preload
HSTS_ENVS = ("staging", "production")


class SecurityHeadersMiddleware:
    """Sets the fixed security headers (and HSTS when `hsts`) on every HTTP response."""

    def __init__(self, app: ASGIApp, hsts: bool) -> None:
        self.app = app
        self.headers = {**SECURITY_HEADERS, **({HSTS_HEADER: HSTS_VALUE} if hsts else {})}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
