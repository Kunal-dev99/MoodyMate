"""core.refusals

`refuse(...)` (BE-29.3): the one refusal path of a write. It rolls back the request's own
work, audits the attempt DENIED with the error code as its reason (in its own session,
through `app.governance.service.record_denial`, BE-3.2), and raises the error, so the client
gets the envelope and nothing else is committed with the audit row.

Modules keep a thin `_refuse` that names their action and resource type; every one of them
ends here. `refuse_endpoint` is the same for a route-level switch: the action is
"<METHOD> <route path>" and the resource is the endpoint.
"""

import uuid
from typing import NoReturn

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.routes import route_template


async def refuse(
    session: AsyncSession,
    principal: Principal,
    error: ApiError,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str | None = None,
) -> NoReturn:
    """Roll back, audit the refusal (DENIED, reason = the error code) and raise `error`."""
    # Imported here: governance itself refuses through this function (no import cycle).
    from app.governance.service import record_denial

    await record_denial(
        session,
        principal,
        action,
        resource_type,
        str(resource_id) if resource_id else None,
        error.code,
    )
    raise error


async def refuse_endpoint(
    request: Request, session: AsyncSession, principal: Principal, error: ApiError
) -> NoReturn:
    """A route-level refusal (a write switch is off), audited like a denied request."""
    endpoint = route_template(request)  # the served template (FastAPI 0.162 lazy routers, D-04)
    await refuse(
        session,
        principal,
        error,
        action=f"{request.method} {endpoint}",
        resource_type="endpoint",
        resource_id=endpoint,
    )
