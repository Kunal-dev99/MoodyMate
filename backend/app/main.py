"""Application factory.

Run with `uvicorn app.main:create_app --factory`. There is deliberately no module-level
`app`: importing this module must never build an app from implicit settings (D-13).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import build_api_router
from app.core.auth import enforce_declared_permission
from app.core.config import Settings, get_settings
from app.core.correlation import HEADER, CorrelationIdMiddleware
from app.core.environment import DATA_CLASS_HEADER, DataClassHeaderMiddleware
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.tokens import build_verifier
from app.db.session import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL)
    db = Database(settings.DATABASE_URL)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await db.dispose()

    app = FastAPI(
        title="MoodyMate Financial Risk Platform API",
        version=__version__,
        lifespan=lifespan,
        dependencies=[Depends(enforce_declared_permission)],  # fail closed (D-04)
    )
    app.state.settings = settings
    app.state.db = db
    app.state.token_verifier = build_verifier(settings)

    register_error_handlers(app)
    app.include_router(build_api_router(settings))

    # Middleware order: the last added runs outermost. CORS must be outermost so that
    # the 500 envelope CorrelationIdMiddleware produces for unhandled errors still gets
    # CORS headers; otherwise a browser on another origin blocks it and loses the
    # correlation ID.
    app.add_middleware(CorrelationIdMiddleware)
    # Outside CorrelationIdMiddleware, so the 500 envelope it produces carries it too.
    app.add_middleware(DataClassHeaderMiddleware, data_class=settings.DATA_CLASS)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[HEADER, DATA_CLASS_HEADER],
    )
    return app
