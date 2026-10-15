"""Aggregates module routers under /api/v1.

Every router included here (except health) must declare a permission — enforced by the
route-registry test added in BE-2.4 (D-04).
"""

from fastapi import APIRouter

from app.api.v1 import health
from app.core.errors import ERROR_RESPONSES

api_router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)
api_router.include_router(health.router)
