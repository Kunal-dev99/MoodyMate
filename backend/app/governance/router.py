"""governance.router

Identity endpoints (BE-2.3, BE-2.6) and read-only user views (BE-2.5). Every route
declares its permission (D-04); public ones are listed in app.core.auth.PUBLIC_PATHS.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import authenticated, require_permission
from app.core.classification import mask_model
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.governance.models import UserAccount
from app.governance.schemas import (
    AuditEventOut,
    AuditEventPage,
    AuthConfig,
    DevPersona,
    DevSessionRequest,
    DevSessionResponse,
    Me,
    MyPermissions,
    OidcClientConfig,
    UserPage,
    UserSummary,
)
from app.governance.service import AuditFilters, AuditQueryService, IdentityService, UserService

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# --- public ---------------------------------------------------------------------------
auth_router = APIRouter(tags=["auth"])


@auth_router.get("/auth/config", response_model=AuthConfig, operation_id="getAuthConfig")
async def auth_config(request: Request) -> AuthConfig:
    s = request.app.state.settings
    oidc = None
    if s.AUTH_MODE == "oidc":
        oidc = OidcClientConfig(
            issuer=s.OIDC_ISSUER, client_id=s.OIDC_CLIENT_ID, scopes=s.OIDC_SCOPES
        )
    return AuthConfig(mode=s.AUTH_MODE, dev_sessions=s.dev_sessions_enabled, oidc=oidc)


# --- dev only (mounted when settings.dev_sessions_enabled) -----------------------------
dev_router = APIRouter(prefix="/dev", tags=["dev-only"])


@dev_router.get("/personas", response_model=list[DevPersona], operation_id="listDevPersonas")
async def dev_personas(session: SessionDep) -> list[DevPersona]:
    roles = await IdentityService(session).dev_personas()
    return [
        DevPersona(
            role=r.code,
            name=r.name,
            description=r.description,
            permissions=[p.code for p in r.permissions],
        )
        for r in roles
    ]


@dev_router.post(
    "/sessions", response_model=DevSessionResponse, status_code=201, operation_id="createDevSession"
)
async def dev_session(
    body: DevSessionRequest, request: Request, session: SessionDep
) -> DevSessionResponse:
    result = await IdentityService(session).start_dev_session(
        body.role, request.app.state.token_verifier
    )
    return DevSessionResponse(access_token=result.access_token, expires_in=result.expires_in)


# --- authenticated ---------------------------------------------------------------------
router = APIRouter(tags=["identity"])


@router.get("/me", response_model=Me, operation_id="getMe")
async def me(principal: Annotated[Principal, Depends(authenticated())]) -> Me:
    return Me(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        display_name=principal.display_name,
        email=principal.email,
        roles=sorted(principal.roles),
    )


@router.get("/me/permissions", response_model=MyPermissions, operation_id="getMyPermissions")
async def my_permissions(
    principal: Annotated[Principal, Depends(authenticated())],
) -> MyPermissions:
    return MyPermissions(permissions=sorted(principal.permissions))


def _summary(u: UserAccount, principal: Principal) -> UserSummary:
    summary = UserSummary(
        id=u.id,
        display_name=u.display_name,
        email=u.email,
        status=u.status,
        roles=[r.code for r in u.roles],
        created_at=u.created_at,
    )
    return mask_model(summary, principal.clear_classifications)


@router.get("/users", response_model=UserPage, operation_id="listUsers")
async def list_users(
    principal: Annotated[Principal, Depends(require_permission(Permission.ADMIN_READ))],
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPage:
    users, total = await UserService(session, principal).list_users(limit, offset)
    items = [_summary(u, principal) for u in users]
    return UserPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=UserSummary, operation_id="getUser")
async def get_user(
    user_id: uuid.UUID,
    principal: Annotated[Principal, Depends(require_permission(Permission.ADMIN_READ))],
    session: SessionDep,
) -> UserSummary:
    return _summary(await UserService(session, principal).get_user(user_id), principal)


# Filter values are stored in the audit.read event, so they are bounded.
FilterText = Annotated[str | None, Query(max_length=200)]


@router.get("/audit-events", response_model=AuditEventPage, operation_id="listAuditEvents")
async def list_audit_events(
    principal: Annotated[Principal, Depends(require_permission(Permission.AUDIT_READ))],
    session: SessionDep,
    resource_type: FilterText = None,
    resource_id: FilterText = None,
    actor: FilterText = None,
    action: FilterText = None,
    outcome: FilterText = None,
    occurred_from: Annotated[datetime | None, Query(alias="from")] = None,
    occurred_to: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    before: Annotated[
        uuid.UUID | None,
        Query(description="Return events older than this event id (stable keyset paging)."),
    ] = None,
) -> AuditEventPage:
    filters = AuditFilters(
        resource_type=resource_type,
        resource_id=resource_id,
        actor=actor,
        action=action,
        outcome=outcome,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )
    rows, total = await AuditQueryService(session, principal).page(filters, limit, offset, before)
    return AuditEventPage(
        items=[
            AuditEventOut(
                id=e.id,
                occurred_at=e.occurred_at,
                actor=e.actor,
                actor_user_id=e.actor_user_id,
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                outcome=e.outcome,
                correlation_id=e.correlation_id,
                approval_ref=e.approval_ref,
                details=details,
            )
            for e, details in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
