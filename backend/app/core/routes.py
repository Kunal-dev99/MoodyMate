"""The routes an app serves, as served: include prefixes and include-level dependencies applied.

FastAPI >= 0.16x keeps each included router as a single lazy entry in `app.routes`, and it
sets `scope["route"]` to the router's *original* `APIRoute`, whose `path` lacks the include
prefixes ("/imports", not "/api/v1/imports") and whose `dependant` lacks include-level
dependencies. Anything that needs the route template a request was served under (the D-04
guard, denial audit actions, rate-limit buckets, the route registry) goes through here, so
that only this module depends on how FastAPI represents included routes.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from starlette.requests import Request
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp


@dataclass(frozen=True)
class ServedRoute:
    route: BaseRoute  # the route object as declared; it is what `scope["route"]` holds
    path: str  # the path template as served, include prefixes applied ("" for a Host)
    methods: frozenset[str]
    dependant: Dependant | None  # the dependency tree as served; APIRoutes only
    _context: RouteContext

    @property
    def is_api_route(self) -> bool:
        return isinstance(self.route, APIRoute)


def _served(context: RouteContext) -> ServedRoute:
    dependant = None
    if isinstance(context.original_route, APIRoute):
        # RouteContext forwards to the included (effective) route, which carries the
        # include-level dependencies as well as the route's own.
        effective = getattr(context, "dependant", None)
        dependant = effective if isinstance(effective, Dependant) else None
    return ServedRoute(
        route=context.original_route,
        path=context.path or "",
        methods=frozenset(context.methods or ()),
        dependant=dependant,
        _context=context,
    )


def served_routes(app: ASGIApp) -> Iterator[ServedRoute]:
    """Every route `app` serves, flattened through included routers."""
    yield from (_served(c) for c in iter_route_contexts(getattr(app, "routes", [])))


def served_route(request: Request) -> ServedRoute | None:
    """The served form of the route that matched `request`, if one did."""
    route = request.scope.get("route")
    if route is None:
        return None
    candidates = [s for s in served_routes(request.app) if s.route is route]
    if len(candidates) > 1:  # one router included under several prefixes
        candidates = [s for s in candidates if s._context.matches(request.scope)[0] == Match.FULL]
    return candidates[0] if candidates else None


def route_template(request: Request) -> str:
    """The path template `request` was served under ("/api/v1/imports/{import_id}"), or
    the raw request path when no route matched."""
    served = served_route(request)
    return served.path if served is not None and served.path else request.url.path
