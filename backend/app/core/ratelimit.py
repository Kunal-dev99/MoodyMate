"""Rate limits on expensive endpoints (BE-28.3, D-53).

An in-process token bucket per (tenant, principal, route template) on the five routes of
`LimitedRoute`. The check is a dependency wrapping the route's permission, so authentication
and the permission run first (401 and 403 unchanged) and the limit is checked before the
handler reads or writes anything. An Idempotency-Key replay is a request like any other.

A refusal is 429 `rate_limited`, `retryable: true` (D-07), with `Retry-After` in whole
seconds; it is audited DENIED under the route's action with reason `rate_limited`, nothing is
written and no request value is echoed.

Per process (D-53 §5): N API processes allow N times the configured rates. A shared store is
required before more than one API instance runs (Phase 33).
"""

import math
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import PrincipalDependency, _audit_session, require_permission
from app.core.config import Settings
from app.core.errors import ApiError, ErrorEnvelope
from app.core.interfaces import audit_denied_by
from app.core.principal import Principal
from app.core.routes import route_template
from app.db.session import get_session

RATE_LIMITED = "rate_limited"
RETRY_AFTER = "Retry-After"
# Buckets kept at most; the least recently used is dropped first (it refills to full anyway
# within burst / rate, so dropping it only forgets a partly spent bucket).
MAX_BUCKETS = 10_000


class LimitedRoute(StrEnum):
    """The rate-limited routes (D-53 §1); each names its RATE_LIMIT_<ROUTE>_* settings."""

    CREATE_IMPORT = "CREATE_IMPORT"  # POST /imports
    VALIDATE_IMPORT = "VALIDATE_IMPORT"  # POST /imports/{import_id}/validate
    SCENARIO_VALIDATION = "SCENARIO_VALIDATION"  # GET /scenarios/{id}/versions/{v}/validation
    CALCULATION_RUN = "CALCULATION_RUN"  # POST /calculation-runs
    SCENARIO_RUN = "SCENARIO_RUN"  # POST /scenarios/{scenario_id}/run


@dataclass(frozen=True)
class Bucket:
    burst: int  # capacity: requests allowed at once
    per_minute: int  # refill rate


def buckets_from(settings: Settings) -> dict[LimitedRoute, Bucket]:
    return {
        route: Bucket(
            burst=getattr(settings, f"RATE_LIMIT_{route}_BURST"),
            per_minute=getattr(settings, f"RATE_LIMIT_{route}_PER_MINUTE"),
        )
        for route in LimitedRoute
    }


BucketKey = tuple[uuid.UUID, uuid.UUID, str]  # (tenant, principal, route template)


class RateLimiter:
    """Token buckets, refilled continuously. One per app (app.state.rate_limiter). The clock
    is injectable; `take` never awaits, so it is atomic on the event loop."""

    def __init__(
        self,
        buckets: dict[LimitedRoute, Bucket],
        clock: Callable[[], float] = time.monotonic,
        max_buckets: int = MAX_BUCKETS,
    ) -> None:
        self.buckets = buckets
        self.clock = clock
        self.max_buckets = max_buckets
        self._state: OrderedDict[BucketKey, tuple[float, float]] = OrderedDict()

    def take(self, route: LimitedRoute, key: BucketKey) -> int | None:
        """Spend one token: None when served, else the whole seconds until one is back."""
        bucket = self.buckets[route]
        rate = bucket.per_minute / 60  # tokens per second
        now = self.clock()
        tokens, then = self._state.pop(key, (float(bucket.burst), now))
        tokens = min(float(bucket.burst), tokens + (now - then) * rate)
        refused = tokens < 1
        self._state[key] = (tokens if refused else tokens - 1, now)
        while len(self._state) > self.max_buckets:
            self._state.popitem(last=False)
        return max(1, math.ceil((1 - tokens) / rate)) if refused else None


def rate_limited(
    route: LimitedRoute, permission: str, action: str | None = None
) -> PrincipalDependency:
    """The route's permission (D-04), then its bucket. `action` is the route's audit action;
    None audits the endpoint form ("GET /api/v1/..."), as require_permission does."""
    check = require_permission(permission)

    async def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(check)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> Principal:
        limiter: RateLimiter | None = request.app.state.rate_limiter
        if limiter is None:  # RATE_LIMIT_ENABLED=false (local/test only)
            return principal
        template = route_template(request)
        retry_after = limiter.take(route, (principal.tenant_id, principal.user_id, template))
        if retry_after is None:
            return principal
        async with _audit_session(request, session) as audit_session:
            audit_denied_by(
                audit_session,
                principal,
                action or f"{request.method} {template}",
                "endpoint",
                template,
                {"reason": RATE_LIMITED, "retry_after_seconds": retry_after},
            )
        raise ApiError(
            429,
            RATE_LIMITED,
            "Too many requests to this endpoint. Retry after the time in Retry-After.",
            {"retry_after_seconds": retry_after},
            retryable=True,
            headers={RETRY_AFTER: str(retry_after)},
        )

    dependency.__name__ = f"rate_limited_{route.lower()}"
    return dependency


RATE_LIMITED_RESPONSE: dict[int | str, dict[str, Any]] = {
    429: {
        "model": ErrorEnvelope,
        "description": "rate_limited: this principal's bucket for the route is empty (D-53); "
        "retryable, audited DENIED, nothing written.",
        "headers": {
            RETRY_AFTER: {
                "description": "Whole seconds until a request can be served again.",
                "schema": {"type": "integer", "minimum": 1},
            }
        },
    }
}
