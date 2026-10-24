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
from app.core.artifacts import FilesystemArtifactStore
from app.core.auth import enforce_declared_permission
from app.core.config import Settings, get_settings
from app.core.correlation import HEADER, CorrelationIdMiddleware
from app.core.environment import DATA_CLASS_HEADER, DataClassHeaderMiddleware
from app.core.error_tracker import configure_error_tracker
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.metrics import MetricsListener, bind_pool
from app.core.observability import RequestObservabilityMiddleware
from app.core.ratelimit import RETRY_AFTER, RateLimiter, buckets_from
from app.core.security_headers import HSTS_ENVS, SecurityHeadersMiddleware
from app.core.tokens import build_verifier
from app.db.session import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.LOG_LEVEL, access_log=settings.ACCESS_LOG_ENABLED)
    configure_error_tracker(settings.ERROR_TRACKER_DSN is not None)  # BE-34.6: a no-op
    db = Database(settings.DATABASE_URL)
    bind_pool(db.pool_stats)  # BE-34.4: the pool series read this app's pool

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # BE-34.4 (D-62 §2): metrics only on the internal listener, never on this app.
        listener = MetricsListener(settings) if settings.METRICS_ENABLED else None
        if listener is not None:
            await listener.start()
        try:
            yield
        finally:
            if listener is not None:
                await listener.stop()
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
    # D-15: write-once raw artifacts (created on first upload).
    app.state.artifact_store = FilesystemArtifactStore(settings.artifact_store_dir)
    # D-53: one limiter per app (per process); None when RATE_LIMIT_ENABLED is off.
    app.state.rate_limiter = (
        RateLimiter(buckets_from(settings)) if settings.RATE_LIMIT_ENABLED else None
    )

    register_error_handlers(app)
    app.include_router(build_api_router(settings))

    # Middleware order: the last added runs outermost. CORS must be outside the
    # correlation middleware so that the 500 envelope CorrelationIdMiddleware produces for
    # unhandled errors still gets CORS headers; otherwise a browser on another origin
    # blocks it and loses the correlation ID.
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[HEADER, DATA_CLASS_HEADER, RETRY_AFTER],
    )
    # Outermost (BE-6.1): every response carries it, including CORS preflights that the
    # CORS middleware answers itself and the 500 envelope from CorrelationIdMiddleware.
    app.add_middleware(DataClassHeaderMiddleware, data_class=settings.data_class)
    # Outermost as well (BE-28.2, D-54): the same reach as X-Data-Class.
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.ENV in HSTS_ENVS)
    # The very outermost (BE-34.3/34.4): it times and logs every answer, preflights and the
    # 500 envelope included, and reads the correlation id from the response header.
    app.add_middleware(
        RequestObservabilityMiddleware,
        access_log=settings.ACCESS_LOG_ENABLED,
    )
    return app
