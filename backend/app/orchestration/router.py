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

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_any_permission, require_permission
from app.core.principal import Principal
from app.db.session import get_session
from app.governance.constants import Permission
from app.orchestration.constants import (
    MAX_CONTRIBUTIONS_PAGE,
    MAX_SHOCKS,
    ApprovalState,
    ScenarioOrigin,
    ScenarioVersionStatus,
)
from app.orchestration.schemas import (
    ScenarioComparisonOut,
    ScenarioContributionPage,
    ScenarioCreate,
    ScenarioDetail,
    ScenarioPage,
    ScenarioRunCreate,
    ScenarioRunOut,
    ScenarioRunPage,
    ScenarioValidationOut,
    ScenarioVersionClose,
    ScenarioVersionConfirm,
    ScenarioVersionCreate,
    ScenarioVersionDecision,
    ScenarioVersionOut,
    ScenarioVersionSubmit,
    ScenarioVersionUpdate,
    TemplatePage,
)
from app.orchestration.service import ScenarioRunService, ScenarioService

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ScenarioReader = Annotated[Principal, Depends(require_permission(Permission.CALC_READ))]
ScenarioProposer = Annotated[Principal, Depends(require_permission(Permission.SCENARIO_PROPOSE))]
ScenarioApprover = Annotated[Principal, Depends(require_permission(Permission.SCENARIO_APPROVE))]
ScenarioCloser = Annotated[
    Principal,
    Depends(require_any_permission(Permission.SCENARIO_PROPOSE, Permission.SCENARIO_APPROVE)),
]
ScenarioRunner = Annotated[Principal, Depends(require_permission(Permission.CALC_RUN))]
VersionNumber = Annotated[int, Path(ge=1, le=1_000_000)]
REPLAYED = "Idempotent-Replayed"

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


# --- Phase 25: run against a baseline and compare (BE-25.3, BE-25.4; D-45, D-46) -------------


def _calculations_enabled(request: Request) -> bool:
    return bool(request.app.state.settings.CALCULATIONS_ENABLED)


@router.get(
    f"{VERSION}/validation",
    response_model=ScenarioValidationOut,
    operation_id="validateScenarioRun",
)
async def validate_scenario_run(
    request: Request,
    scenario_id: uuid.UUID,
    version: VersionNumber,
    principal: ScenarioReader,
    session: SessionDep,
    baseline_run_id: Annotated[uuid.UUID, Query(description="The baseline maturity_ladder run.")],
) -> ScenarioValidationOut:
    """Dry run (D-45 §2, §10): the overlay on the baseline's pinned inputs, with value-free
    match counts per shock and the refusal codes a run would answer now. Nothing is written.
    404 `not_found` alike for an absent or foreign scenario, version or baseline run (D-04)."""
    return await ScenarioRunService(session, principal).validation(
        scenario_id,
        version,
        baseline_run_id,
        _enabled(request),
        _calculations_enabled(request),
    )


@router.post(
    "/scenarios/{scenario_id}/run",
    response_model=ScenarioRunOut,
    status_code=201,
    operation_id="runScenario",
    responses={
        200: {
            "model": ScenarioRunOut,
            "description": "Idempotent replay: this key was used for the same request; the "
            "original scenario_run is returned (header Idempotent-Replayed: true).",
        }
    },
)
async def run_scenario(
    request: Request,
    response: Response,
    scenario_id: uuid.UUID,
    body: ScenarioRunCreate,
    principal: ScenarioRunner,
    session: SessionDep,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            pattern=r"^[!-~]+$",
            description="Unique per run request (per user). A retry with the same key and the "
            "same body returns the original scenario_run; with a different body, 409 "
            "idempotency_conflict.",
        ),
    ],
) -> ScenarioRunOut:
    """Run a version against a baseline maturity_ladder run, synchronously (D-45). Each
    stressed run copies the baseline's pins exactly, applies the shocks to a scenario-specific
    copy of the inputs, runs the unchanged module and the SCENARIO_OVERLAY and
    SCENARIO_CONSERVATION post-checks. k > 1 shocks give k + 1 runs (each alone, then
    combined). No AI is involved. Audited `scenario.run`, and `calculation_run.create` per run.
    Refusals, audited DENIED with nothing written, checked in this order (the first that
    applies answers): 409 `scenarios_disabled`; 409 `calculations_disabled`; 404 `not_found`
    (scenario or version); 409 `idempotency_conflict` (the key was used for a different
    version, shocks or baseline); then, for a new key only: 409 `scenario_not_runnable`; 404
    `not_found` (baseline); 409 `invalid_baseline` or `baseline_not_reproducible`; 409
    `no_matching_cashflows`. A replay of the key returns the original scenario_run without
    re-checking the version's status (it may be CLOSED since) and completes any member run a
    crash left out, on the run's recorded pins (version, shocks_sha256, approval state,
    baseline), each created member audited `calculation_run.create`."""
    row, created = await ScenarioRunService(session, principal).run(
        scenario_id,
        body,
        idempotency_key,
        _enabled(request),
        _calculations_enabled(request),
    )
    if not created:
        response.status_code = 200
        response.headers[REPLAYED] = "true"
    return row


@router.get("/scenario-runs", response_model=ScenarioRunPage, operation_id="listScenarioRuns")
async def list_scenario_runs(
    principal: ScenarioReader,
    session: SessionDep,
    scenario_id: Annotated[uuid.UUID | None, Query()] = None,
    baseline_run_id: Annotated[uuid.UUID | None, Query()] = None,
    approval_state: Annotated[ApprovalState | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScenarioRunPage:
    """The caller's tenant's scenario runs, newest first, with their member runs (D-45 §9)."""
    return await ScenarioRunService(session, principal).page(
        scenario_id, baseline_run_id, approval_state, limit, offset
    )


@router.get(
    "/scenarios/{scenario_id}/comparison",
    response_model=ScenarioComparisonOut,
    operation_id="getScenarioComparison",
)
async def get_scenario_comparison(
    request: Request,
    scenario_id: uuid.UUID,
    principal: ScenarioReader,
    session: SessionDep,
    scenario_run_id: Annotated[uuid.UUID, Query()],
) -> ScenarioComparisonOut:
    """Baseline versus stress per currency and bucket (D-46), for each shock alone and
    combined: exact deltas, turns-negative flags, combined-only breaks, the largest changes and
    a templated (non-AI) summary. Masked per D-33 and D-46 §3. 404 `not_found` alike for an
    absent or foreign scenario or scenario run, or a run of another scenario."""
    minimum = request.app.state.settings.CALC_MIN_AGGREGATE_COUNT
    return await ScenarioRunService(session, principal).comparison(
        scenario_id, scenario_run_id, minimum
    )


@router.get(
    "/scenarios/{scenario_id}/comparison/contributions",
    response_model=ScenarioContributionPage,
    operation_id="listScenarioComparisonContributions",
)
async def list_scenario_comparison_contributions(
    scenario_id: uuid.UUID,
    principal: ScenarioReader,
    session: SessionDep,
    scenario_run_id: Annotated[uuid.UUID, Query()],
    currency: Annotated[str | None, Query(pattern=r"^[A-Z]{3}$")] = None,
    bucket_code: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    shock_subset: Annotated[
        list[int] | None,
        Query(
            max_length=MAX_SHOCKS,
            description="1-based shock indexes of the member run; default the combined run.",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CONTRIBUTIONS_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScenarioContributionPage:
    """Drill-down (D-46 §4): the changed parts (moved, stayed, added) of one member run, with
    days and buckets before and after and source-row lineage. Amounts masked per D-33."""
    return await ScenarioRunService(session, principal).contributions(
        scenario_id, scenario_run_id, currency, bucket_code, shock_subset, limit, offset
    )
