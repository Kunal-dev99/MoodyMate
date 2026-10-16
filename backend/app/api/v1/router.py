"""Aggregates module routers under /api/v1.

Fail closed (D-04): the app-level guard (app.core.auth.enforce_declared_permission,
installed in app.main) denies any /api/v1 route that does not declare a permission, and
test_route_registry fails CI if one is added.
"""

from fastapi import APIRouter

from app.api.v1 import health
from app.core.config import Settings
from app.core.errors import ERROR_RESPONSES
from app.governance import router as governance
from app.ingestion import router as ingestion


def build_api_router(settings: Settings) -> APIRouter:
    api_router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)
    api_router.include_router(health.router)
    api_router.include_router(governance.auth_router)
    api_router.include_router(governance.router)
    api_router.include_router(ingestion.router)
    if settings.dev_sessions_enabled:
        api_router.include_router(governance.dev_router)
    return api_router
