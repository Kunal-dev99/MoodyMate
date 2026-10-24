"""Application factory.

Run with `uvicorn app.main:create_app --factory`. There is deliberately no module-level
`app`: importing this module must never build an app from implicit settings (D-13).
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import build_api_router
from app.calculations.constants import PREFERENCE_APPLIED_HEADER
from app.calculations.executor import RunExecutor
from app.calculations.service import run_sweeper
from app.core import pagination
from app.core.artifacts import build_artifact_store
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
    # BE-33.4: pool settings (SQLAlchemy's defaults) and the statement timeout.
    db = Database(
        settings.DATABASE_URL,
        server_settings=settings.server_settings,
        **settings.engine_options,
    )
    bind_pool(db.pool_stats)  # BE-34.4: the pool series read this app's pool
    # BE-33.1: where module runs execute (inline, or the bounded worker pool).
    executor = RunExecutor.from_settings(settings)
    # BE-33.5: the list cursors' signing key (D-59 §6).
    pagination.configure(settings.cursor_signing_key)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # BE-34.4 (D-62 §2): metrics only on the internal listener, never on this app.
        listener = MetricsListener(settings) if settings.METRICS_ENABLED else None
        if listener is not None:
            await listener.start()
        # BE-33.3: the periodic sweeper of stale RUNNING runs (first pass after an interval).
        sweeper = asyncio.create_task(
            run_sweeper(
                db.sessionmaker,
                settings.CALC_RUN_STALE_SECONDS,
                settings.CALC_SWEEP_INTERVAL_SECONDS,
            )
        )
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            await executor.aclose()  # background runs end (or are ended), workers stop
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
    # D-15: write-once raw artifacts (created on first upload); BE-33.6: filesystem or s3.
    app.state.artifact_store = build_artifact_store(settings)
    app.state.run_executor = executor
    # BE-34.5: the readiness `workers` check asks a pool; inline needs no entry (D-62 §1).
    app.state.workers = executor if executor.pooled else None
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
        # Location and Preference-Applied: a respond-async run's poll URL (D-59 §2).
        expose_headers=[
            HEADER,
            DATA_CLASS_HEADER,
            RETRY_AFTER,
            "Location",
            PREFERENCE_APPLIED_HEADER,
        ],
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
