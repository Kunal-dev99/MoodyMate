"""Authentication and fail-closed authorization (BE-2.3, BE-2.4, D-04).

* `get_principal` turns a bearer token into a server-side `Principal`; no token or a bad
  token is 401.
* `require_permission(code)` / `authenticated()` declare what a route needs; a missing
  permission is 403.
* Every `/api/v1` route except `PUBLIC_PATHS` must declare one of them. This is enforced
  twice: at runtime by `enforce_declared_permission` (undeclared → 403) and in CI by
  `undeclared_routes` (test_route_registry).
"""

from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.errors import ApiError
from app.core.principal import Principal
from app.core.tokens import InvalidToken, ProviderUnavailable, TokenExpired
from app.db.session import get_session
from app.governance.service import IdentityService

# Deliberately unauthenticated endpoints. Adding to this set is a security decision.
PUBLIC_PATHS = frozenset(
    {
        "/api/v1/health",
        "/api/v1/auth/config",
        "/api/v1/dev/personas",
        "/api/v1/dev/sessions",
    }
)

AUTHENTICATED = "authenticated"  # declared requirement: any signed-in principal
PERMISSION_ATTR = "__permission__"

_bearer = HTTPBearer(auto_error=False, description="Access token from the identity provider")


def _unauthorized(code: str, message: str, error: str | None = None) -> ApiError:
    challenge = "Bearer" if error is None else f'Bearer error="{error}"'
    return ApiError(401, code, message, headers={"WWW-Authenticate": challenge})


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Principal:
    cached: Principal | None = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    if credentials is None:
        raise _unauthorized("not_authenticated", "Sign in to use this endpoint.")
    verifier = request.app.state.token_verifier
    try:
        identity = await run_in_threadpool(verifier.verify, credentials.credentials)
    except TokenExpired as exc:
        raise _unauthorized("token_expired", "Your session has expired.", "invalid_token") from exc
    except ProviderUnavailable as exc:
        raise ApiError(
            503,
            "auth_provider_unavailable",
            "Sign-in is temporarily unavailable. Try again shortly.",
            retryable=True,
        ) from exc
    except InvalidToken as exc:
        raise _unauthorized(
            "invalid_token", "The access token is not valid.", "invalid_token"
        ) from exc
    principal = await IdentityService(session).resolve_principal(
        identity, can_self_provision=request.app.state.settings.can_self_provision
    )
    request.state.principal = principal
    return principal


PrincipalDependency = Callable[..., Awaitable[Principal]]


def require_permission(permission: str) -> PrincipalDependency:
    async def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if permission != AUTHENTICATED and permission not in principal.permissions:
            raise ApiError(
                403,
                "forbidden",
                "You do not have permission for this action.",
                {"required_permission": permission},
            )
        return principal

    setattr(dependency, PERMISSION_ATTR, permission)
    dependency.__name__ = f"require_{permission.replace(':', '_')}"
    return dependency


def authenticated() -> PrincipalDependency:
    return require_permission(AUTHENTICATED)


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    yield dependant
    for sub in dependant.dependencies:
        yield from _walk(sub)


def declared_permission(route: APIRoute) -> str | None:
    for dep in _walk(route.dependant):
        permission = getattr(dep.call, PERMISSION_ATTR, None)
        if permission:
            return str(permission)
    return None


def undeclared_routes(app: FastAPI) -> list[str]:
    """`/api/v1` routes that are neither public nor declare a permission.

    Mounts and websocket routes under `/api/v1` are always reported: the runtime guard
    cannot see inside a mount, and websockets need their own authorization design.
    """
    found: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1") or path in PUBLIC_PATHS:
            continue
        if isinstance(route, APIRoute):
            if declared_permission(route) is None:
                found.append(f"{','.join(sorted(route.methods))} {path}")
        else:
            found.append(f"{type(route).__name__} {path}")
    return sorted(found)


async def enforce_declared_permission(request: Request) -> None:
    """App-level guard: an /api/v1 route that forgot to declare a permission is denied,
    even if it was attached to the app directly rather than through the API router."""
    route = request.scope.get("route")
    if (
        not isinstance(route, APIRoute)
        or not route.path.startswith("/api/v1")
        or route.path in PUBLIC_PATHS
    ):
        return
    if declared_permission(route) is None:
        raise ApiError(403, "permission_not_declared", "This endpoint is not available.")
