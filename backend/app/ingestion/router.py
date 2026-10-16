"""ingestion.router

Source registration endpoints (BE-7.4). Every route declares its permission (D-04, the
Feature 02 endpoint table); tenant scope comes from the principal. Writes are refused with
409 `ingestion_disabled` (audited) while INGESTION_ENABLED is off (BE-7.3).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_principal, require_permission
from app.core.errors import ApiError
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.governance.service import record_denial
from app.ingestion.models import SourceContract, SourceSystem
from app.ingestion.schemas import (
    ContractColumn,
    ContractIn,
    ContractOut,
    ProfileOut,
    SourceCreate,
    SourceDetail,
    SourcePage,
    SourceSummary,
)
from app.ingestion.service import (
    ContractSpec,
    ProfileSpec,
    SourceService,
    current_contract,
    profile_of,
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SourceReader = Annotated[Principal, Depends(require_permission(Permission.SOURCE_READ))]
SourceManager = Annotated[Principal, Depends(require_permission(Permission.SOURCE_MANAGE))]

router = APIRouter(tags=["ingestion"])


async def ingestion_enabled(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    session: SessionDep,
) -> None:
    """WS2 is read-only while INGESTION_ENABLED is false. The refusal is audited like any
    other denied request (BE-3.2). Declared after the permission, so a caller without it
    still gets 403."""
    if not request.app.state.settings.INGESTION_ENABLED:
        route = request.scope.get("route")
        endpoint = getattr(route, "path", request.url.path)
        await record_denial(
            session,
            principal,
            f"{request.method} {endpoint}",
            "endpoint",
            endpoint,
            "ingestion_disabled",
        )
        raise ApiError(
            409, "ingestion_disabled", "Ingestion changes are switched off in this environment."
        )


IngestionEnabled = Annotated[None, Depends(ingestion_enabled)]


def _contract_spec(body: ContractIn) -> ContractSpec:
    return ContractSpec(
        schema_id=body.schema_id,
        expected_columns=[c.model_dump() for c in body.expected_columns],
        business_key_columns=list(body.business_key_columns),
        control_total_columns=list(body.control_total_columns),
        change_summary=body.change_summary,
    )


def _contract_out(c: SourceContract, codes: dict[uuid.UUID, str]) -> ContractOut:
    return ContractOut(
        id=c.id,
        version=c.version,
        status=c.status,
        schema_id=c.schema_id,
        schema_code=codes.get(c.schema_id, ""),
        expected_columns=[ContractColumn.model_validate(col) for col in c.expected_columns],
        business_key_columns=c.business_key_columns,
        control_total_columns=c.control_total_columns,
        content_sha256=c.content_sha256,
        supersedes_id=c.supersedes_id,
        change_summary=c.change_summary,
        created_by=c.created_by,
        created_at=c.created_at,
    )


def _summary_fields(s: SourceSystem, codes: dict[uuid.UUID, str]) -> SourceSummary:
    p = profile_of(s)
    current = current_contract(p.contracts)
    return SourceSummary(
        id=s.id,
        code=s.code,
        name=s.name,
        description=s.description,
        kind=s.kind,
        provisional=p.provisional,
        profile=ProfileOut.model_validate(p, from_attributes=True),
        current_contract=_contract_out(current, codes) if current else None,
        contract_count=len(p.contracts),
        created_by=s.created_by,
        created_at=s.created_at,
    )


def _detail(s: SourceSystem, codes: dict[uuid.UUID, str]) -> SourceDetail:
    return SourceDetail(
        **_summary_fields(s, codes).model_dump(),
        contracts=[_contract_out(c, codes) for c in profile_of(s).contracts],
    )


@router.get("/sources", response_model=SourcePage, operation_id="listSources")
async def list_sources(
    principal: SourceReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SourcePage:
    service = SourceService(session, principal)
    sources, total = await service.page(limit, offset)
    codes = await service.schema_codes()
    return SourcePage(
        items=[_summary_fields(s, codes) for s in sources], total=total, limit=limit, offset=offset
    )


@router.post("/sources", response_model=SourceDetail, status_code=201, operation_id="createSource")
async def create_source(
    body: SourceCreate, principal: SourceManager, session: SessionDep, _: IngestionEnabled
) -> SourceDetail:
    service = SourceService(session, principal)
    p = body.profile
    source = await service.create(
        body.code,
        body.name,
        body.description,
        body.kind,
        ProfileSpec(
            format=p.format,
            encoding=p.encoding,
            delimiter=p.delimiter,
            sheet_name=p.sheet_name,
            cadence=p.cadence,
            owner=p.owner,
            default_classification=p.default_classification,
        ),
        _contract_spec(body.contract),
    )
    return _detail(source, await service.schema_codes())


@router.get("/sources/{source_id}", response_model=SourceDetail, operation_id="getSource")
async def get_source(
    source_id: uuid.UUID, principal: SourceReader, session: SessionDep
) -> SourceDetail:
    service = SourceService(session, principal)
    return _detail(await service.get(source_id), await service.schema_codes())


@router.post(
    "/sources/{source_id}/contracts",
    response_model=ContractOut,
    status_code=201,
    operation_id="createSourceContract",
)
async def create_source_contract(
    source_id: uuid.UUID,
    body: ContractIn,
    principal: SourceManager,
    session: SessionDep,
    _: IngestionEnabled,
) -> ContractOut:
    service = SourceService(session, principal)
    contract = await service.add_contract(source_id, _contract_spec(body))
    return _contract_out(contract, await service.schema_codes())
