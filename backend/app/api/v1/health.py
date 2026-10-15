"""Health endpoint (BE-1.7). Deliberately unauthenticated.

Returns only liveness, API version, environment name and DB reachability; no business
or user data.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["platform"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    env: str
    database: Literal["ok", "unreachable"]


@router.get("/health", response_model=HealthResponse, operation_id="getHealth")
async def health(request: Request) -> HealthResponse:
    try:
        db_ok = await asyncio.wait_for(request.app.state.db.ping(), timeout=3.0)
    except TimeoutError:
        db_ok = False
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        env=request.app.state.settings.ENV,
        database="ok" if db_ok else "unreachable",
    )
