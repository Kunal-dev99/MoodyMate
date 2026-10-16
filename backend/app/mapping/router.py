"""mapping.router

The mapping workbench (BE-10.3 – BE-10.5). Every route declares its permission (D-04, the
Feature 02 endpoint table); tenant scope comes from the principal.

Writes are refused with 409 (audited DENIED) while INGESTION_ENABLED (`ingestion_disabled`,
all WS2 writes) or MAPPING_CHANGE_ENABLED (`mapping_change_disabled`, BE-10.6) is off.
Reads, and imports using ACTIVE versions, keep working.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_principal, require_permission
from app.core.errors import ApiError
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.governance.service import record_denial
from app.mapping.constants import Refusal
from app.mapping.models import FieldMapping, MappingValidation, MappingVersion
from app.mapping.schemas import (
    ApprovalIn,
    MappingCreate,
    MappingDetail,
    MappingPage,
    MappingSummary,
    MappingValidationOut,
    MappingVersionIn,
    MappingVersionOut,
    RuleIn,
    RuleOut,
    RuleResult,
    SubmitIn,
    TransformOut,
    ValidateIn,
    ValidationIssue,
    ValidationRef,
)
from app.mapping.service import MappingLabels, MappingService, MappingSpec, RuleSpec

SessionDep = Annotated[AsyncSession, Depends(get_session)]
MappingReader = Annotated[Principal, Depends(require_permission(Permission.MAPPING_READ))]
MappingProposer = Annotated[Principal, Depends(require_permission(Permission.MAPPING_PROPOSE))]
MappingApprover = Annotated[Principal, Depends(require_permission(Permission.MAPPING_APPROVE))]
VersionNumber = Annotated[int, Path(ge=1, le=1_000_000)]

router = APIRouter(tags=["mapping"])


def _switch(setting: str, code: str, message: str) -> Callable[..., Awaitable[None]]:
    """A write switch: off refuses with 409 `code`, audited like any denied request
    (BE-3.2). Declared after the permission, so a caller without it still gets 403."""

    async def check(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        session: SessionDep,
    ) -> None:
        if not getattr(request.app.state.settings, setting):
            route = request.scope.get("route")
            endpoint = getattr(route, "path", request.url.path)
            await record_denial(
                session, principal, f"{request.method} {endpoint}", "endpoint", endpoint, code
            )
            raise ApiError(409, code, message)

    return check


IngestionEnabled = Annotated[
    None,
    Depends(
        _switch(
            "INGESTION_ENABLED",
            "ingestion_disabled",
            "Ingestion changes are switched off in this environment.",
        )
    ),
]
MappingChangeEnabled = Annotated[
    None,
    Depends(
        _switch(
            "MAPPING_CHANGE_ENABLED",
            Refusal.MAPPING_CHANGE_DISABLED,
            "Mapping changes are switched off in this environment; ACTIVE versions still " "apply.",
        )
    ),
]


def _rules(rules: list[RuleIn]) -> list[RuleSpec]:
    return [
        RuleSpec(
            source_column=r.source_column,
            target_field=r.target_field,
            column_policy=r.column_policy,
            transform_code=r.transform.code,
            transform_params=r.transform.params.model_dump(mode="json"),
            null_policy=r.null_policy,
            default_value=r.default_value,
        )
        for r in rules
    ]


def _validation_ref(v: MappingValidation | None) -> ValidationRef | None:
    if v is None:
        return None
    return ValidationRef(
        id=v.id,
        import_id=v.import_job_id,
        outcome=v.outcome,
        ok=v.outcome != "FAILED",
        mapping_sha256=v.mapping_sha256,
        created_at=v.created_at,
    )


def _version_out(
    mapping_id: uuid.UUID, v: MappingVersion, labels: MappingLabels
) -> MappingVersionOut:
    return MappingVersionOut(
        id=v.id,
        mapping_id=mapping_id,
        version=v.version,
        status=v.status,
        schema_version_id=v.schema_version_id,
        schema_version=labels.schema_versions.get(v.schema_version_id),
        content_sha256=v.content_sha256,
        author=v.created_by,
        author_user_id=v.author_user_id,
        change_summary=v.change_summary,
        submitted_at=v.submitted_at,
        decided_by=v.decided_by,
        decided_at=v.decided_at,
        decision_comment=v.decision_comment,
        last_validation=_validation_ref(v.last_validation),
        rules=[
            RuleOut(
                position=r.position,
                source_column=r.source_column,
                target_field=r.target_field,
                column_policy=r.column_policy,
                transform=TransformOut(code=r.transform_code, params=r.transform_params),
                null_policy=r.null_policy,
                default_value=r.default_value,
            )
            for r in v.rules
        ],
        created_at=v.created_at,
    )


def _summary(m: FieldMapping, labels: MappingLabels) -> MappingSummary:
    contract = labels.contracts.get(m.source_contract_id)
    active = next((v.version for v in m.versions if v.status == "ACTIVE"), None)
    return MappingSummary(
        id=m.id,
        code=m.code,
        name=m.name,
        description=m.description,
        source_id=m.source_system_id,
        source_code=contract[0] if contract else "",
        source_contract_id=m.source_contract_id,
        contract_version=contract[1] if contract else None,
        schema_id=m.schema_id,
        schema_code=labels.schema_codes.get(m.schema_id, ""),
        active_version=active,
        latest_version=max((v.version for v in m.versions), default=0),
        version_count=len(m.versions),
        created_by=m.created_by,
        created_at=m.created_at,
    )


def _detail(m: FieldMapping, labels: MappingLabels) -> MappingDetail:
    return MappingDetail(
        **_summary(m, labels).model_dump(),
        versions=[_version_out(m.id, v, labels) for v in m.versions],
    )


@router.get("/mappings", response_model=MappingPage, operation_id="listMappings")
async def list_mappings(
    principal: MappingReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    source_id: uuid.UUID | None = None,
    schema_id: uuid.UUID | None = None,
) -> MappingPage:
    """Mappings by code; filter by source and target schema."""
    service = MappingService(session, principal)
    mappings, total = await service.page(limit, offset, source_id, schema_id)
    labels = await service.labels(mappings)
    return MappingPage(
        items=[_summary(m, labels) for m in mappings], total=total, limit=limit, offset=offset
    )


@router.post(
    "/mappings", response_model=MappingDetail, status_code=201, operation_id="createMapping"
)
async def create_mapping(
    body: MappingCreate,
    principal: MappingProposer,
    session: SessionDep,
    _i: IngestionEnabled,
    _m: MappingChangeEnabled,
) -> MappingDetail:
    """A mapping for a source contract with DRAFT version 1, pinned to the target schema's
    ACTIVE version (D-18)."""
    service = MappingService(session, principal)
    mapping = await service.create(
        MappingSpec(
            code=body.code,
            name=body.name,
            description=body.description,
            source_contract_id=body.source_contract_id,
            schema_id=body.schema_id,
            rules=_rules(body.rules),
            change_summary=body.change_summary,
        )
    )
    return _detail(mapping, await service.labels([mapping]))


@router.get("/mappings/{mapping_id}", response_model=MappingDetail, operation_id="getMapping")
async def get_mapping(
    mapping_id: uuid.UUID, principal: MappingReader, session: SessionDep
) -> MappingDetail:
    """The mapping with every version and its rules."""
    service = MappingService(session, principal)
    mapping = await service.get(mapping_id)
    return _detail(mapping, await service.labels([mapping]))


@router.post(
    "/mappings/{mapping_id}/versions",
    response_model=MappingVersionOut,
    status_code=201,
    operation_id="createMappingVersion",
)
async def create_mapping_version(
    mapping_id: uuid.UUID,
    body: MappingVersionIn,
    principal: MappingProposer,
    session: SessionDep,
    _i: IngestionEnabled,
    _m: MappingChangeEnabled,
) -> MappingVersionOut:
    """The next DRAFT version, pinned to the target schema's ACTIVE version now. Earlier
    versions are never edited."""
    service = MappingService(session, principal)
    version = await service.add_version(mapping_id, _rules(body.rules), body.change_summary)
    mapping = await service.get(mapping_id)
    return _version_out(mapping_id, version, await service.labels([mapping]))


@router.post(
    "/mappings/{mapping_id}/versions/{version}/submit",
    response_model=MappingVersionOut,
    operation_id="submitMappingVersion",
)
async def submit_mapping_version(
    mapping_id: uuid.UUID,
    version: VersionNumber,
    body: SubmitIn,
    principal: MappingProposer,
    session: SessionDep,
    _i: IngestionEnabled,
    _m: MappingChangeEnabled,
) -> MappingVersionOut:
    """DRAFT -> IN_REVIEW. The author only; needs an ok dry run of this version (409
    mapping_validation_required otherwise)."""
    service = MappingService(session, principal)
    found = await service.submit(mapping_id, version, body.comment)
    mapping = await service.get(mapping_id)
    return _version_out(mapping_id, found, await service.labels([mapping]))


@router.post(
    "/mappings/{mapping_id}/versions/{version}/approval",
    response_model=MappingVersionOut,
    operation_id="decideMappingVersion",
)
async def decide_mapping_version(
    mapping_id: uuid.UUID,
    version: VersionNumber,
    body: ApprovalIn,
    principal: MappingApprover,
    session: SessionDep,
    _i: IngestionEnabled,
    _m: MappingChangeEnabled,
) -> MappingVersionOut:
    """APPROVE activates the version and deprecates the previous ACTIVE one; REJECT ends
    it (a comment is required). The author cannot decide (403 self_approval)."""
    service = MappingService(session, principal)
    found = await service.decide(mapping_id, version, body.decision, body.comment)
    mapping = await service.get(mapping_id)
    return _version_out(mapping_id, found, await service.labels([mapping]))


@router.post(
    "/imports/{import_id}/mappings/validate",
    response_model=MappingValidationOut,
    status_code=201,
    operation_id="validateImportMapping",
)
async def validate_import_mapping(
    import_id: uuid.UUID,
    body: ValidateIn,
    principal: MappingProposer,
    session: SessionDep,
    _i: IngestionEnabled,
    _m: MappingChangeEnabled,
) -> MappingValidationOut:
    """Dry run: apply a mapping version to every row of this import and report counts per
    rule and issues (never values). Stores the result; writes no canonical rows."""
    service = MappingService(session, principal)
    mapping, version, v = await service.validate(import_id, body.mapping_version_id)
    return MappingValidationOut(
        id=v.id,
        mapping_id=mapping.id,
        mapping_version_id=version.id,
        version=version.version,
        import_id=v.import_job_id,
        mapping_sha256=v.mapping_sha256,
        outcome=v.outcome,
        ok=v.outcome != "FAILED",
        row_count=v.row_count,
        blocker_count=v.blocker_count,
        error_count=v.error_count,
        warning_count=v.warning_count,
        issues=[ValidationIssue.model_validate(i) for i in v.issues],
        rule_results=[RuleResult.model_validate(r) for r in v.rule_results],
        created_by=v.created_by,
        created_at=v.created_at,
    )
