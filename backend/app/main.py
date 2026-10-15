"""Application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.correlation import HEADER, CorrelationIdMiddleware
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
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
    )
    app.state.settings = settings
    app.state.db = db

    register_error_handlers(app)
    app.include_router(api_router)

    # Middleware order: the last added runs outermost. CORS must be outermost so that
    # the 500 envelope CorrelationIdMiddleware produces for unhandled errors still gets
    # CORS headers; otherwise a browser on another origin blocks it and loses the
    # correlation ID.
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[HEADER],
    )
    return app


app = create_app()
