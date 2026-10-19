"""orchestration.router

The Scenario API (Phase 24, BE-24.2 - BE-24.4; D-44, D-47; Feature 05 endpoint table):

- GET /scenario-templates (`calc:read`): the three templates, their parameters, bounds and
  selector vocabulary.
- GET /scenarios, GET /scenarios/{scenario_id} (`calc:read`): the default list is the shared
  library (APPROVED_FOR_TEST only, never exploration versions).
- POST /scenarios, POST .../versions, PUT .../versions/{version}, POST .../confirm,
  POST .../submit (`scenario:propose`, author only for the last three).
- POST .../approval (`scenario:approve`, never the author).
- POST .../close (`scenario:propose` or `scenario:approve`; the service checks which applies).

Every write and every refusal is audited (`scenario.*`), with the shocks' sha256 and never
their text. SCENARIOS_ENABLED=false answers 409 scenarios_disabled to every write. Tenant
scope comes from the principal (D-04): a foreign id is the same 404 as a random one.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_any_permission, require_permission
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.orchestration.constants import ScenarioOrigin, ScenarioVersionStatus
from app.orchestration.schemas import (
    ScenarioCreate,
    ScenarioDetail,
    ScenarioPage,
    ScenarioVersionClose,
    ScenarioVersionConfirm,
    ScenarioVersionCreate,
    ScenarioVersionDecision,
    ScenarioVersionOut,
    ScenarioVersionSubmit,
    ScenarioVersionUpdate,
    TemplatePage,
)
from app.orchestration.service import ScenarioService

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ScenarioReader = Annotated[Principal, Depends(require_permission(Permission.CALC_READ))]
ScenarioProposer = Annotated[Principal, Depends(require_permission(Permission.SCENARIO_PROPOSE))]
ScenarioApprover = Annotated[Principal, Depends(require_permission(Permission.SCENARIO_APPROVE))]
ScenarioCloser = Annotated[
    Principal,
    Depends(require_any_permission(Permission.SCENARIO_PROPOSE, Permission.SCENARIO_APPROVE)),
]
VersionNumber = Annotated[int, Path(ge=1, le=1_000_000)]

router = APIRouter(tags=["scenarios"])

VERSION = "/scenarios/{scenario_id}/versions/{version}"


def _enabled(request: Request) -> bool:
    return bool(request.app.state.settings.SCENARIOS_ENABLED)


@router.get(
    "/scenario-templates", response_model=TemplatePage, operation_id="listScenarioTemplates"
)
async def list_scenario_templates(principal: ScenarioReader, session: SessionDep) -> TemplatePage:
    """The three v1 templates (D-44 §2): parameters with kinds and bounds, the selector
    fields, and the instrument types of the ACTIVE cashflow_direction rule each accepts."""
    return await ScenarioService(session, principal).templates()


@router.get("/scenarios", response_model=ScenarioPage, operation_id="listScenarios")
async def list_scenarios(
    principal: ScenarioReader,
    session: SessionDep,
    status: Annotated[
        list[ScenarioVersionStatus] | None,
        Query(
            description="Versions in these statuses. Default: APPROVED_FOR_TEST only (the "
            "shared library); exploration versions are never listed by default."
        ),
    ] = None,
    origin: Annotated[ScenarioOrigin | None, Query()] = None,
    mine: Annotated[
        bool, Query(description="Only versions the caller authored ('My explorations').")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScenarioPage:
    """Scenarios of the caller's tenant with at least one version matching the filter. CLOSED
    versions are hidden from pickers unless asked for by status."""
    return await ScenarioService(session, principal).list(status, origin, mine, limit, offset)


@router.get("/scenarios/{scenario_id}", response_model=ScenarioDetail, operation_id="getScenario")
async def get_scenario(
    request: Request, scenario_id: uuid.UUID, principal: ScenarioReader, session: SessionDep
) -> ScenarioDetail:
    """One scenario with every version (status, shocks, provenance, author, decider), CLOSED
    and REJECTED included. 404 alike for an absent and a foreign id (D-04)."""
    return await ScenarioService(session, principal).detail(scenario_id, _enabled(request))


@router.post(
    "/scenarios", response_model=ScenarioDetail, status_code=201, operation_id="createScenario"
)
async def create_scenario(
    request: Request, body: ScenarioCreate, principal: ScenarioProposer, session: SessionDep
) -> ScenarioDetail:
    """A scenario and its first DRAFT version (origin MANUAL). Refusals, audited DENIED
    (`scenario.create`): 409 `scenarios_disabled`, 409 `duplicate_code`, 422
    `invalid_shock` (value-free details `{"problems": [{shock_index, field, code}]}`)."""
    return await ScenarioService(session, principal).create(body, _enabled(request))


@router.post(
    "/scenarios/{scenario_id}/versions",
    response_model=ScenarioVersionOut,
    status_code=201,
    operation_id="createScenarioVersion",
)
async def create_scenario_version(
    request: Request,
    scenario_id: uuid.UUID,
    body: ScenarioVersionCreate,
    principal: ScenarioProposer,
    session: SessionDep,
) -> ScenarioVersionOut:
    """A new DRAFT version, optionally adapting `parent_version` (e.g. a rejected one, or
    "what if 30 days?"). Refusals, audited (`scenario.version`): 404 `not_found` (scenario
    or parent_version not found; another tenant's id answers the same), 409
    `scenarios_disabled`, 422 `invalid_shock` (value-free details
    `{"problems": [{shock_index, field, code}]}`)."""
    return await ScenarioService(session, principal).new_version(
        scenario_id, body, _enabled(request)
    )


@router.put(VERSION, response_model=ScenarioVersionOut, operation_id="updateScenarioVersion")
async def update_scenario_version(
    request: Request,
    scenario_id: uuid.UUID,
    version: VersionNumber,
    body: ScenarioVersionUpdate,
    principal: ScenarioProposer,
    session: SessionDep,
) -> ScenarioVersionOut:
    """Replace a DRAFT's shocks; the author only. Refusals, audited (`scenario.edit`): 404
    `not_found`, 409 `scenarios_disabled`, 409 `not_author`, 409 `invalid_status`, 422
    `invalid_shock` (value-free details `{"problems": [{shock_index, field, code}]}`)."""
    return await ScenarioService(session, principal).edit(
        scenario_id, version, body, _enabled(request)
    )


@router.post(
    f"{VERSION}/confirm", response_model=ScenarioVersionOut, operation_id="confirmScenarioVersion"
)
async def confirm_scenario_version(
    request: Request,
    scenario_id: uuid.UUID,
    version: VersionNumber,
    body: ScenarioVersionConfirm,
    principal: ScenarioProposer,
    session: SessionDep,
) -> ScenarioVersionOut:
    """DRAFT -> CONFIRMED_FOR_EXPLORATION by the author alone (D-47 §3): exploration runs
    only, labelled "Exploration, not approved". Refusals, audited (`scenario.confirm`): 404,
    409 `scenarios_disabled`, `not_author`, `invalid_status`; 422 `ai_review_required`,
    `unacknowledged_defaults`."""
    return await ScenarioService(session, principal).confirm(
        scenario_id, version, body, _enabled(request)
    )


@router.post(
    f"{VERSION}/submit", response_model=ScenarioVersionOut, operation_id="submitScenarioVersion"
)
async def submit_scenario_version(
    request: Request,
    scenario_id: uuid.UUID,
    version: VersionNumber,
    body: ScenarioVersionSubmit,
    principal: ScenarioProposer,
    session: SessionDep,
) -> ScenarioVersionOut:
    """DRAFT or CONFIRMED_FOR_EXPLORATION -> IN_REVIEW (promotion), by the author. Refusals,
    audited (`scenario.submit`): 404, 409 `scenarios_disabled`, `not_author`,
    `invalid_status`; 422 `ai_review_required`, `unacknowledged_defaults`."""
    return await ScenarioService(session, principal).submit(
        scenario_id, version, body, _enabled(request)
    )


@router.post(
    f"{VERSION}/approval", response_model=ScenarioVersionOut, operation_id="decideScenarioVersion"
)
async def decide_scenario_version(
    request: Request,
    scenario_id: uuid.UUID,
    version: VersionNumber,
    body: ScenarioVersionDecision,
    principal: ScenarioApprover,
    session: SessionDep,
) -> ScenarioVersionOut:
    """Maker-checker promotion (D-44 §6): APPROVE -> APPROVED_FOR_TEST (the shared library),
    REJECT -> REJECTED. Refusals, audited (`scenario.approve` / `scenario.reject`): 404, 409
    `scenarios_disabled`, `self_approval` (the author), `invalid_status` (not IN_REVIEW)."""
    return await ScenarioService(session, principal).decide(
        scenario_id, version, body, _enabled(request)
    )


@router.post(
    f"{VERSION}/close", response_model=ScenarioVersionOut, operation_id="closeScenarioVersion"
)
async def close_scenario_version(
    request: Request,
    scenario_id: uuid.UUID,
    version: VersionNumber,
    body: ScenarioVersionClose,
    principal: ScenarioCloser,
    session: SessionDep,
) -> ScenarioVersionOut:
    """Any status but DRAFT -> CLOSED: hidden from pickers, still readable by id, its runs
    still re-runnable. The author (`scenario:propose`) closes a non-approved version; an
    approver (`scenario:approve`) closes an approved one. Refusals, audited
    (`scenario.close`): 404, 409 `scenarios_disabled`, `not_author`, `invalid_status`, 403
    `forbidden` (the permission that applies is missing)."""
    return await ScenarioService(session, principal).close(
        scenario_id, version, body, _enabled(request)
    )
