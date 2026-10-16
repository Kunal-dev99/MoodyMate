"""Environment controls (BE-6.1, DG-06).

Every response says which class of data this environment holds, so a client, a proxy log
or a person looking at a response can never mistake synthetic data for client data (or
the reverse).
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DATA_CLASS_HEADER = "X-Data-Class"


class DataClassHeaderMiddleware:
    """Adds `X-Data-Class` to every HTTP response, error envelopes included."""

    def __init__(self, app: ASGIApp, data_class: str) -> None:
        self.app = app
        self.data_class = data_class

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[DATA_CLASS_HEADER] = self.data_class
            await send(message)

        await self.app(scope, receive, send_with_header)
