"""Health endpoints (BE-1.7, BE-34.5). Deliberately unauthenticated (D-62 §1).

* `GET /health` (BE-1.7, unchanged) returns liveness, API version, environment name and DB
  reachability; no business or user data.
* `GET /health/live` does no I/O: the process is up and serving.
* `GET /health/ready` checks the DB (within 2 s), the migration head, the artifact store and
  the workers, and answers 200 or 503. The body carries only fixed check names and
  `ok`/`fail`: never a DSN, host, revision or version string.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from app import __version__
from app.core.readiness import CheckResult, check_readiness

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


class LivenessResponse(BaseModel):
    """`/health/live`: no I/O, so it is `ok` whenever the process can answer."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


class ReadinessChecks(BaseModel):
    """Value-free readiness checks: the names are fixed and each value is `ok` or `fail`."""

    model_config = ConfigDict(extra="forbid")

    database: CheckResult
    migrations: CheckResult
    artifact_store: CheckResult
    workers: CheckResult


class ReadinessResponse(BaseModel):
    """`/health/ready`: `ok` (200) only when every check is `ok`, otherwise `fail` (503)."""

    model_config = ConfigDict(extra="forbid")

    status: CheckResult
    checks: ReadinessChecks


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    operation_id="getHealthLive",
    summary="Liveness probe (no I/O)",
)
async def health_live() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    operation_id="getHealthReady",
    summary="Readiness probe: database, migration head, artifact store, workers",
    responses={
        503: {
            "model": ReadinessResponse,
            "description": "Not ready: at least one check is `fail` (value-free check names)",
        }
    },
)
async def health_ready(request: Request, response: Response) -> ReadinessResponse:
    checks = ReadinessChecks(**await check_readiness(request.app.state))
    ready = all(value == "ok" for value in checks.model_dump().values())
    if not ready:
        response.status_code = 503
    return ReadinessResponse(status="ok" if ready else "fail", checks=checks)
