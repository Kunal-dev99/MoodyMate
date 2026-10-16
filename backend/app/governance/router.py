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
from app.core.errors import ApiError
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission, ProposalStatus
from app.governance.models import (
    SchemaChangeProposal,
    SchemaDefinition,
    SchemaVersion,
    UserAccount,
)
from app.governance.schemas import (
    AuditEventOut,
    AuditEventPage,
    AuthConfig,
    DevPersona,
    DevSessionRequest,
    DevSessionResponse,
    ImpactAnalysis,
    Me,
    MyPermissions,
    OidcClientConfig,
    ProposalApproval,
    ProposalCreate,
    ProposalList,
    ProposalOut,
    ProposalSubmit,
    SchemaDependent,
    SchemaDiffOut,
    SchemaFieldChange,
    SchemaFieldOut,
    SchemaList,
    SchemaSummary,
    SchemaVersionList,
    SchemaVersionOut,
    SchemaVersionRef,
    UserPage,
    UserSummary,
)
from app.governance.service import (
    AuditFilters,
    AuditQueryService,
    FieldSpec,
    IdentityService,
    ProposedField,
    SchemaChangeService,
    SchemaService,
    UserService,
    effective_version,
)

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


# --- Schema registry (BE-4.3) ------------------------------------------------------------

SchemaReader = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_READ))]


def _version_ref(v: SchemaVersion) -> SchemaVersionRef:
    return SchemaVersionRef(
        id=v.id, version=v.version, status=v.status, effective_from=v.effective_from
    )


def _schema_summary(s: SchemaDefinition) -> SchemaSummary:
    effective = effective_version(s)
    latest = max(s.versions, key=lambda v: v.version)
    return SchemaSummary(
        id=s.id,
        code=s.code,
        name=s.name,
        description=s.description,
        owner=s.owner,
        provisional=s.provisional,
        status=(effective or latest).status,
        effective_version=_version_ref(effective) if effective else None,
        latest_version=_version_ref(latest),
        version_count=len(s.versions),
        dependents=[
            SchemaDependent(
                dependent_type=d.dependent_type,
                dependent_ref=d.dependent_ref,
                dependent_schema_id=d.dependent_schema_id,
                description=d.description,
                provisional=d.provisional,
            )
            for d in s.dependents
        ],
    )


@router.get("/schemas", response_model=SchemaList, operation_id="listSchemas")
async def list_schemas(principal: SchemaReader, session: SessionDep) -> SchemaList:
    # A definition has at least one version once registered; skip any mid-creation.
    schemas = [s for s in await SchemaService(session, principal).list_schemas() if s.versions]
    return SchemaList(items=[_schema_summary(s) for s in schemas], total=len(schemas))


@router.get(
    "/schemas/{schema_id}/versions",
    response_model=SchemaVersionList,
    operation_id="listSchemaVersions",
)
async def list_schema_versions(
    schema_id: uuid.UUID, principal: SchemaReader, session: SessionDep
) -> SchemaVersionList:
    schema = await SchemaService(session, principal).get_schema(schema_id)
    if not schema.versions:
        raise ApiError(404, "not_found", "Schema not found.")
    return SchemaVersionList(
        schema_=_schema_summary(schema),
        items=[
            SchemaVersionOut(
                **_version_ref(v).model_dump(),
                compatibility=v.compatibility,
                content_sha256=v.content_sha256,
                change_summary=v.change_summary,
                created_at=v.created_at,
                fields=[SchemaFieldOut.model_validate(f, from_attributes=True) for f in v.fields],
            )
            for v in schema.versions
        ],
    )


@router.get(
    "/schemas/{schema_id}/diff", response_model=SchemaDiffOut, operation_id="diffSchemaVersions"
)
async def diff_schema_versions(
    schema_id: uuid.UUID,
    principal: SchemaReader,
    session: SessionDep,
    from_version: Annotated[int, Query(alias="from", ge=1, description="Base version number.")],
    to_version: Annotated[int, Query(alias="to", ge=1, description="Compared version number.")],
) -> SchemaDiffOut:
    schema, old, new, result = await SchemaService(session, principal).diff(
        schema_id, from_version, to_version
    )
    return SchemaDiffOut(
        schema_id=schema.id,
        from_version=_version_ref(old),
        to_version=_version_ref(new),
        overall=result.overall,
        changes=[SchemaFieldChange(**vars(c)) for c in result.changes],
    )


# --- Controlled schema change (BE-5.2 … BE-5.4) -------------------------------------------

ProposalReader = SchemaReader
Proposer = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_PROPOSE))]
Approver = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_APPROVE))]


def schema_change_enabled(request: Request) -> None:
    """Writes are off when SCHEMA_CHANGE_ENABLED is false: the registry is read-only."""
    if not request.app.state.settings.SCHEMA_CHANGE_ENABLED:
        raise ApiError(
            403, "schema_change_disabled", "Schema changes are switched off in this environment."
        )


ChangeEnabled = Annotated[None, Depends(schema_change_enabled)]


async def _proposal_out(service: SchemaChangeService, p: SchemaChangeProposal) -> ProposalOut:
    return ProposalOut(
        id=p.id,
        schema_id=p.schema_id,
        schema_code=p.schema.code,
        schema_name=p.schema.name,
        status=p.status,
        title=p.title,
        rationale=p.rationale,
        proposer=p.created_by,
        proposer_user_id=p.proposer_user_id,
        created_at=p.created_at,
        base_version=_version_ref(p.base_version),
        proposed_version=_version_ref(p.proposed_version),
        proposed_fields=[
            SchemaFieldOut.model_validate(f, from_attributes=True) for f in await service.fields(p)
        ],
        impact=ImpactAnalysis.model_validate(p.impact) if p.impact else None,
        regression_evidence_ref=p.regression_evidence_ref,
        submitted_at=p.submitted_at,
        decided_by=p.decided_by,
        decided_at=p.decided_at,
        decision_comment=p.decision_comment,
        reverts_proposal_id=p.reverts_proposal_id,
    )


@router.get("/schema-change-proposals", response_model=ProposalList, operation_id="listProposals")
async def list_proposals(
    principal: ProposalReader,
    session: SessionDep,
    status: ProposalStatus | None = None,
    schema_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProposalList:
    service = SchemaChangeService(session, principal)
    items, total = await service.page(status, schema_id, limit, offset)
    return ProposalList(items=[await _proposal_out(service, p) for p in items], total=total)


@router.post(
    "/schema-change-proposals",
    response_model=ProposalOut,
    status_code=201,
    operation_id="createProposal",
)
async def create_proposal(
    body: ProposalCreate, principal: Proposer, session: SessionDep, _: ChangeEnabled
) -> ProposalOut:
    service = SchemaChangeService(session, principal)
    proposal = await service.create(
        body.schema_id,
        body.title,
        body.rationale,
        [
            ProposedField(FieldSpec(**f.model_dump(exclude={"description"})), f.description)
            for f in body.fields
        ],
    )
    return await _proposal_out(service, proposal)


@router.get(
    "/schema-change-proposals/{proposal_id}", response_model=ProposalOut, operation_id="getProposal"
)
async def get_proposal(
    proposal_id: uuid.UUID, principal: ProposalReader, session: SessionDep
) -> ProposalOut:
    service = SchemaChangeService(session, principal)
    return await _proposal_out(service, await service.get(proposal_id))


@router.post(
    "/schema-change-proposals/{proposal_id}/impact-analysis",
    response_model=ProposalOut,
    operation_id="analyseProposal",
)
async def analyse_proposal(
    proposal_id: uuid.UUID, principal: Proposer, session: SessionDep, _: ChangeEnabled
) -> ProposalOut:
    service = SchemaChangeService(session, principal)
    return await _proposal_out(service, await service.analyse(proposal_id))


@router.post(
    "/schema-change-proposals/{proposal_id}/submit",
    response_model=ProposalOut,
    operation_id="submitProposal",
)
async def submit_proposal(
    proposal_id: uuid.UUID,
    body: ProposalSubmit,
    principal: Proposer,
    session: SessionDep,
    _: ChangeEnabled,
) -> ProposalOut:
    service = SchemaChangeService(session, principal)
    return await _proposal_out(
        service, await service.submit(proposal_id, body.regression_evidence_ref)
    )


@router.post(
    "/schema-change-proposals/{proposal_id}/approval",
    response_model=ProposalOut,
    operation_id="decideProposal",
)
async def decide_proposal(
    proposal_id: uuid.UUID,
    body: ProposalApproval,
    principal: Approver,
    session: SessionDep,
    _: ChangeEnabled,
) -> ProposalOut:
    service = SchemaChangeService(session, principal)
    return await _proposal_out(
        service, await service.decide(proposal_id, body.decision, body.comment)
    )


@router.post(
    "/schema-change-proposals/{proposal_id}/revert",
    response_model=ProposalOut,
    status_code=201,
    operation_id="revertProposal",
)
async def revert_proposal(
    proposal_id: uuid.UUID, principal: Proposer, session: SessionDep, _: ChangeEnabled
) -> ProposalOut:
    service = SchemaChangeService(session, principal)
    return await _proposal_out(service, await service.revert(proposal_id))
